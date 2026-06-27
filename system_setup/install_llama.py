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

from gpu_compat import (  # noqa: E402
    CUDA13_MIN_COMPUTE_CAPABILITY,
    detect_amd_gpu_names,
    detect_nvidia_compute_capability,
    detect_nvidia_driver_cuda_version,
    detect_rocm_tooling,
    detect_vulkan_runtime,
    detect_vulkan_sdk,
    detect_windows_msvc_build_tools,
    format_compute_capability,
    format_gpu_names,
    preferred_amd_gfx_target,
)


RELEASES_API = "https://api.github.com/repos/JamePeng/llama-cpp-python/releases?per_page=100"
JAMEPENG_SOURCE_URL = "git+https://github.com/JamePeng/llama-cpp-python.git"
PYPI_PACKAGE = "llama-cpp-python"

KNOWN_FALLBACK_WHEELS = {
    "cu130": [
        "https://github.com/JamePeng/llama-cpp-python/releases/download/"
        "v0.3.36-cu130-Basic-win-20260417/"
        "llama_cpp_python-0.3.36+cu130.basic-cp313-cp313-win_amd64.whl"
    ],
    "cu126": [
        "https://github.com/JamePeng/llama-cpp-python/releases/download/"
        "v0.3.18-cu126-AVX2-win-20251220/"
        "llama_cpp_python-0.3.18-cp313-cp313-win_amd64.whl"
    ],
}


def _supports_driver(tier: str, driver_cuda: tuple[int, int] | None) -> bool:
    if tier in {"cpu", "cpu-pypi", "cpu-source", "vulkan-source", "hip-source"}:
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


def _candidate_tiers() -> list[str]:
    cc = detect_nvidia_compute_capability()
    driver_cuda = detect_nvidia_driver_cuda_version()
    amd_gpus = detect_amd_gpu_names()

    print(f"NVIDIA compute capability: {format_compute_capability(cc)}")
    print(
        "NVIDIA driver CUDA ceiling: "
        f"{driver_cuda[0]}.{driver_cuda[1]}" if driver_cuda else
        "NVIDIA driver CUDA ceiling: unknown"
    )
    print(f"AMD GPU(s): {format_gpu_names(amd_gpus)}")
    print(f"Vulkan runtime detected: {'yes' if detect_vulkan_runtime() else 'no'}")
    print(f"Vulkan SDK detected: {detect_vulkan_sdk() or 'not found'}")
    print(f"ROCm/HIP tooling detected: {'yes' if detect_rocm_tooling() else 'no'}")

    if cc is None and amd_gpus:
        preferred = ["vulkan-source", "hip-source", "cpu-pypi", "cpu-source"]
    elif cc is None:
        preferred = ["cpu-pypi", "cpu-source"]
    elif cc < CUDA13_MIN_COMPUTE_CAPABILITY:
        preferred = ["cu126", "cu124", "cu121", "cpu-pypi", "cpu-source"]
    else:
        preferred = ["cu131", "cu130", "cu128", "cu126", "cu124", "cu121", "cpu-pypi", "cpu-source"]

    return [tier for tier in preferred if _supports_driver(tier, driver_cuda)]


def _python_tag() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def _platform_tag() -> str:
    if sys.platform.startswith("win"):
        return "win_amd64"
    if sys.platform.startswith("linux"):
        return "linux_x86_64"
    return ""


def _tier_from_text(text: str) -> str:
    lower = text.lower()
    match = re.search(r"(?:cu|cuda)(12[145689]|13[0-9])", lower)
    if match:
        return f"cu{match.group(1)}"
    if "vulkan" in lower:
        return "vulkan"
    if "rocm" in lower or "hip" in lower:
        return "hip"
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
    env["CMAKE_ARGS"] = cmake_args
    env.setdefault("CMAKE_BUILD_PARALLEL_LEVEL", str(max(1, (os.cpu_count() or 4) - 1)))
    vulkan_sdk = detect_vulkan_sdk()
    if vulkan_sdk:
        bin_dir = str(Path(vulkan_sdk) / "Bin")
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
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
        print("Install the Vulkan SDK from LunarG/Khronos for AMD GPU local model acceleration.")
        return 1
    return _install_source_backend("Vulkan llama-cpp-python build", "-DGGML_VULKAN=ON")


def _install_hip_source() -> int:
    if not detect_rocm_tooling():
        print("Skipping HIP/ROCm llama-cpp-python build: ROCm/HIP tooling was not found.")
        return 1
    cmake_args = "-DGGML_HIP=ON"
    target = preferred_amd_gfx_target()
    if target:
        cmake_args = f"{cmake_args} -DAMDGPU_TARGETS={target}"
        print(f"Using AMDGPU target: {target}")
    else:
        print(
            "AMDGPU target could not be inferred. Set AI_COMPANION_AMDGPU_TARGETS "
            "before setup if HIP compilation needs an explicit gfx target."
        )
    return _install_source_backend("HIP/ROCm llama-cpp-python build", cmake_args)


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


def _runtime_import_ok() -> bool:
    code = subprocess.run(
        [sys.executable, "-c", "from llama_cpp import Llama; print('llama_cpp import OK')"],
        text=True,
    ).returncode
    return code == 0


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
            if _install_vulkan_source() == 0 and _runtime_import_ok():
                print("Runtime installed with Vulkan.")
                return 0
            continue

        if tier == "hip-source":
            print("Trying runtime: hip-source")
            if _install_hip_source() == 0 and _runtime_import_ok():
                print("Runtime installed with HIP/ROCm.")
                return 0
            continue

        if tier == "cpu-pypi":
            print("Trying runtime: cpu-pypi")
            if _install_cpu_pypi() == 0 and _runtime_import_ok():
                print("Runtime installed with CPU/PyPI.")
                return 0
            continue

        if tier == "cpu-source":
            print("Trying runtime: cpu-source")
            if _install_cpu_source() == 0 and _runtime_import_ok():
                print("Runtime installed with CPU/source.")
                return 0
            continue

        urls = _unique([
            *discovered.get(tier, []),
            *KNOWN_FALLBACK_WHEELS.get(tier, []),
        ])
        if not urls:
            print(f"No appropriate wheel found for {tier}.")
            continue

        for url in urls:
            print(f"Trying runtime: {tier}")
            code = _install_url(url)
            if code == 0 and _runtime_import_ok():
                print(f"Runtime installed with {tier}.")
                return 0
            print(f"Installation failed with exit code {code}: {url}")

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
