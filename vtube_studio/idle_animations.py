import asyncio
import copy
import math
import random
import time
from dataclasses import dataclass, field
from typing import Optional

CUSTOM_PARAMS = [
    {"parameterName": "AIFaceAngleX",  "explanation": "AI idle head X",        "min": -30, "max": 30, "defaultValue": 0},
    {"parameterName": "AIFaceAngleY",  "explanation": "AI idle head Y",        "min": -30, "max": 30, "defaultValue": 0},
    {"parameterName": "AIFaceAngleZ",  "explanation": "AI idle head tilt",     "min": -30, "max": 30, "defaultValue": 0},
    {"parameterName": "AIBodyAngleX",  "explanation": "AI idle body X",        "min": -20, "max": 20, "defaultValue": 0},
    {"parameterName": "AIBodyAngleY",  "explanation": "AI idle body Y",        "min": -20, "max": 20, "defaultValue": 0},
    {"parameterName": "AIBodyAngleZ",  "explanation": "AI idle body tilt",     "min": -20, "max": 20, "defaultValue": 0},
    {"parameterName": "AIEyeX",        "explanation": "AI idle gaze X",        "min": -1,  "max": 1,  "defaultValue": 0},
    {"parameterName": "AIEyeY",        "explanation": "AI idle gaze Y",        "min": -1,  "max": 1,  "defaultValue": 0},
    {"parameterName": "AIEyeOpenL",    "explanation": "AI idle L eye open",    "min": 0,   "max": 1,  "defaultValue": 1},
    {"parameterName": "AIEyeOpenR",    "explanation": "AI idle R eye open",    "min": 0,   "max": 1,  "defaultValue": 1},
    {"parameterName": "AIEyeSmileL",   "explanation": "AI idle L eye smile",   "min": 0,   "max": 1,  "defaultValue": 0},
    {"parameterName": "AIEyeSmileR",   "explanation": "AI idle R eye smile",   "min": 0,   "max": 1,  "defaultValue": 0},
    {"parameterName": "AIMouthForm",   "explanation": "AI idle mouth shape",   "min": -1,  "max": 1,  "defaultValue": 0},
    {"parameterName": "AIBreath",      "explanation": "AI idle breath",        "min": -1,  "max": 1,  "defaultValue": 0},
    {"parameterName": "AIBrowLY",      "explanation": "AI idle L brow",        "min": -1,  "max": 1,  "defaultValue": 0},
    {"parameterName": "AIBrowRY",      "explanation": "AI idle R brow",        "min": -1,  "max": 1,  "defaultValue": 0},
]

