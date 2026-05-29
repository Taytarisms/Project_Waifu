import threading
import asyncio
import time
from pathlib import Path

from files.system_setup.settings import get_settings, get_auth
from files.system_setup.system_logger import Logger
from files.ui.pages.logs import LogManager

from files.llm.openai_llm import open_response
from files.llm.LocalLLM import response_local
from files.llm.boilerplate_novel import NovelAIClient
from files.llm.grok import grok_response
from files.llm.claude import claude_response
from files.llm.gemini import gemini_response

from files.vision.VisionContext import Backend
from files.vision.VisionWatcher import WatchMode, InjectionResult, get_watcher

from files.tts import elevenlabs_tts
from files.tts import FishTTS
from files.tts import EdgeTTS
from files.tts.novel_ai_tts import NovelTTSClient
from files.tts.openai_tts import openai_tts
from files.tts.inworld_tts import stream_tts
import os
os.environ["PATH_SOX"] = ""  # suppress sox warning, may not always work though.
from files.main_loop import turn_control

provider = get_settings("tts_model")
chat_model = get_settings("chat_model")

novel_client: NovelAIClient | None = None
novel_tts_client: NovelTTSClient | None = None

character: str | None = None
device_index: int | None = None
novelai_voice_seed: str = "Ligeia"


def interrupt() -> None:
    turn_control.request_interrupt()
    try:
        from files.closed_captions import caption_coordinator
        caption_coordinator.cancel_pending()
    except Exception:
        pass
    stop_tts()


def pause_tts() -> None:
    turn_control.request_pause()
    core = _local_tts_core
    if core is not None:
        try:
            core.pause()
        except Exception:
            pass


def resume_tts() -> None:
    turn_control.request_resume()
    core = _local_tts_core
    if core is not None:
        try:
            core.resume()
        except Exception:
            pass


def is_tts_paused() -> bool:
    return turn_control.is_paused()


def stop_tts() -> None:
    try:
        stop_local_tts()
    except Exception:
        pass
    try:
        from files.tts import openai_tts
        openai_tts.interrupt_tts_playback(True)
    except Exception:
        pass
    try:
        from files.tts import novel_ai_tts
        novel_ai_tts.interrupt()
    except Exception:
        pass
    for module in (elevenlabs_tts, FishTTS, EdgeTTS):
        try:
            fn = getattr(module, "request_interrupt", None)
            if callable(fn):
                fn()
        except Exception:
            pass
    try:
        from files.tts import inworld_tts
        fn = getattr(inworld_tts, "request_interrupt", None)
        if callable(fn):
            fn()
    except Exception:
        pass


def clear_interrupt() -> None:
    turn_control.clear_interrupt()
    try:
        from files.tts import openai_tts
        openai_tts.interrupt_tts_playback(False)
    except Exception:
        pass


def was_interrupted() -> bool:
    return turn_control.is_interrupted()


def raise_if_interrupted() -> None:
    turn_control.raise_if_interrupted()

def _lm_info(text: str) -> None:
    try:
        LogManager.info(text)
    except Exception:
        pass

def _lm_model(text: str) -> None:
    try:
        LogManager.model(text)
    except Exception:
        pass

def _lm_perf(label: str, duration_ms: float, detail: str = "") -> None:
    try:
        LogManager.perf(label, duration_ms=duration_ms, detail=detail)
    except Exception:
        pass

def _lm_error(text: str, *, exc_info: bool = False) -> None:
    try:
        LogManager.error(text, exc_info=exc_info)
    except Exception:
        pass

def _as_int_or_none(value):
    try:
        return None if value in ("", None) else int(value)
    except Exception:
        return None
    

def _novelai_character_name() -> str:
    raw = (
        character
        or get_settings("active_novelai_character")
        or get_settings("character_name")
        or ""
    )
    name = str(raw or "").strip()
    if not name or name.lower() in ("none", "character", "default"):
        return "Abby"
    return name.removesuffix(".json")

def set_character(name: str | None) -> None:
    global character
    character = name

async def set_novelai_voice_seed(seed: str) -> None:
    global novelai_voice_seed
    novelai_voice_seed = seed

def set_device_index(index: int | None) -> None:
    global device_index
    device_index = index

