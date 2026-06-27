import json
import shutil
from dataclasses import dataclass, asdict, fields
from pathlib import Path
from typing import Optional, Any

try:
    from .qwen3 import Qwen3TTSConfig
except ImportError:
    from qwen3 import Qwen3TTSConfig

VOICE_ROOT = Path(__file__).resolve().parents[1] / "userdata" / "voices"
LEGACY_PATH = Path(__file__).resolve().parents[1] / "userdata" / "qwen3_voices"


def _voice_root() -> Path:
    if LEGACY_PATH.exists() and not VOICE_ROOT.exists():
        LEGACY_PATH.rename(VOICE_ROOT)

    VOICE_ROOT.mkdir(parents=True, exist_ok=True)
    return VOICE_ROOT

def _fix_model_for_clone(model_id: str, ref_audio: Optional[str]) -> str:
    model_id = model_id or ""
    if ref_audio and "customvoice" in model_id.lower():
        fixed = model_id.replace("CustomVoice", "Base").replace("customvoice", "Base")
        print(f"Auto-switching model for voice clone: {model_id} -> {fixed}")
        return fixed
    return model_id

def _safe_name(name: str) -> str:
    cleaned = "".join(c for c in name.strip() if c.isalnum() or c in (" ", "_", "-"))
    cleaned = cleaned.strip().replace(" ", "_")
    return cleaned or "voice"

def preset_folder(name: str) -> Path:
    return _voice_root() / _safe_name(name)

def preset_exists(name: str) -> bool:
    if not name or not name.strip():
        return False
    return (preset_folder(name) / "voice.json").is_file()

@dataclass
class VoicePreset:
    name: str
    mode: str
    model_id: str
    engine: str = "qwen3"

    language: str = "English"
    speaker: str = "ryan"
    ref_audio: Optional[str] = None
    ref_text: str = ""
    xvec_only: bool = True
    instruct: str = ""
    device: str = "auto"
    backend: str = "auto"
    streaming_chunk_size: int = 8
    sample_rate: int = 24000

    num_step: int = 32
    speed: float = 1.0
    duration: Optional[float] = None

    def to_config(self) -> Qwen3TTSConfig:
        model_id = _fix_model_for_clone(self.model_id, self.ref_audio)
        backend = (self.backend or "auto").strip().lower()

        return Qwen3TTSConfig(
            model_id=model_id,
            device=self.device or "auto",
            backend=backend,
            speaker=self.speaker or "ryan",
            language=self.language or "English",
            instruct=self.instruct or "",
            ref_audio=self.ref_audio,
            ref_text=self.ref_text or "",
            xvec_only=self.xvec_only,
            streaming_chunk_size=int(self.streaming_chunk_size or 8),
            sample_rate=int(self.sample_rate or 24000),
        )

def _preset_from_data(data: dict) -> VoicePreset:
    data = dict(data)
    data.setdefault("engine", "qwen3")
    data.setdefault("device", "auto")
    data.setdefault("backend", "auto")
    data.setdefault("streaming_chunk_size", 8)
    data.setdefault("sample_rate", 24000)
    data.setdefault("num_step", 32)
    data.setdefault("speed", 1.0)
    data.setdefault("duration", None)

    allowed = {f.name for f in fields(VoicePreset)}
    cleaned = {k: v for k, v in data.items() if k in allowed}
    return VoicePreset(**cleaned)

