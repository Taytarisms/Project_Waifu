import uuid
import asyncio
import random
from functools import partial
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pathlib import Path

import chromadb
from files.system_setup.system_logger import Logger
from files.moderation.text_cleaner import normalize_text
PROJECT_ROOT = Path(__file__).resolve().parent
chroma_client = chromadb.PersistentClient(path=f"{PROJECT_ROOT}/chroma_data")
collection = chroma_client.get_or_create_collection(name="memories")
AI_SPEAKER = "assistant"
USER_SPEAKER = "user"
SCOPE_GLOBAL = "global"
SCOPE_CHATTER = "chatter"


class MemoryOperationError(RuntimeError):
    """Raised when a memory mutation could not be completed.
        Better to raise an error than do nothing and break the
        proccesses in place."""

def dedupe_memory(
    text: str,
    user_id: str,
    speaker: str,
    distance_threshold: float = 0.15
) -> bool:
    try:
        where_filter = {"$and": [
            {"user_id": user_id},
            {"speaker": speaker}
        ]}

        results = collection.query(
            query_texts=[text],
            n_results=1,
            where=where_filter
        )

        docs = results.get("documents", [[]])
        dists = results.get("distances", [[]])
        if not docs or not docs[0]:
            return False

        closest_doc = docs[0][0]
        closest_dist = dists[0][0] if dists and dists[0] else None
        if closest_doc and normalize_text(closest_doc) == normalize_text(text):
            return True
        if closest_dist is not None and closest_dist <= distance_threshold:
            return True
        return False
    except Exception as e:
        Logger.error(f"Memory deduplication failed: {e}")
        return False

def add_memory(text: str, user_id: str, speaker: str, scope: str = SCOPE_CHATTER, memory_type: str = "chat", source: str = "chat", tags: Optional[List[str]] = None, created_at: Optional[str] = None) -> bool:
    if not text or not text.strip():
        return False
    if dedupe_memory(text, user_id=user_id, speaker=speaker):
        Logger.warn("Duplicate memory found, skipping.")
        return False
    try:
        metadata: Dict[str, Any] = {
            "user_id": user_id,
            "speaker": speaker,
            "scope": scope,
            "memory_type": memory_type,
            "source": source,
            "created_at": created_at or datetime.now(timezone.utc).isoformat()}

        if tags:
            metadata["tags"] = ",".join(t.strip().lower() for t in tags if t.strip())
        collection.add(
            ids=[str(uuid.uuid4())],
            documents=[text],
            metadatas=[metadata]
        )
        Logger.quiet_print("Memory stored.")
        return True
    except Exception as e:
        Logger.error(f"Memory storage failed: {e}")
        return False

def _build_where_filter(conditions: List[Dict[str, str]]) -> Optional[Dict]:
    filters = [{k: v} for c in conditions for k, v in c.items()]
    if not filters:
        return None
    if len(filters) == 1:
        return filters[0]
    return {"$and": filters}

