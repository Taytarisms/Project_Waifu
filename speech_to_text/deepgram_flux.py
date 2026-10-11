import asyncio
from dataclasses import dataclass
import json
import queue
import threading
import time
from urllib.parse import urlencode

import aiohttp
import numpy as np
import sounddevice as sd

from files.system_setup.settings import get_auth
from files.system_setup.system_logger import Logger


@dataclass
class FluxConfig:
    model: str = "flux-general-en"
    eot_threshold: float = 0.7
    push_to_talk: bool = False

    def url(self, rate):
        if self.model not in ("flux-general-en", "flux-general-multi"):
            raise ValueError("Choose a supported Flux model.")
        if not 0.5 <= self.eot_threshold < 1.0:
            raise ValueError("Flux turn confidence must be between 0.5 and 0.99.")
        return "wss://api.deepgram.com/v2/listen?" + urlencode({
            "model": self.model, "encoding": "linear16", "sample_rate": rate,
            "eot_threshold": 1.0 if self.push_to_talk else self.eot_threshold,
            "eot_timeout_ms": 60000 if self.push_to_talk else 5000,
        })


class FluxTurns:
    def __init__(self):
        self.final_index = -1
        self.partial = ""

    def accept(self, message):
        if message.get("type") != "TurnInfo":
            return None
        index = message.get("turn_index")
        if not isinstance(index, int) or index <= self.final_index:
            return None
        text = str(message.get("transcript") or "").strip()
        event = message.get("event")
        if event == "EndOfTurn":
            self.final_index = index
            self.partial = ""
            return (text, False)
        if event in ("Update", "StartOfTurn") and text and text != self.partial:
            self.partial = text
            return (text, True)
        return None


