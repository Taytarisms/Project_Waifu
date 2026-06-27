import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Optional
import asyncio
import pyaudio
from files.system_setup.settings import get_auth
from files.system_setup.system_logger import Logger
from files.tts.novel_ai_tts import NovelTTSClient
from .Base import NarratorBackend

def _find_ffmpeg() -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    cwd = os.environ.get("CWD", os.getcwd())
    local_path = os.path.abspath(os.path.join(cwd, "ffmpeg", "ffmpeg.exe"))
    if os.path.isfile(local_path):
        return local_path
    return ""


FFMPEG_PATH = _find_ffmpeg()
NOVELAI_STOCK_VOICES = [
    "Ligeia", "Aini", "Orea", "Claea", "Lim", "Orev", "Hae", "Cyllene",
    "Leucosia", "Crina", "Marisa", "Alseid", "Daphnis",
]


class NovelAIBackend(NarratorBackend):
    backend_id = "novelai"
    display_name = "NovelAI"

    def __init__(self):
        self._client: Optional[NovelTTSClient] = None
        self._last_token: Optional[str] = None
        self._audio: Optional[pyaudio.PyAudio] = None
        self._interrupt = False

    def _get_client(self) -> Optional[NovelTTSClient]:
        token = self._get_token()
        if not token:
            return None
        if self._client is None or token != self._last_token:
            self._client = NovelTTSClient(api_token=token)
            self._last_token = token
        return self._client

    @staticmethod
    def _get_token() -> Optional[str]:
        try:
            return get_auth("novelai", "token") or None
        except Exception:
            return None

    def _get_audio(self) -> pyaudio.PyAudio:
        if self._audio is None:
            self._audio = pyaudio.PyAudio()
        return self._audio

    def is_available(self) -> bool:
        return bool(self._get_token())

    def interrupt(self) -> None:
        self._interrupt = True
        try:
            from files.tts.novel_ai_tts import interrupt as _novel_interrupt
            _novel_interrupt()
        except Exception:
            pass

    def list_voices(self) -> list[str]:
        return list(NOVELAI_STOCK_VOICES)

    async def generate_audio(
        self,
        text: str,
        *,
        voice: str,
        volume_db: float = 0.0,
    ) -> Optional[str]:
        client = self._get_client()
        if client is None:
            Logger.warn("No API token configured.")
            return None

        if not FFMPEG_PATH:
            Logger.warn(
                "ffmpeg not found — install it or place "
                "ffmpeg.exe in ./ffmpeg/. Cannot generate audio."
            )
            return None
        try:
            file_id = await client.generate_tts(speak=text, voice_seed=voice)
        except Exception as e:
            if len(getattr(e, "args", ())) >= 2 and e.args[1] == 401:
                Logger.warn("Bad or missing API token.")
            else:
                Logger.warn(f"Generation failed: {e}")
            return None

        mp3_path = client.audio_path_for(file_id, "mp3")
        wav_path = client.audio_path_for(file_id, "wav")
        def _convert() -> bool:
            try:
                subprocess.run(
                    [
                        FFMPEG_PATH,
                        "-loglevel", "quiet",
                        "-y",
                        "-i", mp3_path,
                        "-filter:a", f"volume={int(volume_db)}dB",
                        wav_path,
                    ],
                    check=True,
                )
                return True
            except Exception as e:
                Logger.warn(f"ffmpeg conversion failed: {e}")
                return False

        ok = await asyncio.to_thread(_convert)
        if not ok:
            self._safe_unlink(mp3_path)
            self._safe_unlink(wav_path)
            return None
        self._safe_unlink(mp3_path)
        return wav_path

    async def play_audio(
        self,
        audio_path: str,
        *,
        device_index: Optional[int] = None,
        volume_db: float = 0.0,
    ) -> bool:
        self._interrupt = False
        ok = await asyncio.to_thread(self._play_wav_blocking, audio_path, device_index)
        self._safe_unlink(audio_path)
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
            chunk = 8192
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
    def _safe_unlink(path: str) -> None:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass