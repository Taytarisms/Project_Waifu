import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys
import zipfile

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from install_llama import _parse_sha256, _sha256_file, _run_pip

FILES = Path(__file__).resolve().parents[1]


def validate_wheel(path):
    wheel = Path(str(path).strip().strip('"')).resolve()
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("This installer requires Windows x64 Python.")
    if not wheel.is_file() or not wheel.name.startswith("llama_cpp_python-") or not wheel.name.endswith(f"-{tag}-{tag}-win_amd64.whl"):
        raise ValueError(f"Select a llama_cpp_python wheel for {tag}, Windows x64.")
    checksum = wheel.with_name(wheel.name + ".sha256")
    expected = _parse_sha256(checksum.read_text(encoding="utf-8-sig"), wheel.name)
    if _sha256_file(wheel).lower() != expected.lower():
        raise ValueError("Wheel checksum mismatch; obtain the original wheel and checksum again.")
    with zipfile.ZipFile(wheel) as archive:
        if not any(name.lower().endswith("/ggml-vulkan.dll") for name in archive.namelist()):
            raise ValueError("This pilot installer expects a wheel containing ggml-vulkan.dll.")
    return wheel


def verify_runtime():
    probe = """
from pathlib import Path
import diskcache
import llama_cpp
from files.system_setup.llama_runtime import load_dynamic_backends
from llama_cpp import Llama
library = Path(llama_cpp.__file__).parent / 'lib' / 'ggml-vulkan.dll'
assert library.is_file(), 'Vulkan DLL missing from installed runtime'
assert load_dynamic_backends(llama_cpp), 'GPU offload backend unavailable'
print('Vulkan wheel import/offload probe passed. A GGUF generation test is still required.')
"""
    try:
        return subprocess.run([sys.executable, "-B", "-c", probe], cwd=FILES.parent, timeout=60).returncode
    except subprocess.TimeoutExpired:
        print("ERROR: Runtime probe timed out.")
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", nargs="?", help="Wheel file; quotes from drag-and-drop are accepted")
    parser.add_argument("--check-only", action="store_true", help="Validate artifact without installing")
    args = parser.parse_args(argv)
    try:
        path = args.wheel or os.environ.get("AI_COMPANION_VULKAN_WHEEL", "").strip()
        if not path:
            candidates = list((FILES / "wheels" / "vulkan").glob("*.whl"))
            if len(candidates) != 1:
                raise ValueError("Supply one Vulkan wheel and its .whl.sha256 file in files/wheels/vulkan, or provide a wheel path.")
            path = candidates[0]
        wheel = validate_wheel(path)
        print(f"Validated: {wheel.name}")
        if args.check_only:
            return 0
        expected_env = (FILES / ".venv").resolve()
        if Path(sys.prefix).resolve() != expected_env:
            raise ValueError("Use files\\.venv\\Scripts\\python.exe; refusing to modify another Python environment.")
        if _run_pip(["install", "--upgrade", "--only-binary=:all:", str(wheel)]) != 0:
            return 1
        if _run_pip(["install", "--force-reinstall", "--no-deps", str(wheel)]) != 0:
            return 1
        return verify_runtime()
    except Exception as exc:
        print(f"ERROR: Vulkan installation/validation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