_TTS_ENGINE_ALIASES = {
    "ElevenLabs": "elevenlabs",
    "Elevenlabs": "elevenlabs",
    "elevenlabs": "elevenlabs",
    "NovelAI": "novelai_tts",
    "NovelAI TTS": "novelai_tts",
    "novelai": "novelai_tts",
    "novelai_tts": "novelai_tts",
    "OpenAI": "openai_tts",
    "OpenAI TTS": "openai_tts",
    "openai": "openai_tts",
    "openai_tts": "openai_tts",
    "InWorld": "inworld",
    "Inworld": "inworld",
    "inworld": "inworld",
    "EdgeTTS": "edge",
    "Edge TTS": "edge",
    "edge": "edge",
    "FishSpeech": "fishspeech",
    "fishspeech": "fishspeech",
    "Kokoro": "kokoro",
    "kokoro": "kokoro",
    "Qwen3 TTS": "qwen3",
    "Qwen3": "qwen3",
    "qwen3": "qwen3"
    }

_ENGINE_TO_LEGACY_PROVIDER = {
    "elevenlabs": "ElevenLabs",
    "novelai_tts": "NovelAI",
    "openai_tts": "OpenAI",
    "inworld": "InWorld",
    "edge": "EdgeTTS",
    "fishspeech": "FishSpeech",
    "kokoro": "Kokoro",
    "qwen3": "Qwen3 TTS"
}

_local_tts_core = None
_local_tts_identity = None

def _current_tts_engine() -> tuple[str, str]:
    legacy_provider = get_settings("tts_model") or provider or ""
    engine_key = get_settings("tts_engine") or ""

    if not engine_key:
        engine_key = _TTS_ENGINE_ALIASES.get(legacy_provider, legacy_provider)
    else:
        engine_key = _TTS_ENGINE_ALIASES.get(engine_key, engine_key)

    display_provider = _ENGINE_TO_LEGACY_PROVIDER.get(engine_key, legacy_provider or engine_key)
    return engine_key, display_provider


def stop_local_tts() -> None:
    global _local_tts_core, _local_tts_identity
    if _local_tts_core is not None:
        try:
            _local_tts_core.stop()
        except Exception as e:
            Logger.warn(f"Error stopping local TTS core: {e}")
            _lm_error(f"Error stopping local TTS core: {e}", exc_info=True)
    _local_tts_core = None
    _local_tts_identity = None


def local_tts_runtime(engine_key: str, identity: tuple, factory, text: str) -> None:
    global _local_tts_core, _local_tts_identity

    runner_start = time.perf_counter()

    try:
        turn_control.raise_if_interrupted()
        cold_start = _local_tts_core is None or _local_tts_identity != identity

        if cold_start:
            stop_local_tts()

            load_start = time.perf_counter()
            _local_tts_core = factory()
            _local_tts_identity = identity

            _lm_model(f"Loading local TTS core: engine={engine_key}, identity={identity}")

            _local_tts_core.start(
                on_synth_start=lambda t: Logger.quiet_print(f"[{engine_key}] synth: {t!r}"),
                on_synth_done=lambda t: Logger.quiet_print(f"[{engine_key}] synth done: {t!r}"),
            )

            load_ms = (time.perf_counter() - load_start) * 1000
            _lm_perf("Local TTS core load", load_ms, detail=f"engine={engine_key}")
            _lm_model(f"Local TTS core ready: engine={engine_key}")

        synth_start = time.perf_counter()
        turn_control.raise_if_interrupted()
        _local_tts_core.feed(text)
        _local_tts_core.wait_until_done()
        turn_control.raise_if_interrupted()

        synth_ms = (time.perf_counter() - synth_start) * 1000
        total_ms = (time.perf_counter() - runner_start) * 1000

        _lm_perf(
            "Local TTS synthesis",
            synth_ms,
            detail=f"engine={engine_key}, chars={len(text or '')}",
        )
        _lm_perf(
            "Local TTS route total",
            total_ms,
            detail=f"engine={engine_key}, chars={len(text or '')}",
        )

    except Exception as e:
        if isinstance(e, turn_control.TurnInterrupted):
            Logger.warn(f"Local TTS interrupted for {engine_key}.")
            stop_local_tts()
            return
        Logger.error(f"Local TTS route failed for {engine_key}: {e}")
        _lm_error(f"Local TTS route failed for {engine_key}: {e}", exc_info=True)
        stop_local_tts()

def _files_dir() -> Path:
    return Path(__file__).resolve().parents[1]

def kokoro_directory() -> str:
    return str(_files_dir() / "tts" / "models")

