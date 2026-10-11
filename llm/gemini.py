import os
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from files.provider_capabilities import (
    GEMINI_CHAT_MODELS,
    gemini_supports_sampling,
    normalize_gemini_model,
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
SYSTEM_MESSAGE_FILE = Path(__file__).with_name("system_message_gemini.txt")

AVAILABLE_MODELS = GEMINI_CHAT_MODELS


class Parameters:
    def __init__(self):
        self.model: str           = "gemini-2.5-flash"
        self.max_tokens: int      = 4096
        self.temperature: float   = 1.0
        self.top_p: float         = 1.0
        self.top_k: Optional[int] = None
        self.thinking_level: str  = "medium"
        self.system: Optional[str]= None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "model":       self.model,
            "max_tokens":  self.max_tokens,
            "thinking_level": self.thinking_level,
        }
        if gemini_supports_sampling(self.model):
            d["temperature"] = self.temperature
            d["top_p"] = self.top_p
            if self.top_k is not None:
                d["top_k"] = self.top_k
        if self.system:
            d["system"] = self.system
        return d


def build_gemini_params(settings: dict | None = None) -> Dict[str, Any]:
    if settings is not None:
        params = dict(settings)
    else:
        def saved(name, default):
            value = get_settings(name)
            return default if value in ("", None) else value
        params = {
            "model": saved("gemini_model", "gemini-2.5-flash"),
            "max_tokens": int(saved("gemini_max_tokens", 4096)),
            "temperature": float(saved("gemini_temperature", 1.0)),
            "top_p": float(saved("gemini_top_p", 1.0)),
            "top_k": int(saved("gemini_top_k", 0)),
            "thinking_level": saved("gemini_thinking_level", "medium"),
        }
    params.setdefault("model", "gemini-2.5-flash")
    params.setdefault("max_tokens", 4096)
    params["model"] = normalize_gemini_model(params["model"])
    return params


def build_gemini_config(params: dict, system_prompt: str = "") -> Dict[str, Any]:
    model = str(params.get("model") or "gemini-2.5-flash")
    config: Dict[str, Any] = {
        "max_output_tokens": int(params.get("max_tokens") or 4096),
    }
    if gemini_supports_sampling(model):
        config["temperature"] = float(params.get("temperature", 1.0))
        config["top_p"] = float(params.get("top_p", 1.0))
        top_k = int(params.get("top_k") or 0)
        if top_k > 0:
            config["top_k"] = top_k
    thinking_config: Dict[str, Any] = {"include_thoughts": True}
    if model.lower().startswith("gemini-3"):
        level = str(params.get("thinking_level") or "medium").strip().lower()
        if level not in {"minimal", "low", "medium", "high"}:
            level = "medium"
        if level == "minimal" and model.lower().startswith(
            ("gemini-3.7-", "gemini-3.1-pro")
        ):
            level = "low"
        thinking_config["thinking_level"] = level
    config["thinking_config"] = thinking_config
    explicit_system = params.get("system")
    if explicit_system or system_prompt:
        config["system_instruction"] = explicit_system or system_prompt
    return config


def _get_client():
    global _client
    if _client is not None:
        return _client
    api_key = get_auth("google", "token") or os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "No Google API key found. Add your key in Settings → LLM → Gemini."
        )
    from google import genai  # lazy import cause lazy
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


async def gemini_response(user_text: str, settings: dict = None, on_event=None) -> GenerationResult:
    global message_log, _system_prompt
    reload_system_message(force=False)
    params = build_gemini_params(settings)
    contents: List[Any] = list(message_log)
    current_user_content: Dict[str, Any] = {
        "role": "user",
        "parts": [{"text": user_text}],
    }
    contents.append(current_user_content)
    watcher = get_watcher()
    injection = getattr(watcher, "last_injection", None)
    if injection and injection.vision_active:
        vision_parts = []
        for block in injection.content:
            if block.get("type") == "text":
                vision_parts.append({"text": block["text"]})
            elif "inline_data" in block:
                vision_parts.append(block)
        current_user_content = {"role": "user", "parts": vision_parts}
        contents[-1] = current_user_content

    try:
        client = _get_client()
    except RuntimeError as e:
        Logger.error(str(e))
        return GenerationResult(provider="Gemini", model=str(params.get("model") or ""))

    from google import genai
    from google.genai.errors import APIError

    gen_config = genai.types.GenerateContentConfig(
        **build_gemini_config(params, _system_prompt)
    )
    t0 = time.perf_counter()
    if on_event is not None:
        from files.llm.streaming import GenerationStream, collect_gemini_stream
        text, reasoning, content, usage = await collect_gemini_stream(
            client, params["model"], contents, gen_config, GenerationStream(on_event)
        )
        # Commit history only after a complete successful stream.
        message_log.extend([current_user_content, content])
        return GenerationResult(
            text=text, reasoning=reasoning,
            reasoning_kind="provider_summary" if reasoning else "none",
            provider="Gemini", model=params["model"],
            usage={"input_tokens": getattr(usage, "prompt_token_count", None),
                   "output_tokens": getattr(usage, "candidates_token_count", None)},
        )
    try:
        response = await client.aio.models.generate_content(
            model    = params.get("model", "gemini-2.5-flash"),
            contents = contents,
            config   = gen_config,
        )
    except APIError as e:
        Logger.error(f"Gemini API error: {e.message}")
        print(traceback.format_exc())
        return GenerationResult(provider="Gemini", model=str(params.get("model") or ""))
    except Exception as e:
        Logger.error(f"Error in Gemini LLM Generation: {e}")
        print(traceback.format_exc())
        return GenerationResult(provider="Gemini", model=str(params.get("model") or ""))
    t1    = time.perf_counter()
    answer_parts: List[str] = []
    thought_parts: List[str] = []
    candidate_content = None
    if getattr(response, "candidates", None):
        candidate_content = getattr(response.candidates[0], "content", None)
    for part in getattr(candidate_content, "parts", None) or []:
        part_text = str(getattr(part, "text", "") or "").strip()
        if not part_text:
            continue
        if bool(getattr(part, "thought", False)):
            thought_parts.append(part_text)
        else:
            answer_parts.append(part_text)
    text = "\n\n".join(answer_parts).strip() or str(response.text or "").strip()
    reasoning = "\n\n".join(thought_parts).strip()
    usage = getattr(response, "usage_metadata", None)
    in_tok  = getattr(usage, "prompt_token_count",     "?") if usage else "?"
    out_tok = getattr(usage, "candidates_token_count", "?") if usage else "?"
    Logger.notify(
        f"[GEMINI complete]: {(t1 - t0):.2f}s | "
        f"prompt_msgs={len(message_log)} | "
        f"in={in_tok} | out={out_tok} | "
        f"characters={len(text)}"
    )
    Logger.quiet_print(text)
    message_log.append(current_user_content)
    if candidate_content is not None:
        # Keep Google's thought signatures intact for the next turn.
        message_log.append(candidate_content)
    else:
        message_log.append({"role": "model", "parts": [{"text": text}]})

    return GenerationResult(
        text=text,
        reasoning=reasoning,
        reasoning_kind="provider_summary" if reasoning else "none",
        provider="Gemini",
        model=str(params.get("model") or ""),
        usage={"input_tokens": in_tok, "output_tokens": out_tok},
    )
