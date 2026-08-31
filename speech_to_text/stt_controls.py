from dataclasses import replace
from typing import Optional
import re
import threading

from files.system_setup.settings import get_bool_setting, get_settings, save_settings
from files.system_setup.system_logger import Logger
from files.system_setup.gpu_compat import detect_nvidia_compute_capability
from files.speech_to_text.audio_devices import (
    DeviceResolution,
    enumerate_input_devices,
    parse_preferred_patterns,
    resolve_input_device,
)
from files.speech_to_text.stt_config import STTConfig

try:
    from files.main_loop.main_loop import message_queue
except Exception:
    message_queue = None

STTCore = None

try:
    from files.speech_to_text.openai_whisper import OpenAIWhisperCore, OpenAIWhisperConfig
except Exception as e:
    print(f"OpenAI Whisper unavailable: {e}")
    OpenAIWhisperCore = None
    OpenAIWhisperConfig = None

try:
    from files.speech_to_text.google_stt import GoogleSTTCore, GoogleSTTConfig
except Exception as e:
    print(f"Google STT unavailable: {e}")
    GoogleSTTCore = None
    GoogleSTTConfig = None


active_core = None
active_engine = None
last_partial = ""
_ptt_bound_root = None
_ptt_listener = None
_ptt_binding_mode = None
_ptt_binding_suspended = False
_ptt_pressed_keys: set[str] = set()
_ptt_active = False
_runtime_lock = threading.RLock()
_runtime_phase = "stopped"
_runtime_detail = ""
_manual_paused = False
_playback_suspended = False


_KEY_ALIASES = {
    " ": "space",
    "spacebar": "space",
    "control_l": "ctrl",
    "control_r": "ctrl",
    "control": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "shift_l": "shift",
    "shift_r": "shift",
    "alt_l": "alt",
    "alt_r": "alt",
    "option_l": "alt",
    "option_r": "alt",
    "return": "enter",
    "escape": "esc",
    "prior": "pageup",
    "next": "pagedown",
}


def _setting(key, default=None):
    value = get_settings(key)
    return default if value in ("", None) else value


def _set_runtime(phase: str, detail: str = "") -> None:
    global _runtime_phase, _runtime_detail
    with _runtime_lock:
        _runtime_phase = str(phase or "stopped")
        _runtime_detail = str(detail or "")


def runtime_snapshot() -> dict[str, object]:
    with _runtime_lock:
        return {
            "phase": _runtime_phase,
            "detail": _runtime_detail,
            "engine": active_engine,
            "running": active_core is not None and _runtime_phase not in ("stopped", "failed"),
            "manual_paused": _manual_paused,
            "playback_suspended": _playback_suspended,
            "push_to_talk_active": _ptt_active,
        }


def _backend_status(core, phase: str, detail: str = "") -> None:
    if core is not active_core:
        return
    with _runtime_lock:
        if _playback_suspended:
            _set_runtime("suspended", "Microphone paused while companion audio is playing.")
            return
        if _manual_paused:
            _set_runtime("paused", "Microphone capture is manually paused.")
            return
    _set_runtime(phase, detail)


def _normalize_key(key: str) -> str:
    key = str(key or "").strip().lower()
    return _KEY_ALIASES.get(key, key)


def normalize_ptt_combo(combo: str) -> str:
    parts = [
        _normalize_key(part)
        for part in re.split(r"[+\s]+", str(combo or "space"))
        if part.strip()
    ]
    unique = []
    for part in parts or ["space"]:
        if part not in unique:
            unique.append(part)
    return "+".join(unique)


def ptt_key_display() -> str:
    return normalize_ptt_combo(_setting("stt_ptt_key", "space"))


def _ptt_keys() -> set[str]:
    return set(normalize_ptt_combo(_setting("stt_ptt_key", "space")).split("+"))


def _is_push_to_talk() -> bool:
    return str(_setting("stt_mode", "Hot Mic")).strip().lower() == "push to talk"


