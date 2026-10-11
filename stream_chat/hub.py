import queue
import threading
import time
import math
from collections import OrderedDict
from itertools import count
from pathlib import Path
from typing import Callable

from files.stream_chat.models import ProviderStatus, StreamChatEvent, StreamChatMessage
from files.stream_chat.policies import normalize_policy, select_next
from files.system_setup.settings import get_bool_setting, get_settings, save_settings
from files.system_setup.system_logger import Logger

_STREAM_PROVIDERS = {"twitch", "youtube", "kick"}
_LOCAL_IGNORE_PATH = Path(__file__).resolve().parents[1] / "twitch_chat" / "blacklist.txt"


def _setting_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(get_settings(name) or default))
    except Exception:
        return default


def _enabled_from_settings() -> set[str]:
    raw = get_settings("STREAM_CHAT_ENABLED_PROVIDERS")
    if raw is None or raw == "":
        return {"twitch"}
    if isinstance(raw, str):
        values = raw.replace(",", "\n").splitlines()
    elif isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = ["twitch"]
    enabled = {str(value).strip().lower() for value in values if str(value).strip()}
    return enabled & _STREAM_PROVIDERS


class StreamChatHub:
    def __init__(self, *, start_dispatcher: bool = True) -> None:
        legacy_policy = get_settings("TWITCH_POLICY") or "latest-buffered"
        self.policy = normalize_policy(get_settings("STREAM_CHAT_POLICY") or legacy_policy)
        self.keep_last = _setting_int("STREAM_CHAT_KEEP_LAST", 4, 0)
        self.global_backlog = _setting_int("STREAM_CHAT_GLOBAL_BACKLOG", 100)
        self.provider_backlog = _setting_int("STREAM_CHAT_PROVIDER_BACKLOG", 40)
        self.enabled_providers = _enabled_from_settings()
        self.paused = bool(get_settings("STREAM_CHAT_PAUSED"))
        self.cooldown_enabled = get_bool_setting("STREAM_CHAT_COOLDOWN_ENABLED", default=False)
        try:
            self.cooldown_seconds = self._validate_cooldown(get_settings("STREAM_CHAT_COOLDOWN_SECONDS") or 20)
        except (ValueError, TypeError):
            self.cooldown_seconds = 20.0
        self._last_dispatch_finished = None

        self._priority: list[StreamChatMessage] = []
        self._standard: list[StreamChatMessage] = []
        self._condition = threading.Condition(threading.RLock())
        self._sequence = count(1)
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._seen_limit = 5000
        self._blocked_authors: set[str] = set()
        self._blocked_loaded_at = 0.0
        self._running = False
        self._in_pipeline = False
        self._thread: threading.Thread | None = None
        self._ui_page = None
        self._status: dict[str, ProviderStatus] = {
            name: ProviderStatus(name) for name in sorted(_STREAM_PROVIDERS)
        }
        self._queue_listeners: list[Callable[[], None]] = []
        self._status_listeners: list[Callable[[], None]] = []
        if start_dispatcher:
            self.start()

    @property
    def in_pipeline(self) -> bool:
        with self._condition:
            return self._in_pipeline

    def start(self) -> None:
        with self._condition:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._dispatch_loop,
                name="stream-chat-dispatcher",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        with self._condition:
            self._running = False
            self._condition.notify_all()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        self._thread = None

    def set_ui_page(self, page) -> None:
        self._ui_page = page
        self._notify_queue()
        self._notify_status()

    def clear_ui_page(self, page) -> None:
        if self._ui_page is page:
            self._ui_page = None

    def add_queue_listener(self, callback: Callable[[], None]) -> None:
        if callback not in self._queue_listeners:
            self._queue_listeners.append(callback)

    def add_status_listener(self, callback: Callable[[], None]) -> None:
        if callback not in self._status_listeners:
            self._status_listeners.append(callback)

    def emit_message(self, message: StreamChatMessage) -> None:
        if message.provider not in self.enabled_providers or self.is_blocked(message):
            return
        page = self._ui_page
        if page is None:
            return
        try:
            if hasattr(page, "push_stream_message"):
                page.after(0, page.push_stream_message, message)
            elif message.provider == "twitch" and hasattr(page, "push_chat_message"):
                tags = {
                    "color": "",
                    "mod": "1" if message.role == "mod" else "0",
                    "subscriber": "1" if message.role == "sub" else "0",
                    "badges": "broadcaster" if message.role == "broadcaster" else "",
                    "user-id": message.author_id,
                }
                page.after(0, page.push_chat_message, message.author_name, tags, message.text)
        except Exception as exc:
            Logger.warn(f"Failed rendering {message.provider} chat: {exc}")

    def emit_event(self, event: StreamChatEvent) -> bool:
        if (
            event.provider not in self.enabled_providers
            or self.is_author_blocked(event.provider, event.username)
        ):
            return False
        page = self._ui_page
        if page is None:
            return True
        try:
            if hasattr(page, "push_stream_event"):
                page.after(0, page.push_stream_event, event)
            elif event.provider == "twitch" and hasattr(page, "push_event"):
                page.after(0, page.push_event, event.event_type, event.username, event.detail)
        except Exception as exc:
            Logger.warn(f"Failed rendering {event.provider} event: {exc}")
        return True

    def enqueue(self, message: StreamChatMessage) -> bool:
        if not self._twitch_input_allowed(message):
            return False
        if (
            not message.text
            or message.provider not in self.enabled_providers
            or self.is_blocked(message)
            or self.is_ignored_message(message.text)
        ):
            return False
        with self._condition:
            if message.queue_id in self._seen:
                return False
            self._seen[message.queue_id] = time.time()
            while len(self._seen) > self._seen_limit:
                self._seen.popitem(last=False)
            message.sequence = next(self._sequence)
            target = self._priority if message.priority else self._standard
            target.append(message)
            self._enforce_limits_locked(message.provider)
            self._condition.notify_all()
        self._notify_queue()
        return True

    @staticmethod
    def _twitch_input_allowed(message):
        if message.provider != "twitch" or not get_bool_setting("TWITCH_REDEEM_ONLY", default=False):
            return True
        reward_id = str(get_settings("TWITCH_INPUT_REWARD_ID") or "").strip()
        return bool(reward_id and message.event_type == "input_redemption"
                    and isinstance(message.raw, dict)
                    and message.raw.get("reward_id") == reward_id)

    def _refresh_blocked_authors(self) -> None:
        now = time.time()
        if now - self._blocked_loaded_at >= 5.0:
            try:
                self._blocked_authors = {
                    line.strip().casefold()
                    for line in _LOCAL_IGNORE_PATH.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                }
            except FileNotFoundError:
                self._blocked_authors = set()
            except Exception as exc:
                Logger.warn(f"Could not read local stream-chat ignore list: {exc}")
            self._blocked_loaded_at = now

    def is_author_blocked(
        self,
        provider: str,
        author_name: str,
        author_id: str = "",
    ) -> bool:
        self._refresh_blocked_authors()
        provider = str(provider or "").strip().casefold()
        author = str(author_name or "").strip().casefold()
        author_id = str(author_id or "").strip().casefold()
        if not author and not author_id:
            return False
        candidates = {
            author,
            f"{provider}:{author}",
            f"{author} · {provider}",
        }
        if author_id:
            candidates.update({author_id, f"{provider}:{author_id}"})
        candidates.discard("")
        return bool(candidates & self._blocked_authors)

    def is_blocked(self, message: StreamChatMessage) -> bool:
        return self.is_author_blocked(
            message.provider,
            message.author_name,
            message.author_id,
        )

    @staticmethod
    def ignored_prefixes() -> list[str]:
        raw = (
            get_settings("STREAM_CHAT_IGNORED_PREFIXES")
            or get_settings("TWITCH_IGNORED_PREFIXES")
            or ""
        )
        values = raw if isinstance(raw, (list, tuple)) else str(raw).splitlines()
        return [str(value).strip() for value in values if str(value).strip()]

    def is_ignored_message(self, text: str) -> bool:
        content = str(text or "").lstrip()
        if not content:
            return True
        if get_bool_setting("STREAM_CHAT_IGNORE_COMMANDS", default=False) and content.startswith("!"):
            return True
        return any(content.startswith(prefix) for prefix in self.ignored_prefixes())

    def refresh_local_ignores(self) -> None:
        self._blocked_loaded_at = 0.0

    def _enforce_limits_locked(self, provider: str) -> None:
        provider_items = [
            item for item in self._standard if item.provider == provider
        ]
        overflow = len(provider_items) - self.provider_backlog
        if overflow > 0:
            for item in provider_items[:overflow]:
                try:
                    self._standard.remove(item)
                except ValueError:
                    pass
        while len(self._priority) + len(self._standard) > self.global_backlog:
            if self._standard:
                self._standard.pop(0)
            elif self._priority:
                self._priority.pop(0)

    def take_next(self) -> StreamChatMessage | None:
        with self._condition:
            item = select_next(
                self._standard,
                self._priority,
                policy=self.policy,
                keep_last=self.keep_last,
            )
        if item is not None:
            self._notify_queue()
        return item

    def list_pending(self) -> list[dict]:
        with self._condition:
            priority = [item.snapshot("priority") for item in self._priority]
            standard = [item.snapshot("standard") for item in self._standard]
        return priority + standard

    def remove(self, queue_id: str) -> bool:
        wanted = str(queue_id or "")
        with self._condition:
            for buffer in (self._priority, self._standard):
                for index, item in enumerate(buffer):
                    if item.queue_id == wanted or item.native_id == wanted:
                        del buffer[index]
                        self._notify_queue()
                        return True
        return False

    def clear(self, provider: str | None = None) -> int:
        provider = str(provider or "").strip().lower()
        with self._condition:
            before = len(self._priority) + len(self._standard)
            if provider:
                self._priority[:] = [item for item in self._priority if item.provider != provider]
                self._standard[:] = [item for item in self._standard if item.provider != provider]
            else:
                self._priority.clear()
                self._standard.clear()
            removed = before - len(self._priority) - len(self._standard)
        if removed:
            self._notify_queue()
        return removed

    def set_policy(self, policy: str, *, persist: bool = True) -> str:
        self.policy = normalize_policy(policy)
        if persist:
            save_settings("STREAM_CHAT_POLICY", self.policy)
        self._notify_queue()
        return self.policy

    def set_paused(self, paused: bool, *, persist: bool = True) -> None:
        with self._condition:
            self.paused = bool(paused)
            self._condition.notify_all()
        if persist:
            save_settings("STREAM_CHAT_PAUSED", self.paused)
        self._notify_status()

    def set_enabled_providers(self, providers, *, persist: bool = True) -> set[str]:
        enabled = {
            str(provider).strip().lower()
            for provider in providers
            if str(provider).strip().lower() in _STREAM_PROVIDERS
        }
        with self._condition:
            self.enabled_providers = enabled
            self._priority[:] = [
                item for item in self._priority if item.provider in enabled
            ]
            self._standard[:] = [
                item for item in self._standard if item.provider in enabled
            ]
            self._condition.notify_all()
        if persist:
            save_settings("STREAM_CHAT_ENABLED_PROVIDERS", sorted(enabled))
        self._notify_status()
        self._notify_queue()
        return set(enabled)

    def update_provider_status(
        self,
        provider: str,
        state: str,
        detail: str = "",
        account_name: str = "",
    ) -> None:
        provider = str(provider or "").strip().lower()
        if not provider:
            return
        self._status[provider] = ProviderStatus(
            provider=provider,
            state=str(state or "disconnected").strip().lower(),
            detail=str(detail or "").strip(),
            account_name=str(account_name or "").strip(),
        )
        self._notify_status()

    def provider_status(self, provider: str) -> dict:
        status = self._status.get(str(provider or "").strip().lower())
        return status.as_dict() if status else ProviderStatus(provider).as_dict()

    def statuses(self) -> dict[str, dict]:
        return {name: status.as_dict() for name, status in self._status.items()}

    def _notify_queue(self) -> None:
        callbacks = list(self._queue_listeners)
        page = self._ui_page
        if page is not None and hasattr(page, "refresh_queue_panel"):
            callbacks.append(lambda: page.after(0, page.refresh_queue_panel))
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass

    def _notify_status(self) -> None:
        callbacks = list(self._status_listeners)
        page = self._ui_page
        if page is not None and hasattr(page, "refresh_platform_status"):
            callbacks.append(lambda: page.after(0, page.refresh_platform_status))
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass

    @staticmethod
    def _validate_cooldown(seconds):
        seconds = float(seconds)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Cooldown must be a positive number of seconds.")
        return seconds

    def set_cooldown(self, enabled, seconds):
        seconds = self._validate_cooldown(seconds)
        save_settings("STREAM_CHAT_COOLDOWN_SECONDS", seconds)
        save_settings("STREAM_CHAT_COOLDOWN_ENABLED", bool(enabled))
        with self._condition:
            self.cooldown_enabled = bool(enabled)
            self.cooldown_seconds = seconds
            self._condition.notify_all()
        self._notify_queue()

    def _cooldown_remaining(self):
        if not self.cooldown_enabled or self._last_dispatch_finished is None:
            return 0.0
        return max(0.0, self.cooldown_seconds - (time.monotonic() - self._last_dispatch_finished))

    def _dispatch_loop(self) -> None:
        while True:
            with self._condition:
                ready = self._condition.wait_for(
                    lambda: not self._running
                    or (
                        not self.paused
                        and not self._in_pipeline
                        and bool(self._priority or self._standard)
                    ),
                    timeout=0.5,
                )
                if not self._running:
                    return
                # wait_for may time out while paused: never dequeue then.
                if not ready:
                    continue
                # Recheck queued items when redeem-only settings change.
                self._standard[:] = [m for m in self._standard if self._twitch_input_allowed(m)]
                self._priority[:] = [m for m in self._priority if self._twitch_input_allowed(m)]
                remaining = self._cooldown_remaining()
                if remaining > 0:
                    self._condition.wait(timeout=min(remaining, 0.5))
                    continue
                item = select_next(
                    self._standard,
                    self._priority,
                    policy=self.policy,
                    keep_last=self.keep_last,
                )
                if item is None:
                    continue
                self._in_pipeline = True
            self._notify_queue()
            try:
                self._dispatch_item(item)
            except Exception as exc:
                Logger.warn(f"{item.source_label} chat dispatch failed: {exc}")
            finally:
                with self._condition:
                    self._in_pipeline = False
                    self._last_dispatch_finished = time.monotonic()
                    self._condition.notify_all()
                self._notify_queue()

    def _dispatch_item(self, item: StreamChatMessage) -> None:
        from files.main_loop.main_loop import message_queue

        response_queue = queue.Queue(maxsize=1)
        meta = {
            "turn_id": item.queue_id,
            "source": item.provider,
            "provider": item.provider,
            "provider_message_id": item.native_id,
            "channel_id": item.channel_id,
            "channel_name": item.channel_name,
            "author_id": item.author_id,
            "memory_user_id": f"{item.provider}:{item.author_id or item.author_name}",
            "role": item.role,
            "kind": item.kind,
            "event_type": item.event_type,
            "store_input_memory": item.kind != "event",
            "store_output_memory": item.kind != "event",
            "use_live_recall": item.kind != "event",
            "allow_finetune": item.kind != "event",
            "speak_input": item.kind != "event",
            "llm_prompt": (
                f"[{'Stream event from' if item.kind == 'event' else 'Message from'} {item.source_label} live chat]\n"
                f"{item.author_name}: {item.text}"
            ),
        }
        Logger.quiet_print(f"[{item.source_label}] {item.author_name}: {item.text}")
        message_queue.put((f"{item.author_name}: {item.text}", True, response_queue, meta))
        try:
            response_queue.get(timeout=180)
        except queue.Empty:
            Logger.warn(f"Timed out waiting for response to {item.source_label} chat.")


_hub: StreamChatHub | None = None
_hub_lock = threading.Lock()


def get_stream_chat_hub() -> StreamChatHub:
    global _hub
    with _hub_lock:
        if _hub is None:
            _hub = StreamChatHub()
        return _hub
