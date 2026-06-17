import os
from pathlib import Path
import subprocess
import sys
from importlib import metadata


OPENMP_PATTERNS = (
    "libiomp*.dll",
    "vcomp*.dll",
    "libomp*.dll",
)

EXTERNAL_PATH_MARKERS = (
    ".venv",
    "site-packages",
    "python_embeded",
    "python_embedded",
    "conda",
    "miniconda",
    "anaconda",
    "comfyui",
    "ai companion",
)

ALLOWLISTED_PATH_MARKERS: tuple[str, ...] = ()
PACKAGE_VERSION_NAMES = (
    ("llama-cpp-python", ("llama_cpp_python", "llama-cpp-python")),
    ("torch", ("torch",)),
    ("ctranslate2", ("ctranslate2",)),
    ("faster-whisper", ("faster-whisper", "faster_whisper")),
    ("nvidia-ml-py", ("nvidia-ml-py", "nvidia_ml_py")),
)

def _omp_flavor(name: str) -> str:
    n = name.lower()
    if n.startswith("libiompstubs"):
        return "intel-stub"   # no-op shim shipped by torch
    if n.startswith("libiomp"):
        return "intel"        # Intel OpenMP
    if n.startswith("libomp"):
        return "llvm"         # LLVM/clang OpenMP
    if n.startswith("vcomp"):
        return "msvc"         # Microsoft OpenMP
    return "unknown"

RISK_FLAVORS = frozenset({"intel", "llvm"})
def _norm(path: Path) -> str:
    try:
        return str(path.resolve()).lower()
    except Exception:
        return str(path).lower()


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _path_entries() -> list[Path]:
    entries: list[Path] = []
    for raw in os.environ.get("PATH", "").split(os.pathsep):
        raw = raw.strip().strip('"')
        if raw:
            entries.append(Path(raw))
    return entries


def _find_dlls_in_dir(directory: Path) -> list[Path]:
    found: list[Path] = []
    try:
        if not directory.is_dir():
            return found
    except OSError:
        return found
    for pattern in OPENMP_PATTERNS:
        try:
            found.extend(path for path in directory.glob(pattern) if path.is_file())
        except Exception:
            pass
    return sorted(set(found), key=lambda p: str(p).lower())

def _find_dlls_under(root: Path) -> list[Path]:
    found: list[Path] = []
    if not root.is_dir():
        return found
    for pattern in OPENMP_PATTERNS:
        try:
            found.extend(path for path in root.rglob(pattern) if path.is_file())
        except Exception:
            pass
    return sorted(set(found), key=lambda p: str(p).lower())

def _package_version(candidates: tuple[str, ...]) -> str:
    for name in candidates:
        try:
            return metadata.version(name)
        except Exception:
            pass
    return "not installed"

def _run_nvidia_smi(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["nvidia-smi", *args],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception as exc:
        return f"unavailable: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return f"failed ({result.returncode}): {detail}"
    return (result.stdout or "").strip() or "no output"

def _print_openmp_verdict(scoped_dlls: list[Path]) -> None:
    risk_entries: list[tuple[str, str, str]] = []
    for path in scoped_dlls:
        flavor = _omp_flavor(path.name)
        if flavor in RISK_FLAVORS:
            risk_entries.append((flavor, path.name, _norm(path.parent)))

    distinct_dirs = {parent for _, _, parent in risk_entries}
    if not risk_entries:
        print("  No Intel/LLVM OpenMP runtime found in app scope -- #15 not expected.")
        return

    if len(distinct_dirs) < 2:
        print("  OK: a single in-scope OpenMP runtime -- #15 not expected.")
    else:
        print("  HIGH RISK: more than one OpenMP runtime can load into this process.")
        print("  Conflicting runtimes (this is what triggers #15):")
        by_flavor: dict[str, set[tuple[str, str]]] = {}
        for flavor, name, parent in risk_entries:
            by_flavor.setdefault(flavor, set()).add((name, parent))
        for flavor in sorted(by_flavor):
            for name, parent in sorted(by_flavor[flavor]):
                print(f"    [{flavor}] {name}  <-  {parent}")
        print(
            "  Mitigation: set KMP_DUPLICATE_LIB_OK=TRUE before importing any of "
            "torch / llama_cpp / ctranslate2 (set it in the launcher or as the first\n"
            "  line of your entry module, not after those imports), or remove the "
            "redundant copy so only one runtime loads."
        )

def main() -> int:
    files_dir = Path(__file__).resolve().parents[1]
    app_root = files_dir.parent

    print("\nPython package versions:")
    for label, candidates in PACKAGE_VERSION_NAMES:
        print(f"  {label}: {_package_version(candidates)}")

    print("\nNVIDIA GPU telemetry:")
    print(
        _run_nvidia_smi([
            "--query-gpu=name,compute_cap,driver_version,memory.total,memory.used",
            "--format=csv",
        ])
    )

    app_dlls = _find_dlls_under(files_dir)

    path_dlls: list[Path] = []
    external_markered: list[Path] = []
    for entry in _path_entries():
        if not _is_inside(entry, app_root):
            lowered = str(entry).lower()
            if any(marker in lowered for marker in EXTERNAL_PATH_MARKERS):
                external_markered.append(entry)
        path_dlls.extend(_find_dlls_in_dir(entry))

    scoped: list[Path] = list(app_dlls)
    scoped += [p for p in path_dlls if _is_inside(p, app_root)]
    _deduped: dict[str, Path] = {}
    for p in scoped:
        _deduped.setdefault(_norm(p), p)
    scoped = list(_deduped.values())

    _print_openmp_verdict(scoped)

    print("\nOpenMP DLLs under this app (reference):")
    if app_dlls:
        for path in app_dlls:
            print(f"  [{_omp_flavor(path.name)}] {path}")
    else:
        print("  none found")
    print("\nOpenMP DLLs visible directly on PATH (reference; see caveat in source):")
    if path_dlls:
        for path in sorted(set(path_dlls), key=lambda p: str(p).lower()):
            scope = "current app" if _is_inside(path, app_root) else "external"
            print(f"  [{scope}/{_omp_flavor(path.name)}] {path}")
    else:
        print("  none found")

    allowlisted: list[Path] = []
    review: list[Path] = []
    for entry in sorted(set(external_markered), key=lambda p: str(p).lower()):
        lowered = str(entry).lower()
        if any(marker in lowered for marker in ALLOWLISTED_PATH_MARKERS):
            allowlisted.append(entry)
        else:
            review.append(entry)

    print("\nExternal toolchains on PATH -- known/intentional (allowlisted):")
    if allowlisted:
        for entry in allowlisted:
            print(f"  {entry}")
    else:
        print("  none (populate ALLOWLISTED_PATH_MARKERS to silence your own)")

    print("\nExternal toolchains on PATH -- review (remove if unexpected):")
    if review:
        for entry in review:
            print(f"  {entry}")
    else:
        print("  none found")

    duplicate_names: dict[str, set[str]] = {}
    for path in app_dlls + path_dlls:
        duplicate_names.setdefault(path.name.lower(), set()).add(_norm(path.parent))

    duplicates = {
        name: dirs
        for name, dirs in duplicate_names.items()
        if len(dirs) > 1
    }
    print("\nDuplicate OpenMP DLL names in multiple directories:")
    if duplicates:
        for name, dirs in sorted(duplicates.items()):
            flavor = _omp_flavor(name)
            tag = "RISK" if flavor in RISK_FLAVORS else "info"
            print(f"  [{tag}/{flavor}] {name}")
            for directory in sorted(dirs):
                print(f"    {directory}")
    else:
        print("  none found")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())