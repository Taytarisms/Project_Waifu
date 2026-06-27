import asyncio
import json
from pathlib import Path

from files.moderation.text_cleaner import normalize_chat_string


PROJECT_ROOT = Path(__file__).resolve().parent
FILE_PATH = PROJECT_ROOT / "fine-tune-file.jsonl"
EXPORT_CHATS = True  # Temp Chat if set to False
FILTER_LLM_OUTPUT = True


def _write_row_sync(row: dict, file_path: Path) -> None:
    file_existed = file_path.exists()
    if not file_existed:
        print(f"Creating new file: {file_path}")
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


async def create_chat_pair(user: str, assistant: str) -> None:
    if not EXPORT_CHATS:
        return

    user_chat = normalize_chat_string(user)
    assistant_chat = normalize_chat_string(assistant)
    if not user_chat or not assistant_chat:
        return

    row = {
        "messages": [
            {"role": "user", "content": user_chat},
            {"role": "assistant", "content": assistant_chat},
        ]
    }
    await asyncio.to_thread(_write_row_sync, row, FILE_PATH)
