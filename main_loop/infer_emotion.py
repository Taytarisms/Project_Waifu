import asyncio
from typing import Optional
from files.system_setup.settings import get_settings, get_auth
from files.system_setup.system_logger import Logger

DEFAULT_EMOTIONS = [
    "neutral",
    "happy",
    "sad",
    "angry",
    "confused",
    "surprised",
    "embarrassed",
    "love",
    "thinking",
    "sleepy",
]

def _build_inference_prompt(
    response_text: str,
    emotion_list: list[str],
    character_name: str = "Character",
) -> str:
    emotions_csv = ", ".join(emotion_list)
    cleaned_response = response_text.strip().replace("\n", " ")

    return (
        "TASK: Choose the best emotion label for the reply.\n"
        f"LABELS: {emotions_csv}\n"
        f"REPLY: {cleaned_response}\n\n"
        "OUTPUT RULES:\n"
        "- Output exactly one label from LABELS.\n"
        "- No sentence.\n"
        "- No explanation.\n"
        "- No reasoning.\n"
        "- No punctuation.\n\n"
        "LABEL:"
    )

def _heuristic_emotion_fallback(response_text: str, emotion_list: list[str]) -> str:
    text = (response_text or "").lower()
    valid = {e.lower() for e in emotion_list}

    checks = [
        ("happy", ["😊", "😄", "haha", "glad", "great", "awesome", "excited", "fun", "good"]),
        ("sad", ["sad", "sorry", "unfortunate", "lonely", "hurt", "upset"]),
        ("angry", ["angry", "mad", "annoyed", "frustrated", "irritated"]),
        ("surprised", ["wow", "whoa", "surprised", "shocked", "unexpected"]),
        ("confused", ["confused", "unsure", "not sure", "what do you mean", "huh"]),
        ("embarrassed", ["embarrassed", "blush", "awkward", "shy"]),
        ("love", ["love", "adorable", "sweet", "heart", "affection"]),
        ("thinking", ["think", "consider", "maybe", "let me see", "hmm"]),
        ("sleepy", ["tired", "sleepy", "yawn", "exhausted"]),
    ]

    for emotion, needles in checks:
        if emotion in valid and any(n in text for n in needles):
            return emotion

    return "neutral" if "neutral" in valid else ""

def _parse_emotion_output(raw: str, emotion_list: list[str]) -> str:
    if not raw or not raw.strip():
        return ""

    valid = [e.strip().lower() for e in emotion_list if e and e.strip()]
    text = raw.strip().lower()

    cleaned = "".join(
        c if c.isalpha() or c.isspace() else " "
        for c in text
    )

    words = [w.strip() for w in cleaned.split() if w.strip()]

    for word in words:
        if word in valid:
            return word

    for emotion in valid:
        if emotion in cleaned:
            return emotion

    for word in words:
        for emotion in valid:
            if len(word) >= 3 and (
                word.startswith(emotion) or emotion.startswith(word)
            ):
                return emotion

    return ""

async def infer_emotion(
    response_text: str,
    emotion_list: Optional[list[str]] = None,
    character_name: str = "Character",
    timeout_seconds: float = 6.0,
    mode: str = "llm",
) -> str:
    if not response_text or not response_text.strip():
        return ""

    mode = (mode or "llm").strip().lower()
    if mode not in ("llm", "heuristic", "off"):
        mode = "llm"

    if mode == "off":
        return ""

    if emotion_list is None:
        emotion_list = DEFAULT_EMOTIONS

    emotion_list = [
        str(e).strip().lower()
        for e in emotion_list
        if str(e).strip()
    ]

    if not emotion_list:
        emotion_list = DEFAULT_EMOTIONS

    heuristic = _heuristic_emotion_fallback(response_text, emotion_list)
    if mode == "heuristic":
        if heuristic:
            Logger.print(f"Heuristic-only mode: {heuristic}")
            return heuristic
        return ""

    if heuristic and heuristic != "neutral":
        Logger.print(f"Heuristic emotion: {heuristic}")
        return heuristic

    prompt = _build_inference_prompt(
        response_text=response_text,
        emotion_list=emotion_list,
        character_name=character_name,
    )

    try:
        raw = await asyncio.wait_for(
            _call_llm_for_inference(prompt),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        Logger.warn("LLM inference timed out.")
        return heuristic or ""
    except Exception as e:
        Logger.warn(f"LLM call failed: {e}")
        return heuristic or ""

    emotion = _parse_emotion_output(raw, emotion_list)

    if emotion:
        Logger.print(f"Inferred emotion: {emotion}")
        return emotion

    if heuristic:
        Logger.warn(
            f"LLM output was unusable: {raw!r}. "
            f"Using fallback emotion: {heuristic}"
        )
        return heuristic

    Logger.warn(
        f"Could not parse valid emotion from raw output: {raw!r}"
    )
    return ""

_novel_inference_client = None
async def _call_llm_for_inference(prompt: str) -> str:
    from files.llm.openai_llm import open_response
    from files.llm.LocalLLM import response_local
    from files.llm.grok import grok_response
    from files.llm.claude import claude_response
    from files.llm.gemini import gemini_response

    current_chat_model = get_settings("chat_model") or "OpenAI"
    if current_chat_model == "OpenAI":
        return await open_response(prompt)
    if current_chat_model == "Claude":
        return await claude_response(prompt)
    if current_chat_model == "Gemini":
        return await gemini_response(prompt)
    if current_chat_model == "Grok":
        return await grok_response(prompt)
    if current_chat_model == "NovelAI":
        return await _call_novelai_for_inference(prompt)
    if current_chat_model == "Local":
        return await response_local(
            text=prompt,
            max_tokens=24,
            save_to_history=False,
            temperature=0.0,
            top_k=1,
            top_p=1.0,
            min_p=0.0,
            repeat_penalty=1.2,
            force_new_context=True,
        )

    try:
        return await open_response(prompt)
    except Exception:
        Logger.warn(f"No suitable backend for '{current_chat_model}'")
        return ""

async def _call_novelai_for_inference(prompt: str) -> str:
    global _novel_inference_client
    from files.llm.boilerplate_novel import NovelAIClient
    api_token = get_auth("novelai", "token") or get_settings("NOVELAI_TOKEN")
    if not api_token:
        Logger.warn("NovelAI token not found. Cannot infer emotion.")
        return ""
    if _novel_inference_client is None:
        _novel_inference_client = NovelAIClient(api_token=api_token)
    parameters = {
        "max_output_length": 8,
        "min_length": 1,
        "temperature": 0.1,
        "top_p": 0.9,
        "top_k": 40,
        "frequency_penalty": 0,
        "presence_penalty": 0,
        "repetition_penalty": 2.0,
        "length_penalty": 0.0,
        "stop": ["\n"],
    }

    return await _novel_inference_client.generate_full(
        custom_prompt=prompt,
        parameters=parameters,
    )