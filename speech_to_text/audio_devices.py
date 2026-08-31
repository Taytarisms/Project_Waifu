from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import re
import sys
import unicodedata
from typing import Iterable, Optional


def normalize_device_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"\(\s*\d+\s*-\s*", "(", text)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def parse_preferred_patterns(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        raw = [str(item) for item in value]
    else:
        raw = re.split(r"[\r\n;]+", str(value or ""))
    result: list[str] = []
    for item in raw:
        clean = str(item).strip()
        if clean and clean not in result:
            result.append(clean)
    return tuple(result)


@dataclass(frozen=True)
class AudioDeviceDescriptor:
    index: int
    name: str
    host_api: str
    input_channels: int
    default_sample_rate: float
    is_default: bool = False

    @property
    def normalized_name(self) -> str:
        return normalize_device_name(self.name)

    @property
    def stable_id(self) -> str:
        material = "|".join(
            (
                normalize_device_name(self.host_api),
                self.normalized_name,
                str(self.input_channels),
                str(int(round(self.default_sample_rate or 0))),
            )
        )
        digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]
        return f"audio:{digest}"

    @property
    def label(self) -> str:
        default = " · default" if self.is_default else ""
        return (
            f"{self.name} · {self.host_api} · "
            f"{self.input_channels} input channel(s){default}"
        )


@dataclass(frozen=True)
class DeviceResolution:
    device: Optional[AudioDeviceDescriptor]
    reason: str
    score: float = 0.0
    ambiguous: bool = False
    alternatives: tuple[AudioDeviceDescriptor, ...] = ()


def _default_input_index(sounddevice_module) -> Optional[int]:
    try:
        value = sounddevice_module.default.device
        if isinstance(value, (tuple, list)):
            value = value[0]
        index = int(value)
        return index if index >= 0 else None
    except Exception:
        return None


def enumerate_input_devices(sounddevice_module=None) -> list[AudioDeviceDescriptor]:
    if sounddevice_module is None:
        import sounddevice as sounddevice_module

    devices = sounddevice_module.query_devices()
    try:
        host_apis = sounddevice_module.query_hostapis()
    except Exception:
        host_apis = []
    default_index = _default_input_index(sounddevice_module)

    result: list[AudioDeviceDescriptor] = []
    for index, raw in enumerate(devices):
        try:
            channels = int(raw.get("max_input_channels", 0) or 0)
        except Exception:
            channels = 0
        if channels <= 0:
            continue

        try:
            host_index = int(raw.get("hostapi", -1))
            host_api = str(host_apis[host_index].get("name", "Unknown"))
        except Exception:
            host_api = "Unknown"

        result.append(
            AudioDeviceDescriptor(
                index=index,
                name=str(raw.get("name", f"Input {index}")),
                host_api=host_api,
                input_channels=channels,
                default_sample_rate=float(raw.get("default_samplerate", 0.0) or 0.0),
                is_default=index == default_index,
            )
        )
    return result


def _host_api_bonus(host_api: str) -> float:
    name = normalize_device_name(host_api)
    if sys.platform == "win32":
        order = ("wasapi", "wdm ks", "directsound", "mme")
    elif sys.platform == "darwin":
        order = ("core audio",)
    else:
        order = ("pipewire", "pulse", "jack", "alsa")
    for position, preferred in enumerate(order):
        if preferred in name:
            return float((len(order) - position) * 10)
    return 0.0


def _pattern_score(device: AudioDeviceDescriptor, pattern: str) -> float:
    wanted = normalize_device_name(pattern)
    actual = device.normalized_name
    if not wanted or not actual:
        return 0.0
    if wanted == actual:
        return 6_000.0
    if wanted in actual:
        return 4_000.0 + min(500.0, len(wanted) * 5.0)

    wanted_tokens = set(wanted.split())
    actual_tokens = set(actual.split())
    if wanted_tokens and wanted_tokens.issubset(actual_tokens):
        return 3_000.0 + len(wanted_tokens) * 50.0

    similarity = SequenceMatcher(None, wanted, actual).ratio()
    if similarity >= 0.78:
        return 1_000.0 * similarity
    return 0.0


