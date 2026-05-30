import time
import asyncio
import queue
import uuid
from itertools import count
from pathlib import Path
from threading import RLock, Thread

from files.system_setup.settings import get_auth, get_settings
from files.system_setup.system_logger import Logger
from twitchio.ext import commands as _commands
from twitchio import eventsub as _eventsub

twitchbot       = None
read_chat_thread = None
read_chat_running = False
_bot_loop: asyncio.AbstractEventLoop | None = None

PROJECT_ROOT = Path(__file__).resolve().parent
FILE_PATH    = PROJECT_ROOT / "blacklist.txt"

twitch_access_token  = get_auth("twitch", "access_token")
twitch_channel       = ""
blacklisted_users: list[str] = []
_ui_page = None
_queue_ids = count(1)
_queue_lock = RLock()
_VALID_POLICIES = {"latest", "latest-buffered", "all"}
_PRIORITY_EVENT_TYPES = {
    "follow",
    "sub",
    "resub",
    "raid",
    "gift_sub",
    "milestone",
    "redemption",
}

def _normalize_policy(policy: str | None) -> str:
    value = str(policy or "").strip().lower()
    return value if value in _VALID_POLICIES else "latest-buffered"


def ignored_prefixes() -> list[str]:
    raw = get_settings("TWITCH_IGNORED_PREFIXES") or ""
    if isinstance(raw, (list, tuple)):
        parts = raw
    else:
        parts = str(raw).splitlines()
    prefixes: list[str] = []
    for item in parts:
        prefix = str(item or "").strip()
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)
    return prefixes


def has_ignored_prefix(message: str) -> bool:
    content = str(message or "").lstrip()
    if not content:
        return False
    return any(content.startswith(prefix) for prefix in ignored_prefixes())

def set_ui_page(page) -> None:
    global _ui_page
    _ui_page = page

def get_ui_page():
    return _ui_page

def _push_chat(author: str, color: str, role: str, content: str, user_id: str = "") -> None:
    page = _ui_page
    if page is None:
        Logger.warn(f"No Twitch UI page registered for chat: {author}")
        return

    pseudo_tags = {
        "color":             color or "",
        "mod":               "1" if role == "mod" else "0",
        "subscriber":        "1" if role == "sub" else "0",
        "first-msg":         "1" if role == "first" else "0",
        "returning-chatter": "1" if role == "returning" else "0",
        "badges":            "broadcaster" if role == "broadcaster" else "",
        "user-id":           user_id or "",
    }

    try:
        page.after(0, page.push_chat_message, author, pseudo_tags, content)
    except Exception as e:
        Logger.warn(f"Failed pushing Twitch chat from {author}: {e}")

def _push_event(event_type: str, username: str, detail: str = "") -> None:
    page = _ui_page
    if page is None:
        Logger.warn(f"No Twitch page registered for event: {event_type}")
    else:
        try:
            page.after(0, page.push_event, event_type, username, detail)
        except Exception as e:
            Logger.warn(f"Failed pushing Twitch event {event_type}: {e}")
    _enqueue_priority_event(event_type, username, detail)

def _push_queue_update() -> None:
    page = _ui_page
    if page is None or not hasattr(page, "refresh_queue_panel"):
        return
    try:
        page.after(0, page.refresh_queue_panel)
    except Exception as e:
        Logger.warn(f"Failed pushing Twitch queue update: {e}")

def _make_queue_item(
    author: str,
    content: str,
    *,
    priority: bool,
    user_id: str,
    role: str,
    event_type: str = "",
) -> dict:
    return {
        "id": next(_queue_ids),
        "author": author,
        "content": content,
        "priority": bool(priority),
        "user_id": user_id or "",
        "role": role or "",
        "event_type": event_type or "",
        "received_at": time.time(),
    }

def _priority_event_prompt(event_type: str, username: str, detail: str = "") -> str | None:
    event_type = (event_type or "").strip().lower()
    if event_type not in _PRIORITY_EVENT_TYPES:
        return None
    username = (username or "Someone").strip()
    detail = (detail or "").strip()

    if event_type == "raid":
        viewer_text = f" with {detail} viewers" if detail else ""
        return f"{username} just raided the channel{viewer_text}. Welcome the raiders warmly and invite them into the conversation."
    if event_type == "sub":
        return f"{username} just subscribed. Thank them warmly and make them feel appreciated."
    if event_type == "resub":
        months = f" for {detail}" if detail else ""
        return f"{username} just resubscribed{months}. Thank them and celebrate their continued support."
    if event_type == "gift_sub":
        target = f" to {detail}" if detail else ""
        return f"{username} just gifted a subscription{target}. Thank them for supporting the community."
    if event_type == "follow":
        return f"{username} just followed the channel. Welcome them briefly and warmly."
    if event_type == "redemption":
        reward = f": {detail}" if detail else ""
        return f"{username} redeemed a channel point reward{reward}. Acknowledge the redemption and respond appropriately."
    if event_type == "milestone":
        milestone = f": {detail}" if detail else ""
        return f"{username} triggered a stream milestone{milestone}. Acknowledge it in a friendly way."
    return None

def _enqueue_priority_event(event_type: str, username: str, detail: str = "") -> None:
    prompt = _priority_event_prompt(event_type, username, detail)
    bot = get_twitch_bot()
    if prompt is None or bot is None or getattr(bot, "start_chat_pause", False):
        return
    item = _make_queue_item(
        "Stream Event",
        prompt,
        priority=True,
        user_id="",
        role="event",
        event_type=event_type,
    )
    with _queue_lock:
        bot.prioritized_buffer.append(item)
    _push_queue_update()

def _item_author(item) -> str:
    if isinstance(item, dict):
        return str(item.get("author", ""))
    try:
        return str(item[0])
    except Exception:
        return ""

def _item_content(item) -> str:
    if isinstance(item, dict):
        return str(item.get("content", ""))
    try:
        return str(item[1])
    except Exception:
        return ""

def _snapshot_item(item, lane: str) -> dict:
    now = time.time()
    if isinstance(item, dict):
        received_at = float(item.get("received_at") or now)
        return {
            "id": item.get("id"),
            "author": _item_author(item),
            "content": _item_content(item),
            "priority": bool(item.get("priority")),
            "lane": lane,
            "event_type": item.get("event_type", ""),
            "age_seconds": max(0, int(now - received_at)),
        }

    return {
        "id": None,
        "author": _item_author(item),
        "content": _item_content(item),
        "priority": lane == "priority",
        "lane": lane,
        "event_type": "",
        "age_seconds": 0,
    }

def _load_blacklist_loop() -> None:
    while True:
        try:
            global blacklisted_users
            with open(FILE_PATH, "r") as f:
                blacklisted_users = f.read().splitlines()
        except Exception:
            Logger.notify(f"Could not read {FILE_PATH}")
        time.sleep(5)

