import argparse
import os
import sys
from dataclasses import dataclass
from typing import Iterator, Optional
import numpy as np
try:
    from .LocalBase import TTSConfig, TTSEngineBase, TTSCore
except ImportError:
    from LocalBase import TTSConfig, TTSEngineBase, TTSCore
try:
    from .qwen3_voice_presets import (
        VoicePreset,
        list_qwen3_voice_presets,
        load_qwen3_voice_preset,
        save_qwen3_voice_preset,
    )
except ImportError:
    try:
        from qwen3_voice_presets import (
            VoicePreset,
            list_qwen3_voice_presets,
            load_qwen3_voice_preset,
            save_qwen3_voice_preset,
        )
    except Exception:
        VoicePreset = None
        list_qwen3_voice_presets = None
        load_qwen3_voice_preset = None
        save_qwen3_voice_preset = None


QWEN3_SPEAKERS = [
    "aiden", "dylan", "eric", "ono_anna",
    "ryan", "serena", "sohee", "uncle_fu", "vivian",
]

QWEN3_LANGUAGES = [
    "Chinese", "English", "Japanese", "Korean",
    "German", "French", "Russian", "Portuguese",
    "Spanish", "Italian",
]

CUSTOMVOICE_06B = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
CUSTOMVOICE_17B = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
BASE_06B = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
BASE_17B = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"

DEFAULT_TEXT = (
    "Hello! This is a Qwen three text to speech test using the selected voice preset. "
    "If this sounds good, it can be wired directly into the companion app."
)

def _detect_device(requested: str = "auto") -> str:
    import torch

    if requested != "auto" and requested != "":
        return requested

    if torch.cuda.is_available():
        return "cuda:0"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _detect_backend(requested: str, device: str) -> str:
    if requested == "faster":
        return "faster"
    if requested == "upstream":
        return "upstream"

    if "cuda" in device:
        try:
            import faster_qwen3_tts
            return "faster"
        except ImportError:
            pass
    return "upstream"

def _detect_dtype(device: str):
    import torch
    if "cuda" in device:
        return torch.bfloat16
    if device == "mps":
        return torch.float32
    return torch.float32

def _detect_attn(device: str) -> str:
    if "cuda" not in device:
        return "sdpa"
    try:
        import flash_attn
        return "flash_attention_2"
    except ImportError:
        return "sdpa"

@dataclass
class Qwen3TTSConfig(TTSConfig):
    model_id: str = CUSTOMVOICE_06B
    device: str = "auto"              # auto | cuda:0 | mps | cpu
    backend: str = "faster"           # auto | faster | upstream
    speaker: str = "ryan"
    language: str = "English"
    instruct: str = ""
    ref_audio: Optional[str] = None
    ref_text: str = ""
    xvec_only: bool = True
    streaming_chunk_size: int = 8
    sample_rate: int = 24000

class Qwen3TTSEngine(TTSEngineBase):
    engine_name = "qwen3"

    def __init__(self, cfg: Optional[Qwen3TTSConfig] = None):
        self.cfg = cfg or Qwen3TTSConfig()
        self._model = None       
        self._prompt_cache = None
        self._backend = "auto"         
        self._device = "auto"          
        self._is_custom_voice = self._detect_custom_voice()

    def _detect_custom_voice(self) -> bool:
        return "customvoice" in (self.cfg.model_id or "").lower()

    def validate_config(self) -> None:
        model_id = self.cfg.model_id or ""
        self._is_custom_voice = self._detect_custom_voice()

        if not model_id:
            raise ValueError("model_id cannot be empty.")
        if self.cfg.sample_rate <= 0:
            raise ValueError("sample_rate must be greater than 0.")
        if self.cfg.streaming_chunk_size <= 0:
            raise ValueError("streaming_chunk_size must be greater than 0.")
        if self.cfg.language not in QWEN3_LANGUAGES:
            raise ValueError(
                f"unknown language {self.cfg.language!r}; available: {QWEN3_LANGUAGES}"
            )

        if self._is_custom_voice:
            if self.cfg.speaker not in QWEN3_SPEAKERS:
                raise ValueError(
                    f"unknown speaker {self.cfg.speaker!r}; available: {QWEN3_SPEAKERS}"
                )
        else:
            if not self.cfg.ref_audio:
                raise ValueError(
                    "Base/voice-clone mode requires cfg.ref_audio, or use a CustomVoice model."
                )
            if not os.path.isfile(self.cfg.ref_audio):
                raise FileNotFoundError(f"reference audio not found: {self.cfg.ref_audio}")

    def load_model(self) -> None:
        import torch
        self.validate_config()
        self._device = _detect_device(self.cfg.device)
        self._backend = _detect_backend(self.cfg.backend, self._device)
        model_id = self.cfg.model_id
        print(f"loading model: {model_id}", file=sys.stderr)
        print(f"device: {self._device}  backend: {self._backend}", file=sys.stderr)
        if self._backend == "faster":
            self._load_faster(model_id)
        else:
            self._load_upstream(model_id)
        if not self._is_custom_voice and self.cfg.ref_audio:
            self._cache_voice_prompt()
        print("warming up...", file=sys.stderr)
        try:
            self._synthesize_internal("Warm up.")
        except Exception as exc:
            print(f"warm-up note: {exc}", file=sys.stderr)
        mode = "custom_voice" if self._is_custom_voice else "voice_clone"
        print(f"ready  device={self._device}  backend={self._backend}  mode={mode}",
              file=sys.stderr)

    def _load_faster(self, model_id: str):
        from faster_qwen3_tts import FasterQwen3TTS
        self._model = FasterQwen3TTS.from_pretrained(model_id)

    def _load_upstream(self, model_id: str):
        import torch
        from qwen_tts import Qwen3TTSModel

        dtype = _detect_dtype(self._device)
        attn  = _detect_attn(self._device)

        kwargs = {
            "device_map": self._device,
            "dtype": dtype,
        }
        if "cuda" in self._device:
            kwargs["attn_implementation"] = attn

        self._model = Qwen3TTSModel.from_pretrained(model_id, **kwargs)

    def unload_model(self) -> None:
        self._model = None
        self._prompt_cache = None

    def set_model_id(self, model_id: str):
        self.cfg.model_id = model_id
        self._is_custom_voice = self._detect_custom_voice()
        self._prompt_cache = None

    def set_speaker(self, speaker: str):
        if speaker not in QWEN3_SPEAKERS:
            print(f"warning: unknown speaker {speaker!r}; "
                  f"available: {QWEN3_SPEAKERS}", file=sys.stderr)
        self.cfg.speaker = speaker

    def set_ref_audio(self, ref_audio_path: str, ref_text: str = ""):
        if ref_audio_path and not os.path.isfile(ref_audio_path):
            raise FileNotFoundError(f"reference audio not found: {ref_audio_path}")
        self.cfg.ref_audio = ref_audio_path
        self.cfg.ref_text = ref_text
        self._prompt_cache = None
        if self._model is not None:
            self._cache_voice_prompt()

    def set_language(self, language: str):
        self.cfg.language = language

    def set_instruct(self, instruct: str):
        self.cfg.instruct = instruct

    # Only faster-qwen3-tts supports streaming / I wouldn't use it unless the venv installs it (which I already install for you anyways)
    @property
    def supports_streaming(self) -> bool:
        return self._backend == "faster"

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        assert self._model is not None, "call load model first before inferencing"
        audio = self._synthesize_internal(text)
        return audio, self.cfg.sample_rate

    def synthesize_streaming(self, text: str) -> Iterator[np.ndarray]:
        assert self._model is not None, "call load model first before inferencing"
        assert self._backend == "faster", "streaming only available with 'faster' backend"

        try:
            if self._is_custom_voice:
                stream = self._model.generate_custom_voice_streaming(
                    text=text,
                    speaker=self.cfg.speaker,
                    language=self.cfg.language,
                    instruct=self.cfg.instruct or None,
                    chunk_size=self.cfg.streaming_chunk_size,
                )
            else:
                kwargs = self._get_clone_kwargs(text)
                stream = self._model.generate_voice_clone_streaming(
                    chunk_size=self.cfg.streaming_chunk_size,
                    **kwargs,
                )
            for item in stream:
                audio_chunk = item[0] if isinstance(item, (tuple, list)) else item
                if audio_chunk is not None:
                    if hasattr(audio_chunk, "cpu"):
                        chunk = audio_chunk.cpu().numpy().astype(np.float32)
                    else:
                        chunk = np.asarray(audio_chunk, dtype=np.float32)
                    if chunk.ndim > 1:
                        chunk = chunk.flatten()
                    if len(chunk) > 0:
                        yield chunk

        except Exception as exc:
            print(f"streaming error: {exc}", file=sys.stderr)

    def _synthesize_internal(self, text: str) -> np.ndarray:
        import torch

        try:
            if self._backend == "faster":
                return self._synth_faster(text)
            else:
                return self._synth_upstream(text)
        except Exception as exc:
            print(f"synthesis error: {exc}", file=sys.stderr)
            return np.array([], dtype=np.float32)

    def _synth_faster(self, text: str) -> np.ndarray:
        import torch

        if self._is_custom_voice:
            audio_list, sr = self._model.generate_custom_voice(
                text=text,
                speaker=self.cfg.speaker,
                language=self.cfg.language,
                instruct=self.cfg.instruct or None,
            )
        else:
            kwargs = self._get_clone_kwargs(text)
            audio_list, sr = self._model.generate_voice_clone(**kwargs)

        return self._tensor_list_to_numpy(audio_list)

    def _synth_upstream(self, text: str) -> np.ndarray:
        import torch

        if self._is_custom_voice:
            wavs, sr = self._model.generate_custom_voice(
                text=text,
                speaker=self.cfg.speaker,
                language=self.cfg.language,
                instruct=self.cfg.instruct or None,
            )
        else:
            kwargs = self._get_clone_kwargs_upstream(text)
            wavs, sr = self._model.generate_voice_clone(**kwargs)
        return self._tensor_list_to_numpy(wavs)

    @staticmethod
    def _tensor_list_to_numpy(audio_list) -> np.ndarray:
        import torch
        if isinstance(audio_list, list):
            if len(audio_list) == 0:
                return np.array([], dtype=np.float32)
            tensors = []
            for item in audio_list:
                if isinstance(item, torch.Tensor):
                    tensors.append(item)
                elif isinstance(item, np.ndarray):
                    tensors.append(torch.from_numpy(item))
                else:
                    tensors.append(torch.tensor(item))
            audio = torch.cat(tensors).cpu().numpy().astype(np.float32)
        elif isinstance(audio_list, torch.Tensor):
            audio = audio_list.cpu().numpy().astype(np.float32)
        else:
            audio = np.asarray(audio_list, dtype=np.float32)
        if audio.ndim > 1:
            audio = audio.flatten()
        return audio

    def _get_clone_kwargs(self, text: str) -> dict:
        kwargs = {"text": text, "language": self.cfg.language}

        if self.cfg.ref_audio:
            kwargs["ref_audio"] = self.cfg.ref_audio
            kwargs["ref_text"] = self.cfg.ref_text
            kwargs["xvec_only"] = self.cfg.xvec_only
        else:
            raise ValueError(
                "Voice clone mode requires ref_audio. "
                "Set cfg.ref_audio or use a CustomVoice model."
            )
        return kwargs

    def _get_clone_kwargs_upstream(self, text: str) -> dict:
        kwargs = {"text": text, "language": self.cfg.language}

        if self._prompt_cache is not None:
            kwargs["voice_clone_prompt"] = self._prompt_cache
        elif self.cfg.ref_audio:
            kwargs["ref_audio"] = self.cfg.ref_audio
            kwargs["ref_text"] = self.cfg.ref_text
            kwargs["x_vector_only_mode"] = self.cfg.xvec_only
        else:
            raise ValueError(
                "Voice clone mode requires ref_audio. "
                "Set cfg.ref_audio or use a CustomVoice model."
            )
        return kwargs

    def _cache_voice_prompt(self):
        if self._backend != "upstream":
            return
        if self._model is None or not self.cfg.ref_audio:
            return

        print(f"caching voice prompt from: {self.cfg.ref_audio}",
              file=sys.stderr)
        try:
            self._prompt_cache = self._model.create_voice_clone_prompt(
                ref_audio=self.cfg.ref_audio,
                ref_text=self.cfg.ref_text,
                x_vector_only_mode=self.cfg.xvec_only,
            )
            print("voice prompt cached", file=sys.stderr)
        except Exception as exc:
            print(f"failed to cache voice prompt: {exc}",
                  file=sys.stderr)
            self._prompt_cache = None

