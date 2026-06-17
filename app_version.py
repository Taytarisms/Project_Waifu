from typing import Optional
import base64
import re
from pathlib import Path
from files.system_setup.system_logger import Logger

MODERATION_DIR = Path(__file__).resolve().parent
BAD_WORDS_FILE = MODERATION_DIR / "bad_words_b64"

def decode_bad_words(path: Path | str = BAD_WORDS_FILE) -> list[str]:
    path = Path(path)

    if not path.exists():
        Logger.warn(f"Bad words file not found: {path}")
        return []
    try:
        encoded = "".join(path.read_text(encoding="utf-8").split())
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except Exception as e:
        Logger.warn(f"Failed to decode bad words file: {e}")
        return []

    return [
        line.strip().lower()
        for line in decoded.splitlines()
        if line.strip()
    ]

def check_for_bad_words(text: str, bad_words: list[str]) -> Optional[list[str]]:
    if not text:
        return None
    low_text = text.lower()
    matched_words = []
    for bad_word in bad_words:
        bw = re.escape(bad_word.strip().lower())
        if not bw:
            continue
        if re.search(rf"\b{bw}\b", low_text):
            matched_words.append(bad_word)
    return matched_words or None

def compile_bad_word_pattern(bad_words: list[str]):
    escaped = [re.escape(w.strip().lower()) for w in bad_words if w.strip()]
    if not escaped:
        return None
    pattern = r"\b(" + "|".join(escaped) + r")\b"
    return re.compile(pattern)