IDLE_PROFILES = {
    # A relaxed state with visible movement, not a statue.
    "calm": {
        "head":    {"x_amplitude": 8.0,  "y_amplitude": 5.0,  "z_amplitude": 4.0,  "frequency": 0.18},
        "body":    {"x_amplitude": 3.0,  "y_amplitude": 2.0,  "z_amplitude": 1.5,  "frequency": 0.10},
        "eyes":    {"gaze_x_range": 0.35, "gaze_y_range": 0.22, "fixation_hold": (0.25, 0.7), "saccade_speed": 0.8},
        "blink":   {"interval_range": (2.5, 6.5), "blink_duration": 0.12, "wink_chance": 0.025},
        "breath":  {"amplitude": 0.45, "frequency": 0.20, "frequency_drift": 0.03},
        "brows":   {"amplitude": 0.30, "frequency": 0.07, "asymmetry": 0.25},
        "mouth":   {"smile_base": 0.05, "smile_amplitude": 0.15, "frequency": 0.05},
        "bounce":  {"amplitude": 0.8, "frequency": 0.35, "enabled": True},
        "gesture": {"interval_range": (8.0, 22.0)},
    },
    # Leaning in, looking around, interested in their surrounding.
    "curious": {
        "head":    {"x_amplitude": 10.0, "y_amplitude": 7.0,  "z_amplitude": 7.0,  "frequency": 0.24},
        "body":    {"x_amplitude": 4.0,  "y_amplitude": 2.8,  "z_amplitude": 2.5,  "frequency": 0.14},
        "eyes":    {"gaze_x_range": 0.55, "gaze_y_range": 0.40, "fixation_hold": (0.12, 0.4), "saccade_speed": 1.3},
        "blink":   {"interval_range": (2.0, 5.0), "blink_duration": 0.10, "wink_chance": 0.04},
        "breath":  {"amplitude": 0.50, "frequency": 0.26, "frequency_drift": 0.04},
        "brows":   {"amplitude": 0.50, "frequency": 0.14, "asymmetry": 0.45},
        "mouth":   {"smile_base": 0.10, "smile_amplitude": 0.22, "frequency": 0.08},
        "bounce":  {"amplitude": 1.2, "frequency": 0.50, "enabled": True},
        "gesture": {"interval_range": (6.0, 16.0)},
    },
    # Still has presence but locked in.
    "focused": {
        "head":    {"x_amplitude": 2.5,  "y_amplitude": 1.5,  "z_amplitude": 1.2,  "frequency": 0.08},
        "body":    {"x_amplitude": 0.8,  "y_amplitude": 0.5,  "z_amplitude": 0.4,  "frequency": 0.05},
        "eyes":    {"gaze_x_range": 0.12, "gaze_y_range": 0.08, "fixation_hold": (0.5, 1.3), "saccade_speed": 0.5},
        "blink":   {"interval_range": (3.5, 8.0), "blink_duration": 0.10, "wink_chance": 0.0},
        "breath":  {"amplitude": 0.20, "frequency": 0.14, "frequency_drift": 0.008},
        "brows":   {"amplitude": 0.10, "frequency": 0.03, "asymmetry": 0.10},
        "mouth":   {"smile_base": -0.05, "smile_amplitude": 0.08, "frequency": 0.03},
        "bounce":  {"amplitude": 0.3, "frequency": 0.20, "enabled": True},
        "gesture": {"interval_range": (16.0, 40.0)},
    },
    # High intensity energy bouncy, wide movements, expressive.
    "energetic": {
        "head":    {"x_amplitude": 14.0, "y_amplitude": 10.0, "z_amplitude": 8.0,  "frequency": 0.38},
        "body":    {"x_amplitude": 6.0,  "y_amplitude": 4.0,  "z_amplitude": 3.5,  "frequency": 0.24},
        "eyes":    {"gaze_x_range": 0.65, "gaze_y_range": 0.45, "fixation_hold": (0.08, 0.28), "saccade_speed": 1.6},
        "blink":   {"interval_range": (1.5, 3.5), "blink_duration": 0.085, "wink_chance": 0.07},
        "breath":  {"amplitude": 0.65, "frequency": 0.36, "frequency_drift": 0.07},
        "brows":   {"amplitude": 0.70, "frequency": 0.22, "asymmetry": 0.50},
        "mouth":   {"smile_base": 0.18, "smile_amplitude": 0.30, "frequency": 0.12},
        "bounce":  {"amplitude": 2.0, "frequency": 0.70, "enabled": True},
        "gesture": {"interval_range": (4.0, 12.0)},
    },
    # Droopy, heavy, slow, mainly if the AI is not talking to someone or is dormant but still looks "alive", idk.
    "sleepy": {
        "head":    {"x_amplitude": 4.0,  "y_amplitude": 3.0,  "z_amplitude": 2.5,  "frequency": 0.08},
        "body":    {"x_amplitude": 1.5,  "y_amplitude": 1.2,  "z_amplitude": 1.0,  "frequency": 0.05},
        "eyes":    {"gaze_x_range": 0.10, "gaze_y_range": 0.08, "fixation_hold": (0.7, 1.8), "saccade_speed": 0.25},
        "blink":   {"interval_range": (1.2, 3.5), "blink_duration": 0.20, "wink_chance": 0.01},
        "breath":  {"amplitude": 0.35, "frequency": 0.12, "frequency_drift": 0.02},
        "brows":   {"amplitude": 0.12, "frequency": 0.04, "asymmetry": 0.12},
        "mouth":   {"smile_base": -0.08, "smile_amplitude": 0.06, "frequency": 0.03},
        "bounce":  {"amplitude": 0.4, "frequency": 0.15, "enabled": True},
        "gesture": {"interval_range": (18.0, 45.0)},
    },
    # Full send. Everything cranked. For testing limits. Wouldn't recommend for live streaming, but I'm not your mom.
    "dramatic_test": {
        "head":    {"x_amplitude": 20.0, "y_amplitude": 14.0, "z_amplitude": 12.0, "frequency": 0.48},
        "body":    {"x_amplitude": 10.0, "y_amplitude": 6.0,  "z_amplitude": 5.0,  "frequency": 0.30},
        "eyes":    {"gaze_x_range": 0.85, "gaze_y_range": 0.60, "fixation_hold": (0.06, 0.18), "saccade_speed": 2.2},
        "blink":   {"interval_range": (1.0, 2.8), "blink_duration": 0.08, "wink_chance": 0.10},
        "breath":  {"amplitude": 0.90, "frequency": 0.42, "frequency_drift": 0.10},
        "brows":   {"amplitude": 0.90, "frequency": 0.30, "asymmetry": 0.65},
        "mouth":   {"smile_base": 0.0, "smile_amplitude": 0.45, "frequency": 0.18},
        "bounce":  {"amplitude": 3.5, "frequency": 1.0, "enabled": True},
        "gesture": {"interval_range": (3.0, 8.0)},
    },
}

def _hash_noise(n: int) -> float:
    n = (n << 13) ^ n
    n = n * (n * n * 15731 + 789221) + 1376312589
    return 1.0 - (n & 0x7FFFFFFF) / 1073741824.0

def _smooth_noise(t: float, seed: int) -> float:
    i = int(math.floor(t))
    f = t - i
    f = f * f * (3.0 - 2.0 * f)
    return _hash_noise(i + seed) + f * (_hash_noise(i + 1 + seed) - _hash_noise(i + seed))

def _layered_noise(t: float, seed: int, octaves: int = 3) -> float:
    value = 0.0
    amp = 1.0
    freq = 1.0
    total = 0.0
    for i in range(octaves):
        value += amp * _smooth_noise(t * freq, seed + i * 1000)
        total += amp
        amp *= 0.5
        freq *= 2.0
    return value / total

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * min(1.0, max(0.0, t))

