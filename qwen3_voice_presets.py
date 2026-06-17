import sys, time, threading, queue, re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional, Literal
import numpy as np
import sounddevice as sd

try:
    from files.main_loop import turn_control
except Exception:
    turn_control = None

DefaultSampleRate = 24000 # General default for Kokoro and Qwen

@dataclass
class TTSConfig:
    sample_rate: int             = DefaultSampleRate
    output_device_index: Optional[int] = None
    split_sentences: bool        = True
    sentence_min_chars: int      = 12       # don't split fragments smaller than this
    playback_blocksize: int      = 1024
    playback_buffer_ms: int      = 80
    fade_in_ms: int              = 5
    fade_out_ms: int             = 5
    queue_timeout: float         = 0.1

class TTSEngineBase(ABC):
    engine_name: str = "base"

    def validate_config(self) -> None:
        pass

    @abstractmethod
    def load_model(self) -> None: ...

    @abstractmethod
    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        ...

    @abstractmethod
    def unload_model(self) -> None: ...
    @property
    def supports_streaming(self) -> bool:
        return False

    def synthesize_streaming(self, text: str) -> Iterator[np.ndarray]:
        audio, _ = self.synthesize(text)
        yield audio

_SENTENCE_RE = re.compile(
    r'(?<=[.!?…])'          # after sentence-ending punctuation
    r'[\s]+'                 # followed by whitespace
    r'(?=[A-Z"\'\(\[])'      # and a capital letter / opening quote
)

def split_sentences(text: str, min_chars: int = 12) -> list[str]:
    raw = _SENTENCE_RE.split(text.strip())
    if not raw:
        return []
    merged: list[str] = []
    buf = ""
    for piece in raw:
        piece = piece.strip()
        if not piece:
            continue
        if buf:
            buf += " " + piece
        else:
            buf = piece
        if len(buf) >= min_chars:
            merged.append(buf)
            buf = ""
    if buf:
        if merged:
            merged[-1] += " " + buf
        else:
            merged.append(buf)
    return merged


def caption_estimation(text: str, *, chars_per_second: float = 15.0) -> None:
    try:
        cleaned = (text or "").strip()
        if not cleaned:
            return

        duration_ms = max(
            600,
            int((len(cleaned) / max(float(chars_per_second), 1.0)) * 1000),
        )

        from files.closed_captions import caption_coordinator
        caption_coordinator.audio_ready_with_duration(duration_ms)
    except Exception:
        pass # Do nothing in case the captions don't stamp correctly

def _apply_fade(audio: np.ndarray, sr: int, fade_in_ms: int, fade_out_ms: int) -> np.ndarray:
    if len(audio) == 0:
        return audio
    if fade_in_ms > 0:
        n = min(int(sr * fade_in_ms / 1000), len(audio))
        audio[:n] *= np.linspace(0.0, 1.0, n, dtype=np.float32)
    if fade_out_ms > 0:
        n = min(int(sr * fade_out_ms / 1000), len(audio))
        audio[-n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)
    return audio

class _AudioRingBuffer:
    def __init__(self, capacity_samples: int = 960000):
        self._buf   = np.zeros(capacity_samples, dtype=np.float32)
        self._cap   = capacity_samples
        self._write = 0          # next write position
        self._read  = 0          # next read position
        self._lock  = threading.Lock()

    @property
    def available(self) -> int:
        with self._lock:
            return (self._write - self._read) % self._cap

    @property
    def free(self) -> int:
        with self._lock:
            return self._cap - 1 - ((self._write - self._read) % self._cap)

    def write(self, data: np.ndarray) -> int:
        n = len(data)
        with self._lock:
            avail_write = self._cap - 1 - ((self._write - self._read) % self._cap)
            n = min(n, avail_write)
            if n == 0:
                return 0

            w = self._write
            # Handle wrap-around
            end = w + n
            if end <= self._cap:
                self._buf[w:end] = data[:n]
            else:
                first = self._cap - w
                self._buf[w:self._cap] = data[:first]
                self._buf[0:n - first] = data[first:n]
            self._write = (w + n) % self._cap
            return n

    def read(self, out: np.ndarray) -> int:
        n = len(out)
        with self._lock:
            avail = (self._write - self._read) % self._cap
            to_read = min(n, avail)

            r = self._read
            end = r + to_read
            if end <= self._cap:
                out[:to_read] = self._buf[r:end]
            else:
                first = self._cap - r
                out[:first] = self._buf[r:self._cap]
                out[first:to_read] = self._buf[0:to_read - first]
            self._read = (r + to_read) % self._cap
        if to_read < n:
            out[to_read:] = 0.0
        return to_read

    def clear(self):
        with self._lock:
            self._read = 0
            self._write = 0