def kokoro_core():
    from files.tts.LocalBase import TTSCore
    from files.tts.kokoro import KokoroTTSEngine, KokoroTTSConfig

    cfg_kwargs = {
        "voice": get_settings("kokoro_voice") or "af_heart",
        "speed": float(get_settings("kokoro_speed") or 1.0),
        "output_device_index": _as_int_or_none(get_settings("tts_output_device")),
    }

    cfg_kwargs["model_dir"] = kokoro_directory()
    model_file = get_settings("kokoro_model_file") or ""
    if model_file:
        cfg_kwargs["model_file"] = model_file
    voices_file = get_settings("kokoro_voices_file") or ""
    if voices_file:
        cfg_kwargs["voices_file"] = voices_file

    cfg = KokoroTTSConfig(**cfg_kwargs)
    return TTSCore(KokoroTTSEngine(cfg), cfg)

def qwen3_core():
    from files.tts.LocalBase import TTSCore
    from files.tts.qwen3 import Qwen3TTSEngine, Qwen3TTSConfig

    preset_name = (get_settings("qwen3_tts_preset") or "").strip()
    if preset_name:
        from files.tts.qwen3_voice_presets import ensure_preset_from_settings
        preset = ensure_preset_from_settings("qwen3", get_settings)
        if preset is None:
            raise ValueError(f"Could not load or create preset {preset_name!r}")
        if getattr(preset, "engine", "qwen3") != "qwen3":
            raise ValueError(f"Preset {preset_name!r} is engine={preset.engine!r}, not 'qwen3'.")
        cfg = preset.to_config()
    else:
        cfg = Qwen3TTSConfig(
            model_id=get_settings("qwen3_tts_model_id") or "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
            device=get_settings("qwen3_tts_device") or "auto",
            backend=get_settings("qwen3_tts_backend") or "faster",
            speaker=get_settings("qwen3_tts_speaker") or "ryan",
            language=get_settings("qwen3_tts_language") or "English",
            instruct=get_settings("qwen3_tts_instruct") or "",
            ref_audio=get_settings("qwen3_tts_ref_audio") or None,
            ref_text=get_settings("qwen3_tts_ref_text") or "",
            xvec_only=bool(get_settings("qwen3_tts_xvec_only") if get_settings("qwen3_tts_xvec_only") not in ("", None) else True),
            streaming_chunk_size=int(get_settings("qwen3_tts_streaming_chunk_size") or 8),
            output_device_index=_as_int_or_none(get_settings("tts_output_device")),
        )
    cfg.output_device_index = _as_int_or_none(get_settings("tts_output_device"))
    return TTSCore(Qwen3TTSEngine(cfg), cfg)

