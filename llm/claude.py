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
CLAUDE_DEFAULT_MAX_TOKENS = 4096
CLAUDE_EMPTY_RETRY_MAX_TOKENS = 8192


class Parameters:
    def __init__(self):
        self.model: str              = "claude-sonnet-4-6"
        self.max_tokens: int         = CLAUDE_DEFAULT_MAX_TOKENS
        self.temperature: float      = 1.0
        self.top_p: float            = 1.0
        self.top_k: Optional[int]    = None
        self.thinking_mode: str      = "adaptive"
        self.thinking_effort: str    = "medium"
        self.system: Optional[str]   = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "model":       self.model,
            "max_tokens":  self.max_tokens,
            "thinking_mode": self.thinking_mode,
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
            "max_tokens": int(saved("claude_max_tokens", CLAUDE_DEFAULT_MAX_TOKENS)),
            "temperature": float(saved("claude_temperature", 1.0)),
            "top_p": float(saved("claude_top_p", 1.0)),
            "top_k": int(saved("claude_top_k", 0)),
            "thinking_mode": saved("claude_thinking_mode", "adaptive"),
            "thinking_effort": saved("claude_thinking_effort", "medium"),
        }
    params.setdefault("model", "claude-sonnet-4-6")
    params.setdefault("max_tokens", CLAUDE_DEFAULT_MAX_TOKENS)
    return params


def build_claude_request(params: dict, messages: list, system_prompt: str = "") -> Dict[str, Any]:
    model = str(params.get("model") or "claude-sonnet-4-6")
    request: Dict[str, Any] = {
        "model": model,
        "max_tokens": int(params.get("max_tokens") or CLAUDE_DEFAULT_MAX_TOKENS),
        "messages": messages,
    }
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
    thinking_enabled = False
    if adaptive_thinking:
        mode = str(params.get("thinking_mode") or "adaptive").strip().lower()
        effort = str(params.get("thinking_effort") or "medium").strip().lower()
        if effort not in {"none", "off", "disabled", "low", "medium", "high", "xhigh", "max"}:
            effort = "medium"
        disabled = mode in {"none", "off", "disabled"} or effort in {
            "none",
            "off",
            "disabled",
        }
        if disabled and not name.startswith("claude-fable-5"):
            request["thinking"] = {"type": "disabled"}
        else:
            if effort in {"none", "off", "disabled"}:
                effort = "low"
            if effort == "xhigh" and not name.startswith(
                (
                    "claude-fable-5",
                    "claude-opus-5",
                    "claude-sonnet-5",
                    "claude-opus-4-8",
                    "claude-opus-4-7",
                )
            ):
                effort = "max"
            request["thinking"] = {"type": "adaptive", "display": "summarized"}
            request["output_config"] = {"effort": effort}
            thinking_enabled = True

    if claude_supports_sampling(model) and not thinking_enabled:
        temperature = float(params.get("temperature", 1.0))
        top_p = float(params.get("top_p", 1.0))
        top_k = int(params.get("top_k") or 0)
        if temperature != 1.0:
            request["temperature"] = temperature
        elif top_p != 1.0:
            request["top_p"] = top_p
        if top_k > 0:
            request["top_k"] = top_k
    return request


def _completion_parts(completion) -> tuple[str, str, list[dict], list[str]]:
    text_parts: List[str] = []
    thought_parts: List[str] = []
    assistant_content: list[dict] = []
    block_types: list[str] = []

    for block in getattr(completion, "content", []) or []:
        if isinstance(block, dict):
            block_type = str(block.get("type", "") or "")
            value_for = block.get
            dumped = dict(block)
        else:
            block_type = str(getattr(block, "type", "") or "")
            value_for = lambda name, default="": getattr(block, name, default)
            dumped = (
                block.model_dump(exclude_none=True)
                if hasattr(block, "model_dump")
                else None
            )

        block_types.append(block_type or "unknown")
        if dumped:
            assistant_content.append(dumped)
        if block_type == "text":
            value = str(value_for("text", "") or "").strip()
            if value:
                text_parts.append(value)
        elif block_type == "thinking":
            value = str(value_for("thinking", "") or "").strip()
            if value:
                thought_parts.append(value)

    return (
        "\n\n".join(text_parts).strip(),
        "\n\n".join(thought_parts).strip(),
        assistant_content,
        block_types,
    )


