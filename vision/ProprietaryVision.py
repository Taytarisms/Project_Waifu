from typing import Any, Dict, Optional, Tuple
from files.vision.VisionContext import VisionContext, get_shared_context
from files.vision.VisionWatcher import WatchMode, get_watcher

# Esentially, I've made this file acts as a wrapper

async def set_vision_mode(
    mode:    WatchMode,
    source:  str = "fullscreen",
    monitor: int = 1,
    bbox:    Optional[Tuple[int, int, int, int]] = None,
    path:    Optional[str] = None,
) -> None:
    ctx = get_shared_context()
    src = source.lower().strip()
    if src == "fullscreen":
        ctx.set_source_fullscreen()
    elif src == "monitor":
        ctx.set_source_monitor(monitor)
    elif src == "bbox" and bbox:
        ctx.set_source_bbox(*bbox)
    elif src == "image" and path:
        ctx.set_source_image(path)
    else:
        ctx.set_source_fullscreen()
    await get_watcher().set_mode(mode)


def set_vision_mode_sync(
    mode:    WatchMode,
    source:  str = "fullscreen",
    monitor: int = 1,
    bbox:    Optional[Tuple[int, int, int, int]] = None,
    path:    Optional[str] = None,
) -> None:
    ctx = get_shared_context()
    src = source.lower().strip()
    if src == "fullscreen":
        ctx.set_source_fullscreen()
    elif src == "monitor":
        ctx.set_source_monitor(monitor)
    elif src == "bbox" and bbox:
        ctx.set_source_bbox(*bbox)
    elif src == "image" and path:
        ctx.set_source_image(path)
    else:
        ctx.set_source_fullscreen()
    get_watcher().set_mode_sync(mode)

def vision_status() -> Dict[str, Any]:
    w = get_watcher()
    return {
        **w.stats,
        "monitors": VisionContext.list_monitors(),
    }


def is_vision_active() -> bool:
    return get_watcher().mode != WatchMode.DISABLED
