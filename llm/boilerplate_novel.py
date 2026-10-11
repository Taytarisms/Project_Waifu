import asyncio
import json
import re
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Union
import httpx
from files.llm.novelai_runtime import coordinated, runtime, status_code

try:
    from aiohttp import ClientSession
except Exception:
    ClientSession = None

from novelai_api.Preset import Model, Preset
from novelai_api.Tokenizer import Tokenizer
from novelai_api.utils import b64_to_tokens, tokens_to_b64

try:
    from novelai_api import NovelAIAPI
    from novelai_api.BanList import BanList
    from novelai_api.BiasGroup import BiasGroup
    from novelai_api.GlobalSettings import GlobalSettings
except Exception:
    NovelAIAPI = None
    BanList = None
    BiasGroup = None
    GlobalSettings = None

from files.system_setup.system_logger import Logger

try:
    from files.moderation.text_cleaner import (
        separate_sentences,
        process_final_text,
        remove_novelai_special_symbols,
    )
except Exception:
    def separate_sentences(text: str) -> str:
        return text

    def process_final_text(text: str) -> str:
        return text.strip()

    def remove_novelai_special_symbols(text: str) -> str:
        return text

PROJECT_ROOT  = Path(__file__).resolve().parent.parent
CHARACTER_DIR = PROJECT_ROOT / "characters"
DEFAULT_CHARACTER = "Abby"

NAI_OA_MODELS = {"xialong-v1", "glm-4-6"}
NAI_LEGACY_MODELS = {"kayra", "erato", "clio"}

HistoryEntry = Dict[str, str]

_TEMPLATE_LEAK_PATTERNS = [
    r"\[ ?Style: ?chat ?\]",
    r"\[ ?Style: ?[a-z]+ ?\]",
    r"\n\*\*\*\s*$",
    r"^\*\*\*\s*",
    r"\nRules:\s*\n?[-•].*",
    r"\nPersonality:\s*\n",
    r"\nPrompt:\s*",
    r"\nInstruction:\s*",
]

# Cause Novel does this cute thing where it prints the template sometimes so we gotta block that out!
# Also, if you need it, the API docs are here: https://docs.novelai.net/en/scripting/api-reference
# Source API GH: https://github.com/Aedial/novelai-api

_PROMPT_LEAK_CUT_PATTERNS = [
    r"\[ ?user\b",
    r"\[ ?assistant\b",
    r"\bCurrent Message\s*:",
    r"\bRelevant Memories\s*:",
    r"\bContext notes\s*:",
    r"\bUse these only when relevant\b",
    r"\bDo not mention them unless they apply\b",
    r"\bSystem\s*:",
    r"\bDeveloper\s*:",
]

_PROMPT_LEAK_RE = re.compile(
    "|".join(_PROMPT_LEAK_CUT_PATTERNS),
    re.IGNORECASE | re.DOTALL,
)

def _strip_template_artifacts(text: str) -> str:
    if not text:
        return ""

    for pattern in _TEMPLATE_LEAK_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            text = text[:match.start()]

    return text.rstrip()

def looks_like_prompt_leak(text: str) -> bool:
    lowered = (text or "").lower()
    needles = [
        "[user",
        "[assistant",
        "current message:",
        "relevant memories:",
        "context notes:",
        "use these only when relevant",
        "do not mention them unless they apply",
    ]
    return any(n in lowered for n in needles)

def sanitize_novelai_reply(text: str, *, cut_newline: bool = True) -> str:
    if not text:
        return ""
    text = _strip_template_artifacts(str(text).lstrip())
    match = _PROMPT_LEAK_RE.search(text)
    if match:
        text = text[:match.start()]
    if cut_newline and "\n" in text:
        text = text.split("\n", 1)[0]

    text = remove_novelai_special_symbols(text)
    text = process_final_text(text.strip())
    return text.strip()

