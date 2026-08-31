from dataclasses import dataclass
import queue
import threading
from typing import Callable, Optional

import numpy as np
import speech_recognition as sr

from files.speech_to_text.stt_config import STTConfig
from files.speech_to_text.utterance_capture import SAMPLE_RATE, UtteranceCapture
from files.system_setup.system_logger import Logger


@dataclass
class GoogleSTTConfig:
    language: str = "en-US"
    timeout: float = 0.5
    phrase_time_limit: float = 8.0
    adjust_ambient: bool = True
    energy_threshold: Optional[int] = None


class GoogleSTTCore:
    def __init__(
        self,
        input_device_index: Optional[int] = None,
        cfg: Optional[GoogleSTTConfig] = None,
        capture_cfg: Optional[STTConfig] = None,
    ) -> None:
        self.input_device_index = input_device_index
        self.cfg = cfg or GoogleSTTConfig()
        self.capture_cfg = capture_cfg or STTConfig(enable_partials=False)
        self.recognizer = sr.Recognizer()
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
        self._on_text = on_text
        self._on_status = on_status
        self._stop.clear()
        self._paused.clear()
        self._session += 1
        session = self._session
        self._status("starting", "Opening the selected microphone for Google STT.")

        self._capture = UtteranceCapture(
            input_device_index=self.input_device_index,
            cfg=self.capture_cfg,
            on_final=lambda audio: self._queue_utterance(audio, session),
        )
        try:
            self._capture.start()
        except Exception:
            self._capture = None
            self._status("failed", "Could not open the selected microphone.")
            raise

        self._worker_thread = threading.Thread(
            target=self._worker,
            args=(session,),
            name="stt_google_recognition",
            daemon=True,
        )
        self._worker_thread.start()
        self._status("listening", "Google STT is listening.")

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
            Logger.warn("Google STT dropped an utterance because requests are behind.")

    def _worker(self, session: int) -> None:
        while not self._stop.is_set() and session == self._session:
            try:
                pcm16 = self._jobs.get(timeout=0.1)
            except queue.Empty:
                continue
            if pcm16 is None:
                return
            self._status("transcribing", "Sending a completed utterance to Google.")
            audio = sr.AudioData(
                np.asarray(pcm16, dtype=np.int16).tobytes(),
                SAMPLE_RATE,
                2,
            )
            try:
                text = self.recognizer.recognize_google(
                    audio,
                    language=self.cfg.language or "en-US",
                ).strip()
                request_ok = True
            except sr.UnknownValueError:
                text = ""
                request_ok = True
            except sr.RequestError as exc:
                Logger.warn(f"Google STT request failed: {exc}")
                self._status("degraded", f"Google request failed: {exc}")
                text = ""
                request_ok = False
            except Exception as exc:
                Logger.warn(f"Google STT recognition failed: {exc}")
                self._status("degraded", f"Google recognition failed: {exc}")
                text = ""
                request_ok = False

            if text and self._on_text and session == self._session and not self._stop.is_set():
                try:
                    self._on_text(text, False)
                except Exception as exc:
                    Logger.warn(f"Google STT callback failed: {exc}")
            if request_ok and session == self._session:
                if self._paused.is_set() and self.capture_cfg.push_to_talk:
                    self._status("armed", "Push-to-talk is ready.")
                elif not self._paused.is_set():
                    self._status("listening", "Google STT is listening.")

    def pause(self) -> None:
        self._paused.set()
        if self._capture:
            self._capture.pause(discard=True)
        self._status("paused", "Microphone capture is paused.")

    def force_flush_and_pause(self) -> None:
        self._paused.set()
        if self._capture:
            self._capture.force_flush_and_pause()
        self._status("paused", "Push-to-talk utterance queued for Google.")

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
            Logger.warn("A Google recognition request is still winding down after stop.")
        self._worker_thread = None
        while True:
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                break
        self._status("stopped", "Google STT stopped.")