class Bot(_commands.Bot):
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        bot_id: str,
        owner_id: str,
        policy: str = "latest-buffered",
    ):
        super().__init__(
            client_id=client_id,
            client_secret=client_secret,
            bot_id=bot_id,
            owner_id=owner_id,
            prefix="?",
        )

        self._owner_id = owner_id
        self._bot_id = bot_id
        self.policy = _normalize_policy(policy)
        self.max_backlog = 5
        self.keep_last = 4
        self.chat_buffer: list = []
        self.prioritized_buffer: list = []
        self.in_pipeline = False
        self.pipeline_timer = time.perf_counter()
        self.step_timer = time.perf_counter()
        self.start_chat_pause = False
        self.reader_task = None
        Thread(target=_load_blacklist_loop, daemon=True).start()

    async def setup_hook(self) -> None:
        Logger.notify("Registering Twitch tokens/subscriptions...")
        # Capture the running loop
        global _bot_loop
        _bot_loop = asyncio.get_running_loop()

        async def _await_step(label: str, coro, timeout: float = 30.0):
            try:
                result = await asyncio.wait_for(coro, timeout=timeout)
                return result
            except asyncio.TimeoutError:
                Logger.warn(f"TIMEOUT after {timeout:.0f}s: {label}")
                return None
            except Exception as e:
                Logger.warn(f"FAILED: {label}: {e}")
                return None

        bot_access = get_auth("twitch", "access_token") or ""
        bot_refresh = get_auth("twitch", "refresh_token") or ""
        channel_access = get_auth("twitch", "channel_access_token") or ""
        channel_refresh = get_auth("twitch", "channel_refresh_token") or ""
        same_account = str(self._bot_id) == str(self._owner_id)
        if same_account:
            token = bot_access or channel_access
            refresh = bot_refresh or channel_refresh
            if token:
                await _await_step("add single-account token", self.add_token(token, refresh), timeout=30.0)
            else:
                Logger.warn("Missing account token.")
        else:
            Logger.notify("Separate bot/channel mode detected.")
            if bot_access:
                await _await_step("add bot token", self.add_token(bot_access, bot_refresh), timeout=30.0)
            else:
                Logger.warn("Missing bot access token.")
            if channel_access:
                await _await_step("add channel token", self.add_token(channel_access, channel_refresh), timeout=30.0)
            else:
                Logger.warn("Missing channel access token.")
        if self.reader_task is None:
            self.reader_task = asyncio.create_task(self._read_buffer_loop())
        chat_sub = _eventsub.ChatMessageSubscription(broadcaster_user_id=self._owner_id, user_id=self._bot_id)
        await _await_step("subscribe ChatMessageSubscription", self.subscribe_websocket(payload=chat_sub), timeout=30.0)
        notif_sub = _eventsub.ChatNotificationSubscription(broadcaster_user_id=self._owner_id, user_id=self._bot_id)
        await _await_step("subscribe ChatNotificationSubscription", self.subscribe_websocket(payload=notif_sub), timeout=30.0)
        try:
            redemption_sub = _eventsub.ChannelPointsRedeemAddSubscription(broadcaster_user_id=self._owner_id)
            await _await_step("subscribe ChannelPointsRedeemAddSubscription", self.subscribe_websocket(payload=redemption_sub), timeout=30.0)
        except Exception as e:
            Logger.warn(f"Could not build redemption subscription: {e}")

    async def event_ready(self) -> None:
        Logger.notify(f"Ready as {self.bot_id}")

    async def event_message(self, payload: "twitchio.ChatMessage") -> None:  # type: ignore[name-defined]
        if self.start_chat_pause:
            return
        chatter = payload.chatter
        author  = chatter.name or ""
        content = payload.text or ""
        if "@" in content:
            return
        if author.lower() in blacklisted_users:
            return
        color = str(chatter.color) if chatter.color else ""
        if getattr(chatter, "broadcaster", False):
            role = "broadcaster"
        elif getattr(chatter, "mod", False):
            role = "mod"
        elif getattr(chatter, "subscriber", False):
            role = "sub"
        elif getattr(chatter, "is_first", False):
            role = "first"
        else:
            role = ""
        user_id = str(getattr(chatter, "id", "") or "")
        _push_chat(author, color, role, content, user_id=user_id)
        if getattr(chatter, "is_first", False):
            _push_event("first_msg", author)
        reward = getattr(payload, "channel_points_custom_reward_id", None)
        if reward:
            reward_title = getattr(getattr(payload, "channel_points_custom_reward", None), "title", "Channel Point Reward")
            _push_event("redemption", author, reward_title)

        if has_ignored_prefix(content):
            Logger.quiet_print(f"Twitch message ignored by prefix: {author}: {content}")
            return

        priority = getattr(payload, "highlighted", False)
        item = _make_queue_item(
            author,
            content,
            priority=priority,
            user_id=user_id,
            role=role,
        )
        with _queue_lock:
            if priority:
                self.prioritized_buffer.append(item)
            else:
                self.chat_buffer.append(item)
        _push_queue_update()

    async def event_chat_notification(self, payload) -> None:
        try:
            notice = getattr(payload, "notice_type", "") or ""
            chatter = getattr(payload, "chatter", None)
            username = (
                getattr(chatter, "name", None)
                or getattr(chatter, "login", None)
                or getattr(chatter, "display_name", None)
                or "anonymous"
            )

            if notice == "sub":
                _push_event("sub", username)

            elif notice == "resub":
                resub = getattr(payload, "resub", None)
                months = str(getattr(resub, "cumulative_months", "")) if resub else ""
                _push_event("resub", username, f"{months} months" if months else "")

            elif notice == "raid":
                raid = getattr(payload, "raid", None)
                count = str(getattr(raid, "viewer_count", "?")) if raid else "?"
                raider = getattr(raid, "raider_name", username) if raid else username
                _push_event("raid", raider, count)

            elif notice == "sub_gift":
                gift = getattr(payload, "sub_gift", None)
                recipient = getattr(gift, "recipient_name", "someone") if gift else "someone"
                _push_event("gift_sub", username, recipient)

            elif notice == "community_sub_gift":
                comm = getattr(payload, "community_sub_gift", None)
                total = str(getattr(comm, "total", "?")) if comm else "?"
                _push_event("gift_sub", username, f"{total} subs gifted")

            elif notice == "gift_paid_upgrade":
                upgrade = getattr(payload, "gift_paid_upgrade", None)
                gifter = getattr(upgrade, "gifter_name", "anonymous") if upgrade else "anonymous"
                _push_event("milestone", username, f"Continuing gift sub from {gifter}")

            else:
                _push_event("milestone", username, notice or "Chat notification")

        except Exception as e:
            Logger.warn(f"Failed handling chat notification: {e}")

    async def event_notification(self, payload) -> None:
        await self.event_chat_notification(payload)

    async def event_custom_redemption_add(self, payload) -> None:
        try:
            user = getattr(payload, "user", None)
            reward = getattr(payload, "reward", None)

            username = (
                getattr(user, "name", None)
                or getattr(user, "login", None)
                or getattr(user, "display_name", None)
                or "unknown"
            )

            reward_title = (
                getattr(reward, "title", None)
                or getattr(payload, "title", None)
                or "Channel Point Reward"
            )

            _push_event("redemption", username, reward_title)
            Logger.notify(f"Redemption user={username!r} reward={reward_title!r}")

        except Exception as e:
            Logger.warn(f"Failed handling redemption event: {e}")

    async def event_channel_points_redeem_add(self, payload) -> None:
        await self.event_custom_redemption_add(payload)

    def _next_item(self):
        with _queue_lock:
            policy = _normalize_policy(self.policy)
            self.policy = policy
            if policy == "latest":
                item = _latest_message(self.chat_buffer, self.prioritized_buffer)
            elif policy == "all":
                item = _view_all(self.chat_buffer, self.prioritized_buffer,
                 max_backlog=self.max_backlog)
            else:
                item = _latest_in_queue(self.chat_buffer, self.prioritized_buffer, keep_last=self.keep_last)
        if item:
            _push_queue_update()
        return item

    async def _read_buffer_loop(self):
        from files.main_loop.main_loop import message_queue
        while True:
            if self.start_chat_pause or self.in_pipeline:
                await asyncio.sleep(0.1)
                continue
            item = self._next_item()
            if item:
                author = _item_author(item)
                msg = _item_content(item)
                Logger.quiet_print(f"{author}: {msg}")
                turn_id = (
                    f"twitch-{item.get('id')}"
                    if isinstance(item, dict) and item.get("id") is not None
                    else f"twitch-{uuid.uuid4()}"
                )
                await self._finalize_pipeline(
                    f"{author}: {msg}",
                    meta={
                        "turn_id": turn_id,
                        "source": "twitch",
                    },
                )
            else:
                await asyncio.sleep(0.1)

    async def _finalize_pipeline(self, text: str, meta: dict | None = None):
        from files.main_loop.main_loop import message_queue
        self.in_pipeline = True
        self.pipeline_timer = time.perf_counter()
        self.step_timer = time.perf_counter()
        try:
            resp_q = queue.Queue(maxsize=1)
            message_queue.put((text, True, resp_q, meta or {"source": "twitch"}))
            try:
                await asyncio.to_thread(resp_q.get, True, 180)
            except queue.Empty:
                Logger.warn("Timed out waiting for main loop response.")
        finally:
            self.in_pipeline = False