def recall_memories(
    query: str,
    user_id: Optional[str] = None,
    speaker: Optional[str] = None,
    scope: Optional[str] = None,
    memory_type: Optional[str] = None,
    source: Optional[str] = None,
    n_results: int = 5,
    max_distance: float = 1.0,
    after: Optional[str] = None,
    before: Optional[str] = None,
    tag: Optional[str] = None
) -> List[Dict[str, Any]]:
    if not query or not query.strip():
        return []

    conditions: List[Dict[str, str]] = []
    if user_id:
        conditions.append({"user_id": user_id})
    if speaker:
        conditions.append({"speaker": speaker})
    if scope:
        conditions.append({"scope": scope})
    if memory_type:
        conditions.append({"memory_type": memory_type})
    if source:
        conditions.append({"source": source})
    where_filter = _build_where_filter(conditions)
    try:
        query_kwargs: Dict[str, Any] = {
            "query_texts": [query],
            "n_results": n_results,
        }
        if where_filter:
            query_kwargs["where"] = where_filter

        results = collection.query(**query_kwargs)
        docs = results.get("documents", [[]])
        metas = results.get("metadatas", [[]])
        dists = results.get("distances", [[]])
        ids = results.get("ids", [[]])
        if not docs or not docs[0]:
            return []
        output: List[Dict[str, Any]] = []
        seen = set()

        for i, doc in enumerate(docs[0]):
            dist = dists[0][i] if dists and dists[0] and i < len(dists[0]) else None
            meta = metas[0][i] if metas and metas[0] and i < len(metas[0]) else {}
            if dist is not None and dist > max_distance:
                continue
            created = meta.get("created_at", "")
            if after and created and created < after:
                continue
            if before and created and created > before:
                continue
            if tag:
                stored_tags = meta.get("tags", "")
                if tag.lower() not in stored_tags.lower():
                    continue
            normalized = normalize_text(doc)
            if normalized in seen:
                continue
            seen.add(normalized)
            output.append({
                "id": ids[0][i] if ids and ids[0] and i < len(ids[0]) else None,
                "text": doc,
                "metadata": meta,
                "distance": dist
            })
        return output
    except Exception as e:
        Logger.error(f"Memory recall failed: {e}")
        return []

def recall_chatter_messages(query: str, user_id: str, **kwargs) -> List[Dict[str, Any]]:
    return recall_memories(query, user_id=user_id, speaker=USER_SPEAKER, **kwargs)

def recall_ai_about_chatter(query: str, user_id: str, **kwargs) -> List[Dict[str, Any]]:
    return recall_memories(query, user_id=user_id, speaker=AI_SPEAKER, scope=SCOPE_CHATTER, **kwargs)

def recall_ai_global(query: str, **kwargs) -> List[Dict[str, Any]]:
    return recall_memories(query, scope=SCOPE_GLOBAL, speaker=AI_SPEAKER, **kwargs)

def recall_chatter_ai_pair(query: str, user_id: str, n_results: int = 5, **kwargs) -> List[Dict[str, Any]]:
    user_mems = recall_memories(query, user_id=user_id, speaker=USER_SPEAKER, n_results=n_results, **kwargs)
    ai_mems = recall_memories(query, user_id=user_id, speaker=AI_SPEAKER, scope=SCOPE_CHATTER, n_results=n_results, **kwargs)
    combined = user_mems + ai_mems

    seen = set()
    unique = []
    for mem in combined:
        norm = normalize_text(mem["text"])
        if norm not in seen:
            seen.add(norm)
            unique.append(mem)
    unique.sort(key=lambda m: m.get("metadata", {}).get("created_at", ""))
    return unique


def recall_cross_user(
    query: str,
    user_ids: List[str],
    n_results: int = 3,
    **kwargs
) -> List[Dict[str, Any]]:
    seen = set()
    combined = []
    for uid in user_ids:
        for mem in recall_memories(query, user_id=uid, n_results=n_results, **kwargs):
            norm = normalize_text(mem["text"])
            if norm not in seen:
                seen.add(norm)
                combined.append(mem)
    return combined

def format_memories(memories: List[Dict[str, Any]]) -> Optional[str]:
    if not memories:
        return None
    lines = ["Relevant Memories:"]
    for mem in memories:
        speaker = mem.get("metadata", {}).get("speaker", "unknown")
        scope = mem.get("metadata", {}).get("scope", "")
        prefix = f"[{speaker}]" if scope != SCOPE_GLOBAL else "[AI-global]"
        lines.append(f"  {prefix} {mem['text']}")
    lines.append("Use these only when relevant. Do not mention them unless they apply.")
    return "\n".join(lines)


