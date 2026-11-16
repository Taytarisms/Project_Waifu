"""Application connector for the Discord AI VTuber collab network.

Requires Python 3.10+ and the dependencies in pip_requirements.txt.
Distribute this file alongside lib/wcc.py.

Pass connection settings directly, or set COLLAB_ROOM_ID, COLLAB_USER and
COLLAB_PASSWORD using credentials obtained
from the Discord collab bot (/signup or /resetpassword, and /create or /rooms).
Never use a Discord bot token here. Keep credentials out of distributed files.
COLLAB_WS_URL optionally overrides the endpoint; COLLAB_DISPLAY_NAME sets the
VTuber's display name (defaults to the collab account name).

Integration:
    from example import CollabConnector

    collab = CollabConnector(on_text=your_application.enqueue_collab_message)
    collab.connect()  # Once during application startup.
    # At your AI's response-completion point:
    collab.send_text(completed_response)
    # During application shutdown, including exception cleanup:
    collab.disconnect()

Legacy-style settings (no environment variables or .env file required):
    WS_URL = "wss://collab.tayderbot.com/ws"
    CHANNEL_ID = "your-room-id"
    USER = "your-collab-account"
    PASS = "your-collab-password"

    collab = CollabConnector(on_text=your_application.enqueue_collab_message)
    collab.connect(
        url=WS_URL,
        channel_id=CHANNEL_ID,
        user=USER,
        password=PASS,
        display_name="Your VTuber",
    )

Replace these placeholders with your own settings in your private script.
Remove real credentials before sharing that script with other developers.
Explicit arguments override environment settings. Only None/omitted arguments
fall back to the environment; empty explicit values are rejected. This module
does not load .env files automatically.

The on_text callback receives a ProtocolMessage, not a plain string. Adapt it
to your application's input queue, e.g. using message.payload.name/content.
This module does not connect on import or start an interactive console.
"""

import os
from typing import Callable
from pathlib import Path
import sys

from files.lib.wcc import ProtocolMessage, WebsocketCollabClient

ROOT = Path(__file__).resolve().parents[0]
sys.path.insert(0, str(ROOT))

DEFAULT_SERVER_URL = "wss://collab.tayderbot.com/ws"
CHANNEL_ID = ""
USER = ""
PASS = ""
DISPLAY_NAME= ""

def connection_setting(
    explicit: str | None, environment: str, default: str | None = None
) -> str:
    value = explicit if explicit is not None else os.environ.get(environment, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Pass a non-empty value or set {environment} before connecting.")
    return value


class CollabConnector:
    """One connection per application/participant, using a distinct account.

    Callbacks run on the receive thread: enqueue work instead of running
    inference/TTS there. Text callbacks exclude self-messages and messages
    addressed to other accounts. All room peers still receive the envelope:
    recipient filtering is not confidentiality.

    The host application owns its event loop and must stay running. The
    underlying receive thread does not keep an otherwise finished app alive.
    """

    def __init__(
        self,
        on_text: Callable[[ProtocolMessage], None],
        on_data: Callable[[ProtocolMessage], None] | None = None,
        on_connection_state: Callable[[bool], None] | None = None,
    ) -> None:
        self._client = WebsocketCollabClient()
        self._display_name = ""
        self._client.on_text_message(on_text)
        if on_data is not None:
            self._client.on_data_message(on_data)
        if on_connection_state is not None:
            self._client.on_connection_state_changed(on_connection_state)

    @property
    def connected(self) -> bool:
        return self._client.connected

    def connect(
        self,
        url: str | None = None,
        channel_id: str | None = None,
        user: str | None = None,
        password: str | None = None,
        *,
        display_name: str | None = None,
    ) -> None:
        """Connect with explicit settings or per-field environment fallbacks.

        The first four arguments match the legacy client's connection API.
        Calling connect() without arguments retains environment-based setup.
        Startup errors reach the caller; no connection occurs on import.
        """
        resolved_user = connection_setting(user, "COLLAB_USER")
        resolved_name = connection_setting(
            display_name, "COLLAB_DISPLAY_NAME", resolved_user
        )
        previous_name = self._display_name
        self._display_name = resolved_name
        try:
            self._connect(url, channel_id, resolved_user, password)
        except Exception:
            self._display_name = previous_name
            raise

    def _connect(
        self, url: str | None, channel_id: str | None, user: str, password: str | None
    ) -> None:
        self._client.connect(
            url=connection_setting(url, "COLLAB_WS_URL", DEFAULT_SERVER_URL),
            channel_id=connection_setting(channel_id, "COLLAB_ROOM_ID"),
            user=user,
            password=connection_setting(password, "COLLAB_PASSWORD"),
        )

    def send_text(self, content: str, to: list[str] | None = None) -> None:
        """Send a completed AI response; None broadcasts to the room.

        Recipients are collab account names, not VTuber display names.
        Send once per response, not once per token or again after TTS.
        Errors reach the caller. No automatic replay or delivery receipt.
        """
        self._client.send_text(self._display_name, content, to)

    def send_data(
        self, name: str, content: str, to: list[str] | None = None
    ) -> None:
        """Send agreed non-conversational data; v1 requires string content.

        For structured content, pass json.dumps(your_data). Handle incoming
        data separately from AI conversation input and validate its schema.
        """
        self._client.send_data(name, content, to)

    def disconnect(self) -> None:
        """Call from the host application's shutdown/finally path."""
        self._client.disconnect()
