from dataclasses import dataclass
import io
import queue
import threading
from typing import Callable, Optional
import wave

import numpy as np
from openai import OpenAI

from files.speech_to_text.stt_config import STTConfig
from files.speech_to_text.utterance_capture import SAMPLE_RATE, UtteranceCapture
from files.system_setup.settings import get_auth
from files.system_setup.system_logger import Logger

@dataclass
class OpenAIWhisperConfig:
    model: str = "gpt-4o-transcribe"
    language: str = "en"
    chunk_seconds: float = 5.0

class OpenAIWhisperCore:
    def __init__(
        self,
        input_device_index: Optional[int] = None,
        cfg: Optional[OpenAIWhisperConfig] = None,
        capture_cfg: Optional[STTConfig] = None,
    ) -> None:
        self.input_device_index = input_device_index
        self.cfg = cfg or OpenAIWhisperConfig()
        self.capture_cfg = capture_cfg or STTConfig(enable_partials=False)
        self._api_key = str(get_auth("openai", "token") or "").strip()
        self.client: Optional[OpenAI] = None
        self._capture: Optional[UtteranceCapture] = None
        self._jobs: queue.Queue[Optional[np.ndarray]] = queue.Queue(maxsize=4)
        self._worker_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._on_text: Optional[Callable[[str, bool], None]] = None
        self._on_status: Optional[Callable[[str, str], None]] = None
        self._session = 0

    def _status(self, phase: str, detail: str = "") -> None:
        if self._on_status:
            try:
                self._on_status(phase, detail)
            except Exception:
                pass

    def start(
        self,
        on_text: Callable[[str, bool], None],
        on_status: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            return
        if not self._api_key:
            raise RuntimeError("OpenAI STT requires an OpenAI API key.")
        self.client = OpenAI(api_key=self._api_key)
        self._on_text = on_text
        self._on_status = on_status
        self._stop.clear()
        self._paused.clear()
        self._session += 1
        session = self._session
        self._status("starting", f"Opening microphone for {self.cfg.model}.")

        self._capture = UtteranceCapture(
            input_device_index=self.input_device_index,
            cfg=self.capture_cfg,
            on_final=lambda audio: self._queue_utterance(audio, session),
        )
        try:
            self._capture.start()
        except Exception:
            self._capture = None
            self.client = None
            self._status("failed", "Could not open the selected microphone.")
            raise

        self._worker_thread = threading.Thread(
            target=self._worker,
            args=(session,),
            name="stt_openai_transcription",
            daemon=True,
        )
        self._worker_thread.start()
        self._status("listening", f"OpenAI transcription is listening with {self.cfg.model}.")

    def _queue_utterance(self, audio: np.ndarray, session: int) -> None:
        if session != self._session or self._stop.is_set():
            return
        try:
            self._jobs.put_nowait(audio)
            return
        except queue.Full:
            pass
        try:
            self._jobs.get_nowait()
        except queue.Empty:
            pass
        try:
            self._jobs.put_nowait(audio)
        except queue.Full:
            Logger.warn("OpenAI STT dropped an utterance because requests are behind.")

    def _worker(self, session: int) -> None:
        while not self._stop.is_set() and session == self._session:
            try:
                audio = self._jobs.get(timeout=0.1)
            except queue.Empty:
                continue
            if audio is None:
                return
            self._status("transcribing", "Uploading a completed utterance to OpenAI.")
            text = self._transcribe(audio)
            if text and self._on_text and session == self._session and not self._stop.is_set():
                try:
                    self._on_text(text, False)
                except Exception as exc:
                    Logger.warn(f"OpenAI STT callback failed: {exc}")
            if text is not None and session == self._session:
                if self._paused.is_set() and self.capture_cfg.push_to_talk:
                    self._status("armed", "Push-to-talk is ready.")
                elif not self._paused.is_set():
                    self._status("listening", "OpenAI transcription is listening.")

    def _transcribe(self, pcm16: np.ndarray) -> Optional[str]:
        client = self.client
        if client is None or pcm16.size < SAMPLE_RATE // 5:
            return ""
        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(np.asarray(pcm16, dtype=np.int16).tobytes())
        wav_io.seek(0)
        wav_io.name = "speech.wav"
        try:
            result = client.audio.transcriptions.create(
                model=self.cfg.model,
                file=wav_io,
                language=self.cfg.language or None,
            )
        except Exception as exc:
            Logger.warn(f"OpenAI STT transcription failed (model={self.cfg.model!r}): {exc}")
            self._status("degraded", f"OpenAI transcription failed: {exc}")
            return None
        return str(getattr(result, "text", "") or "").strip()

    def pause(self) -> None:
        self._paused.set()
        if self._capture:
            self._capture.pause(discard=True)
        self._status("paused", "Microphone capture is paused.")

    def force_flush_and_pause(self) -> None:
        self._paused.set()
        if self._capture:
            self._capture.force_flush_and_pause()
        self._status("paused", "Push-to-talk utterance queued for OpenAI.")

    def resume(self) -> None:
        self._paused.clear()
        if self._capture:
            self._capture.resume()
        self._status("listening", "Microphone capture resumed.")

    def stop(self) -> None:
        self._session += 1
        self._stop.set()
        if self._capture:
            self._capture.stop()
        self._capture = None
        try:
            self._jobs.put_nowait(None)
        except queue.Full:
            try:
                self._jobs.get_nowait()
                self._jobs.put_nowait(None)
            except queue.Empty:
                pass
        thread = self._worker_thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        if thread and thread.is_alive():
            Logger.warn("An OpenAI transcription request is still winding down after stop.")
        self._worker_thread = None
        self.client = None
        while True:
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                break
        self._status("stopped", "OpenAI transcription stopped.")
