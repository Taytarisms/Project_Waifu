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
_chat   = None
message_log: List[Dict[str, Any]] = []
_system_prompt: str = ""
_system_prompt_loaded = False
_system_prompt_mtime: float | None = None
SYSTEM_MESSAGE_FILE = Path(__file__).with_name("system_message_grok.txt")

AVAILABLE_MODELS = [
    "grok-4.3",
    "grok-4.20-multi-agent-0309",
    "grok-4.20-0309-reasoning",
    "grok-4.20-0309-non-reasoning",
    "grok-4-1-fast-reasoning",
    "grok-4-1-fast-non-reasoning",
    "grok-3",
    "grok-3-mini",
    "grok-2-1212",
]


class Parameters:
    def __init__(self):
        self.model: str         = "grok-4.3"
        self.max_tokens: int    = 1024
        self.temperature: float = 1.0
        self.system_prompt: str = "You are Grok, a helpful AI assistant."

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model":         self.model,
            "max_tokens":    self.max_tokens,
            "temperature":   self.temperature,
            "system_prompt": self.system_prompt,
        }


def _get_client():
    global _client
    if _client is not None:
        return _client

    api_key = get_auth("xai", "token") or os.getenv("XAI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "No xAI API key found. Add your key in Settings → LLM → Grok."
        )

    from xai_sdk import AsyncClient
    _client = AsyncClient(api_key=api_key)
    return _client


def system_message(system_prompt: Optional[str] = None) -> None:
    global _chat, message_log, _system_prompt, _system_prompt_loaded, _system_prompt_mtime
    message_log = []

    from xai_sdk.chat import system as xai_system

    _params = Parameters()
    _system_prompt = system_prompt or _params.system_prompt
    _chat = _get_client().chat.create(
        model=_params.model,
        messages=[xai_system(_system_prompt)],
    )
    message_log.append({"role": "system", "content": _system_prompt})
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
        Logger.warn(f"Missing Grok system prompt template key: {e}")
        return template


def load_system_message_from_file() -> str:
    try:
        if SYSTEM_MESSAGE_FILE.exists():
            return _format_system_template(
                SYSTEM_MESSAGE_FILE.read_text(encoding="utf-8").strip()
            )
    except Exception as e:
        Logger.warn(f"Failed to load Grok system message: {e}")
    return ""


def reload_system_message(force: bool = True) -> None:
    global _chat, message_log, _system_prompt, _system_prompt_loaded, _system_prompt_mtime
    try:
        mtime = SYSTEM_MESSAGE_FILE.stat().st_mtime if SYSTEM_MESSAGE_FILE.exists() else None
    except Exception:
        mtime = None
    if _system_prompt_loaded and not force and mtime == _system_prompt_mtime:
        return
    _system_prompt = load_system_message_from_file() or Parameters().system_prompt
    _system_prompt_loaded = True
    _system_prompt_mtime = mtime
    message_log = []
    _chat = None


async def grok_response(user_text: str, settings: dict = None) -> str:
    global _chat, message_log
    reload_system_message(force=False)
    params = settings or Parameters().to_dict()
    from xai_sdk.chat import user as xai_user
    if _chat is None:
        try:
            system_message(_system_prompt or params.get("system_prompt"))
        except RuntimeError as e:
            Logger.error(str(e))
            return ""
    current_model = getattr(_chat, "_model", params.get("model", "grok-4.3"))
    if params.get("model") and params["model"] != current_model:
        try:
            system_message(_system_prompt or params.get("system_prompt"))
        except RuntimeError as e:
            Logger.error(str(e))
            return ""
    t0 = time.perf_counter()
    try:
        _chat.append(xai_user(user_text))
        response = await _chat.sample()
    except Exception as e:
        Logger.error(f"Error in Grok LLM Generation: {e}")
        print(traceback.format_exc())
        return ""
    t1 = time.perf_counter()
    text = response.content
    Logger.notify(
        f"[GROK TTFC]: {(t1 - t0):.2f}s | "
        f"prompt_msgs={len(message_log)} | "
        f"characters={len(text)}"
    )
    Logger.quiet_print(text)
    _chat.append(response)
    message_log.append({"role": "user",      "content": user_text})
    message_log.append({"role": "assistant", "content": text})
    return text