def _gesture_double_blink():
    return [
        (0.08, {"AIEyeOpenL": 0.0, "AIEyeOpenR": 0.0}),
        (0.06, {"AIEyeOpenL": 1.0, "AIEyeOpenR": 1.0}),
        (0.10, {"AIEyeOpenL": 0.0, "AIEyeOpenR": 0.0}),
        (0.08, {"AIEyeOpenL": 1.0, "AIEyeOpenR": 1.0}),
    ]

def _gesture_slow_blink():
    return [
        (0.06, {"AIEyeOpenL": 0.5, "AIEyeOpenR": 0.5}),
        (0.08, {"AIEyeOpenL": 0.1, "AIEyeOpenR": 0.1}),
        (0.25, {"AIEyeOpenL": 0.0, "AIEyeOpenR": 0.0}),
        (0.12, {"AIEyeOpenL": 0.4, "AIEyeOpenR": 0.4}),
        (0.08, {"AIEyeOpenL": 1.0, "AIEyeOpenR": 1.0}),
    ]

def _gesture_glance_left():
    return [
        (0.05, {"AIEyeX": -0.7, "AIEyeY": 0.0}),
        (0.35, {"AIEyeX": -0.75, "AIEyeY": 0.05}),
        (0.06, {"AIEyeX": 0.0,  "AIEyeY": 0.0}),
    ]

def _gesture_glance_right():
    return [
        (0.05, {"AIEyeX": 0.7,  "AIEyeY": 0.0}),
        (0.35, {"AIEyeX": 0.75, "AIEyeY": 0.05}),
        (0.06, {"AIEyeX": 0.0,  "AIEyeY": 0.0}),
    ]

def _gesture_brow_raise():
    side = random.choice(["both", "left", "right"])
    if side == "left":
        return [(0.12, {"AIBrowLY": 0.8}), (0.45, {"AIBrowLY": 0.7}), (0.18, {"AIBrowLY": 0.0})]
    elif side == "right":
        return [(0.12, {"AIBrowRY": 0.8}), (0.45, {"AIBrowRY": 0.7}), (0.18, {"AIBrowRY": 0.0})]
    return [(0.12, {"AIBrowLY": 0.75, "AIBrowRY": 0.75}), (0.40, {"AIBrowLY": 0.65, "AIBrowRY": 0.65}), (0.18, {"AIBrowLY": 0.0, "AIBrowRY": 0.0})]

def _gesture_nod():
    return [
        (0.10, {"AIFaceAngleY": -4.0}),
        (0.06, {"AIFaceAngleY": 1.0}),
        (0.08, {"AIFaceAngleY": -2.0}),
        (0.12, {}),
    ]

def _gesture_head_shake():
    return [
        (0.06, {"AIFaceAngleX": 5.0}),
        (0.06, {"AIFaceAngleX": -5.0}),
        (0.06, {"AIFaceAngleX": 3.0}),
        (0.06, {"AIFaceAngleX": -2.0}),
        (0.10, {}),
    ]

def _gesture_smile_flash():
    return [
        (0.15, {"AIMouthForm": 0.6, "AIEyeSmileL": 0.5, "AIEyeSmileR": 0.5}),
        (0.50, {"AIMouthForm": 0.5, "AIEyeSmileL": 0.4, "AIEyeSmileR": 0.4}),
        (0.25, {"AIMouthForm": 0.0, "AIEyeSmileL": 0.0, "AIEyeSmileR": 0.0}),
    ]

def _gesture_dramatic_tilt():
    direction = random.choice([-1, 1])
    return [
        (0.15, {"AIFaceAngleZ": 8.0 * direction, "AIFaceAngleX": 3.0 * direction}),
        (0.60, {"AIFaceAngleZ": 7.0 * direction, "AIFaceAngleX": 2.5 * direction}),
        (0.20, {}),
    ]

def _gesture_look_away_and_back():
    side = random.choice([-1, 1])
    return [
        (0.06, {"AIEyeX": 0.6 * side, "AIEyeY": -0.15, "AIFaceAngleX": 4.0 * side}),
        (0.50, {"AIEyeX": 0.55 * side, "AIEyeY": -0.12, "AIFaceAngleX": 3.5 * side}),
        (0.08, {"AIEyeX": 0.0, "AIEyeY": 0.0}),
        (0.15, {}),
    ]

def _gesture_eye_widen():
    return [
        (0.06, {"AIEyeOpenL": 1.0, "AIEyeOpenR": 1.0, "AIBrowLY": 0.7, "AIBrowRY": 0.7}),
        (0.40, {"AIEyeOpenL": 1.0, "AIEyeOpenR": 1.0, "AIBrowLY": 0.6, "AIBrowRY": 0.6}),
        (0.15, {"AIBrowLY": 0.0, "AIBrowRY": 0.0}),
    ]

def _gesture_excited_bounce():
    return [
        (0.08, {"AIBodyAngleY": 3.5, "AIFaceAngleY": 2.0}),
        (0.08, {"AIBodyAngleY": -1.0, "AIFaceAngleY": -0.5}),
        (0.08, {"AIBodyAngleY": 2.5, "AIFaceAngleY": 1.5}),
        (0.08, {"AIBodyAngleY": -0.5, "AIFaceAngleY": -0.3}),
        (0.10, {}),
    ]

