import asyncio
import base64
import hashlib
import io
import os
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple, Union

import mss
from PIL import Image, ImageOps

class VisionSource(Enum):
    FULLSCREEN = auto()
    MONITOR_N  = auto()
    BBOX       = auto()
    IMAGE_FILE = auto()


class Backend(str, Enum):
    CHATGPT = "chatgpt"
    CLAUDE  = "claude"
    GEMINI  = "gemini"
    GROK    = "grok"
    NOVELAI = "novelai"
    LOCAL   = "local"

    @classmethod
    def parse(cls, value: Union[str, "Backend"]) -> "Backend":
        if isinstance(value, cls):
            return value
        raw = str(value).lower().strip()
        if raw.startswith("backend."):
            raw = raw.split(".", 1)[1]
        try:
            return cls(raw)
        except ValueError:
            raise ValueError(
                f"Unknown backend '{value}'. "
                f"Valid options: {[b.value for b in cls]}"
            )

@dataclass
class Frame:
    b64:      str
    mime:     str   = "image/jpeg"
    captured: float = field(default_factory=time.time)
    source:   str   = ""

    @property
    def fingerprint(self) -> str:
        return hashlib.md5(self.b64.encode()).hexdigest()[:16]

    @property
    def data_uri(self) -> str:
        return f"data:{self.mime};base64,{self.b64}"

    @property
    def age_seconds(self) -> float:
        return time.time() - self.captured

