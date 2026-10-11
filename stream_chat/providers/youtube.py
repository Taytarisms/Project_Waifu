import threading
import time
from collections import Counter
from typing import Any
import requests

from files.stream_chat import StreamChatEvent, StreamChatMessage, get_stream_chat_hub
from files.stream_chat.providers.base import StreamChatProvider
from files.system_setup.settings import get_auth, get_settings, save_auth
from files.system_setup.system_logger import Logger

API_ROOT = "https://www.googleapis.com/youtube/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"

PRIORITY_TYPES = {
    "superChatEvent",
    "superStickerEvent",
    "newSponsorEvent",
    "memberMilestoneChatEvent",
    "membershipGiftingEvent",
    "giftMembershipReceivedEvent",
}


class YouTubeProvider(StreamChatProvider):
    provider_name = "youtube"

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._session = requests.Session()
        self._running = False
        self._live_chat_id = ""
        self._video_id = ""
        self._lifecycle_lock = threading.Lock()
        self._page_token = ""
        self._first_batch = True
        self._failures = 0
        self._request_counts = Counter()

    def is_running(self) -> bool:
        return self._running and bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        with self._lifecycle_lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if self.is_running():
            return
        if not get_auth("youtube", "refresh_token") and not get_auth("youtube", "access_token"):
            get_stream_chat_hub().update_provider_status(
                "youtube", "error", "Authorize a YouTube broadcaster account first"
            )
            return
        self._stop_event = threading.Event()
        self._live_chat_id = ""
        self._page_token = ""
        self._first_batch = True
        self._failures = 0
        self._request_counts.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            name="youtube-live-chat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        alive = bool(thread and thread.is_alive())
        if not alive:
            self._thread = None
            self._running = False
        get_stream_chat_hub().update_provider_status(
            "youtube", "stopping" if alive else "disconnected",
            "Waiting for the current request to finish" if alive else "",
        )

    def _run(self) -> None:
        hub = get_stream_chat_hub()
        account = get_auth("youtube", "channel_name") or ""
        hub.update_provider_status("youtube", "connecting", "Looking for an active broadcast", account)
        try:
            while not self._stop_event.is_set():
                try:
                    access_token = self._access_token()
                    if not self._live_chat_id:
                        self._video_id, self._live_chat_id = self._discover_live_chat(access_token)
                        if not self._live_chat_id:
                            if not bool(get_settings("YOUTUBE_AUTO_MONITOR")):
                                hub.update_provider_status(
                                    "youtube", "waiting", "No active live chat found", account
                                )
                                return
                            hub.update_provider_status(
                                "youtube",
                                "waiting",
                                "No active live chat; checking again",
                                account,
                            )
                            self._stop_event.wait(300.0)
                            continue
                    hub.update_provider_status(
                        "youtube",
                        "connected",
                        f"Monitoring video {self._video_id}" if self._video_id else "Monitoring live chat",
                        account,
                    )
                    self._poll_chat(access_token)
                except requests.HTTPError as exc:
                    status = exc.response.status_code if exc.response is not None else "HTTP"
                    detail = self._api_error(exc)
                    reason = self._api_reason(exc)
                    if self._stop_event.is_set():
                        break
                    self._failures += 1
                    if status == 401 and self._failures == 1:
                        save_auth("youtube", "token_expires_at", 0)
                    elif reason not in {"rateLimitExceeded", "userRateLimitExceeded"} and status != 429 and not (isinstance(status, int) and status >= 500):
                        hub.update_provider_status("youtube", "error", detail, account)
                        Logger.warn(f"YouTube stopped: {detail}. Resolve the error before reconnecting.")
                        return
                    hub.update_provider_status("youtube", "reconnecting", detail, account)
                    Logger.warn(f"YouTube chat API error: {detail}")
                    self._stop_event.wait(min(300.0, 8.0 * 2 ** min(self._failures - 1, 6)))
                except Exception as exc:
                    if self._stop_event.is_set():
                        break
                    if not isinstance(exc, requests.RequestException):
                        hub.update_provider_status("youtube", "error", str(exc), account)
                        Logger.warn(f"YouTube reader stopped: {exc}")
                        return
                    self._failures += 1
                    hub.update_provider_status("youtube", "reconnecting", str(exc), account)
                    Logger.warn(f"YouTube chat reader error: {exc}")
                    self._stop_event.wait(min(300.0, 8.0 * 2 ** min(self._failures - 1, 6)))
        finally:
            self._running = False
            Logger.print(f"YouTube session API request counts: {dict(self._request_counts)}")
            if self._stop_event.is_set():
                hub.update_provider_status("youtube", "disconnected")

    def _access_token(self) -> str:
        token = get_auth("youtube", "access_token") or ""
        try:
            expires_at = float(get_auth("youtube", "token_expires_at") or 0)
        except Exception:
            expires_at = 0
        if token and expires_at > time.time() + 60:
            return token

        refresh_token = get_auth("youtube", "refresh_token") or ""
        client_id = get_auth("youtube", "client_id") or ""
        client_secret = get_auth("youtube", "client_secret") or ""
        if not refresh_token or not client_id:
            if token:
                return token
            raise RuntimeError("YouTube access token is unavailable; authorize the account again")

        data = {
            "client_id": client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        if client_secret:
            data["client_secret"] = client_secret
        response = self._session.post(TOKEN_URL, data=data, timeout=30)
        response.raise_for_status()
        payload = response.json()
        token = str(payload.get("access_token") or "")
        if not token:
            raise RuntimeError("Google did not return a refreshed access token")
        save_auth("youtube", "access_token", token)
        save_auth("youtube", "token_expires_at", time.time() + int(payload.get("expires_in") or 3600))
        return token

    def _get(self, path: str, token: str, **params) -> dict[str, Any]:
        self._request_counts[path] += 1
        if sum(self._request_counts.values()) % 100 == 0:
            Logger.print(f"YouTube session API request counts: {dict(self._request_counts)}")
        response = self._session.get(
            f"{API_ROOT}/{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=45,
        )
        response.raise_for_status()
        self._failures = 0
        return response.json()

    def _discover_live_chat(self, token: str) -> tuple[str, str]:
        configured_video = str(get_settings("YOUTUBE_VIDEO_ID") or "").strip()
        if configured_video:
            data = self._get(
                "videos",
                token,
                part="liveStreamingDetails",
                id=configured_video,
            )
            items = data.get("items") or []
            details = items[0].get("liveStreamingDetails", {}) if items else {}
            return configured_video, str(details.get("activeLiveChatId") or "")

        data = self._get(
            "liveBroadcasts",
            token,
            part="id,snippet",
            broadcastStatus="active",
            broadcastType="all",
            maxResults=5,
        )
        items = data.get("items") or []
        if not items:
            return "", ""
        broadcast = items[0]
        return str(broadcast.get("id") or ""), str(broadcast.get("snippet", {}).get("liveChatId") or "")

    def _poll_chat(self, token: str) -> None:
        while not self._stop_event.is_set() and self._live_chat_id:
            token = self._access_token()
            if self._stop_event.is_set():
                return
            params = {
                "liveChatId": self._live_chat_id,
                "part": "id,snippet,authorDetails",
                "maxResults": 200,
            }
            if self._page_token:
                params["pageToken"] = self._page_token
            data = self._get("liveChat/messages", token, **params)
            if self._stop_event.is_set():
                return
            self._page_token = str(data.get("nextPageToken") or self._page_token)
            items = data.get("items") or []
            if self._first_batch:
                # The first list response can contain historical messages. Start at its continuation token so the AI only answers new arrivals but also keeping it contextually aware...
                self._first_batch = False
            else:
                for item in items:
                    self._handle_item(item)
            if data.get("offlineAt"):
                self._live_chat_id = ""
            if not self._live_chat_id:
                self._page_token = ""
                self._first_batch = True
                self._stop_event.wait(300.0)
                return
            interval = max(5.0, float(data.get("pollingIntervalMillis") or 5000) / 1000.0)
            self._stop_event.wait(interval)

    def _handle_item(self, item: dict[str, Any]) -> None:
        snippet = item.get("snippet") or {}
        event_type = str(snippet.get("type") or "")
        if event_type == "chatEndedEvent":
            self._live_chat_id = ""
            return
        message = self.normalize_chat_message(item)
        if message is None:
            return
        hub = get_stream_chat_hub()
        hub.emit_message(message)
        if event_type in PRIORITY_TYPES:
            hub.emit_event(
                StreamChatEvent(
                    provider="youtube",
                    event_type=self._normalized_event_type(event_type),
                    username=message.author_name,
                    detail=message.text,
                    native_id=message.native_id,
                    raw=item,
                )
            )
        hub.enqueue(message)

    @staticmethod
    def normalize_chat_message(item: dict[str, Any]) -> StreamChatMessage | None:
        snippet = item.get("snippet") or {}
        author = item.get("authorDetails") or {}
        event_type = str(snippet.get("type") or "")
        if event_type == "chatEndedEvent" or not snippet.get("hasDisplayContent", True):
            return None
        text = str(snippet.get("displayMessage") or "").strip()
        if not text:
            return None
        role = ""
        if author.get("isChatOwner"):
            role = "broadcaster"
        elif author.get("isChatModerator"):
            role = "mod"
        elif author.get("isChatSponsor"):
            role = "sub"
        return StreamChatMessage(
            provider="youtube",
            native_id=str(item.get("id") or ""),
            author_name=str(author.get("displayName") or "YouTube Viewer"),
            author_id=str(author.get("channelId") or snippet.get("authorChannelId") or ""),
            channel_id=get_auth("youtube", "channel_id") or "",
            channel_name=get_auth("youtube", "channel_name") or "",
            text=text,
            role=role,
            priority=event_type in PRIORITY_TYPES,
            event_type=event_type,
            raw=item,
        )

    @staticmethod
    def _normalized_event_type(event_type: str) -> str:
        return {
            "superChatEvent": "super_chat",
            "superStickerEvent": "super_sticker",
            "newSponsorEvent": "sub",
            "memberMilestoneChatEvent": "milestone",
            "membershipGiftingEvent": "gift_sub",
            "giftMembershipReceivedEvent": "gift_sub",
        }.get(event_type, "milestone")

    @staticmethod
    def _api_reason(exc: requests.HTTPError) -> str:
        try:
            errors = exc.response.json().get("error", {}).get("errors", [])
            return str(errors[0].get("reason", "")) if errors else ""
        except Exception:
            return ""

    @staticmethod
    def _api_error(exc: requests.HTTPError) -> str:
        try:
            payload = exc.response.json()
            errors = payload.get("error", {})
            return str(errors.get("message") or exc)
        except Exception:
            return str(exc)


_provider: YouTubeProvider | None = None
_provider_lock = threading.Lock()


def get_youtube_provider() -> YouTubeProvider:
    global _provider
    with _provider_lock:
        if _provider is None:
            _provider = YouTubeProvider()
        return _provider


def start_youtube() -> None:
    get_youtube_provider().start()


def stop_youtube() -> None:
    get_youtube_provider().stop()