def config_from_preset_name(name: str) -> Qwen3TTSConfig:
    if load_qwen3_voice_preset is None:
        raise RuntimeError("qwen3_voice_presets.py could not be imported.")
    preset = load_qwen3_voice_preset(name)
    return preset.to_config()

def default_builtin_config() -> Qwen3TTSConfig:
    return Qwen3TTSConfig(
        model_id=CUSTOMVOICE_06B,
        speaker="ryan",
        language="English",
    )

def create_engine_from_preset(name: str) -> Qwen3TTSEngine:
    return Qwen3TTSEngine(config_from_preset_name(name))

def create_core_from_config(cfg: Qwen3TTSConfig) -> TTSCore:
    engine = Qwen3TTSEngine(cfg)
    return TTSCore(engine, cfg)

def create_core_from_preset(name: str) -> TTSCore:
    return create_core_from_config(config_from_preset_name(name))

def run_tts_test(cfg: Qwen3TTSConfig, text: str) -> None:
    tts = create_core_from_config(cfg)
    tts.start()
    try:
        tts.feed(text)
        tts.wait_until_done()
    finally:
        tts.stop()

def _build_config_from_args(args: argparse.Namespace) -> Qwen3TTSConfig:
    if args.preset:
        cfg = config_from_preset_name(args.preset)
    else:
        model_id = args.model_id
        if args.mode == "clone" and model_id == CUSTOMVOICE_06B:
            model_id = BASE_06B
        cfg = Qwen3TTSConfig(
            model_id=model_id,
            device=args.device,
            backend=args.backend,
            speaker=args.speaker,
            language=args.language,
            instruct=args.instruct,
            ref_audio=args.ref_audio,
            ref_text=args.ref_text or "",
            xvec_only=not args.full_clone_prompt,
            streaming_chunk_size=args.streaming_chunk_size,
        )
    if args.device:
        cfg.device = args.device
    if args.backend:
        cfg.backend = args.backend
    if args.language_override:
        cfg.language = args.language_override
    if args.instruct:
        cfg.instruct = args.instruct
    if args.ref_audio:
        cfg.ref_audio = args.ref_audio
    if args.ref_text:
        cfg.ref_text = args.ref_text
    return cfg


