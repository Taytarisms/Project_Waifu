from collections import deque
import math
import queue
import threading
import time
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
import webrtcvad # Sourced from: https://github.com/wiseman/py-webrtcvad - pretty good for low latency

from files.speech_to_text.stt_config import STTConfig
from files.system_setup.system_logger import Logger


SAMPLE_RATE = 16_000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2


class VADUtteranceSegmenter:
    def __init__(
        self,
        cfg: STTConfig,
        *,
        on_final: Callable[[np.ndarray], None],
        on_partial: Optional[Callable[[np.ndarray], None]] = None,
    ) -> None:
        self.cfg = cfg
        self.on_final = on_final
        self.on_partial = on_partial
        self._vad = webrtcvad.Vad(max(0, min(3, int(cfg.vad_aggressiveness))))
        self._byte_buffer = bytearray()
        self._pre_frames: deque[np.ndarray] = deque(
            maxlen=max(1, math.ceil(max(0, cfg.pre_speech_ms) / FRAME_MS))
        )
        self._frames: list[np.ndarray] = []
        self._manual_frames: list[np.ndarray] = []
        self._in_speech = False
        self._voiced_ms = 0
        self._silence_ms = 0
        self._last_partial_at = 0.0

    def reset(self) -> None:
        self._byte_buffer.clear()
        self._pre_frames.clear()
        self._frames.clear()
        self._manual_frames.clear()
        self._in_speech = False
        self._voiced_ms = 0
        self._silence_ms = 0
        self._last_partial_at = 0.0

    def feed(self, pcm16: np.ndarray) -> None:
        if pcm16.size == 0:
            return
        self._byte_buffer.extend(np.asarray(pcm16, dtype=np.int16).tobytes())
        while len(self._byte_buffer) >= FRAME_BYTES:
            raw = bytes(self._byte_buffer[:FRAME_BYTES])
            del self._byte_buffer[:FRAME_BYTES]
            frame = np.frombuffer(raw, dtype=np.int16).copy()
            if self.cfg.push_to_talk:
                self._manual_frames.append(frame)
            else:
                self._feed_vad_frame(raw, frame)

    def _feed_vad_frame(self, raw: bytes, frame: np.ndarray) -> None:
        try:
            is_speech = self._vad.is_speech(raw, SAMPLE_RATE)
        except Exception as exc:
            Logger.warn(f"STT VAD rejected an audio frame: {exc}")
            return

        if not self._in_speech:
            self._pre_frames.append(frame)
            if not is_speech:
                return
            self._in_speech = True
            self._frames = list(self._pre_frames)
            self._voiced_ms = FRAME_MS
            self._silence_ms = 0
            self._last_partial_at = time.monotonic()
            return

        self._frames.append(frame)
        if is_speech:
            self._voiced_ms += FRAME_MS
            self._silence_ms = 0
        else:
            self._silence_ms += FRAME_MS

        now = time.monotonic()
        partial_interval = max(0.1, self.cfg.partial_interval_ms / 1000.0)
        if (
            self.cfg.enable_partials
            and self.on_partial is not None
            and now - self._last_partial_at >= partial_interval
        ):
            self.on_partial(np.concatenate(self._frames))
            self._last_partial_at = now

        total_ms = len(self._frames) * FRAME_MS
        if (
            self._silence_ms >= max(FRAME_MS, self.cfg.post_speech_ms)
            or total_ms >= max(1_000, self.cfg.segment_max_ms)
        ):
            self._finalize_vad()

    def _finalize_vad(self) -> None:
        frames = self._frames
        voiced_ms = self._voiced_ms
        self._pre_frames.clear()
        self._frames = []
        self._in_speech = False
        self._voiced_ms = 0
        self._silence_ms = 0
        self._last_partial_at = 0.0
        if not frames or voiced_ms < max(FRAME_MS, self.cfg.min_length_ms):
            return
        self.on_final(np.concatenate(frames))

    def flush(self) -> None:
        if self.cfg.push_to_talk:
            frames = self._manual_frames
            self._manual_frames = []
            duration_ms = len(frames) * FRAME_MS
            if frames and duration_ms >= min(200, max(FRAME_MS, self.cfg.min_length_ms)):
                self.on_final(np.concatenate(frames))
            self._byte_buffer.clear()
            return
        if self._in_speech:
            self._finalize_vad()
        self._byte_buffer.clear()


