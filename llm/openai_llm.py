from openai import AsyncOpenAI
from typing import Optional, List, Dict, Any
import traceback
import time
from pathlib import Path
from datetime import datetime

from files.system_setup.settings import get_auth, get_settings
from files.system_setup.system_logger import Logger
from files.vision.VisionWatcher import get_watcher

client: Optional[AsyncOpenAI] = None
_client_token: Optional[str] = None
message_log: List[Dict[str, Any]] = []
SYSTEM_MESSAGE_FILE = Path(__file__).with_name("system_message_openai.txt")
_system_loaded = False


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
    return {
        "model": get_settings("openai_model") or "gpt-4.1",
        "max_tokens": int(get_settings("openai_max_tokens") or 512),
        "temperature": float(get_settings("openai_temperature") or 0.7),
        "top_p": float(get_settings("openai_top_p") or 1.0),
        "frequency_penalty": float(get_settings("openai_frequency_penalty") or 0.0),
        "presence_penalty": float(get_settings("openai_presence_penalty") or 0.0),
        "stop": stop or None,
    }


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


async def open_response(user_text, settings: dict = None):
    global message_log
    ensure_system_message_loaded()
    params = build_openai_params(settings)
    t0 = time.perf_counter()
    working_messages = list(message_log)
    watcher = get_watcher()
    injection = getattr(watcher, "last_injection", None)
    if injection and injection.vision_active:
        user_content = injection.content
    else:
        user_content = user_text
    working_messages.append({
        "role": "user",
        "content": user_content,
    })
    try:
        openai_client = get_openai_client()

        kwargs = {
            "model": params["model"],
            "messages": working_messages,
            "timeout": 30.0,
        }
        model_name = str(params["model"]).lower()
        is_gpt5_family = model_name.startswith("gpt-5")
        if is_gpt5_family:
            kwargs["max_completion_tokens"] = params["max_tokens"]
        else:
            kwargs.update({
                "temperature": params["temperature"],
                "max_tokens": params["max_tokens"],
                "top_p": params["top_p"],
                "presence_penalty": params["presence_penalty"],
                "frequency_penalty": params["frequency_penalty"],
            })
        if params.get("stop"):
            kwargs["stop"] = params["stop"]
        completion = await openai_client.chat.completions.create(**kwargs)
        text = completion.choices[0].message.content or ""
    except Exception as e:
        Logger.error(f"Error in LLM Generation: {e}")
        print(traceback.format_exc())
        return f"Error in OpenAI generation: {e}"
    t1 = time.perf_counter()
    Logger.notify(
        f"[LLM TTFC]: {(t1 - t0):.2f}s | "
        f"prompt_msgs={len(working_messages)} | characters={len(text)}"
    )
    Logger.quiet_print(text)
    message_log = working_messages
    message_log.append({
        "role": "assistant",
        "content": text,
    })
    return text
