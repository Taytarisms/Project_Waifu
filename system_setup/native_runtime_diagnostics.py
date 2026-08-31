import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
FILES_DIR = ROOT_DIR / "files"
BOOTSTRAP_DIR = FILES_DIR / "python_embeded"


def _configure_bundled_runtime() -> None:
    search_paths = [
        ROOT_DIR,
        BOOTSTRAP_DIR / "Lib",
        BOOTSTRAP_DIR / "DLLs",
    ]
    for path in reversed(search_paths):
        value = str(path)
        if path.exists() and value not in sys.path:
            sys.path.insert(0, value)

    if os.name == "nt":
        for path in (BOOTSTRAP_DIR, BOOTSTRAP_DIR / "DLLs"):
            if path.is_dir():
                os.add_dll_directory(str(path))

        tcl_dir = BOOTSTRAP_DIR / "tcl"
        fallback_tcl_dir = FILES_DIR / "tcl"
        if not tcl_dir.is_dir():
            tcl_dir = fallback_tcl_dir
        os.environ.setdefault("TCL_LIBRARY", str(tcl_dir / "tcl8.6"))
        os.environ.setdefault("TK_LIBRARY", str(tcl_dir / "tk8.6"))

    from files.system_setup.runtime_environment import configure_process_environment

    configure_process_environment()


def main() -> int:
    _configure_bundled_runtime()

    failures: list[str] = []
    for module_name in (
        "tkinter",
        "customtkinter",
        "numpy",
        "sounddevice",
        "faster_whisper",
        "torch",
    ):
        try:
            __import__(module_name)
            print(f"OK: import {module_name}")
        except Exception as exc:
            failures.append(f"{module_name}: {exc}")
            print(f"ERROR: import {module_name} failed: {exc}")

    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        print("OK: create tkinter window")
    except Exception as exc:
        failures.append(f"tkinter window: {exc}")
        print(f"ERROR: tkinter window creation failed: {exc}")

    try:
        import torch

        print(f"PyTorch: {torch.__version__}")
        print(f"CUDA build: {torch.version.cuda}")
        print(f"Torch accelerator available: {torch.cuda.is_available()}")
    except Exception:
        pass

    try:
        import llama_cpp
        from files.system_setup.llama_runtime import load_dynamic_backends

        load_dynamic_backends(llama_cpp)
        print(f"llama.cpp GPU offload: {llama_cpp.llama_supports_gpu_offload()}")
    except Exception as exc:
        print(f"WARNING: llama.cpp unavailable: {exc}")

    if failures:
        print("ERROR: Required app runtime imports failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("Core runtime diagnostics passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
