import json
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
    detect_nvidia_compute_capability,
    detect_nvidia_driver_cuda_version,
    format_compute_capability,
)


RELEASES_API = "https://api.github.com/repos/JamePeng/llama-cpp-python/releases?per_page=100"

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
    if tier == "cpu" or driver_cuda is None:
        return True
    required = {
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

    print(f"[setup] NVIDIA compute capability: {format_compute_capability(cc)}")
    print(
        "[setup] NVIDIA driver CUDA ceiling: "
        f"{driver_cuda[0]}.{driver_cuda[1]}" if driver_cuda else
        "[setup] NVIDIA driver CUDA ceiling: unknown"
    )

    if cc is None:
        preferred = ["cpu", "cu130"]
    elif cc < CUDA13_MIN_COMPUTE_CAPABILITY:
        preferred = ["cu126", "cu124", "cu121", "cpu", "cu130"]
    else:
        preferred = ["cu130", "cu128", "cu126", "cu124", "cu121", "cpu"]

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


def _install_url(url: str) -> int:
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", url]
    print("[setup] Running:", " ".join(cmd))
    completed = subprocess.run(cmd)
    return completed.returncode


def main() -> int:
    tiers = _candidate_tiers()
    try:
        discovered = _fetch_release_wheels()
    except Exception as exc:
        print(f"[setup] Could not query JamePeng releases: {exc}")
        discovered = {}

    for tier in tiers:
        urls = _unique([
            *discovered.get(tier, []),
            *KNOWN_FALLBACK_WHEELS.get(tier, []),
        ])
        if not urls:
            print(f"[setup] No llama-cpp-python wheel candidates found for {tier}.")
            continue

        for url in urls:
            print(f"[setup] Trying llama-cpp-python runtime: {tier}")
            code = _install_url(url)
            if code == 0:
                print(f"[setup] llama-cpp-python runtime installed with {tier}.")
                return 0
            print(f"[setup] llama-cpp-python wheel failed with exit code {code}: {url}")

    print("[setup] ERROR: No compatible llama-cpp-python runtime could be installed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
