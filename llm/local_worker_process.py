import asyncio
import json
import sys
import traceback
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

_json_stdout = sys.stdout
sys.stdout = sys.stderr

try:
    from files.system_setup.runtime_environment import configure_process_environment
    configure_process_environment()
except Exception:
    pass

from files.llm.LocalLLM import local_init, reset_local_model, response_local


async def handle_request(request: dict) -> dict:
    command = request.get("cmd")

    if command == "init":
        await local_init()
        return {"ok": True, "result": "initialized"}

    if command == "generate":
        text = request.get("text", "")
        kwargs = request.get("kwargs", {}) or {}
        result = await response_local(text=text, **kwargs)
        return {"ok": True, "result": result}

    if command == "reset":
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
