import asyncio
import threading
import time
from typing import Optional

from concurrent.futures import Future as _Future

from files.system_setup.system_logger import Logger

from .audio_duration import get_duration_ms
from .closed_captions import (
    set_caption,
    stream_caption,
    interrupt_streaming,
    is_typed_mode,
    SOURCE_NARRATOR,
)

_AUDIO_READY_GRACE_MS = 800
_lock = threading.Lock()
_pending_text: str = ""
_pending_source: str = SOURCE_NARRATOR
_pending_set_at_ms: float = 0.0
_pending_loop: Optional[asyncio.AbstractEventLoop] = None
_pending_resolved: bool = False
_active_stream_future: Optional[_Future] = None
_active_stream_task: Optional[asyncio.Task] = None

def _resolve_loop() -> Optional[asyncio.AbstractEventLoop]:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return _pending_loop


def _schedule(coro) -> Optional[_Future]:
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is not None:
        try:
            asyncio.create_task(coro)
        except Exception as e:
            Logger.warn(f"create_task failed: {e}")
            coro.close()
        return None

    target = _pending_loop
    if target is None or not target.is_running():
        Logger.warn("no event loop available; dropping caption update")
        coro.close()
        return None
    try:
        return asyncio.run_coroutine_threadsafe(coro, target)
    except Exception as e:
        Logger.warn(f"run_coroutine_threadsafe failed: {e}")
        try:
            coro.close()
        except Exception:
            pass
        return None

def set_pending(text: str, source: str = SOURCE_NARRATOR) -> None:
    global _pending_text, _pending_source, _pending_set_at_ms
    global _pending_loop, _pending_resolved
    loop_ref: Optional[asyncio.AbstractEventLoop] = None
    try:
        loop_ref = asyncio.get_running_loop()
    except RuntimeError:
        loop_ref = _pending_loop

    with _lock:
        _pending_text = (text or "").strip()
        _pending_source = source
        _pending_set_at_ms = time.perf_counter() * 1000
        _pending_resolved = False
        if loop_ref is not None:
            _pending_loop = loop_ref
    _cancel_active_stream_sync()
    if not is_typed_mode():
        if _pending_text:
            _schedule(set_caption(_pending_text, source=source))
        with _lock:
            _pending_resolved = True


def audio_ready(audio_path: str) -> None:
    if not is_typed_mode():
        return
    with _lock:
        if _pending_resolved:
            return
        text = _pending_text
        source = _pending_source
    if not text:
        return
    _schedule(_audio_ready_async(audio_path, text, source))


def audio_ready_with_duration(duration_ms: int) -> None:
    if not is_typed_mode():
        return
    with _lock:
        if _pending_resolved:
            return
        text = _pending_text
        source = _pending_source
    if not text or duration_ms is None or duration_ms <= 0:
        return
    _schedule(_audio_ready_duration_async(int(duration_ms), text, source))


def finish_pending() -> None:
    global _pending_resolved
    with _lock:
        if _pending_resolved:
            return
        text = _pending_text
        source = _pending_source
        _pending_resolved = True
    if text:
        _schedule(set_caption(text, source=source))


def cancel_pending() -> None:
    global _pending_resolved
    interrupt_streaming()
    _cancel_active_stream_sync()
    with _lock:
        _pending_resolved = True

async def _audio_ready_async(audio_path: str, text: str, source: str) -> None:
    global _pending_resolved, _active_stream_future, _active_stream_task

    duration_ms = await get_duration_ms(audio_path)
    if duration_ms is None or duration_ms <= 0:
        Logger.warn(f"Could not probe duration of {audio_path}")
        with _lock:
            if _pending_resolved:
                return
            _pending_resolved = True
        await set_caption(text, source=source)
        return
    await _start_stream_task(duration_ms, text, source)

async def _audio_ready_duration_async(duration_ms: int, text: str, source: str) -> None:
    await _start_stream_task(duration_ms, text, source)

async def _start_stream_task(duration_ms: int, text: str, source: str) -> None:
    global _pending_resolved, _active_stream_task

    with _lock:
        if _pending_resolved:
            return
        _pending_resolved = True
    task = asyncio.create_task(stream_caption(text, duration_ms, source=source))
    _active_stream_task = task
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        if _active_stream_task is task:
            _active_stream_task = None


def _cancel_active_stream_sync() -> None:
    global _active_stream_task, _active_stream_future
    task = _active_stream_task
    fut = _active_stream_future
    _active_stream_task = None
    _active_stream_future = None
    if task is not None:
        try:
            task.cancel()
        except Exception:
            pass
    if fut is not None:
        try:
            fut.cancel()
        except Exception:
            pass
