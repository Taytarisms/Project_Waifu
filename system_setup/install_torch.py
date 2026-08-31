import subprocess
import sys
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from gpu_compat import (
    CUDA13_MIN_COMPUTE_CAPABILITY,
    detect_amd_gpu_names,
    detect_nvidia_compute_capability,
    detect_nvidia_driver_cuda_version,
    format_compute_capability,
    format_gpu_names,
)


TORCH_INDEXES = {
    "cu130": "https://download.pytorch.org/whl/cu130",
    "cu128": "https://download.pytorch.org/whl/cu128",
    "cu126": "https://download.pytorch.org/whl/cu126",
    "cu124": "https://download.pytorch.org/whl/cu124",
    "cpu": "https://download.pytorch.org/whl/cpu",
    "default": None,
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


def _candidate_tiers(platform_name: str | None = None) -> list[str]:
    platform_name = platform_name or sys.platform
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

    if platform_name == "darwin":
        return ["default"]

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


def _runtime_info() -> dict:
    probe = (
        "import json, torch; "
        "print(json.dumps({'cuda': torch.version.cuda, "
        "'available': torch.cuda.is_available(), "
        "'device': torch.cuda.get_device_name(0) if torch.cuda.is_available() else ''}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {"error": (result.stderr or result.stdout or "import failed").strip()}
    try:
        return json.loads((result.stdout or "").strip().splitlines()[-1])
    except Exception:
        return {"error": (result.stdout or "invalid probe output").strip()}


def _runtime_matches(tier: str, info: dict) -> bool:
    if info.get("error"):
        return False
    if tier.startswith("cu"):
        return bool(info.get("cuda"))
    return True


def main() -> int:
    tiers = _candidate_tiers()
    for tier in tiers:
        index_url = TORCH_INDEXES[tier]
        print(f"Trying PyTorch runtime: {tier}")
        pip_args = [
            "install",
            "--upgrade",
            "torch",
            "torchvision",
            "torchaudio",
        ]
        if index_url:
            pip_args.extend(["--index-url", index_url])
        code = _run_pip(pip_args)
        if code == 0:
            info = _runtime_info()
            if _runtime_matches(tier, info):
                print(f"PyTorch runtime installed with {tier}: {info}")
                if tier.startswith("cu") and not info.get("available"):
                    print(
                        "WARNING: The GPU-enabled PyTorch build is installed, but the GPU "
                        "is not currently accessible. Check the driver and device permissions."
                    )
                return 0
            print(f"PyTorch runtime verification failed for {tier}: {info}")
        else:
            print(f"PyTorch runtime {tier} failed with exit code {code}.")

    print("ERROR: No compatible PyTorch runtime could be installed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
