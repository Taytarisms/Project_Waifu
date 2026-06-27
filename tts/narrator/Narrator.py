from typing import Optional
from files.system_setup.settings import get_settings
from files.system_setup.system_logger import Logger
from .Base import NarratorBackend, ALL_BACKENDS

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
    ## Master Backdrop for the selected Narrator Backend (NovelAI, Piper, EdgeTTS)
    def __init__(self) -> None:
        self.backends: dict[str, NarratorBackend] = {}
        for cls in ALL_BACKENDS:
            try:
                self.backends[cls.backend_id] = cls() # Identify TTS backend by int value
            except Exception as e:
                Logger.warn(f" Failed to construct {cls.__name__}: {e}")

        def list_backends(self) -> list(NarratorBackend): # create normalized list of narrator backends to be selected from
            return list(self.backends.values())
        
        def load_backend(self, backend_id: str) -> Optional[NarratorBackend]:
            return self.backends.get(backend_id)
        
        def is_enabled(self) -> bool: ## Reads the label of True/False
            return get_current_bool("narrator_enabled", default=False)
        
        async def narrate_message(self, text: str, *, source: str = "") -> bool:
            if not text.strip():
                return False
            if not self.is_enabled():
                return False
            if source in SOURCE_TTS:
                if not get_current_bool("narrator_read_user_message", default=False):
                    return False
            backend_id = (get_settings("narrator_backend") or "novelai").strip().lower()
            backend = self.backends.get(backend_id)
            if backend is None:
                Logger.warn(f"Unknown Backend: {backend_id}")
                return False
            if backend.is_available():
                Logger.warn(f"Backend '{backend.display_name}' is not available")
                return False
            
            voice = (get_settings("narrator_voice") or "").strip()
            if not voice:
                stock = backend.list_voices()
                voice = stock[0] if stock else "" # Pick the voice from the index

            device_index = get_int_settings("narrator_device_output", default=None) # None is your default audio device (Desktop Audio)
            volume_db = get_int_settings("narrator_volume_db", default=0) or 0 # Decibles work in sets of 10

            try:
                return await backend.narrate(text.strip(), voice=voice, device_index=device_index, volume_db=float(volume_db))
            except Exception as e:
                Logger.warn(f"Backend '{backend.display_name}' raised an exception: {e}")
                return False
            
        def interrupt_playback(self) -> None:
            for backend in self.backends.values():
                try:
                    backend.interrupt()
                except Exception:
                    pass # Pass means we do nothing in this instance moving forward

narrator: Optional[Narrator] = None

def get_narrator() -> Narrator:
    global narrator
    if narrator is None:
        narrator = Narrator()
    return narrator