def _combo_is_pressed() -> bool:
    keys = _ptt_keys()
    return bool(keys) and keys.issubset(_ptt_pressed_keys)


def _ptt_core_paused() -> bool:
    core = active_core
    if core is None:
        return False
    if hasattr(core, "_paused"):
        try:
            return bool(core._paused.is_set())
        except Exception:
            pass
    if hasattr(core, "_pause"):
        try:
            return bool(core._pause.is_set())
        except Exception:
            pass
    return bool(getattr(core, "paused", False))


def _set_ptt_active(value: bool) -> None:
    global _ptt_active
    _ptt_active = bool(value)


def _event_from_text_input(event) -> bool:
    try:
        widget = getattr(event, "widget", None)
        focus = widget.focus_get() if widget is not None else None
        cls = str(focus.winfo_class()).lower() if focus is not None else ""
        return cls in ("entry", "text", "spinbox")
    except Exception:
        return False


def _listener_key_name(key) -> str:
    char = getattr(key, "char", None)
    if char:
        return _normalize_key(char)
    name = getattr(key, "name", None)
    if name:
        return _normalize_key(name)
    raw = str(key or "")
    if raw.lower().startswith("key."):
        raw = raw[4:]
    return _normalize_key(raw.strip("'"))


def _on_global_ptt_key_press(key, *_) -> None:
    try:
        _handle_ptt_key_press(_listener_key_name(key))
    except Exception as exc:
        Logger.warn(f"Push-to-talk key press handler failed: {exc}")


def _on_global_ptt_key_release(key, *_) -> None:
    try:
        _handle_ptt_key_release(_listener_key_name(key))
    except Exception as exc:
        Logger.warn(f"Push-to-talk key release handler failed: {exc}")


def bind_push_to_talk(root) -> bool:
    global _ptt_bound_root, _ptt_listener, _ptt_binding_mode
    if root is None:
        return False
    if _ptt_listener is not None:
        try:
            if _ptt_listener.is_alive():
                return True
        except Exception:
            pass
        Logger.warn("STT push-to-talk listener stopped unexpectedly; restarting it.")
        try:
            _ptt_listener.stop()
        except Exception:
            pass
        _ptt_listener = None
        _ptt_binding_mode = None
    if _ptt_bound_root is root and _ptt_binding_mode == "in-app":
        return True

    try:
        from pynput import keyboard

        listener = keyboard.Listener(
            on_press=_on_global_ptt_key_press,
            on_release=_on_global_ptt_key_release,
        )
        listener.start()
        listener.wait()
        _ptt_listener = listener
        _ptt_bound_root = root
        _ptt_binding_mode = "global"

        Logger.print("STT push-to-talk global keyboard listener ready.")
        if getattr(listener, "IS_TRUSTED", True) is False:
            Logger.warn(
                "The operating system has not granted trusted keyboard monitoring access; "
                "global push-to-talk may require an accessibility permission."
            )
        return True
    except Exception as exc:
        Logger.warn(
            "Global push-to-talk listener unavailable; keyboard input will work only "
            f"while the app is focused. {exc}"
        )

    _ptt_bound_root = root
    root.bind_all("<KeyPress>", _on_ptt_key_press, add="+")
    root.bind_all("<KeyRelease>", _on_ptt_key_release, add="+")
    _ptt_binding_mode = "in-app"
    return True


def shutdown_push_to_talk_binding() -> None:
    global _ptt_bound_root, _ptt_listener, _ptt_binding_mode, _ptt_binding_suspended
    listener = _ptt_listener
    _ptt_listener = None
    _ptt_bound_root = None
    _ptt_binding_mode = None
    _ptt_binding_suspended = False
    if listener is not None:
        try:
            listener.stop()
            listener.join(timeout=0.5)
        except Exception:
            pass


