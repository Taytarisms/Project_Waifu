from __future__ import annotations

import asyncio
import atexit
import json
import os
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any


class LocalLLMWorkerClient:
    def __init__(self) -> None:
        self.proc: subprocess.Popen[str] | None = None
        self.lock = threading.Lock()
        self.stderr_tail: deque[str] = deque(maxlen=40)

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        if self.is_running():
            return

        root = Path(__file__).resolve().parents[2]
        worker = root / "files" / "llm" / "local_worker_process.py"

        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW

        self.proc = subprocess.Popen(
            [sys.executable, "-u", str(worker)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=str(root),
            creationflags=creationflags,
        )
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            text = line.rstrip()
            if text:
                self.stderr_tail.append(text)
            try:
                sys.stderr.write(f"[LocalLLM worker] {line}")
                sys.stderr.flush()
            except Exception:
                pass

    def _recent_stderr(self) -> str:
        return "\n".join(self.stderr_tail)

    def request(self, payload: dict[str, Any], *, start_if_needed: bool = True) -> dict[str, Any]:
        if start_if_needed:
            self.start()
        elif not self.is_running():
            return {"ok": True, "result": "not running"}

        assert self.proc is not None
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None

        with self.lock:
            try:
                self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
                line = self.proc.stdout.readline()
            except (BrokenPipeError, OSError) as exc:
                self.proc = None
                raise RuntimeError(f"Local LLM worker pipe failed: {exc}") from exc

        if not line:
            detail = self._recent_stderr()
            raise RuntimeError(
                "Local LLM worker exited without returning a response."
                + (f"\nRecent worker output:\n{detail}" if detail else "")
            )

        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid Local LLM worker response: {line.strip()}") from exc

        if not response.get("ok"):
            raise RuntimeError(response.get("error", "Unknown Local LLM worker error"))
        return response

    def reset_if_running(self) -> None:
        self.request({"cmd": "reset"}, start_if_needed=False)

    def shutdown(self) -> None:
        proc = self.proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                self.request({"cmd": "shutdown"}, start_if_needed=False)
                proc.wait(timeout=5)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        finally:
            self.proc = None


_local_worker = LocalLLMWorkerClient()


async def response_local_via_worker(text: str, **kwargs: Any) -> str:
    response = await asyncio.to_thread(
        _local_worker.request,
        {"cmd": "generate", "text": text, "kwargs": kwargs},
    )
    return response.get("result", "")


async def local_init_via_worker() -> None:
    await asyncio.to_thread(_local_worker.request, {"cmd": "init"})


def reset_local_worker_if_running() -> None:
    _local_worker.reset_if_running()


def shutdown_local_worker() -> None:
    _local_worker.shutdown()


atexit.register(shutdown_local_worker)
