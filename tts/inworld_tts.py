import asyncio
import base64
import json
import time
from io import BytesIO
from typing import Any, Optional

import pyaudio
import requests
from pydub import AudioSegment

from files.system_setup.audio_tools import configure_audio_tools, tts_output_dir
from files.provider_capabilities import inworld_uses_delivery_mode
from files.system_setup.settings import get_auth, get_settings
from files.system_setup.system_logger import Logger
from files.main_loop import turn_control
configure_audio_tools()
OUTPUT_DIR = tts_output_dir("inworld")
DEFAULT_MODEL_ID = "inworld-tts-2"
DEFAULT_VOICE_ID = "Ashley"
DEFAULT_AUDIO_ENCODING = "MP3"
DEFAULT_SAMPLE_RATE = 24000
DEFAULT_BIT_RATE = 32000
DEFAULT_DELIVERY_MODE = "BALANCED"
_AUDIO = pyaudio.PyAudio()


def request_interrupt() -> None:
    turn_control.request_interrupt()

def _setting(name: str, default: Any = "") -> Any:
    try:
        value = get_settings(name)
    except Exception:
        value = default
    return default if value in ("", None) else value


def _as_bool(value: Any, default: bool = False) -> bool:
    if value in ("", None):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y", "on", "enabled"}


def _as_int(value: Any, default: int) -> int:
    try:
        if value in ("", None):
            return default
        return int(value)
    except Exception:
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        if value in ("", None):
            return default
        return float(value)
    except Exception:
        return default


def _output_device() -> Optional[int]:
    raw = _setting("tts_output_device", None)
    try:
        return None if raw in ("", None, "System Default") else int(raw)
    except Exception:
        return None


def check_api_key() -> Optional[str]:
    api_key = get_auth("inworld", "token")
    if not api_key:
        Logger.warn("InWorld API key is not set. Add it from the InWorld TTS provider page.")
        return None
    return api_key


def _active_voice_id(fallback: Optional[str] = None) -> str:
    return str(
        _setting("inworld_tts_voice_id", "")
        or _setting("inworld_tts_voice", "")
        or fallback
        or DEFAULT_VOICE_ID
    ).strip()


def _active_model_id(fallback: Optional[str] = None) -> str:
    return str(
        _setting("inworld_tts_model_id", "")
        or _setting("inworld_tts_model", "")
        or fallback
        or DEFAULT_MODEL_ID
    ).strip()


_VOICE_CACHE: dict[str, dict[str, str]] = {}


def _fetch_voice_catalog(api_key: str) -> dict[str, str]:
    cached = _VOICE_CACHE.get(api_key)
    if cached is not None:
        return cached
    catalog: dict[str, str] = {}
    try:
        resp = requests.get(
            "https://api.inworld.ai/tts/v1/voices",
            headers={"Authorization": f"Basic {api_key}"},
            timeout=15,
        )
        if resp.ok:
            for entry in resp.json().get("voices", []):
                vid = str(entry.get("voiceId") or "").strip()
                name = str(entry.get("displayName") or "").strip()
                if vid:
                    catalog[vid.lower()] = vid
                    if name:
                        catalog.setdefault(name.lower(), vid)
        else:
            Logger.warn(f"InWorld voice list HTTP {resp.status_code}: {resp.text or '<empty body>'}")
    except Exception as exc:
        Logger.warn(f"InWorld voice list failed: {exc}")
    _VOICE_CACHE[api_key] = catalog
    return catalog


def _resolve_voice_id(api_key: str, requested: str) -> str:
    catalog = _fetch_voice_catalog(api_key)
    if not catalog:
        return requested
    resolved = catalog.get(requested.lower())
    if resolved is None:
        Logger.warn(f"InWorld voice {requested!r} not found in this workspace's voice list.")
        return requested
    if resolved != requested:
        Logger.notify(f"InWorld voice {requested!r} resolved to voiceId {resolved!r}.")
    return resolved


