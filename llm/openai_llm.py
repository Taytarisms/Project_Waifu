from openai import AsyncOpenAI
from typing import Optional, List, Dict, Any
import traceback
import time
from pathlib import Path
from datetime import datetime

from files.system_setup.settings import get_auth, get_settings
from files.provider_capabilities import OPENAI_CHAT_MODELS
from files.system_setup.system_logger import Logger
from files.vision.VisionWatcher import get_watcher
from files.llm.generation_result import GenerationResult

client: Optional[AsyncOpenAI] = None
_client_token: Optional[str] = None
message_log: List[Dict[str, Any]] = []
SYSTEM_MESSAGE_FILE = Path(__file__).with_name("system_message_openai.txt")
_system_loaded = False
AVAILABLE_MODELS = OPENAI_CHAT_MODELS


def get_day_and_weekday():
    now = datetime.now()
    return now.strftime("%B %d, %Y"), now.strftime("%A")


def format_system_template(template: str) -> str:
    date_str, weekday_str = get_day_and_weekday()
    try:
        return template.format(date_str=date_str, weekday_str=weekday_str)
    except KeyError as e:
        Logger.warn(f"Missing system prompt template key: {e}")
        return template


def load_system_message_from_file() -> str:
    try:
        if SYSTEM_MESSAGE_FILE.exists():
            template = SYSTEM_MESSAGE_FILE.read_text(encoding="utf-8").strip()
            return format_system_template(template)
    except Exception as e:
        Logger.warn(f"Failed to load OpenAI system message: {e}")
    return ""


def ensure_system_message_loaded(force: bool = False) -> None:
    global _system_loaded, message_log
    if _system_loaded and not force:
        return
    system_text = load_system_message_from_file()
    if not system_text:
        _system_loaded = True
        return
    message_log = [m for m in message_log if m.get("role") != "system"]
    message_log.insert(0, {
        "role": "system",
        "content": system_text,
    })
    _system_loaded = True


def reload_system_message(force: bool = True) -> None:
    system_message(None)


def get_openai_client() -> AsyncOpenAI:
    global client, _client_token
    token = get_auth("openai", "token")
    if not token:
        raise RuntimeError(
            "OpenAI API key is missing. Add it in the OpenAI provider settings first."
        )
    if client is None or token != _client_token:
        client = AsyncOpenAI(api_key=token)
        _client_token = token
    return client


class Parameters:
    def __init__(self):
        self.model: str | None = None
        self.max_tokens: int = 300
        self.prompt: dict[str, str] | None = None
        self.messages: List[Dict[str, str]] = []
        self.temperature: float = 1.0
        self.top_p: float = 1.0
        self.frequency_penalty: float = 0.0
        self.presence_penalty: float = 0.0
        self.reasoning_effort: str = "medium"
        self.n: int = 1
        self.stream: bool = False
        self.stop = ["<eot>"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
            "reasoning_effort": self.reasoning_effort,
            "stop": self.stop,
        }


def build_openai_params(settings: dict | None = None) -> Dict[str, Any]:
    if settings:
        return settings
    stop_raw = get_settings("openai_stop") if get_settings else ""
    if isinstance(stop_raw, str):
        stop = [s.strip() for s in stop_raw.split(",") if s.strip()]
    elif isinstance(stop_raw, list):
        stop = stop_raw
    else:
        stop = []
    def saved(name, default):
        value = get_settings(name)
        return default if value in ("", None) else value
    return {
        "model": saved("openai_model", "gpt-5.6-luna"),
        "max_tokens": int(saved("openai_max_tokens", 512)),
        "temperature": float(saved("openai_temperature", 0.7)),
        "top_p": float(saved("openai_top_p", 1.0)),
        "frequency_penalty": float(saved("openai_frequency_penalty", 0.0)),
        "presence_penalty": float(saved("openai_presence_penalty", 0.0)),
        "stop": stop or None,
        "reasoning_effort": saved("openai_reasoning_effort", "medium"),
    }