async def speak(text):
    global novel_tts_client, novelai_voice_seed, device_index, provider

    text = text or ""
    turn_control.raise_if_interrupted()
    engine_key, current_provider = _current_tts_engine()
    provider = current_provider
    speak_start = time.perf_counter()

    _lm_info(
        f"TTS route requested: provider={current_provider}, "
        f"engine={engine_key}, chars={len(text or '')}"
    )

    def _return_thread(playback_thread):
        route_ms = (time.perf_counter() - speak_start) * 1000
        _lm_perf(
            "TTS route setup",
            route_ms,
            detail=f"provider={current_provider}, engine={engine_key}, chars={len(text or '')}",
        )
        return playback_thread

    if engine_key == "elevenlabs":
        Logger.print("ROUTING ElevenLabs")
        _lm_info("TTS routed: ElevenLabs")
        voice_id = (
            get_settings("eleven_voice_id")
            or get_settings("eleven_voice")
            or elevenlabs_tts.voiceid
        )
        playback_thread = threading.Thread(
            target=lambda: elevenlabs_tts.synthesize(text, voice_id),
            daemon=True,
        )
        playback_thread.start()
        return _return_thread(playback_thread)

    if engine_key == "fishspeech":
        Logger.print("ROUTING FishSpeech!")
        _lm_info("TTS routed: FishSpeech")

        fish_voice_id = (
            get_settings("fish_voice_id")
            or get_settings("fish_reference_id")
            or FishTTS.voice_id
        )

        FishTTS.API_URL = get_settings("fish_api_url") or FishTTS.API_URL
        FishTTS.MODEL = get_settings("fish_model") or FishTTS.MODEL
        FishTTS.RATE = int(get_settings("fish_rate") or FishTTS.RATE)
        FishTTS.CHANNELS = int(get_settings("fish_channels") or FishTTS.CHANNELS)
        FishTTS.OUTPUT_DEVICE = _as_int_or_none(get_settings("tts_output_device"))

        playback_thread = threading.Thread(
            target=lambda: asyncio.run(FishTTS.run_tts(text, fish_voice_id)),
            daemon=True,
        )
        playback_thread.start()
        return _return_thread(playback_thread)

    if engine_key == "edge":
        Logger.print("ROUTING EdgeTTS")
        _lm_info("TTS routed: EdgeTTS")
        EdgeTTS.VOICE = get_settings("edge_voice") or EdgeTTS.VOICE
        playback_thread = threading.Thread(
            target=lambda: asyncio.run(EdgeTTS.read_chat(text)),
            daemon=True,
        )
        playback_thread.start()
        return _return_thread(playback_thread)

    if engine_key == "novelai_tts":
        Logger.quiet_print("ROUTING NovelAI TTS")
        _lm_info("TTS routed: NovelAI TTS")
        api_token = get_auth("novelai", "token") or get_settings("NOVELAI_TOKEN")
        if novel_tts_client is None:
            novel_tts_client = NovelTTSClient(api_token=api_token)
        try:
            voice_seed = get_settings("novel_tts_voice_seed") or novelai_voice_seed
            t0 = time.perf_counter()
            file_id = await novel_tts_client.generate_tts(
                speak=text,
                voice_seed=voice_seed,
            )
            gen_ms = (time.perf_counter() - t0) * 1000
            _lm_perf(
                "NovelAI TTS generation",
                gen_ms,
                detail=f"chars={len(text or '')}, voice_seed={voice_seed}",
            )
            Logger.print(f"NovelAI TTS file ID: {file_id}")
            try:
                from files.closed_captions import caption_coordinator
                caption_coordinator.audio_ready(f"audio/{file_id}.mp3")
            except Exception as _ce:
                Logger.warn(f"Captions Error raised: {_ce}")

            playback_thread = threading.Thread(
                target=novel_tts_client.play_tts,
                args=(file_id, _as_int_or_none(get_settings("tts_output_device")) or device_index, 10),
                daemon=True,
            )
            playback_thread.start()
            return _return_thread(playback_thread)
        except Exception as e:
            Logger.error(f"Error during NovelAI TTS generation: {e}")
            _lm_error(f"Error during NovelAI TTS generation: {e}", exc_info=True)
            return None

    if engine_key == "openai_tts":
        Logger.print("ROUTING OpenAI TTS")
        _lm_info("TTS routed: OpenAI TTS")
        playback_thread = threading.Thread(
            target=lambda: openai_tts(
                text=text,
                model=get_settings("openai_tts_model") or "gpt-4o-mini-tts",
                voice=get_settings("openai_tts_voice") or "nova",
            ),
            daemon=True,
        )
        playback_thread.start()
        return _return_thread(playback_thread)

    if engine_key == "inworld":
        Logger.print("ROUTING INWORLD")
        _lm_info("TTS routed: InWorld")
        try:
            inworld_api = get_auth("inworld", "token") or get_settings("INWORLD_TOKEN")
            inworld_voice = (
                get_settings("inworld_tts_voice_id")
                or get_settings("inworld_tts_voice")
                or "Ashley"
            )
            inworld_model = (
                get_settings("inworld_tts_model_id")
                or get_settings("inworld_tts_model")
                or "inworld-tts-1.5-mini"
            )

            playback_thread = threading.Thread(
                target=lambda: asyncio.run(
                    stream_tts(inworld_api, text, inworld_voice, inworld_model)
                ),
                daemon=True,
            )
            playback_thread.start()
            return _return_thread(playback_thread)
        except Exception as e:
            Logger.warn(f"Error during InWorld TTS generation: {e}")
            _lm_error(f"Error during InWorld TTS generation: {e}", exc_info=True)
            return None

    if engine_key == "kokoro":
        Logger.print("ROUTING Kokoro Local TTS")
        _lm_info("TTS routed: Kokoro Local TTS")
        identity = (
            engine_key,
            get_settings("kokoro_voice"),
            get_settings("kokoro_speed"),
            kokoro_directory(),
            get_settings("kokoro_model_file"),
            get_settings("kokoro_voices_file"),
            get_settings("tts_output_device"),
        )
        playback_thread = threading.Thread(
            target=lambda: local_tts_runtime(engine_key, identity, kokoro_core, text),
            daemon=True,
        )
        playback_thread.start()
        return _return_thread(playback_thread)

    if engine_key == "qwen3":
        Logger.print("ROUTING Qwen3 Local TTS")
        _lm_info("TTS routed: Qwen3 Local TTS")
        identity = (
            engine_key,
            get_settings("qwen3_tts_preset"),
            get_settings("qwen3_tts_model_id"),
            get_settings("qwen3_tts_backend"),
            get_settings("qwen3_tts_device"),
            get_settings("qwen3_tts_speaker"),
            get_settings("qwen3_tts_language"),
            get_settings("qwen3_tts_instruct"),
            get_settings("qwen3_tts_ref_audio"),
            get_settings("qwen3_tts_ref_text"),
            get_settings("qwen3_tts_xvec_only"),
            get_settings("qwen3_tts_streaming_chunk_size"),
            get_settings("tts_output_device"),
        )
        playback_thread = threading.Thread(
            target=lambda: local_tts_runtime(engine_key, identity, qwen3_core, text),
            daemon=True,
        )
        playback_thread.start()
        return _return_thread(playback_thread)

    msg = f"Unknown TTS provider/engine: provider={current_provider!r} engine={engine_key!r}"
    Logger.warn(msg)
    _lm_error(msg)
    return None