def _gesture_squint():
    return [
        (0.10, {"AIEyeOpenL": 0.4, "AIEyeOpenR": 0.4, "AIMouthForm": 0.2}),
        (0.50, {"AIEyeOpenL": 0.35, "AIEyeOpenR": 0.35, "AIMouthForm": 0.15}),
        (0.15, {"AIEyeOpenL": 1.0, "AIEyeOpenR": 1.0, "AIMouthForm": 0.0}),
    ]

ALL_GESTURES = [
    (_gesture_double_blink,       1.5),
    (_gesture_slow_blink,         1.0),
    (_gesture_glance_left,        1.3),
    (_gesture_glance_right,       1.3),
    (_gesture_brow_raise,         1.5),
    (_gesture_nod,                1.2),
    (_gesture_head_shake,         0.6),
    (_gesture_smile_flash,        1.2),
    (_gesture_dramatic_tilt,      1.0),
    (_gesture_look_away_and_back, 1.0),
    (_gesture_eye_widen,          0.8),
    (_gesture_excited_bounce,     1.0),
    (_gesture_squint,             0.7),
]

@dataclass
class ParamChannel:
    param_name: str
    frequency: float = 0.25
    amplitude: float = 1.0
    octaves: int = 3
    seed: int = 0
    emotion_bias: float = 0.0
    emotion_amplitude_scale: float = 1.0
    enabled: bool = True

@dataclass
class HeadConfig:
    enabled: bool = True
    x_amplitude: float = 8.0
    y_amplitude: float = 5.0
    z_amplitude: float = 4.0
    frequency: float = 0.18

@dataclass
class BodyConfig:
    enabled: bool = True
    x_amplitude: float = 3.0
    y_amplitude: float = 2.0
    z_amplitude: float = 1.5
    frequency: float = 0.1

@dataclass
class EyeConfig:
    enabled: bool = True
    gaze_x_range: float = 0.35
    gaze_y_range: float = 0.22
    fixation_hold: tuple[float, float] = (0.25, 0.7)
    saccade_speed: float = 0.8

@dataclass
class BlinkConfig:
    enabled: bool = True
    interval_range: tuple[float, float] = (2.5, 6.5)
    blink_duration: float = 0.12
    wink_chance: float = 0.025

@dataclass
class BreathConfig:
    enabled: bool = True
    amplitude: float = 0.45
    frequency: float = 0.20
    frequency_drift: float = 0.03

@dataclass
class BrowConfig:
    enabled: bool = True
    amplitude: float = 0.30
    frequency: float = 0.07
    asymmetry: float = 0.25

@dataclass
class MouthConfig:
    enabled: bool = True
    smile_base: float = 0.05
    smile_amplitude: float = 0.15
    frequency: float = 0.05

@dataclass
class BounceConfig:
    enabled: bool = True
    amplitude: float = 0.8       # Degrees of Y-axis bob
    frequency: float = 0.35      # Bobs per second

@dataclass
class GestureConfig:
    enabled: bool = True
    interval_range: tuple[float, float] = (8.0, 22.0)

@dataclass
class IdleConfig:
    head: HeadConfig = field(default_factory=HeadConfig)
    body: BodyConfig = field(default_factory=BodyConfig)
    eyes: EyeConfig = field(default_factory=EyeConfig)
    blink: BlinkConfig = field(default_factory=BlinkConfig)
    breath: BreathConfig = field(default_factory=BreathConfig)
    brows: BrowConfig = field(default_factory=BrowConfig)
    mouth: MouthConfig = field(default_factory=MouthConfig)
    bounce: BounceConfig = field(default_factory=BounceConfig)
    gesture: GestureConfig = field(default_factory=GestureConfig)
    injection_mode: str = "set"