def extract_latest_user_message(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""

    m = re.search(r"Current Message\s*:\s*(.*)", raw, re.IGNORECASE | re.DOTALL)
    if m:
        raw = m.group(1).strip()

    for pattern in [
        r"\nRelevant Memories\s*:",
        r"\nContext notes\s*:",
        r"\nUse these only when relevant",
        r"\nDo not mention them unless they apply",
    ]:
        hit = re.search(pattern, raw, re.IGNORECASE)
        if hit:
            raw = raw[:hit.start()].strip()

    raw = re.sub(r"\[/?\s*(user|assistant|system|developer)\s*\]?", "", raw, flags=re.IGNORECASE)
    return raw.strip()


def clean_history_content(text: str) -> str:
    if not text:
        return ""

    text = extract_latest_user_message(text)
    text = sanitize_novelai_reply(text, cut_newline=False)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def _safe_character_name(name: str | None = None) -> str:
    name = (name or "").strip()
    if not name or name.lower() == "none":
        return DEFAULT_CHARACTER
    return name.removesuffix(".json")

def load_character(name: str | None = DEFAULT_CHARACTER) -> dict:
    name = _safe_character_name(name)
    path = CHARACTER_DIR / f"{name}.json"

    if not path.exists():
        Logger.warn(f"Character file not found: {path}")
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        Logger.error(f"Failed to load character {name}: {e}")
        return {}

def load_char_name(character_key: str | None = DEFAULT_CHARACTER) -> str:
    character_key = _safe_character_name(character_key)
    character = load_character(character_key)
    return character.get("char_name") or character.get("name") or character_key or "Assistant"


def legacy_model(parameters):
    key = str(parameters.get("legacy_model") or "kayra").lower()
    models = {"kayra": Model.Kayra, "clio": Model.Clio, "erato": Model.Erato}
    if key not in models:
        raise ValueError(f"Unsupported NovelAI completion model: {key}")
    return models[key]


def dialogue_name(value):
    return re.sub(r"[\r\n:]", " ", str(value or "Assistant")).strip()


def unlabel(text, name):
    text = str(text or "").strip()
    prefix = name + ":"
    return text[len(prefix):].lstrip() if text.casefold().startswith(prefix.casefold()) else text

def normalize_character_card(char: dict) -> dict:
    return {
        "name": char.get("char_name") or char.get("name") or "Assistant",
        "persona": char.get("char_persona") or char.get("description") or "",
        "personality": char.get("personality") or "",
        "example_dialogue": char.get("example_dialogue") or char.get("mes_example") or "",
        "greeting": char.get("greeting") or "",
    }

def build_novelai_completion_prompt(
    user_text: str,
    character: dict,
    history: Optional[List[HistoryEntry]] = None,
    user_name: str = "User",
    preserve_context: bool = False,
) -> str:
    char = normalize_character_card(character)
    name = dialogue_name(char["name"])
    user_name = dialogue_name(user_name)
    persona = (char["persona"] or "").strip()
    personality = (char["personality"] or "").strip()
    examples = (char["example_dialogue"] or "").strip()

    lines: list[str] = []

    if persona:
        lines.append(persona)

    if personality:
        lines.append("")
        lines.append("Personality:")
        lines.append(personality)

    lines.append("")
    lines.append("Rules:")
    lines.append(f"- You are {name}.")
    lines.append(f"- Reply only as {name}.")
    lines.append(f"- Do not write dialogue for {user_name}.")
    lines.append("- Do not write system notes, labels, memories, or hidden context.")
    lines.append("- Write one natural reply.")

    if examples:
        lines.append("")
        lines.append("***")
        lines.append("[ Style: chat ]")
        lines.append("")
        examples = examples.replace("{{char}}", name).replace("{{user}}", user_name)
        examples = re.sub(r"(?m)^User:", lambda _: user_name + ":", examples)
        lines.append(examples)
        lines.append("***")

    for entry in (history or []):
        role = entry.get("role", "")
        content = str(entry.get("content") or "").strip()
        if not content:
            continue

        if role in ("user", "assistant"):
            historical_name = dialogue_name(entry.get("speaker") or (user_name if role == "user" else name))
            lines.append(f"{historical_name}: {unlabel(content, historical_name)}")

    clean_user_text = user_text if preserve_context else extract_latest_user_message(user_text)
    lines.append(f"{user_name}: {unlabel(clean_user_text, user_name)}")
    lines.append(f"{name}:")

    return "\n".join(lines).strip()


def format_kayra_instruction(
    user_text: str,
    character_name: str = DEFAULT_CHARACTER,
    history: Optional[List[HistoryEntry]] = None,
    user_name: str = "User",
    preserve_context: bool = False,
) -> str:
    character = load_character(character_name)
    return build_novelai_completion_prompt(
        user_text=user_text,
        character=character,
        history=history,
        user_name=user_name,
        preserve_context=preserve_context,
    )

def _get_param(parameters: Dict[str, Any], key: str, default: Any) -> Any:
    value = parameters.get(key, default)
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _legacy_stop_sequences(model: Model, character: Optional[str] = None) -> list[list[int]]:
    stops = [
        "\nUser:",
        " User:",
        "\nAssistant:",
        " Assistant:",
        "\n[user",
        " [user",
        "\n[assistant",
        " [assistant",
        "\nCurrent Message:",
        " Current Message:",
        "\nRelevant Memories:",
        " Relevant Memories:",
        "\nContext notes:",
        " Context notes:",
        "\nUse these only when relevant",
        " Use these only when relevant",
        "\nDo not mention them unless they apply",
        " Do not mention them unless they apply",
        "\n***",
        "[ Style:",
        "\nRules:",
        "\nPersonality:",
    ]

    if character:
        stops.extend([
            f"\n{character}:",
            f" {character}:",
        ])

    return [Tokenizer.encode(model, s) for s in stops]


def _structural_banlist():
    if BanList is None:
        return None

    return BanList(
        "[user",
        " [user",
        "\n[user",
        "[assistant",
        " [assistant",
        "\n[assistant",
        "Current Message:",
        " Current Message:",
        "\nCurrent Message:",
        "Relevant Memories:",
        " Relevant Memories:",
        "\nRelevant Memories:",
        "Context notes:",
        " Context notes:",
        "\nContext notes:",
        "Use these only when relevant",
        "Do not mention them unless they apply",
        "Prompt:",
        "Instruction:",
    )


def _strict_structural_banlist():
    if BanList is None:
        return None
    return BanList(
        ":", " :", "::", " ::",
        "*:", ";", " ;", ";;", " ;;",
        "#", " #", "|", " |",
        "{", " {", "}", " }",
        "[", " [", "]", " ]",
        "\\", " \\", "/", " /",
        "*", " *", "~", " ~",
        "www", " www", "http", " http", "https", " https",
        ".com", ".org", ".net",
    )

class NovelAIClient:
    def __init__(self, api_token: str, tier: str = "Tablet"):
        self.api_token = api_token
        self.tier = tier
        self._cached_tier: Optional[str] = None
        self._history: List[HistoryEntry] = []

    def add_to_history(self, role: str, content: str) -> None:
        if role not in ("user", "assistant"):
            return

        content = clean_history_content(content)
        if not content:
            return

        if looks_like_prompt_leak(content):
            Logger.warn(f"NovelAI history rejected leaked content: {content!r}")
            return

        self._history.append({"role": role, "content": content})

    def get_history(self) -> List[HistoryEntry]:
        return list(self._history)

    def clear_history(self) -> None:
        self._history = []
        Logger.quiet_print("Conversation history cleared.")

    def trim_history(self, max_turns: int = 20) -> None:
        max_entries = max_turns * 2
        if len(self._history) > max_entries:
            self._history = self._history[-max_entries:]

    def save_history(self, path: str | Path) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._history, f, indent=2, ensure_ascii=False)
            Logger.quiet_print(f"History saved to {path}")
        except Exception as e:
            Logger.error(f"Failed to save history: {e}")

    def load_history(self, path: str | Path) -> None:
        path = Path(path)

        if not path.exists():
            Logger.warn(f"History file not found: {path}")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                Logger.warn("History file has unexpected format; ignoring.")
                return
            cleaned: list[HistoryEntry] = []
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                role = entry.get("role")
                content = clean_history_content(entry.get("content") or "")
                if role in ("user", "assistant") and content:
                    cleaned.append({"role": role, "content": content})
            self._history = cleaned
            Logger.quiet_print(f"Loaded {len(self._history)} history entries from {path}")
        except Exception as e:
            Logger.error(f"Failed to load history: {e}")

    def get_auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    def get_available_models(self) -> list:
        return [m.value for m in Model]

    def get_presets_for_model(self, model_name: str) -> list:
        model = Model(model_name)
        try:
            return [p.name for p in Preset.get_presets(model)]
        except AttributeError:
            Logger.warn("Preset.get_presets() not available; returning empty list.")
            return []

    def get_preset_parameters(self, model_name: str, preset_name: str) -> dict:
        model = Model(model_name)
        preset = Preset.from_official(model, preset_name)
        return preset.to_settings()

    def get_parameter_types(self) -> dict:
        return Preset._TYPE_MAPPING

    async def fetch_subscription(self) -> dict:
        url = "https://api.novelai.net/user/subscription"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, headers=self.get_auth_headers())
        return response.json() if response.status_code == 200 else {}

    async def detect_tier(self) -> str:
        if self._cached_tier is not None:
            return self._cached_tier
        sub = await self.fetch_subscription()
        raw = json.dumps(sub).lower()
        if "opus" in raw:
            tier = "Opus"
        elif "scroll" in raw:
            tier = "Scroll"
        elif "tablet" in raw:
            tier = "Tablet"
        else:
            Logger.warn("Could not detect tier from subscription response.")
            tier = "Unknown"

        self._cached_tier = tier
        return tier

    async def can_use_xialong(self) -> bool:
        return await self.detect_tier() == "Opus"

    @coordinated
    async def generate_full(
        self,
        custom_prompt: Union[str, List[int]],
        parameters: Dict[str, Any],
        *,
        model_name: str = "kayra",
        system_text: Optional[str] = None,
        character_name: str = DEFAULT_CHARACTER,
        user_name: str = "User",
        max_history_turns: int = 20,
        save_history: bool = True,
        route: Optional[str] = None,
        preserve_context: bool = False,
        history_override: Optional[List[HistoryEntry]] = None,
        memory_context: str = "",
    ) -> str:
        self.trim_history(max_history_turns)

        model_key = (model_name or "kayra").lower()
        parameters = dict(parameters, legacy_model=model_key)
        display_name = load_char_name(character_name)
        user_text = str(custom_prompt) if preserve_context else extract_latest_user_message(str(custom_prompt))
        history = history_override if history_override is not None else self.get_history()
        if memory_context:
            system_text = "\n\n".join(filter(None, [system_text, memory_context]))

        if model_key in NAI_OA_MODELS:
            reply = await self.generate_openai_compatible(
                user_text=user_text,
                parameters=parameters,
                model_name=model_key,
                system_text=system_text,
                history=history,
                preserve_context=preserve_context,
            )

        elif model_key in NAI_LEGACY_MODELS:
            parameters["speaker_names"] = list({user_name, display_name} | {
                str(entry["speaker"]) for entry in history if entry.get("speaker")})
            prompt = format_kayra_instruction(
                user_text,
                character_name,
                history=history,
                user_name=user_name,
                preserve_context=preserve_context,
            )
            reply = await self.generate_prompt_completion(
                (memory_context + "\n\n" + prompt) if memory_context else prompt,
                parameters,
                character=display_name,
                route=route,
            )

        else:
            raise ValueError(f"Unsupported NovelAI model: {model_name}")
        reply = sanitize_novelai_reply(reply)
        if save_history and reply and not looks_like_prompt_leak(reply):
            self.add_to_history("user", user_text)
            self.add_to_history("assistant", reply)
        return reply

    @coordinated
    async def generate_prompt_completion(
        self,
        prompt: Union[str, List[int]],
        parameters: Dict[str, Any],
        *,
        character: Optional[str] = None,
        route: Optional[str] = None,
    ) -> str:
        selected_route = (route or "high").strip().lower()
        if selected_route == "low":
            return await self.generate_legacy_low(prompt, parameters, character=character)
        try:
            return await self.generate_legacy_high(prompt, parameters, character=character)
        except Exception as e:
            # HTTP rejections are not evidence that the high-level route is
            # incompatible. Repeating a 429/401/5xx through another route is futile.
            if status_code(e) is not None:
                raise
            if not (isinstance(e, RuntimeError) and "high-level route is unavailable" in str(e)):
                raise
            Logger.warn(f"NovelAI high-level route failed; falling back to low route: {e}")
            return await self.generate_legacy_low(prompt, parameters, character=character)

    async def generate_legacy_full(
        self,
        custom_prompt: Union[str, List[int]],
        parameters: Dict[str, Any],
        character: Optional[str] = None,
        history: Optional[List[HistoryEntry]] = None,
    ) -> str:
        return await self.generate_prompt_completion(
            custom_prompt,
            parameters,
            character=character,
        )

    @coordinated
    async def generate_legacy_high(
        self,
        custom_prompt: Union[str, List[int]],
        parameters: Dict[str, Any],
        *,
        character: Optional[str] = None,
    ) -> str:
        if NovelAIAPI is None or ClientSession is None or GlobalSettings is None:
            raise RuntimeError("novelai_api high-level route is unavailable.")

        model = legacy_model(parameters)
        preset_name = {Model.Kayra: "Carefree", Model.Clio: "Fresh Coffee", Model.Erato: "Dragonfruit"}[model]
        preset = Preset.from_official(model, preset_name)
        if preset is None:
            raise ValueError(f"NovelAI preset {preset_name} unavailable for {model.value}")

        preset.max_length = int(_get_param(parameters, "max_output_length", 150))
        preset.min_length = int(_get_param(parameters, "min_length", 1))
        preset.repetition_penalty = float(_get_param(parameters, "repetition_penalty", 1.35))
        preset.temperature = float(_get_param(parameters, "temperature", 0.8))
        preset.length_penalty = float(_get_param(parameters, "length_penalty", 1.0))
        preset.stop_sequences = _legacy_stop_sequences(model, character)
        for speaker in parameters.get("speaker_names", []):
            preset.stop_sequences.append(Tokenizer.encode(model, "\n" + dialogue_name(speaker) + ":"))
        if parameters.get("utility_summary"):
            preset.stop_sequences += [Tokenizer.encode(model, "\n\n")]
        if parameters.get("summary_single_line"):
            preset.stop_sequences += [Tokenizer.encode(model, marker) for marker in ("\n", "{")]
        global_settings = GlobalSettings(num_logprobs=GlobalSettings.NO_LOGPROBS)
        global_settings.bias_dinkus_asterism = False
        global_settings.generate_until_sentence = True
        global_settings.ban_ambiguous_genji_tokens = True

        bias_groups: list = []
        module = "vanilla"

        prompt_tokens = (
            Tokenizer.encode(model, custom_prompt)
            if isinstance(custom_prompt, str)
            else custom_prompt
        )
        ban_mode = str(parameters.get("banlist_mode") or "soft").lower()
        bad_words = _strict_structural_banlist() if ban_mode == "strict" else _structural_banlist()
        novel_api = NovelAIAPI(logger=None)
        session = runtime().session(self.api_token, self.get_auth_headers())
        novel_api.attach_session(session)
        gen = await novel_api.high_level.generate(
            prompt_tokens, model, preset, global_settings, bad_words, bias_groups, module,
        )

        decoded = Tokenizer.decode(model, b64_to_tokens(gen["output"], 4 if model is Model.Erato else 2))
        if parameters.get("summary_single_line"):
            return decoded.strip()  # Validate raw output before dialogue cleanup can hide it.
        return sanitize_novelai_reply(decoded, cut_newline=not parameters.get("utility_summary", False))

    @coordinated
    async def generate_legacy_low(
        self,
        custom_prompt: Union[str, List[int]],
        parameters: Dict[str, Any],
        *,
        character: Optional[str] = None,
    ) -> str:
        model = legacy_model(parameters)
        max_length = int(_get_param(parameters, "max_output_length", 150))
        temperature = float(_get_param(parameters, "temperature", 0.8))
        repetition_penalty = float(_get_param(parameters, "repetition_penalty", 1.35))
        top_k = int(_get_param(parameters, "top_k", 40))
        top_p = float(_get_param(parameters, "top_p", 0.9))
        stop_sequences = _legacy_stop_sequences(model, character)
        for speaker in parameters.get("speaker_names", []):
            stop_sequences.append(Tokenizer.encode(model, "\n" + dialogue_name(speaker) + ":"))
        params = {
            "max_length": max_length,
            "min_length": 1,
            "repetition_penalty": repetition_penalty,
            "temperature": temperature,
            "top_k": top_k,
            "top_p": top_p,
            "cfg_scale": 1,
            "generate_until_sentence": True,
            "stop_sequences": stop_sequences,
        }
        if parameters.get("utility_summary"):
            params["length_penalty"] = float(_get_param(parameters, "length_penalty", 0))
            params["stop_sequences"] += [Tokenizer.encode(model, "\n\n")]
        if parameters.get("summary_single_line"):
            params["stop_sequences"] += [Tokenizer.encode(model, marker) for marker in ("\n", "{")]

        prompt_tokens = (
            Tokenizer.encode(model, custom_prompt)
            if isinstance(custom_prompt, str)
            else custom_prompt
        )

        prompt_b64 = tokens_to_b64(prompt_tokens, 4 if model is Model.Erato else 2)
        data = {"input": prompt_b64, "model": model.value, "parameters": params}
        url = "https://text.novelai.net/ai/generate"
        response = await runtime().http_client().post(url, headers=self.get_auth_headers(), json=data)
        if response.status_code != 200:
            response.raise_for_status()
        content = response.json()
        token_list = b64_to_tokens(content["output"], 4 if model is Model.Erato else 2)
        decoded = Tokenizer.decode(model, token_list)
        if parameters.get("summary_single_line"):
            return decoded.strip()
        if character:
            decoded = decoded.split(f"\n{character}:", 1)[0]
            decoded = decoded.split(f" {character}:", 1)[0]
        decoded = decoded.split("\nUser:", 1)[0]
        decoded = decoded.split(" User:", 1)[0]
        return sanitize_novelai_reply(decoded, cut_newline=not parameters.get("utility_summary", False))

    @coordinated
    async def generate_openai_compatible(
        self,
        user_text: str,
        parameters: Dict[str, Any],
        *,
        model_name: str = "xialong-v1",
        system_text: Optional[str] = None,
        history: Optional[List[HistoryEntry]] = None,
        preserve_context: bool = False,
    ) -> str:
        messages: list[dict] = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        for entry in (history or []):
            role = entry.get("role", "")
            content = clean_history_content(entry.get("content") or "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_text if preserve_context else extract_latest_user_message(user_text)})
        payload = {
            "model": model_name,
            "messages": messages,
            "max_tokens": int(_get_param(parameters, "max_output_length", 300)),
            "temperature": float(_get_param(parameters, "temperature", 0.8)),
            "top_p": float(_get_param(parameters, "top_p", 0.9)),
        }
        url = "https://text.novelai.net/oa/v1/chat/completions"
        response = await runtime().http_client().post(url, headers=self.get_auth_headers(), json=payload)
        if response.status_code not in (200, 201):
            response.raise_for_status()
        return sanitize_novelai_reply(response.json()["choices"][0]["message"]["content"])

async def fetch_user_info(token: str) -> dict:
    url = "https://api.novelai.net/user/information"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=headers)
    if response.status_code != 200:
        Logger.error(f"User info fetch failed {response.status_code}: {response.text}")
        return {}
    return response.json()

def extract_username(user_info: dict) -> str:
    return (
        user_info.get("username")
        or user_info.get("name")
        or user_info.get("user", {}).get("username")
        or "Unknown"
    )

async def main() -> None:
    token = input("NovelAI API token: ").strip()
    if not token:
        print("NovelAI API token is required.")
        return

    client = NovelAIClient(api_token=token)
    print(f"Tier: {await client.detect_tier()}")

    parameters = {
        "max_output_length": 150,
        "temperature": 0.9,
        "top_p": 0.9,
        "top_k": 40,
        "repetition_penalty": 1.35,
        "length_penalty": 1.0,
        "banlist_mode": "soft",
    }

    while True:
        user_input = input("You: ").strip()

        if not user_input:
            continue

        reply = await client.generate_full(
            user_input,
            parameters,
            model_name="kayra",
            character_name=DEFAULT_CHARACTER,
        )
        print(f"AI: {reply}\n")

if __name__ == "__main__":
    asyncio.run(main())
