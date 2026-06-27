import asyncio
import threading
from files.system_setup.settings import get_settings, save_settings
from files.system_setup.system_logger import Logger
from .narrator import get_narrator

_bg_loop: asyncio.AbstractEventLoop | None = None
_bg_lock = threading.Lock()

def _ensure_bg_loop() -> asyncio.AbstractEventLoop:
    global _bg_loop
    with _bg_lock:
        if _bg_loop is not None and not _bg_loop.is_closed():
            return _bg_loop

        ready = threading.Event()

        def _runner():
            global _bg_loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _bg_loop = loop
            ready.set()
            loop.run_forever()

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        ready.wait(timeout=2.0)
        assert _bg_loop is not None
        return _bg_loop
    
def is_enabled() -> bool:
    return get_narrator().is_enabled()

def status_text() -> str:
    narrator = get_narrator()
    if not narrator.is_enabled():
        return "Narrator: off"
    backend_id = (get_settings("narrator_backend") or "novelai").strip().lower()
    backend = narrator.get_backend(backend_id)
    if backend is None:
        return f"Narrator: on (unknown backend '{backend_id}')"
    if not backend.is_available():
        return f"Narrator: on ({backend.display_name}) — unavailable"
    voice = (get_settings("narrator_voice") or "").strip() or "(default voice)"
    return f"Narrator: on ({backend.display_name}, voice: {voice})"


def list_backends() -> list[tuple[str, str, bool]]:
    return [
        (b.backend_id, b.display_name, b.is_available())
        for b in get_narrator().list_backends()
    ]


def list_voices_for(backend_id: str) -> list[str]:
    backend = get_narrator().get_backend(backend_id)
    return backend.list_voices() if backend else []

def set_enabled(enabled: bool) -> None:
    save_settings("narrator_enabled", bool(enabled))
    Logger.print(f"Narrator {'enabled' if enabled else 'disabled'}.")


def set_read_own_messages(enabled: bool) -> None:
    save_settings("narrator_read_own_messages", bool(enabled))


def set_backend(backend_id: str) -> None:
    save_settings("narrator_backend", str(backend_id))


def set_voice(voice: str) -> None:
    save_settings("narrator_voice", str(voice))


def set_output_device(device_index: int | str | None) -> None:
    if device_index in (None, ""):
        save_settings("narrator_output_device", "")
    else:
        try:
            save_settings("narrator_output_device", int(device_index))
        except Exception:
            save_settings("narrator_output_device", "")


def set_volume_db(volume_db: int) -> None:
    try:
        save_settings("narrator_volume_db", int(volume_db))
    except Exception:
        save_settings("narrator_volume_db", 0)

def interrupt() -> None:
    get_narrator().interrupt()


def test_voice(sample_text: str = "Testing, one two three.") -> None:
    loop = _ensure_bg_loop()
    coro = get_narrator().narrate_message(sample_text, source="manual")
    asyncio.run_coroutine_threadsafe(coro, loop)