import ctypes
import os
from pathlib import Path


_DLL_DIRECTORY_HANDLES = []
_TCL_LIBRARY_HANDLE = None
_TCL_PREINIT_SCRIPT: bytes | None = None


def _prepend_path(path: Path) -> None:
    if not path.exists():
        return

    value = str(path)
    current = os.environ.get("PATH", "")
    parts = [part for part in current.split(os.pathsep) if part]
    if any(part.lower() == value.lower() for part in parts):
        return
    os.environ["PATH"] = value + os.pathsep + current


def configure_process_environment() -> None:
    global _TCL_LIBRARY_HANDLE, _TCL_PREINIT_SCRIPT

    files_dir = Path(__file__).resolve().parents[1]
    bootstrap_dir = files_dir / "python_embeded"

    _prepend_path(files_dir / "ffmpeg")
    _prepend_path(files_dir / "bin")
    _prepend_path(bootstrap_dir)
    _prepend_path(bootstrap_dir / "DLLs")
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    if os.name == "nt":
        tcl_root = bootstrap_dir / "tcl"
        if not (tcl_root / "tcl8.6" / "init.tcl").is_file():
            tcl_root = files_dir / "tcl"
        tcl_library = tcl_root / "tcl8.6"
        os.environ.pop("TCL_LIBRARY", None)
        os.environ.pop("TK_LIBRARY", None)

        dll_dir = bootstrap_dir / "DLLs"
        if dll_dir.is_dir() and hasattr(os, "add_dll_directory"):
            try:
                handle = os.add_dll_directory(str(dll_dir))
                _DLL_DIRECTORY_HANDLES.append(handle)
            except OSError:
                pass

        tcl_dll = dll_dir / "tcl86t.dll"
        if _TCL_PREINIT_SCRIPT is None and tcl_dll.is_file():
            try:
                import tkinter

                _TCL_LIBRARY_HANDLE = ctypes.CDLL(str(tcl_dll))
                set_preinit = _TCL_LIBRARY_HANDLE.TclSetPreInitScript
                set_preinit.argtypes = [ctypes.c_char_p]
                set_preinit.restype = None
                tcl_path = str(tcl_library.resolve()).replace("\\", "/")
                tcl_path = tcl_path.replace("}", "\\}")
                _TCL_PREINIT_SCRIPT = (
                    f"set ::tcl_library {{{tcl_path}}}"
                ).encode("utf-8")
                set_preinit(_TCL_PREINIT_SCRIPT)
            except (AttributeError, OSError):
                _TCL_LIBRARY_HANDLE = None
                _TCL_PREINIT_SCRIPT = None

    # stopping sox warning since it's not on path by default
    os.environ.setdefault("PATH_SOX", "")