_CHAT_MODEL_TO_BACKEND = {
    "OpenAI":  Backend.CHATGPT,
    "Claude":  Backend.CLAUDE,
    "Gemini":  Backend.GEMINI,
    "Grok":    Backend.GROK,
    "NovelAI": Backend.NOVELAI,
    "Local":   Backend.LOCAL,
}

async def _capture_vision_frame(current_chat_model: str, user_text: str = "") -> None:
    watcher = get_watcher()
    backend = _CHAT_MODEL_TO_BACKEND.get(current_chat_model)

    if backend is None or watcher.mode == WatchMode.DISABLED:
        watcher.last_injection = InjectionResult(
            content=[], vision_active=False,
        )
        return

    try:
        t0 = time.perf_counter()
        result = await watcher.inject_async(backend=backend, text=user_text)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        watcher.last_injection = result

        _lm_perf(
            "Vision capture/injection",
            elapsed_ms,
            detail=f"backend={backend.value}, active={result.vision_active}",
        )

        if result.vision_active:
            age = f"{result.frame.age_seconds:.1f}s" if result.frame else "?"
            Logger.quiet_print(
                f"{backend.value} frame captured "
                f"(mode={watcher.mode.name}, age={age})"
            )
            _lm_info(
                f"Vision frame captured: backend={backend.value}, mode={watcher.mode.name}, age={age}"
            )
    except Exception as e:
        Logger.warn(f"capture error: {e}")
        _lm_error(f"capture error: {e}", exc_info=True)
        watcher.last_injection = InjectionResult(
            content=[], vision_active=False,
        )


