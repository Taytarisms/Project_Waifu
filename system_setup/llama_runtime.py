import os
from pathlib import Path
from typing import Any


def load_dynamic_backends(
    llama_module: Any = None,
    ggml_module: Any = None,
) -> bool:
    if llama_module is None:
        import llama_cpp as llama_module

    supports_offload = getattr(llama_module, "llama_supports_gpu_offload", None)
    if supports_offload is None:
        return False

    try:
        if bool(supports_offload()):
            return True
    except Exception:
        return False

    try:
        if ggml_module is None:
            from llama_cpp import _ggml as ggml_module

        load_all = getattr(ggml_module, "ggml_backend_load_all_from_path", None)
        module_file = getattr(llama_module, "__file__", "")
        if load_all is None or not module_file:
            return False

        library_dir = Path(module_file).resolve().parent / "lib"
        if not library_dir.is_dir():
            return False

        load_all(os.fsencode(str(library_dir)))
        return bool(supports_offload())
    except Exception:
        return False