def _latest_message(buffer, priority):
    if priority:
        return priority.pop(0)
    if not buffer:
        return None
    msg = buffer[-1]
    buffer.clear()
    return msg

def _latest_in_queue(buffer, priority, keep_last=4):
    if priority:
        return priority.pop(0)
    if not buffer:
        return None
    msg = buffer.pop()
    while len(buffer) > keep_last:
        buffer.pop(0)
    return msg

def _view_all(buffer, priority, max_backlog=5):
    if priority:
        return priority.pop(0)
    if not buffer:
        return None
    return buffer.pop(0)

def read_chat() -> None:
    global twitchbot, read_chat_running, read_chat_thread, twitch_channel, twitch_access_token
    Logger.notify("Starting Chat Fetching")

    policy = _normalize_policy(get_settings("TWITCH_POLICY"))
    client_id     = get_auth("twitch", "client_id") or ""
    client_secret = get_auth("twitch", "client_secret") or ""
    bot_id        = get_auth("twitch", "bot_id") or ""
    owner_id      = get_auth("twitch", "owner_id") or ""

    if not all([client_id, client_secret, bot_id, owner_id]):
        Logger.notify(
            "Missing required auth keys: "
            "client_id, client_secret, bot_id, owner_id. "
            "Please set these in Settings → Twitch Authentication."
        )
        return

    twitchbot = Bot(
        client_id=client_id,
        client_secret=client_secret,
        bot_id=bot_id,
        owner_id=owner_id,
        policy=policy,
    )
    read_chat_thread = Thread(target=runBot, daemon=True)
    read_chat_thread.start()
    read_chat_running = True


def runBot() -> None:
    Logger.notify("Chat thread started.")
    async def _runner():
        async with twitchbot:
            await twitchbot.start()
    asyncio.run(_runner())

