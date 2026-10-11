import asyncio
import base64
from collections import deque
from dataclasses import dataclass, replace
import json
import math
import queue
import time

import aiohttp
import numpy as np
from scipy.signal import resample_poly

from files.speech_to_text.deepgram_flux import DeepgramFluxCore
from files.speech_to_text.stt_config import STTConfig
from files.speech_to_text.utterance_capture import VADUtteranceSegmenter
from files.system_setup.system_logger import Logger


@dataclass
class LiveConfig:
    model: str = "gpt-live-transcribe"
    language: str = "en"
    push_to_talk: bool = False

    def url(self, rate):
        if self.model != "gpt-live-transcribe":
            raise ValueError("OpenAI Live currently supports gpt-live-transcribe.")
        return "wss://api.openai.com/v1/realtime?intent=transcription"

    def session(self):
        transcription = {"model": self.model, "delay": "low"}
        if self.language:
            transcription["languages"] = [self.language]
        return {"type": "session.update", "session": {"type": "transcription",
            "audio": {"input": {"format": {"type": "audio/pcm", "rate": 24000},
                "transcription": transcription, "turn_detection": None}}}}


class OrderedTranscripts:
    def __init__(self):
        self.order = deque()
        self.known = set()
        self.done = set()
        self.results = {}
        self.partials = {}
        self.events = set()

    def accept(self, message):
        event_id = message.get("event_id")
        if event_id and event_id in self.events:
            return [], None
        if event_id:
            self.events.add(event_id)
        item = message.get("item_id")
        if not item or item in self.done:
            return [], None
        kind = message.get("type", "")
        partial = None
        if kind == "input_audio_buffer.committed" and item not in self.known:
            self.known.add(item)
            self.order.append(item)
        elif kind.endswith("input_audio_transcription.delta") and item not in self.results:
            self.partials[item] = self.partials.get(item, "") + str(message.get("delta") or "")
            partial = self.partials[item]
        elif kind.endswith("input_audio_transcription.completed"):
            self.results[item] = str(message.get("transcript") or "").strip()
        elif kind.endswith("input_audio_transcription.failed"):
            self.results[item] = ""
        finals = []
        while self.order and self.order[0] in self.results:
            item = self.order.popleft()
            finals.append((item, self.results.pop(item)))
            self.done.add(item)
            self.partials.pop(item, None)
        return finals, partial


class StreamingBoundary(VADUtteranceSegmenter):
    def __init__(self, cfg):
        self.output = []
        self.accepted = False
        super().__init__(replace(cfg, enable_partials=False), on_final=self._accepted)
        self.manual_samples = 0

    def _accepted(self, audio):
        self.accepted = True

    def _feed_vad_frame(self, raw, frame):
        was_speech = self._in_speech
        self.accepted = False
        super()._feed_vad_frame(raw, frame)
        if not was_speech and self._in_speech:
            self.output.append(("audio", np.concatenate(self._frames)))
        elif was_speech:
            self.output.append(("audio", frame))
            if not self._in_speech:
                self.output.append(("commit" if self.accepted else "clear", None))

    def push(self, audio):
        self.output = []
        if self.cfg.push_to_talk:
            self.manual_samples += len(audio)
            self.output.append(("audio", audio))
        else:
            self.feed(audio)
        return self.output

    def release(self):
        enough = self.manual_samples >= 1600
        self.manual_samples = 0
        return [("commit" if enough else "clear", None)]


