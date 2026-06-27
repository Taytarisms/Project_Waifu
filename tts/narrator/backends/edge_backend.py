import asyncio
import sys
import uuid
import wave
from pathlib import Path
from typing import Optional

import edge_tts
import pyaudio
from pydub import AudioSegment

from files.system_setup.audio_tools import tts_output_dir
from files.system_setup.system_logger import Logger

from .Base import NarratorBackend


EDGE_STOCK_VOICES = [
    "en-US-AvaNeural",
    "en-US-AriaNeural",
    "en-US-JennyNeural",
    "en-US-GuyNeural",
    "en-US-DavisNeural",
    "en-US-AndrewNeural",
    "en-US-EmmaNeural",
    "en-GB-SoniaNeural",
    "en-GB-RyanNeural",
    "en-AU-NatashaNeural",
]


class EdgeBackend(NarratorBackend):
    backend_id = "edge"
    display_name = "Edge TTS (free, online)"
    _OUTPUT_DIR = tts_output_dir("narrator") / "edge"

    def __init__(self):
        self._interrupt = False
        self._audio: Optional[pyaudio.PyAudio] = None
        self._OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def _get_audio(self) -> pyaudio.PyAudio:
        if self._audio is None:
            self._audio = pyaudio.PyAudio()
        return self._audio

    def is_available(self) -> bool:
        return True

    def interrupt(self) -> None:
        self._interrupt = True

    def list_voices(self) -> list[str]:
        return list(EDGE_STOCK_VOICES)
    
    async def generate_audio(
        self,
        text: str,
        *,
        voice: str,
        volume_db: float = 0.0,
    ) -> Optional[str]:
        unique = uuid.uuid4().hex
        mp3_path = self._OUTPUT_DIR / f"narrator_{unique}.mp3"
        wav_path = self._OUTPUT_DIR / f"narrator_{unique}.wav"

        try:
            communicate = edge_tts.Communicate(text, voice or "en-US-AvaNeural")
            await communicate.save(str(mp3_path))
        except Exception as e:
            Logger.warn(f"edge_tts.save failed: {e}")
            self._safe_unlink(mp3_path)
            return None
        try:
            audio_seg = AudioSegment.from_file(str(mp3_path), format="mp3")
            if volume_db:
                audio_seg = audio_seg + float(volume_db)
            audio_seg.export(
                str(wav_path),
                format="wav",
                parameters=["-ar", "44100", "-ac", "2"],
            )
        except Exception as e:
            Logger.warn(f"Wav conversion failed: {e}")
            self._safe_unlink(mp3_path)
            self._safe_unlink(wav_path)
            return None
        finally:
            self._safe_unlink(mp3_path)

        return str(wav_path)

    async def play_audio(
        self,
        audio_path: str,
        *,
        device_index: Optional[int] = None,
        volume_db: float = 0.0,
    ) -> bool:
        self._interrupt = False
        ok = await asyncio.to_thread(self._play_wav_blocking, audio_path, device_index)
        self._safe_unlink(Path(audio_path))
        return ok

    def _play_wav_blocking(self, wav_path: str, device_index: Optional[int]) -> bool:
        try:
            wf = wave.open(wav_path, "rb")
        except Exception as e:
            print(f"Cannot open wav: {e}", file=sys.stderr)
            return False

        try:
            stream = self._get_audio().open(
                format=self._get_audio().get_format_from_width(wf.getsampwidth()),
                channels=wf.getnchannels(),
                rate=wf.getframerate(),
                output=True,
                output_device_index=device_index,
            )
        except Exception as e:
            print(f"Cannot open output stream: {e}", file=sys.stderr)
            wf.close()
            return False

        try:
            chunk = 1024
            data = wf.readframes(chunk)
            while data:
                if self._interrupt:
                    self._interrupt = False
                    break
                stream.write(data)
                data = wf.readframes(chunk)
        finally:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
            wf.close()
        return True

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass