import sys
from typing import Optional

from files.system_setup.settings import get_auth
from files.system_setup.system_logger import Logger
from files.tts.novel_ai_tts import NovelTTSClient

from .Base import NarratorBackend

NOVELAI_STOCK_VOICES = [
    "Ligeia", "Aini", "Orea", "Claea", "Lim", "Orev", "Hae", "Cyllene",
    "Leucosia", "Crina", "Marisa", "Alseid", "Daphnis",
]


class NovelAIBackend(NarratorBackend):
    backend_id = "novelai"
    display_name = "NovelAI"

    def __init__(self):
        self._client: Optional[NovelTTSClient] = None
        self._last_token: Optional[str] = None

    def _get_client(self) -> Optional[NovelTTSClient]:
        token = get_auth("novelai", "token") if get_auth else None
        if not token:
            return None
        if self._client is None or token != self._last_token:
            self._client = NovelTTSClient(api_token=token)
            self._last_token = token
        return self._client

    def is_available(self) -> bool:
        try:
            return bool(get_auth("novelai", "token")) if get_auth else False
        except Exception:
            return False

    async def narrate(
        self,
        text: str,
        *,
        voice: str,
        device_index: Optional[int] = None,
        volume_db: float = 0.0,
    ) -> bool:
        client = self._get_client()
        if client is None:
            Logger.warn("No API token configured.")
            return False

        try:
            file_id = await client.generate_tts(speak=text, voice_seed=voice)
        except Exception as e:
            if len(getattr(e, "args", ())) >= 2 and e.args[1] == 401:
                Logger.warn("Bad or missing API token.")
            else:
                Logger.warn(f"generate_tts failed: {e}")
            return False

        try:
            played = client.play_tts(
                file_id=file_id,
                device=device_index,
                volume_modifier=int(volume_db),
            )
            return bool(played)
        except Exception as e:
            print(f"play_tts failed: {e}", file=sys.stderr)
            return False

    def interrupt(self) -> None:
        try:
            from files.tts.novel_ai_tts import interrupt as _interrupt
            _interrupt()
        except Exception:
            pass

    def list_voices(self) -> list[str]:
        return list(NOVELAI_STOCK_VOICES)
