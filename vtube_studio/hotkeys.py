import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from files.system_setup.json_utils import load_json_safe
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
    sequence_step_delay_seconds: float = 0.03

class VTSHotkeyManager:
    def __init__(self, settings: Optional[VTSSettings] = None, logger=print):
        self.settings = settings or VTSSettings()
        self._lock = asyncio.Lock()
        self._client: Optional[VTSRawClient] = None
        self._connected = False
        self._authenticated = False
        self._current_emotion = ""
        self._idle_engine: Optional[IdleAnimationEngine] = None
        self._last_triggered_emotion = ""
        self._last_emotion_trigger_time = 0.0
        self._emotion_retrigger_cooldown = 4.0
        self._logger = print
        self.logger = self._safe_log
        self.set_logger(logger)

    @property
    def idle_engine(self) -> Optional[IdleAnimationEngine]:
        return self._idle_engine

    @property
    def idle_running(self) -> bool:
        return self._idle_engine is not None and self._idle_engine.is_running

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
                self._idle_engine = IdleAnimationEngine(self._client, logger=self._safe_log)

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
                self._idle_engine = IdleAnimationEngine(self._client, logger=self._safe_log)

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
            logger=self._safe_log,
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

        try:
            if self._client is None:
                self._client = self._make_client()

            ok = await self._client.connect()
            self._connected = ok
            self._authenticated = ok

            if not ok:
                return False

            self._current_emotion = ""
            self._last_triggered_emotion = ""
            self._last_emotion_trigger_time = 0.0

            if self._idle_engine is None:
                self._idle_engine = IdleAnimationEngine(self._client, logger=self._safe_log)

            try:
                if not await self._idle_engine.ensure_custom_params_registered():
                    self.logger("Connected to VTS, but custom VTS parameters are not ready.")
            except Exception as e:
                self.logger(f"Custom VTS parameter registration failed: {e}")

            return True

        except Exception as e:
            self.logger(f"Connect/auth failed: {e}")
            self._connected = False
            self._authenticated = False
            self._client = None
            return False

    async def close(self) -> None:
        async with self._lock:
            await self._close_locked()

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
            hotkeys = self._normalize_hotkey_response(response or {})
            self.logger(f"Found {len(hotkeys)} hotkeys.")

            if hotkeys:
                self.save_hotkeys(hotkeys)

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

    def _step_is_toggle(self, step: dict[str, Any]) -> bool:
        if not isinstance(step, dict):
            return False

        hotkey_type = step.get("type") or ""

        if not hotkey_type:
            hotkey_id = step.get("hotkey_id") or step.get("hotkeyID") or step.get("name")
            hotkey_type = self._get_hotkey_type_from_cache(hotkey_id)

        return str(hotkey_type).lower() in {
            "toggleexpression",
            "toggleitemscene",
        }

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
            if isinstance(step, dict) and self._step_is_toggle(step)
        ]

        if not toggle_steps:
            self.logger(
                f"Previous emotion '{emotion_name}' has no toggle-safe hotkeys to turn off."
            )
            return True

        self.logger(f"Toggling off previous emotion: {emotion_name}")
        return await self.trigger_hotkey_sequence(
            toggle_steps,
            label=f"{emotion_name}.toggle_off",
        )

    def save_hotkeys(self, hotkeys: list[dict[str, Any]]) -> None:
        self._ensure_userdata()
        payload = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "source": "VTube Studio",
            "plugin_name": self.settings.plugin_name,
            "host": self.settings.host,
            "port": self.settings.port,
            "count": len(hotkeys),
            "hotkeys": hotkeys,
        }
        self.hotkey_cache_path.write_text(
            json.dumps(payload, indent=4, ensure_ascii=False),
            encoding="utf-8",
        )
        self.logger(f"Saved hotkey cache: {self.hotkey_cache_path}")

    def load_cached_hotkeys(self) -> list[dict[str, Any]]:
        payload = load_json_safe(
            self.hotkey_cache_path,
            DEFAULT_HOTKEY_CACHE,
            dict,
            logger=lambda message: self.logger(f"Failed to load cached hotkeys: {message}"),
        )
        hotkeys = payload.get("hotkeys", []) if isinstance(payload, dict) else []
        return hotkeys if isinstance(hotkeys, list) else []

    def get_cached_hotkey_names(self) -> list[str]:
        return [
            hotkey.get("name", "")
            for hotkey in self.load_cached_hotkeys()
            if hotkey.get("name")
        ]

    def find_cached_hotkey_by_name(self, hotkey_name: str) -> Optional[dict[str, Any]]:
        for hotkey in self.load_cached_hotkeys():
            if hotkey.get("name") == hotkey_name:
                return hotkey
        return None

    def build_mapping_entry_from_name(self, hotkey_name: str) -> Optional[dict[str, str]]:
        hotkey = self.find_cached_hotkey_by_name(hotkey_name)

        if not hotkey:
            return None

        return {
            "name": hotkey.get("name", ""),
            "hotkey_id": hotkey.get("hotkey_id", ""),
            "type": hotkey.get("type", ""),
        }

    def _get_hotkey_type_from_cache(self, hotkey_id_or_name: str) -> str:
        hotkey_id_or_name = (hotkey_id_or_name or "").strip()

        if not hotkey_id_or_name:
            return ""

        for hotkey in self.load_cached_hotkeys():
            if (
                hotkey.get("hotkey_id") == hotkey_id_or_name
                or hotkey.get("name") == hotkey_id_or_name
            ):
                return hotkey.get("type", "") or ""

        return ""

    def load_vts_mapping(self) -> dict[str, Any]:
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
            "current_emotion": mapping.get("current_emotion", ""),
            "full_reset_hotkey_sequence": mapping.get("full_reset_hotkey_sequence", []),
            "emotions": mapping.get("emotions", {}),
        }
        self.emotion_map_path.write_text(
            json.dumps(payload, indent=4, ensure_ascii=False),
            encoding="utf-8",
        )

        self.logger(f"Saved VTS mapping: {self.emotion_map_path}")

    async def trigger_hotkey(self, hotkey_id_or_name: str) -> bool:
        if not hotkey_id_or_name:
            return False
        async with self._lock:
            return await self._trigger_hotkey_locked(hotkey_id_or_name)

    async def _trigger_hotkey_locked(self, hotkey_id_or_name: str) -> bool:
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
            self.logger(f"VTS failed to trigger hotkey '{hotkey_id_or_name}': {response}")
            return False
        return True

    async def trigger_hotkey_sequence(
        self,
        sequence: list[dict[str, Any]],
        *,
        label: str = "sequence",
        delay_seconds: Optional[float] = None,
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
            for step in sequence:
                if not isinstance(step, dict):
                    continue
                hotkey_id = step.get("hotkey_id") or step.get("hotkeyID") or step.get("name")
                if not hotkey_id:
                    continue
                ok = await self._trigger_hotkey_locked(hotkey_id)
                if ok:
                    success_count += 1
                if delay_seconds and delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
            self.logger(
                f"Finished {label}: {success_count}/{len(sequence)} steps triggered."
            )
            return success_count > 0

    def _get_emotion_config(self, emotion_name: str) -> Optional[dict[str, Any]]:
        mapping = self.load_vts_mapping()
        return mapping.get("emotions", {}).get((emotion_name or "").strip().lower())

    def set_current_emotion_local(self, emotion_name: str) -> bool:
        emotion_name = (emotion_name or "").strip().lower()
        mapping = self.load_vts_mapping()

        if emotion_name not in mapping.get("emotions", {}):
            self.logger(f"Tried to set undefined emotion: {emotion_name}")
            return False
        self._current_emotion = emotion_name
        mapping["current_emotion"] = emotion_name
        self.save_vts_mapping(mapping)
        return True

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
        sequence = emotion_config.get(key, [])
        return await self.trigger_hotkey_sequence(
            sequence,
            label=f"{emotion_name}.{key}",
        )

    async def set_emotion_and_animate_talking(self, emotion_name: str) -> bool:
        import time
        emotion_name = (emotion_name or "").strip().lower()
        if not emotion_name:
            return False
        previous_emotion = self.get_current_emotion()
        now = time.monotonic()
        if (
            emotion_name == self._last_triggered_emotion
            and now - self._last_emotion_trigger_time < self._emotion_retrigger_cooldown
        ):
            self.apply_emotion_to_idle(emotion_name)
            self.logger(f"Skipped duplicate emotion trigger during cooldown: {emotion_name}")
            return True
        emotion_config = self._get_emotion_config(emotion_name)
        if not emotion_config:
            self.logger(f"Tried to set undefined emotion: {emotion_name}")
            return False
        if previous_emotion and previous_emotion != emotion_name:
            await self._toggle_off_previous_emotion(previous_emotion)
            await asyncio.sleep(0.08)
        if not self.set_current_emotion_local(emotion_name):
            return False
        self._last_triggered_emotion = emotion_name
        self._last_emotion_trigger_time = now
        self.apply_emotion_to_idle(emotion_name)
        sequence = emotion_config.get("talking_hotkey_sequence", [])
        if not sequence:
            self.logger(f"No talking sequence mapped for emotion: {emotion_name}")
            return True

        return await self.play_sequence_for_emotion(emotion_name, "talking")

    async def animate_talking(self) -> bool:
        current = self.get_current_emotion()
        if not current:
            self.logger("No current emotion set for talking animation.")
            return False

        return await self.play_sequence_for_emotion(current, "talking")

    async def animate_idle(self) -> bool:
        current = self.get_current_emotion()
        if not current:
            self.logger("No current emotion set for idle animation.")
            return False

        return await self.play_sequence_for_emotion(current, "idle")
    
    async def reset_current_emotion(self) -> bool:
        current = self.get_current_emotion()

        if not current:
            self.logger("No current emotion set for reset.")
            self.apply_emotion_to_idle("neutral")
            return True
        
        ok = await self._toggle_off_previous_emotion(current)
        mapping = self.load_vts_mapping()
        mapping["current_emotion"] = ""
        self._current_emotion = ""
        self._last_triggered_emotion = ""
        self._last_emotion_trigger_time = 0.0
        self.save_vts_mapping(mapping)
        self.apply_emotion_to_idle("neutral")

        if ok:
            self.logger(f"Reset current VTS emotion: {current}")
        else:
            self.logger(f"Attempted to reset current VTS emotion: {current}")

        return ok

    async def full_reset(self) -> bool:
        mapping = self.load_vts_mapping()
        sequence = mapping.get("full_reset_hotkey_sequence", [])
        mapping["current_emotion"] = ""
        self._current_emotion = ""
        self._last_triggered_emotion = ""
        self._last_emotion_trigger_time = 0.0
        self.save_vts_mapping(mapping)
        self.apply_emotion_to_idle("neutral")

        return await self.trigger_hotkey_sequence(
            sequence,
            label="full_reset_hotkey_sequence",
        )

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
