import os
from pathlib import Path
from pydub import AudioSegment


def find_project_root() -> Path:
    here = Path(__file__).resolve()

    candidates = [
        Path.cwd(),
        Path.cwd() / "files",
        *here.parents,
    ]

    for base in candidates:
        if (base / "ffmpeg" / "ffmpeg.exe").exists():
            return base
        if (base / "files" / "ffmpeg" / "ffmpeg.exe").exists():
            return base / "files"

    return Path.cwd()


PROJECT_ROOT = find_project_root()
FFMPEG_DIR = PROJECT_ROOT / "ffmpeg"
FFMPEG_EXE = FFMPEG_DIR / "ffmpeg.exe"
FFPROBE_EXE = FFMPEG_DIR / "ffprobe.exe"
_FILES_DIR = Path(__file__).resolve().parent.parent
TTS_OUTPUT_DIR = _FILES_DIR / "tts" / "output"


def tts_output_dir(backend: str) -> Path:
    safe = "".join(c for c in str(backend or "misc").lower() if c.isalnum() or c in "-_") or "misc"
    target = TTS_OUTPUT_DIR / safe
    target.mkdir(parents=True, exist_ok=True)
    return target


def configure_audio_tools() -> None:
    if FFMPEG_DIR.exists():
        os.environ["PATH"] = str(FFMPEG_DIR) + os.pathsep + os.environ.get("PATH", "")

    if FFMPEG_EXE.exists():
        AudioSegment.converter = str(FFMPEG_EXE)

    if FFPROBE_EXE.exists():
        AudioSegment.ffprobe = str(FFPROBE_EXE)

def ffmpeg_cmd() -> str:
    return str(FFMPEG_EXE) if FFMPEG_EXE.exists() else "ffmpeg"

def list_output_devices():
    try:
        import sounddevice as sd
    except Exception:
        return []

    devices = []

    try:
        for index, device in enumerate(sd.query_devices()):
            if device.get("max_output_channels", 0) > 0:
                devices.append({
                    "index": index,
                    "name": device.get("name", f"Device {index}"),
                    "label": f"{index}: {device.get('name', f'Device {index}')}",
                })
    except Exception:
        return []

    return devices


def parse_output_device(value):
    if value in ("", None, "Default", "System Default"):
        return None

    try:
        return int(str(value).split(":", 1)[0].strip())
    except Exception:
        return None