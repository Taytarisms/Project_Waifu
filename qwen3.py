import os
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import numpy as np

try:
    from .LocalBase import TTSConfig, TTSEngineBase
except ImportError:
    from LocalBase import TTSConfig, TTSEngineBase

DEFAULT_KOKORO_MODEL_DIR = Path(__file__).resolve().parent / "models"
KOKORO_VOICES = {
    # American English
    "af_heart": "en-us",  "af_alloy": "en-us",  "af_aoede": "en-us",
    "af_bella": "en-us",  "af_jessica": "en-us", "af_kore": "en-us",
    "af_nicole": "en-us", "af_nova": "en-us",    "af_river": "en-us",
    "af_sarah": "en-us",  "af_sky": "en-us",
    "am_adam": "en-us",   "am_echo": "en-us",    "am_eric": "en-us",
    "am_fenrir": "en-us", "am_liam": "en-us",    "am_michael": "en-us",
    "am_onyx": "en-us",   "am_puck": "en-us",
    # British English
    "bf_emma": "en-gb",   "bf_isabella": "en-gb",
    "bm_george": "en-gb", "bm_lewis": "en-gb",   "bm_daniel": "en-gb",
    # Japanese
    "jf_alpha": "ja",     "jf_gongitsune": "ja",
    "jm_kumo": "ja",      "jm_beta": "ja",
    # Chinese
    "zf_xiaobei": "cmn",  "zf_xiaoni": "cmn",
    "zm_yunjian": "cmn",  "zm_yunxi": "cmn",
    # Spanish
    "ef_dora": "es",      "em_alex": "es",
    # French
    "ff_siwis": "fr-fr",
    # Hindi
    "hf_alpha": "hi",     "hm_omega": "hi",
    # Italian
    "if_sara": "it",      "im_nicola": "it",
    # Brazilian Portuguese
    "pf_dora": "pt-br",   "pm_alex": "pt-br",
}


def _lang_from_voice(name: str) -> str:
    if name in KOKORO_VOICES:
        return KOKORO_VOICES[name]
    prefix = name[:2].lower() if len(name) >= 2 else "af"
    mapping = {
        "af": "en-us", "am": "en-us",
        "bf": "en-gb", "bm": "en-gb",
        "jf": "ja",    "jm": "ja",
        "zf": "cmn",   "zm": "cmn",
        "ef": "es",    "em": "es",
        "ff": "fr-fr",
        "hf": "hi",    "hm": "hi",
        "if": "it",    "im": "it",
        "pf": "pt-br", "pm": "pt-br",
    }
    return mapping.get(prefix, "en-us")

@dataclass
class KokoroTTSConfig(TTSConfig):
    voice: str          = "af_heart"
    speed: float        = 1.0
    sample_rate: int    = 24000           # Kokoro outputs 24 kHz
    model_dir: str      = str(DEFAULT_KOKORO_MODEL_DIR)  # folder containing the two files
    model_file: str     = "kokoro-v1.0.onnx"
    voices_file: str    = "voices-v1.0.bin"

class KokoroTTSEngine(TTSEngineBase):
    engine_name = "kokoro"

    def __init__(self, cfg: Optional[KokoroTTSConfig] = None):
        self.cfg = cfg or KokoroTTSConfig()
        self._kokoro = None
        self._lang = _lang_from_voice(self.cfg.voice)

    def validate_config(self) -> None:
        if not self.cfg.voice:
            raise ValueError("voice cannot be empty.")
        if self.cfg.speed <= 0:
            raise ValueError("speed must be greater than 0.")
        if self.cfg.sample_rate <= 0:
            raise ValueError("sample_rate must be greater than 0.")

    def load_model(self) -> None:
        from kokoro_onnx import Kokoro

        model_path  = os.path.join(self.cfg.model_dir, self.cfg.model_file)
        voices_path = os.path.join(self.cfg.model_dir, self.cfg.voices_file)

        # Validate files exist
        for path, label in [(model_path, "model"), (voices_path, "voices")]:
            if not os.path.isfile(path):
                raise FileNotFoundError(
                    f"{label} file not found: {path}\n"
                    f"  Download from: https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0"
                )

        print(f"loading  voice={self.cfg.voice}  "
              f"lang={self._lang}  speed={self.cfg.speed}",
              file=sys.stderr)
        print(f"model: {model_path}", file=sys.stderr)
        self._kokoro = Kokoro(model_path, voices_path)
        print(f"warming up...", file=sys.stderr)
        try:
            self._kokoro.create(
                "Warm up.", voice=self.cfg.voice,
                speed=self.cfg.speed, lang=self._lang,
            )
        except Exception as exc:
            print(f"warm-up note: {exc}", file=sys.stderr)

        print(f"ready", file=sys.stderr)

    def unload_model(self) -> None:
        self._kokoro = None

    def set_voice(self, voice: str):
        self.cfg.voice = voice
        self._lang = _lang_from_voice(voice)
        print(f"switched to voice={voice} lang={self._lang}",
              file=sys.stderr)

    def set_speed(self, speed: float):
        self.cfg.speed = speed
    @property
    def supports_streaming(self) -> bool:
        return False

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        assert self._kokoro is not None, "call load_model() first"

        try:
            samples, sr = self._kokoro.create(
                text,
                voice=self.cfg.voice,
                speed=self.cfg.speed,
                lang=self._lang,
            )
        except Exception as exc:
            print(f"synthesis error: {exc}", file=sys.stderr)
            return np.array([], dtype=np.float32), self.cfg.sample_rate

        audio = np.asarray(samples, dtype=np.float32)
        if audio.ndim > 1:
            audio = audio.flatten()

        return audio, sr

if __name__ == "__main__":
    try:
        from .LocalBase import TTSCore
    except ImportError:
        from LocalBase import TTSCore

    cfg    = KokoroTTSConfig(voice="af_heart", speed=1.0)
    engine = KokoroTTSEngine(cfg)
    tts    = TTSCore(engine, cfg)

    tts.start(
        on_synth_start=lambda t: print(f"  synth: {t!r}"),
        on_synth_done=lambda t:  print(f"  done:  {t!r}"),
    )

    tts.feed("Hello! This is a test of the Kokoro text to speech engine. "
             "It should split this into sentences and play them back. "
             "Let me know how it sounds!")

    tts.wait_until_done()
    tts.stop()
    print("done.")