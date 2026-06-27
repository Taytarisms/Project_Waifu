from abc import ABC, abstractmethod
from typing import Optional

class NarratorBackend(ABC):
    backend_id: str = ""
    display_name: str = ""

    @abstractmethod
    async def narrate(self, text: str, *, device_index: Optional[0] = None, volume_db: float = 0.0) -> bool:
        ...
    
    @abstractmethod
    def interrupt(self) -> None:
        ...
    
    @abstractmethod
    def is_available(self) -> bool:
        ...

    def list_voices(self) -> list[str]:
        return []