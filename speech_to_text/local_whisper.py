import sys, time, threading, queue
from dataclasses import dataclass
from typing import Callable, Optional, Literal

import numpy as np
import sounddevice as sd
import webrtcvad
from faster_whisper import WhisperModel

"""
Gutted and rescripted a smaller version the RealtimeSTT from KoljaB
If you want the full repo for yourself, you can find it here:
https://github.com/KoljaB/RealtimeSTT
"""

SampleRate   = 16000
FrameMs      = 20
FrameLen     = int(SampleRate * FrameMs / 1000)   # 320 samples
BytesPerSamp = 2                                   # int16 mono


@dataclass
class STTConfig:
    device: Literal["cpu", "cuda"]   = "cuda"
    model_name: str                  = "tiny"
    compute_type: str                = "float16"

    vad_aggressiveness: int          = 1
    pre_speech_ms: int               = 1000
    post_speech_ms: int              = 600
    segment_max_ms: int              = 15000
    min_length_ms: int               = 1000
    min_gap_ms: int                  = 400

    enable_partials: bool            = True
    partial_interval_ms: int         = 200

    allowed_latency_ms: int          = 100
    handle_overflow: bool            = True
    input_blocksize: int             = 320
    enforce_exact_vad_frames: bool   = False

    language: Optional[str]          = "en"
    beam_size: int                   = 1