def random_memories(
    n_results: int = 3,
    speaker: Optional[str] = None,
    scope: Optional[str] = None,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conditions: List[Dict[str, str]] = []
    if speaker:
        conditions.append({"speaker": speaker})
    if scope:
        conditions.append({"scope": scope})
    if source:
        conditions.append({"source": source})
    where_filter = _build_where_filter(conditions)

    try:
        get_kwargs: Dict[str, Any] = {
            "limit": max(int(n_results or 3) * 12, int(n_results or 3), 12),
            "include": ["documents", "metadatas"],
        }
        if where_filter:
            get_kwargs["where"] = where_filter

        results = collection.get(**get_kwargs)
        docs = results.get("documents", []) or []
        metas = results.get("metadatas", []) or []
        ids = results.get("ids", []) or []
        if not docs:
            return []

        indices = list(range(len(docs)))
        random.shuffle(indices)
        output: List[Dict[str, Any]] = []
        seen = set()
        for idx in indices:
            doc = docs[idx]
            if not doc or not str(doc).strip():
                continue
            normalized = normalize_text(doc)
            if normalized in seen:
                continue
            seen.add(normalized)
            meta = metas[idx] if idx < len(metas) and metas[idx] else {}
            output.append({
                "id": ids[idx] if idx < len(ids) else None,
                "text": doc,
                "metadata": meta,
                "distance": None,
            })
            if len(output) >= int(n_results or 3):
                break
        return output
    except Exception as e:
        Logger.error(f"Random memory recall failed: {e}")
        return []

def store_user_memory(
    text: str,
    user_id: str,
    source: str = "chat",
    tags: Optional[List[str]] = None
) -> bool:
    return add_memory(
        text=text,
        user_id=user_id,
        speaker=USER_SPEAKER,
        scope=SCOPE_CHATTER,
        memory_type="user_fact",
        source=source,
        tags=tags
    )

def store_ai_memory_for_chatter(
    text: str,
    user_id: str,
    source: str = "chat",
    tags: Optional[List[str]] = None
) -> bool:
    return add_memory(
        text=text,
        user_id=user_id,
        speaker=AI_SPEAKER,
        scope=SCOPE_CHATTER,
        memory_type="assistant_response",
        source=source,
        tags=tags
    )


def store_ai_global_memory(
    text: str,
    source: str = "system",
    tags: Optional[List[str]] = None
) -> bool:
    return add_memory(
        text=text,
        user_id="__global__",
        speaker=AI_SPEAKER,
        scope=SCOPE_GLOBAL,
        memory_type="ai_self",
        source=source,
        tags=tags
    )

def delete_memories(ids: List[str]) -> int:
    clean = list(dict.fromkeys(
        str(i).strip()
        for i in (ids or [])
        if i is not None and str(i).strip()
    ))
    if not clean:
        return 0
    try:
        existing = collection.get(ids=clean, include=[])
        present = existing.get("ids", []) or []
        if not present:
            Logger.warn("No matching memories to delete.")
            return 0
        collection.delete(ids=present)
        Logger.quiet_print(f"Deleted {len(present)} memory(ies).")
        return len(present)
    except Exception as e:
        Logger.error(f"Memory deletion failed: {e}")
        raise MemoryOperationError("The memory database could not delete the selected memories.") from e


def _deletion_filter(
    user_id: Optional[str] = None,
    speaker: Optional[str] = None,
    scope: Optional[str] = None,
    memory_type: Optional[str] = None,
    source: Optional[str] = None,
) -> Optional[Dict]:
    conditions: List[Dict[str, str]] = []
    if user_id:
        conditions.append({"user_id": user_id})
    if speaker:
        conditions.append({"speaker": speaker})
    if scope:
        conditions.append({"scope": scope})
    if memory_type:
        conditions.append({"memory_type": memory_type})
    if source:
        conditions.append({"source": source})
    return _build_where_filter(conditions)


def count_memories_where(
    user_id: Optional[str] = None,
    speaker: Optional[str] = None,
    scope: Optional[str] = None,
    memory_type: Optional[str] = None,
    source: Optional[str] = None,
) -> int:
    where_filter = _deletion_filter(user_id, speaker, scope, memory_type, source)
    if not where_filter:
        return 0
    try:
        found = collection.get(where=where_filter, include=[])
        return len(found.get("ids", []) or [])
    except Exception as e:
        Logger.error(f"Memory count failed: {e}")
        raise MemoryOperationError("The memory database could not count matching memories.") from e


def delete_memories_where(
    user_id: Optional[str] = None,
    speaker: Optional[str] = None,
    scope: Optional[str] = None,
    memory_type: Optional[str] = None,
    source: Optional[str] = None,
) -> int:
    where_filter = _deletion_filter(user_id, speaker, scope, memory_type, source)
    if not where_filter:
        Logger.warn("Refusing bulk delete with no filter; use clear_all_memories().")
        return 0
    try:
        found = collection.get(where=where_filter, include=[])
        present = found.get("ids", []) or []
        if not present:
            return 0
        collection.delete(ids=present)
        Logger.quiet_print(f"Deleted {len(present)} memory(ies) by filter.")
        return len(present)
    except Exception as e:
        Logger.error(f"Bulk memory deletion failed: {e}")
        raise MemoryOperationError("The memory database could not complete the bulk deletion.") from e


def count_all_memories() -> int:
    try:
        return int(collection.count())
    except Exception as e:
        Logger.error(f"Memory count failed: {e}")
        raise MemoryOperationError("The memory database could not count stored memories.") from e


def clear_all_memories(confirm: bool = False) -> int:
    if not confirm:
        Logger.warn("clear_all_memories() requires confirm=True.")
        return 0
    try:
        found = collection.get(include=[])
        present = found.get("ids", []) or []
        if not present:
            return 0
        collection.delete(ids=present)
        Logger.quiet_print(f"Cleared {len(present)} memory(ies).")
        return len(present)
    except Exception as e:
        Logger.error(f"Memory clear failed: {e}")
        raise MemoryOperationError("The memory database could not clear all memories.") from e


_MEMORY_LOCK = asyncio.Lock()
async def run_mem_loop(func, *args, **kwargs):
    async with _MEMORY_LOCK:
        return await asyncio.to_thread(partial(func, *args, **kwargs))


async def dedupe_memory_main(
    text: str,
    user_id: str,
    speaker: str,
    distance_threshold: float = 0.15,
) -> bool:
    return await run_mem_loop(
        dedupe_memory,
        text=text,
        user_id=user_id,
        speaker=speaker,
        distance_threshold=distance_threshold,
    )


async def add_memory_main(
    text: str,
    user_id: str,
    speaker: str,
    scope: str = SCOPE_CHATTER,
    memory_type: str = "chat",
    source: str = "chat",
    tags: Optional[List[str]] = None,
    created_at: Optional[str] = None,
) -> bool:
    return await run_mem_loop(
        add_memory,
        text=text,
        user_id=user_id,
        speaker=speaker,
        scope=scope,
        memory_type=memory_type,
        source=source,
        tags=tags,
        created_at=created_at,
    )


async def recall_memories_main(
    query: str,
    user_id: Optional[str] = None,
    speaker: Optional[str] = None,
    scope: Optional[str] = None,
    memory_type: Optional[str] = None,
    source: Optional[str] = None,
    n_results: int = 5,
    max_distance: float = 1.0,
    after: Optional[str] = None,
    before: Optional[str] = None,
    tag: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return await run_mem_loop(
        recall_memories,
        query=query,
        user_id=user_id,
        speaker=speaker,
        scope=scope,
        memory_type=memory_type,
        source=source,
        n_results=n_results,
        max_distance=max_distance,
        after=after,
        before=before,
        tag=tag,
    )


async def recall_chatter_messages_main(
    query: str,
    user_id: str,
    **kwargs,
) -> List[Dict[str, Any]]:
    return await run_mem_loop(
        recall_chatter_messages,
        query=query,
        user_id=user_id,
        **kwargs,
    )


async def recall_ai_about_chatter_main(
    query: str,
    user_id: str,
    **kwargs,
) -> List[Dict[str, Any]]:
    return await run_mem_loop(
        recall_ai_about_chatter,
        query=query,
        user_id=user_id,
        **kwargs,
    )


async def recall_ai_global_main(
    query: str,
    **kwargs,
) -> List[Dict[str, Any]]:
    return await run_mem_loop(
        recall_ai_global,
        query=query,
        **kwargs,
    )


async def recall_chatter_ai_pair_main(
    query: str,
    user_id: str,
    n_results: int = 5,
    **kwargs,
) -> List[Dict[str, Any]]:
    return await run_mem_loop(
        recall_chatter_ai_pair,
        query=query,
        user_id=user_id,
        n_results=n_results,
        **kwargs,
    )


async def recall_cross_user_main(
    query: str,
    user_ids: List[str],
    n_results: int = 3,
    **kwargs,
) -> List[Dict[str, Any]]:
    return await run_mem_loop(
        recall_cross_user,
        query=query,
        user_ids=user_ids,
        n_results=n_results,
        **kwargs,
    )


async def recall_memories_total_main(
    query: str,
    user_id: str,
    n_results: int = 3,
    max_distance: float = 1.0,
) -> List[Dict[str, Any]]:

    if not query or not query.strip():
        return []

    async with _MEMORY_LOCK:
        chatter_mems, ai_chatter_mems, ai_global_mems = await asyncio.to_thread(
            lambda: (
                recall_chatter_messages(
                    query,
                    user_id=user_id,
                    n_results=n_results,
                    max_distance=max_distance,
                ),
                recall_ai_about_chatter(
                    query,
                    user_id=user_id,
                    n_results=n_results,
                    max_distance=max_distance,
                ),
                recall_ai_global(
                    query,
                    n_results=n_results,
                    max_distance=max_distance,
                ),
            )
        )

    seen = set()
    combined: List[Dict[str, Any]] = []

    for mem in chatter_mems + ai_chatter_mems + ai_global_mems:
        text = mem.get("text", "")
        normalized = normalize_text(text)
        if normalized in seen:
            continue
        seen.add(normalized)
        combined.append(mem)

    return combined


async def store_user_memory_main(
    text: str,
    user_id: str,
    source: str = "chat",
    tags: Optional[List[str]] = None,
) -> bool:
    return await run_mem_loop(
        store_user_memory,
        text=text,
        user_id=user_id,
        source=source,
        tags=tags,
    )


async def store_ai_memory_for_chatter_main(
    text: str,
    user_id: str,
    source: str = "chat",
    tags: Optional[List[str]] = None,
) -> bool:
    return await run_mem_loop(
        store_ai_memory_for_chatter,
        text=text,
        user_id=user_id,
        source=source,
        tags=tags,
    )


async def store_ai_global_memory_main(
    text: str,
    source: str = "system",
    tags: Optional[List[str]] = None,
) -> bool:
    return await run_mem_loop(
        store_ai_global_memory,
        text=text,
        source=source,
        tags=tags,
    )


async def random_memories_main(
    n_results: int = 3,
    speaker: Optional[str] = None,
    scope: Optional[str] = None,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return await run_mem_loop(
        random_memories,
        n_results=n_results,
        speaker=speaker,
        scope=scope,
        source=source,
    )


async def delete_memories_main(ids: List[str]) -> int:
    return await run_mem_loop(delete_memories, ids=ids)


async def count_memories_where_main(
    user_id: Optional[str] = None,
    speaker: Optional[str] = None,
    scope: Optional[str] = None,
    memory_type: Optional[str] = None,
    source: Optional[str] = None,
) -> int:
    return await run_mem_loop(
        count_memories_where,
        user_id=user_id,
        speaker=speaker,
        scope=scope,
        memory_type=memory_type,
        source=source,
    )


async def count_all_memories_main() -> int:
    return await run_mem_loop(count_all_memories)


async def delete_memories_where_main(
    user_id: Optional[str] = None,
    speaker: Optional[str] = None,
    scope: Optional[str] = None,
    memory_type: Optional[str] = None,
    source: Optional[str] = None,
) -> int:
    return await run_mem_loop(
        delete_memories_where,
        user_id=user_id,
        speaker=speaker,
        scope=scope,
        memory_type=memory_type,
        source=source,
    )


async def clear_all_memories_main(confirm: bool = False) -> int:
    return await run_mem_loop(clear_all_memories, confirm=confirm)