def resolve_input_device(
    devices: Iterable[AudioDeviceDescriptor],
    *,
    mode: str = "automatic",
    saved_id: str = "",
    saved_name: str = "",
    saved_host_api: str = "",
    preferred_patterns: object = (),
    legacy_index: Optional[int] = None,
) -> DeviceResolution:
    candidates = list(devices)
    if not candidates:
        return DeviceResolution(None, "No active input devices were found.")

    saved_id = str(saved_id or "").strip()
    if saved_id:
        for device in candidates:
            if device.stable_id == saved_id:
                return DeviceResolution(device, "Matched the saved stable device identifier.", 10_000.0)

    normalized_saved = normalize_device_name(saved_name)
    normalized_host = normalize_device_name(saved_host_api)
    if normalized_saved:
        exact = [device for device in candidates if device.normalized_name == normalized_saved]
        if normalized_host:
            same_host = [
                device
                for device in exact
                if normalize_device_name(device.host_api) == normalized_host
            ]
            if same_host:
                return DeviceResolution(
                    same_host[0],
                    "Matched the saved device name and host API.",
                    9_000.0,
                )
        if len(exact) == 1:
            return DeviceResolution(exact[0], "Matched the saved device name.", 8_000.0)

    try:
        legacy = int(legacy_index) if legacy_index is not None else None
    except Exception:
        legacy = None

    if str(mode or "automatic").strip().lower() == "manual":
        if legacy is not None:
            for device in candidates:
                if device.index == legacy:
                    return DeviceResolution(
                        device,
                        "Migrated the legacy manual device index to a stable descriptor.",
                        7_000.0,
                    )
        default = next((device for device in candidates if device.is_default), candidates[0])
        return DeviceResolution(
            default,
            "The saved manual device is unavailable; using the current system default.",
            100.0,
        )

    patterns = parse_preferred_patterns(preferred_patterns)
    ranked: list[tuple[float, AudioDeviceDescriptor, str]] = []
    for device in candidates:
        best_pattern = ""
        best = 0.0
        for pattern in patterns:
            score = _pattern_score(device, pattern)
            if score > best:
                best = score
                best_pattern = pattern
        if best:
            best += _host_api_bonus(device.host_api)
            if device.is_default:
                best += 5.0
            ranked.append((best, device, best_pattern))

    if ranked:
        ranked.sort(key=lambda item: (-item[0], item[1].index))
        best_score, best_device, best_pattern = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        ambiguous = bool(second_score and best_score - second_score < 25.0)
        alternatives = tuple(item[1] for item in ranked[:4])
        if ambiguous:
            fallback = next(
                (device for device in candidates if legacy is not None and device.index == legacy),
                None,
            )
            if fallback is None:
                fallback = next(
                    (device for device in candidates if device.is_default),
                    candidates[0],
                )
            return DeviceResolution(
                fallback,
                (
                    f"Preferred pattern {best_pattern!r} matched several input endpoints; "
                    "keeping the legacy/default input until a specific pattern or manual device is chosen."
                ),
                best_score,
                True,
                alternatives,
            )
        reason = f"Matched preferred input pattern {best_pattern!r}."
        return DeviceResolution(
            best_device,
            reason,
            best_score,
            False,
            tuple(item[1] for item in ranked[1:4]),
        )

    if legacy is not None:
        for device in candidates:
            if device.index == legacy:
                return DeviceResolution(
                    device,
                    "No preferred pattern matched; migrated the legacy device index.",
                    500.0,
                )

    default = next((device for device in candidates if device.is_default), candidates[0])
    return DeviceResolution(default, "No preferred pattern matched; using the system default input.", 100.0)
