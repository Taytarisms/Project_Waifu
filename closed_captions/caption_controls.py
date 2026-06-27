from files.system_setup.settings import get_settings, save_settings
from files.system_setup.system_logger import Logger
from files.closed_captions.closed_captions import (
    set_caption,
    clear_caption,
    get_current_caption,
    status_text as _cc_status_text,
    is_typed_mode as _cc_is_typed_mode,
    SOURCE_NARRATOR,
    _caption_file_path,
    default_caption_file_path,
)

def status_text() -> str:
    return _cc_status_text()

def is_enabled() -> bool:
    raw = get_settings("captions_enabled")
    if raw is None or raw == "":
        return True
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("true", "1", "yes", "on")

def is_typed_mode() -> bool:
    return _cc_is_typed_mode()

def current_caption() -> str:
    return get_current_caption()

def get_file_path() -> str:
    return str(_caption_file_path())

def set_enabled(enabled: bool) -> None:
    save_settings("captions_enabled", bool(enabled))
    Logger.print(f"Captions {'enabled' if enabled else 'disabled'}.")

def set_typed_mode(enabled: bool) -> None:
    save_settings("captions_typed_mode", bool(enabled))
    Logger.print(f"Captions typed-mode {'enabled' if enabled else 'disabled'}.")

def set_caption_narrator(enabled: bool) -> None:
    save_settings("captions_for_narrator", bool(enabled))

def set_caption_user_messages(enabled: bool) -> None:
    save_settings("captions_for_user_messages", bool(enabled))

def set_caption_llm_replies(enabled: bool) -> None:
    save_settings("captions_for_llm_replies", bool(enabled))

def set_file_path(path: str) -> None:
    raw = str(path or "").strip()
    try:
        selected = raw.replace("\\", "/").rstrip("/")
        default = str(default_caption_file_path()).replace("\\", "/").rstrip("/")
        if selected.lower() == default.lower():
            raw = ""
    except Exception:
        pass
    save_settings("captions_file_path", raw)

def set_wrap_width(width: int) -> None:
    try:
        value = int(width)
    except Exception:
        value = 60
    if value < 10:
        value = 10
    save_settings("captions_wrap_width", value)

def set_window_lines(lines: int) -> None:
    try:
        value = int(lines)
    except Exception:
        value = 2
    if value < 1:
        value = 1
    save_settings("captions_window_lines", value)

async def clear() -> None:
    await clear_caption()


async def preview(text: str = "Caption preview test.") -> None:
    await set_caption(text, source=SOURCE_NARRATOR)

import asyncio as _asyncio

def _run_async_from_ui(coro) -> None:
    try:
        _asyncio.get_running_loop()
        _asyncio.create_task(coro)
        return
    except RuntimeError:
        pass
    try:
        from files.closed_captions import caption_coordinator
        target = getattr(caption_coordinator, "_pending_loop", None)
    except Exception:
        target = None

    if target is not None and target.is_running():
        _asyncio.run_coroutine_threadsafe(coro, target)
        return
    try:
        _asyncio.run(coro)
    except Exception as e:
        Logger.warn(f"failed to run {coro!r}: {e}")
        try:
            coro.close()
        except Exception:
            pass

def clear_sync() -> None:
    _run_async_from_ui(clear_caption())


def preview_sync(text: str = "Caption preview test.") -> None:
    _run_async_from_ui(set_caption(text, source=SOURCE_NARRATOR))
