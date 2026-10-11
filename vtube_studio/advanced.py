import asyncio
import copy
import math


def validate_actions(actions):
    if not isinstance(actions, list) or len(actions) > 32:
        raise ValueError("Use at most 32 actions per sequence.")
    result = copy.deepcopy(actions)
    for action in result:
        kind = action.get("action") if isinstance(action, dict) else None
        if kind == "hotkey":
            if not str(action.get("hotkey_id", "")).strip():
                raise ValueError("Choose a hotkey.")
        elif kind == "expression":
            if not str(action.get("file", "")).endswith(".exp3.json"):
                raise ValueError("Choose an expression ending in .exp3.json.")
            if not isinstance(action.get("active"), bool):
                raise ValueError("Choose expression on or off.")
            fade = float(action.get("fade", 0.1))
            if not math.isfinite(fade) or not 0 <= fade <= 2:
                raise ValueError("Expression fade must be between 0 and 2 seconds.")
        elif kind == "wait":
            seconds = float(action.get("seconds", 0))
            if not math.isfinite(seconds) or not 0 <= seconds <= 5:
                raise ValueError("A delay must be between 0 and 5 seconds.")
        else:
            raise ValueError("Unknown VTS action.")
    if sum(float(a.get("seconds", 0)) for a in result if a["action"] == "wait") > 15:
        raise ValueError("Combined delays must not exceed 15 seconds.")
    return result


def initial_advanced(mapping):
    def convert(sequence):
        return [dict(step, action="hotkey", hotkey_id=step.get("hotkey_id") or step.get("hotkeyID") or step.get("name"))
                for step in sequence if isinstance(step, dict)]
    return {"emotions": {name: {"talking": convert(config.get("talking_hotkey_sequence", [])),
                               "idle": convert(config.get("idle_hotkey_sequence", [])),
                               "reset": convert(config.get("reset_hotkey_sequence", [])),
                               "after_speech": "idle"}
                         for name, config in mapping.get("emotions", {}).items()},
            "full_reset": convert(mapping.get("full_reset_hotkey_sequence", []))}


class AdvancedActions:
    def __init__(self, manager):
        self.manager = manager
        self.lock = asyncio.Lock()
        self.emotion = ""
        self.phase = ""
        self.active_config = {}
        self.owned_expressions = set()
        self.model_id = None

    async def _expression(self, action):
        m = self.manager
        file = action["file"]
        active = action["active"]
        state = await m._request_locked("ExpressionStateRequest", {"expressionFile": file, "details": False})
        if not state or state.get("messageType") == "APIError":
            raise RuntimeError(f"Could not inspect expression {file}")
        data = state.get("data", {})
        model_id = data.get("modelID")
        if self.model_id is not None and self.model_id != model_id:
            self.owned_expressions.clear()
        self.model_id = model_id
        matches = [e for e in data.get("expressions", []) if e.get("file") == file]
        if not matches:
            raise RuntimeError(f"Expression unavailable in the current model: {file}")
        if active and not matches[0].get("active"):
            self.owned_expressions.add(file)
        response = await m._request_locked("ExpressionActivationRequest", {
            "expressionFile": file, "active": active, "fadeTime": float(action.get("fade", 0.1))})
        if not response or response.get("messageType") == "APIError":
            raise RuntimeError(f"Expression activation failed: {file}")
        if not active:
            self.owned_expressions.discard(file)

    async def run(self, actions, phase="talking"):
        actions = validate_actions(actions)
        if not actions:
            return
        self.manager.logger(f"VTS advanced {phase}: running {len(actions)} actions.")
        async with self.manager._lock:
            if not await self.manager._connect_locked():
                raise RuntimeError("VTube Studio is not connected.")
            for action in actions:
                kind = action["action"]
                if kind == "wait":
                    await asyncio.sleep(float(action.get("seconds", 0)))
                elif kind == "expression":
                    await self._expression(action)
                elif self.manager._step_is_expression_toggle(action):
                    file = self.manager._expression_file_for_step(action)
                    await self._expression({"file": file, "active": phase != "reset"})
                elif not await self.manager._trigger_hotkey_locked(action["hotkey_id"], action):
                    raise RuntimeError(f"Hotkey failed: {action['hotkey_id']}")

    async def cleanup(self):
        # Deactivate only expressions this engine activated, and only on that model.
        if not self.owned_expressions:
            return
        async with self.manager._lock:
            if not await self.manager._connect_locked():
                raise RuntimeError("Reconnect VTube Studio to clean up active expressions.")
            state = await self.manager._request_locked("ExpressionStateRequest", {"details": False})
            if not state or state.get("messageType") == "APIError":
                raise RuntimeError("Could not inspect expressions for cleanup.")
            if state.get("data", {}).get("modelID") != self.model_id:
                self.owned_expressions.clear()
                return
            for file in list(self.owned_expressions):
                result = await self.manager._request_locked("ExpressionActivationRequest", {
                    "expressionFile": file, "active": False, "fadeTime": 0.1})
                if not result or result.get("messageType") == "APIError":
                    raise RuntimeError(f"Could not clean up expression: {file}")
                self.owned_expressions.discard(file)

    async def _reset(self):
        try:
            if self.emotion:
                await self.run(self.active_config.get("reset", []), "reset")
        finally:
            await self.cleanup()
        self.emotion = self.phase = ""
        self.active_config = {}
        self.manager._current_emotion = ""
        self.manager.apply_emotion_to_idle("neutral")

    async def talking(self, emotion):
        async with self.lock:
            try:
                if self.emotion == emotion and self.phase == "talking":
                    return True
                # A new speech cycle resets the previous idle/talking effects before
                # reading a fresh configuration, including repeated emotions.
                await self._reset()
                config = self.manager.load_vts_mapping().get("advanced", {}).get("emotions", {}).get(emotion)
                if config is None:
                    self.manager.logger(f"No advanced mapping for {emotion}.")
                    return False
                self.emotion = emotion
                self.active_config = copy.deepcopy(config)
                await self.run(config.get("talking", []))
                self.phase = "talking"
                self.manager._current_emotion = emotion
                self.manager.apply_emotion_to_idle(emotion)
                return True
            except BaseException:
                await asyncio.shield(self._reset())
                raise

    async def finish(self):
        async with self.lock:
            if not self.emotion or self.phase == "idle":
                return True
            try:
                if self.active_config.get("after_speech", "idle") == "reset":
                    await self._reset()
                else:
                    await self.run(self.active_config.get("idle", []), "idle")
                    self.phase = "idle"
                return True
            except BaseException:
                await asyncio.shield(self._reset())
                raise

    async def reset(self, full=False):
        async with self.lock:
            await self._reset()
            if full:
                try:
                    await self.run(self.manager.load_vts_mapping().get("advanced", {}).get("full_reset", []), "reset")
                finally:
                    await self.cleanup()
            return True

    async def preview(self, actions, phase, reset_actions=None):
        actions = validate_actions(actions)
        reset_actions = validate_actions(reset_actions or [])
        async with self.lock:
            await self._reset()
            try:
                await self.run(actions, phase)
                await asyncio.sleep(1)
            finally:
                try:
                    if phase != "reset":
                        await self.run(reset_actions, "reset")
                finally:
                    await self.cleanup()
