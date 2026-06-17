from dataclasses import replace
from typing import Optional
import re

from files.system_setup.settings import get_bool_setting, get_settings
from files.system_setup.system_logger import Logger
from files.system_setup.gpu_compat import detect_nvidia_compute_capability
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
_ptt_pressed_keys: set[str] = set()
_ptt_active = False


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


def bind_push_to_talk(root) -> bool:
    global _ptt_bound_root
    if root is None:
        return False
    if _ptt_bound_root is root:
        return True
    _ptt_bound_root = root
    root.bind_all("<KeyPress>", _on_ptt_key_press, add="+")
    root.bind_all("<KeyRelease>", _on_ptt_key_release, add="+")
    return True


def _on_ptt_key_press(event):
    if _event_from_text_input(event):
        return
    key = _normalize_key(getattr(event, "keysym", ""))
    if not key:
        return
    _ptt_pressed_keys.add(key)
    if _ptt_active and _ptt_core_paused():
        _set_ptt_active(False)
        _ptt_pressed_keys.clear()
        _ptt_pressed_keys.add(key)
    if not active_core or not _is_push_to_talk() or _ptt_active:
        return
    if _combo_is_pressed():
        try:
            active_core.resume()
            _set_ptt_active(True)
            Logger.quiet_print(f"STT push-to-talk active: {ptt_key_display()}")
        except Exception as e:
            Logger.warn(f"Failed to resume STT for push-to-talk: {e}")


def _on_ptt_key_release(event):
    if _event_from_text_input(event) and not _ptt_active:
        return
    key = _normalize_key(getattr(event, "keysym", ""))
    if key:
        _ptt_pressed_keys.discard(key)
    if not active_core or not _is_push_to_talk() or not _ptt_active:
        return
    if not _combo_is_pressed():
        try:
            fn = getattr(active_core, "force_flush_and_pause", None)
            if callable(fn):
                fn()
            else:
                active_core.pause()
            _set_ptt_active(False)
            _ptt_pressed_keys.clear()
            Logger.quiet_print("STT push-to-talk released.")
        except Exception as e:
            Logger.warn(f"Failed to pause STT for push-to-talk: {e}")


def _input_device_index() -> Optional[int]:
    raw = _setting("stt_device_index", "")
    if raw in ("", None):
        return None

    try:
        return int(raw)
    except Exception:
        return None


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


def build_local_config():
    device = _setting("stt_device", "cuda")
    compute_type = _setting("stt_compute_type", "float16")
    if device == "cuda":
        if detect_nvidia_compute_capability() is None:
            Logger.warn("Local Whisper: no CUDA-capable NVIDIA GPU found; falling back to CPU.")
            device = "cpu"

        if device == "cpu" and str(compute_type).lower() == "float16":
            compute_type = "int8"

    return STTConfig(
        device=device,
        model_name=_setting("stt_model_name", "tiny"),
        compute_type=compute_type,
        vad_aggressiveness=int(_setting("stt_vad_aggressiveness", 1)),
        pre_speech_ms=int(_setting("stt_pre_speech_ms", 1000)),
        post_speech_ms=int(_setting("stt_post_speech_ms", 600)),
        segment_max_ms=int(_setting("stt_segment_max_ms", 15000)),
        min_length_ms=int(_setting("stt_min_length_ms", 1000)),
        enable_partials=bool(_setting("stt_enable_partials", True)),
        language=_setting("stt_language", "en") or None,
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

    if active_core is not None:
        Logger.print("STT is already running.")
        return True

    engine = _setting("stt_engine", "Local Whisper")
    device_index = _input_device_index()
    local_cfg = None

    callback = on_text or handle_stt_text

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

        elif engine == "OpenAI Whisper":
            if not OpenAIWhisperCore or not OpenAIWhisperConfig:
                Logger.warn("OpenAI Whisper backend unavailable.")
                return False

            active_core = OpenAIWhisperCore(
                input_device_index=device_index,
                cfg=build_openai_config(),
            )

        elif engine in ("Google", "Google Fallback"):
            if not GoogleSTTCore or not GoogleSTTConfig:
                Logger.warn("Google STT backend unavailable.")
                return False

            active_core = GoogleSTTCore(
                input_device_index=device_index,
                cfg=build_google_config(),
            )

        else:
            Logger.warn(f"Unknown STT engine: {engine!r}")
            return False

        active_engine = engine
        try:
            active_core.start(on_text=callback)
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
            active_core.start(on_text=callback)

        _set_ptt_active(False)
        _ptt_pressed_keys.clear()
        if _is_push_to_talk():
            active_core.pause()
            Logger.print(f"STT push-to-talk armed: hold {ptt_key_display()}.")
        Logger.print(f"STT started: {engine}")
        return True

    except Exception as e:
        Logger.warn(f"Failed to start STT: {e}")
        active_core = None
        active_engine = None
        return False


def stop_stt() -> bool:
    global active_core, active_engine, _ptt_active

    if active_core is None:
        Logger.print("STT is not running.")
        return False

    try:
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

    return True


def pause_stt() -> bool:
    if active_core is None:
        Logger.print("STT is not running.")
        return False

    try:
        active_core.pause()
        Logger.print("STT paused.")
        return True
    except Exception as e:
        Logger.warn(f"Failed to pause STT: {e}")
        return False


def resume_stt() -> bool:
    if active_core is None:
        Logger.print("STT is not running.")
        return False

    try:
        active_core.resume()
        _set_ptt_active(not _is_push_to_talk())
        Logger.print("STT resumed.")
        return True
    except Exception as e:
        Logger.warn(f"Failed to resume STT: {e}")
        return False


def is_running() -> bool:
    return active_core is not None


def status_text() -> str:
    if active_core is None:
        return "STT stopped."
    if _is_push_to_talk():
        state = "recording" if _ptt_active else f"armed, hold {ptt_key_display()}"
        return f"STT running: {active_engine} ({state})"
    return f"STT running: {active_engine}"