def list_presets() -> list[VoicePreset]:
    root = _voice_root()
    presets: list[VoicePreset] = []
    for path in root.glob("*/voice.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            presets.append(_preset_from_data(data))
        except Exception as e:
            print(f"Failed loading {path}: {e}")
    return sorted(presets, key=lambda p: p.name.lower())

list_qwen3_voice_presets = list_presets

def save_preset(
    *,
    name: str,
    mode: str,
    model_id: str,
    engine: str = "qwen3",
    language: str = "English",
    speaker: str = "ryan",
    ref_audio_source: Optional[str] = None,
    ref_text: str = "",
    xvec_only: bool = True,
    instruct: str = "",
    device: str = "auto",
    backend: str = "auto",
    streaming_chunk_size: int = 8,
    num_step: int = 32,
    speed: float = 1.0,
    duration: Optional[float] = None,
    sample_rate: int = 24000,
) -> VoicePreset:
    _voice_root()
    folder = preset_folder(name)
    folder.mkdir(parents=True, exist_ok=True)

    if engine == "qwen3":
        model_id = _fix_model_for_clone(model_id, ref_audio_source)

    ref_audio_dest: Optional[str] = None

    if ref_audio_source:
        src = Path(ref_audio_source)

        if not src.is_file():
            raise FileNotFoundError(f"Reference audio not found: {src}")

        suffix = src.suffix.lower() or ".wav"
        ref_audio_dest_path = folder / f"reference{suffix}"

        if src.resolve() != ref_audio_dest_path.resolve():
            shutil.copy2(src, ref_audio_dest_path)

        ref_audio_dest = str(ref_audio_dest_path)

    preset = VoicePreset(
        name=name,
        mode=mode,
        model_id=model_id,
        engine=engine,
        language=language,
        speaker=speaker,
        ref_audio=ref_audio_dest,
        ref_text=ref_text,
        xvec_only=xvec_only,
        instruct=instruct,
        device=device,
        backend=backend,
        streaming_chunk_size=int(streaming_chunk_size or 8),
        num_step=int(num_step or 32),
        speed=float(speed or 1.0),
        duration=duration,
        sample_rate=int(sample_rate or 24000),
    )

    (folder / "voice.json").write_text(
        json.dumps(asdict(preset), indent=4),
        encoding="utf-8",
    )

    return preset

save_qwen3_voice_preset = save_preset
def load_preset(name: str) -> VoicePreset:
    path = preset_folder(name) / "voice.json"

    if not path.is_file():
        raise FileNotFoundError(f"Voice preset not found: {name}")

    data = json.loads(path.read_text(encoding="utf-8"))
    return _preset_from_data(data)


load_qwen3_voice_preset = load_preset

def delete_preset(name: str) -> None:
    folder = preset_folder(name)
    if folder.exists():
        shutil.rmtree(folder)

delete_qwen3_voice_preset = delete_preset

def ensure_preset(
    name: str,
    engine: str,
    *,
    model_id: str = "",
    language: str = "English",
    speaker: str = "ryan",
    ref_audio: str = "",
    ref_text: str = "",
    xvec_only: bool = True,
    instruct: str = "",
    device: str = "auto",
    backend: str = "auto",
    streaming_chunk_size: int = 8,
    num_step: int = 32,
    speed: float = 1.0,
    duration: Optional[float] = None,
    sample_rate: int = 24000,
) -> VoicePreset:
    if preset_exists(name):
        preset = load_preset(name)

        changed = False

        if preset.engine == "qwen3" and preset.ref_audio:
            fixed_model = _fix_model_for_clone(preset.model_id, preset.ref_audio)
            if fixed_model != preset.model_id:
                preset.model_id = fixed_model
                changed = True

        if changed:
            folder = preset_folder(name)
            (folder / "voice.json").write_text(
                json.dumps(asdict(preset), indent=4),
                encoding="utf-8",
            )

        return preset

    if engine == "qwen3" and ref_audio:
        model_id = _fix_model_for_clone(model_id, ref_audio)

    if ref_audio:
        mode = "voice_clone"
    else:
        mode = "custom_voice"

    if not model_id:
        if ref_audio:
            model_id = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
        else:
            model_id = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"

    print(
        f"Creating preset '{name}' "
        f"(engine={engine}, mode={mode}, model={model_id}, backend={backend})"
    )

    return save_preset(
        name=name,
        mode=mode,
        model_id=model_id,
        engine=engine,
        language=language,
        speaker=speaker,
        ref_audio_source=ref_audio or None,
        ref_text=ref_text,
        xvec_only=xvec_only,
        instruct=instruct,
        device=device,
        backend=backend,
        streaming_chunk_size=streaming_chunk_size,
        num_step=num_step,
        speed=speed,
        duration=duration,
        sample_rate=sample_rate,
    )


def _bool_setting(value: Any, default: bool = True) -> bool:
    if value in ("", None):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _int_setting(value: Any, default: int) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _float_setting(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value in ("", None):
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def ensure_preset_from_settings(
    engine: str,
    get_settings_fn,
) -> Optional[VoicePreset]:
    if get_settings_fn is None:
        return None

    if engine == "qwen3":
        prefix = "qwen3_tts_"
        name = get_settings_fn(f"{prefix}preset") or ""

        if not name:
            return None

        ref_audio = get_settings_fn(f"{prefix}ref_audio") or ""
        model_id = get_settings_fn(f"{prefix}model_id") or ""

        return ensure_preset(
            name=name,
            engine="qwen3",
            model_id=model_id,
            language=get_settings_fn(f"{prefix}language") or "English",
            speaker=get_settings_fn(f"{prefix}speaker") or "ryan",
            ref_audio=ref_audio,
            ref_text=get_settings_fn(f"{prefix}ref_text") or "",
            xvec_only=_bool_setting(get_settings_fn(f"{prefix}xvec_only"), True),
            instruct=get_settings_fn(f"{prefix}instruct") or "",
            device=get_settings_fn(f"{prefix}device") or "auto",
            backend=get_settings_fn(f"{prefix}backend") or "auto",
            streaming_chunk_size=_int_setting(
                get_settings_fn(f"{prefix}streaming_chunk_size"),
                8,
            ),
            duration=_float_setting(get_settings_fn(f"{prefix}duration"), None),
            sample_rate=24000,
        )