def get_twitch_bot():
    return twitchbot


def stop_read_chat(timeout: float = 2.0) -> None:
    global twitchbot, read_chat_running, _bot_loop
    read_chat_running = False
    bot = twitchbot
    loop = _bot_loop

    async def _close_bot():
        for name in ("close", "stop"):
            fn = getattr(bot, name, None)
            if callable(fn):
                result = fn()
                if asyncio.iscoroutine(result):
                    await result
                return

    if bot is not None and loop is not None and loop.is_running():
        try:
            fut = asyncio.run_coroutine_threadsafe(_close_bot(), loop)
            fut.result(timeout=timeout)
        except Exception as e:
            Logger.warn(f"Twitch chat shutdown failed: {e}")
    twitchbot = None

def set_twitch_policy(policy: str) -> str:
    normalized = _normalize_policy(policy)
    bot = get_twitch_bot()
    if bot is not None:
        bot.policy = normalized
        Logger.notify(f"Twitch queue policy set to {normalized}.")
    return normalized

def list_pending_messages() -> list[dict]:
    bot = get_twitch_bot()
    if bot is None:
        return []
    with _queue_lock:
        priority_items = [
            _snapshot_item(item, "priority")
            for item in list(getattr(bot, "prioritized_buffer", []))
        ]
        standard_items = [
            _snapshot_item(item, "standard")
            for item in list(getattr(bot, "chat_buffer", []))
        ]
    return priority_items + standard_items

def remove_pending_message(message_id) -> bool:
    bot = get_twitch_bot()
    if bot is None:
        return False
    try:
        wanted_id = int(message_id)
    except Exception:
        return False

    with _queue_lock:
        for buffer in (bot.prioritized_buffer, bot.chat_buffer):
            for idx, item in enumerate(list(buffer)):
                if isinstance(item, dict) and item.get("id") == wanted_id:
                    del buffer[idx] # This will remove the chat message by the ID associated with it. Be sure to keep an eye on chat to ensure you don't miss it.
                    _push_queue_update()
                    return True
    return False

def set_twitch_pause(value: bool) -> None:
    bot = get_twitch_bot()
    if bot is not None:
        bot.start_chat_pause = bool(value)

def pause_chat_twitch(paused: bool) -> None:
    global twitchbot
    if twitchbot:
        twitchbot.start_chat_pause = paused
        Logger.notify(f"Chat reading {'paused' if paused else 'resumed'}.")

def fetch_user_profile(user_id: str) -> dict | None:
    bot = get_twitch_bot()
    loop = _bot_loop
    if bot is None or loop is None or not user_id:
        Logger.warn(
            f"Profile skipped: bot={bot is not None} "
            f"loop={loop is not None} user_id={user_id!r}"
        )
        return None

    def extract(obj) -> str:
        if obj is None:
            return ""
        if isinstance(obj, str):
            return obj
        for attr in ("url", "_url", "src"):
            val = getattr(obj, attr, None)
            if isinstance(val, str) and val:
                return val
        try:
            return str(obj)
        except Exception:
            return ""

    def attached_urls(user_obj) -> dict | None:
        if user_obj is None:
            return None
        profile = (
            getattr(user_obj, "profile_image", None)
            or getattr(user_obj, "profile_image_url", None)
        )
        offline = (
            getattr(user_obj, "offline_image", None)
            or getattr(user_obj, "offline_image_url", None)
        )
        result = {
            "profile_image_url": extract(profile),
            "offline_image_url": extract(offline),
        }
        return result

    async def _do():
        try:
            partial = bot.create_partialuser(id=str(user_id))
            user = await partial.user()
            return attached_urls(user)
        except TypeError:
            try:
                partial = bot.create_partialuser(str(user_id))
                user = await partial.user()
                return attached_urls(user)
            except Exception as e:
                Logger.warn(f"Positional fallback failed: {e}")
        except Exception as e:
            Logger.warn(f"User profile loader route failed: {e}")

        try:
            users = await bot.fetch_users(ids=[str(user_id)])
            if users:
                return attached_urls(users[0])
        except Exception as e:
            Logger.warn(f"fetch_users fallback failed for {user_id}: {e}")

        return None

    try:
        fut = asyncio.run_coroutine_threadsafe(_do(), loop)
        result = fut.result(timeout=10)
        return result
    except Exception as e:
        Logger.warn(f"User profile fetching failed: {e}")
        return None


def main():
    from files.main_loop.main_loop import start_loop
    start_loop()
    read_chat()

if __name__ == "__main__":
    main()
