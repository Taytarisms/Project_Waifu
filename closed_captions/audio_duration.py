import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from files.system_setup.system_logger import Logger

_DURATION_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})\.(\d{2})")


def _find_ffmpeg() -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    cwd = os.environ.get("CWD", os.getcwd())
    local_path = os.path.abspath(os.path.join(cwd, "ffmpeg", "ffmpeg.exe"))
    if os.path.isfile(local_path):
        return local_path
    return ""


def find_duration_pydub(path: str) -> Optional[int]:
    try:
        from pydub import AudioSegment
    except Exception:
        return None
    try:
        segment = AudioSegment.from_file(path)
        return int(round(len(segment)))
    except Exception as e:
        Logger.warn(f"pydub probe failed for {path}: {e}")
        return None


def find_duration(path: str) -> Optional[int]:
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [ffmpeg, "-i", str(path)],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        stderr = proc.stderr or ""
    except Exception as e:
        Logger.warn(f"ffmpeg probe failed for {path}: {e}")
        return None

    match = _DURATION_RE.search(stderr)
    if not match:
        return None
    try:
        hours = int(match.group(1)) * 3_600_000
        minutes = int(match.group(2)) * 60_000
        seconds = int(match.group(3)) * 1_000
        centis = int(match.group(4)) * 10  # 1/100s = ms
        return hours + minutes + seconds + centis
    except Exception:
        return None


async def get_duration_ms(path: str) -> Optional[int]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        Logger.warn(f"File not found: {path}")
        return None
    
    duration = await asyncio.to_thread(find_duration_pydub, str(p))
    if duration is not None and duration > 0:
        return duration

    # ffmpeg fallback
    duration = await asyncio.to_thread(find_duration, str(p))
    if duration is not None and duration > 0:
        return duration

    return None
