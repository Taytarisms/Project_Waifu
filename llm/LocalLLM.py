import os
import io
import time
import base64
import traceback
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple, Any, Type
from enum import Enum
from datetime import datetime
from PIL import Image, ImageOps
import mss
from llama_cpp import Llama
try:
    from llama_cpp import llama_chat_format as _llama_chat_format
except Exception:
    _llama_chat_format = None
import psutil
from files.system_setup.settings import get_settings
from files.system_setup.gpu_compat import (
    cuda13_support_message,
    detect_nvidia_compute_capability,
    should_force_llama_cpp_cpu,
)
import re

try:
    import pynvml
    NVML_AVAILABLE = True
except Exception:
    pynvml = None
    NVML_AVAILABLE = False


def _chat_handler_class(name: str) -> Optional[type]:
    if _llama_chat_format is None:
        return None
    return getattr(_llama_chat_format, name, None)


Llava15ChatHandler = _chat_handler_class("Llava15ChatHandler")
Llava16ChatHandler = _chat_handler_class("Llava16ChatHandler")
MoondreamChatHandler = _chat_handler_class("MoondreamChatHandler")
NanoLlavaChatHandler = _chat_handler_class("NanoLlavaChatHandler")
Llama3VisionAlphaChatHandler = _chat_handler_class("Llama3VisionAlphaChatHandler")
MiniCPMv26ChatHandler = _chat_handler_class("MiniCPMv26ChatHandler")
MiniCPMv45ChatHandler = _chat_handler_class("MiniCPMv45ChatHandler")
Gemma3ChatHandler = _chat_handler_class("Gemma3ChatHandler")
Gemma4ChatHandler = _chat_handler_class("Gemma4ChatHandler")
GLM41VChatHandler = _chat_handler_class("GLM41VChatHandler")
GLM46VChatHandler = _chat_handler_class("GLM46VChatHandler")
LFM2VLChatHandler = _chat_handler_class("LFM2VLChatHandler")
Qwen25VLChatHandler = _chat_handler_class("Qwen25VLChatHandler")
Qwen3VLChatHandler = _chat_handler_class("Qwen3VLChatHandler")

########################
####----------------####
#### Hardware State ####
####----------------####
########################

@dataclass
class HardwareSnapshot:
    ts: float
    cpu_usage: float
    ram_used: float          # Gigabytes
    ram_percent: float
    process_ram: float
    gpu_util_percent: Optional[float] = None
    vram_used: Optional[float] = None
    vram_total: Optional[float] = None
    gpu_name: Optional[str] = None


