import time
import re

from files.tts.pcm_stream import PCMDecoder, prefetched


def split_requests(text, limit=4096):
    if limit < 1:
        raise ValueError("Request limit must be positive")
    parts = []
    while len(text) > limit:
        window = text[:limit]
        boundaries = list(re.finditer(r"[.!?]\s+", window))
        cut = boundaries[-1].end() if boundaries else 0
        if cut < limit // 2:
            spaces = list(re.finditer(r"\s+", window))
            cut = spaces[-1].end() if spaces else limit
        parts.append(text[:cut])
        text = text[cut:]
    if text:
        parts.append(text)
    return parts


def speak_streaming(text, *, turn_started=None, turn_id=None):
    import pyaudio
    from files.tts.openai_tts import build_openai_tts_request, get_openai_tts_client
    from files.system_setup.settings import get_settings
    from files.system_setup.system_logger import Logger
    from files.main_loop import turn_control
    from files.closed_captions import caption_coordinator

    started = time.perf_counter()
    turn_control.raise_if_interrupted()
    model = get_settings("openai_tts_model") or "gpt-4o-mini-tts"
    voice = get_settings("openai_tts_voice") or "nova"
    parts = split_requests(text)
    if not parts:
        return None
    # Freeze voice controls for all parts; never retry a partially spoken reply.
    payload = build_openai_tts_request(parts[0], model, voice)
    payload["response_format"] = "pcm"
    client = get_openai_tts_client().with_options(max_retries=0, timeout=120)
    raw_device = get_settings("tts_output_device")
    device = None if raw_device in (None, "", "System Default") else int(raw_device)
    rate = 24000
    audio = stream = None
    received = written = None
    total = 0
    completed = False

    def caption(action, *args):
        try:
            getattr(caption_coordinator, action)(*args)
        except Exception as exc:
            Logger.warn(f"OpenAI caption update failed: {exc}")

    def check():
        turn_control.raise_if_interrupted()
        while turn_control.is_paused():
            turn_control.raise_if_interrupted()
            time.sleep(0.025)

    Logger.detail(f"OpenAI HTTP streaming: model={payload['model']}, pcm_24000")
    try:
        for part in parts:
            check()
            decoder = PCMDecoder(rate)
            before = total
            request = dict(payload, input=part)
            with client.audio.speech.with_streaming_response.create(**request) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                if "json" in content_type or "text/" in content_type or "mpeg" in content_type:
                    raise RuntimeError(f"OpenAI returned unexpected audio format: {content_type}")

                def blocks():
                    nonlocal received
                    for chunk in response.iter_bytes(chunk_size=1024):
                        if not chunk:
                            continue
                        if received is None:
                            received = time.perf_counter()
                            Logger.turn_metric(f"OpenAI first audio received: {received - started:.2f}s since speech request", turn_id)
                        # HTTP boundaries are arbitrary, not separate WAV files.
                        pcm = decoder.feed(chunk)
                        for offset in range(0, len(pcm), 4096):
                            yield pcm[offset:offset + 4096]
                    tail = decoder.finish()
                    if tail:
                        yield tail

                incoming = prefetched(blocks(), check, response.close)
                try:
                    for pcm in incoming:
                        for offset in range(0, len(pcm), 1200):
                            check()
                            if stream is None:
                                audio = pyaudio.PyAudio()
                                stream = audio.open(format=pyaudio.paInt16, channels=1, rate=rate,
                                                    output=True, output_device_index=device)
                                caption("audio_ready_with_duration", max(600, int(len(text) / 15 * 1000)))
                            block = pcm[offset:offset + 1200]
                            stream.write(block)
                            total += len(block)
                            if written is None:
                                written = time.perf_counter()
                                Logger.turn_metric(f"OpenAI first audio write: {written - started:.2f}s since speech request", turn_id)
                                if turn_started is not None:
                                    Logger.turn_metric(f"Turn to first assistant audio write: {written - turn_started:.2f}s", turn_id)
                finally:
                    incoming.close()
            if total == before:
                raise RuntimeError("OpenAI returned no playable audio for a text segment")
        if not total:
            raise RuntimeError("OpenAI returned no playable audio")
        completed = True
        return True
    finally:
        try:
            if stream is not None:
                try:
                    stream.stop_stream()
                finally:
                    stream.close()
        finally:
            try:
                if audio is not None:
                    audio.terminate()
            finally:
                caption("finish_pending" if completed else "cancel_pending")
