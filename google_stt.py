from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable
import zipfile

from files.memory import chromadb as memory_backend


W_AI_FU_SOURCE = "w-ai-fu"
GLOBAL_USER_ID = "__global__"


@dataclass
class WAIImportPreview:
    source_path: str
    config_path: str
    contextual_count: int
    character_count: int
    sample: list[str]


@dataclass
class WAIImportResult:
    source_path: str
    config_path: str
    found: int
    imported: int
    skipped: int
    failed: int


def _read_json_text(text: str, source: str) -> dict[str, Any]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{source} did not contain a JSON object.")
    return data


def _read_zip_text(zip_path: Path, entry_name: str) -> str:
    with zipfile.ZipFile(zip_path) as archive:
        with archive.open(entry_name) as handle:
            return handle.read().decode("utf-8-sig")


def _find_zip_entry(zip_path: Path, suffix: str) -> str | None:
    suffix = suffix.replace("\\", "/").lower().strip("/")
    with zipfile.ZipFile(zip_path) as archive:
        candidates = [
            info.filename
            for info in archive.infolist()
            if info.filename.replace("\\", "/").lower().endswith(suffix)
        ]
    if not candidates:
        return None
    candidates.sort(key=lambda name: (len(name), name.lower()))
    return candidates[0]


def _zip_character_entries(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as archive:
        entries = [
            info.filename
            for info in archive.infolist()
            if info.filename.replace("\\", "/").lower().endswith(".json")
            and "/userdata/characters/" in info.filename.replace("\\", "/").lower()
        ]
    return sorted(entries, key=str.lower)


def _find_dir_config(path: Path) -> Path | None:
    if path.is_file() and path.name.lower() == "config.json":
        return path

    candidates = [
        path / "userdata" / "config" / "config.json",
        path / "Hilda AI" / "userdata" / "config" / "config.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    try:
        found = sorted(
            path.rglob("config.json"),
            key=lambda item: (0 if "userdata" in str(item).lower() else 1, len(str(item))),
        )
    except OSError:
        return None
    return found[0] if found else None


def _dir_character_paths(path: Path) -> list[Path]:
    roots = [
        path / "userdata" / "characters",
    ]
    output: list[Path] = []
    for root in roots:
        if root.is_dir():
            output.extend(sorted(root.glob("*.json"), key=lambda item: item.name.lower()))
    return output


def _load_w_ai_fu_config(path: Path) -> tuple[dict[str, Any], str, list[str]]:
    path = Path(path)
    if path.is_file() and path.suffix.lower() == ".zip":
        entry = _find_zip_entry(path, "userdata/config/config.json")
        if entry is None:
            raise FileNotFoundError("No userdata/config/config.json was found in this w-AI-fu zip.")
        return _read_json_text(_read_zip_text(path, entry), entry), entry, _zip_character_entries(path)

    config_path = _find_dir_config(path)
    if config_path is None:
        raise FileNotFoundError("No userdata/config/config.json was found in this w-AI-fu folder.")
    return (
        _read_json_text(config_path.read_text(encoding="utf-8-sig"), str(config_path)),
        str(config_path),
        [str(item) for item in _dir_character_paths(path)],
    )


def _value_container(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, dict) and "value" in current:
        return current.get("value")
    return current


def _safe_keywords(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output = []
    for item in value:
        text = str(item or "").strip()
        if text:
            output.append(text)
    return output


def _extract_contextual_memories(config: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _value_container(config, "memory", "contextual_memories")
    if not isinstance(raw, list):
        return []

    memories = []
    seen = set()
    for item in raw:
        if isinstance(item, str):
            content = item.strip()
            keywords: list[str] = []
        elif isinstance(item, dict):
            content = str(item.get("content") or item.get("text") or "").strip()
            keywords = _safe_keywords(item.get("keywords"))
        else:
            continue

        if not content:
            continue
        normalized = " ".join(content.lower().split())
        if normalized in seen:
            continue
        seen.add(normalized)
        memories.append({"content": content, "keywords": keywords})
    return memories


def _keyword_tags(keywords: Iterable[str], limit: int = 8) -> list[str]:
    tags = ["w-ai-fu", "contextual"]
    for keyword in keywords:
        cleaned = "".join(
            char.lower()
            for char in keyword.strip().replace(" ", "-")
            if char.isalnum() or char in ("-", "_")
        )
        if cleaned and cleaned not in tags:
            tags.append(cleaned[:32])
        if len(tags) >= limit:
            break
    return tags


def preview_import(path: str | Path) -> WAIImportPreview:
    source = Path(path)
    config, config_path, character_paths = _load_w_ai_fu_config(source)
    memories = _extract_contextual_memories(config)
    return WAIImportPreview(
        source_path=str(source),
        config_path=config_path,
        contextual_count=len(memories),
        character_count=len(character_paths),
        sample=[item["content"] for item in memories[:5]],
    )


def import_contextual_memories(path: str | Path) -> WAIImportResult:
    source = Path(path)
    config, config_path, _character_paths = _load_w_ai_fu_config(source)
    memories = _extract_contextual_memories(config)

    imported = 0
    skipped = 0
    failed = 0
    for item in memories:
        try:
            ok = memory_backend.add_memory(
                text=item["content"],
                user_id=GLOBAL_USER_ID,
                speaker=memory_backend.AI_SPEAKER,
                scope=memory_backend.SCOPE_GLOBAL,
                memory_type="context",
                source=W_AI_FU_SOURCE,
                tags=_keyword_tags(item.get("keywords") or []),
            )
        except Exception:
            failed += 1
            continue
        if ok:
            imported += 1
        else:
            skipped += 1

    return WAIImportResult(
        source_path=str(source),
        config_path=config_path,
        found=len(memories),
        imported=imported,
        skipped=skipped,
        failed=failed,
    )
