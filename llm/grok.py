import os
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from files.provider_capabilities import GROK_CHAT_MODELS
from files.system_setup.settings import get_auth, get_settings
from files.system_setup.system_logger import Logger
from files.vision.VisionWatcher import get_watcher
from files.llm.generation_result import GenerationResult

_client = None
_chat   = None
_chat_model = ""
_chat_config: tuple = ()
message_log: List[Dict[str, Any]] = []
_system_prompt: str = ""
_system_prompt_loaded = False
_system_prompt_mtime: float | None = None
SYSTEM_MESSAGE_FILE = Path(__file__).with_name("system_message_grok.txt")

AVAILABLE_MODELS = GROK_CHAT_MODELS


class Parameters:
    def __init__(self):
        self.model: str         = "grok-4.5"
        self.max_tokens: int    = 1024
        self.temperature: float = 1.0
        self.reasoning_effort: str = "medium"
        self.system_prompt: str = "You are Grok, a helpful AI assistant."

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model":         self.model,
            "max_tokens":    self.max_tokens,
            "temperature":   self.temperature,
            "reasoning_effort": self.reasoning_effort,
            "system_prompt": self.system_prompt,
        }


def build_grok_params(settings: dict | None = None) -> Dict[str, Any]:
    if settings is not None:
        params = dict(settings)
    else:
        def saved(name, default):
            value = get_settings(name)
            return default if value in ("", None) else value
        params = {
            "model": saved("grok_model", "grok-4.5"),
            "max_tokens": int(saved("grok_max_tokens", 1024)),
            "temperature": float(saved("grok_temperature", 1.0)),
            "reasoning_effort": saved("grok_reasoning_effort", "medium"),
        }
    params.setdefault("model", "grok-4.5")
    params.setdefault("max_tokens", 1024)
    params.setdefault("temperature", 1.0)
    return params


def build_grok_chat_kwargs(params: dict, messages: list) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "model": str(params.get("model") or "grok-4.5"),
        "messages": messages,
        "max_tokens": int(params.get("max_tokens") or 1024),
        "temperature": float(params.get("temperature", 1.0)),
    }
    effort = str(params.get("reasoning_effort") or "").strip().lower()
    if effort in {"low", "medium", "high", "xhigh"}:
        kwargs["reasoning_effort"] = effort
    else:
        kwargs["reasoning_effort"] = "medium"
    return kwargs


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


def system_message(system_prompt: Optional[str] = None, settings: dict | None = None) -> None:
    global _chat, _chat_model, _chat_config, message_log, _system_prompt, _system_prompt_loaded, _system_prompt_mtime
    message_log = []

    from xai_sdk.chat import system as xai_system

    params = build_grok_params(settings)
    _system_prompt = system_prompt or Parameters().system_prompt
    _chat = _get_client().chat.create(
        **build_grok_chat_kwargs(params, [xai_system(_system_prompt)])
    )
    _chat_model = str(params["model"])
    _chat_config = (
        _chat_model,
        int(params.get("max_tokens") or 1024),
        float(params.get("temperature", 1.0)),
        str(params.get("reasoning_effort") or "").lower(),
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
    global _chat, _chat_model, _chat_config, message_log, _system_prompt, _system_prompt_loaded, _system_prompt_mtime
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
    _chat_model = ""
    _chat_config = ()


async def grok_response(user_text: str, settings: dict = None) -> GenerationResult:
    global _chat, _chat_model, _chat_config, message_log
    reload_system_message(force=False)
    params = build_grok_params(settings)
    from xai_sdk.chat import user as xai_user
    if _chat is None:
        try:
            system_message(_system_prompt or params.get("system_prompt"), params)
        except RuntimeError as e:
            Logger.error(str(e))
            return GenerationResult(provider="Grok", model=str(params.get("model") or ""))
    desired_config = (
        str(params.get("model") or "grok-4.5"),
        int(params.get("max_tokens") or 1024),
        float(params.get("temperature", 1.0)),
        str(params.get("reasoning_effort") or "").lower(),
    )
    if desired_config != _chat_config:
        try:
            system_message(_system_prompt or params.get("system_prompt"), params)
        except RuntimeError as e:
            Logger.error(str(e))
            return GenerationResult(provider="Grok", model=str(params.get("model") or ""))
    t0 = time.perf_counter()
    try:
        _chat.append(xai_user(user_text))
        response = await _chat.sample()
    except Exception as e:
        Logger.error(f"Error in Grok LLM Generation: {e}")
        print(traceback.format_exc())
        return GenerationResult(provider="Grok", model=str(params.get("model") or ""))
    t1 = time.perf_counter()
    text = str(response.content or "").strip()
    reasoning = str(getattr(response, "reasoning_content", "") or "").strip()
    Logger.notify(
        f"[GROK TTFC]: {(t1 - t0):.2f}s | "
        f"prompt_msgs={len(message_log)} | "
        f"characters={len(text)}"
    )
    Logger.quiet_print(text)
    _chat.append(response)
    message_log.append({"role": "user",      "content": user_text})
    message_log.append({"role": "assistant", "content": text})
    return GenerationResult(
        text=text,
        reasoning=reasoning,
        reasoning_kind="provider_summary" if reasoning else "none",
        provider="Grok",
        model=str(params.get("model") or ""),
    )
