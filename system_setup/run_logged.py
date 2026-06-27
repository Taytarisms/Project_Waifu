import subprocess
import sys
from pathlib import Path


def main() -> int:
    if "--" not in sys.argv or len(sys.argv) < 4:
        print("Usage: run_logged.py <log_path> -- <command> [args...]", file=sys.stderr)
        return 2

    separator = sys.argv.index("--")
    log_path = Path(sys.argv[1])
    command = sys.argv[separator + 1:]
    if not command:
        print("ERROR: no command provided", file=sys.stderr)
        return 2

    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("a", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            log.flush()

        return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
