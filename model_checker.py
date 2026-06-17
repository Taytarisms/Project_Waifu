from dataclasses import dataclass
from typing import Literal, Optional


@dataclass
class STTConfig:
    device: Literal["cpu", "cuda"] = "cuda"
    model_name: str = "tiny"
    compute_type: str = "float16"

    vad_aggressiveness: int = 1
    pre_speech_ms: int = 1000
    post_speech_ms: int = 600
    segment_max_ms: int = 15000
    min_length_ms: int = 1000
    min_gap_ms: int = 400

    enable_partials: bool = True
    partial_interval_ms: int = 200

    allowed_latency_ms: int = 100
    handle_overflow: bool = True
    input_blocksize: int = 320
    enforce_exact_vad_frames: bool = False

    language: Optional[str] = "en"
    beam_size: int = 1
