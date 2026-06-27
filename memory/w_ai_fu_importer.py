from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import pickle
import re
from typing import Any, Iterable
import zipfile

from files.memory import chromadb as memory_backend

W_AI_FU_SOURCE = "w-ai-fu"
GLOBAL_USER_ID = "__global__"
VECTORDB_MEMORY_TYPE = "w_ai_fu_long_term"
VECTORDB_DATE_PREFIX_RE = re.compile(r"^\[[^\]\r\n]{1,32}\]\s*")
MIN_VECTORDB_TS = 1_600_000_000_000
MAX_VECTORDB_TS = 1_900_000_000_000

@dataclass
class WAIImportPreview:
    source_path: str
    config_path: str
    database_path: str
    contextual_count: int
    vectordb_count: int
    character_count: int
    sample: list[str]

@dataclass
class WAIImportResult:
    source_path: str
    config_path: str
    database_path: str
    found: int
    contextual_found: int
    vectordb_found: int
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

def _read_zip_bytes(zip_path: Path, entry_name: str) -> bytes:
    with zipfile.ZipFile(zip_path) as archive:
        with archive.open(entry_name) as handle:
            return handle.read()

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

def _zip_database_entries(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as archive:
        entries = []
        for info in archive.infolist():
            name = info.filename.replace("\\", "/").lower()
            if not name.endswith(".txt") and "database.txt.backup_" not in name:
                continue
            if name == "database.txt" or name.startswith("database.txt.backup_"):
                entries.append(info.filename)
            elif "/vectordb/database.txt" in name or name.endswith("vectordb/database.txt"):
                entries.append(info.filename)
            elif "/vectordb/database.txt.backup_" in name or name.endswith("vectordb/database.txt.backup_"):
                entries.append(info.filename)

    def sort_key(name: str) -> tuple[int, str]:
        lowered = name.replace("\\", "/").lower()
        is_backup = ".backup_" in lowered
        return (1 if is_backup else 0, lowered)

    return sorted(set(entries), key=sort_key)

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

def _dir_database_paths(path: Path) -> list[Path]:
    if path.is_file():
        name = path.name.lower()
        if name == "database.txt" or name.startswith("database.txt.backup_"):
            return [path]
        return []

    preferred = [
        path / "source" / "app" / "vectordb" / "database.txt",
        path / "Hilda AI" / "source" / "app" / "vectordb" / "database.txt",
        path / "vectordb" / "database.txt",
    ]
    found: list[Path] = []
    for candidate in preferred:
        if candidate.is_file() and candidate not in found:
            found.append(candidate)

    try:
        scanned = [
            item
            for item in path.rglob("database.txt*")
            if item.is_file()
            and item.name.lower().startswith("database.txt")
            and "vectordb" in {part.lower() for part in item.parts}
        ]
    except OSError:
        scanned = []

    def sort_key(item: Path) -> tuple[int, int, str]:
        is_backup = ".backup_" in item.name.lower()
        is_preferred = item in found
        return (0 if is_preferred else 1, 1 if is_backup else 0, str(item).lower())

    for item in sorted(scanned, key=sort_key):
        if item not in found:
            found.append(item)
    return found

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

def _try_load_w_ai_fu_config(path: Path) -> tuple[dict[str, Any], str, list[str]]:
    try:
        return _load_w_ai_fu_config(path)
    except FileNotFoundError:
        if path.is_file() and path.suffix.lower() == ".zip":
            return {}, "", _zip_character_entries(path)
        return {}, "", [str(item) for item in _dir_character_paths(path)]

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

def _vector_tags(source_label: str) -> list[str]:
    tags = ["w-ai-fu", "vectordb", "long-term"]
    if ".backup_" in source_label.lower():
        tags.append("backup")
    return tags

def _scan_pickle_unicode(raw: bytes) -> list[tuple[int, str]]:
    output: list[tuple[int, str]] = []
    i = 0
    while i < len(raw) - 2:
        opcode = raw[i]
        if opcode == 0x8C:
            size = raw[i + 1]
            start = i + 2
            end = start + size
        elif opcode == 0x58:
            if i + 5 > len(raw):
                i += 1
                continue
            size = int.from_bytes(raw[i + 1:i + 5], "little")
            start = i + 5
            end = start + size
        elif opcode == 0x8D:
            if i + 9 > len(raw):
                i += 1
                continue
            size = int.from_bytes(raw[i + 1:i + 9], "little")
            start = i + 9
            end = start + size
        else:
            i += 1
            continue

        if 0 < size < 250_000 and end <= len(raw):
            try:
                text = raw[start:end].decode("utf-8")
            except UnicodeDecodeError:
                i += 1
                continue
            if text.startswith("[") and "/20" in text and len(text) > 8:
                output.append((i, text))
        i += 1
    return output

def _scan_pickle_timestamps(raw: bytes) -> list[tuple[int, int]]:
    output: list[tuple[int, int]] = []
    i = 0
    while i < len(raw) - 2:
        opcode = raw[i]
        if opcode == 0x8A:
            size = raw[i + 1]
            start = i + 2
            end = start + size
            if 4 <= size <= 8 and end <= len(raw):
                value = int.from_bytes(raw[start:end], "little", signed=False)
                if MIN_VECTORDB_TS <= value <= MAX_VECTORDB_TS:
                    output.append((i, value))
        elif opcode == 0x8B:  # LONG4
            if i + 5 <= len(raw):
                size = int.from_bytes(raw[i + 1:i + 5], "little")
                start = i + 5
                end = start + size
                if 4 <= size <= 8 and end <= len(raw):
                    value = int.from_bytes(raw[start:end], "little", signed=False)
                    if MIN_VECTORDB_TS <= value <= MAX_VECTORDB_TS:
                        output.append((i, value))
        i += 1
    return output

def _pair_chunks_timestamps(
    chunks: list[tuple[int, str]],
    timestamps: list[tuple[int, int]],
) -> list[tuple[str, int | None]]:
    paired: list[tuple[str, int | None]] = []
    if chunks and timestamps and timestamps[0][0] > chunks[-1][0]:
        for index, (_chunk_offset, chunk_text) in enumerate(chunks):
            timestamp = timestamps[index][1] if index < len(timestamps) else None
            paired.append((chunk_text, timestamp))
        return paired

    ts_idx = 0
    for chunk_offset, chunk_text in chunks:
        while ts_idx < len(timestamps) - 1 and timestamps[ts_idx][0] < chunk_offset:
            ts_idx += 1
        timestamp = timestamps[ts_idx][1] if ts_idx < len(timestamps) else None
        paired.append((chunk_text, timestamp))
    return paired

def _clean_vectordb_text(text: str) -> str:
    cleaned = VECTORDB_DATE_PREFIX_RE.sub("", str(text or "").strip()).strip()
    return " ".join(cleaned.split())

def _timestamp_to_iso(timestamp_ms: int | None) -> str | None:
    if not timestamp_ms:
        return None
    if timestamp_ms < MIN_VECTORDB_TS or timestamp_ms > MAX_VECTORDB_TS:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat()

def _stitch_window_chunks(chunks: list[str]) -> str:
    if not chunks:
        return ""
    words = chunks[0].split()
    for chunk in chunks[1:]:
        nxt = chunk.split()
        if not nxt:
            continue
        max_k = min(len(words), len(nxt))
        overlap = 0
        for k in range(max_k, 0, -1):
            if words[-k:] == nxt[:k]:
                overlap = k
                break
        words.extend(nxt[overlap:])
    return " ".join(words)

def _pull_existing_records(db: dict[str, Any], source_label: str) -> list[dict[str, Any]]:
    memory = db.get("memory")
    metadata = db.get("metadata")
    if not isinstance(memory, list) or not isinstance(metadata, list):
        raise ValueError("unexpected vectordb structure")

    groups: dict[Any, list[str]] = {}
    order: list[Any] = []
    for entry in memory:
        if not isinstance(entry, dict):
            continue
        chunk = entry.get("chunk")
        if not isinstance(chunk, str):
            continue
        idx = entry.get("metadata_index")
        if idx not in groups:
            groups[idx] = []
            order.append(idx)
        groups[idx].append(chunk)

    records: list[dict[str, Any]] = []
    seen = set()
    for idx in order:
        content = _clean_vectordb_text(_stitch_window_chunks(groups[idx]))
        if not content or content.lower() == "empty":
            continue
        normalized = " ".join(content.lower().split())
        if normalized in seen:
            continue
        seen.add(normalized)

        timestamp_ms = None
        if isinstance(idx, int) and 0 <= idx < len(metadata):
            raw_ts = metadata[idx]
            if isinstance(raw_ts, (int, float)):
                timestamp_ms = int(raw_ts)

        records.append({
            "content": content,
            "timestamp_ms": timestamp_ms,
            "created_at": _timestamp_to_iso(timestamp_ms),
            "source_label": source_label,
        })
    return records


def _records_from_scan(raw: bytes, source_label: str) -> list[dict[str, Any]]:
    chunks = _scan_pickle_unicode(raw)
    timestamps = _scan_pickle_timestamps(raw)
    records: list[dict[str, Any]] = []
    seen = set()
    for raw_text, timestamp_ms in _pair_chunks_timestamps(chunks, timestamps):
        content = _clean_vectordb_text(raw_text)
        if not content or content.lower() == "empty":
            continue
        normalized = " ".join(content.lower().split())
        if normalized in seen:
            continue
        seen.add(normalized)
        records.append({
            "content": content,
            "timestamp_ms": timestamp_ms,
            "created_at": _timestamp_to_iso(timestamp_ms),
            "source_label": source_label,
        })
    return records


def _extract_db(raw: bytes, source_label: str) -> list[dict[str, Any]]:
    try:
        loaded = pickle.loads(raw)
    except Exception:
        loaded = None

    if loaded is not None:
        db = loaded[0] if isinstance(loaded, list) and loaded else loaded
        if isinstance(db, dict) and "memory" in db and "metadata" in db:
            try:
                return _pull_existing_records(db, source_label)
            except Exception:
                pass
    return _records_from_scan(raw, source_label)

def _load_db_memories(path: Path) -> tuple[list[dict[str, Any]], str]:
    database_labels: list[str] = []
    memories: list[dict[str, Any]] = []
    seen = set()

    if path.is_file() and path.suffix.lower() == ".zip":
        for entry in _zip_database_entries(path):
            database_labels.append(entry)
            records = _extract_db(_read_zip_bytes(path, entry), entry)
            for record in records:
                normalized = " ".join(record["content"].lower().split())
                if normalized in seen:
                    continue
                seen.add(normalized)
                memories.append(record)
    else:
        for db_path in _dir_database_paths(path):
            database_labels.append(str(db_path))
            try:
                raw = db_path.read_bytes()
            except OSError:
                continue
            records = _extract_db(raw, str(db_path))
            for record in records:
                normalized = " ".join(record["content"].lower().split())
                if normalized in seen:
                    continue
                seen.add(normalized)
                memories.append(record)

    return memories, "; ".join(database_labels)

def preview_import(path: str | Path) -> WAIImportPreview:
    source = Path(path)
    config, config_path, character_paths = _try_load_w_ai_fu_config(source)
    contextual_memories = _extract_contextual_memories(config)
    vectordb_memories, database_path = _load_db_memories(source)
    if not config_path and not database_path:
        raise FileNotFoundError(
            "No w-AI-fu config.json or source/app/vectordb/database.txt was found."
        )
    return WAIImportPreview(
        source_path=str(source),
        config_path=config_path,
        database_path=database_path,
        contextual_count=len(contextual_memories),
        vectordb_count=len(vectordb_memories),
        character_count=len(character_paths),
        sample=[
            item["content"]
            for item in (contextual_memories[:3] + vectordb_memories[:3])
        ],
    )

def import_contextual_memories(path: str | Path) -> WAIImportResult:
    source = Path(path)
    config, config_path, _character_paths = _try_load_w_ai_fu_config(source)
    contextual_memories = _extract_contextual_memories(config)
    vectordb_memories, database_path = _load_db_memories(source)
    if not config_path and not database_path:
        raise FileNotFoundError(
            "No w-AI-fu config.json or source/app/vectordb/database.txt was found."
        )

    imported = 0
    skipped = 0
    failed = 0
    for item in contextual_memories:
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

    for item in vectordb_memories:
        try:
            ok = memory_backend.add_memory(
                text=item["content"],
                user_id=GLOBAL_USER_ID,
                speaker=memory_backend.AI_SPEAKER,
                scope=memory_backend.SCOPE_GLOBAL,
                memory_type=VECTORDB_MEMORY_TYPE,
                source=W_AI_FU_SOURCE,
                tags=_vector_tags(item.get("source_label") or ""),
                created_at=item.get("created_at"),
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
        database_path=database_path,
        found=len(contextual_memories) + len(vectordb_memories),
        contextual_found=len(contextual_memories),
        vectordb_found=len(vectordb_memories),
        imported=imported,
        skipped=skipped,
        failed=failed,
    )

import_memories = import_contextual_memories