def suspend_push_to_talk_binding(value: bool = True) -> None:
    global _ptt_binding_suspended
    _ptt_binding_suspended = bool(value)
    if value:
        _ptt_pressed_keys.clear()


def _handle_ptt_key_press(key: str) -> bool:
    if _ptt_binding_suspended:
        return False
    key = _normalize_key(key)
    if not key:
        return False
    if key not in _ptt_keys() and key not in {"ctrl", "shift", "alt"}:
        return False
    _ptt_pressed_keys.add(key)
    if _ptt_active and _ptt_core_paused():
        _set_ptt_active(False)
        _ptt_pressed_keys.clear()
        _ptt_pressed_keys.add(key)
    if (
        not active_core
        or not _is_push_to_talk()
        or _ptt_active
        or _manual_paused
        or _playback_suspended
    ):
        return False
    if _combo_is_pressed():
        try:
            active_core.resume()
            _set_ptt_active(True)
            _set_runtime("recording", f"Push-to-talk active: {ptt_key_display()}.")
            Logger.quiet_print(f"STT push-to-talk active: {ptt_key_display()}")
            return True
        except Exception as e:
            Logger.warn(f"Failed to resume STT for push-to-talk: {e}")
    return False


def _handle_ptt_key_release(key: str) -> bool:
    if _ptt_binding_suspended:
        return False
    key = _normalize_key(key)
    if key:
        _ptt_pressed_keys.discard(key)
    if not active_core or not _is_push_to_talk() or not _ptt_active:
        return False
    if not _combo_is_pressed():
        try:
            fn = getattr(active_core, "force_flush_and_pause", None)
            if callable(fn):
                fn()
            else:
                active_core.pause()
            _set_ptt_active(False)
            _ptt_pressed_keys.clear()
            _set_runtime("processing", "Transcribing the push-to-talk utterance.")
            Logger.quiet_print("STT push-to-talk released.")
            return True
        except Exception as e:
            Logger.warn(f"Failed to pause STT for push-to-talk: {e}")
    return False


def _on_ptt_key_press(event):
    key = _normalize_key(getattr(event, "keysym", ""))
    if _event_from_text_input(event) and key not in _ptt_keys():
        return
    if _handle_ptt_key_press(key):
        return "break"


def _on_ptt_key_release(event):
    key = _normalize_key(getattr(event, "keysym", ""))
    if _event_from_text_input(event) and key not in _ptt_keys() and not _ptt_active:
        return
    if _handle_ptt_key_release(key):
        return "break"


def discover_input_devices():
    try:
        return enumerate_input_devices()
    except Exception as exc:
        Logger.warn(f"Failed to enumerate STT input devices: {exc}")
        return []


def configured_device_resolution(*, persist: bool = False) -> DeviceResolution:
    raw_legacy = _setting("stt_device_index", "")
    try:
        legacy_index = None if raw_legacy in ("", None) else int(raw_legacy)
    except Exception:
        legacy_index = None

    resolution = resolve_input_device(
        discover_input_devices(),
        mode=str(_setting("stt_device_selection_mode", "automatic")),
        saved_id=str(_setting("stt_device_id", "")),
        saved_name=str(_setting("stt_device_name", "")),
        saved_host_api=str(_setting("stt_device_host_api", "")),
        preferred_patterns=parse_preferred_patterns(
            _setting("stt_device_preferred_patterns", "CABLE Output\nVB-Audio")
        ),
        legacy_index=legacy_index,
    )
    if persist and resolution.device is not None and not resolution.ambiguous:
        device = resolution.device
        values = {
            "stt_device_id": device.stable_id,
            "stt_device_name": device.name,
            "stt_device_host_api": device.host_api,
            "stt_device_index": str(device.index),
        }
        for key, value in values.items():
            if get_settings(key) != value:
                save_settings(key, value)
    return resolution


def _input_device_index() -> Optional[int]:
    resolution = configured_device_resolution(persist=True)
    return resolution.device.index if resolution.device is not None else None


