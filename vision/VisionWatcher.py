import asyncio
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Union
from files.vision.VisionContext import Backend, Frame, VisionContext, get_shared_context

class WatchMode(Enum):
    DISABLED = auto()
    REACTIVE = auto()
    PASSIVE  = auto()


@dataclass(frozen=True)
class BackendCaps:
    vision:       bool
    inline_image: bool  # False → NovelAI (image is a separate param)

_CAPS: Dict[Backend, BackendCaps] = {
    Backend.CHATGPT: BackendCaps(vision=True,  inline_image=True),
    Backend.CLAUDE:  BackendCaps(vision=True,  inline_image=True),
    Backend.GEMINI:  BackendCaps(vision=True,  inline_image=True),
    Backend.GROK:    BackendCaps(vision=True,  inline_image=True),
    Backend.NOVELAI: BackendCaps(vision=True,  inline_image=False),
    Backend.LOCAL:   BackendCaps(vision=True,  inline_image=True),
}


@dataclass
class InjectionResult:
    content:       List[Dict[str, Any]]
    novelai_image: Optional[Dict[str, str]] = None
    frame:         Optional[Frame]          = None
    vision_active: bool                     = False

    def __repr__(self) -> str:
        blocks = [b.get("type") or list(b.keys())[0] for b in self.content]
        return (
            f"InjectionResult(blocks={blocks}, vision={self.vision_active}, "
            f"novelai_image={'yes' if self.novelai_image else 'no'})"
        )

