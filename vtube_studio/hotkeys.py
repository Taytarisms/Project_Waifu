import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from files.system_setup.json_utils import load_json_safe, write_json_atomic
import sys
from files.vtube_studio.idle_animations import (
    IdleAnimationEngine,
    EMOTION_PRESETS,
    EMOTION_TO_IDLE_PROFILE,
)
from files.vtube_studio.vts_client import VTSRawClient

FILES_DIR = Path(__file__).resolve().parents[1]
USERDATA_DIR = FILES_DIR / "userdata"
UNMAPPED_OPTION = "None / Unmapped"
DEFAULT_VTS_MAPPING = {
    "current_emotion": "",
    "full_reset_hotkey_sequence": [],
    "emotions": {},
}
DEFAULT_HOTKEY_CACHE = {
    "hotkeys": [],
}

def empty_emotion_config() -> dict[str, list[dict[str, str]]]:
    return {
        "talking_hotkey_sequence": [],
        "idle_hotkey_sequence": [],
        "reset_hotkey_sequence": [],
    }

@dataclass
class VTSSettings:
    plugin_name: str = "AI Companion"
    developer: str = "YukonDefenders"
    authentication_token_path: str = str(USERDATA_DIR / "vts_token.txt")

    host: str = "localhost"
    port: int = 8001
    api_name: str = "VTubeStudioPublicAPI"
    api_version: str = "1.0"

    userdata_dir: str = str(USERDATA_DIR)
    hotkey_cache_filename: str = "vts_hotkeys.json"
    emotion_map_filename: str = "vts_emotion_map.json"

    reconnect_delay_seconds: float = 7.5
    request_timeout_seconds: float = 8.0
    open_timeout_seconds: float = 2.5
    sequence_step_delay_seconds: float = 0.10
    idle_fps: int = 10
    idle_await_response: bool = True