def _submit_text(text: str, *, source: str = "stt"):
    text = (text or "").strip()
    if not text:
        return

    if not message_queue:
        Logger.warn("STT could not submit text")
        return

    username = get_settings("username") or get_settings("user_name") or "User"
    raw_text = f"{username}: {text}"

    meta = {
        "source": source,
    }

    message_queue.put((raw_text, True, None, meta))
    Logger.print(f"STT submitted: {text}")


def apply_wake_word(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""

    if not get_bool_setting("stt_wake_word_enabled", default=False):
        return text

    wake_word = str(get_settings("stt_wake_word") or "").strip()
    if not wake_word:
        return text

    lowered = text.lower()
    wake_lower = wake_word.lower()
    if not lowered.startswith(wake_lower):
        Logger.quiet_print(f"STT ignored without wake word: {text}")
        return ""

    remainder = text[len(wake_word):]
    remainder = re.sub(r"^[\s,;:.!?-]+", "", remainder).strip()
    if not remainder:
        Logger.quiet_print("STT wake word heard without command.")
        return ""
    return remainder


def handle_stt_text(text: str, is_partial: bool = False):
    global last_partial

    text = (text or "").strip()
    if not text:
        return

    if is_partial:
        last_partial = text
        return

    last_partial = ""
    filtered = apply_wake_word(text)
    if filtered:
        _submit_text(filtered)


def build_capture_config(*, enable_partials: Optional[bool] = None) -> STTConfig:
    partials = bool(_setting("stt_enable_partials", True))
    if enable_partials is not None:
        partials = bool(enable_partials)
    return STTConfig(
        vad_aggressiveness=int(_setting("stt_vad_aggressiveness", 1)),
        pre_speech_ms=int(_setting("stt_pre_speech_ms", 1000)),
        post_speech_ms=int(_setting("stt_post_speech_ms", 600)),
        segment_max_ms=int(_setting("stt_segment_max_ms", 15000)),
        min_length_ms=int(_setting("stt_min_length_ms", 1000)),
        enable_partials=partials,
        partial_interval_ms=int(_setting("stt_partial_interval_ms", 500)),
        language=_setting("stt_language", "en") or None,
        push_to_talk=_is_push_to_talk(),
    )


def build_local_config():
    device = _setting("stt_device", "cuda")
    compute_type = _setting("stt_compute_type", "float16")
    if device == "cuda":
        if detect_nvidia_compute_capability() is None:
            Logger.warn("Local Whisper: no CUDA-capable NVIDIA GPU found; falling back to CPU.")
            device = "cpu"

        if device == "cpu" and str(compute_type).lower() == "float16":
            compute_type = "int8"

    return replace(
        build_capture_config(),
        device=device,
        model_name=_setting("stt_model_name", "tiny"),
        compute_type=compute_type,
        beam_size=int(_setting("stt_beam_size", 1)),
    )


def build_openai_config():
    return OpenAIWhisperConfig(
        model=_setting("stt_openai_model", "gpt-4o-transcribe"),
        language=_setting("stt_language", "en") or "en",
        chunk_seconds=float(_setting("stt_openai_chunk_seconds", 5.0)),
    )


def build_google_config():
    energy = _setting("stt_google_energy_threshold", "")
    try:
        energy_threshold = None if energy in ("", None) else int(energy)
    except Exception:
        energy_threshold = None

    return GoogleSTTConfig(
        language=_setting("stt_language", "en-US") or "en-US",
        timeout=float(_setting("stt_google_timeout", 0.5)),
        phrase_time_limit=float(_setting("stt_google_phrase_time_limit", 8.0)),
        adjust_ambient=bool(_setting("stt_google_adjust_ambient", True)),
        energy_threshold=energy_threshold,
    )


def _get_local_whisper_core():
    global STTCore
    if STTCore is not None:
        return STTCore
    try:
        from files.speech_to_text.local_whisper import STTCore as core_cls
    except Exception as e:
        Logger.warn(f"Local Whisper unavailable: {e}")
        return None
    STTCore = core_cls
    return STTCore


def start_stt(on_text=None) -> bool:
    global active_core, active_engine, _ptt_active
    global _manual_paused, _playback_suspended

    if active_core is not None:
        Logger.print("STT is already running.")
        return True

    engine = _setting("stt_engine", "Local Whisper")
    resolution = configured_device_resolution(persist=True)
    if resolution.device is None:
        Logger.warn(resolution.reason)
        _set_runtime("failed", resolution.reason)
        return False
    device_index = resolution.device.index
    Logger.print(
        f"STT input selected: [{device_index}] {resolution.device.name} "
        f"via {resolution.device.host_api}. {resolution.reason}"
    )
    local_cfg = None

    callback = on_text or handle_stt_text

    def dispatch_text(text: str, is_partial: bool = False) -> None:
        callback(text, is_partial)
        if not is_partial and _is_push_to_talk() and active_core is not None:
            _set_runtime("armed", f"Hold {ptt_key_display()} to speak.")
    _manual_paused = False
    _playback_suspended = False
    _set_runtime("starting", f"Starting {engine}.")

    try:
        if engine == "Local Whisper":
            core_cls = _get_local_whisper_core()
            if not core_cls:
                Logger.warn("Local Whisper backend unavailable.")
                return False

            local_cfg = build_local_config()
            active_core = core_cls(
                input_device_index=device_index,
                cfg=local_cfg,
            )

        elif engine in ("OpenAI Whisper", "OpenAI Transcription"):
            if not OpenAIWhisperCore or not OpenAIWhisperConfig:
                Logger.warn("OpenAI Whisper backend unavailable.")
                return False

            active_core = OpenAIWhisperCore(
                input_device_index=device_index,
                cfg=build_openai_config(),
                capture_cfg=build_capture_config(enable_partials=False),
            )

        elif engine in ("Google", "Google Fallback"):
            if not GoogleSTTCore or not GoogleSTTConfig:
                Logger.warn("Google STT backend unavailable.")
                return False

            active_core = GoogleSTTCore(
                input_device_index=device_index,
                cfg=build_google_config(),
                capture_cfg=build_capture_config(enable_partials=False),
            )

        else:
            Logger.warn(f"Unknown STT engine: {engine!r}")
            return False

        active_engine = engine
        try:
            core = active_core
            active_core.start(
                on_text=dispatch_text,
                on_status=lambda phase, detail, source=core: _backend_status(
                    source, phase, detail
                ),
            )
        except Exception:
            active_cfg = getattr(active_core, "cfg", None) or local_cfg
            if engine != "Local Whisper" or getattr(active_cfg, "device", "") != "cuda":
                raise
            Logger.warn("Local Whisper CUDA start failed; retrying CPU as the final fallback.")
            try:
                if active_core is not None:
                    active_core.stop()
            except Exception:
                pass
            cpu_cfg = replace(active_cfg, device="cpu")
            if str(cpu_cfg.compute_type).lower() == "float16":
                cpu_cfg.compute_type = "int8"
            core_cls = _get_local_whisper_core()
            if not core_cls:
                return False
            active_core = core_cls(input_device_index=device_index, cfg=cpu_cfg)
            core = active_core
            active_core.start(
                on_text=dispatch_text,
                on_status=lambda phase, detail, source=core: _backend_status(
                    source, phase, detail
                ),
            )

        _set_ptt_active(False)
        _ptt_pressed_keys.clear()
        if _is_push_to_talk():
            active_core.pause()
            _set_runtime("armed", f"Hold {ptt_key_display()} to speak.")
            Logger.print(f"STT push-to-talk armed: hold {ptt_key_display()}.")
        else:
            snapshot = runtime_snapshot()
            if snapshot["phase"] == "starting":
                _set_runtime("listening", f"{engine} is listening.")
        Logger.print(f"STT started: {engine}")
        return True

    except Exception as e:
        Logger.warn(f"Failed to start STT: {e}")
        try:
            if active_core is not None:
                active_core.stop()
        except Exception:
            pass
        active_core = None
        active_engine = None
        _set_runtime("failed", str(e))
        return False


def restart_stt(on_text=None) -> bool:
    if active_core is not None:
        Logger.print("Restarting STT to apply the current engine and capture settings.")
        if not stop_stt():
            return False
    return start_stt(on_text=on_text)


def stop_stt() -> bool:
    global active_core, active_engine, _ptt_active
    global _manual_paused, _playback_suspended

    if active_core is None:
        Logger.print("STT is not running.")
        return False

    try:
        _set_runtime("stopping", f"Stopping {active_engine}.")
        active_core.stop()
        Logger.print(f"STT stopped: {active_engine}")
    except Exception as e:
        Logger.warn(f"Failed to stop STT: {e}")
        return False
    finally:
        active_core = None
        active_engine = None
        _set_ptt_active(False)
        _ptt_pressed_keys.clear()
        _manual_paused = False
        _playback_suspended = False
        _set_runtime("stopped", "")

    return True


def pause_stt() -> bool:
    global _manual_paused, _playback_suspended
    if active_core is None:
        Logger.print("STT is not running.")
        return False

    try:
        _manual_paused = True
        _playback_suspended = False
        active_core.pause()
        _set_runtime("paused", "Microphone capture is manually paused.")
        Logger.print("STT paused.")
        return True
    except Exception as e:
        Logger.warn(f"Failed to pause STT: {e}")
        return False


def resume_stt() -> bool:
    global _manual_paused, _playback_suspended
    if active_core is None:
        Logger.print("STT is not running.")
        return False

    try:
        _manual_paused = False
        _playback_suspended = False
        if _is_push_to_talk():
            active_core.pause()
            _set_ptt_active(False)
            _set_runtime("armed", f"Hold {ptt_key_display()} to speak.")
        else:
            active_core.resume()
            _set_ptt_active(False)
            _set_runtime("listening", f"{active_engine} is listening.")
        Logger.print("STT resumed.")
        return True
    except Exception as e:
        Logger.warn(f"Failed to resume STT: {e}")
        return False


def is_running() -> bool:
    snapshot = runtime_snapshot()
    return bool(snapshot["running"])


def status_text() -> str:
    snapshot = runtime_snapshot()
    phase = str(snapshot["phase"])
    detail = str(snapshot["detail"] or "")
    if active_core is None or phase == "stopped":
        return "STT stopped."
    label = f"STT {phase}: {active_engine}"
    return f"{label} — {detail}" if detail else label


def suspend_for_playback() -> bool:
    global _playback_suspended
    if (
        active_core is None
        or _is_push_to_talk()
        or _manual_paused
        or _playback_suspended
        or not get_bool_setting("stt_pause_during_playback", default=True)
    ):
        return False
    try:
        active_core.pause()
        _playback_suspended = True
        _set_runtime("suspended", "Microphone paused while companion audio is playing.")
        return True
    except Exception as exc:
        Logger.warn(f"Failed to suspend STT during playback: {exc}")
        return False


def resume_after_playback(was_suspended: bool = True) -> bool:
    global _playback_suspended
    if not was_suspended or not _playback_suspended:
        return False
    _playback_suspended = False
    if active_core is None or _manual_paused or _is_push_to_talk():
        return False
    try:
        active_core.resume()
        _set_runtime("listening", f"{active_engine} is listening.")
        return True
    except Exception as exc:
        Logger.warn(f"Failed to resume STT after playback: {exc}")
        _set_runtime("degraded", f"Could not resume after playback: {exc}")
        return False
