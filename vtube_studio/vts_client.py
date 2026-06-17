import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Optional

try:
    import websockets
except ImportError as exc:
    websockets = None
    _WEBSOCKETS_IMPORT_ERROR = exc
else:
    _WEBSOCKETS_IMPORT_ERROR = None


class VTSClientError(RuntimeError):
    """Raised when the raw VTube Studio websocket client cannot complete a request. Also, this is here so it doesn't create a critical error."""

class VTSRawClient:
    def __init__(
        self,
        *,
        plugin_name: str,
        developer: str,
        token_path: str | Path,
        host: str = "localhost",
        port: int = 8001,
        api_name: str = "VTubeStudioPublicAPI",
        api_version: str = "1.0",
        request_timeout_seconds: float = 8.0,
        logger=print,
    ):
        if websockets is None:
            raise ImportError(
                "The 'websockets' package is required for the VTS client. "
                "Install it with: pip install websockets"
            ) from _WEBSOCKETS_IMPORT_ERROR

        self.plugin_name = plugin_name
        self.developer = developer
        self.token_path = Path(token_path)
        self.host = host
        self.port = int(port)
        self.api_name = api_name
        self.api_version = api_version
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.logger = logger

        self._ws = None
        self._recv_task: Optional[asyncio.Task] = None
        self._pending: dict[str, asyncio.Future] = {}
        self._send_lock = asyncio.Lock()
        self._connected = False
        self._authenticated = False
        self._closing = False
        self._auth_token = ""
        self._request_counter = 0

    @property
    def uri(self) -> str:
        return f"ws://{self.host}:{self.port}"

    @property
    def connected(self) -> bool:
        return self._connected and self._ws is not None

    @property
    def authenticated(self) -> bool:
        return self.connected and self._authenticated

    async def connect(self) -> bool:
        if self.authenticated:
            return True

        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self._closing = False

        try:
            if self._ws is None:
                self.logger(f"Connecting to {self.uri}...")
                self._ws = await websockets.connect(
                    self.uri,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=2,
                    max_queue=64,
                )
                self._connected = True
                self._recv_task = asyncio.create_task(self._recv_loop())

            token = self._load_token()
            if token:
                auth_ok = await self._authenticate_with_token(token)
                if auth_ok:
                    self._auth_token = token
                    self._authenticated = True
                    self.logger("Connected and authenticated with saved token.")
                    return True

                self.logger("Saved token was rejected; requesting a new one.")

            token = await self._request_auth_token()
            if not token:
                raise VTSClientError("VTube Studio authentication token was not granted.")

            self._save_token(token)
            auth_ok = await self._authenticate_with_token(token)
            if not auth_ok:
                raise VTSClientError("VTube Studio authentication failed after token grant.")

            self._auth_token = token
            self._authenticated = True
            self.logger("Connected and authenticated.")
            return True

        except Exception as e:
            self.logger(f"Connect/auth failed: {e}")
            await self.close()
            return False

    async def close(self) -> None:
        self._closing = True
        self._authenticated = False
        self._connected = False

        for request_id, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_exception(VTSClientError("VTS client closed before response arrived."))
            self._pending.pop(request_id, None)

        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._recv_task = None

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as e:
                self.logger(f"Close error: {e}")
            self._ws = None

    async def request(
        self,
        message_type: str,
        data: dict | None = None,
        *,
        request_id: str | None = None,
        timeout: float = 8.0,
        await_response: bool = True,
    ) -> dict | None:
        if self._ws is None or not self._connected:
            raise RuntimeError("VTS websocket is not connected.")

        request_id = request_id or self._next_request_id(message_type)

        payload = {
            "apiName": self.api_name,
            "apiVersion": self.api_version,
            "requestID": request_id,
            "messageType": message_type,
            "data": data or {},
        }

        future = None
        if await_response:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._pending[request_id] = future

        try:
            import json

            async with self._send_lock:
                await self._ws.send(json.dumps(payload, ensure_ascii=False))

            if not await_response:
                return None

            return await asyncio.wait_for(future, timeout=timeout)

        except Exception:
            if await_response:
                pending = self._pending.pop(request_id, None)
                if pending is not None and not pending.done():
                    pending.cancel()
            raise

    async def list_hotkeys(self, *, timeout: float = 8.0) -> dict | None:
        return await self.request(
            "HotkeysInCurrentModelRequest",
            {},
            timeout=timeout,
            await_response=True,
        )

    async def trigger_hotkey(
        self,
        hotkey_id: str,
        *,
        timeout: float = 8.0,
    ) -> dict | None:
        return await self.request(
            "HotkeyTriggerRequest",
            {"hotkeyID": hotkey_id},
            timeout=timeout,
            await_response=True,
        )
    
    async def create_parameter(
        self,
        *,
        parameter_name: str,
        explanation: str = "",
        min_value: float = 0.0,
        max_value: float = 1.0,
        default_value: float = 0.0,
        timeout: float = 5.0,
    ) -> dict | None:
        return await self.request(
            "ParameterCreationRequest",
            {
                "parameterName": parameter_name,
                "explanation": explanation,
                "min": min_value,
                "max": max_value,
                "defaultValue": default_value,
            },
            timeout=timeout,
            await_response=True,
        )

    async def inject_parameter_values(
        self,
        parameter_values: list[dict],
        *,
        mode: str = "set",
        await_response: bool = True,
        timeout: float = 2.0,
    ) -> dict | None:
        return await self.request(
            "InjectParameterDataRequest",
            {
                "mode": mode,
                "parameterValues": parameter_values,
            },
            timeout=timeout,
            await_response=await_response,
        )

    async def _request_auth_token(self) -> str:
        response = await self.request(
            "AuthenticationTokenRequest",
            {
                "pluginName": self.plugin_name,
                "pluginDeveloper": self.developer,
            },
        )

        data = (response or {}).get("data", {})
        return data.get("authenticationToken", "") or ""

    async def _authenticate_with_token(self, token: str) -> bool:
        response = await self.request(
            "AuthenticationRequest",
            {
                "pluginName": self.plugin_name,
                "pluginDeveloper": self.developer,
                "authenticationToken": token,
            },
        )

        data = (response or {}).get("data", {})
        return bool(data.get("authenticated"))

    async def _recv_loop(self) -> None:
        import json

        try:
            async for raw in self._ws:
                try:
                    message = json.loads(raw)
                except Exception as e:
                    self.logger(f"Failed to parse websocket message: {e}")
                    continue

                request_id = message.get("requestID")

                if request_id and request_id in self._pending:
                    future = self._pending.pop(request_id, None)
                    if future is not None and not future.done():
                        future.set_result(message)
                    continue
                message_type = message.get("messageType", "")
                if message_type == "APIError":
                    self.logger(f"Unmatched API error: {message}")
                else:
                    pass

        except Exception as e:
            self.logger(f"Receive loop stopped: {e}")

        finally:
            self._connected = False
            for request_id, future in list(self._pending.items()):
                if not future.done():
                    future.set_exception(RuntimeError("VTS receive loop stopped."))
            self._pending.clear()

    def _next_request_id(self, prefix: str = "VTS") -> str:
        self._request_counter += 1
        return f"{prefix}-{self._request_counter}"

    def _load_token(self) -> str:
        try:
            if self.token_path.exists():
                return self.token_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            self.logger(f"Failed to read auth token: {e}")
        return ""

    def _save_token(self, token: str) -> None:
        try:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(token.strip(), encoding="utf-8")
        except Exception as e:
            self.logger(f"Failed to save auth token: {e}")

    @staticmethod
    def _new_request_id(prefix: str = "request") -> str:
        safe_prefix = "".join(ch for ch in prefix if ch.isalnum() or ch in ("_", "-"))[:24]
        return f"{safe_prefix}-{uuid.uuid4().hex[:12]}"
