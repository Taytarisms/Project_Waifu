from __future__ import annotations

import base64
import json
import sys
import threading
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote, urlsplit, urlunsplit


PROTOCOL_VERSION = 1
POLICY_VIOLATION = 1008
NORMAL_CLOSURE = 1000


@dataclass(slots=True)
class Payload:
    name: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "content": self.content}


@dataclass(slots=True)
class ProtocolMessageUnknown:
    version: int
    type: str
    sender: str
    to: list[str]
    payload: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "type": self.type,
            "from": self.sender,
            "to": self.to,
            "payload": self.payload,
        }


@dataclass(slots=True)
class ProtocolMessage:
    version: int
    type: str
    sender: str
    to: list[str]
    payload: Payload

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "type": self.type,
            "from": self.sender,
            "to": self.to,
            "payload": self.payload.to_dict(),
        }


class WebsocketCollabClient:
    """Synchronous protocol-v1 client with a background receive thread."""

    PROTOCOL_VERSION = PROTOCOL_VERSION

    def __init__(
        self,
        *,
        max_retries: int = 5,
        reconnect_base_delay: float = 1.0,
        reconnect_max_delay: float = 30.0,
        max_message_bytes: int = 64 * 1024,
        open_timeout: float = 10.0,
        close_timeout: float = 5.0,
        connect_factory: Callable[..., Any] | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if reconnect_base_delay <= 0 or reconnect_max_delay <= 0:
            raise ValueError("reconnect delays must be positive")
        if max_message_bytes < 1024:
            raise ValueError("max_message_bytes must be at least 1024")

        self.max_retries = max_retries
        self.reconnect_base_delay = reconnect_base_delay
        self.reconnect_max_delay = reconnect_max_delay
        self.max_message_bytes = max_message_bytes
        self.open_timeout = open_timeout
        self.close_timeout = close_timeout
        self.__connect_factory = connect_factory

        self.connected = False
        self.channel_id = ""
        self.server_url = ""

        self.__user = ""
        self.__password = ""
        self.__socket: Any | None = None
        self.__thread: threading.Thread | None = None
        self.__stop_event = threading.Event()
        self.__state_lock = threading.RLock()
        self.__send_lock = threading.Lock()

        self.__listeners_all_msg: list[Callable[[ProtocolMessage], None]] = []
        self.__listeners_text_msg: list[Callable[[ProtocolMessage], None]] = []
        self.__listeners_data_msg: list[Callable[[ProtocolMessage], None]] = []
        self.__listeners_connection_state: list[Callable[[bool], None]] = []

    @staticmethod
    def build_room_url(url: str, channel_id: str) -> str:
        if not isinstance(url, str) or not url:
            raise ValueError("url must be a non-empty string")
        if not WebsocketCollabClient._valid_room_id(channel_id):
            raise ValueError(
                "channel_id must contain 4-64 lowercase letters or digits"
            )

        parsed = urlsplit(url)
        if parsed.scheme not in ("ws", "wss"):
            raise ValueError("url must use the ws or wss scheme")
        if not parsed.hostname:
            raise ValueError("url must include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("credentials must not be embedded in the URL")
        if parsed.query or parsed.fragment:
            raise ValueError("url must not contain a query or fragment")

        base_path = parsed.path.rstrip("/")
        room_path = f"{base_path}/{quote(channel_id, safe='')}"
        return urlunsplit((parsed.scheme, parsed.netloc, room_path, "", ""))

    @staticmethod
    def create_basic_authorization(user: str, password: str) -> str:
        if not isinstance(user, str) or not user:
            raise ValueError("user must be a non-empty string")
        if ":" in user:
            raise ValueError("user cannot contain a colon")
        if not isinstance(password, str) or not password:
            raise ValueError("password must be a non-empty string")

        encoded = base64.b64encode(f"{user}:{password}".encode("utf-8"))
        return f"Basic {encoded.decode('ascii')}"

    @staticmethod
    def parse_protocol_message(raw_message: str) -> ProtocolMessage | None:
        if not isinstance(raw_message, str):
            return None

        try:
            value = json.loads(raw_message)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

        if not isinstance(value, dict):
            return None
        if type(value.get("version")) is not int or value["version"] != PROTOCOL_VERSION:
            return None
        if not isinstance(value.get("type"), str) or not value["type"]:
            return None
        if not isinstance(value.get("from"), str) or not value["from"]:
            return None

        recipients = value.get("to")
        if not isinstance(recipients, list) or not 1 <= len(recipients) <= 64:
            return None
        if not all(isinstance(item, str) and item for item in recipients):
            return None

        payload_value = value.get("payload")
        if not isinstance(payload_value, dict):
            return None
        if not isinstance(payload_value.get("name"), str):
            return None
        if not isinstance(payload_value.get("content"), str):
            return None

        return ProtocolMessage(
            version=PROTOCOL_VERSION,
            type=value["type"],
            sender=value["from"],
            to=list(recipients),
            payload=Payload(payload_value["name"], payload_value["content"]),
        )

    def connect(self, url: str, channel_id: str, user: str, password: str) -> None:
        """Connect to a room and start the background receive loop."""
        room_url = self.build_room_url(url, channel_id)
        self.create_basic_authorization(user, password)

        with self.__state_lock:
            if self.__socket is not None or self.connected:
                raise RuntimeError("this client is already connected or connecting")
            self.server_url = room_url
            self.channel_id = channel_id
            self.__user = user
            self.__password = password
            self.__stop_event.clear()

        try:
            self.__open_websocket()
        except Exception:
            self.__clear_server_data()
            raise

        self.__thread = threading.Thread(
            target=self.__listener,
            name="websocket-collab-listener",
            daemon=True,
        )
        self.__thread.start()

    def disconnect(self) -> None:
        """Stop reconnecting, close the socket, and join the receive thread."""
        self.__stop_event.set()
        with self.__state_lock:
            socket = self.__socket

        if socket is not None:
            try:
                socket.close(code=NORMAL_CLOSURE, reason="Client disconnecting.")
            except Exception:
                pass

        thread = self.__thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self.close_timeout + 1)

        self.__clear_server_data()
        self.__notify_connection_state(False)
        self.__wcc_log("Disconnected from WebSocket Collab Server.")

    def send(
        self,
        msg_type: str,
        payload: dict[str, Any],
        to: list[str] | None = None,
    ) -> None:
        if not isinstance(msg_type, str) or not msg_type:
            raise ValueError("msg_type must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dictionary")
        if not isinstance(payload.get("name"), str):
            raise ValueError("payload.name must be a string")
        if not isinstance(payload.get("content"), str):
            raise ValueError("payload.content must be a string")

        recipients = ["all"] if to is None else to
        if not isinstance(recipients, list) or not 1 <= len(recipients) <= 64:
            raise ValueError("to must contain 1-64 recipients")
        if not all(isinstance(name, str) and name for name in recipients):
            raise ValueError("every recipient must be a non-empty string")

        message = json.dumps(
            {
                "version": PROTOCOL_VERSION,
                "type": msg_type,
                "from": self.__user,
                "to": recipients,
                "payload": payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(message.encode("utf-8")) > self.max_message_bytes:
            raise ValueError("serialized message exceeds max_message_bytes")

        with self.__send_lock:
            with self.__state_lock:
                socket = self.__socket
                connected = self.connected
            if socket is None or not connected:
                raise RuntimeError("the collab connection is closed")
            socket.send(message)

    def send_text(
        self,
        sender_name: str,
        content: str,
        to: list[str] | None = None,
    ) -> None:
        self.send("message", {"name": sender_name, "content": content}, to)

    def send_data(
        self,
        data_name: str,
        data: str,
        to: list[str] | None = None,
    ) -> None:
        self.send("data", {"name": data_name, "content": data}, to)

    def on_all_messages(self, listener: Callable[[ProtocolMessage], None]) -> None:
        self.__add_listener(self.__listeners_all_msg, listener)

    def on_text_message(self, listener: Callable[[ProtocolMessage], None]) -> None:
        self.__add_listener(self.__listeners_text_msg, listener)

    def on_data_message(self, listener: Callable[[ProtocolMessage], None]) -> None:
        self.__add_listener(self.__listeners_data_msg, listener)

    def on_connection_state_changed(self, listener: Callable[[bool], None]) -> None:
        self.__add_listener(self.__listeners_connection_state, listener)

    def remove_all_listeners(self) -> None:
        self.__listeners_all_msg.clear()
        self.__listeners_text_msg.clear()
        self.__listeners_data_msg.clear()
        self.__listeners_connection_state.clear()

    def __open_websocket(self) -> None:
        connect_factory = self.__connect_factory
        if connect_factory is None:
            try:
                from websockets.sync.client import connect as connect_factory
            except ImportError as error:
                raise RuntimeError(
                    "Install the 'websockets' dependency before connecting"
                ) from error

        authorization = self.create_basic_authorization(
            self.__user,
            self.__password,
        )
        socket = connect_factory(
            self.server_url,
            additional_headers={"Authorization": authorization},
            open_timeout=self.open_timeout,
            close_timeout=self.close_timeout,
            ping_interval=20,
            ping_timeout=20,
            max_size=self.max_message_bytes,
        )

        with self.__state_lock:
            old_socket = self.__socket
            self.__socket = socket
            self.connected = True

        if old_socket is not None and old_socket is not socket:
            try:
                old_socket.close()
            except Exception:
                pass

        self.__notify_connection_state(True)

    def __listener(self) -> None:
        while not self.__stop_event.is_set():
            with self.__state_lock:
                socket = self.__socket
            if socket is None:
                return

            try:
                received = socket.recv()
            except Exception as error:
                with self.__state_lock:
                    self.connected = False
                self.__notify_connection_state(False)

                if self.__stop_event.is_set():
                    return
                close_code = getattr(error, "code", None)
                if close_code == NORMAL_CLOSURE:
                    self.__wcc_log("Server closed the session normally.")
                    self.__clear_server_data()
                    return

                if close_code == POLICY_VIOLATION:
                    self.__wcc_err("Server rejected the session:", error)
                    self.__clear_server_data()
                    return
                if not self.__reconnect():
                    self.__clear_server_data()
                    return
                continue

            if isinstance(received, bytes):
                self.__wcc_err(
                    "Ignored a binary message; protocol version 1 requires JSON text."
                )
                continue
            if not isinstance(received, str):
                continue
            if len(received.encode("utf-8")) > self.max_message_bytes:
                self.__wcc_err("Ignored an oversized message from the collab server.")
                continue

            message = self.parse_protocol_message(received)
            if message is None:
                self.__wcc_err("Ignored a malformed protocol-v1 message.")
                continue

            self.__call_listeners(self.__listeners_all_msg, message)
            if "all" not in message.to and self.__user not in message.to:
                continue
            if message.sender == self.__user:
                continue

            if message.type == "message":
                self.__call_listeners(self.__listeners_text_msg, message)
            elif message.type == "data":
                self.__call_listeners(self.__listeners_data_msg, message)

    def __reconnect(self) -> bool:
        for attempt in range(self.max_retries):
            delay = min(
                self.reconnect_base_delay * (2**attempt),
                self.reconnect_max_delay,
            )
            self.__wcc_log(f"Reconnecting in {delay:g} seconds...")
            if self.__stop_event.wait(delay):
                return False

            try:
                self.__open_websocket()
                self.__wcc_log("Reconnected successfully.")
                return True
            except Exception as error:
                self.__wcc_err("Reconnection attempt failed:", error)

        self.__wcc_err("Maximum reconnect attempts reached.")
        return False

    def __clear_server_data(self) -> None:
        with self.__state_lock:
            self.connected = False
            self.channel_id = ""
            self.server_url = ""
            self.__user = ""
            self.__password = ""
            self.__socket = None
            self.__thread = None

    @staticmethod
    def _valid_room_id(channel_id: Any) -> bool:
        return (
            isinstance(channel_id, str)
            and 4 <= len(channel_id) <= 64
            and all("a" <= char <= "z" or "0" <= char <= "9" for char in channel_id)
        )

    @staticmethod
    def __add_listener(listeners: list[Callable], listener: Callable) -> None:
        if not callable(listener):
            raise TypeError("listener must be callable")
        listeners.append(listener)

    def __call_listeners(
        self,
        listeners: list[Callable[[ProtocolMessage], None]],
        message: ProtocolMessage,
    ) -> None:
        for listener in tuple(listeners):
            try:
                listener(message)
            except Exception as error:
                self.__wcc_err("A client callback failed:", error)

    def __notify_connection_state(self, connected: bool) -> None:
        for listener in tuple(self.__listeners_connection_state):
            try:
                listener(connected)
            except Exception as error:
                self.__wcc_err("A connection-state callback failed:", error)

    @staticmethod
    def __wcc_log(*args: Any) -> None:
        print("WCC:", *args)

    @staticmethod
    def __wcc_err(*args: Any) -> None:
        print("WCC:", *args, file=sys.stderr)