class OpenAILiveCore(DeepgramFluxCore):
    # Share microphone/connection lifecycle and generation guards with Flux.
    provider_label = "OpenAI Live"
    auth_group = "openai"
    auth_scheme = "Bearer"

    def __init__(self, input_device_index=None, cfg=None, capture_cfg=None):
        super().__init__(input_device_index, cfg or LiveConfig())
        self._jobs = queue.Queue(maxsize=128)
        self.capture_cfg = replace(capture_cfg or STTConfig(), push_to_talk=self.cfg.push_to_talk)

    def _status(self, phase, detail):
        if detail == "Deepgram Flux connected.":
            phase, detail = "starting", "OpenAI Live connected; configuring transcription."
        super()._status(phase, detail)

    async def _receive(self, ws):
        incoming = await ws.receive(timeout=0.01)
        if incoming.type == aiohttp.WSMsgType.TEXT:
            return json.loads(incoming.data)
        if incoming.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
            raise ConnectionError("OpenAI Live connection closed")
        return {}

    async def _connection(self, ws, epoch):
        await ws.send_json(self.cfg.session())
        ready = False
        connected_at = time.monotonic()
        boundary = StreamingBoundary(self.capture_cfg)
        turns = OrderedTranscripts()
        awaiting = deque()
        item_times = {}
        pending_count = 0
        while not self._stop.is_set() and epoch == self._epoch:
            if self._paused and self._flush_at is None:
                return
            now = time.monotonic()
            if not ready and now - connected_at > 10:
                raise TimeoutError("OpenAI session configuration timed out")
            if (awaiting and now - awaiting[0] > 20) or (item_times and now - min(item_times.values()) > 20):
                raise TimeoutError("OpenAI final transcription timed out")
            if self._flush_at and now - self._flush_at > 20:
                self.pause()
                self._status("degraded", "Final transcript timed out; please repeat the turn.")
                return
            if ready:
                for _ in range(4):
                    try:
                        item_epoch, payload = self._jobs.get_nowait()
                    except queue.Empty:
                        break
                    if item_epoch != epoch:
                        continue
                    if isinstance(payload, str):
                        operations = boundary.release()
                    else:
                        raw, _captured_at = payload
                        pcm = np.frombuffer(raw, dtype="<i2")
                        if self._rate != 16000:
                            divisor = math.gcd(self._rate, 16000)
                            pcm = np.clip(resample_poly(pcm.astype(np.float32), 16000 // divisor,
                                self._rate // divisor), -32768, 32767).astype(np.int16)
                        operations = boundary.push(pcm)
                    for operation, audio in operations:
                        if epoch != self._epoch or self._stop.is_set():
                            return
                        if operation == "audio":
                            samples = np.clip(resample_poly(audio.astype(np.float32), 3, 2), -32768, 32767).astype("<i2")
                            await ws.send_json({"type": "input_audio_buffer.append",
                                "audio": base64.b64encode(samples.tobytes()).decode("ascii")})
                        else:
                            await ws.send_json({"type": "input_audio_buffer." + operation})
                            if operation == "commit":
                                awaiting.append(time.monotonic())
                                pending_count += 1
                            elif self._paused and pending_count == 0:
                                self._flush_at = None
                                self._status("armed", "No speech submitted; push-to-talk is ready.")
            try:
                message = await self._receive(ws)
            except asyncio.TimeoutError:
                continue
            with self._lock:
                if epoch != self._epoch or self._stop.is_set():
                    continue
                kind = message.get("type")
                if kind == "error":
                    code = str(message.get("error", {}).get("code") or "unknown")
                    Logger.warn(f"OpenAI Live protocol error: {code}")
                    self.pause()
                    self._fatal = True
                    self._status("failed", "OpenAI rejected the streaming request. Check model access/settings and restart STT.")
                    return
                if kind in ("session.updated", "transcription_session.updated"):
                    ready = True
                    self._status("listening", "OpenAI Live transcription session ready.")
                item = message.get("item_id")
                if kind == "input_audio_buffer.committed" and item not in turns.known and awaiting:
                    item_times[item] = awaiting.popleft()
                if kind == "conversation.item.input_audio_transcription.failed":
                    self._status("degraded", "OpenAI could not transcribe a turn; please repeat it.")
                finals, partial = turns.accept(message)
                if partial and self._on_text:
                    self._on_text(partial, True)
                for item, text in finals:
                    started = item_times.pop(item, None)
                    pending_count = max(0, pending_count - 1)
                    if started is not None:
                        Logger.detail(f"STT OpenAI Live commit to final transcript: {time.monotonic() - started:.3f}s")
                    if self._paused and pending_count == 0:
                        if self._flush_at:
                            Logger.detail(f"STT OpenAI Live release to final transcript: {time.monotonic() - self._flush_at:.3f}s")
                        self._flush_at = None
                        self._status("armed", "Push-to-talk is ready.")
                    if text and self._on_text:
                        self._on_text(text, False)
                    if epoch != self._epoch or self._stop.is_set():
                        return
