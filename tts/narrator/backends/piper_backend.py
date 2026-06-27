import os
from typing import Optional
from files.system_setup.settings import get_settings
from files.system_setup.system_logger import Logger
from .Base import NarratorBackend


class PiperBackend(NarratorBackend):
    backend_id = "piper"
    display_name = "Piper (offline, not yet installed)"

    def is_available(self) -> bool:
        try:
            import piper
        except Exception:
            return False
        models_dir = get_settings("narrator_piper_models_dir") or ""
        if not models_dir or not os.path.isdir(models_dir):
            return False
        for name in os.listdir(models_dir):
            if name.endswith(".onnx"):
                return True
        return False

    def interrupt(self) -> None:
        pass

    def list_voices(self) -> list[str]:
        models_dir = get_settings("narrator_piper_models_dir") or ""
        if not models_dir or not os.path.isdir(models_dir):
            return []
        return sorted(
            os.path.splitext(name)[0]
            for name in os.listdir(models_dir)
            if name.endswith(".onnx")
        )

    async def generate_audio(
        self,
        text: str,
        *,
        voice: str,
        volume_db: float = 0.0,
    ) -> Optional[str]:
        Logger.warn(
            "Stub backend — install piper-tts and a voice "
            "that'll probably be installed later on down the road"
        )
        return None

    async def play_audio(
        self,
        audio_path: str,
        *,
        device_index: Optional[int] = None,
        volume_db: float = 0.0,
    ) -> bool:
        return False