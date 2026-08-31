import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from gpu_compat import (
    CUDA13_MIN_COMPUTE_CAPABILITY,
    detect_amd_gpu_names,
    detect_gpu_names,
    detect_nvidia_compute_capability,
    detect_nvidia_driver_cuda_version,
    detect_vulkan_runtime,
    detect_vulkan_sdk,
    detect_windows_msvc_build_tools,
    format_compute_capability,
    format_gpu_names,
)


RELEASES_API = "https://api.github.com/repos/JamePeng/llama-cpp-python/releases?per_page=100"
JAMEPENG_SOURCE_URL = "git+https://github.com/JamePeng/llama-cpp-python.git"
PYPI_PACKAGE = "llama-cpp-python"

KNOWN_FALLBACK_WHEELS = {
    "cu130": [
        "https://github.com/JamePeng/llama-cpp-python/releases/download/"
        "v0.3.38-cu130-Basic-win-20260504/"
        "llama_cpp_python-0.3.38+cu130.basic-cp313-cp313-win_amd64.whl"
    ],
    "cu126": [
        "https://github.com/JamePeng/llama-cpp-python/releases/download/"
        "v0.3.18-cu126-AVX2-win-20251220/"
        "llama_cpp_python-0.3.18-cp313-cp313-win_amd64.whl"
    ],
}


def _supports_driver(tier: str, driver_cuda: tuple[int, int] | None) -> bool:
    if tier in {"cpu", "cpu-pypi", "cpu-source", "vulkan-source"}:
        return True
    if driver_cuda is None:
        return True
    required = {
        "cu131": (13, 1),
        "cu130": (13, 0),
        "cu129": (12, 9),
        "cu128": (12, 8),
        "cu126": (12, 6),
        "cu125": (12, 5),
        "cu124": (12, 4),
        "cu121": (12, 1),
    }.get(tier)
    return required is None or driver_cuda >= required


def _installed_torch_cuda_tier() -> str:
    try:
        import torch

        version = str(getattr(getattr(torch, "version", None), "cuda", "") or "")
        match = re.match(r"(\d+)[.](\d+)", version)
        if match:
            return f"cu{match.group(1)}{match.group(2)}"
    except Exception:
        pass
    return ""


def _candidate_tiers(platform_name: str | None = None) -> list[str]:
    platform_name = platform_name or sys.platform
    cc = detect_nvidia_compute_capability()
    driver_cuda = detect_nvidia_driver_cuda_version()
    amd_gpus = detect_amd_gpu_names()
    gpu_names = detect_gpu_names()

    print(f"NVIDIA compute capability: {format_compute_capability(cc)}")
    print(
        "NVIDIA driver CUDA ceiling: "
        f"{driver_cuda[0]}.{driver_cuda[1]}" if driver_cuda else
        "NVIDIA driver CUDA ceiling: unknown"
    )
    print(f"AMD GPU(s): {format_gpu_names(amd_gpus)}")
    print(f"Vulkan runtime detected: {'yes' if detect_vulkan_runtime() else 'no'}")
    print(f"Vulkan SDK detected: {detect_vulkan_sdk() or 'not found'}")

    forced = os.environ.get("AI_COMPANION_LLAMA_BACKEND", "auto").strip().lower()
    forced_tiers = {
        "vulkan": "vulkan-source",
        "metal": "metal-source",
        "cpu": "cpu-pypi",
    }
    if forced != "auto":
        selected = forced_tiers.get(forced)
        if not selected:
            raise ValueError(
                "AI_COMPANION_LLAMA_BACKEND must be auto, vulkan, metal, or cpu"
            )
        return [selected] if selected == "cpu-pypi" else [selected, "cpu-pypi", "cpu-source"]

    if platform_name == "darwin":
        preferred = ["metal-source", "cpu-pypi", "cpu-source"]
    elif cc is None and amd_gpus:
        preferred = ["vulkan-source", "cpu-pypi", "cpu-source"]
    elif cc is None and gpu_names and detect_vulkan_runtime():
        preferred = ["vulkan-source", "cpu-pypi", "cpu-source"]
    elif cc is None:
        preferred = ["cpu-pypi", "cpu-source"]
    elif cc < CUDA13_MIN_COMPUTE_CAPABILITY:
        preferred = ["cu126", "cu124", "cu121", "cpu-pypi", "cpu-source"]
    else:
        preferred = ["cu131", "cu130", "cu128", "cu126", "cu124", "cu121", "cpu-pypi", "cpu-source"]

    torch_tier = _installed_torch_cuda_tier()
    if torch_tier in preferred:
        preferred.remove(torch_tier)
        preferred.insert(0, torch_tier)
        print(f"Matching llama.cpp to installed PyTorch runtime first: {torch_tier}")

    return [tier for tier in preferred if _supports_driver(tier, driver_cuda)]


