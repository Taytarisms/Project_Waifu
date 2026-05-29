import wave
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
OUTPUT_FILE_MP3 = OUTPUT_DIR / "edge_output.mp3"
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

async def read_chat(message: str):
    try:
        if turn_control.is_interrupted():
            return
        communicate = edge_tts.Communicate(message, VOICE) # Communicates with Edge Servers - creating a faux "browser request" -> tts output | DOES require an internet connection!
        await communicate.save(str(OUTPUT_FILE_MP3))
        if turn_control.is_interrupted():
            return
        audio_content = AudioSegment.from_file(str(OUTPUT_FILE_MP3), format="mp3")
        audio_content.export(
            str(OUTPUT_FILE_WAV),
            format="wav",
            parameters=["-ar", "44100", "-ac", "2"],
        )
        Logger.print(f"Created New File: {OUTPUT_FILE_WAV}")
        try:
            from files.closed_captions import caption_coordinator
            caption_coordinator.audio_ready(str(OUTPUT_FILE_WAV))
        except Exception as _e:
            Logger.warn(f"Caption sync error: {_e}")

        PlayAudio(str(OUTPUT_FILE_WAV))

    except Exception as e:
        print(f"Error in generation: {e}")
