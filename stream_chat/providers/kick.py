import json
import threading
from typing import Any

from files.stream_chat import StreamChatEvent, StreamChatMessage, get_stream_chat_hub
from files.stream_chat.providers.base import StreamChatProvider
from files.system_setup.settings import get_settings


## This one is a bit more tricky given that Kick's official API (located here) https://docs.kick.com/events/introduction -
## Requires us to use its verification tool since it's looked behind a webhook adapter
## I'm just too lazy to make an account to verify it works 100%. So please make an issue if broken or PR with a solution - 
## For easier authentication.


class KickProvider(StreamChatProvider):
    provider_name = "kick"

    def __init__(self, hub=None) -> None:
        self.hub = hub or get_stream_chat_hub()
        self._running = False

    def start(self) -> None:
        webhook = str(get_settings("KICK_WEBHOOK_URL") or "").strip()
        detail = (
            "Webhook URL saved; connect a signature-verifying relay transport"
            if webhook
            else "Configure a public HTTPS webhook or relay before connecting"
        )
        self._running = False
        self.hub.update_provider_status("kick", "needs_setup", detail)

    def stop(self) -> None:
        self._running = False
        self.hub.update_provider_status("kick", "disconnected")

    def is_running(self) -> bool:
        return self._running

    def ingest_verified_event(
        self,
        event_type: str,
        payload: dict[str, Any] | str | bytes,
        *,
        event_message_id: str,
    ) -> bool:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise TypeError("Kick webhook payload must be a JSON object")
        event_type = str(event_type or "").strip().lower()
        if event_type == "chat.message.sent":
            message = self.normalize_chat_message(payload, event_message_id=event_message_id)
            self.hub.emit_message(message)
            return self.hub.enqueue(message)

        username = self._username(payload)
        detail = self._event_detail(event_type, payload)
        normalized = {
            "channel.followed": "follow",
            "channel.subscription.new": "sub",
            "channel.subscription.renewal": "resub",
            "channel.subscription.gifts": "gift_sub",
            "channel.reward.redemption.updated": "redemption",
            "kicks.gifted": "milestone",
        }.get(event_type, "milestone")
        accepted = self.hub.emit_event(
            StreamChatEvent(
                provider="kick",
                event_type=normalized,
                username=username,
                detail=detail,
                native_id=event_message_id,
                raw=payload,
            )
        )
        if not accepted:
            return False
        prompt = self._event_prompt(normalized, username, detail)
        if not prompt:
            return False
        return self.hub.enqueue(
            StreamChatMessage(
                provider="kick",
                native_id=event_message_id,
                author_name="Stream Event",
                text=prompt,
                priority=True,
                kind="event",
                event_type=normalized,
                raw=payload,
            )
        )

    @classmethod
    def normalize_chat_message(
        cls,
        payload: dict[str, Any],
        *,
        event_message_id: str,
    ) -> StreamChatMessage:
        sender = payload.get("sender") or payload.get("user") or {}
        broadcaster = payload.get("broadcaster") or {}
        identity = sender.get("identity") or {}
        badges = identity.get("badges") or sender.get("badges") or []
        badge_text = " ".join(
            str(badge.get("text") or badge.get("type") or badge)
            if isinstance(badge, dict) else str(badge)
            for badge in badges
        ).lower()
        role = ""
        if "broadcaster" in badge_text:
            role = "broadcaster"
        elif "moderator" in badge_text or "mod" in badge_text:
            role = "mod"
        elif "subscriber" in badge_text or "sub" in badge_text:
            role = "sub"
        native_id = str(payload.get("message_id") or payload.get("id") or event_message_id)
        content = payload.get("content")
        if isinstance(content, dict):
            content = content.get("message") or content.get("text") or ""
        return StreamChatMessage(
            provider="kick",
            native_id=native_id,
            author_name=str(sender.get("username") or sender.get("name") or "Kick Viewer"),
            author_id=str(sender.get("user_id") or sender.get("id") or ""),
            channel_id=str(broadcaster.get("user_id") or broadcaster.get("id") or ""),
            channel_name=str(broadcaster.get("username") or broadcaster.get("name") or ""),
            text=str(content or payload.get("message") or ""),
            role=role,
            raw=payload,
        )

    @staticmethod
    def _username(payload: dict[str, Any]) -> str:
        actor = payload.get("follower") or payload.get("subscriber") or payload.get("gifter") or payload.get("user") or payload.get("sender") or {}
        return str(actor.get("username") or actor.get("name") or "Someone")

    @staticmethod
    def _event_detail(event_type: str, payload: dict[str, Any]) -> str:
        if event_type == "channel.subscription.gifts":
            return str(payload.get("gifted_subscriptions") or payload.get("amount") or "gift subscriptions")
        if event_type == "channel.reward.redemption.updated":
            reward = payload.get("reward") or {}
            return str(reward.get("title") or payload.get("reward_title") or "channel reward")
        if event_type == "kicks.gifted":
            return str(payload.get("amount") or payload.get("kicks") or "KICKs")
        return str(payload.get("duration") or payload.get("months") or "")

    @staticmethod
    def _event_prompt(event_type: str, username: str, detail: str) -> str:
        if event_type == "follow":
            return f"{username} just followed on Kick. Welcome them briefly and warmly."
        if event_type in {"sub", "resub"}:
            return f"{username} just subscribed on Kick. Thank them warmly."
        if event_type == "gift_sub":
            return f"{username} gifted subscriptions on Kick ({detail}). Thank them for supporting the community."
        if event_type == "redemption":
            return f"{username} redeemed {detail} on Kick. Acknowledge the redemption."
        if event_type == "milestone":
            return f"{username} triggered a Kick milestone: {detail}. Acknowledge it warmly."
        return ""


_provider: KickProvider | None = None
_provider_lock = threading.Lock()


def get_kick_provider() -> KickProvider:
    global _provider
    with _provider_lock:
        if _provider is None:
            _provider = KickProvider()
        return _provider
