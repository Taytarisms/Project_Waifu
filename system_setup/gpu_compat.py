import re
import subprocess
from functools import lru_cache
from importlib import metadata
from typing import Any


CUDA13_MIN_COMPUTE_CAPABILITY = (7, 5)


def _parse_compute_capability(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    text = str(value).strip()
    match = re.search(r"(\d+)(?:[.\s,_-]+(\d+))?", text)
    if not match:
        return None
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    return major, minor


def _compute_capability_from_name(name: str) -> tuple[int, int] | None:
    lower = (name or "").lower()

    if any(token in lower for token in ("v100", "titan v", "gv100")):
        return 7, 0
    if "quadro p" in lower or "tesla p" in lower or "titan xp" in lower:
        return 6, 1
    if "titan x" in lower and "maxwell" not in lower:
        return 6, 1
    if re.search(r"\bgtx\s+10\d0(?:\s*ti)?\b", lower):
        return 6, 1
    if re.search(r"\bgt\s+1030\b", lower):
        return 6, 1
    if re.search(r"\bgtx\s+9\d0(?:\s*ti)?\b", lower):
        return 5, 2
    if "maxwell" in lower:
        return 5, 2
    if "pascal" in lower:
        return 6, 1
    if "volta" in lower:
        return 7, 0

    return None


def _run_nvidia_smi(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None
    return (result.stdout or "").strip()


@lru_cache(maxsize=1)
def detect_nvidia_driver_cuda_version() -> tuple[int, int] | None:
    output = _run_nvidia_smi([])
    if not output:
        return None
    match = re.search(r"CUDA\s+Version:\s*(\d+)\.(\d+)", output, re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


@lru_cache(maxsize=1)
def detect_nvidia_compute_capability() -> tuple[int, int] | None:
    output = _run_nvidia_smi([
        "--query-gpu=compute_cap",
        "--format=csv,noheader,nounits",
    ])
    if output:
        parsed = _parse_compute_capability(output.splitlines()[0])
        if parsed:
            return parsed

    output = _run_nvidia_smi([
        "--query-gpu=name",
        "--format=csv,noheader",
    ])
    if output:
        parsed = _compute_capability_from_name(output.splitlines()[0])
        if parsed:
            return parsed

    try:
        import torch

        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            return int(props.major), int(props.minor)
    except Exception:
        pass

    return None


def format_compute_capability(cc: tuple[int, int] | None) -> str:
    if not cc:
        return "unknown"
    return f"{cc[0]}.{cc[1]}"


def is_pre_turing_nvidia_gpu() -> bool:
    cc = detect_nvidia_compute_capability()
    return cc is not None and cc < CUDA13_MIN_COMPUTE_CAPABILITY


def cuda13_support_message() -> str | None:
    cc = detect_nvidia_compute_capability()
    if cc is None or cc >= CUDA13_MIN_COMPUTE_CAPABILITY:
        return None
    return (
        "Detected NVIDIA compute capability "
        f"{format_compute_capability(cc)}. CUDA 13 builds require Turing-class "
        "compute capability 7.5 or newer for GPU offload, so this machine should "
        "use CPU mode for CUDA 13-backed local features."
    )


@lru_cache(maxsize=1)
def llama_cpp_build_tag() -> str:
    try:
        return metadata.version("llama_cpp_python").lower()
    except Exception:
        return ""


def llama_cpp_build_is_cuda13() -> bool:
    tag = llama_cpp_build_tag()
    return "cu13" in tag or "cuda13" in tag


def should_force_llama_cpp_cpu() -> bool:
    return llama_cpp_build_is_cuda13() and is_pre_turing_nvidia_gpu()


def torch_cuda_version(torch_module: Any) -> tuple[int, int] | None:
    version = getattr(getattr(torch_module, "version", None), "cuda", None)
    return _parse_compute_capability(version)


def should_avoid_torch_cuda(torch_module: Any) -> bool:
    cuda_version = torch_cuda_version(torch_module)
    if cuda_version is None or cuda_version < (13, 0):
        return False
    return is_pre_turing_nvidia_gpu()