async def response_novel(prompt: str) -> str:
    global novel_client

    api_token = get_auth("novelai", "token") or get_settings("NOVELAI_TOKEN")
    if novel_client is None:
        novel_client = NovelAIClient(api_token=api_token)
        _lm_model("NovelAI client initialized")

    parameters = {
        "max_output_length": int(get_settings("max_output_length") or 150),
        "min_length": 1,
        "temperature": float(get_settings("novel_temperature") or get_settings("temperature") or 0.8),
        "top_p": float(get_settings("novel_top_p") or 0.9),
        "top_k": int(get_settings("novel_top_k") or 40),
        "frequency_penalty": 0,
        "presence_penalty": 0,
        "repetition_penalty": float(get_settings("repetition_penalty") or 1.35),
        "length_penalty": 1.0,
        "stop": [],
        "banlist_mode": get_settings("novel_banlist_mode") or "soft",
    }

    t0 = time.perf_counter()
    try:
        result = await novel_client.generate_prompt_completion(
            prompt,
            parameters,
            character=_novelai_character_name(),
            route=get_settings("novel_generation_route") or "high",
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _lm_perf(
            "LLM generation",
            elapsed_ms,
            detail=f"provider=NovelAI-direct-prompt, input_chars={len(prompt or '')}, output_chars={len(result or '')}",
        )
        return result
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _lm_perf(
            "LLM failed generation duration",
            elapsed_ms,
            detail=f"provider=NovelAI-direct-prompt, input_chars={len(prompt or '')}",
        )
        _lm_error(f"Error in NovelAI direct prompt generation: {exc}", exc_info=True)
        Logger.error(f"Error in NovelAI direct prompt generation: {exc}")
        return ""

async def send_user_text(user_text: str) -> str:
    global novel_client, chat_model

    current_chat_model = get_settings("chat_model") or chat_model
    chat_model = current_chat_model

    _lm_model(f"LLM route selected: {current_chat_model}")
    _lm_info(
        f"LLM request started: provider={current_chat_model}, "
        f"input_chars={len(user_text or '')}"
    )
    await _capture_vision_frame(current_chat_model, user_text)
    async def _timed(label: str, coro):
        t0 = time.perf_counter()
        try:
            result = await coro
            elapsed_ms = (time.perf_counter() - t0) * 1000
            _lm_perf(
                "LLM generation",
                elapsed_ms,
                detail=(
                    f"provider={label}, input_chars={len(user_text or '')}, "
                    f"output_chars={len(result or '')}"
                ),
            )
            return result
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            _lm_perf(
                "LLM failed generation duration",
                elapsed_ms,
                detail=f"provider={label}, input_chars={len(user_text or '')}",
            )
            _lm_error(f"Error in {label} generation: {exc}", exc_info=True)
            raise

    if current_chat_model == "OpenAI":
        Logger.quiet_print(user_text)
        try:
            return await _timed("OpenAI", open_response(user_text))
        except Exception as e:
            Logger.error(f"Error in OpenAI generation: {e}")
            return ""

    if current_chat_model == "NovelAI":
        Logger.quiet_print(user_text)
        api_token = get_auth("novelai", "token") or get_settings("NOVELAI_TOKEN")
        novel_model = get_settings("NOVELAI_MODEL") or get_settings("novel_model") or "kayra"
        if novel_client is None:
            novel_client = NovelAIClient(api_token=api_token)
            _lm_model("NovelAI client initialized")

        return await _timed(
            "NovelAI",
            novel_client.generate_full(
                custom_prompt=user_text,
                parameters={
                    "max_output_length": int(get_settings("max_output_length") or 150),
                    "min_length": 1,
                    "temperature": float(get_settings("novel_temperature") or get_settings("temperature") or 0.8),
                    "top_p": float(get_settings("novel_top_p") or 0.9),
                    "top_k": int(get_settings("novel_top_k") or 40),
                    "frequency_penalty": 0,
                    "presence_penalty": 0,
                    "repetition_penalty": float(get_settings("repetition_penalty") or 1.35),
                    "length_penalty": 1.0,
                    "stop": [],
                    "banlist_mode": get_settings("novel_banlist_mode") or "soft",
                },
                model_name=novel_model,
                character_name=_novelai_character_name(),
                route=get_settings("novel_generation_route") or "high",
            ),
        )
    if current_chat_model == "Local":
        Logger.quiet_print(user_text)
        return await _timed(
            "Local",
            response_local(
                text=user_text,
                max_tokens=int(get_settings("local_max_tokens") or 220),
                save_to_history=True,
                temperature=float(get_settings("local_temperature") or 0.65),
                top_k=int(get_settings("local_top_k") or 50),
                top_p=float(get_settings("local_top_p") or 1.0),
                min_p=float(get_settings("local_min_p") or 0.0),
                repeat_penalty=float(get_settings("local_repeat_penalty") or 1.3),
                force_new_context=bool(get_settings("local_force_new_context") or False),
            ),
        )

    if current_chat_model == "Grok":
        Logger.quiet_print(user_text)
        try:
            return await _timed("Grok", grok_response(user_text))
        except Exception as e:
            Logger.error(f"Error in Grok generation: {e}")
            return ""

    if current_chat_model == "Gemini":
        Logger.quiet_print(user_text)
        try:
            return await _timed("Gemini", gemini_response(user_text))
        except Exception as e:
            Logger.error(f"Error in Gemini generation: {e}")
            return ""

    if current_chat_model == "Claude":
        Logger.quiet_print(user_text)
        try:
            return await _timed("Claude", claude_response(user_text))
        except Exception as e:
            Logger.error(f"Error in Claude generation: {e}")
            return ""

    msg = f"LLM provider {current_chat_model!r} not recognized."
    Logger.warn(msg)
    _lm_error(msg)
    return msg
