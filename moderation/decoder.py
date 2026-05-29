import base64
from pathlib import Path

MODERATION_DIR = Path(__file__).resolve().parent

encoded_path = MODERATION_DIR / "bad_words_b64"
plain_path = MODERATION_DIR / "bad_words.txt"

encoded = "".join(encoded_path.read_text(encoding="utf-8").split())
decoded = base64.b64decode(encoded, validate=True).decode("utf-8")

plain_path.write_text(decoded, encoding="utf-8")