class VTSHotkeyManager:
    def __init__(self, settings: Optional[VTSSettings] = None, logger=print):
        self.settings = settings or VTSSettings()
        self._lock = asyncio.Lock()
        self._emotion_transition_lock = asyncio.Lock()
        from files.vtube_studio.advanced import AdvancedActions
        self.advanced = AdvancedActions(self)
        self._storage_lock = threading.RLock()
        self._client: Optional[VTSRawClient] = None
        self._connected = False
        self._authenticated = False
        self._current_emotion = ""
        self._idle_engine: Optional[IdleAnimationEngine] = None
        self._last_triggered_emotion = ""
        self._last_emotion_trigger_time = 0.0
        self._emotion_retrigger_cooldown = 4.0
        self._last_emotion_action = ""
        self._connection_state = "disconnected"
        self._last_error = ""
        self._logger = print
        self.logger = self._safe_log
        self.set_logger(logger)

    @property
    def idle_engine(self) -> Optional[IdleAnimationEngine]:
        return self._idle_engine

    @property
    def idle_running(self) -> bool:
        return self._idle_engine is not None and self._idle_engine.is_running

    @property
    def connected(self) -> bool:
        return bool(
            self._client is not None
            and self._client.connected
            and self._client.authenticated
            and self._connected
            and self._authenticated
        )

    @property
    def connection_state(self) -> str:
        if self.connected:
            return "connected"
        if self._connection_state == "connecting":
            return "connecting"
        if self._connection_state == "faulted" or self.last_error:
            return "faulted"
        return "disconnected"

    @property
    def last_error(self) -> str:
        if self._client is not None and self._client.last_error:
            return self._client.last_error
        return self._last_error

    @property
    def last_emotion_action(self) -> str:
        return self._last_emotion_action

    def set_logger(self, logger=print) -> None:
        self._logger = logger or print
        self.logger = self._safe_log
        if self._client is not None:
            for attr in ("logger", "_logger"):
                if hasattr(self._client, attr):
                    try:
                        setattr(self._client, attr, self._safe_log)
                    except Exception:
                        pass
        if self._idle_engine is not None:
            for attr in ("logger", "_logger"):
                if hasattr(self._idle_engine, attr):
                    try:
                        setattr(self._idle_engine, attr, self._safe_log)
                    except Exception:
                        pass

    def _safe_log(self, message: str) -> None:
        try:
            self._logger(message)
        except RuntimeError as exc:
            try:
                sys.__stdout__.write(
                    f"logger suppressed RuntimeError: {exc} | {message}\n"
                )
                sys.__stdout__.flush()
            except Exception:
                pass
        except Exception as exc:
            try:
                sys.__stdout__.write(
                    f"logger failed: {exc} | {message}\n"
                )
                sys.__stdout__.flush()
            except Exception:
                pass

    async def start_idle_animation(self) -> bool:
        async with self._lock:
            if not await self._connect_locked():
                return False

            if self._idle_engine is None:
                self._idle_engine = self._make_idle_engine()

            return await self._idle_engine.start()

    async def stop_idle_animation(self) -> None:
        engine = self._idle_engine
        if engine is not None:
            await engine.stop()

    def apply_emotion_to_idle(self, emotion_name: str) -> None:
        if self._idle_engine is None:
            return

        emotion_name = (emotion_name or "").strip().lower() or "neutral"

        profile_name = EMOTION_TO_IDLE_PROFILE.get(emotion_name)
        if profile_name:
            self._idle_engine.set_idle_profile(profile_name)

        preset = EMOTION_PRESETS.get(
            emotion_name,
            EMOTION_PRESETS.get("neutral", {}),
        )
        self._idle_engine.set_emotion_modifiers(**preset)

        self.logger(
            f"Idle animation tuned for emotion: {emotion_name} "
            f"| profile={self._idle_engine.get_idle_profile()}"
        )

    @property
    def custom_params_registered(self) -> bool:
        return bool(
            self._idle_engine is not None
            and getattr(self._idle_engine, "_params_registered", False)
        )

    async def register_custom_parameters(self) -> bool:
        async with self._lock:
            if not await self._connect_locked():
                return False

            if self._idle_engine is None:
                self._idle_engine = self._make_idle_engine()

            return await self._idle_engine.ensure_custom_params_registered()

    @property
    def userdata_path(self) -> Path:
        return Path(self.settings.userdata_dir)

    @property
    def hotkey_cache_path(self) -> Path:
        return self.userdata_path / self.settings.hotkey_cache_filename

    @property
    def emotion_map_path(self) -> Path:
        return self.userdata_path / self.settings.emotion_map_filename

    def _ensure_userdata(self) -> None:
        self.userdata_path.mkdir(parents=True, exist_ok=True)
        Path(self.settings.authentication_token_path).parent.mkdir(parents=True, exist_ok=True)

    def _make_client(self) -> VTSRawClient:
        return VTSRawClient(
            plugin_name=self.settings.plugin_name,
            developer=self.settings.developer,
            token_path=self.settings.authentication_token_path,
            host=self.settings.host,
            port=self.settings.port,
            api_name=self.settings.api_name,
            api_version=self.settings.api_version,
            request_timeout_seconds=self.settings.request_timeout_seconds,
            open_timeout_seconds=self.settings.open_timeout_seconds,
            logger=self._safe_log,
        )

    def _make_idle_engine(self) -> IdleAnimationEngine:
        return IdleAnimationEngine(
            self._client,
            logger=self._safe_log,
            fps=self.settings.idle_fps,
            await_response=self.settings.idle_await_response,
        )

    async def connect(self) -> bool:
        async with self._lock:
            return await self._connect_locked()

    async def _connect_locked(self) -> bool:
        if (
            self._client is not None
            and self._client.authenticated
            and self._connected
            and self._authenticated
        ):
            return True

        self._ensure_userdata()
        self._connection_state = "connecting"
        self._last_error = ""

        try:
            if self._client is None:
                self._client = self._make_client()

            ok = await self._client.connect()
            self._connected = ok
            self._authenticated = ok

            if not ok:
                self._last_error = self._client.last_error or "VTube Studio connection failed."
                self._connection_state = "faulted"
                return False

            self._current_emotion = ""
            self._last_triggered_emotion = ""
            self._last_emotion_trigger_time = 0.0
            self._last_emotion_action = ""
            mapping = self.load_vts_mapping()
            if mapping.get("current_emotion"):
                mapping["current_emotion"] = ""
                self.save_vts_mapping(mapping)

            self._connection_state = "connected"
            return True

        except Exception as e:
            self.logger(f"Connect/auth failed: {e}")
            self._last_error = str(e)
            self._connection_state = "faulted"
            self._connected = False
            self._authenticated = False
            self._client = None
            return False

    async def close(self) -> None:
        try:
            if self.advanced.emotion or self.advanced.owned_expressions:
                await self.advanced.reset()
        finally:
            async with self._lock:
                await self._close_locked()

    @property
    def advanced_mode(self):
        return self.load_vts_mapping().get("control_mode", "simple") == "advanced"

    async def set_control_mode(self, mode):
        if mode not in ("simple", "advanced"):
            raise ValueError("Unknown VTS control mode.")
        if self.advanced_mode:
            await self.advanced.reset()
        elif self.get_current_emotion():
            if not await self.reset_current_emotion():
                raise RuntimeError("Could not reset the current emotion before switching modes.")
        from files.vtube_studio.advanced import initial_advanced
        with self._storage_lock:
            mapping = self.load_vts_mapping()
            if mode == "advanced" and "advanced" not in mapping:
                mapping["advanced"] = initial_advanced(mapping)
            mapping["control_mode"] = mode
            self.save_vts_mapping(mapping)

    async def list_expressions(self):
        async with self._lock:
            if not await self._connect_locked():
                return []
            response = await self._request_locked("ExpressionStateRequest", {"details": False})
            if not response or response.get("messageType") == "APIError":
                raise RuntimeError("VTube Studio could not list expressions.")
            return response.get("data", {}).get("expressions", [])

    async def _close_locked(self) -> None:
        if self._idle_engine is not None:
            try:
                await self._idle_engine.stop()
            except Exception:
                pass

        if self._client is not None:
            try:
                await self._client.close()
            except Exception as e:
                self.logger(f"Close error: {e}")

        self._client = None
        self._connected = False
        self._authenticated = False
        self._idle_engine = None
        self._connection_state = "disconnected"
        self._last_error = ""
        self._last_emotion_action = ""
        self.logger("Closed VTS connection.")

    async def _request_locked(
        self,
        message_type: str,
        data: Optional[dict[str, Any]] = None,
        *,
        timeout: Optional[float] = None,
        await_response: bool = True,
    ) -> Optional[dict[str, Any]]:
        if not await self._connect_locked():
            return None

        try:
            return await self._client.request(
                message_type,
                data or {},
                timeout=timeout or self.settings.request_timeout_seconds,
                await_response=await_response,
            )
        except Exception as e:
            self.logger(f"Request failed, reconnecting once: {e}")
            await self._close_locked()

            if not await self._connect_locked():
                return None

            try:
                return await self._client.request(
                    message_type,
                    data or {},
                    timeout=timeout or self.settings.request_timeout_seconds,
                    await_response=await_response,
                )
            except Exception as retry_error:
                self.logger(f"Request failed after reconnect: {retry_error}")
                return None

    async def refresh_hotkey_cache(self) -> list[dict[str, Any]]:
        async with self._lock:
            self.logger("Fetching VTS hotkey list...")
            if not await self._connect_locked():
                return []

            response = await self._request_locked("HotkeysInCurrentModelRequest")
            if not isinstance(response, dict) or response.get("messageType") == "APIError":
                self.logger("VTS did not return a valid hotkey list; keeping the existing cache.")
                return []
            hotkeys = self._normalize_hotkey_response(response or {})
            self.logger(f"Found {len(hotkeys)} hotkeys.")

            data = response.get("data", {})
            self.save_hotkeys(
                hotkeys,
                model_id=str(data.get("modelID") or ""),
                model_name=str(data.get("modelName") or ""),
            )

            return hotkeys

    def _normalize_hotkey_response(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        data = response.get("data", {}) if isinstance(response, dict) else {}
        raw_hotkeys = data.get("availableHotkeys", [])

        normalized: list[dict[str, Any]] = []

        for index, item in enumerate(raw_hotkeys):
            if not isinstance(item, dict):
                continue

            name = item.get("name", "")
            hotkey_id = (
                item.get("hotkeyID")
                or item.get("hotkeyId")
                or item.get("id")
                or name
            )

            normalized.append(
                {
                    "index": index,
                    "name": name,
                    "hotkey_id": hotkey_id,
                    "type": item.get("type"),
                    "description": item.get("description", ""),
                    "raw": item,
                }
            )
        return normalized

    def _step_is_expression_toggle(self, step: dict[str, Any]) -> bool:
        if not isinstance(step, dict):
            return False

        hotkey_type = step.get("type") or ""

        if not hotkey_type:
            hotkey_id = step.get("hotkey_id") or step.get("hotkeyID") or step.get("name")
            hotkey_type = self._get_hotkey_type_from_cache(hotkey_id)

        return str(hotkey_type).lower() == "toggleexpression"

    @staticmethod
    def _step_hotkey_id(step: dict[str, Any]) -> str:
        if not isinstance(step, dict):
            return ""
        return str(
            step.get("hotkey_id")
            or step.get("hotkeyID")
            or step.get("name")
            or ""
        ).strip()

    def _expression_file_for_step(self, step: dict[str, Any]) -> str:
        expression_file = str(step.get("file") or "").strip()
        if expression_file:
            return expression_file
        hotkey = self._find_cached_hotkey(self._step_hotkey_id(step))
        if not hotkey:
            return ""
        raw = hotkey.get("raw", {}) if isinstance(hotkey.get("raw"), dict) else {}
        return str(hotkey.get("file") or raw.get("file") or "").strip()

    def _log_hotkey_api_error(
        self,
        hotkey_id_or_name: str,
        response: dict[str, Any],
        hotkey_metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        data = response.get("data", {}) if isinstance(response, dict) else {}
        error_id = data.get("errorID", "unknown")
        message = str(data.get("message") or "Hotkey execution failed.")
        hotkey = dict(hotkey_metadata or {})
        cached_hotkey = self._find_cached_hotkey(hotkey_id_or_name) or {}
        for key, value in cached_hotkey.items():
            hotkey.setdefault(key, value)
        name = str(hotkey.get("name") or hotkey_id_or_name)
        hotkey_type = str(hotkey.get("type") or "")
        guidance = ""
        if str(error_id) == "206":
            if hotkey_type.lower() == "loadeffectpreset":
                guidance = (
                    " Close any Visual Effects configuration window in VTube Studio, "
                    "make sure Visual Effects are enabled, and try again."
                )
            else:
                guidance = (
                    " Make sure a model is loaded and allow the VTS hotkey cooldown "
                    "to finish before retrying."
                )
        detail = (
            f"VTS could not execute hotkey '{name}'"
            f"{f' ({hotkey_type})' if hotkey_type else ''}: "
            f"error {error_id} - {message}{guidance}"
        )
        self._last_error = detail
        self.logger(detail)

    async def _set_expression_active_locked(
        self,
        step: dict[str, Any],
        active: bool,
    ) -> bool:
        expression_file = self._expression_file_for_step(step)
        if not expression_file:
            self.logger(
                "Cannot safely change expression state because its .exp3.json file "
                f"is missing from the hotkey cache: {self._step_hotkey_id(step)}"
            )
            return False
        response = await self._request_locked(
            "ExpressionActivationRequest",
            {
                "expressionFile": expression_file,
                "active": bool(active),
                "fadeTime": 0.1,
            },
        )
        if response is None:
            return False
        if response.get("messageType") == "APIError":
            data = response.get("data", {})
            detail = (
                f"VTS could not {'activate' if active else 'deactivate'} expression "
                f"'{expression_file}': error {data.get('errorID', 'unknown')} - "
                f"{data.get('message', 'Expression request failed.')}"
            )
            self._last_error = detail
            self.logger(detail)
            return False
        self._last_error = ""
        return True

    async def _toggle_off_previous_emotion(self, emotion_name: str) -> bool:
        emotion_name = (emotion_name or "").strip().lower()
        if not emotion_name:
            return True
        emotion_config = self._get_emotion_config(emotion_name)
        if not emotion_config:
            return True
        sequence = emotion_config.get("talking_hotkey_sequence", [])
        toggle_steps = [
            step for step in sequence
            if isinstance(step, dict) and self._step_is_expression_toggle(step)
        ]

        if not toggle_steps:
            self.logger(
                f"Previous emotion '{emotion_name}' has no toggle-safe hotkeys to turn off."
            )
            return True

        self.logger(f"Deactivating previous emotion: {emotion_name}")
        async with self._lock:
            if not await self._connect_locked():
                return False
            success_count = 0
            for step in toggle_steps:
                if await self._set_expression_active_locked(step, False):
                    success_count += 1
                await asyncio.sleep(self.settings.sequence_step_delay_seconds)
            self.logger(
                f"Finished {emotion_name}.deactivate: "
                f"{success_count}/{len(toggle_steps)} expressions deactivated."
            )
            return success_count == len(toggle_steps)

    def save_hotkeys(
        self,
        hotkeys: list[dict[str, Any]],
        *,
        model_id: str = "",
        model_name: str = "",
    ) -> None:
        self._ensure_userdata()
        payload = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "source": "VTube Studio",
            "plugin_name": self.settings.plugin_name,
            "host": self.settings.host,
            "port": self.settings.port,
            "model_id": model_id,
            "model_name": model_name,
            "count": len(hotkeys),
            "hotkeys": hotkeys,
        }
        with self._storage_lock:
            write_json_atomic(self.hotkey_cache_path, payload)
        self.logger(f"Saved hotkey cache: {self.hotkey_cache_path}")

    def load_cached_hotkeys(self) -> list[dict[str, Any]]:
        with self._storage_lock:
            payload = load_json_safe(
                self.hotkey_cache_path,
                DEFAULT_HOTKEY_CACHE,
                dict,
                logger=lambda message: self.logger(f"Failed to load cached hotkeys: {message}"),
            )
        hotkeys = payload.get("hotkeys", []) if isinstance(payload, dict) else []
        return hotkeys if isinstance(hotkeys, list) else []

    def get_cached_hotkey_names(self) -> list[str]:
        return [label for label, _hotkey in self.get_cached_hotkey_options()]

    def get_cached_hotkey_options(self) -> list[tuple[str, dict[str, Any]]]:
        hotkeys = [item for item in self.load_cached_hotkeys() if item.get("name")]
        counts: dict[str, int] = {}
        for hotkey in hotkeys:
            name = str(hotkey.get("name") or "")
            counts[name] = counts.get(name, 0) + 1

        options = []
        for hotkey in hotkeys:
            name = str(hotkey.get("name") or "")
            label = name
            if counts.get(name, 0) > 1:
                kind = str(hotkey.get("type") or "Hotkey")
                hotkey_id = str(hotkey.get("hotkey_id") or "")
                label = f"{name} · {kind} · {hotkey_id[:8]}"
            options.append((label, hotkey))
        return options

    def find_cached_hotkey_by_name(self, hotkey_name: str) -> Optional[dict[str, Any]]:
        options = self.get_cached_hotkey_options()
        for label, hotkey in options:
            if label == hotkey_name:
                return hotkey
        raw_matches = [
            hotkey for _label, hotkey in options
            if hotkey.get("name") == hotkey_name
        ]
        if len(raw_matches) == 1:
            return raw_matches[0]
        if len(raw_matches) > 1:
            self.logger(f"Hotkey name is ambiguous: {hotkey_name}")
        return None

    def _find_cached_hotkey(self, hotkey_id_or_name: str) -> Optional[dict[str, Any]]:
        value = str(hotkey_id_or_name or "").strip()
        if not value:
            return None
        for hotkey in self.load_cached_hotkeys():
            if (
                str(hotkey.get("hotkey_id") or "") == value
                or str(hotkey.get("name") or "") == value
            ):
                return hotkey
        return None

    def display_name_for_mapping_entry(self, entry: dict[str, Any]) -> str:
        hotkey_id = str(entry.get("hotkey_id") or entry.get("hotkeyID") or "")
        name = str(entry.get("name") or "")
        for label, hotkey in self.get_cached_hotkey_options():
            if hotkey_id and str(hotkey.get("hotkey_id") or "") == hotkey_id:
                return label
        return name

    def build_mapping_entry_from_name(self, hotkey_name: str) -> Optional[dict[str, str]]:
        hotkey = self.find_cached_hotkey_by_name(hotkey_name)

        if not hotkey:
            return None

        raw = hotkey.get("raw", {}) if isinstance(hotkey.get("raw"), dict) else {}
        return {
            "name": hotkey.get("name", ""),
            "hotkey_id": hotkey.get("hotkey_id", ""),
            "type": hotkey.get("type", ""),
            "file": hotkey.get("file", "") or raw.get("file", ""),
        }

    def _get_hotkey_type_from_cache(self, hotkey_id_or_name: str) -> str:
        hotkey_id_or_name = (hotkey_id_or_name or "").strip()

        if not hotkey_id_or_name:
            return ""

        hotkey = self._find_cached_hotkey(hotkey_id_or_name)
        if not hotkey:
            return ""
        return hotkey.get("type", "") or ""

    def load_vts_mapping(self) -> dict[str, Any]:
        with self._storage_lock:
            payload = load_json_safe(
                self.emotion_map_path,
                DEFAULT_VTS_MAPPING,
                dict,
                logger=lambda message: self.logger(f"Failed to load VTS mapping: {message}"),
            )
        if "emotions" in payload:
            payload.setdefault("current_emotion", "")
            payload.setdefault("full_reset_hotkey_sequence", [])
            payload.setdefault("emotions", {})
            return payload

        converted = {
            "current_emotion": "",
            "full_reset_hotkey_sequence": [],
            "emotions": {},
        }
        for emotion_name, entry in payload.items():
            if not isinstance(entry, dict):
                continue
            converted["emotions"][emotion_name] = {
                "talking_hotkey_sequence": [entry],
                "idle_hotkey_sequence": [],
                "reset_hotkey_sequence": [],
            }
        return converted

    def save_vts_mapping(self, mapping: dict[str, Any]) -> None:
        self._ensure_userdata()

        mapping.setdefault("current_emotion", "")
        mapping.setdefault("full_reset_hotkey_sequence", [])
        mapping.setdefault("emotions", {})
        payload = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "model_id": mapping.get("model_id", ""),
            "control_mode": mapping.get("control_mode", "simple"),
            **({"advanced": mapping["advanced"]} if "advanced" in mapping else {}),
            "model_name": mapping.get("model_name", ""),
            "current_emotion": mapping.get("current_emotion", ""),
            "full_reset_hotkey_sequence": mapping.get("full_reset_hotkey_sequence", []),
            "emotions": mapping.get("emotions", {}),
        }
        with self._storage_lock:
            write_json_atomic(self.emotion_map_path, payload)

        self.logger(f"Saved VTS mapping: {self.emotion_map_path}")

    def save_primary_mappings(
        self,
        emotion_entries: dict[str, Optional[dict[str, Any]]],
        full_reset_entry: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._storage_lock:
            mapping = self.load_vts_mapping()
            emotions = mapping.setdefault("emotions", {})

            for emotion_name, entry in emotion_entries.items():
                emotion_name = (emotion_name or "").strip().lower()
                if not emotion_name:
                    continue
                config = emotions.setdefault(emotion_name, empty_emotion_config())
                for key, default_value in empty_emotion_config().items():
                    config.setdefault(key, default_value)
                existing = list(config.get("talking_hotkey_sequence", []))
                if entry is None:
                    config["talking_hotkey_sequence"] = []
                elif existing:
                    config["talking_hotkey_sequence"] = [entry, *existing[1:]]
                else:
                    config["talking_hotkey_sequence"] = [entry]

            reset_sequence = list(mapping.get("full_reset_hotkey_sequence", []))
            if full_reset_entry is None:
                mapping["full_reset_hotkey_sequence"] = []
            elif reset_sequence:
                mapping["full_reset_hotkey_sequence"] = [
                    full_reset_entry,
                    *reset_sequence[1:],
                ]
            else:
                mapping["full_reset_hotkey_sequence"] = [full_reset_entry]

            self.save_vts_mapping(mapping)
        return mapping

    async def trigger_hotkey(self, hotkey_id_or_name: str) -> bool:
        if not hotkey_id_or_name:
            return False
        async with self._lock:
            return await self._trigger_hotkey_locked(hotkey_id_or_name)

    async def _trigger_hotkey_locked(
        self,
        hotkey_id_or_name: str,
        hotkey_metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        if not await self._connect_locked():
            return False
        response = await self._request_locked(
            "HotkeyTriggerRequest",
            {"hotkeyID": hotkey_id_or_name},
        )

        if response is None:
            return False
        msg_type = response.get("messageType")
        if msg_type == "APIError":
            self._log_hotkey_api_error(
                hotkey_id_or_name,
                response,
                hotkey_metadata,
            )
            return False
        self._last_error = ""
        return True

    async def trigger_hotkey_sequence(
        self,
        sequence: list[dict[str, Any]],
        *,
        label: str = "sequence",
        delay_seconds: Optional[float] = None,
        expression_active: Optional[bool] = None,
    ) -> bool:
        if delay_seconds is None:
            delay_seconds = self.settings.sequence_step_delay_seconds
        if not sequence:
            self.logger(f"Empty hotkey sequence: {label}")
            return False
        async with self._lock:
            if not await self._connect_locked():
                return False
            success_count = 0
            attempted_count = 0
            for step in sequence:
                if not isinstance(step, dict):
                    continue
                hotkey_id = self._step_hotkey_id(step)
                if not hotkey_id:
                    continue
                attempted_count += 1
                if (
                    expression_active is not None
                    and self._step_is_expression_toggle(step)
                ):
                    ok = await self._set_expression_active_locked(
                        step,
                        expression_active,
                    )
                else:
                    ok = await self._trigger_hotkey_locked(hotkey_id, step)
                if ok:
                    success_count += 1
                if delay_seconds and delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
            self.logger(
                f"Finished {label}: {success_count}/{attempted_count} steps triggered."
            )
            return attempted_count > 0 and success_count == attempted_count

    def _get_emotion_config(self, emotion_name: str) -> Optional[dict[str, Any]]:
        mapping = self.load_vts_mapping()
        return mapping.get("emotions", {}).get((emotion_name or "").strip().lower())

    def set_current_emotion_local(self, emotion_name: str) -> bool:
        emotion_name = (emotion_name or "").strip().lower()
        with self._storage_lock:
            mapping = self.load_vts_mapping()

            if emotion_name not in mapping.get("emotions", {}):
                self.logger(f"Tried to set undefined emotion: {emotion_name}")
                return False
            self._current_emotion = emotion_name
            mapping["current_emotion"] = emotion_name
            self.save_vts_mapping(mapping)
        return True

    def _clear_current_emotion_local(self) -> None:
        with self._storage_lock:
            mapping = self.load_vts_mapping()
            mapping["current_emotion"] = ""
            self._current_emotion = ""
            self._last_triggered_emotion = ""
            self._last_emotion_trigger_time = 0.0
            self.save_vts_mapping(mapping)

    def get_current_emotion(self) -> str:
        return self._current_emotion
    
    async def play_sequence_for_emotion(self, emotion_name: str, sequence_type: str) -> bool:
        emotion_name = (emotion_name or "").strip().lower()
        sequence_type = (sequence_type or "").strip().lower()
        if sequence_type not in {"talking", "idle", "reset"}:
            self.logger(f"Invalid sequence type: {sequence_type}")
            return False
        emotion_config = self._get_emotion_config(emotion_name)
        if not emotion_config:
            self.logger(f"Tried to play undefined emotion: {emotion_name}")
            return False
        key = f"{sequence_type}_hotkey_sequence"
        sequence = emotion_config.get(key, [])[:1]
        return await self.trigger_hotkey_sequence(
            sequence,
            label=f"{emotion_name}.{key}",
            expression_active=sequence_type != "reset",
        )

    async def set_emotion_and_animate_talking(self, emotion_name: str) -> bool:
        if self.advanced_mode:
            return await self.advanced.talking((emotion_name or "").strip().lower())
        async with self._emotion_transition_lock:
            return await self._set_emotion_and_animate_talking_locked(emotion_name)

    async def _set_emotion_and_animate_talking_locked(self, emotion_name: str) -> bool:
        import time
        emotion_name = (emotion_name or "").strip().lower()
        if not emotion_name:
            self._last_emotion_action = "invalid"
            return False
        previous_emotion = self.get_current_emotion()
        now = time.monotonic()
        if (
            emotion_name == self._last_triggered_emotion
            and now - self._last_emotion_trigger_time < self._emotion_retrigger_cooldown
        ):
            self.apply_emotion_to_idle(emotion_name)
            self.logger(f"Skipped duplicate emotion trigger during cooldown: {emotion_name}")
            self._last_emotion_action = "cooldown_skipped"
            return True
        emotion_config = self._get_emotion_config(emotion_name)
        if not emotion_config:
            self.logger(f"Tried to set undefined emotion: {emotion_name}")
            self._last_emotion_action = "unmapped"
            return False
        if previous_emotion and previous_emotion != emotion_name:
            previous_config = self._get_emotion_config(previous_emotion) or {}
            reset_sequence = previous_config.get("reset_hotkey_sequence", [])
            if reset_sequence:
                reset_ok = await self.play_sequence_for_emotion(previous_emotion, "reset")
            else:
                reset_ok = await self._toggle_off_previous_emotion(previous_emotion)
            if not reset_ok:
                self.logger(f"Could not reset previous emotion: {previous_emotion}")
                self._last_emotion_action = "reset_failed"
                return False
            self._clear_current_emotion_local()
            await asyncio.sleep(0.08)
        sequence = emotion_config.get("talking_hotkey_sequence", [])[:1]
        if sequence:
            if not await self.play_sequence_for_emotion(emotion_name, "talking"):
                self._last_emotion_action = "trigger_failed"
                return False
        else:
            self.logger(f"No talking sequence mapped for emotion: {emotion_name}")

        if not self.set_current_emotion_local(emotion_name):
            self._last_emotion_action = "state_failed"
            return False
        self._last_triggered_emotion = emotion_name
        self._last_emotion_trigger_time = now
        self.apply_emotion_to_idle(emotion_name)
        self._last_emotion_action = "triggered"
        return True

    async def animate_talking(self) -> bool:
        current = self.get_current_emotion()
        if not current:
            self.logger("No current emotion set for talking animation.")
            return False

        if self.advanced_mode:
            return await self.advanced.talking(current)
        return await self.play_sequence_for_emotion(current, "talking")

    async def animate_idle(self) -> bool:
        if self.advanced_mode:
            return await self.advanced.finish()
        current = self.get_current_emotion()
        if not current:
            self.logger("No current emotion set for idle animation.")
            return False

        return await self.play_sequence_for_emotion(current, "idle")
    
    async def reset_current_emotion(self) -> bool:
        if self.advanced_mode:
            return await self.advanced.reset()
        async with self._emotion_transition_lock:
            return await self._reset_current_emotion_locked()

    async def _reset_current_emotion_locked(self) -> bool:
        current = self.get_current_emotion()

        if not current:
            self.logger("No current emotion set for reset.")
            self.apply_emotion_to_idle("neutral")
            return True
        
        emotion_config = self._get_emotion_config(current) or {}
        reset_sequence = emotion_config.get("reset_hotkey_sequence", [])
        if reset_sequence:
            ok = await self.play_sequence_for_emotion(current, "reset")
        else:
            ok = await self._toggle_off_previous_emotion(current)
        if not ok:
            self.logger(f"Failed to reset current VTS emotion: {current}")
            return False
        self._clear_current_emotion_local()
        self.apply_emotion_to_idle("neutral")

        self.logger(f"Reset current VTS emotion: {current}")
        return True

    async def full_reset(self) -> bool:
        if self.advanced_mode:
            return await self.advanced.reset(full=True)
        async with self._emotion_transition_lock:
            return await self._full_reset_locked()

    async def _full_reset_locked(self) -> bool:
        mapping = self.load_vts_mapping()
        sequence = mapping.get("full_reset_hotkey_sequence", [])[:1]
        if not sequence:
            self.logger("No full reset hotkey sequence is mapped.")
            return False
        if not await self.trigger_hotkey_sequence(
            sequence,
            label="full_reset_hotkey_sequence",
        ):
            return False
        self._clear_current_emotion_local()
        self.apply_emotion_to_idle("neutral")

        return True

async def main():
    manager = VTSHotkeyManager()
    await manager.connect()
    hotkeys = await manager.refresh_hotkey_cache()

    for hotkey in hotkeys:
        print(f"{hotkey['index']}: {hotkey['name']} | id={hotkey['hotkey_id']}")
    if await manager.start_idle_animation():
        await asyncio.sleep(10)
        await manager.stop_idle_animation()

    await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
