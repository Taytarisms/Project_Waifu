import os
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from files.provider_capabilities import (
    CLAUDE_CHAT_MODELS,
    claude_supports_sampling,
)
from files.system_setup.settings import get_auth, get_settings
from files.system_setup.system_logger import Logger
from files.vision.VisionWatcher import get_watcher
from files.llm.generation_result import GenerationResult

_client = None
message_log: List[Dict[str, Any]] = []
_system_prompt: str = ""
_system_prompt_loaded = False
_system_prompt_mtime: float | None = None
SYSTEM_MESSAGE_FILE = Path(__file__).with_name("system_message_claude.txt")

AVAILABLE_MODELS = CLAUDE_CHAT_MODELS


class Parameters:
    def __init__(self):
        self.model: str              = "claude-sonnet-4-6"
        self.max_tokens: int         = 1024
        self.temperature: float      = 1.0
        self.top_p: float            = 1.0
        self.top_k: Optional[int]    = None
        self.thinking_effort: str    = "medium"
        self.system: Optional[str]   = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "model":       self.model,
            "max_tokens":  self.max_tokens,
            "thinking_effort": self.thinking_effort,
        }
        if claude_supports_sampling(self.model):
            d["temperature"] = self.temperature
            d["top_p"] = self.top_p
            if self.top_k is not None:
                d["top_k"] = self.top_k
        if self.system:
            d["system"] = self.system
        return d


def build_claude_params(settings: dict | None = None) -> Dict[str, Any]:
    if settings is not None:
        params = dict(settings)
    else:
        def saved(name, default):
            value = get_settings(name)
            return default if value in ("", None) else value
        params = {
            "model": saved("claude_model", "claude-sonnet-4-6"),
            "max_tokens": int(saved("claude_max_tokens", 1024)),
            "temperature": float(saved("claude_temperature", 1.0)),
            "top_p": float(saved("claude_top_p", 1.0)),
            "top_k": int(saved("claude_top_k", 0)),
            "thinking_effort": saved("claude_thinking_effort", "medium"),
        }
    params.setdefault("model", "claude-sonnet-4-6")
    params.setdefault("max_tokens", 1024)
    return params


def build_claude_request(params: dict, messages: list, system_prompt: str = "") -> Dict[str, Any]:
    model = str(params.get("model") or "claude-sonnet-4-6")
    request: Dict[str, Any] = {
        "model": model,
        "max_tokens": int(params.get("max_tokens") or 1024),
        "messages": messages,
    }
    if claude_supports_sampling(model):
        request["temperature"] = float(params.get("temperature", 1.0))
        request["top_p"] = float(params.get("top_p", 1.0))
        top_k = int(params.get("top_k") or 0)
        if top_k > 0:
            request["top_k"] = top_k
    explicit_system = params.get("system")
    if explicit_system or system_prompt:
        request["system"] = explicit_system or system_prompt
    name = model.lower()
    adaptive_thinking = name.startswith(
        (
            "claude-fable-5",
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-opus-4-8",
            "claude-opus-4-7",
            "claude-opus-4-6",
            "claude-sonnet-4-6",
        )
    )
    if adaptive_thinking:
        effort = str(params.get("thinking_effort") or "medium").strip().lower()
        if effort not in {"none", "low", "medium", "high", "max"}:
            effort = "medium"
        if effort == "none" and not name.startswith("claude-fable-5"):
            request["thinking"] = {"type": "disabled"}
        else:
            if effort == "none":
                effort = "low"
            request["thinking"] = {"type": "adaptive", "display": "summarized"}
            request["output_config"] = {"effort": effort}
    return request


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


async def claude_response(user_text: str, settings: dict = None) -> GenerationResult:
    global message_log, _system_prompt
    reload_system_message(force=False)
    params = build_claude_params(settings)
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
        return GenerationResult(provider="Claude", model=str(params.get("model") or ""))

    t0 = time.perf_counter()

    try:
        completion = await client.messages.create(
            **build_claude_request(params, message_log, _system_prompt)
        )
    except Exception as e:
        Logger.error(f"Error in Claude LLM Generation: {e}")
        print(traceback.format_exc())
        message_log.pop()
        return GenerationResult(provider="Claude", model=str(params.get("model") or ""))
    t1   = time.perf_counter()
    text_parts: List[str] = []
    thought_parts: List[str] = []
    for block in completion.content:
        block_type = str(getattr(block, "type", "") or "")
        if block_type == "text":
            value = str(getattr(block, "text", "") or "").strip()
            if value:
                text_parts.append(value)
        elif block_type == "thinking":
            value = str(getattr(block, "thinking", "") or "").strip()
            if value:
                thought_parts.append(value)
    text = "\n\n".join(text_parts).strip()
    reasoning = "\n\n".join(thought_parts).strip()
    Logger.notify(
        f"[CLAUDE TTFC]: {(t1 - t0):.2f}s | "
        f"prompt_msgs={len(message_log)} | "
        f"in={completion.usage.input_tokens} | out={completion.usage.output_tokens} | "
        f"characters={len(text)}"
    )
    Logger.quiet_print(text)
    assistant_content = []
    for block in completion.content:
        if hasattr(block, "model_dump"):
            assistant_content.append(block.model_dump(exclude_none=True))
    message_log.append({
        "role": "assistant",
        "content": assistant_content or [{"type": "text", "text": text}],
    })
    return GenerationResult(
        text=text,
        reasoning=reasoning,
        reasoning_kind="provider_summary" if reasoning else "none",
        provider="Claude",
        model=str(params.get("model") or ""),
        usage={
            "input_tokens": completion.usage.input_tokens,
            "output_tokens": completion.usage.output_tokens,
        },
    )
