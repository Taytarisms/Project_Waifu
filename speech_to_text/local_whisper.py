import queue
import threading
from typing import Callable, Optional

import numpy as np

from files.speech_to_text.stt_config import STTConfig
from files.speech_to_text.utterance_capture import SAMPLE_RATE, UtteranceCapture
from files.system_setup.system_logger import Logger


class STTCore:
    def __init__(
        self,
        input_device_index: Optional[int] = None,
        cfg: Optional[STTConfig] = None,
    ) -> None:
        self.cfg = cfg or STTConfig()
        self.input_device_index = input_device_index
        self._on_text: Optional[Callable[[str, bool], None]] = None
        self._on_status: Optional[Callable[[str, str], None]] = None
        self._model = None
        self._capture: Optional[UtteranceCapture] = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._final_q: queue.Queue[Optional[np.ndarray]] = queue.Queue(maxsize=8)
        self._partial_lock = threading.Lock()
        self._latest_partial: Optional[np.ndarray] = None
        self._asr_thread: Optional[threading.Thread] = None
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
        if self._asr_thread and self._asr_thread.is_alive():
            return
        self._on_text = on_text
        self._on_status = on_status
        self._stop.clear()
        self._paused.clear()
        self._session += 1
        session = self._session
        self._status("starting", f"Loading local model {self.cfg.model_name!r}.")

        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            self.cfg.model_name,
            device=self.cfg.device,
            compute_type=self.cfg.compute_type,
        )

        self._capture = UtteranceCapture(
            input_device_index=self.input_device_index,
            cfg=self.cfg,
            on_final=lambda audio: self._queue_final(audio, session),
            on_partial=lambda audio: self._queue_partial(audio, session),
        )
        try:
            self._capture.start()
        except Exception:
            self._capture = None
            self._model = None
            self._status("failed", "Could not open the selected microphone.")
            raise

        self._asr_thread = threading.Thread(
            target=self._asr_loop,
            args=(session,),
            name="stt_local_asr",
            daemon=True,
        )
        self._asr_thread.start()
        self._status("listening", f"Local Whisper is listening on device {self.input_device_index!r}.")

    def _queue_partial(self, audio: np.ndarray, session: int) -> None:
        if session != self._session or self._stop.is_set() or not self.cfg.enable_partials:
            return
        with self._partial_lock:
            self._latest_partial = audio

    def _queue_final(self, audio: np.ndarray, session: int) -> None:
        if session != self._session or self._stop.is_set():
            return
        with self._partial_lock:
            self._latest_partial = None
        try:
            self._final_q.put_nowait(audio)
        except queue.Full:
            try:
                self._final_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._final_q.put_nowait(audio)
            except queue.Full:
                Logger.warn("Local Whisper dropped an utterance because transcription is behind.")

    def _take_partial(self) -> Optional[np.ndarray]:
        with self._partial_lock:
            audio = self._latest_partial
            self._latest_partial = None
            return audio

    def _transcribe(self, audio: np.ndarray, *, partial: bool) -> str:
        model = self._model
        if model is None:
            return ""
        float_audio = audio.astype(np.float32) / 32768.0
        if partial and float_audio.shape[0] > SAMPLE_RATE * 8:
            float_audio = float_audio[-SAMPLE_RATE * 8 :]
        segments, _ = model.transcribe(
            float_audio,
            language=self.cfg.language,
            beam_size=1 if partial else self.cfg.beam_size,
            vad_filter=False,
            temperature=0.0,
        )
        return "".join(segment.text for segment in segments).strip()

    def _asr_loop(self, session: int) -> None:
        while not self._stop.is_set() and session == self._session:
            final_audio: Optional[np.ndarray]
            try:
                final_audio = self._final_q.get(timeout=0.05)
            except queue.Empty:
                final_audio = None

            if final_audio is not None:
                try:
                    text = self._transcribe(final_audio, partial=False)
                    if text and self._on_text and session == self._session and not self._stop.is_set():
                        self._on_text(text, False)
                    if session == self._session:
                        if self._paused.is_set() and self.cfg.push_to_talk:
                            self._status("armed", "Push-to-talk is ready.")
                        elif not self._paused.is_set():
                            self._status("listening", "Local Whisper is listening.")
                except Exception as exc:
                    Logger.warn(f"Local Whisper transcription failed: {exc}")
                    self._status("degraded", f"Local transcription failed: {exc}")
                continue

            partial_audio = self._take_partial()
            if partial_audio is None:
                continue
            try:
                text = self._transcribe(partial_audio, partial=True)
                if text and self._on_text and session == self._session and not self._stop.is_set():
                    self._on_text(text, True)
            except Exception as exc:
                Logger.warn(f"Local Whisper partial transcription failed: {exc}")

    def pause(self) -> None:
        self._paused.set()
        if self._capture:
            self._capture.pause(discard=True)
        self._status("paused", "Microphone capture is paused.")

    def force_flush_and_pause(self) -> None:
        self._paused.set()
        if self._capture:
            self._capture.force_flush_and_pause()
        self._status("paused", "Push-to-talk utterance submitted.")

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
        with self._partial_lock:
            self._latest_partial = None
        try:
            self._final_q.put_nowait(None)
        except queue.Full:
            pass
        thread = self._asr_thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._asr_thread = None
        if not thread or not thread.is_alive():
            self._model = None
        else:
            Logger.warn("Local Whisper inference is still winding down after stop.")
        while True:
            try:
                self._final_q.get_nowait()
            except queue.Empty:
                break
        self._status("stopped", "Local Whisper stopped.")