def build_openai_request(params: dict, messages: list) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "model": params["model"],
        "input": messages,
        "max_output_tokens": int(params["max_tokens"]),
        "store": False,
        "timeout": 30.0,
    }
    model_name = str(params["model"]).lower()
    if model_name.startswith("gpt-5"):
        effort = str(params.get("reasoning_effort") or "medium").strip().lower()
        if effort not in {"none", "low", "medium", "high", "xhigh"}:
            effort = "medium"
        kwargs["reasoning"] = {"effort": effort, "summary": "auto"}
        kwargs["include"] = ["reasoning.encrypted_content"]
    else:
        kwargs.update({
            "temperature": float(params["temperature"]),
            "top_p": float(params["top_p"]),
        })
    return kwargs


def _responses_user_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    converted = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            converted.append({"type": "input_text", "text": block.get("text", "")})
        elif block.get("type") == "image_url":
            image = block.get("image_url") or {}
            url = image.get("url") if isinstance(image, dict) else image
            if url:
                converted.append({"type": "input_image", "image_url": url})
    return converted


def _openai_reasoning_summary(response: Any) -> str:
    parts: List[str] = []
    for item in getattr(response, "output", None) or []:
        if str(getattr(item, "type", "") or "") != "reasoning":
            continue
        for summary in getattr(item, "summary", None) or []:
            text = str(getattr(summary, "text", "") or "").strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts).strip()


def system_message(system_text: Optional[str] = None) -> None:
    global message_log, _system_loaded
    message_log = []
    if system_text:
        message_log.append({
            "role": "system",
            "content": system_text,
        })
        _system_loaded = True
    else:
        _system_loaded = False
        ensure_system_message_loaded(force=True)


async def open_response(user_text, settings: dict = None) -> GenerationResult:
    global message_log
    ensure_system_message_loaded()
    params = build_openai_params(settings)
    t0 = time.perf_counter()
    working_messages = list(message_log)
    watcher = get_watcher()
    injection = getattr(watcher, "last_injection", None)
    if injection and injection.vision_active:
        user_content = _responses_user_content(injection.content)
    else:
        user_content = user_text
    working_messages.append({
        "role": "user",
        "content": user_content,
    })
    try:
        openai_client = get_openai_client()

        kwargs = build_openai_request(params, working_messages)
        try:
            completion = await openai_client.responses.create(**kwargs)
        except Exception as first_error:
            error_text = str(first_error).lower()
            if "summary" not in error_text and "verification" not in error_text:
                raise
            fallback_kwargs = dict(kwargs)
            reasoning = dict(fallback_kwargs.get("reasoning") or {})
            reasoning.pop("summary", None)
            fallback_kwargs["reasoning"] = reasoning
            completion = await openai_client.responses.create(**fallback_kwargs)
        text = str(completion.output_text or "").strip()
        reasoning_summary = _openai_reasoning_summary(completion)
    except Exception as e:
        Logger.error(f"Error in LLM Generation: {e}")
        print(traceback.format_exc())
        return GenerationResult(
            text=f"Error in OpenAI generation: {e}",
            provider="OpenAI",
            model=str(params.get("model") or ""),
        )
    t1 = time.perf_counter()
    Logger.notify(
        f"[LLM TTFC]: {(t1 - t0):.2f}s | "
        f"prompt_msgs={len(working_messages)} | characters={len(text)}"
    )
    Logger.quiet_print(text)
    message_log = working_messages
    for item in getattr(completion, "output", None) or []:
        if hasattr(item, "model_dump"):
            message_log.append(item.model_dump(exclude_none=True))
    usage = getattr(completion, "usage", None)
    return GenerationResult(
        text=text,
        reasoning=reasoning_summary,
        reasoning_kind="provider_summary" if reasoning_summary else "none",
        provider="OpenAI",
        model=str(params.get("model") or ""),
        usage={
            "input_tokens": getattr(usage, "input_tokens", "?") if usage else "?",
            "output_tokens": getattr(usage, "output_tokens", "?") if usage else "?",
        },
    )