class IdleAnimationEngine:
    # Don't change any values in this engine unless you know what you're doing!
    FPS: int = 15
    FRAME_DURATION: float = 1.0 / FPS

    def __init__(self, vts_client, logger=print):
        self._client = vts_client
        self._logger = logger
        self.config = IdleConfig()
        self._t_origin = random.uniform(0.0, 10000.0)
        self._channels = self._build_channels()

        # Eye saccade.
        self._eye_current = [0.0, 0.0]
        self._eye_target = [0.0, 0.0]
        self._eye_next_saccade: float = 0.0

        # Head follow.
        self._head_follow_target = [0.0, 0.0, 0.0]
        self._head_follow_current = [0.0, 0.0, 0.0]
        self._head_follow_strength: float = 0.45

        # Blink.
        self._eye_lids = [1.0, 1.0]
        self._blink_next: float = 0.0
        self._blink_reopen_at: Optional[float] = None

        # Breath.
        self._breath_phase: float = random.uniform(0.0, math.tau)

        # Body bounce.
        self._bounce_phase: float = random.uniform(0.0, math.tau)

        # Micro-gesture.
        self._gesture_next: float = 0.0
        self._gesture_active: Optional[list[tuple[float, dict]]] = None
        self._gesture_step_idx: int = 0
        self._gesture_step_end: float = 0.0
        self._gesture_overrides: dict[str, float] = {}

        # Profile transition.
        self._transition_duration: float = 1.5
        self._transition_start: float = 0.0
        self._transition_progress: float = 1.0

        # Loop control.
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._params_registered = False
        self._consecutive_send_failures = 0
        self._max_consecutive_send_failures = 8

        # Emotion modifiers.
        self._head_range_scale: float = 1.0
        self._body_range_scale: float = 1.0
        self._eye_range_scale: float = 1.0
        self._head_speed_scale: float = 1.0
        self._blink_speed_scale: float = 1.0
        self._breath_scale: float = 1.0
        self._head_bias = [0.0, 0.0, 0.0]
        self._body_bias = [0.0, 0.0, 0.0]
        self._eye_bias = [0.0, 0.0]

        # Auto-profile.
        self._current_profile_name: str = "calm"
        self._profile_change_interval: tuple[float, float] = (25.0, 55.0)
        self._next_profile_change: float = time.monotonic() + random.uniform(*self._profile_change_interval)
        self._auto_profile_enabled: bool = False

    def _build_channels(self):
        s = random.randint(0, 100000)
        return {
            "head_x": ParamChannel("AIFaceAngleX", seed=s),
            "head_y": ParamChannel("AIFaceAngleY", seed=s+100),
            "head_z": ParamChannel("AIFaceAngleZ", seed=s+200),
            "body_x": ParamChannel("AIBodyAngleX", seed=s+300),
            "body_y": ParamChannel("AIBodyAngleY", seed=s+400),
            "body_z": ParamChannel("AIBodyAngleZ", seed=s+500),
            "brow_l": ParamChannel("AIBrowLY",     seed=s+800),
            "brow_r": ParamChannel("AIBrowRY",     seed=s+900),
            "mouth":  ParamChannel("AIMouthForm",  seed=s+1000),
        }

    async def start(self) -> bool:
        if self._running:
            self._logger("Idle animation already running.")
            return True
        if not await self._register_custom_params():
            self._logger("Failed to register custom params — is VTS connected?")
            return False
        self.set_idle_profile(self._current_profile_name)
        self._running = True
        self._task = asyncio.create_task(self._loop())
        self._logger("Idle animation started.")
        return True

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass
            self._task = None
        await self._send_zero()
        self._logger("Idle animation stopped.")

    @property
    def is_running(self) -> bool:
        return self._running

    def update_head_config(self, **kw):
        for k, v in kw.items():
            if hasattr(self.config.head, k): setattr(self.config.head, k, v)

    def update_body_config(self, **kw):
        for k, v in kw.items():
            if hasattr(self.config.body, k): setattr(self.config.body, k, v)

    def update_eye_config(self, **kw):
        for k, v in kw.items():
            if hasattr(self.config.eyes, k): setattr(self.config.eyes, k, v)

    def update_blink_config(self, **kw):
        for k, v in kw.items():
            if hasattr(self.config.blink, k): setattr(self.config.blink, k, v)

    def update_breath_config(self, **kw):
        for k, v in kw.items():
            if hasattr(self.config.breath, k): setattr(self.config.breath, k, v)

    def update_brow_config(self, **kw):
        for k, v in kw.items():
            if hasattr(self.config.brows, k): setattr(self.config.brows, k, v)

    def update_mouth_config(self, **kw):
        for k, v in kw.items():
            if hasattr(self.config.mouth, k): setattr(self.config.mouth, k, v)

    def update_bounce_config(self, **kw):
        for k, v in kw.items():
            if hasattr(self.config.bounce, k): setattr(self.config.bounce, k, v)

    def update_gesture_config(self, **kw):
        for k, v in kw.items():
            if hasattr(self.config.gesture, k): setattr(self.config.gesture, k, v)

    def set_emotion_modifiers(self, head_range_scale=1.0, body_range_scale=1.0,
                               eye_range_scale=1.0, head_speed_scale=1.0,
                               blink_speed_scale=1.0, breath_scale=1.0,
                               head_bias=None, body_bias=None, eye_bias=None):
        self._head_range_scale = max(0.0, float(head_range_scale))
        self._body_range_scale = max(0.0, float(body_range_scale))
        self._eye_range_scale = max(0.0, float(eye_range_scale))
        self._head_speed_scale = max(0.05, float(head_speed_scale))
        self._blink_speed_scale = max(0.05, float(blink_speed_scale))
        self._breath_scale = max(0.0, float(breath_scale))
        if head_bias is not None: self._head_bias = [float(v) for v in head_bias[:3]]
        if body_bias is not None: self._body_bias = [float(v) for v in body_bias[:3]]
        if eye_bias is not None:  self._eye_bias = [float(v) for v in eye_bias[:2]]

    def set_idle_profile(self, profile_name: str) -> bool:
        profile_name = (profile_name or "").strip().lower()
        profile = IDLE_PROFILES.get(profile_name)
        if not profile:
            self._logger(f"Unknown idle profile: {profile_name}")
            return False
        self._transition_start = time.monotonic()
        self._transition_progress = 0.0
        for section, updater in [
            ("head", self.update_head_config), ("body", self.update_body_config),
            ("eyes", self.update_eye_config), ("blink", self.update_blink_config),
            ("breath", self.update_breath_config), ("brows", self.update_brow_config),
            ("mouth", self.update_mouth_config), ("bounce", self.update_bounce_config),
            ("gesture", self.update_gesture_config),
        ]:
            data = profile.get(section, {})
            if data: updater(**data)
        self._current_profile_name = profile_name
        self._logger(f"Idle profile set: {profile_name}")
        return True

    def get_idle_profile(self) -> str:
        return self._current_profile_name

    def set_auto_profiles(self, enabled: bool, interval_range=None):
        self._auto_profile_enabled = bool(enabled)
        if interval_range is not None:
            lo, hi = interval_range
            self._profile_change_interval = (max(5.0, float(lo)), max(6.0, float(hi)))
        self._next_profile_change = time.monotonic() + random.uniform(*self._profile_change_interval)

    async def _register_custom_params(self) -> bool:
        if self._params_registered:
            return True
        try:
            for p in CUSTOM_PARAMS:
                await self._client.create_parameter(
                    parameter_name=p["parameterName"], min_value=p["min"],
                    max_value=p["max"], default_value=p["defaultValue"],
                    explanation=p.get("explanation", ""),
                )
            self._params_registered = True
            self._logger(f"Registered {len(CUSTOM_PARAMS)} custom VTS parameters.")
            return True
        except Exception as e:
            self._logger(f"Custom param registration failed: {e}")
            return False

    async def _loop(self) -> None:
        self._logger("Idle animation loop entering main cycle.")
        while self._running:
            frame_start = time.monotonic()
            now = time.monotonic()
            t = now - self._t_origin

            self._maybe_rotate_idle_profile(now)
            self._tick_transition(now)

            out = {}
            self._tick_head(t, out)
            self._tick_body(t, out)
            self._tick_bounce(t, out)
            self._tick_eyes(now, out)
            self._tick_head_follow(out)
            self._tick_blink(now, out)
            self._tick_breath(t, out)
            self._tick_brows(t, out)
            self._tick_mouth(t, out)
            self._tick_gesture(now, out)

            vts_params = [{"id": k, "value": float(v)} for k, v in out.items()]
            if vts_params:
                await self._send_params(vts_params)

            elapsed = time.monotonic() - frame_start
            await asyncio.sleep(max(0.0, self.FRAME_DURATION - elapsed))

    def _tick_head(self, t, out):
        if not self.config.head.enabled:
            return
        cfg = self.config.head
        freq = cfg.frequency * self._head_speed_scale
        ch = self._channels
        nx = _layered_noise(t * freq, ch["head_x"].seed) * cfg.x_amplitude * self._head_range_scale
        ny = _layered_noise(t * freq * 0.85, ch["head_y"].seed) * cfg.y_amplitude * self._head_range_scale
        nz = _layered_noise(t * freq * 0.6, ch["head_z"].seed) * cfg.z_amplitude * self._head_range_scale
        out["AIFaceAngleX"] = nx + self._head_follow_current[0] + self._head_bias[0]
        out["AIFaceAngleY"] = ny + self._head_follow_current[1] + self._head_bias[1]
        out["AIFaceAngleZ"] = nz + self._head_follow_current[2] + self._head_bias[2]

    def _tick_body(self, t, out):
        if not self.config.body.enabled:
            return
        cfg = self.config.body
        f = cfg.frequency
        ch = self._channels
        out["AIBodyAngleX"] = _layered_noise(t*f, ch["body_x"].seed, 2) * cfg.x_amplitude * self._body_range_scale + self._body_bias[0]
        out["AIBodyAngleY"] = _layered_noise(t*f*0.7, ch["body_y"].seed, 2) * cfg.y_amplitude * self._body_range_scale + self._body_bias[1]
        out["AIBodyAngleZ"] = _layered_noise(t*f*0.5, ch["body_z"].seed, 2) * cfg.z_amplitude * self._body_range_scale + self._body_bias[2]

    def _tick_bounce(self, t, out):
        if not self.config.bounce.enabled:
            return
        cfg = self.config.bounce
        raw = math.sin(self._bounce_phase)
        bounce = raw * cfg.amplitude
        self._bounce_phase += cfg.frequency * self.FRAME_DURATION * math.tau
        mod = 0.8 + 0.2 * _smooth_noise(t * 0.12, 77777)
        bounce *= mod
        out["AIBodyAngleY"] = out.get("AIBodyAngleY", 0.0) + bounce
        out["AIFaceAngleY"] = out.get("AIFaceAngleY", 0.0) - bounce * 0.25

    def _tick_eyes(self, now, out):
        if not self.config.eyes.enabled:
            out["AIEyeX"] = self._eye_bias[0]
            out["AIEyeY"] = self._eye_bias[1]
            return
        cfg = self.config.eyes
        if now >= self._eye_next_saccade:
            rx = cfg.gaze_x_range * self._eye_range_scale
            ry = cfg.gaze_y_range * self._eye_range_scale
            self._eye_target = [
                random.uniform(-rx, rx) + self._eye_bias[0],
                random.uniform(-ry, ry) + self._eye_bias[1],
            ]
            hold_lo, hold_hi = cfg.fixation_hold
            self._eye_next_saccade = now + random.uniform(hold_lo, hold_hi)
            self._head_follow_target[0] = self._eye_target[0] * self._head_follow_strength * 10.0
            self._head_follow_target[1] = self._eye_target[1] * self._head_follow_strength * 8.0
        snap = 0.5 + cfg.saccade_speed * 0.4
        self._eye_current[0] = _lerp(self._eye_current[0], self._eye_target[0], snap)
        self._eye_current[1] = _lerp(self._eye_current[1], self._eye_target[1], snap)
        out["AIEyeX"] = self._eye_current[0]
        out["AIEyeY"] = self._eye_current[1]

    def _tick_head_follow(self, out):
        spd = 0.08
        for i in range(3):
            self._head_follow_current[i] = _lerp(self._head_follow_current[i], self._head_follow_target[i], spd)

    def _tick_blink(self, now, out):
        if not self.config.blink.enabled:
            out["AIEyeOpenL"] = 1.0
            out["AIEyeOpenR"] = 1.0
            return
        cfg = self.config.blink
        if self._blink_reopen_at is not None:
            if now >= self._blink_reopen_at:
                self._eye_lids = [1.0, 1.0]
                self._blink_reopen_at = None
        elif now >= self._blink_next:
            lo, hi = cfg.interval_range
            self._blink_next = now + random.uniform(lo * self._blink_speed_scale, hi * self._blink_speed_scale)
            self._blink_reopen_at = now + cfg.blink_duration
            if random.random() < cfg.wink_chance:
                self._eye_lids = [0.0, 1.0] if random.random() < 0.5 else [1.0, 0.0]
            else:
                self._eye_lids = [0.0, 0.0]
        out["AIEyeOpenL"] = self._eye_lids[0]
        out["AIEyeOpenR"] = self._eye_lids[1]

    def _tick_breath(self, t, out):
        if not self.config.breath.enabled:
            return
        cfg = self.config.breath
        drift = _smooth_noise(t * 0.05, 99999) * cfg.frequency_drift
        self._breath_phase += (cfg.frequency + drift) * self.FRAME_DURATION * math.tau
        out["AIBreath"] = math.sin(self._breath_phase) * cfg.amplitude * self._breath_scale

    def _tick_brows(self, t, out):
        if not self.config.brows.enabled:
            return
        cfg = self.config.brows
        f = cfg.frequency
        bl = _layered_noise(t * f, self._channels["brow_l"].seed, 2) * cfg.amplitude
        br = _layered_noise(t * f, self._channels["brow_r"].seed, 2) * cfg.amplitude
        shared = (bl + br) * 0.5
        out["AIBrowLY"] = _lerp(shared, bl, cfg.asymmetry)
        out["AIBrowRY"] = _lerp(shared, br, cfg.asymmetry)

    def _tick_mouth(self, t, out):
        if not self.config.mouth.enabled:
            return
        cfg = self.config.mouth
        noise = _layered_noise(t * cfg.frequency, self._channels["mouth"].seed, 2)
        smile = cfg.smile_base + noise * cfg.smile_amplitude
        out["AIMouthForm"] = max(-1.0, min(1.0, smile))
        eye_smile = max(0.0, smile) * 0.45
        out["AIEyeSmileL"] = eye_smile
        out["AIEyeSmileR"] = eye_smile

    def _tick_gesture(self, now, out):
        if not self.config.gesture.enabled:
            self._gesture_overrides.clear()
            return
        if self._gesture_active is not None:
            if now >= self._gesture_step_end:
                self._gesture_step_idx += 1
                if self._gesture_step_idx >= len(self._gesture_active):
                    self._gesture_active = None
                    self._gesture_overrides.clear()
                    return
                dur, overrides = self._gesture_active[self._gesture_step_idx]
                self._gesture_overrides = overrides
                self._gesture_step_end = now + dur
            out.update(self._gesture_overrides)
            return
        if now >= self._gesture_next:
            lo, hi = self.config.gesture.interval_range
            self._gesture_next = now + random.uniform(lo, hi)
            total_w = sum(w for _, w in ALL_GESTURES)
            r = random.uniform(0, total_w)
            cum = 0.0
            for factory, weight in ALL_GESTURES:
                cum += weight
                if r <= cum:
                    self._gesture_active = factory()
                    self._gesture_step_idx = 0
                    dur, overrides = self._gesture_active[0]
                    self._gesture_overrides = overrides
                    self._gesture_step_end = now + dur
                    return

    def _tick_transition(self, now):
        if self._transition_progress >= 1.0:
            return
        self._transition_progress = min(1.0, (now - self._transition_start) / self._transition_duration)

    def _maybe_rotate_idle_profile(self, now):
        if not self._auto_profile_enabled or now < self._next_profile_change:
            return
        candidates = ["calm", "curious", "focused", "energetic"]
        if self._current_profile_name in candidates and len(candidates) > 1:
            candidates = [p for p in candidates if p != self._current_profile_name]
        self.set_idle_profile(random.choice(candidates))
        self._next_profile_change = now + random.uniform(*self._profile_change_interval)

    async def _send_params(self, param_values):
        try:
            await self._client.inject_parameter_values(
                param_values, mode=self.config.injection_mode,
                await_response=False, timeout=2.0,
            )
            self._consecutive_send_failures = 0
        except asyncio.TimeoutError:
            self._consecutive_send_failures += 1
        except Exception as e:
            self._consecutive_send_failures += 1
            if self._consecutive_send_failures == 1 or random.random() < 0.02:
                self._logger(f"Idle param injection hiccup: {e}")
        if self._consecutive_send_failures >= self._max_consecutive_send_failures:
            self._logger("Idle animation stopping after repeated VTS parameter injection failures.")
            self._running = False

    async def _send_zero(self):
        zero = [{"id": p["parameterName"], "value": p["defaultValue"]} for p in CUSTOM_PARAMS]
        try:
            await self._client.inject_parameter_values(
                zero, mode=self.config.injection_mode, await_response=True, timeout=2.0,
            )
        except Exception:
            pass