def main() -> None:
    parser = argparse.ArgumentParser(description="Qwen3 TTS voice/preset test runner")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--preset", default="", help="Load a saved Qwen3 voice preset by name.")
    parser.add_argument("--mode", choices=("builtin", "clone"), default="builtin")
    parser.add_argument("--model-id", default=CUSTOMVOICE_06B)
    parser.add_argument("--device", default="auto", help="auto | cuda:0 | mps | cpu")
    parser.add_argument("--backend", default="auto", help="auto | faster | upstream")
    parser.add_argument("--speaker", default="ryan", choices=QWEN3_SPEAKERS)
    parser.add_argument("--language", default="English", choices=QWEN3_LANGUAGES)
    parser.add_argument("--language-override", default="", choices=[""] + QWEN3_LANGUAGES)
    parser.add_argument("--instruct", default="")
    parser.add_argument("--ref-audio", default=None)
    parser.add_argument("--ref-text", default="")
    parser.add_argument("--full-clone-prompt", action="store_true", help="Use full prompt mode instead of xvec_only.")
    parser.add_argument("--streaming-chunk-size", type=int, default=8)
    parser.add_argument("--save-preset", default="", help="Save config as a preset and exit.")
    parser.add_argument("--run-after-save", action="store_true")
    parser.add_argument("--list-presets", action="store_true")
    parser.add_argument("--list-speakers", action="store_true")
    parser.add_argument("--list-languages", action="store_true")

    args = parser.parse_args()
    if args.list_speakers:
        print("\n".join(QWEN3_SPEAKERS))
        return
    if args.list_languages:
        print("\n".join(QWEN3_LANGUAGES))
        return
    if args.list_presets:
        if list_qwen3_voice_presets is None:
            raise RuntimeError("qwen3_voice_presets.py could not be imported.")
        presets = list_qwen3_voice_presets()
        if not presets:
            print("No Qwen3 presets saved yet.")
            return
        for p in presets:
            print(
                f"{p.name} | mode={p.mode} | model={p.model_id} | "
                f"speaker={p.speaker} | ref={p.ref_audio or '-'} | "
                f"instruct={p.instruct or '-'}"
            )
        return

    cfg = _build_config_from_args(args)

    if args.save_preset:
        if save_qwen3_voice_preset is None:
            raise RuntimeError("Preset File could not be imported.")
        mode = "custom_voice" if "customvoice" in cfg.model_id.lower() else "voice_clone"
        preset = save_qwen3_voice_preset(
            name=args.save_preset,
            mode=mode,
            model_id=cfg.model_id,
            language=cfg.language,
            speaker=cfg.speaker,
            ref_audio_source=cfg.ref_audio,
            ref_text=cfg.ref_text,
            xvec_only=cfg.xvec_only,
            instruct=cfg.instruct,
        )
        print(f"Saved preset: {preset.name}")
        if not args.run_after_save:
            return
        cfg = preset.to_config()
    run_tts_test(cfg, args.text)

if __name__ == "__main__":
    main()