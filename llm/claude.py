import os
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from files.system_setup.settings import get_auth
from files.system_setup.system_logger import Logger
from files.vision.VisionWatcher import get_watcher

_client = None
message_log: List[Dict[str, Any]] = []
_system_prompt: str = ""
_system_prompt_loaded = False
_system_prompt_mtime: float | None = None
SYSTEM_MESSAGE_FILE = Path(__file__).with_name("system_message_claude.txt")

AVAILABLE_MODELS = [
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-opus-4-5-20251101",
    "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5-20251001",
]


class Parameters:
    def __init__(self):
        self.model: str              = "claude-sonnet-4-6"
        self.max_tokens: int         = 1024
        self.temperature: float      = 1.0
        self.top_p: float            = 1.0
        self.top_k: Optional[int]    = None
        self.system: Optional[str]   = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "model":       self.model,
            "max_tokens":  self.max_tokens,
            "temperature": self.temperature,
            "top_p":       self.top_p,
        }
        if self.top_k is not None:
            d["top_k"] = self.top_k
        if self.system:
            d["system"] = self.system
        return d


def _get_client():
    global _client
    if _client is not None:
        return _client

    api_key = get_auth("anthropic", "token") or os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "No Anthropic API key found. Add your key in Settings → LLM → Claude."
        )

    from anthropic import AsyncAnthropic
    _client = AsyncAnthropic(api_key=api_key)
    return _client


def system_message(system_prompt: Optional[str] = None) -> None:
    global message_log, _system_prompt, _system_prompt_loaded, _system_prompt_mtime
    message_log    = []
    _system_prompt = system_prompt or ""
    _system_prompt_loaded = True
    try:
        _system_prompt_mtime = SYSTEM_MESSAGE_FILE.stat().st_mtime
    except Exception:
        _system_prompt_mtime = None


def _format_system_template(template: str) -> str:
    now = datetime.now()
    try:
        return template.format(
            date_str=now.strftime("%B %d, %Y"),
            weekday_str=now.strftime("%A"),
        )
    except KeyError as e:
        Logger.warn(f"Missing Claude system prompt template key: {e}")
        return template


def load_system_message_from_file() -> str:
    try:
        if SYSTEM_MESSAGE_FILE.exists():
            return _format_system_template(
                SYSTEM_MESSAGE_FILE.read_text(encoding="utf-8").strip()
            )
    except Exception as e:
        Logger.warn(f"Failed to load Claude system message: {e}")
    return ""


def reload_system_message(force: bool = True) -> None:
    global _system_prompt_loaded, _system_prompt_mtime
    try:
        mtime = SYSTEM_MESSAGE_FILE.stat().st_mtime if SYSTEM_MESSAGE_FILE.exists() else None
    except Exception:
        mtime = None
    if _system_prompt_loaded and not force and mtime == _system_prompt_mtime:
        return
    system_message(load_system_message_from_file())
    _system_prompt_loaded = True
    _system_prompt_mtime = mtime


async def claude_response(user_text: str, settings: dict = None) -> str:
    global message_log, _system_prompt
    reload_system_message(force=False)
    params = settings or Parameters().to_dict()
    watcher = get_watcher()
    injection = getattr(watcher, "last_injection", None)
    if injection and injection.vision_active:
        user_content = injection.content
    else:
        user_content = [{"type": "text", "text": user_text}]

    message_log.append({
        "role":    "user",
        "content": user_content,
    })

    try:
        client = _get_client()
    except RuntimeError as e:
        Logger.error(str(e))
        message_log.pop()
        return ""

    t0 = time.perf_counter()

    try:
        completion = await client.messages.create(
            model      = params.get("model",       "claude-sonnet-4-6"),
            max_tokens = params.get("max_tokens",  1024),
            temperature= params.get("temperature", 1.0),
            top_p      = params.get("top_p",       1.0),
            **({"top_k": params["top_k"]} if params.get("top_k") else {}),
            **({"system": params["system"]} if params.get("system")
               else ({"system": _system_prompt} if _system_prompt else {})),
            messages=message_log,
        )
    except Exception as e:
        Logger.error(f"Error in Claude LLM Generation: {e}")
        print(traceback.format_exc())
        message_log.pop()
        return ""
    t1   = time.perf_counter()
    text = completion.content[0].text
    Logger.notify(
        f"[CLAUDE TTFC]: {(t1 - t0):.2f}s | "
        f"prompt_msgs={len(message_log)} | "
        f"in={completion.usage.input_tokens} | out={completion.usage.output_tokens} | "
        f"characters={len(text)}"
    )
    Logger.quiet_print(text)
    message_log.append({
        "role":    "assistant",
        "content": [{"type": "text", "text": text}],
    })
    return text