class DeepgramFluxCore:
    provider_label = "Deepgram"
    auth_group = "deepgram"
    auth_scheme = "Token"
    def __init__(self, input_device_index=None, cfg=None):
        self.cfg = cfg or FluxConfig()
        self.input_device_index = input_device_index
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._paused = self.cfg.push_to_talk
        self._flush_at = None
        self._epoch = 0
        self._jobs = queue.Queue(maxsize=16)
        self._fatal = False
        self._stream = None
        self._thread = None
        self._on_text = None
        self._on_status = None
        self._rate = 16000

    def _status(self, phase, detail):
        if self._on_status:
            self._on_status(phase, detail.replace("Deepgram", self.provider_label))

    def start(self, on_text, on_status=None):
        self._key = str(get_auth(self.auth_group, "token") or "").strip()
        if not self._key:
            raise ValueError(f"{self.provider_label} STT requires an API key.")
        self.cfg.url(16000)
        self._on_text, self._on_status = on_text, on_status
        self._stop.clear()
        try:
            try:
                self._open_microphone(16000)
            except Exception:
                if self._stream:
                    self._stream.close()
                rate = int(sd.query_devices(self.input_device_index, "input")["default_samplerate"])
                if rate not in (8000, 16000, 24000, 44100, 48000) or rate == 16000:
                    raise
                self._open_microphone(rate)
            self._thread = threading.Thread(target=self._run, name="stt_deepgram", daemon=True)
            self._thread.start()
        except Exception:
            self.stop()
            raise

    def _open_microphone(self, rate):
        self._rate = rate
        self._stream = sd.InputStream(device=self.input_device_index, channels=1,
            samplerate=rate, dtype="int16", blocksize=int(rate * 0.08), callback=self._audio)
        self._stream.start()

    def _drain(self):
        while True:
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                return

    def _audio(self, data, frames, timing, status):
        with self._lock:
            if self._stop.is_set() or self._paused:
                return
            if status or self._jobs.full():
                # Never replay a backlog or join speech across missing audio
                self._epoch += 1
                self._drain()
            self._jobs.put_nowait((self._epoch, (np.asarray(data, dtype="<i2").tobytes(), time.monotonic())))

    def pause(self):
        with self._lock:
            self._paused = True
            self._flush_at = None
            self._epoch += 1
            self._drain()
        self._status("paused", "Deepgram microphone paused.")

    def resume(self):
        with self._lock:
            if self._fatal:
                self._status("failed", "Correct Deepgram settings and stop/restart STT.")
                return
            if self._flush_at is not None:
                self._status("transcribing", "Finishing the previous push-to-talk turn; release and press again.")
                return
            if not self._paused:
                return
            self._epoch += 1
            self._drain()
            self._paused = False
        self._status("starting", "Connecting Deepgram streaming transcription.")

    def force_flush_and_pause(self):
        with self._lock:
            if self._paused:
                return
            self._paused = True
            self._flush_at = time.monotonic()
            try:
                self._jobs.put_nowait((self._epoch, "ForceEndTurn"))
            except queue.Full:
                self._flush_at = None
                self._epoch += 1
                self._drain()
                self._status("degraded", "Deepgram audio queue overflowed; please repeat the turn.")

    def stop(self):
        self._stop.set()
        self.pause()
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._status("stopped", "Deepgram stopped.")

    def _run(self):
        try:
            asyncio.run(self._serve())
        except Exception:
            self.pause()
            self._fatal = True
            self._status("failed", "Deepgram worker stopped unexpectedly; restart STT.")

    async def _serve(self):
        failures = 0
        while not self._stop.is_set():
            if self._paused and self._flush_at is None:
                await asyncio.sleep(0.05)
                continue
            epoch = self._epoch
            try:
                connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
                async with aiohttp.ClientSession(connector=connector,
                        timeout=aiohttp.ClientTimeout(total=None, sock_connect=5, sock_read=10)) as session:
                    async with session.ws_connect(self.cfg.url(self._rate),
                            headers={"Authorization": f"{self.auth_scheme} {self._key}"}, heartbeat=5) as ws:
                        if epoch != self._epoch or self._stop.is_set():
                            continue
                        failures = 0
                        self._status("listening", "Deepgram Flux connected.")
                        await self._connection(ws, epoch)
                        if epoch != self._epoch and not self._paused and not self._stop.is_set():
                            self._status("degraded", "Deepgram audio was interrupted; starting a fresh stream.")
            except Exception as exc:
                if self._stop.is_set():
                    return
                code = getattr(exc, "status", None)
                # Never log request headers or raw exception strings containing credentials
                if code in (400, 401, 402, 403):
                    self.pause()
                    self._fatal = True
                    self._status("failed", f"Deepgram rejected the connection (HTTP {code}). Check API key, credit and model settings; restart STT.")
                    return
                failures += 1
                with self._lock:
                    self._epoch += 1
                    self._drain()
                    self._flush_at = None
                self._status("degraded", "Deepgram disconnected; incomplete audio discarded. Reconnecting.")
                deadline = time.monotonic() + min(5, 0.5 * 2 ** min(failures, 4))
                while not self._stop.is_set() and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)

    async def _connection(self, ws, epoch):
        turns = FluxTurns()
        sent_seconds = 0.0
        audio_origin = None
        while not self._stop.is_set() and epoch == self._epoch:
            if self._paused and self._flush_at is None:
                return
            if self._flush_at and time.monotonic() - self._flush_at > 5:
                with self._lock:
                    self._flush_at = None
                    self._epoch += 1
                    self._drain()
                self._status("degraded", "Deepgram final transcript timed out; please repeat the turn.")
                return
            for _ in range(8):
                try:
                    item_epoch, payload = self._jobs.get_nowait()
                except queue.Empty:
                    break
                if item_epoch != epoch:
                    continue
                if isinstance(payload, str):
                    await ws.send_json({"type": payload})
                else:
                    audio, captured_at = payload
                    sent_seconds += len(audio) / (2 * self._rate)
                    audio_origin = captured_at - sent_seconds
                    await ws.send_bytes(audio)
            try:
                incoming = await ws.receive(timeout=0.01)
            except asyncio.TimeoutError:
                continue
            if incoming.type != aiohttp.WSMsgType.TEXT:
                if incoming.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    raise ConnectionError("Flux connection closed")
                continue
            message = json.loads(incoming.data)
            if message.get("type") == "Error":
                raise ConnectionError("Flux stream error")
            with self._lock:
                if epoch != self._epoch or self._stop.is_set():
                    continue
                result = turns.accept(message)
                if result is None:
                    continue
                text, partial = result
                if not partial and text and audio_origin is not None:
                    words = message.get("words") or []
                    ends = [w.get("end") for w in words if isinstance(w.get("end"), (int, float))]
                    if ends:
                        delay = max(0.0, time.monotonic() - audio_origin - max(ends))
                        Logger.detail(f"STT Deepgram estimated last word to final transcript: {delay:.3f}s (capture clock)")
                if not partial and self._flush_at is not None:
                    Logger.detail(f"STT Deepgram release to final transcript: {time.monotonic() - self._flush_at:.3f}s")
                    self._flush_at = None
                    self._status("armed", "Push-to-talk is ready.")
                if text and self._on_text:
                    self._on_text(text, partial)
