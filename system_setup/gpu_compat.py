import re
import os
import shutil
import subprocess
from functools import lru_cache
from importlib import metadata
from pathlib import Path
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


def _run_text_command(args: list[str], timeout: int = 8) -> str | None:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None
    return (result.stdout or "").strip()


@lru_cache(maxsize=1)
def detect_gpu_names() -> tuple[str, ...]:
    names: list[str] = []

    if os.name == "nt":
        output = _run_text_command([
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }",
        ])
        if output:
            names.extend(line.strip() for line in output.splitlines() if line.strip())
    else:
        output = _run_text_command(["lspci"])
        if output:
            for line in output.splitlines():
                if any(token in line.lower() for token in ("vga", "3d controller", "display")):
                    names.append(line.strip())

    return tuple(dict.fromkeys(names))


@lru_cache(maxsize=1)
def detect_amd_gpu_names() -> tuple[str, ...]:
    amd_tokens = (
        "advanced micro devices",
        "amd ",
        "radeon",
        "rx ",
        "vega",
        "firepro",
        "instinct",
    )
    result: list[str] = []
    for name in detect_gpu_names():
        lower = f" {name.lower()} "
        if any(token in lower for token in amd_tokens):
            result.append(name)
    return tuple(result)


def has_amd_gpu() -> bool:
    return bool(detect_amd_gpu_names())


def format_gpu_names(names: tuple[str, ...] | list[str]) -> str:
    return ", ".join(names) if names else "none detected"


def detect_vulkan_sdk() -> str | None:
    raw = os.environ.get("VULKAN_SDK", "").strip().strip('"')
    if raw and Path(raw).exists():
        return raw
    glslc = shutil.which("glslc")
    if glslc:
        return str(Path(glslc).resolve().parent.parent)
    return None


def detect_vulkan_runtime() -> bool:
    if shutil.which("vulkaninfo") or shutil.which("glslc"):
        return True
    if os.name == "nt":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        return (Path(windir) / "System32" / "vulkan-1.dll").exists()
    return Path("/usr/lib/libvulkan.so.1").exists() or Path("/usr/lib64/libvulkan.so.1").exists()


def detect_rocm_tooling() -> bool:
    if shutil.which("hipcc") or shutil.which("rocm-smi") or shutil.which("rocm-sdk"):
        return True
    for env_key in ("ROCM_PATH", "HIP_PATH"):
        raw = os.environ.get(env_key, "").strip().strip('"')
        if raw and Path(raw).exists():
            return True
    return False


def detect_windows_msvc_build_tools() -> bool:
    if shutil.which("cl"):
        return True
    if os.name != "nt":
        return True
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe",
    ]
    for vswhere in candidates:
        if not vswhere.exists():
            continue
        output = _run_text_command([
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property",
            "installationPath",
        ])
        if output:
            return True
    return False


def infer_amd_gfx_target(name: str) -> str | None:
    lower = (name or "").lower()
    if re.search(r"\brx\s+79\d0\b", lower):
        return "gfx1100"
    if re.search(r"\brx\s+78\d0\b", lower) or re.search(r"\brx\s+77\d0\b", lower):
        return "gfx1101"
    if re.search(r"\brx\s+76\d0\b", lower):
        return "gfx1102"
    if re.search(r"\brx\s+69\d0\b", lower) or re.search(r"\brx\s+68\d0\b", lower):
        return "gfx1030"
    if re.search(r"\brx\s+67\d0\b", lower) or re.search(r"\brx\s+66\d0\b", lower):
        return "gfx1031"
    return None


def preferred_amd_gfx_target() -> str | None:
    explicit = os.environ.get("AI_COMPANION_AMDGPU_TARGETS", "").strip()
    if explicit:
        return explicit
    for name in detect_amd_gpu_names():
        inferred = infer_amd_gfx_target(name)
        if inferred:
            return inferred
    return None


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


def llama_cpp_build_is_cuda() -> bool:
    tag = llama_cpp_build_tag()
    return "cu" in tag or "cuda" in tag


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
