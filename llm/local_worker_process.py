import asyncio
import json
import sys
import traceback
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

for _stream in (sys.stdin, sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="strict")

_json_stdout = sys.stdout
sys.stdout = sys.stderr

try:
    from files.system_setup.runtime_environment import configure_process_environment
    configure_process_environment()
except Exception:
    pass

from files.llm.LocalLLM import local_init, reset_local_model, response_local
from files.system_setup.settings import reload_settings


async def handle_request(request: dict) -> dict:
    command = request.get("cmd")

    if command == "init":
        reload_settings()
        await local_init()
        return {"ok": True, "result": "initialized"}

    if command == "generate":
        reload_settings()
        text = request.get("text", "")
        kwargs = request.get("kwargs", {}) or {}
        result = await response_local(text=text, **kwargs)
        payload = result.to_dict() if hasattr(result, "to_dict") else result
        return {"ok": True, "result": payload}

    if command == "reset":
        reload_settings()
        reset_local_model()
        return {"ok": True, "result": "reset"}

    if command == "shutdown":
        return {"ok": True, "result": "shutdown", "shutdown": True}

    return {"ok": False, "error": f"Unknown Local LLM worker command: {command!r}"}


async def main() -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = await handle_request(request)
        except Exception:
            response = {"ok": False, "error": traceback.format_exc()}

        _json_stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        _json_stdout.flush()

        if response.get("shutdown"):
            break


if __name__ == "__main__":
    asyncio.run(main())
