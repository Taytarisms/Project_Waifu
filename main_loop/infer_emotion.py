import asyncio
import json
from typing import Optional
from files.system_setup.system_logger import Logger
from files.llm.generation_result import result_text

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
    return json.dumps({"labels": emotion_list, "character": character_name,
                       "reply": response_text.strip()}, ensure_ascii=False)


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

    valid = {e.strip().lower() for e in emotion_list if e and e.strip()}
    label = raw.strip().lower().strip('"\'[] .!\n\r')
    return label if label in valid else ""


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
        Logger.warn(f"VTS classification timed out; heuristic fallback: {heuristic or 'none'}")
        return heuristic or ""
    except Exception as e:
        Logger.warn(f"VTS classification failed: {e}; heuristic fallback: {heuristic or 'none'}")
        return heuristic or ""

    raw = result_text(raw)
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

async def _call_llm_for_inference(prompt: str) -> str:
    from files.llm.emotion_request import classify_text
    return await classify_text(prompt)