class STTCore:
    def __init__(self, input_device_index: Optional[int] = None,
                 cfg: Optional[STTConfig] = None):
        self.cfg = cfg or STTConfig()
        self.input_device_index = input_device_index

        self._audio_q:   queue.Queue[np.ndarray] = queue.Queue(maxsize=64)
        self._segment_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=8)

        self._stop            = threading.Event()
        self._paused          = threading.Event()       # .set() = paused
        self._flush_requested = threading.Event()       # PTT release signal
        self._flush_completed = threading.Event()
        self._flush_completed.set()

        self._audio_t: Optional[threading.Thread] = None
        self._vad_t:   Optional[threading.Thread] = None
        self._asr_t:   Optional[threading.Thread] = None
        self._on_text: Optional[Callable[[str, bool], None]] = None
        self._stream:  Optional[sd.InputStream] = None
        self._model:   Optional[WhisperModel]    = None

        self._vad_frame_acc = bytearray()

    def start(self, on_text: Callable[[str, bool], None]):
        if self._audio_t and self._audio_t.is_alive():
            return
        self._on_text = on_text
        self._stop.clear()
        self._paused.clear()
        self._flush_requested.clear()
        self._flush_completed.set()

        self._model = WhisperModel(
            self.cfg.model_name,
            device=self.cfg.device,
            compute_type=self.cfg.compute_type,
        )

        self._audio_t = threading.Thread(target=self._audio_loop, name="stt_audio", daemon=True)
        self._vad_t   = threading.Thread(target=self._vad_loop,   name="stt_vad",   daemon=True)
        self._asr_t   = threading.Thread(target=self._asr_loop,   name="stt_asr",   daemon=True)
        self._audio_t.start()
        self._vad_t.start()
        self._asr_t.start()

    def stop(self):
        self._stop.set()
        for t in (self._audio_t, self._vad_t, self._asr_t):
            if t and t.is_alive():
                t.join(timeout=1.0)
        self._audio_t = self._vad_t = self._asr_t = None

        try:
            if self._stream:
                self._stream.close()
        except Exception:
            pass
        self._stream = None
        self._model  = None

        for q in (self._audio_q, self._segment_q):
            while not q.empty():
                try:    q.get_nowait()
                except queue.Empty: break

    def pause(self):
        self._paused.set()

    def force_flush_and_pause(self):
        self._flush_completed.clear()
        self._flush_requested.set()
        self._paused.set()

    def resume(self):
        if self._paused.is_set():
            if self._flush_requested.is_set() and not self._flush_completed.wait(timeout=0.35):
                self._flush_requested.clear()
                self._flush_completed.set()
            while not self._audio_q.empty():
                try:
                    self._audio_q.get_nowait()
                except queue.Empty:
                    break
            self._vad_frame_acc = bytearray()
            self._paused.clear()

    def _audio_loop(self):
        cfg = self.cfg

        def callback(indata, frames, t, status):
            if status and not cfg.handle_overflow:
                print(f"[stt] input status: {status}", file=sys.stderr)
            if self._paused.is_set():
                return
            x = indata if indata.ndim == 1 else indata.mean(axis=1)
            self._enqueue_audio(x)

        self._stream = sd.InputStream(
            device=self.input_device_index,
            channels=1,
            samplerate=SampleRate,
            dtype="float32",
            blocksize=cfg.input_blocksize,
            callback=callback,
        )
        self._stream.start()

        while not self._stop.is_set():
            time.sleep(0.05)

    def _enqueue_audio(self, float_mono: np.ndarray):
        cfg   = self.cfg
        pcm16 = np.clip(float_mono * 32768.0, -32768, 32767).astype(np.int16)

        queued = self._audio_q.qsize()
        if queued * FrameMs > cfg.allowed_latency_ms:
            return

        try:    self._audio_q.put_nowait(pcm16)
        except queue.Full: pass

    def _vad_loop(self):
        import math
        cfg = self.cfg
        vad = webrtcvad.Vad(cfg.vad_aggressiveness)

        pre_ms  = cfg.pre_speech_ms
        post_ms = cfg.post_speech_ms
        max_ms  = cfg.segment_max_ms
        min_len = cfg.min_length_ms
        min_gap = cfg.min_gap_ms

        pre_buf:    list[np.ndarray] = []
        speech_buf: list[np.ndarray] = []

        ms_in_frame = FrameMs
        silence_ms  = 0
        in_speech   = False
        speech_ms   = 0
        post_frames = 0

        pre_frames_needed  = max(1, math.ceil(pre_ms  / ms_in_frame))
        post_frames_needed = max(1, math.ceil(post_ms / ms_in_frame))

        last_segment_end = 0.0
        need = FrameLen * BytesPerSamp      # bytes for one 20 ms frame

        def flush_segment():
            nonlocal pre_buf, speech_buf, silence_ms, in_speech, speech_ms
            nonlocal last_segment_end, post_frames
            if not speech_buf:
                # Nothing to flush, just reset state
                pre_buf.clear()
                silence_ms  = 0
                in_speech   = False
                speech_ms   = 0
                post_frames = 0
                return

            total_speech_samples = sum(len(f) for f in speech_buf)
            seg_ms = int(1000 * total_speech_samples / SampleRate)
            if seg_ms < min_len:
                pre_buf.clear();  speech_buf.clear()
                silence_ms = 0;   in_speech = False
                speech_ms  = 0;   post_frames = 0
                last_segment_end = time.perf_counter()
                return

            seg = np.concatenate(pre_buf + speech_buf) if pre_buf else np.concatenate(speech_buf)

            pre_buf.clear();  speech_buf.clear()
            silence_ms = 0;   in_speech = False
            speech_ms  = 0;   post_frames = 0
            last_segment_end = time.perf_counter()

            try:    self._segment_q.put_nowait(seg)
            except queue.Full: pass

        while not self._stop.is_set():

            # PTT release
            if self._flush_requested.is_set():
                self._flush_requested.clear()
                flush_segment()
                self._vad_frame_acc = bytearray()
                self._paused.set()
                self._flush_completed.set()
                continue

            if self._paused.is_set():
                time.sleep(0.05)
                continue

            try:
                block = self._audio_q.get(timeout=0.1)
            except queue.Empty:
                continue

            self._vad_frame_acc += block.tobytes()

            while len(self._vad_frame_acc) >= need:
                chunk = self._vad_frame_acc[:need]
                self._vad_frame_acc = self._vad_frame_acc[need:]

                # Cooldown between segments
                now = time.perf_counter()
                if not in_speech and (now - last_segment_end) * 1000.0 < min_gap:
                    frame = np.frombuffer(chunk, dtype=np.int16)
                    pre_buf.append(frame)
                    if len(pre_buf) > pre_frames_needed:
                        pre_buf = pre_buf[-pre_frames_needed:]
                    continue

                try:
                    is_speech = vad.is_speech(chunk, SampleRate)
                except Exception:
                    continue

                frame = np.frombuffer(chunk, dtype=np.int16)

                if not in_speech:
                    pre_buf.append(frame)
                    if len(pre_buf) > pre_frames_needed:
                        pre_buf = pre_buf[-pre_frames_needed:]
                    if is_speech:
                        in_speech  = True
                        speech_buf = [frame]
                        silence_ms = 0
                        post_frames = 0
                        speech_ms  = ms_in_frame
                else:
                    speech_buf.append(frame)
                    speech_ms += ms_in_frame
                    if is_speech:
                        silence_ms  = 0
                        post_frames = 0
                    else:
                        silence_ms  += ms_in_frame
                        post_frames += 1
                        if post_frames >= post_frames_needed or speech_ms >= max_ms:
                            flush_segment()

    def _asr_loop(self):
        assert self._model is not None
        cfg = self.cfg
        last_partial_time = 0.0
        partial_every = cfg.partial_interval_ms / 1000.0

        while not self._stop.is_set():
            try:
                seg_pcm16 = self._segment_q.get(timeout=0.1)
            except queue.Empty:
                if self._paused.is_set():
                    time.sleep(0.05)
                continue

            audio = seg_pcm16.astype(np.float32) / 32768.0

            if cfg.enable_partials:
                now = time.time()
                if now - last_partial_time >= partial_every:
                    clip = audio[:SampleRate * 3] if audio.shape[0] > SampleRate * 3 else audio
                    try:
                        segments, _ = self._model.transcribe(
                            clip, language=cfg.language,
                            beam_size=1, vad_filter=False, temperature=0.0,
                        )
                        partial = "".join(s.text for s in segments).strip()
                        if partial and self._on_text:
                            self._on_text(partial, True)
                    except Exception:
                        pass
                    last_partial_time = now

            try:
                segments, _ = self._model.transcribe(
                    audio, language=cfg.language,
                    beam_size=cfg.beam_size, vad_filter=False, temperature=0.0,
                )
                full_text = "".join(s.text for s in segments).strip()
                if full_text and self._on_text:
                    self._on_text(full_text, False)
            except Exception:
                pass