class UtteranceCapture:
    def __init__(
        self,
        *,
        input_device_index: Optional[int],
        cfg: STTConfig,
        on_final: Callable[[np.ndarray], None],
        on_partial: Optional[Callable[[np.ndarray], None]] = None,
    ) -> None:
        self.input_device_index = input_device_index
        self.cfg = cfg
        self._segmenter = VADUtteranceSegmenter(
            cfg,
            on_final=on_final,
            on_partial=on_partial,
        )
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=128)
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._stream: Optional[sd.InputStream] = None
        self._capture_rate = SAMPLE_RATE
        self._last_overflow_log = 0.0

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._paused.clear()
        self._segmenter.reset()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="stt_capture",
            daemon=True,
        )
        self._worker.start()

        try:
            self._open_stream(SAMPLE_RATE, FRAME_SAMPLES)
        except Exception as primary_error:
            self._close_stream()
            try:
                device = sd.query_devices(self.input_device_index, "input")
                fallback_rate = int(round(float(device.get("default_samplerate", SAMPLE_RATE))))
                if fallback_rate <= 0 or fallback_rate == SAMPLE_RATE:
                    raise primary_error
                self._open_stream(fallback_rate, 0)
                Logger.warn(
                    f"STT device does not accept {SAMPLE_RATE} Hz directly; "
                    f"capturing at {fallback_rate} Hz and resampling."
                )
            except Exception:
                self._stop.set()
                self._put_control("stop", None)
                if self._worker.is_alive():
                    self._worker.join(timeout=1.0)
                self._worker = None
                raise primary_error

    def _open_stream(self, sample_rate: int, blocksize: int) -> None:
        self._capture_rate = int(sample_rate)
        self._stream = sd.InputStream(
            device=self.input_device_index,
            channels=1,
            samplerate=self._capture_rate,
            dtype="float32",
            blocksize=blocksize,
            callback=self._audio_callback,
        )
        self._stream.start()

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        if self._stop.is_set() or self._paused.is_set():
            return
        if status:
            now = time.monotonic()
            if now - self._last_overflow_log >= 5.0:
                Logger.warn(f"STT input status: {status}")
                self._last_overflow_log = now
        mono = np.asarray(indata, dtype=np.float32)
        if mono.ndim > 1:
            mono = mono.mean(axis=1)
        self._put_audio(mono.copy(), self._capture_rate)

    def _put_audio(self, block: np.ndarray, sample_rate: int) -> None:
        item = ("audio", (block, sample_rate))
        try:
            self._queue.put_nowait(item)
            return
        except queue.Full:
            pass
        try:
            displaced = self._queue.get_nowait()
        except queue.Empty:
            displaced = None
        if displaced is not None and displaced[0] != "audio":
            try:
                self._queue.put_nowait(displaced)
            except queue.Full:
                pass
            return
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            pass

    def _put_control(self, command: str, value: object) -> None:
        while True:
            try:
                self._queue.put((command, value), timeout=0.1)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    return

    @staticmethod
    def _pcm16(block: np.ndarray, source_rate: int) -> np.ndarray:
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        if source_rate != SAMPLE_RATE and block.size:
            target_length = max(1, int(round(block.size * SAMPLE_RATE / source_rate)))
            source_positions = np.arange(block.size, dtype=np.float64)
            target_positions = np.linspace(
                0,
                max(0, block.size - 1),
                target_length,
                endpoint=True,
            )
            block = np.interp(target_positions, source_positions, block).astype(np.float32)
        return np.clip(block * 32768.0, -32768, 32767).astype(np.int16)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                command, value = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if command == "stop":
                self._segmenter.reset()
                return
            if command == "reset":
                self._segmenter.reset()
                continue
            if command == "flush":
                self._segmenter.flush()
                if isinstance(value, threading.Event):
                    value.set()
                continue
            if command == "audio":
                block, sample_rate = value
                self._segmenter.feed(self._pcm16(block, int(sample_rate)))

    def pause(self, *, discard: bool = True) -> None:
        self._paused.set()
        if discard:
            self._put_control("reset", None)

    def resume(self) -> None:
        self._put_control("reset", None)
        self._paused.clear()

    def force_flush_and_pause(self) -> None:
        self._paused.set()
        completed = threading.Event()
        self._put_control("flush", completed)
        completed.wait(timeout=0.75)

    def _close_stream(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

    def stop(self) -> None:
        self._paused.set()
        self._close_stream()
        self._stop.set()
        self._put_control("stop", None)
        worker = self._worker
        if worker and worker.is_alive():
            worker.join(timeout=1.5)
        self._worker = None
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
