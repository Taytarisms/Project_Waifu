import os
from pathlib import Path


def _prepend_path(path: Path) -> None:
    if not path.exists():
        return

    value = str(path)
    current = os.environ.get("PATH", "")
    parts = [part for part in current.split(os.pathsep) if part]
    if any(part.lower() == value.lower() for part in parts):
        return
    os.environ["PATH"] = value + os.pathsep + current


def configure_process_environment() -> None:
    files_dir = Path(__file__).resolve().parents[1]

    _prepend_path(files_dir / "ffmpeg")
    _prepend_path(files_dir / "bin")
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    # stopping sox warning since it's not on path by default
    os.environ.setdefault("PATH_SOX", "")
