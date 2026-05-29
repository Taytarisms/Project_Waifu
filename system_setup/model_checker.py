import subprocess
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT  = Path(__file__).resolve().parents[1]
MODELS_DIR    = PROJECT_ROOT / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
HF_API = "https://huggingface.co/api"

TIGHT_THRESHOLD       = 0.85   # 85–100 %  Tight
COMPLIANT_THRESHOLD   = 0.85   # < 85 %   Compatible
# > 100 %                        Non-compliant

def detect_vram() -> float:
    try:
        import torch
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            return round(props.total_memory / (1024 ** 3), 2)
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            mib = int(result.stdout.strip().splitlines()[0])
            return round(mib / 1024, 2)
    except Exception:
        pass

    return 0.0

def get_gpu_name() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[0]
    except Exception:
        pass

    return "Unknown GPU"

def estimate_vram_gb(file_size_gb: float) -> float:
    return round(file_size_gb * 1.15 + 0.5, 2)


def get_compatibility(required_vram_gb: float, available_vram_gb: float) -> str:
    if available_vram_gb <= 0:
        return "unknown"

    ratio = required_vram_gb / available_vram_gb

    if ratio > 1.0:
        return "non-compliant"
    if ratio >= TIGHT_THRESHOLD:
        return "tight"
    return "compatible"

def scan_local_models() -> list[dict[str, Any]]:
    available_vram = detect_vram()
    results: list[dict[str, Any]] = []

    if not MODELS_DIR.exists():
        return results

    for folder in sorted(MODELS_DIR.iterdir()):
        if not folder.is_dir():
            continue

        gguf_files  = sorted(folder.glob("*.gguf"))
        mmproj_files = [f for f in gguf_files if "mmproj" in f.name.lower()]
        model_files  = [f for f in gguf_files if "mmproj" not in f.name.lower()]

        for gguf in model_files:
            size_bytes   = gguf.stat().st_size
            size_gb      = round(size_bytes / (1024 ** 3), 2)
            vram_est     = estimate_vram_gb(size_gb)
            compat       = get_compatibility(vram_est, available_vram)

            results.append({
                "name":          folder.name,
                "filename":      gguf.name,
                "path":          gguf,
                "size_gb":       size_gb,
                "vram_estimate": vram_est,
                "compatibility": compat,
                "has_mmproj":    len(mmproj_files) > 0,
            })

    return results


def get_model_folder(model_name: str) -> Path:
    safe_name = model_name.replace("/", "_").replace(" ", "_")
    folder = MODELS_DIR / safe_name
    folder.mkdir(parents=True, exist_ok=True)
    return folder

_VISION_KEYWORDS = {
    "llava", "bakllava", "moondream", "minicpm-v", "cogvlm", "internvl",
    "qwen-vl", "qwenvl", "phi-3-vision", "phi3-vision", "gemma-4",
    "pixtral", "idefics", "cambrian", "eve-7b", "deepseek-vl",
}

_VISION_TAGS = {"image-text-to-text", "visual-question-answering", "multimodal"}

def _has_vision(model_meta: dict) -> bool:
    tags      = {t.lower() for t in model_meta.get("tags", [])}
    model_id  = model_meta.get("modelId", "").lower()
    pipeline  = (model_meta.get("pipeline_tag") or "").lower()

    if tags & _VISION_TAGS:
        return True
    if pipeline in ("image-text-to-text",):
        return True
    if any(kw in model_id for kw in _VISION_KEYWORDS):
        return True
    return False


def _parse_size_from_id(model_id: str) -> float | None:
    import re
    match = re.search(r"(\d+\.?\d*)[Bb]", model_id)
    if match:
        return float(match.group(1))
    return None


def _estimate_vram_from_params(params_b: float, bits: int = 4) -> float:
    bytes_needed = params_b * 1e9 * bits / 8
    gb = bytes_needed / (1024 ** 3)
    return round(gb * 1.10 + 0.5, 2)


def fetch_hf_models(
    query: str = "GGUF",
    limit: int = 30,
    filter_tag: str = "gguf",
) -> list[dict[str, Any]]:
    try:
        import urllib.request
        import urllib.parse

        params = urllib.parse.urlencode({
            "search":   query,
            "filter":   filter_tag,
            "limit":    limit,
            "sort":     "downloads",
            "direction": -1,
        })

        url = f"{HF_API}/models?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "AICompanionApp/1.0"})

        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode())

    except Exception as e:
        print(f"HF fetch failed: {e}")
        return []

    available_vram = detect_vram()
    results: list[dict[str, Any]] = []

    for entry in raw:
        model_id  = entry.get("modelId") or entry.get("id") or ""
        author    = model_id.split("/")[0] if "/" in model_id else ""
        params_b  = _parse_size_from_id(model_id)
        vram_est  = _estimate_vram_from_params(params_b) if params_b else None
        compat    = get_compatibility(vram_est, available_vram) if vram_est else "unknown"

        results.append({
            "model_id":      model_id,
            "author":        author,
            "downloads":     entry.get("downloads", 0),
            "likes":         entry.get("likes", 0),
            "has_vision":    _has_vision(entry),
            "params_b":      params_b,
            "vram_estimate": vram_est,
            "compatibility": compat,
            "tags":          entry.get("tags", []),
            "url":           f"https://huggingface.co/{model_id}",
        })

    return results


def fetch_gguf_files(model_id: str) -> list[dict[str, Any]]:
    try:
        import urllib.request
        safe_id = model_id.replace("/", "%2F") if "%2F" not in model_id else model_id
        url = f"{HF_API}/models/{model_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "AICompanionApp/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"Failed to fetch file list for {model_id}: {e}")
        return []

    files = []
    for sibling in data.get("siblings", []):
        fname = sibling.get("rfilename", "")
        if not fname.lower().endswith(".gguf"):
            continue
        size_bytes = sibling.get("size")
        size_gb    = round(size_bytes / (1024 ** 3), 2) if size_bytes else None
        files.append({
            "filename": fname,
            "size_gb":  size_gb,
            "url":      f"https://huggingface.co/{model_id}/resolve/main/{fname}",
        })
    return files


def download_gguf(
    model_id:  str,
    filename:  str,
    url:       str,
    on_progress: Any = None,
) -> Path | None:
    import urllib.request

    folder   = get_model_folder(model_id.split("/")[-1])
    dest     = folder / filename
    if dest.exists():
        print(f"Already exists: {dest}")
        return dest
    print(f"Downloading {filename} → {folder}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AICompanionApp/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024  # 1 MB chunks

            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_progress:
                        on_progress(downloaded, total)

        print(f"Download complete: {dest}")
        return dest

    except Exception as e:
        print(f"Download failed: {e}")
        if dest.exists():
            dest.unlink()   # clean up partial file
        return None