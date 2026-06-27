from abc import ABC, abstractmethod
from typing import Optional

class NarratorBackend(ABC):
    backend_id: str = ""
    display_name: str = ""

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def interrupt(self) -> None:
        ...

    async def generate_audio(
        self,
        text: str,
        *,
        voice: str,
        volume_db: float = 0.0,
    ) -> Optional[str]:
        return None

    async def play_audio(
        self,
        audio_path: str,
        *,
        device_index: Optional[int] = None,
        volume_db: float = 0.0,
    ) -> bool:
        return False

    async def narrate(
        self,
        text: str,
        *,
        voice: str,
        device_index: Optional[int] = None,
        volume_db: float = 0.0,
    ) -> bool:
        path = await self.generate_audio(text, voice=voice, volume_db=volume_db)
        if path is None:
            return False
        return await self.play_audio(
            path,
            device_index=device_index,
            volume_db=0.0,  # volume already baked in
        )

    def list_voices(self) -> list[str]:
        return []