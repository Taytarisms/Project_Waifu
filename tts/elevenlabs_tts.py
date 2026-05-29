import io
import os
import time
import uuid
import wave

import pyaudio
import requests
from pydub import AudioSegment

from files.system_setup.audio_tools import configure_audio_tools, tts_output_dir
from files.system_setup.settings import get_auth, get_settings
from files.system_setup.system_logger import Logger
from files.main_loop import turn_control
configure_audio_tools()
OUTPUT_DIR = tts_output_dir("eleven")
voiceid = get_settings("eleven_voice_id")
_eleven_key = ""


def request_interrupt() -> None:
    turn_control.request_interrupt()


def _resolve_api_key() -> str:
    global _eleven_key
    if not _eleven_key:
        _eleven_key = get_auth("elevenlabs", "api_key") or ""
    return _eleven_key


def synthesize(text, voiceid=None):
    turn_control.raise_if_interrupted()
    api_key = _resolve_api_key()
    if not api_key:
        raise RuntimeError("Missing ELEVENLABS_API_KEY.")
    if not voiceid:
        voiceid = get_settings("eleven_voice_id")
    if not voiceid:
        raise RuntimeError(
            "Missing ElevenLabs voice ID. Set eleven_voice or save eleven_voice_id in settings."
        )

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voiceid}"
    headers = {
        "accept": "audio/mpeg",
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    data = {
        "text": text,
        "model_id": get_settings("eleven_model_id"),
        "voice_settings": {
            "stability": 0.75,
            "similarity": 0.75,
        },
    }
    print(f"Sending a POST request to: {url}")
    response = requests.post(url, headers=headers, json=data, stream=True)
    turn_control.raise_if_interrupted()
    print(response)
    if response.status_code != 200:
        raise RuntimeError(
            f"ElevenLabs request failed: {response.status_code} - {response.text[:500]}"
        )
    final_path = OUTPUT_DIR / f"eleven_audio_{uuid.uuid4()}.wav"

    audio_content = AudioSegment.from_file(io.BytesIO(response.content), format="mp3")
    audio_content.export(str(final_path), "wav")
    Logger.print(f"Saved New File: {final_path}")

    try:
        from files.closed_captions import caption_coordinator
        caption_coordinator.audio_ready(str(final_path))
    except Exception as _e:
        Logger.warn(f"Audio Error Raised: {_e}")

    try:
        turn_control.raise_if_interrupted()
        PlayAudio(str(final_path))
    finally:
        try:
            if final_path.exists():
                os.remove(final_path)
        except Exception as _e:
            Logger.warn(f"Cleanup of {final_path} failed: {_e}")


def PlayAudio(wav_file: str):
    raw_device = get_settings("tts_output_device")
    try:
        output_device = None if raw_device in ("", None) else int(raw_device)
    except Exception:
        output_device = None

    wf = wave.open(wav_file, "rb")
    p = pyaudio.PyAudio()
    chunk = 1024
    stream = p.open(
        format=p.get_format_from_width(wf.getsampwidth()),
        channels=wf.getnchannels(),
        rate=wf.getframerate(),
        output=True,
        output_device_index=output_device,
    )
    data = wf.readframes(chunk)
    while data:
        if turn_control.is_interrupted():
            break
        if turn_control.is_paused():
            try:
                stream.stop_stream()
                while turn_control.is_paused() and not turn_control.is_interrupted():
                    time.sleep(0.05)
                if not turn_control.is_interrupted():
                    stream.start_stream()
            except Exception:
                while turn_control.is_paused() and not turn_control.is_interrupted():
                    time.sleep(0.05)
            continue
        stream.write(data)
        data = wf.readframes(chunk)

    wf.close()
    stream.close()
    p.terminate()


def main():
    text = "This is a test of text-to-speech. A little fast, a little slow, but always on point."
    synthesize(text, voiceid="cgSgspJ2msm6clMCkdW9")

if __name__ == "__main__":
    main()
