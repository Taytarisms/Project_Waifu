import asyncio
import textwrap
import threading
from pathlib import Path
from typing import Callable, Optional

from files.system_setup.settings import get_settings
from files.system_setup.system_logger import Logger

SOURCE_NARRATOR = "narrator"
SOURCE_USER = "user"
SOURCE_LLM = "llm"
SOURCE_CLEAR = "clear"
_ALL_SOURCES = {SOURCE_NARRATOR, SOURCE_USER, SOURCE_LLM, SOURCE_CLEAR}
_DEFAULT_FILE = "closed_captions.txt"

_lock = threading.Lock()
_current_caption: str = ""
_subscribers: list[Callable[[str], None]] = []
_typed_interrupt = threading.Event()

_DEFAULT_WRAP_WIDTH = 60
_DEFAULT_WINDOW_LINES = 2

def app_root() -> Path:
    return Path(__file__).resolve().parents[2]

def default_caption_file_path() -> Path:
    return app_root() / _DEFAULT_FILE

def _get_bool_setting(name: str, default: bool = False) -> bool:
    try:
        raw = get_settings(name)
    except Exception:
        return default
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    return str(raw).strip().lower() in ("true", "1", "yes", "on")

def _get_int_setting(name: str, default: int, minimum: int = 1) -> int:
    try:
        raw = get_settings(name)
    except Exception:
        return default
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return value if value >= minimum else default

def _format_for_display(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""

    wrap_width = _get_int_setting(
        "captions_wrap_width", default=_DEFAULT_WRAP_WIDTH, minimum=10
    )
    window_lines = _get_int_setting(
        "captions_window_lines", default=_DEFAULT_WINDOW_LINES, minimum=1
    )
    lines = textwrap.wrap(
        text,
        width=wrap_width,
        break_long_words=False,
        break_on_hyphens=False,
    )
    if not lines:
        return ""

    if len(lines) > window_lines:
        lines = lines[-window_lines:]

    return "\n".join(lines)

def _caption_file_path() -> Path:
    raw = get_settings("captions_file_path")
    if raw:
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            return app_root() / path
        if path.name == _DEFAULT_FILE and path.parent.name == app_root().name and path.parent != app_root():
            return default_caption_file_path()
        return path
    return default_caption_file_path()

def _source_enabled(source: str) -> bool:
    if source == SOURCE_CLEAR:
        return True
    if not _get_bool_setting("captions_enabled", default=True):
        return False
    if source == SOURCE_NARRATOR:
        return _get_bool_setting("captions_for_narrator", default=True)
    if source == SOURCE_USER:
        return _get_bool_setting("captions_for_user_messages", default=True)
    if source == SOURCE_LLM:
        return _get_bool_setting("captions_for_llm_replies", default=False)
    return True

def is_typed_mode() -> bool:
    return _get_bool_setting("captions_typed_mode", default=False)

def get_current_caption() -> str:
    with _lock:
        return _current_caption

def status_text() -> str:
    if not _get_bool_setting("captions_enabled", default=True):
        return "Captions: off"
    parts = []
    if _get_bool_setting("captions_for_narrator", default=True):
        parts.append("narrator")
    if _get_bool_setting("captions_for_user_messages", default=True):
        parts.append("user")
    if _get_bool_setting("captions_for_llm_replies", default=False):
        parts.append("AI replies")
    if not parts:
        return "Captions: on (no sources enabled)"
    mode = "typed" if is_typed_mode() else "whole"
    return f"Captions: on / {mode} ({', '.join(parts)})"

def subscribe(callback: Callable[[str], None]) -> Callable[[], None]:
    with _lock:
        _subscribers.append(callback)

    def _unsubscribe() -> None:
        with _lock:
            try:
                _subscribers.remove(callback)
            except ValueError:
                pass
    return _unsubscribe

def interrupt_streaming() -> None:
    _typed_interrupt.set()

def _write_file_blocking(path: Path, formatted: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(formatted, encoding="utf-8")
    except Exception as e:
        Logger.warn(f"Failed to write {path}: {e}")


async def _write(text: str) -> None:
    formatted = _format_for_display(text)
    path = _caption_file_path()
    global _current_caption
    with _lock:
        _current_caption = formatted
        subs = list(_subscribers)
    await asyncio.to_thread(_write_file_blocking, path, formatted)
    for cb in subs:
        try:
            cb(formatted)
        except Exception as e:
            Logger.warn(f"Subscriber callback raised: {e}")


async def set_caption(text: str, source: str = SOURCE_NARRATOR) -> None:
    if source not in _ALL_SOURCES:
        Logger.warn(f"Unknown source kind: {source!r}")
        source = SOURCE_NARRATOR

    if not _source_enabled(source):
        return

    await _write((text or "").strip())

async def clear_caption() -> None:
    await set_caption("", source=SOURCE_CLEAR)

async def stream_caption(
    text: str,
    audio_duration_ms: int,
    source: str = SOURCE_NARRATOR,
) -> bool:
    if source not in _ALL_SOURCES:
        Logger.warn(f"Unknown source kind: {source!r}")
        source = SOURCE_NARRATOR

    if not _source_enabled(source):
        return False

    text = (text or "").strip()
    if not text:
        return False
    if audio_duration_ms <= 0:
        await _write(text)
        return True

    words = text.split()
    total_chars = sum(len(w) for w in words)
    if total_chars <= 0:
        await _write(text)
        return True

    ms_per_char = audio_duration_ms / total_chars
    _typed_interrupt.clear()

    revealed_parts: list[str] = []
    for word in words:
        if _typed_interrupt.is_set():
            await _write(text)
            return False

        revealed_parts.append(word)
        await _write(" ".join(revealed_parts))
        sleep_ms = len(word) * ms_per_char
        try:
            await asyncio.sleep(sleep_ms / 1000.0)
        except asyncio.CancelledError:
            await _write(text)
            raise

    return True
