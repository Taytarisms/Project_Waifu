import time
from urllib.parse import quote

from files.tts.pcm_stream import PCMDecoder, prefetched


def speak_streaming(text, *, turn_started=None, turn_id=None):
    import pyaudio
    import requests
    from files.tts.elevenlabs_tts import _build_payload
    from files.system_setup.settings import get_auth, get_settings
    from files.system_setup.system_logger import Logger
    from files.main_loop import turn_control
    from files.closed_captions import caption_coordinator

    started = time.perf_counter()
    turn_control.raise_if_interrupted()
    token = get_auth("elevenlabs", "api_key")
    voice = get_settings("eleven_voice_id") or get_settings("eleven_voice")
    if not token or not voice:
        raise RuntimeError("ElevenLabs requires an API key and voice ID.")
    payload = _build_payload(text)
    raw_device = get_settings("tts_output_device")
    device = None if raw_device in (None, "", "System Default") else int(raw_device)
    rate = 24000
    decoder = PCMDecoder(rate)
    audio = stream = None
    received = written = None
    total = 0
    completed = False

    def caption(action, *args):
        try:
            getattr(caption_coordinator, action)(*args)
        except Exception as exc:
            Logger.warn(f"ElevenLabs caption update failed: {exc}")

    def check():
        turn_control.raise_if_interrupted()
        while turn_control.is_paused():
            turn_control.raise_if_interrupted()
            time.sleep(0.025)

    Logger.detail(f"ElevenLabs HTTP streaming: model={payload['model_id']}, pcm_24000")
    try:
        with requests.Session() as session:
            with session.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{quote(str(voice), safe='')}/stream",
                params={"output_format": "pcm_24000"},
                headers={"xi-api-key": token, "Content-Type": "application/json", "Accept": "application/octet-stream"},
                json=payload, stream=True, timeout=(10, 120),
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").lower()
                if "json" in content_type or "text/" in content_type or "mpeg" in content_type:
                    raise RuntimeError(f"ElevenLabs returned unexpected audio format: {content_type}")

                def blocks():
                    nonlocal received
                    for chunk in response.iter_content(chunk_size=None):
                        if not chunk:
                            continue
                        if received is None:
                            received = time.perf_counter()
                            Logger.turn_metric(f"ElevenLabs first audio received: {received - started:.2f}s since speech request", turn_id)
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
                                Logger.turn_metric(f"ElevenLabs first audio write: {written - started:.2f}s since speech request", turn_id)
                                if turn_started is not None:
                                    Logger.turn_metric(f"Turn to first assistant audio write: {written - turn_started:.2f}s", turn_id)
                finally:
                    incoming.close()
        if not total:
            raise RuntimeError("ElevenLabs returned no playable audio")
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
