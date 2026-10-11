import asyncio
import json
import os
import time
import uuid
import threading
from functools import wraps
from pathlib import Path
from files.memory.conversation_ledger import public_exchange, exchange_messages, render_exchange


def locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped

DEFAULTS = {
    "summary_enabled": True,
    "summary_recent_turns": 7,
    "summary_batch_turns": 3,
    "summary_recent_count": 7,
    "summary_context_tokens": 12000,
    "summary_batch_tokens": 6000,
    "summary_words": 30,
    "summary_generation_tokens": 4096,
    "summary_store_vectors": True,
}


def token_estimate(text):
    # Deliberately conservative byte-based estimate; not a model tokenizer.
    return max(1, len(str(text).encode("utf-8")))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class ConversationMemory:
    def __init__(self, path, setting, log=print, on_summary=None):
        self._lock = threading.RLock()
        self.path, self.setting, self.log = Path(path), setting, log
        self.on_summary = on_summary
        self.state = {"recent": [], "pending": [], "summaries": []}
        if self.path.exists():
            # Do not overwrite an unreadable memory file with an empty state.
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
            for key in ("recent", "pending", "summaries"):
                if not isinstance(self.state.get(key), list):
                    raise ValueError(f"Invalid conversation memory: {key}")
        self.task = None
        self.retry_at = 0

    def value(self, key):
        value = self.setting(key)
        return DEFAULTS[key] if value is None or value == "" else value

    def enabled(self):
        return str(self.value("summary_enabled")).lower() in ("true", "1", "yes", "on")

    def number(self, key):
        try:
            return max(1, int(self.value(key)))
        except (TypeError, ValueError):
            return DEFAULTS[key]

    @locked
    def save(self):
        atomic_json(self.path, self.state)

    @locked
    def resume(self, restart_turns):
        if self.state["recent"]:
            self.state["pending"].extend(self.state["recent"][:-3])
            self.state["recent"] = self.state["recent"][-3:]
        elif not self.state["pending"] and not self.state["summaries"]:
            self.state["recent"] = [entry for t in restart_turns[-3:]
                                    if (entry := public_exchange(t)) is not None]
        self.save()

    @staticmethod
    def render(turn):
        return render_exchange(turn)

    def rebalance(self):
        recent = self.state["recent"]
        limit = self.number("summary_context_tokens")
        while recent and (len(recent) > self.number("summary_recent_turns") or
                          sum(token_estimate(self.render(t)) for t in recent) > limit):
            self.state["pending"].append(recent.pop(0))

    @locked
    def record(self, turn):
        entry = public_exchange(turn, self.setting("character_name") or "Assistant")
        if entry is None:
            return False
        known = {t.get("id") for t in self.state["recent"] + self.state["pending"]}
        known.update(i for s in self.state["summaries"] for i in s.get("turn_ids", []))
        if entry["id"] in known:
            return False
        self.state["recent"].append(entry)
        self.rebalance()
        self.save()
        return True

    @locked
    def request_context(self, available=None):
        self.rebalance()
        self.save()
        budget = self.number("summary_context_tokens")
        if available is not None:
            budget = min(budget, max(0, available))
        # Newest exact turns get priority; then summaries, then pending overflow.
        recent, summaries, pending = [], [], []
        groups = [(self.state["recent"], recent, self.render),
                  (self.state["summaries"][-self.number("summary_recent_count"):], summaries,
                   lambda s: s["text"]),
                  (self.state["pending"], pending, self.render)]
        if not self.enabled():
            # Disabling summarization must not disable model-switch continuity.
            # Existing summaries stay archived but are not injected.
            groups = [(self.state["pending"] + self.state["recent"], recent, self.render)]
        for source, destination, render in groups:
            for item in reversed(source):
                text = render(item)
                cost = token_estimate(text)
                if cost > budget:
                    break
                destination.insert(0, item)
                budget -= cost
        messages = []
        for turn in pending + recent:
            messages.extend(exchange_messages(turn))
        return {"messages": messages, "turns": [dict(t) for t in pending + recent], "summaries": [s["text"] for s in summaries]}

    def context(self, available=None):
        context = self.request_context(available)
        return "\n".join(context["summaries"] + [
            f"{m['role']}: {m['content']}" for m in context["messages"]])

    @locked
    def schedule(self, generate, store=None):
        if not self.enabled() or time.monotonic() < self.retry_at:
            return
        if self.task is not None and not self.task.done():
            return
        pending = self.state["pending"]
        if not pending:
            return
        cap = self.number("summary_batch_tokens")
        if (len(pending) < self.number("summary_batch_turns") and
                sum(token_estimate(self.render(t)) for t in pending) < cap):
            return
        batch, used = [], 0
        # The interaction count is a trigger, not a maximum batch size.
        for turn in pending:
            cost = token_estimate(self.render(turn))
            if batch and used + cost > cap:
                break
            batch.append(turn)
            used += cost
        self.log(f"Summarizing {len(batch)} older interactions in the background.")
        self.task = asyncio.create_task(self.summarize(batch, generate, store))

    async def summarize(self, batch, generate, store):
        try:
            text = await generate(batch, self.number("summary_words"),
                                  self.number("summary_generation_tokens"))
            text = str(text or "").strip()
            if not text or text.startswith(("Error:", "Turn interrupted")):
                raise ValueError("Summarizer returned no usable answer")
            with self._lock:
                ids = {t["id"] for t in batch}
                summary = {"id": str(uuid.uuid4()), "text": text,
                           "turn_ids": list(ids), "source_turns": batch, "created_at": time.time()}
                # Commit only the captured IDs. New arrivals remain queued.
                previous = self.state
                self.state = dict(previous, summaries=previous["summaries"] + [summary],
                                  pending=[t for t in previous["pending"] if t["id"] not in ids])
                try:
                    self.save()
                except Exception:
                    self.state = previous
                    raise
            self.log(f"Memory summary ({len(batch)} interactions): {text}")
            if self.on_summary is not None:
                try:
                    self.on_summary(summary)
                except Exception as exc:
                    self.log(f"Summary display failed: {exc}")
            self.retry_at = 0
            if store and str(self.value("summary_store_vectors")).lower() in ("true", "1"):
                await store(summary, batch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.log(f"Memory summary failed; uncommitted turns retained: {exc}")
            self.retry_at = time.monotonic() + 60