class Monitor:
    def __init__(self, gpu_index: int = 0):
        self.process = psutil.Process(os.getpid())
        self.gpu_index = gpu_index
        self.nvml_ready = False
        self.gpu_handle = None

        psutil.cpu_percent(interval=None)
        self.process.cpu_percent(interval=None)

        if NVML_AVAILABLE:
            try:
                pynvml.nvmlInit()
                self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_index)
                self.nvml_ready = True
            except Exception:
                self.nvml_ready = False
                self.gpu_handle = None

    def close(self):
        if self.nvml_ready:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                self.nvml_ready = False
                self.gpu_handle = None

    def snapshot(self) -> HardwareSnapshot:
        vm = psutil.virtual_memory()
        process_ram = self.process.memory_info().rss / (1024 ** 3)

        snap = HardwareSnapshot(
            ts=time.time(),
            cpu_usage=psutil.cpu_percent(interval=None),
            ram_used=vm.used / (1024 ** 3),
            ram_percent=vm.percent,
            process_ram=process_ram,
        )

        if self.nvml_ready and self.gpu_handle is not None:
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle)
                mem  = pynvml.nvmlDeviceGetMemoryInfo(self.gpu_handle)
                name = pynvml.nvmlDeviceGetName(self.gpu_handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", errors="ignore")

                snap.gpu_util_percent = float(util.gpu)
                snap.vram_used        = mem.used  / (1024 ** 3)
                snap.vram_total       = mem.total / (1024 ** 3)
                snap.gpu_name         = name
            except Exception:
                pass
        return snap

    @staticmethod
    def diff(before: HardwareSnapshot, after: HardwareSnapshot) -> Dict[str, Optional[float]]:
        out = {
            "dt_sec":                round(after.ts          - before.ts,          3),
            "cpu_percent_delta":     round(after.cpu_usage   - before.cpu_usage,   2),
            "ram_used_gb_delta":     round(after.ram_used    - before.ram_used,    3),
            "process_ram_gb_delta":  round(after.process_ram - before.process_ram, 3),
        }
        if before.gpu_util_percent is not None and after.gpu_util_percent is not None:
            out["gpu_util_percent_delta"]  = round(after.gpu_util_percent - before.gpu_util_percent, 2)
        else:
            out["gpu_util_percent_delta"]  = None

        if before.vram_used is not None and after.vram_used is not None:
            out["gpu_vram_used_gb_delta"] = round(after.vram_used - before.vram_used, 3)
        else:
            out["gpu_vram_used_gb_delta"] = None

        return out


def format_snapshot(label: str, s: HardwareSnapshot) -> str:
    gpu_part = ""
    if s.gpu_name is not None:
        gpu_part = (
            f" | GPU={s.gpu_name}"
            f" util={s.gpu_util_percent:.1f}%"
            f" vram={s.vram_used:.2f}/{s.vram_total:.2f} GB"
        )
    return (
        f"{label}: "
        f"CPU={s.cpu_usage:.1f}% "
        f"RAM={s.ram_used:.2f} GB ({s.ram_percent:.1f}%) "
        f"PROC={s.process_ram:.2f} GB"
        f"{gpu_part}"
    )


########################
####----------------####
####  Model Families ####
####----------------####
########################

class ModelFamily(str, Enum):
    GEMMA3      = "gemma3"       # vision + text
    GEMMA4      = "gemma4"       # vision + text (+ audio on E2B/E4B)
    LLAVA15     = "llava15"      # vision + text
    LLAVA16     = "llava16"      # vision + text
    MOONDREAM   = "moondream"    # vision + text
    NANOLLAVA   = "nanollava"    # vision + text
    LLAMA3VIS   = "llama3vis"    # llama-3-vision-alpha
    MINICPM26   = "minicpm26"    # minicpm-v-2.6 / 4.0
    MINICPM45   = "minicpm45"    # minicpm-v-4.5
    GLM41V      = "glm41v"       # GLM-4.1V
    GLM46V      = "glm46v"       # GLM-4.6V
    LFM2VL      = "lfm2vl"       # LFM-2-VL
    LFM25VL     = "lfm25vl"      # LFM-2.5-VL
    QWEN25VL    = "qwen25vl"     # Qwen2.5-VL
    QWEN3VL     = "qwen3vl"      # Qwen3-VL
    QWEN35      = "qwen35"       # Qwen3.5 text-only handler
    AUTO        = "auto"


@dataclass
class FamilySpec:
    handler_class:       Optional[type]  
    needs_mmproj:        bool            
    supports_thinking:   bool            
    is_vision:           bool           
    suppress_errors:     List[str] = field(default_factory=list)


_GEMMA_SUPPRESS = [
    "{{- raise_exception('System message cannot contain images.') -}}",
    "{{- raise_exception('llama.cpp does not currently support video.') -}}",
    "{{- raise_exception('System message cannnot contain videos.') -}}",
    "{{- raise_exception('Unexpected item type in content.') -}}",
    "{{- raise_exception('Unexpected content type.') -}}",
    "{{- raise_exception('No messages provided.') -}}",
    "{{- raise_exception('No user query found in messages.') -}}",
    "{{- raise_exception('System message must be at the beginning.') -}}",
    "{{- raise_exception('Unexpected message role') -}}",
    "{%- if not enable_thinking | default(false) -%}\n         {{- '<|channel>thought\\n<channel|>' -}}\n    {%- endif -%}",
    "    {%- if not enable_thinking | default(false) -%}\n        {{- '<|channel>thought\\n<channel|>' -}}\n    {%- endif -%}\n",
]

FAMILY_REGISTRY: Dict[ModelFamily, FamilySpec] = {
    ModelFamily.GEMMA3: FamilySpec(
        handler_class=Gemma3ChatHandler,
        needs_mmproj=True,
        supports_thinking=False,
        is_vision=True,
        suppress_errors=_GEMMA_SUPPRESS,
    ),
    ModelFamily.GEMMA4: FamilySpec(
        handler_class=Gemma4ChatHandler,
        needs_mmproj=True,
        supports_thinking=True,
        is_vision=True,
        suppress_errors=_GEMMA_SUPPRESS,
    ),
    ModelFamily.LLAVA15: FamilySpec(
        handler_class=Llava15ChatHandler,
        needs_mmproj=True,
        supports_thinking=False,
        is_vision=True,
    ),
    ModelFamily.LLAVA16: FamilySpec(
        handler_class=Llava16ChatHandler,
        needs_mmproj=True,
        supports_thinking=False,
        is_vision=True,
    ),
    ModelFamily.MOONDREAM: FamilySpec(
        handler_class=MoondreamChatHandler,
        needs_mmproj=True,
        supports_thinking=False,
        is_vision=True,
    ),
    ModelFamily.NANOLLAVA: FamilySpec(
        handler_class=NanoLlavaChatHandler,
        needs_mmproj=True,
        supports_thinking=False,
        is_vision=True,
    ),
    ModelFamily.LLAMA3VIS: FamilySpec(
        handler_class=Llama3VisionAlphaChatHandler,
        needs_mmproj=True,
        supports_thinking=False,
        is_vision=True,
    ),
    ModelFamily.MINICPM26: FamilySpec(
        handler_class=MiniCPMv26ChatHandler,
        needs_mmproj=True,
        supports_thinking=False,
        is_vision=True,
    ),
    ModelFamily.MINICPM45: FamilySpec(
        handler_class=MiniCPMv45ChatHandler,
        needs_mmproj=True,
        supports_thinking=False,
        is_vision=True,
    ),
    ModelFamily.GLM41V: FamilySpec(
        handler_class=GLM41VChatHandler,
        needs_mmproj=True,
        supports_thinking=False,
        is_vision=True,
    ),
    ModelFamily.GLM46V: FamilySpec(
        handler_class=GLM46VChatHandler,
        needs_mmproj=True,
        supports_thinking=False,
        is_vision=True,
    ),
    ModelFamily.LFM2VL: FamilySpec(
        handler_class=LFM2VLChatHandler,
        needs_mmproj=True,
        supports_thinking=False,
        is_vision=True,
    ),
    ModelFamily.QWEN25VL: FamilySpec(
        handler_class=Qwen25VLChatHandler,
        needs_mmproj=True,
        supports_thinking=False,
        is_vision=True,
    ),
    ModelFamily.QWEN35: FamilySpec(
        handler_class=None,
        needs_mmproj=False,
        supports_thinking=True,
        is_vision=False,
    ),
    ModelFamily.AUTO: FamilySpec(
        handler_class=None,
        needs_mmproj=False,
        supports_thinking=False,
        is_vision=False,
    ),
}


def build_chat_formatter(family: ModelFamily, mmproj_path: Optional[str], enable_thinking: bool):
    spec = FAMILY_REGISTRY[family]
    if spec.handler_class is None:
        if spec.is_vision:
            print(
                f"[LocalLLM] llama-cpp-python does not expose a handler for "
                f"family '{family.value}'. Falling back to the GGUF embedded template."
            )
        return None  # AUTO – Llama will use the GGUF's embedded template

    base_cls = spec.handler_class

    # Build the patched CHAT_FORMAT string
    if spec.suppress_errors and hasattr(base_cls, "CHAT_FORMAT"):
        patched = base_cls.CHAT_FORMAT
        for token in spec.suppress_errors:
            patched = patched.replace(token, "")

        # Create a one-off subclass with the cleaned template
        PatchedCls = type(
            f"_Patched{base_cls.__name__}",
            (base_cls,),
            {"CHAT_FORMAT": patched},
        )
    else:
        PatchedCls = base_cls

    kwargs: Dict[str, Any] = {}
    if spec.needs_mmproj:
        if not mmproj_path:
            raise ValueError(
                f"Family '{family}' requires an mmproj path, but none was provided."
            )
        kwargs["clip_model_path"] = mmproj_path

    if spec.supports_thinking:
        kwargs["enable_thinking"] = enable_thinking

    kwargs["verbose"] = False
    return PatchedCls(**kwargs)


########################
####----------------####
####   LLM Set-Up   ####
####----------------####
########################

history: List[Dict[str, Any]] = []


def get_history() -> List[Dict[str, Any]]:
    return list(history)


def clear_history() -> None:
    history.clear()


def add_user_message(text: str) -> None:
    if text:
        history.append({"role": "user", "content": str(text)})


def add_assistant_message(text: str) -> None:
    if text:
        history.append({"role": "assistant", "content": str(text)})


def trim_history(max_messages: int = 20) -> None:
    if max_messages > 0 and len(history) > max_messages:
        del history[:-max_messages]


@dataclass(frozen=True)
class ModelConfig:
    models_dir:              str
    model_filename:          Optional[str]
    family:                  ModelFamily
    system_prompt_filename:  str
    mmproj_filename:         Optional[str] = None

    n_ctx:                   int   = 16384
    n_gpu_layers:            int   = -1
    n_batch:                 int   = 1024
    n_threads:               int   = 12
    main_gpu:                int   = 0
    seed:                    int   = -1
    verbose:                 bool  = False
    enable_thinking:         bool  = False   # only used if family.supports_thinking

    use_system_prompt_cache: bool  = True
    benchmark_enabled:       bool  = True
    benchmark_gpu_index:     int   = 0
    max_history_messages:    int   = 20
    response_reserve_tokens: int   = 512
    screen_max_dim:          int   = 960
    screenshot_quality:      int   = 82
    screenshot_subsampling:  str   = "4:2:0"


def module_directory() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def files_directory() -> str:
    return os.path.abspath(os.path.join(module_directory(), ".."))


def models_directory() -> str:
    return os.path.join(files_directory(), "models")


def llm_directory() -> str:
    return module_directory()


def resolve_model_path(models_dir: str, filename: str) -> str:
    filename = str(filename or "").strip()
    if not filename:
        return os.path.join(models_dir, filename)

    if os.path.isabs(filename) and os.path.exists(filename):
        return filename

    direct = os.path.normpath(os.path.join(models_dir, filename))
    if os.path.exists(direct):
        return direct

    wanted = os.path.basename(filename)
    wanted_lower = wanted.lower()
    for root, _dirs, files in os.walk(models_dir):
        for fname in files:
            if fname == wanted or fname.lower() == wanted_lower:
                return os.path.join(root, fname)
    return direct

def resolve_mmproj(model_path: str, mmproj_filename: Optional[str], models_dir: str) -> Optional[str]:
    if not mmproj_filename:
        return None
    model_folder = os.path.dirname(model_path)
    same_folder = os.path.join(model_folder, mmproj_filename)
    if os.path.exists(same_folder):
        return same_folder
    for fname in os.listdir(model_folder):
        if fname.lower().endswith(".gguf") and "mmproj" in fname.lower():
            return os.path.join(model_folder, fname)
    return None

def resolve_llm_file(filename: str) -> str:
    return os.path.join(llm_directory(), filename)

def _settings_bool(key: str, default: bool = False) -> bool:
    value = get_settings(key)
    if value in ("", None):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")

def _setting_value(key: str, default: Any = None) -> Any:
    value = get_settings(key)
    return default if value in ("", None) else value

def _settings_int(key: str, default: int) -> int:
    return int(_setting_value(key, default))

def _settings_family() -> ModelFamily:
    raw = get_settings("local_model_family") or "gemma4"
    try:
        return ModelFamily(raw.lower())
    except ValueError:
        print(f"[LocalLLM] Unknown model family '{raw}' in settings; falling back to AUTO.")
        return ModelFamily.AUTO


def build_config_from_settings() -> ModelConfig:
    return ModelConfig(
        models_dir=models_directory(),
        model_filename=_setting_value("local_model_filename", None),
        mmproj_filename=_setting_value("local_mmproj_filename", None),
        system_prompt_filename=_setting_value("local_system_prompt_filename", "system_message_local.txt"),
        family=_settings_family(),
        n_ctx=_settings_int("local_n_ctx", 16384),
        n_gpu_layers=_settings_int("local_n_gpu_layers", -1),
        n_batch=_settings_int("local_n_batch", 1024),
        n_threads=_settings_int("local_n_threads", 12),
        main_gpu=_settings_int("local_main_gpu", 0),
        seed=_settings_int("local_seed", -1),
        screen_max_dim=_settings_int("local_screen_max_dim", 960),
        screenshot_quality=_settings_int("local_screenshot_quality", 82),
        screenshot_subsampling=_setting_value("local_screenshot_subsampling", "4:2:0"),
        enable_thinking=_settings_bool("local_enable_thinking", False),
    )

def refresh_default_config() -> ModelConfig:
    global DEFAULT_CONFIG
    DEFAULT_CONFIG = build_config_from_settings()
    return DEFAULT_CONFIG


DEFAULT_CONFIG = build_config_from_settings()


def safe_join(base: str, name: str) -> str:
    return os.path.normpath(os.path.join(base, name))

def _positive_int(value: Any, default: int, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    if parsed < minimum:
        return default
    return parsed

def _build_llama_kwargs(
    config: ModelConfig,
    model_path: str,
    chat_handler: Any,
    family: ModelFamily,
) -> Dict[str, Any]:
    n_ctx = _positive_int(config.n_ctx, 512, minimum=512)
    n_batch = _positive_int(config.n_batch, min(2048, n_ctx), minimum=1)
    n_gpu_layers = int(config.n_gpu_layers)
    if n_gpu_layers != 0:
        if detect_nvidia_compute_capability() is None:
            print("[LocalLLM] No NVIDIA CUDA-capable GPU found; using CPU offload.")
            n_gpu_layers = 0
        elif should_force_llama_cpp_cpu():
            message = cuda13_support_message()
            if message:
                print(f"[LocalLLM] {message}")
            print("[LocalLLM] Forcing n_gpu_layers=0 for this llama-cpp CUDA 13 build.")
            n_gpu_layers = 0

    kwargs: Dict[str, Any] = dict(
        model_path=model_path,
        n_ctx=n_ctx,
        n_gpu_layers=n_gpu_layers,
        n_batch=n_batch,
        n_ubatch=min(512, n_batch),
        main_gpu=int(config.main_gpu),
        seed=int(config.seed),
        verbose=False,
        logits_all=False,
    )

    n_threads = int(config.n_threads or 0)
    if n_threads > 0:
        kwargs["n_threads"] = n_threads

    if chat_handler is not None:
        kwargs["chat_handler"] = chat_handler

    if family in (ModelFamily.GEMMA4,):
        kwargs["swa_full"] = True

    return kwargs


def _is_retryable_llama_load_error(exc: Exception) -> bool:
    message = str(exc).lower()
    retry_markers = (
        "failed to create context with model",
        "out of memory",
        "cuda error",
        "cublas",
        "cudart",
        "ggml_cuda",
        "no kernel image",
        "invalid device function",
        "invalid resource handle",
    )
    return any(marker in message for marker in retry_markers)


def _partial_gpu_layer_attempts(n_gpu_layers: int) -> list[int]:
    if n_gpu_layers == 0:
        return []
    if n_gpu_layers < 0:
        return [24, 12]

    attempts: list[int] = []
    for candidate in (24, 12):
        if n_gpu_layers > candidate:
            attempts.append(candidate)
    return attempts


async def cleaned_response(text: str) -> str:
    if not text:
        return ""

    text = text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    if "</think>" in text.lower():
        text = re.split(r"</think>", text, maxsplit=1, flags=re.IGNORECASE)[-1]
    text = re.sub(
        r"<\|channel\|>thought\s*.*?<channel\|>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if "<channel|>" in text:
        text = text.split("<channel|>")[-1]
    text = re.sub(r"^\s*<\|channel\|>\s*", "", text)
    text = text.replace("<|think|>", "")
    text = re.sub(
        r"<\|begin_of_box\|>.*?<\|end_of_box\|>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<\|end_of_box\|>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<nothink>", "", text, flags=re.IGNORECASE)
    m = re.search(r"(?m)(\[[A-Za-z][A-Za-z0-9_\- ]{0,40}\])", text)
    if m and m.start() > 0:
        text = text[m.start():]
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def ensure_exists(path: str, label: str) -> None:
    if not os.path.exists(path):
        raise FileNotFoundError(f"{label} not found: {path}")

def load_system_message(filename: Optional[str] = None) -> str:
    if filename is None:
        filename = resolve_llm_file(DEFAULT_CONFIG.system_prompt_filename)
    with open(filename, "r", encoding="utf-8") as f:
        return f.read().strip()


def get_day_and_weekday():
    now = datetime.now()
    return now.strftime("%B %d %Y"), now.strftime("%A")


def format_system_template(template: str) -> str:
    date_str, weekday_str = get_day_and_weekday()
    try:
        return template.format(date_str=date_str, weekday_str=weekday_str)
    except KeyError as e:
        print(f"[LocalLLM] Missing system prompt template key: {e}")
        return template


date_correct = format_system_template(load_system_message())


def normalize_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return str(content)


########################
####----------------####
####   LocalLLM     ####
####----------------####
########################

class LocalLLM:
    def __init__(self, config: ModelConfig):
        self.config = config
        self.spec = FAMILY_REGISTRY[config.family]
        self.effective_n_ctx = config.n_ctx

        if not config.model_filename:
            raise FileNotFoundError(
                "No local model filename is configured. "
                "Set local_model_filename before loading Local."
            )

        self.model_path = resolve_model_path(config.models_dir, config.model_filename)
        ensure_exists(self.model_path, f"{config.family.value} model")

        self.mmproj_path: Optional[str] = None
        effective_family = config.family

        if self.spec.needs_mmproj:
            self.mmproj_path = resolve_mmproj(
                self.model_path,
                config.mmproj_filename,
                config.models_dir,
            )

            if not self.mmproj_path:
                print(
                    f"[LocalLLM] Family '{config.family.value}' needs an mmproj file, "
                    f"but none was found. Loading '{os.path.basename(self.model_path)}' "
                    "in AUTO text-only mode."
                )
                effective_family = ModelFamily.AUTO
                self.spec = FAMILY_REGISTRY[effective_family]
            else:
                ensure_exists(self.mmproj_path, f"{config.family.value} mmproj")

        self.token_cache:          Dict[str, int] = {}
        self.cached_system_prompt: Optional[str]  = None
        self.cached_system_tokens: int            = 0

        print(f"[LocalLLM] Family: {effective_family.value}")
        print(f"[LocalLLM] Model:  {self.model_path}")
        if self.mmproj_path:
            print(f"[LocalLLM] mmproj: {self.mmproj_path}")
        self.monitor = Monitor(gpu_index=config.benchmark_gpu_index) if config.benchmark_enabled else None

        load_before  = self.monitor.snapshot() if self.monitor else None
        load_started = time.time()

        chat_handler = build_chat_formatter(
            family=effective_family,
            mmproj_path=self.mmproj_path,
            enable_thinking=config.enable_thinking,
        )

        llama_kwargs = _build_llama_kwargs(
            config=config,
            model_path=self.model_path,
            chat_handler=chat_handler,
            family=effective_family,
        )
        if chat_handler is not None:
            llama_kwargs["chat_handler"] = chat_handler

        # swa_full is only valid/useful on Gemma 4 – guard it
        if effective_family in (ModelFamily.GEMMA4,):
            llama_kwargs["swa_full"] = True

        if config.n_batch <= 0:
            print(f"[LocalLLM] Batch size <= 0; using runtime n_batch={llama_kwargs['n_batch']}.")
        if config.n_threads <= 0:
            print("[LocalLLM] Threads <= 0; letting llama.cpp choose the thread count.")

        self.effective_n_ctx = int(llama_kwargs["n_ctx"])
        try:
            self.llm = Llama(**llama_kwargs)
        except (ValueError, RuntimeError, OSError) as e:
            if not _is_retryable_llama_load_error(e):
                raise
            fallback_kwargs = dict(llama_kwargs)
            fallback_kwargs["n_ctx"] = min(int(fallback_kwargs.get("n_ctx", 512)), 8192)
            fallback_kwargs["n_batch"] = min(int(fallback_kwargs.get("n_batch", 2048)), 512)
            fallback_kwargs["n_ubatch"] = min(
                int(fallback_kwargs.get("n_ubatch", 512)),
                fallback_kwargs["n_batch"],
            )
            print(
                "[LocalLLM] Context creation failed; retrying with "
                f"n_ctx={fallback_kwargs['n_ctx']}, n_batch={fallback_kwargs['n_batch']}."
            )
            retry_attempts: list[tuple[str, Dict[str, Any]]] = [
                ("reduced context", fallback_kwargs),
            ]
            original_gpu_layers = int(llama_kwargs.get("n_gpu_layers", 0))
            for layers in _partial_gpu_layer_attempts(original_gpu_layers):
                partial_kwargs = dict(fallback_kwargs)
                partial_kwargs["n_gpu_layers"] = layers
                retry_attempts.append((f"{layers} GPU layers", partial_kwargs))

            last_error: Exception = e
            for label, attempt_kwargs in retry_attempts:
                try:
                    print(f"[LocalLLM] Retrying with {label}.")
                    self.llm = Llama(**attempt_kwargs)
                    self.effective_n_ctx = int(attempt_kwargs["n_ctx"])
                    break
                except (ValueError, RuntimeError, OSError) as retry_error:
                    if not _is_retryable_llama_load_error(retry_error):
                        raise
                    last_error = retry_error
            else:
                cpu_kwargs = dict(fallback_kwargs)
                cpu_kwargs["n_gpu_layers"] = 0
                print(
                    "[LocalLLM] GPU retries failed; retrying with CPU-only offload. "
                    f"Last error: {last_error}"
                )
                self.llm = Llama(**cpu_kwargs)
                self.effective_n_ctx = int(cpu_kwargs["n_ctx"])

        if self.monitor and load_before:
            load_after = self.monitor.snapshot()
            print(f"[LocalLLM] Loaded in {time.time() - load_started:.2f}s")
            print(format_snapshot("LOAD BEFORE", load_before))
            print(format_snapshot("LOAD AFTER",  load_after))
            print(f"LOAD DELTA: {self.monitor.diff(load_before, load_after)}")

    def set_static_prompt(self, system_prompt: str) -> None:
        self.cached_system_prompt  = system_prompt or ""
        self.cached_system_tokens  = self.count_tokens(self.cached_system_prompt)

    def warmup_system_prompt(self):
        if not self.cached_system_prompt:
            return
        print("[LocalLLM] Warming KV cache with system prompt...")
        self.llm.create_chat_completion(
            messages=[
                {"role": "system", "content": self.cached_system_prompt},
                {"role": "user",   "content": "hi"},
            ],
            max_tokens=1,
            temperature=0,
            stream=False,
        )
        print("[LocalLLM] System prompt cached.")

    def resolve_system_prompt(self, system_prompt: Optional[str]) -> str:
        if system_prompt is not None:
            return system_prompt
        if self.cached_system_prompt is not None:
            return self.cached_system_prompt
        raise ValueError("No system prompt cached and none was provided.")

    def count_tokens(self, text: str) -> int:
        text = text or ""
        if text not in self.token_cache:
            self.token_cache[text] = len(self.llm.tokenize(text.encode("utf-8")))
        return self.token_cache[text]

    def extract_message(self, msg: Dict[str, Any]) -> str:
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
            return "\n".join(p for p in parts if p)
        return normalize_text(content)

    def manage_context(
        self,
        messages: List[Dict[str, Any]],
        reserve_tokens: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        if not messages:
            return messages

        reserve      = max(256, reserve_tokens or self.config.response_reserve_tokens)
        system_msg   = messages[0]
        conversation = messages[1:]

        if not conversation:
            return messages

        system_tokens = self.count_tokens(self.extract_message(system_msg))
        context_size  = int(getattr(self, "effective_n_ctx", self.config.n_ctx) or self.config.n_ctx)
        available     = context_size - system_tokens - reserve
        if available < 512:
            available = max(512, context_size // 2)

        current_tokens  = 0
        kept: List[Dict[str, Any]] = []

        for msg in reversed(conversation):
            tokens = self.count_tokens(self.extract_message(msg))
            if current_tokens + tokens <= available:
                kept.insert(0, msg)
                current_tokens += tokens
            else:
                break

        return [system_msg] + kept

    def mss_image_process(self, bbox: Optional[Tuple[int, int, int, int]] = None) -> Image.Image:
        with mss.mss() as sct:
            if bbox:
                left, top, right, bottom = bbox
                zone = {"left": left, "top": top, "width": right - left, "height": bottom - top}
            else:
                zone = sct.monitors[1]
            raw = sct.grab(zone)
            return Image.frombytes("RGB", raw.size, raw.rgb)

    def prep_image(
        self,
        img: Image.Image,
        max_dimensions: Optional[int] = None,
        image_quality:  Optional[int] = None,
        subsampling:    Optional[str] = None,
    ) -> str:
        max_dimensions = max_dimensions or self.config.screen_max_dim
        image_quality  = image_quality  or self.config.screenshot_quality
        subsampling    = subsampling    or self.config.screenshot_subsampling

        img = ImageOps.exif_transpose(img)
        if max(img.size) > max_dimensions:
            img.thumbnail((max_dimensions, max_dimensions), Image.BILINEAR)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=image_quality, optimize=True, subsampling=subsampling)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def capture_screenshot(
        self,
        bbox:          Optional[Tuple[int, int, int, int]] = None,
        resize_max:    Optional[int] = None,
        image_quality: Optional[int] = None,
    ) -> str:
        img = self.mss_image_process(bbox)
        return self.prep_image(
            img,
            max_dimensions=resize_max    or self.config.screen_max_dim,
            image_quality=image_quality  or self.config.screenshot_quality,
        )

    @staticmethod
    def create_data_uri(b64_image: str, mime: str = "image/jpeg") -> str:
        return f"data:{mime};base64,{b64_image}"

    def build_text_message(
        self,
        text:              str,
        history:           Optional[List[Dict[str, Any]]] = None,
        system_prompt:     Optional[str] = None,
        force_new_context: bool = False,
        max_tokens:        int  = 256,
    ) -> List[Dict[str, Any]]:
        history       = history or []
        system_prompt = self.resolve_system_prompt(system_prompt)

        # Qwen3/3.5: append /think or /no_think so the GGUF Jinja template
        # enables or suppresses the thinking block at generation time.
        if self.spec.supports_thinking:
            suffix = "/think" if self.config.enable_thinking else "/no_think"
            if not system_prompt.rstrip().endswith(suffix):
                system_prompt = system_prompt.rstrip() + "\n" + suffix

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        if force_new_context:
            messages += [*history[-4:], {"role": "user", "content": text}]
        else:
            messages += history
            messages.append({"role": "user", "content": text})

        return self.manage_context(messages, reserve_tokens=max_tokens + 128)

    def build_vision_message(
        self,
        text:          str,
        image_path:    Optional[str] = None,
        screenshot:    bool = False,
        bbox:          Optional[Tuple[int, int, int, int]] = None,
        history:       Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        max_tokens:    int  = 300,
        resize_max:    Optional[int] = None,
        image_quality: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        if not self.spec.is_vision:
            raise RuntimeError(
                f"Family '{self.config.family}' does not support vision. "
                "Use generate_text() instead."
            )

        history       = history or []
        system_prompt = self.resolve_system_prompt(system_prompt)
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(history)

        user_content: List[Dict[str, Any]] = []
        if screenshot:
            b64 = self.capture_screenshot(bbox=bbox, resize_max=resize_max, image_quality=image_quality)
            user_content.append({"type": "image_url", "image_url": {"url": self.create_data_uri(b64)}})
        elif image_path:
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            user_content.append({"type": "image_url", "image_url": {"url": self.create_data_uri(b64)}})

        user_content.append({"type": "text", "text": text})
        messages.append({"role": "user", "content": user_content})

        return self.manage_context(messages, reserve_tokens=max_tokens + 1400)

    async def generate_text(
        self,
        text:              str,
        history:           Optional[List[Dict[str, Any]]] = None,
        system_prompt:     Optional[str] = None,
        top_k:             int   = 40,
        top_p:             float = 0.95,
        min_p:             float = 0.05,
        repeat_penalty:    float = 1.1,
        temperature:       float = 0.5,
        seed:              int   = -1,
        max_tokens:        int   = 256,
        force_new_context: bool  = False,
    ) -> str:
        messages = self.build_text_message(
            text=text,
            history=history,
            system_prompt=system_prompt,
            force_new_context=force_new_context,
            max_tokens=max_tokens,
        )
        resp = self.llm.create_chat_completion(
            messages=messages,
            stream=False,
            max_tokens=max_tokens,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            min_p=min_p,
            repeat_penalty=repeat_penalty,
            seed=seed,
        )
        return await cleaned_response(resp["choices"][0]["message"]["content"])

    async def generate_vision(
        self,
        text:          str,
        image_path:    Optional[str] = None,
        screenshot:    bool = False,
        bbox:          Optional[Tuple[int, int, int, int]] = None,
        history:       Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        resize_max:    Optional[int] = None,
        image_quality: Optional[int] = None,
        top_k:         int   = 40,
        top_p:         float = 0.95,
        min_p:         float = 0.05,
        repeat_penalty: float = 1.1,
        temperature:   float = 0.5,
        seed:          int   = -1,
        max_tokens:    int   = 256,
    ) -> str:
        messages = self.build_vision_message(
            text=text,
            image_path=image_path,
            screenshot=screenshot,
            bbox=bbox,
            history=history,
            system_prompt=system_prompt,
            resize_max=resize_max,
            image_quality=image_quality,
        )
        resp = self.llm.create_chat_completion(
            messages=messages,
            stream=False,
            max_tokens=max_tokens,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            min_p=min_p,
            repeat_penalty=repeat_penalty,
            seed=seed,
        )
        return await cleaned_response(resp["choices"][0]["message"]["content"])


########################
####----------------####
####  Module API     ####
####----------------####
########################

system_message       = date_correct
vl_base: Optional[LocalLLM] = None


def reset_local_model() -> None:
    global vl_base
    vl_base = None
    clear_history()
    refresh_default_config()


def reload_system_message(force: bool = True) -> str:
    global system_message
    prompt_filename = get_settings("local_system_prompt_filename") or DEFAULT_CONFIG.system_prompt_filename
    try:
        template = load_system_message(resolve_llm_file(prompt_filename))
        new_system_message = format_system_template(template)
    except Exception as e:
        print(f"[LocalLLM] Failed to reload system prompt: {e}")
        new_system_message = ""

    if force or new_system_message != system_message:
        system_message = new_system_message
        clear_history()
        if vl_base is not None:
            vl_base.set_static_prompt(system_message)
    return system_message


async def local_init() -> LocalLLM:
    global vl_base
    config = refresh_default_config()

    if vl_base is not None and vl_base.config == config:
        return vl_base
    if vl_base is not None:
        clear_history()
        vl_base = None

    vl_base = LocalLLM(config)
    reload_system_message(force=True)
    vl_base.set_static_prompt(system_message)
    vl_base.warmup_system_prompt()

    try:
        from files.vision.VisionWatcher import get_watcher
        get_watcher().set_local_vision(bool(vl_base.spec.is_vision))
    except Exception as e:
        print(f"[LocalLLM] Could not sync vision watcher with model capabilities: {e}")

    return vl_base


def should_use_vision() -> bool:
    try:
        from files.vision.VisionWatcher import get_watcher, WatchMode
        watcher = get_watcher()
        if watcher.mode == WatchMode.DISABLED:
            return False
        if not getattr(watcher, "_local_vision", True):
            return False
        return True
    except Exception:
        return False


async def response_local(
    text:              str,
    history:           Optional[List[Dict[str, Any]]] = None,
    save_to_history:   bool  = True,
    top_k:             int   = 40,
    top_p:             float = 0.95,
    min_p:             float = 0.05,
    max_history:       Optional[int] = None,
    repeat_penalty:    float = 1.1,
    temperature:       float = 0.5,
    max_tokens:        int   = 256,
    force_new_context: bool  = False,
) -> str:
    if not text:
        print("Call received empty text")
        return ""

    max_history = max_history or DEFAULT_CONFIG.max_history_messages

    try:
        llm            = await local_init()
        started        = time.time()
        active_history = history if history is not None else get_history()
        use_vision = llm.spec.is_vision and should_use_vision()

        if use_vision:
            print(f"[LocalLLM] Vision path active — capturing screen for prompt")
            result = await llm.generate_vision(
                text=text,
                screenshot=True,
                history=active_history,
                system_prompt=system_message,
                max_tokens=max_tokens,
                temperature=temperature,
                top_k=top_k,
                top_p=top_p,
                min_p=min_p,
                repeat_penalty=repeat_penalty,
            )
        else:
            result = await llm.generate_text(
                text=text,
                history=active_history,
                system_prompt=system_message,
                max_tokens=max_tokens,
                temperature=temperature,
                top_k=top_k,
                top_p=top_p,
                min_p=min_p,
                repeat_penalty=repeat_penalty,
                force_new_context=force_new_context,
            )

        if save_to_history and result:
            add_user_message(text)
            add_assistant_message(result)
            trim_history(max_history)

        print(f"Total Inference Time: {time.time() - started:.2f}s")
        return result or ""

    except Exception as e:
        print(f"Exception in generation: {e}")
        print(traceback.format_exc())
        return ""


async def response_vision(
    text:            str  = "Describe what is on my screen briefly",
    bbox:            Optional[Tuple[int, int, int, int]] = None,
    image_path:      Optional[str] = None,
    history:         Optional[List[Dict[str, Any]]] = None,
    max_tokens:      int   = 300,
    save_to_history: bool  = True,
    max_history:     Optional[int] = None,
    temperature:     float = 0.7,
    top_k:           int   = 40,
    top_p:           float = 0.95,
    min_p:           float = 0.05,
    repeat_penalty:  float = 1.1,
) -> str:
    max_history = max_history or DEFAULT_CONFIG.max_history_messages

    try:
        llm            = await local_init()
        started        = time.time()
        prompt_text    = text or "Describe what is on my screen."
        active_history = history if history is not None else get_history()

        result = await llm.generate_vision(
            text=prompt_text,
            image_path=image_path,
            screenshot=image_path is None,
            bbox=bbox,
            history=active_history,
            system_prompt=system_message,
            max_tokens=max_tokens,
            resize_max=DEFAULT_CONFIG.screen_max_dim,
            image_quality=DEFAULT_CONFIG.screenshot_quality,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            min_p=min_p,
            repeat_penalty=repeat_penalty,
        )

        if save_to_history and result:
            add_assistant_message(result)
            trim_history(DEFAULT_CONFIG.max_history_messages)

        print(f"Total Inference Time: {time.time() - started:.2f}s")
        return result or ""

    except Exception as e:
        print(f"Error in Vision processing: {e}")
        print(traceback.format_exc())
        return ""


if __name__ == "__main__":
    import asyncio

    async def main():
        # text_input  = input("Enter Message: ")
        # text_result = await response_local(text_input)
        # print("\nLLM RESPONSE\n", text_result)

        vision_response = await response_vision(
            text="Describe what is on my screen including the character."
        )
        print("\n[VISION]\n", vision_response)

    asyncio.run(main())
