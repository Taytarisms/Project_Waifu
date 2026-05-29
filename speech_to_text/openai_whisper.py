import io
import time
import wave
import queue
import threading
from dataclasses import dataclass
from typing import Callable, Optional

import sounddevice as sd
from openai import OpenAI

from files.system_setup.settings import get_auth


@dataclass
class OpenAIWhisperConfig:
    model: str = "gpt-4o-transcribe"
    language: str = "en"
    sample_rate: int = 16000
    channels: int = 1
    chunk_seconds: float = 5.0


class OpenAIWhisperCore:
    def __init__(self, input_device_index: Optional[int] = None, cfg: Optional[OpenAIWhisperConfig] = None):
        self.input_device_index = input_device_index
        self.cfg = cfg or OpenAIWhisperConfig()
        self.client = OpenAI(api_key=get_auth("openai", "token"))

        self.running = False
        self.paused = False
        self.audio_q = queue.Queue()
        self.stream = None
        self.worker_thread = None
        self.on_text = None
        self._buffer = bytearray()
        self._buffer_lock = threading.Lock()

    def start(self, on_text: Callable[[str, bool], None]):
        if self.running:
            return

        self.on_text = on_text
        self.running = True
        self.paused = False

        self.stream = sd.InputStream(
            samplerate=self.cfg.sample_rate,
            channels=self.cfg.channels,
            dtype="int16",
            device=self.input_device_index,
            callback=self._audio_callback,
        )
        self.stream.start()

        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()

    def stop(self):
        self.running = False

        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def force_flush_and_pause(self):
        self.paused = True
        flush_bytes = b""
        with self._buffer_lock:
            if self._buffer:
                flush_bytes = bytes(self._buffer)
                self._buffer.clear()

        while not self.audio_q.empty():
            try:
                flush_bytes += self.audio_q.get_nowait()
            except queue.Empty:
                break
        if flush_bytes:
            threading.Thread(
                target=lambda: self._transcribe(flush_bytes),
                daemon=True,
            ).start()

    def _audio_callback(self, indata, frames, time_info, status):
        if not self.running or self.paused:
            return

        self.audio_q.put(bytes(indata))

    def _worker(self):
        bytes_per_second = self.cfg.sample_rate * self.cfg.channels * 2
        target_size = int(bytes_per_second * self.cfg.chunk_seconds)

        while self.running:
            if self.paused:
                time.sleep(0.05)
                continue
            try:
                chunk = self.audio_q.get(timeout=0.25)
                with self._buffer_lock:
                    self._buffer.extend(chunk)

                    if len(self._buffer) >= target_size:
                        audio_bytes = bytes(self._buffer)
                        self._buffer.clear()
                    else:
                        audio_bytes = b""

                if audio_bytes:
                    self._transcribe(audio_bytes)

            except queue.Empty:
                time.sleep(0.05)

    def _transcribe(self, pcm_bytes: bytes):
        wav_io = io.BytesIO()

        with wave.open(wav_io, "wb") as wf:
            wf.setnchannels(self.cfg.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.cfg.sample_rate)
            wf.writeframes(pcm_bytes)

        wav_io.seek(0)
        wav_io.name = "speech.wav"

        result = self.client.audio.transcriptions.create(
            model=self.cfg.model,
            file=wav_io,
            language=self.cfg.language or None,
        )

        text = getattr(result, "text", "").strip()

        if text and self.on_text:
            self.on_text(text, False)
