import json
import os
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


_WRITE_LOCK = threading.RLock()


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    with _WRITE_LOCK:
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def load_json_safe(
    path: Path,
    default: Any,
    expected_type: type | tuple[type, ...],
    *,
    logger: Callable[[str], None] | None = None,
) -> Any:
    fallback = deepcopy(default)

    try:
        if not path.exists() or not path.read_text(encoding="utf-8").strip():
            write_json_atomic(path, fallback)
            return fallback

        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, expected_type):
            raise TypeError(f"Expected {expected_type}, got {type(value)}")
        return value

    except Exception as exc:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        corrupt_path = path.with_suffix(path.suffix + f".corrupt-{timestamp}")
        try:
            if path.exists():
                path.rename(corrupt_path)
        except Exception:
            pass

        write_json_atomic(path, fallback)
        if logger:
            try:
                logger(f"Recovered bad JSON file {path}: {exc}")
            except Exception:
                pass
        return fallback
