"""
Piper offline TTS narrator backend (v2 stub).

Piper is a lightweight, fully-offline TTS engine that runs on CPU and
produces decent quality. It's our intended fully-offline path per the
v1 priority order (offline first, Edge as fallback).

This backend is intentionally a stub: it returns is_available() == False
until Piper is installed and a voice model is downloaded. Wiring it up
later is just a matter of:

  1. pip install piper-tts (or the official wheel/binary for your OS)
  2. Download a voice model (.onnx + .onnx.json) from:
     https://github.com/rhasspy/piper/blob/master/VOICES.md
  3. Set the `narrator_piper_models_dir` setting to the folder containing
     them.
  4. Replace the body of narrate() with the real implementation. The
     synthesize-to-wav part is roughly:

         from piper import PiperVoice
         voice = PiperVoice.load(model_path)
         with wave.open(wav_path, "wb") as wf:
             voice.synthesize(text, wf)

     then reuse the same _play_wav_blocking pattern as EdgeBackend.

Leaving this as a stub keeps the backend list complete in the UI so users
see Piper as a future option, and keeps the dispatch code in
narrator.py uniform.
"""
import os
from typing import Optional

from files.system_setup.settings import get_settings
from files.system_setup.system_logger import Logger

from .Base import NarratorBackend


class PiperBackend(NarratorBackend):
    backend_id = "piper"
    display_name = "Piper (offline, not yet installed)"

    def is_available(self) -> bool:
        # Two conditions for availability: the piper package is importable
        # AND there's at least one .onnx voice file in the configured dir.
        try:
            import piper  # noqa: F401
        except Exception:
            return False

        models_dir = get_settings("narrator_piper_models_dir") if get_settings else ""
        if not models_dir or not os.path.isdir(models_dir):
            return False
        for name in os.listdir(models_dir):
            if name.endswith(".onnx"):
                return True
        return False

    async def narrate(
        self,
        text: str,
        *,
        voice: str,
        device_index: Optional[int] = None,
        volume_db: float = 0.0,
    ) -> bool:
        Logger.warn(
            "[Narrator/Piper] Stub backend — install piper-tts and a voice "
            "model, then implement narrate() in piper_backend.py."
        )
        return False

    def interrupt(self) -> None:
        # No-op until implemented.
        pass

    def list_voices(self) -> list[str]:
        # Once implemented, list .onnx files from narrator_piper_models_dir.
        models_dir = get_settings("narrator_piper_models_dir") if get_settings else ""
        if not models_dir or not os.path.isdir(models_dir):
            return []
        return sorted(
            os.path.splitext(name)[0]
            for name in os.listdir(models_dir)
            if name.endswith(".onnx")
        )