class VisionWatcher:
    def __init__(
        self,
        context:              Optional[VisionContext] = None,
        mode:                 WatchMode = WatchMode.DISABLED,
        local_vision_enabled: bool = True,
    ):
        self.ctx                 = context or get_shared_context()
        self._mode: WatchMode    = mode
        self._local_vision: bool = local_vision_enabled
        self._passive_frame: Optional[Frame] = None
        self._passive_ts:    float           = 0.0
        self._stale_seconds: float           = 30.0
        self._inject_count: int   = 0
        self._change_count: int   = 0
        self._start_time:   float = time.time()
        self.last_injection: Optional[InjectionResult] = None
        self.ctx.set_on_change(self._on_passive_change)

    @property
    def mode(self) -> WatchMode:
        return self._mode

    async def set_mode(self, mode: WatchMode) -> None:
        if mode == self._mode:
            return
        previous   = self._mode
        self._mode = mode

        if mode == WatchMode.PASSIVE:
            await self.ctx.start()
        elif previous == WatchMode.PASSIVE and self.ctx.is_running:
            await self.ctx.stop()
            self._passive_frame = None

    def set_mode_sync(self, mode: WatchMode) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.set_mode(mode))
            else:
                loop.run_until_complete(self.set_mode(mode))
        except RuntimeError:
            asyncio.run(self.set_mode(mode))

    def set_local_vision(self, enabled: bool) -> None:
        self._local_vision = enabled

    async def _on_passive_change(self, ctx: VisionContext) -> None:
        self._passive_frame = ctx.latest_frame
        self._passive_ts    = time.time()
        self._change_count += 1

    def inject(
        self,
        backend:     Union[str, Backend],
        text:        str,
        *,
        image_first: bool = True,
        force:       bool = False,
    ) -> InjectionResult:
        b    = Backend.parse(str(backend))
        caps = _CAPS[b]

        skip = (
            self._mode == WatchMode.DISABLED and not force
            or not caps.vision
            or (b == Backend.LOCAL and not self._local_vision)
        )

        if skip:
            return InjectionResult(
                content=[{"type": "text", "text": text}],
                vision_active=False,
            )

        frame      = self._resolve_frame()
        text_block = {"type": "text", "text": text}

        if not caps.inline_image:
            raw = {"b64": frame.b64, "mime": frame.mime}
            self._inject_count += 1
            return InjectionResult(
                content=[text_block], novelai_image=raw,
                frame=frame, vision_active=True,
            )

        image_block = self.ctx.build_content_block(b)
        content = [image_block, text_block] if image_first else [text_block, image_block]
        self._inject_count += 1
        return InjectionResult(content=content, frame=frame, vision_active=True)

    async def inject_async(
        self,
        backend: Union[str, Backend], text: str,
        *, image_first: bool = True, force: bool = False,
    ) -> InjectionResult:
        b    = Backend.parse(str(backend))
        caps = _CAPS[b]

        skip = (
            self._mode == WatchMode.DISABLED and not force
            or not caps.vision
            or (b == Backend.LOCAL and not self._local_vision)
        )
        if skip:
            return InjectionResult(
                content=[{"type": "text", "text": text}], vision_active=False,
            )
        
        if self._mode == WatchMode.REACTIVE or self._needs_refresh():
            frame = await self.ctx.capture_async()
        else:
            frame = self.ctx.latest_frame

        if frame is None:
            frame = await self.ctx.capture_async()

        if self._mode == WatchMode.PASSIVE:
            self._passive_frame = frame
            self._passive_ts    = time.time()

        text_block = {"type": "text", "text": text}

        if not caps.inline_image:
            raw = {"b64": frame.b64, "mime": frame.mime}
            self._inject_count += 1
            return InjectionResult(
                content=[text_block], novelai_image=raw,
                frame=frame, vision_active=True,
            )

        image_block = self.ctx.build_content_block(b)
        content = [image_block, text_block] if image_first else [text_block, image_block]
        self._inject_count += 1
        return InjectionResult(content=content, frame=frame, vision_active=True)

    def _resolve_frame(self) -> Frame:
        if self._mode == WatchMode.REACTIVE:
            return self.ctx.capture()

        if self._mode == WatchMode.PASSIVE:
            if self._passive_frame and not self._needs_refresh():
                self.ctx._latest = self._passive_frame
                return self._passive_frame
            frame = self.ctx.capture()
            self._passive_frame = frame
            self._passive_ts    = time.time()
            return frame

        return self.ctx.capture()

    def _needs_refresh(self) -> bool:
        return (time.time() - self._passive_ts) > self._stale_seconds

    def use_fullscreen(self) -> None:
        self.ctx.set_source_fullscreen()

    def use_monitor(self, index: int) -> None:
        self.ctx.set_source_monitor(index)

    def use_bbox(self, left: int, top: int, right: int, bottom: int) -> None:
        self.ctx.set_source_bbox(left, top, right, bottom)

    def use_image(self, path: str) -> None:
        self.ctx.set_source_image(path)

    @staticmethod
    def list_monitors():
        return VisionContext.list_monitors()

    @property
    def stats(self) -> Dict[str, Any]:
        uptime = time.time() - self._start_time
        return {
            "mode":         self._mode.name,
            "source":       self.ctx.source_label,
            "loop_running": self.ctx.is_running,
            "inject_count": self._inject_count,
            "change_count": self._change_count,
            "uptime_sec":   round(uptime, 1),
            "latest_frame": (
                round(self.ctx.latest_frame.age_seconds, 1)
                if self.ctx.latest_frame else None
            ),
        }

    def __repr__(self) -> str:
        s = self.stats
        return (
            f"VisionWatcher(mode={s['mode']}, source={s['source']!r}, "
            f"injects={s['inject_count']}, changes={s['change_count']})"
        )

_watcher: Optional[VisionWatcher] = None


def _load_saved_mode() -> WatchMode:
    try:
        from files.system_setup.settings import get_settings
        raw = get_settings("vision_mode")
    except Exception:
        return WatchMode.DISABLED
    if not raw:
        return WatchMode.DISABLED
    key = str(raw).strip().lower()
    return {
        "off":      WatchMode.DISABLED,
        "disabled": WatchMode.DISABLED,
        "reactive": WatchMode.REACTIVE,
        "passive":  WatchMode.PASSIVE,
    }.get(key, WatchMode.DISABLED)


def get_watcher(
    context:              Optional[VisionContext] = None,
    mode:                 Optional[WatchMode] = None,
    local_vision_enabled: bool = True,
) -> VisionWatcher:
    global _watcher
    if _watcher is None:
        effective_mode = mode if mode is not None else _load_saved_mode()
        _watcher = VisionWatcher(
            context=context, mode=effective_mode,
            local_vision_enabled=local_vision_enabled,
        )
    return _watcher

def reset_watcher() -> None:
    global _watcher
    _watcher = None
