import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def _clean(value: Any) -> str:
    return str(value or "").strip()


@dataclass(slots=True)
class StreamChatMessage:
    provider: str
    author_name: str
    text: str
    native_id: str = ""
    author_id: str = ""
    channel_id: str = ""
    channel_name: str = ""
    role: str = ""
    priority: bool = False
    kind: str = "message"
    event_type: str = ""
    received_at: float = field(default_factory=time.time)
    sequence: int = 0
    raw: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.provider = _clean(self.provider).lower() or "unknown"
        self.author_name = _clean(self.author_name) or "Someone"
        self.text = _clean(self.text)
        self.native_id = _clean(self.native_id) or str(uuid.uuid4())
        self.author_id = _clean(self.author_id)
        self.channel_id = _clean(self.channel_id)
        self.channel_name = _clean(self.channel_name)
        self.role = _clean(self.role).lower()
        self.kind = _clean(self.kind).lower() or "message"
        self.event_type = _clean(self.event_type).lower()

    @property
    def queue_id(self) -> str:
        return f"{self.provider}:{self.native_id}"

    @property
    def source_label(self) -> str:
        return {
            "twitch": "Twitch",
            "youtube": "YouTube",
            "kick": "Kick",
        }.get(self.provider, self.provider.replace("_", " ").title())

    def snapshot(self, lane: str, *, now: float | None = None) -> dict[str, Any]:
        current = time.time() if now is None else now
        return {
            "id": self.queue_id,
            "native_id": self.native_id,
            "provider": self.provider,
            "source_label": self.source_label,
            "author": self.author_name,
            "author_id": self.author_id,
            "content": self.text,
            "priority": self.priority,
            "lane": lane,
            "role": self.role,
            "kind": self.kind,
            "event_type": self.event_type,
            "received_at": self.received_at,
            "sequence": self.sequence,
            "age_seconds": max(0, int(current - self.received_at)),
        }


@dataclass(slots=True)
class StreamChatEvent:
    provider: str
    event_type: str
    username: str
    detail: str = ""
    native_id: str = ""
    received_at: float = field(default_factory=time.time)
    raw: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.provider = _clean(self.provider).lower() or "unknown"
        self.event_type = _clean(self.event_type).lower()
        self.username = _clean(self.username) or "Someone"
        self.detail = _clean(self.detail)
        self.native_id = _clean(self.native_id) or str(uuid.uuid4())


@dataclass(slots=True)
class ProviderStatus:
    provider: str
    state: str = "disconnected"
    detail: str = ""
    account_name: str = ""
    updated_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "state": self.state,
            "detail": self.detail,
            "account_name": self.account_name,
            "updated_at": self.updated_at,
        }