EMOTION_TO_IDLE_PROFILE: dict[str, str] = {
    "neutral": "calm", "happy": "energetic", "love": "curious",
    "confused": "curious", "thinking": "focused", "sad": "sleepy",
    "sleepy": "sleepy", "embarrassed": "curious", "angry": "energetic",
    "surprised": "energetic",
}

EMOTION_PRESETS: dict[str, dict] = {
    "neutral": {
        "head_range_scale": 1.0, "body_range_scale": 1.0, "eye_range_scale": 1.0,
        "head_speed_scale": 1.0, "blink_speed_scale": 1.0, "breath_scale": 1.0,
        "head_bias": [0.0, 0.0, 0.0], "body_bias": [0.0, 0.0, 0.0], "eye_bias": [0.0, 0.0],
    },
    "happy": {
        "head_range_scale": 1.15, "body_range_scale": 1.20, "eye_range_scale": 1.10,
        "head_speed_scale": 1.10, "blink_speed_scale": 0.90, "breath_scale": 1.15,
        "head_bias": [0.0, 0.4, 0.0], "body_bias": [0.0, 0.2, 0.0], "eye_bias": [0.0, 0.04],
    },
    "curious": {
        "head_range_scale": 1.05, "body_range_scale": 0.95, "eye_range_scale": 1.30,
        "head_speed_scale": 0.95, "blink_speed_scale": 1.0, "breath_scale": 1.0,
        "head_bias": [0.0, 0.2, 1.2], "body_bias": [0.3, 0.0, 0.2], "eye_bias": [0.0, 0.08],
    },
    "thinking": {
        "head_range_scale": 0.65, "body_range_scale": 0.55, "eye_range_scale": 0.75,
        "head_speed_scale": 0.70, "blink_speed_scale": 1.25, "breath_scale": 0.85,
        "head_bias": [0.0, -0.5, -0.8], "body_bias": [0.0, -0.2, 0.0], "eye_bias": [0.0, -0.04],
    },
    "confused": {
        "head_range_scale": 1.0, "body_range_scale": 0.85, "eye_range_scale": 1.25,
        "head_speed_scale": 0.90, "blink_speed_scale": 1.15, "breath_scale": 0.95,
        "head_bias": [0.0, -0.2, 1.5], "body_bias": [0.0, 0.0, -0.4], "eye_bias": [0.0, 0.02],
    },
    "sad": {
        "head_range_scale": 0.65, "body_range_scale": 0.55, "eye_range_scale": 0.60,
        "head_speed_scale": 0.65, "blink_speed_scale": 1.35, "breath_scale": 0.75,
        "head_bias": [0.0, -1.8, -0.4], "body_bias": [0.0, -0.8, -0.2], "eye_bias": [0.0, -0.12],
    },
    "sleepy": {
        "head_range_scale": 0.45, "body_range_scale": 0.45, "eye_range_scale": 0.45,
        "head_speed_scale": 0.45, "blink_speed_scale": 1.60, "breath_scale": 0.65,
        "head_bias": [0.0, -2.2, 0.0], "body_bias": [0.0, -0.8, 0.0], "eye_bias": [0.0, -0.18],
    },
    "love": {
        "head_range_scale": 0.95, "body_range_scale": 0.85, "eye_range_scale": 0.85,
        "head_speed_scale": 0.75, "blink_speed_scale": 1.20, "breath_scale": 0.9,
        "head_bias": [0.0, 0.2, 1.0], "body_bias": [0.0, 0.0, 0.3], "eye_bias": [0.0, 0.04],
    },
    "embarrassed": {
        "head_range_scale": 0.75, "body_range_scale": 0.65, "eye_range_scale": 0.90,
        "head_speed_scale": 0.80, "blink_speed_scale": 1.30, "breath_scale": 0.85,
        "head_bias": [0.0, -1.0, 1.0], "body_bias": [0.0, -0.3, 0.3], "eye_bias": [0.0, -0.08],
    },
    "angry": {
        "head_range_scale": 0.90, "body_range_scale": 1.10, "eye_range_scale": 0.65,
        "head_speed_scale": 1.05, "blink_speed_scale": 0.80, "breath_scale": 1.2,
        "head_bias": [0.0, 0.3, -0.3], "body_bias": [0.0, 0.4, 0.0], "eye_bias": [0.0, 0.0],
    },
    "surprised": {
        "head_range_scale": 1.25, "body_range_scale": 1.15, "eye_range_scale": 1.10,
        "head_speed_scale": 1.25, "blink_speed_scale": 0.75, "breath_scale": 1.2,
        "head_bias": [0.0, 1.0, 0.0], "body_bias": [0.0, 0.6, 0.0], "eye_bias": [0.0, 0.12],
    },
}