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
SYSTEM_MESSAGE_FILE = Path(__file__).with_name("system_message_gemini.txt")

AVAILABLE_MODELS = [
    "gemini-3.1-pro",
    "gemini-3-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]


class Parameters:
    def __init__(self):
        self.model: str           = "gemini-2.5-flash"
        self.max_tokens: int      = 1024
        self.temperature: float   = 1.0
        self.top_p: float         = 1.0
        self.top_k: Optional[int] = None
        self.system: Optional[str]= None

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
    api_key = get_auth("google", "token") or os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "No Google API key found. Add your key in Settings → LLM → Gemini."
        )
    from google import genai  # lazy import
    _client = genai.Client(api_key=api_key)
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
        Logger.warn(f"Missing Gemini system prompt template key: {e}")
        return template


def load_system_message_from_file() -> str:
    try:
        if SYSTEM_MESSAGE_FILE.exists():
            return _format_system_template(
                SYSTEM_MESSAGE_FILE.read_text(encoding="utf-8").strip()
            )
    except Exception as e:
        Logger.warn(f"Failed to load Gemini system message: {e}")
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


async def gemini_response(user_text: str, settings: dict = None) -> str:
    global message_log, _system_prompt
    reload_system_message(force=False)
    params = settings or Parameters().to_dict()
    contents: List[Dict[str, Any]] = []
    for msg in message_log:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})
    contents.append({"role": "user", "parts": [{"text": user_text}]})
    watcher = get_watcher()
    injection = getattr(watcher, "last_injection", None)
    if injection and injection.vision_active:
        vision_parts = []
        for block in injection.content:
            if block.get("type") == "text":
                vision_parts.append({"text": block["text"]})
            elif "inline_data" in block:
                vision_parts.append(block)
        contents[-1] = {"role": "user", "parts": vision_parts}

    try:
        client = _get_client()
    except RuntimeError as e:
        Logger.error(str(e))
        return ""

    from google import genai
    from google.genai.errors import APIError

    gen_config = genai.types.GenerateContentConfig(
        max_output_tokens = params.get("max_tokens",  1024),
        temperature       = params.get("temperature", 1.0),
        top_p             = params.get("top_p",       1.0),
        **({"top_k": params["top_k"]} if params.get("top_k") else {}),
        **({"system_instruction": params["system"]} if params.get("system")
           else ({"system_instruction": _system_prompt} if _system_prompt else {})),
    )
    t0 = time.perf_counter()
    try:
        response = await client.aio.models.generate_content(
            model    = params.get("model", "gemini-2.5-flash"),
            contents = contents,
            config   = gen_config,
        )
    except APIError as e:
        Logger.error(f"Gemini API error: {e.message}")
        print(traceback.format_exc())
        return ""
    except Exception as e:
        Logger.error(f"Error in Gemini LLM Generation: {e}")
        print(traceback.format_exc())
        return ""
    t1    = time.perf_counter()
    text  = response.text
    usage = getattr(response, "usage_metadata", None)
    in_tok  = getattr(usage, "prompt_token_count",     "?") if usage else "?"
    out_tok = getattr(usage, "candidates_token_count", "?") if usage else "?"
    Logger.notify(
        f"[GEMINI TTFC]: {(t1 - t0):.2f}s | "
        f"prompt_msgs={len(message_log)} | "
        f"in={in_tok} | out={out_tok} | "
        f"characters={len(text)}"
    )
    Logger.quiet_print(text)
    message_log.append({"role": "user",      "content": user_text})
    message_log.append({"role": "assistant", "content": text})

    return text