def _usage_tokens(completion) -> tuple[int, int]:
    usage = getattr(completion, "usage", None)
    return (
        int(getattr(usage, "input_tokens", 0) or 0),
        int(getattr(usage, "output_tokens", 0) or 0),
    )


def _empty_response_retry(
    params: dict,
    messages: list,
    stop_reason: str,
) -> tuple[dict, list, str] | None:
    if stop_reason == "max_tokens":
        current = int(params.get("max_tokens") or CLAUDE_DEFAULT_MAX_TOKENS)
        increased = min(
            max(CLAUDE_DEFAULT_MAX_TOKENS, current * 2),
            CLAUDE_EMPTY_RETRY_MAX_TOKENS,
        )
        if increased <= current:
            return None
        retry_params = dict(params)
        retry_params["max_tokens"] = increased
        return retry_params, list(messages), f"max_tokens increased to {increased}"

    if stop_reason == "end_turn":
        retry_messages = list(messages)
        retry_messages.append({
            "role": "user",
            "content": [{
                "type": "text",
                "text": "Please continue and provide the answer to the previous request.",
            }],
        })
        return dict(params), retry_messages, "continuation requested"

    return None


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

    history_length = len(message_log)
    message_log.append({
        "role":    "user",
        "content": user_content,
    })

    try:
        client = _get_client()
    except RuntimeError as e:
        Logger.error(str(e))
        del message_log[history_length:]
        return GenerationResult(provider="Claude", model=str(params.get("model") or ""))

    t0 = time.perf_counter()
    total_input_tokens = 0
    total_output_tokens = 0

    try:
        completion = await client.messages.create(
            **build_claude_request(params, message_log, _system_prompt)
        )
        input_tokens, output_tokens = _usage_tokens(completion)
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        text, reasoning, assistant_content, block_types = _completion_parts(completion)
        stop_reason = str(getattr(completion, "stop_reason", "") or "unknown")

        if not text:
            retry = _empty_response_retry(params, message_log, stop_reason)
            if retry is not None:
                retry_params, retry_messages, retry_detail = retry
                Logger.warn(
                    "Claude returned no text "
                    f"(stop_reason={stop_reason}, blocks={block_types or ['none']}); "
                    f"retrying once with {retry_detail}."
                )
                completion = await client.messages.create(
                    **build_claude_request(retry_params, retry_messages, _system_prompt)
                )
                input_tokens, output_tokens = _usage_tokens(completion)
                total_input_tokens += input_tokens
                total_output_tokens += output_tokens
                text, reasoning, assistant_content, block_types = _completion_parts(completion)
                stop_reason = str(
                    getattr(completion, "stop_reason", "") or "unknown"
                )

        if not text:
            raise RuntimeError(
                "Claude returned no text after request handling "
                f"(stop_reason={stop_reason}, blocks={block_types or ['none']}, "
                f"output_tokens={total_output_tokens})."
            )
    except Exception as e:
        Logger.error(f"Error in Claude LLM Generation: {e}")
        print(traceback.format_exc())
        del message_log[history_length:]
        return GenerationResult(provider="Claude", model=str(params.get("model") or ""))
    t1 = time.perf_counter()
    Logger.notify(
        f"[CLAUDE TTFC]: {(t1 - t0):.2f}s | "
        f"prompt_msgs={len(message_log)} | "
        f"in={total_input_tokens} | out={total_output_tokens} | "
        f"stop={stop_reason} | "
        f"characters={len(text)}"
    )
    Logger.quiet_print(text)
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
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
        },
    )
