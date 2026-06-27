import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from gpu_compat import (
    CUDA13_MIN_COMPUTE_CAPABILITY,
    detect_nvidia_compute_capability,
    detect_nvidia_driver_cuda_version,
    format_compute_capability,
)


TORCH_INDEXES = {
    "cu130": "https://download.pytorch.org/whl/cu130",
    "cu128": "https://download.pytorch.org/whl/cu128",
    "cu126": "https://download.pytorch.org/whl/cu126",
    "cu124": "https://download.pytorch.org/whl/cu124",
    "cpu": "https://download.pytorch.org/whl/cpu",
}


def _supports_driver(tier: str, driver_cuda: tuple[int, int] | None) -> bool:
    if tier == "cpu" or driver_cuda is None:
        return True
    required = {
        "cu130": (13, 0),
        "cu128": (12, 8),
        "cu126": (12, 6),
        "cu124": (12, 4),
    }.get(tier)
    return required is None or driver_cuda >= required


def _candidate_tiers() -> list[str]:
    cc = detect_nvidia_compute_capability()
    driver_cuda = detect_nvidia_driver_cuda_version()

    print(f"NVIDIA compute capability: {format_compute_capability(cc)}")
    print(
        "NVIDIA driver CUDA ceiling: "
        f"{driver_cuda[0]}.{driver_cuda[1]}" if driver_cuda else
        "NVIDIA driver CUDA ceiling: unknown"
    )

    if cc is None:
        return ["cpu"]

    if cc < CUDA13_MIN_COMPUTE_CAPABILITY:
        preferred = ["cu126", "cu124", "cpu"]
    else:
        preferred = ["cu130", "cu128", "cu126", "cu124", "cpu"]

    return [tier for tier in preferred if _supports_driver(tier, driver_cuda)]


def _run_pip(args: list[str]) -> int:
    cmd = [sys.executable, "-m", "pip", *args]
    print("Running:", " ".join(cmd))
    completed = subprocess.run(cmd)
    return completed.returncode


def main() -> int:
    tiers = _candidate_tiers()
    for tier in tiers:
        index_url = TORCH_INDEXES[tier]
        print(f"Trying PyTorch runtime: {tier}")
        code = _run_pip([
            "install",
            "--upgrade",
            "torch",
            "torchvision",
            "torchaudio",
            "--index-url",
            index_url,
        ])
        if code == 0:
            print(f"PyTorch runtime installed with {tier}.")
            return 0
        print(f"PyTorch runtime {tier} failed with exit code {code}.")

    print("ERROR: No compatible PyTorch runtime could be installed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())