def _python_tag() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def _platform_tag() -> str:
    if sys.platform.startswith("win"):
        return "win_amd64"
    return ""


def _tier_from_text(text: str) -> str:
    lower = text.lower()
    match = re.search(r"(?:cu|cuda)(12[145689]|13[0-9])", lower)
    if match:
        return f"cu{match.group(1)}"
    if "vulkan" in lower:
        return "vulkan"
    if "cpu" in lower and "cuda" not in lower and "cu12" not in lower and "cu13" not in lower:
        return "cpu"
    return ""


def _version_score(text: str) -> tuple[int, int, int, int]:
    version_match = re.search(r"0[.](\d+)[.](\d+)", text)
    date_match = re.search(r"(20\d{6})", text)
    major = minor = 0
    if version_match:
        major = int(version_match.group(1))
        minor = int(version_match.group(2))
    date = int(date_match.group(1)) if date_match else 0
    return major, minor, date, len(text)


def _fetch_release_wheels() -> dict[str, list[str]]:
    py_tag = _python_tag()
    platform_tag = _platform_tag()
    if not platform_tag:
        return {}

    request = urllib.request.Request(
        RELEASES_API,
        headers={"User-Agent": "AI Companion Setup"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        releases = json.loads(response.read().decode("utf-8"))

    wheels: dict[str, list[tuple[tuple[int, int, int, int], str]]] = {}
    for release in releases:
        release_text = " ".join([
            str(release.get("tag_name") or ""),
            str(release.get("name") or ""),
        ])
        for asset in release.get("assets", []):
            name = str(asset.get("name") or "")
            url = str(asset.get("browser_download_url") or "")
            if not name.endswith(".whl") or not url:
                continue
            if f"{py_tag}-{py_tag}" not in name or platform_tag not in name:
                continue
            tier = _tier_from_text(" ".join([release_text, name, url]))
            if not tier:
                continue
            score = _version_score(" ".join([release_text, name]))
            wheels.setdefault(tier, []).append((score, url))

    return {
        tier: [url for _, url in sorted(entries, reverse=True)]
        for tier, entries in wheels.items()
    }


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _wheel_candidates(tier: str, discovered: dict[str, list[str]]) -> list[str]:
    newest = discovered.get(tier, [])[:1]
    return _unique([*newest, *KNOWN_FALLBACK_WHEELS.get(tier, [])])


def _run_pip(args: list[str], *, env: dict[str, str] | None = None) -> int:
    cmd = [sys.executable, "-m", "pip", *args]
    print("Running:", " ".join(cmd))
    completed = subprocess.run(cmd, env=env)
    return completed.returncode


def _install_url(url: str) -> int:
    return _run_pip(["install", "--upgrade", url])


def _install_build_helpers() -> int:
    return _run_pip([
        "install",
        "--upgrade",
        "cmake",
        "ninja",
        "scikit-build-core",
        "wheel",
        "setuptools<82",
    ])


def _source_env(cmake_args: str) -> dict[str, str]:
    env = os.environ.copy()
    env["FORCE_CMAKE"] = "1"
    build_scope = (
        "-DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TOOLS=OFF "
        "-DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_SERVER=OFF"
    )
    env["CMAKE_ARGS"] = f"{cmake_args} {build_scope}".strip()
    env.setdefault("CMAKE_BUILD_PARALLEL_LEVEL", str(max(1, (os.cpu_count() or 4) - 1)))
    vulkan_sdk = detect_vulkan_sdk()
    if vulkan_sdk:
        for dirname in ("Bin", "bin"):
            candidate = Path(vulkan_sdk) / dirname
            if candidate.is_dir():
                env["PATH"] = str(candidate) + os.pathsep + env.get("PATH", "")
                break
    return env


def _install_source_backend(label: str, cmake_args: str) -> int:
    if sys.platform.startswith("win") and not detect_windows_msvc_build_tools():
        print(
            f"Skipping {label}: Visual Studio C++ Build Tools were not found. "
            "Install Desktop development with C++ or Build Tools, then rerun setup."
        )
        return 1

    helper_code = _install_build_helpers()
    if helper_code != 0:
        print(f"Skipping {label}: failed to install Python build helpers.")
        return helper_code

    env = _source_env(cmake_args)
    return _run_pip([
        "install",
        "--upgrade",
        "--force-reinstall",
        "--no-cache-dir",
        "--no-binary",
        "llama-cpp-python",
        JAMEPENG_SOURCE_URL,
    ], env=env)


def _install_vulkan_source() -> int:
    if not detect_vulkan_sdk():
        if detect_vulkan_runtime():
            print(
                "Skipping Vulkan llama-cpp-python build: Vulkan runtime is present, "
                "but Vulkan SDK is not installed."
            )
        else:
            print("Skipping Vulkan llama-cpp-python build: Vulkan runtime/SDK not found.")
        print(
            "Install Vulkan development packages (loader headers, glslc/shaderc, "
            "and SPIR-V headers) or the LunarG SDK, then rerun setup."
        )
        return 1
    return _install_source_backend("Vulkan llama-cpp-python build", "-DGGML_VULKAN=ON")


def _install_metal_source() -> int:
    if sys.platform != "darwin":
        print("Skipping Metal llama-cpp-python build: Metal is only available on macOS.")
        return 1
    return _install_source_backend("Metal llama-cpp-python build", "-DGGML_METAL=ON")


def _install_cpu_pypi() -> int:
    return _run_pip([
        "install",
        "--upgrade",
        "--only-binary",
        ":all:",
        "--prefer-binary",
        PYPI_PACKAGE,
    ])


def _install_cpu_source() -> int:
    return _install_source_backend("CPU llama-cpp-python build", "-DGGML_NATIVE=OFF")


def _runtime_status() -> tuple[bool, bool]:
    probe = (
        "import llama_cpp; "
        "from files.system_setup.llama_runtime import load_dynamic_backends; "
        "load_dynamic_backends(llama_cpp); "
        "from llama_cpp import Llama; "
        "print('OFFLOAD=' + str(bool(llama_cpp.llama_supports_gpu_offload())))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(SCRIPT_DIR.parents[1]),
    )
    output = "\n".join([result.stdout or "", result.stderr or ""])
    if output.strip():
        print(output.strip())
    return result.returncode == 0, "OFFLOAD=True" in output


def _runtime_import_ok() -> bool:
    imported, _ = _runtime_status()
    return imported


def _finish_backend(label: str, code: int, *, expect_gpu: bool = False) -> bool:
    if code != 0:
        return False
    imported, gpu_offload = _runtime_status()
    if not imported:
        print(
            f"{label}: install reported success but 'import llama_cpp' failed "
            "(missing runtime/DLLs, incompatible build, or wrong GPU target); "
            "trying the next option."
        )
        return False
    if expect_gpu and not gpu_offload:
        print(
            f"{label}: install completed, but llama_cpp reports no usable GPU "
            "offload backend. Trying the next option."
        )
        return False
    print(f"Runtime installed with {label}.")
    return True


def main() -> int:
    tiers = _candidate_tiers()
    try:
        discovered = _fetch_release_wheels()
    except Exception as exc:
        print(f"Couldn't find release(s): {exc}")
        discovered = {}

    for tier in tiers:
        if tier == "vulkan-source":
            print("Trying runtime: vulkan-source")
            if _finish_backend("Vulkan", _install_vulkan_source(), expect_gpu=True):
                return 0
            continue

        if tier == "metal-source":
            print("Trying runtime: metal-source")
            if _finish_backend("Metal", _install_metal_source(), expect_gpu=True):
                return 0
            continue

        if tier == "cpu-pypi":
            print("Trying runtime: cpu-pypi")
            if _finish_backend("CPU/PyPI", _install_cpu_pypi()):
                return 0
            continue

        if tier == "cpu-source":
            print("Trying runtime: cpu-source")
            if _finish_backend("CPU/source", _install_cpu_source()):
                return 0
            continue

        urls = _wheel_candidates(tier, discovered)
        if not urls:
            print(f"No appropriate wheel found for {tier}.")
            continue

        for url in urls:
            print(f"Trying runtime: {tier}")
            code = _install_url(url)
            if code != 0:
                print(f"Installation failed with exit code {code}: {url}")
                continue
            imported, gpu_offload = _runtime_status()
            if not imported or not gpu_offload:
                print(
                    f"{tier}: wheel installed but no usable GPU backend loaded "
                    "(missing runtime libraries, incompatible driver, or wrong build); "
                    f"trying the next option: {url}"
                )
                continue
            print(f"Runtime installed with {tier}.")
            return 0

    print("ERROR: No compatible llama-cpp-python wheel/build could be installed.")
    if detect_amd_gpu_names():
        print("AMD GPU detected, but no AMD-ready llama-cpp-python runtime could be installed.")
        if not detect_vulkan_sdk():
            print("For AMD GPU acceleration, install the Vulkan SDK, then rerun setup.")
        if sys.platform.startswith("win") and not detect_windows_msvc_build_tools():
            print("For source builds on Windows, install Visual Studio 2022 Build Tools with Desktop development with C++.")
    print("Local LLM will be unavailable until llama-cpp-python is installed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