class TTSCore:
    def __init__(self, engine: TTSEngineBase, cfg: Optional[TTSConfig] = None):
        self.engine = engine
        self.cfg    = cfg or TTSConfig()
        self._text_q:  queue.Queue[Optional[str]] = queue.Queue(maxsize=64)
        self._audio_q: queue.Queue[Optional[np.ndarray]] = queue.Queue(maxsize=32)
        self._stop    = threading.Event()
        self._paused  = threading.Event()
        self._flushed = threading.Event()
        self._synth_t:    Optional[threading.Thread] = None
        self._playback_t: Optional[threading.Thread] = None
        self._stream:     Optional[sd.OutputStream]  = None
        self._ring = _AudioRingBuffer(capacity_samples=max(1, self.cfg.sample_rate) * 40)
        self._on_synth_start: Optional[Callable[[str], None]]  = None
        self._on_synth_done:  Optional[Callable[[str], None]]  = None
        self._on_playback_done: Optional[Callable[[], None]]   = None

    def start(self, *,
              on_synth_start:  Optional[Callable[[str], None]] = None,
              on_synth_done:   Optional[Callable[[str], None]] = None,
              on_playback_done: Optional[Callable[[], None]]   = None):
        if self._synth_t and self._synth_t.is_alive():
            return

        self._on_synth_start  = on_synth_start
        self._on_synth_done   = on_synth_done
        self._on_playback_done = on_playback_done

        self._stop.clear()
        self._paused.clear()
        self._flushed.clear()
        self._ring.clear()
        self.engine.validate_config()
        self.engine.load_model()

        self._synth_t = threading.Thread(
            target=self._synth_loop, name="tts_synth", daemon=True)
        self._playback_t = threading.Thread(
            target=self._playback_loop, name="tts_playback", daemon=True)
        self._synth_t.start()
        self._playback_t.start()

    def stop(self):
        self._stop.set()
        try: self._text_q.put_nowait(None)
        except queue.Full: pass
        try: self._audio_q.put_nowait(None)
        except queue.Full: pass

        for t in (self._synth_t, self._playback_t):
            if t and t.is_alive():
                t.join(timeout=2.0)
        self._synth_t = self._playback_t = None
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        try:
            self.engine.unload_model()
        except Exception:
            pass

        self._drain(self._text_q)
        self._drain(self._audio_q)
        self._ring.clear()

    def feed(self, text: str):
        if not text or not text.strip():
            return
        text = text.strip()
        caption_estimation(text)
        if self.cfg.split_sentences:
            for sentence in split_sentences(text, self.cfg.sentence_min_chars):
                self._text_q.put(sentence)
        else:
            self._text_q.put(text)

    def feed_stream(self, text_iterator):
        buf = ""
        for chunk in text_iterator:
            if self._stop.is_set() or _interrupted():
                break
            buf += chunk
            sentences = split_sentences(buf, self.cfg.sentence_min_chars)
            if len(sentences) > 1:
                for s in sentences[:-1]:
                    self._text_q.put(s)
                buf = sentences[-1]
        buf = buf.strip()
        if buf:
            self._text_q.put(buf)
    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()

    def flush(self):
        self._flushed.set()
        self._drain(self._text_q)
        self._drain(self._audio_q)
        self._ring.clear()

    def wait_until_done(self, timeout: float = None):
        while self._text_q.unfinished_tasks and not self._stop.is_set():
            _raise_if_interrupted()
            time.sleep(0.05)
        while self._audio_q.unfinished_tasks and not self._stop.is_set():
            _raise_if_interrupted()
            time.sleep(0.05)
        while self._ring.available > 0 and not self._stop.is_set():
            _raise_if_interrupted()
            time.sleep(0.05)
        time.sleep(0.15)

    def _synth_loop(self):
        cfg = self.cfg
        while not self._stop.is_set():
            if _interrupted():
                self.flush()
                break
            if self._paused.is_set() or _paused():
                time.sleep(0.05)
                continue

            try:
                text = self._text_q.get(timeout=cfg.queue_timeout)
            except queue.Empty:
                continue

            if text is None:
                self._text_q.task_done()
                break

            if self._flushed.is_set():
                self._text_q.task_done()
                self._flushed.clear()
                continue

            if self._on_synth_start:
                try: self._on_synth_start(text)
                except Exception: pass

            try:
                if self.engine.supports_streaming:
                    chunks = iter(self.engine.synthesize_streaming(text))
                    while not self._stop.is_set() and not self._flushed.is_set() and not _interrupted():
                        while _paused() and not self._stop.is_set() and not _interrupted():
                            time.sleep(0.05)
                        try:
                            chunk = next(chunks)
                        except StopIteration:
                            break
                        if self._stop.is_set() or self._flushed.is_set() or _interrupted():
                            break
                        if chunk is not None and len(chunk) > 0:
                            self._audio_q.put(chunk)
                else:
                    _raise_if_interrupted()
                    audio, sr = self.engine.synthesize(text)
                    _raise_if_interrupted()
                    if audio is not None and len(audio) > 0:
                        # Resampling just in case the config lists different
                        if sr != cfg.sample_rate:
                            audio = self._resample(audio, sr, cfg.sample_rate)
                        audio = _apply_fade(audio, cfg.sample_rate,
                                            cfg.fade_in_ms, cfg.fade_out_ms)
                        self._audio_q.put(audio)
            except Exception as exc:
                if _is_turn_interrupted_exception(exc):
                    self.flush()
                    self._text_q.task_done()
                    break
                print(f"synth error: {exc}", file=sys.stderr)

            if self._on_synth_done:
                try: self._on_synth_done(text)
                except Exception: pass

            self._text_q.task_done()

    def _playback_loop(self):
        cfg = self.cfg

        def _audio_callback(outdata, frames, time_info, status):
            if status:
                pass
            buf = outdata[:, 0] if outdata.ndim == 2 else outdata
            if self._paused.is_set() or _paused() or self._stop.is_set() or _interrupted():
                buf[:] = 0.0
                return
            self._ring.read(buf)

        self._stream = sd.OutputStream(
            samplerate=cfg.sample_rate,
            channels=1,
            dtype='float32',
            blocksize=cfg.playback_blocksize,
            device=cfg.output_device_index,
            callback=_audio_callback,
        )
        self._stream.start()

        while not self._stop.is_set():
            if _interrupted():
                self.flush()
                break

            if self._paused.is_set() or _paused():
                time.sleep(0.05)
                continue

            try:
                audio = self._audio_q.get(timeout=cfg.queue_timeout)
            except queue.Empty:
                continue

            if audio is None:
                self._audio_q.task_done()
                break

            if self._flushed.is_set():
                self._audio_q.task_done()
                self._ring.clear()
                self._flushed.clear()
                continue

            # Push audio
            offset = 0
            while offset < len(audio):
                if self._stop.is_set() or self._flushed.is_set() or _interrupted():
                    break
                if self._paused.is_set() or _paused():
                    time.sleep(0.05)
                    continue
                written = self._ring.write(audio[offset:])
                offset += written
                if offset < len(audio):
                    # Buffer is full, so let's set a pause before draining
                    time.sleep(0.005)

            if self._on_playback_done:
                try: self._on_playback_done()
                except Exception: pass

            self._audio_q.task_done()

        # Clean up stream
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    @staticmethod
    def _drain(q: queue.Queue):
        while not q.empty():
            try:
                q.get_nowait()
                q.task_done()
            except queue.Empty:
                break

    @staticmethod
    def _resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
        if src_sr == dst_sr:
            return audio
        ratio = dst_sr / src_sr
        n_out = int(len(audio) * ratio)
        indices = np.linspace(0, len(audio) - 1, n_out)
        return np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)


def _interrupted() -> bool:
    return bool(turn_control is not None and turn_control.is_interrupted())


def _paused() -> bool:
    return bool(turn_control is not None and turn_control.is_paused())


def _raise_if_interrupted() -> None:
    if turn_control is not None:
        turn_control.raise_if_interrupted()


def _is_turn_interrupted_exception(exc: Exception) -> bool:
    return bool(
        turn_control is not None
        and hasattr(turn_control, "TurnInterrupted")
        and isinstance(exc, turn_control.TurnInterrupted)
    )