class VisionContext:
    def __init__(
        self,
        max_dim:          int   = 960,
        jpeg_quality:     int   = 82,
        subsampling:      str   = "4:2:0",
        interval:         float = 5.0,
        change_threshold: float = 0.03,
    ):
        self.max_dim          = max_dim
        self.jpeg_quality     = jpeg_quality
        self.subsampling      = subsampling
        self.interval         = interval
        self.change_threshold = change_threshold

        self._source:       VisionSource = VisionSource.FULLSCREEN
        self._monitor_idx:  int          = 1
        self._bbox:         Optional[Tuple[int, int, int, int]] = None
        self._image_path:   Optional[str] = None

        self._latest:  Optional[Frame] = None
        self._prev_fp: Optional[str]   = None

        self._task:    Optional[asyncio.Task] = None
        self._running: bool = False
        self._on_change: Optional[Callable[["VisionContext"], Coroutine]] = None

    def set_source_fullscreen(self) -> None:
        self._source      = VisionSource.FULLSCREEN
        self._monitor_idx = 1
        self._bbox        = None
        self._image_path  = None

    def set_source_monitor(self, index: int) -> None:
        self._source      = VisionSource.MONITOR_N
        self._monitor_idx = index
        self._bbox        = None
        self._image_path  = None

    def set_source_bbox(self, left: int, top: int, right: int, bottom: int) -> None:
        self._source     = VisionSource.BBOX
        self._bbox       = (left, top, right, bottom)
        self._image_path = None

    def set_source_image(self, path: str) -> None:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Image file not found at path: {path}")
        self._source     = VisionSource.IMAGE_FILE
        self._image_path = path
        self._bbox       = None

    @property
    def source_label(self) -> str:
        if self._source == VisionSource.FULLSCREEN:
            return "Full Screen"
        if self._source == VisionSource.MONITOR_N:
            return f"Monitor {self._monitor_idx}"
        if self._source == VisionSource.BBOX and self._bbox:
            l, t, r, b = self._bbox
            return f"Region ({l},{t})\u2192({r},{b})"
        if self._source == VisionSource.IMAGE_FILE:
            return f"File: {os.path.basename(self._image_path or '')}"
        return "Unknown"

    def set_on_change(self, callback: Callable[["VisionContext"], Coroutine]) -> None:
        self._on_change = callback

    def _capture_pil(self) -> Image.Image:
        if self._source == VisionSource.IMAGE_FILE:
            return Image.open(self._image_path).convert("RGB")

        with mss.mss() as sct:
            if self._source == VisionSource.BBOX and self._bbox:
                left, top, right, bottom = self._bbox
                zone = {"left": left, "top": top,
                        "width": right - left, "height": bottom - top}
            elif self._source == VisionSource.MONITOR_N:
                zone = sct.monitors[self._monitor_idx]
            else:
                zone = sct.monitors[1]
            raw = sct.grab(zone)
            return Image.frombytes("RGB", raw.size, raw.rgb)

    def _encode_pil(self, img: Image.Image) -> str:
        img = ImageOps.exif_transpose(img)
        if max(img.size) > self.max_dim:
            img.thumbnail((self.max_dim, self.max_dim), Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.jpeg_quality,
                 optimize=True, subsampling=self.subsampling)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def capture(self) -> Frame:
        img   = self._capture_pil()
        b64   = self._encode_pil(img)
        frame = Frame(b64=b64, captured=time.time(), source=self.source_label)
        self._latest = frame
        return frame

    async def capture_async(self) -> Frame:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.capture)

    @property
    def latest_frame(self) -> Optional[Frame]:
        return self._latest

    @property
    def latest_pil(self) -> Optional[Image.Image]:
        if self._latest is None:
            return None
        data = base64.b64decode(self._latest.b64)
        return Image.open(io.BytesIO(data))

    def build_content_block(self, backend: Union[str, Backend]) -> Dict[str, Any]:
        if self._latest is None:
            raise RuntimeError("No frame captured yet.")
        return self._format(self._latest, Backend.parse(str(backend)))

    def snapshot_and_build(self, backend: Union[str, Backend]) -> Dict[str, Any]:
        frame = self.capture()
        return self._format(frame, Backend.parse(str(backend)))

    @staticmethod
    def _format(frame: Frame, backend: Backend) -> Dict[str, Any]:
        if backend in (Backend.CHATGPT, Backend.GROK, Backend.LOCAL):
            return {"type": "image_url", "image_url": {"url": frame.data_uri}}

        if backend == Backend.CLAUDE:
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": frame.mime, "data": frame.b64},
            }

        if backend == Backend.GEMINI:
            return {"inline_data": {"mime_type": frame.mime, "data": frame.b64}}

        if backend == Backend.NOVELAI:
            return {"b64": frame.b64, "mime": frame.mime}

        raise ValueError(f"Unhandled backend: {backend}")

    def _has_changed(self, frame: Frame) -> bool:
        if self._prev_fp is None or self.change_threshold == 0.0:
            return True
        if frame.fingerprint == self._prev_fp:
            return False
        prev_b64 = self._latest.b64 if self._latest else ""
        if not prev_b64:
            return True
        min_len = min(len(prev_b64), len(frame.b64))
        sample  = range(0, min_len, 4)
        diffs   = sum(1 for i in sample if prev_b64[i] != frame.b64[i])
        return (diffs / max(len(sample), 1)) >= self.change_threshold

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="VisionContext._loop")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                frame = await self.capture_async()
                if self._has_changed(frame):
                    self._prev_fp = frame.fingerprint
                    if self._on_change:
                        try:
                            await self._on_change(self)
                        except Exception as e:
                            print(f"Frame error: {e}")
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Vision loop error: {e}")
                await asyncio.sleep(self.interval)

    @property
    def is_running(self) -> bool:
        return self._running

    @staticmethod
    def list_monitors() -> List[Dict[str, int]]:
        with mss.mss() as sct:
            return [
                {"index": i, "left": m["left"], "top": m["top"],
                 "width": m["width"], "height": m["height"]}
                for i, m in enumerate(sct.monitors)
            ]

    def __repr__(self) -> str:
        status = "running" if self._running else "idle"
        frame  = f"frame@{self._latest.captured:.0f}" if self._latest else "no frame"
        return f"VisionContext(source={self.source_label!r}, status={status}, {frame})"

_shared: Optional[VisionContext] = None

def get_shared_context(**kwargs) -> VisionContext:
    global _shared
    if _shared is None:
        _shared = VisionContext(**kwargs)
    return _shared

def reset_shared_context() -> None:
    global _shared
    if _shared is not None and _shared.is_running:
        raise RuntimeError("Call the stop function before resetting.")
    _shared = None