def _build_payload(text: str, voice_id: Optional[str] = None, model_id: Optional[str] = None, api_key: Optional[str] = None) -> dict[str, Any]:
    voice = _active_voice_id(voice_id)
    if api_key:
        voice = _resolve_voice_id(api_key, voice)
    model = _active_model_id(model_id)

    audio_encoding = str(_setting("inworld_tts_audio_encoding", DEFAULT_AUDIO_ENCODING)).strip() or DEFAULT_AUDIO_ENCODING
    sample_rate = _as_int(_setting("inworld_tts_sample_rate", DEFAULT_SAMPLE_RATE), DEFAULT_SAMPLE_RATE)
    language = str(_setting("inworld_tts_language", "") or "").strip()
    delivery_mode = str(_setting("inworld_tts_delivery_mode", DEFAULT_DELIVERY_MODE) or "").strip().upper()
    temperature = _as_float(_setting("inworld_tts_temperature", 1.0), 1.0)
    normalization_setting = _setting("inworld_tts_apply_text_normalization", True)
    if isinstance(normalization_setting, str) and normalization_setting.upper() in {
        "ON", "OFF", "APPLY_TEXT_NORMALIZATION_UNSPECIFIED"
    }:
        apply_normalization = normalization_setting.upper()
    else:
        apply_normalization = "ON" if _as_bool(normalization_setting, True) else "OFF"
    timestamp_type = str(_setting("inworld_tts_timestamp_type", "") or "").strip().upper()

    payload: dict[str, Any] = {
        "text": text,
        "voiceId": voice,
        "modelId": model,
        "audioConfig": {
            "audioEncoding": audio_encoding,
        },
        "applyTextNormalization": apply_normalization,
    }

    if sample_rate > 0:
        payload["audioConfig"]["sampleRateHertz"] = sample_rate
    if language:
        payload["audioConfig"]["language"] = language
    if inworld_uses_delivery_mode(model):
        if delivery_mode in {"STABLE", "BALANCED", "CREATIVE"}:
            payload["deliveryMode"] = delivery_mode
    elif temperature > 0:
        payload["temperature"] = temperature

    if timestamp_type in {"WORD", "CHARACTER"}:
        payload["timestampType"] = timestamp_type

    return payload


def _extract_audio_from_event(event: dict[str, Any]) -> bytes:
    candidates = [
        event.get("audioContent"),
        event.get("audio_content"),
        event.get("result", {}).get("audioContent") if isinstance(event.get("result"), dict) else None,
        event.get("result", {}).get("audio_content") if isinstance(event.get("result"), dict) else None,
    ]

    for item in candidates:
        if not item:
            continue
        if isinstance(item, bytes):
            return item
        if isinstance(item, str):
            try:
                return base64.b64decode(item)
            except Exception:
                continue

    return b""


def _audio_segment_from_bytes(audio_bytes: bytes, payload: dict[str, Any]) -> AudioSegment:
    audio_config = payload.get("audioConfig", {}) if isinstance(payload, dict) else {}
    encoding = str(audio_config.get("audioEncoding", DEFAULT_AUDIO_ENCODING)).strip().upper()
    sample_rate = _as_int(audio_config.get("sampleRateHertz", DEFAULT_SAMPLE_RATE), DEFAULT_SAMPLE_RATE)

    if encoding in {"PCM", "LINEAR16", "LINEAR_16"}:
        return AudioSegment(
            data=audio_bytes,
            sample_width=2,
            frame_rate=sample_rate,
            channels=1,
        )

    fmt = "mp3" if encoding in {"MP3", "MPEG"} else None
    return AudioSegment.from_file(BytesIO(audio_bytes), format=fmt)


def _play_audio_segment(audio_segment: AudioSegment) -> None:
    output_device = _output_device()
    stream = None
    chunk_size = max(1, audio_segment.frame_width * 2048)

    try:
        stream = _AUDIO.open(
            format=_AUDIO.get_format_from_width(audio_segment.sample_width),
            channels=audio_segment.channels,
            rate=audio_segment.frame_rate,
            output=True,
            output_device_index=output_device,
        )
        raw = audio_segment.raw_data
        for offset in range(0, len(raw), chunk_size):
            if turn_control.is_interrupted():
                break
            stream.write(raw[offset:offset + chunk_size])
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass


def _notify_captions(audio_segment: AudioSegment) -> None:
    try:
        wav_path = OUTPUT_DIR / "inworld_last_caption.wav"
        audio_segment.export(str(wav_path), format="wav")
        from files.closed_captions import caption_coordinator
        caption_coordinator.audio_ready(str(wav_path))
    except Exception as exc:
        Logger.warn(f"InWorld caption timing failed: {exc}")
        try:
            from files.closed_captions import caption_coordinator
            caption_coordinator.audio_ready_with_duration(max(600, int(len(audio_segment))))
        except Exception:
            pass


def _stream_tts_sync(api_key, text, voice_id, model_id, turn_started=None, turn_id=None):
    from files.tts.pcm_stream import PCMDecoder, prefetched
    from files.closed_captions import caption_coordinator
    started = time.perf_counter()
    turn_control.raise_if_interrupted()
    api_key = api_key.strip()
    if api_key.lower().startswith("basic "):
        api_key = api_key[6:].strip()
    # Use saved IDs directly, or an already cached display-name mapping. No
    # catalog lookup or paid warmup belongs on the time-to-speech path.
    payload = _build_payload(text, voice_id, model_id)
    payload["voiceId"] = _VOICE_CACHE.get(api_key, {}).get(
        payload["voiceId"].lower(), payload["voiceId"])
    payload["audioConfig"]["audioEncoding"] = "LINEAR16"
    rate = payload["audioConfig"].get("sampleRateHertz", DEFAULT_SAMPLE_RATE)
    decoder = PCMDecoder(rate)
    stream = None
    received_at = None
    written_at = None
    total = 0
    events = 0
    completed = False

    def caption(action, *args):
        try:
            getattr(caption_coordinator, action)(*args)
        except Exception as exc:
            Logger.warn(f"InWorld caption update failed: {exc}")

    def check():
        turn_control.raise_if_interrupted()
        while turn_control.is_paused():
            turn_control.raise_if_interrupted()
            time.sleep(0.025)

    def write(pcm):
        nonlocal stream, written_at, total
        for offset in range(0, len(pcm), max(2, int(rate * 0.025) * 2)):
            check()
            if stream is None:
                stream = _AUDIO.open(format=pyaudio.paInt16, channels=1, rate=rate,
                                     output=True, output_device_index=_output_device())
                # The final duration is unknown while streaming. Existing typed
                # captions use an estimate, completed when playback ends.
                caption("audio_ready_with_duration", max(600, int(len(text) / 15 * 1000)))
            block = pcm[offset:offset + max(2, int(rate * 0.025) * 2)]
            stream.write(block)
            total += len(block)
            if written_at is None:
                written_at = time.perf_counter()
                Logger.turn_metric(f"InWorld first audio write: {written_at - started:.2f}s since speech request", turn_id)
                if turn_started is not None:
                    Logger.turn_metric(f"Turn to first assistant audio write: {written_at - turn_started:.2f}s", turn_id)

    try:
        with requests.Session() as session:
            with session.post("https://api.inworld.ai/tts/v1/voice:stream",
                              headers={"Authorization": f"Basic {api_key}", "Content-Type": "application/json"},
                              json=payload, stream=True, timeout=(10, 30)) as response:
                response.raise_for_status()
                response.encoding = "utf-8"
                def blocks():
                    nonlocal received_at, events
                    # Consume available HTTP chunks without an extra fixed-size
                    for raw in response.iter_lines(chunk_size=None, decode_unicode=True):
                        if not raw:
                            continue
                        line = raw.strip()
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if line == "[DONE]":
                            break
                        event = json.loads(line)
                        if event.get("error"):
                            raise RuntimeError(f"InWorld stream error: {event['error']}")
                        audio = _extract_audio_from_event(event)
                        if not audio:
                            continue
                        events += 1
                        if received_at is None:
                            received_at = time.perf_counter()
                            Logger.turn_metric(f"InWorld first audio received: {received_at - started:.2f}s since speech request", turn_id)
                        pcm = decoder.feed_event(audio)
                        for offset in range(0, len(pcm), 4096):
                            yield pcm[offset:offset + 4096]
                    tail = decoder.finish()
                    if tail:
                        yield tail
                incoming = prefetched(blocks(), check, response.close)
                try:
                    for block in incoming:
                        write(block)
                finally:
                    incoming.close()
        if not total:
            raise RuntimeError("InWorld returned no playable audio")
        Logger.detail(f"InWorld stream: {events} audio events, {decoder.headers_removed} WAV headers removed")
        completed = True
        return {"ttfb": received_at - started, "total_time": time.perf_counter() - started,
                "audio_bytes": total, "events": events, "duration_ms": total / (rate * 2) * 1000,
                "voice_id": payload["voiceId"], "model_id": payload["modelId"]}
    finally:
        try:
            if stream is not None:
                try:
                    stream.stop_stream()
                finally:
                    stream.close()
        finally:
            if completed:
                caption("finish_pending")
            else:
                caption("cancel_pending")


async def stream_tts(api_key, text, voice_id=None, model_id=None, *, turn_started=None, turn_id=None):
    api_key = api_key or check_api_key()
    if not api_key:
        return None
    return await asyncio.to_thread(_stream_tts_sync, api_key, text, voice_id, model_id, turn_started, turn_id)


def main():
    print("InWorld TTS Low-Latency HTTP Streaming")
    print("=" * 45)

    api_key = check_api_key()
    if not api_key:
        return 1

    text = "Life moves pretty fast. Look around once in a while, or you might miss it."
    voice_id = _active_voice_id()
    model_id = _active_model_id()

    print(f"   Text: {text!r}")
    print(f"  Voice: {voice_id}")
    print(f"  Model: {model_id}")
    print("\nWarming up connection, then generating audio...\n")

    try:
        result = asyncio.run(stream_tts(api_key, text, voice_id, model_id))

        if result:
            ttfb = result.get("ttfb") or 0
            print(f"TTFB:         {ttfb * 1000:.1f} ms")
            print(f"Total time:   {result['total_time'] * 1000:.1f} ms")
            print(f"Audio bytes:  {result['audio_bytes']}")
            print(f"Duration:     {result['duration_ms']} ms")
        else:
            print("Synthesis failed.")
            return 1

    except Exception as e:
        print(f"\nLatency test failed: {e}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
