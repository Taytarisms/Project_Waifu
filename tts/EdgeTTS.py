import wave
import uuid
import aiohttp
import pyaudio
import edge_tts
from pydub import AudioSegment
from files.system_setup.audio_tools import configure_audio_tools, tts_output_dir
from files.system_setup.system_logger import Logger
from files.system_setup.settings import get_settings
from files.main_loop import turn_control

configure_audio_tools()

OUTPUT_DIR = tts_output_dir("edge")
VOICE = "en-US-AvaNeural"
OUTPUT_FILE_WAV = OUTPUT_DIR / "edge_output.wav"


def request_interrupt() -> None:
    turn_control.request_interrupt()

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
        stream.write(data)
        data = wf.readframes(chunk)

    wf.close()
    stream.close()
    p.terminate()

async def read_chat(message: str, *, prepare_only=False, voice=None):
    message = str(message or "").strip()
    if not message:
        Logger.warn("EdgeTTS skipped because the response text was empty.")
        return False

    unique = uuid.uuid4().hex
    mp3_path = OUTPUT_DIR / f"edge_{unique}.mp3"
    wav_path = OUTPUT_DIR / f"edge_{unique}.wav"
    try:
        if turn_control.is_interrupted():
            return False
        # aiohttp otherwise prefers aiodns when it is installed. On some
        # Windows configurations pycares cannot reach the configured DNS
        # servers even though the system resolver works normally.
        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
        communicate = edge_tts.Communicate(
            message,
            voice or VOICE,
            connector=connector,
        )
        await communicate.save(str(mp3_path))
        if not mp3_path.exists() or mp3_path.stat().st_size < 256:
            raise RuntimeError("EdgeTTS returned no playable audio data.")
        if turn_control.is_interrupted():
            return False
        audio_content = AudioSegment.from_file(str(mp3_path), format="mp3")
        if prepare_only:
            return audio_content
        audio_content.export(
            str(wav_path),
            format="wav",
            parameters=["-ar", "44100", "-ac", "2"],
        )
        if not wav_path.exists() or wav_path.stat().st_size <= 44:
            raise RuntimeError("EdgeTTS WAV conversion produced no audio data.")
        wav_path.replace(OUTPUT_FILE_WAV)
        Logger.print(f"Created New File: {OUTPUT_FILE_WAV}")
        try:
            from files.closed_captions import caption_coordinator
            caption_coordinator.audio_ready(str(OUTPUT_FILE_WAV))
        except Exception as _e:
            Logger.warn(f"Caption sync error: {_e}")

        PlayAudio(str(OUTPUT_FILE_WAV))
        return True

    except Exception as e:
        Logger.error(f"EdgeTTS generation failed: {e}")
        return False
    finally:
        for temporary_path in (mp3_path, wav_path):
            try:
                temporary_path.unlink(missing_ok=True)
            except Exception:
                pass
