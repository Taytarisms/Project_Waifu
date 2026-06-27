from pathlib import Path
from openai import OpenAI, AsyncOpenAI
import pyaudio, threading, os, tempfile, wave, sys
from pydub import AudioSegment
from pydub.utils import make_chunks
from files.system_setup.settings import get_auth, get_settings
from files.system_setup.audio_tools import tts_output_dir

_OUTPUT_DIR = tts_output_dir("openai")
_SPEECH_FILE = _OUTPUT_DIR / "speech.mp3"

_client = None
_async_client = None
_client_token = None
_async_client_token = None


def get_openai_tts_client():
    global _client, _client_token

    token = get_auth("openai", "token")
    if not token:
        raise RuntimeError("OpenAI API key is missing. Add it in the OpenAI provider settings first.")
    if _client is None or token != _client_token:
        _client = OpenAI(api_key=token)
        _client_token = token
    return _client


def get_async_openai_tts_client(api_key: str | None = None):
    global _async_client, _async_client_token
    token = api_key or get_auth("openai", "token")
    if not token:
        raise RuntimeError("OpenAI API key is missing. Add it in the OpenAI provider settings first.")
    if _async_client is None or token != _async_client_token:
        _async_client = AsyncOpenAI(api_key=token)
        _async_client_token = token
    return _async_client

def interrupt_tts_playback(value: bool):
    global interrupt_tts_flag
    interrupt_tts_flag = value
    print("Playback interrupted.")

interrupt_tts_flag = False

def openai_tts(text, model=str, voice=str):
    client = get_openai_tts_client()
    speech_file_path = _SPEECH_FILE
    with client.audio.speech.with_streaming_response.create(
        model=model,
        voice=voice,
        input=text,
    ) as response:
        response.stream_to_file(speech_file_path)
        print("File Saved!")
    try:
        from files.closed_captions import caption_coordinator
        caption_coordinator.audio_ready(str(speech_file_path))
    except Exception as _e:
        print(f"Caption error: {_e}")

    try:
        audio_segment = AudioSegment.from_mp3(speech_file_path)
    except Exception as e:
        print("Error loading audio file:", e)
        return None
    playback_thread = threading.Thread(
        target=play_audio_segment,
        args=(audio_segment,),
        daemon=True,
    )
    playback_thread.start()
    try:
        os.remove(speech_file_path)
    except Exception as e:
        print("Error deleting audio file:", e)
    return playback_thread

def play_audio_segment(audio_segment):
    global interrupt_tts_flag
    raw_device = get_settings("tts_output_device")
    try:
        output_device = None if raw_device in ("", None) else int(raw_device)
    except Exception:
        output_device = None
    p = pyaudio.PyAudio()
    stream = p.open(
        format=p.get_format_from_width(audio_segment.sample_width),
        channels=audio_segment.channels,
        rate=audio_segment.frame_rate,
        output=True,
        output_device_index=output_device
    )
    try:
        # Split audio into 500ms chunks.
        chunks = make_chunks(audio_segment, 500)
        for chunk in chunks:
            if interrupt_tts_flag:
                print("Playback interrupted mid-chunk.")
                break
            stream.write(chunk._data)
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

OUTPUT_DEVICE_INDEX = None
CHUNK_SIZE = 4096
pause_event = threading.Event()
pause_event.set()
interrupt_tts_flag = False

def interrupt_tts_playback(value: bool):
    global interrupt_tts_flag
    interrupt_tts_flag = value
    print("Playback interrupted.")

async def openai_stream_and_decode(text, api_key: str, model=str, voice=str) -> str:
    openai_client = get_async_openai_tts_client(api_key)
    audio_bytes = bytearray()

    async with openai_client.audio.speech.with_streaming_response.create(
        model=model,   # Use a valid model.
        voice=voice,            # Use a valid voice.
        input=text,
        response_format="wav",    # Request WAV output.
    ) as response:
        async for chunk in response.iter_bytes(chunk_size=CHUNK_SIZE):
            audio_bytes.extend(chunk)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        tmp.write(audio_bytes)
        temp_filename = tmp.name
    print("Temporary WAV file saved as:", temp_filename)
    return temp_filename

def openai_playback_worker(temp_filename):
    global interrupt_tts_flag
    raw_device = get_settings("tts_output_device")
    try:
        output_device = None if raw_device in ("", None) else int(raw_device)
    except Exception:
        output_device = OUTPUT_DEVICE_INDEX
    p = pyaudio.PyAudio()
    try:
        wf = wave.open(temp_filename, 'rb')
    except Exception as e:
        print("Error opening WAV file:", e, file=sys.stderr)
        os.remove(temp_filename)
        return

    stream = p.open(
        format=p.get_format_from_width(wf.getsampwidth()),
        channels=wf.getnchannels(),
        rate=wf.getframerate(),
        output=True,
        output_device_index=output_device
    )
    data = wf.readframes(CHUNK_SIZE)
    while data:
        stream.write(data)
        data = wf.readframes(CHUNK_SIZE)
    stream.stop_stream()
    stream.close()
    p.terminate()
    wf.close()
    os.remove(temp_filename)

async def openai_stream_tts_main(text: str, api_key: str, model:str, voice: str):
    temp_filename = await openai_stream_and_decode(text, api_key, model, voice)
    openai_playback_worker(temp_filename)