import base64
from pathlib import Path

MODERATION_DIR = Path(__file__).resolve().parent

plain_path = MODERATION_DIR / "bad_words.txt"
encoded_path = MODERATION_DIR / "bad_words_b64"

content = plain_path.read_text(encoding="utf-8")
encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")

encoded_path.write_text(encoded, encoding="utf-8")