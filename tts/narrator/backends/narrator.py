from typing import Optional
from files.system_setup.settings import get_settings
from files.system_setup.system_logger import Logger
from .Base import NarratorBackend
from .novelai_backend import NovelAIBackend
from .edge_backend import EdgeBackend
from .piper_backend import PiperBackend

ALL_BACKENDS = [NovelAIBackend, EdgeBackend, PiperBackend]
SOURCE_TTS = frozenset({"manual", "stt"})

def get_current_bool(name: str, default: bool = False) -> bool:
    try:
        setting = get_settings(name)
    except Exception:
        return default
    if setting is None or setting == "":
        return default
    if isinstance(setting, bool):
        return setting
    if isinstance(setting, (int, float)):
        return bool(setting)
    return str(setting).strip().lower() in ("true", "1", "yes", "on")

def get_int_settings(name: str, default: Optional[int] = None) -> Optional[int]:
    try:
        setting = get_settings(name)
    except Exception:
        return default
    if setting is None or setting == "":
        return default
    try:
        return int(setting)
    except Exception:
        return default

class Narrator:
    def __init__(self) -> None:
        self.backends: dict[str, NarratorBackend] = {}
        for cls in ALL_BACKENDS:
            try:
                self.backends[cls.backend_id] = cls()
            except Exception as e:
                Logger.warn(f"Failed to construct {cls.__name__}: {e}")

    def list_backends(self) -> list[NarratorBackend]:
        return list(self.backends.values())

    def get_backend(self, backend_id: str) -> Optional[NarratorBackend]:
        return self.backends.get((backend_id or "").strip().lower())

    def load_backend(self, backend_id: str) -> Optional[NarratorBackend]:
        return self.get_backend(backend_id)

    def is_enabled(self) -> bool:
        return get_current_bool("narrator_enabled", default=False)

    async def narrate_message(self, text: str, *, source: str = "") -> bool:
        text = (text or "").strip()
        if not text:
            return False
        if not self.is_enabled():
            return False
        if source in SOURCE_TTS:
            if not get_current_bool("narrator_read_own_messages", default=False):
                return False

        backend_id = (get_settings("narrator_backend") or "novelai").strip().lower()
        backend = self.backends.get(backend_id)
        if backend is None:
            Logger.warn(f"Unknown narrator backend: {backend_id}")
            return False
        if not backend.is_available():
            Logger.warn(f"Backend '{backend.display_name}' is not available.")
            return False

        voice = (get_settings("narrator_voice") or "").strip()
        if not voice:
            stock = backend.list_voices()
            voice = stock[0] if stock else ""
        device_index = get_int_settings("narrator_output_device", default=None)
        volume_db = get_int_settings("narrator_volume_db", default=0) or 0

        # Import lazily so a missing/broken captions module doesn't break everything... EZ
        try:
            from files.closed_captions import caption_coordinator
        except Exception:
            caption_coordinator = None

        try:
            audio_path = await backend.generate_audio(
                text, voice=voice, volume_db=float(volume_db)
            )
            if audio_path is None:
                return await backend.narrate(
                    text,
                    voice=voice,
                    device_index=device_index,
                    volume_db=float(volume_db),
                )

            if caption_coordinator is not None:
                try:
                    caption_coordinator.audio_ready(audio_path)
                except Exception as e:
                    Logger.warn(f"Audio Error Raised: {e}")

            return await backend.play_audio(
                audio_path,
                device_index=device_index,
                volume_db=0.0,
            )
        except Exception as e:
            Logger.warn(f"Backend '{backend.display_name}' raised an exception: {e}")
            return False

    def interrupt_playback(self) -> None:
        for backend in self.backends.values():
            try:
                backend.interrupt()
            except Exception:
                pass

    def interrupt(self) -> None:
        self.interrupt_playback()

narrator: Optional[Narrator] = None
def get_narrator() -> Narrator:
    global narrator
    if narrator is None:
        narrator = Narrator()
    return narrator