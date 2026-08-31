import subprocess
import sys
import threading
import time
from pathlib import Path


def _elapsed_text(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _write_console_status(message: str, width: int = 0) -> int:
    padded_width = max(width, len(message))
    sys.stdout.write("\r" + message.ljust(padded_width))
    sys.stdout.flush()
    return padded_width


def _run_quiet(command: list[str], log, log_path: Path, *, optional: bool) -> int:
    try:
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
    except OSError as exc:
        log.write(f"ERROR: could not start command: {exc}\n")
        log.flush()
        print(f"    [FAILED] Could not start this setup step. See {log_path.name}.")
        return 127

    assert process.stdout is not None

    def copy_output() -> None:
        for line in process.stdout:
            log.write(line)
            log.flush()

    reader = threading.Thread(target=copy_output, name="setup-log-reader", daemon=True)
    reader.start()

    started = time.monotonic()
    status_width = 0
    interactive = sys.stdout.isatty()
    if not interactive:
        print("    Working...", flush=True)

    try:
        while process.poll() is None:
            if interactive:
                elapsed = _elapsed_text(time.monotonic() - started)
                status_width = _write_console_status(f"    Working... {elapsed}", status_width)
            time.sleep(0.2)
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        reader.join(timeout=2)
        if interactive:
            _write_console_status("    [CANCELLED] Setup interrupted.", status_width)
            print()
        else:
            print("    [CANCELLED] Setup interrupted.")
        return 130

    exit_code = process.wait()
    reader.join()
    elapsed = _elapsed_text(time.monotonic() - started)
    if exit_code == 0:
        result = f"    [OK] Completed in {elapsed}."
    elif optional:
        result = (
            f"    [WARNING] Optional component was not installed "
            f"(exit code {exit_code}). See {log_path.name}."
        )
    else:
        result = f"    [FAILED] Exit code {exit_code}. See {log_path.name}."

    if interactive:
        _write_console_status(result, status_width)
        print()
    else:
        print(result)
    return exit_code


def main() -> int:
    if "--" not in sys.argv or len(sys.argv) < 4:
        print(
            "Usage: run_logged.py <log_path> [--quiet] [--optional] -- "
            "<command> [args...]",
            file=sys.stderr,
        )
        return 2

    separator = sys.argv.index("--")
    log_path = Path(sys.argv[1])
    options = sys.argv[2:separator]
    command = sys.argv[separator + 1:]
    if not command:
        print("ERROR: no command provided", file=sys.stderr)
        return 2
    allowed_options = {"--quiet", "--optional"}
    unknown_options = [option for option in options if option not in allowed_options]
    if unknown_options:
        print(f"ERROR: unknown option(s): {' '.join(unknown_options)}", file=sys.stderr)
        return 2
    quiet = "--quiet" in options
    optional = "--optional" in options

    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("a", encoding="utf-8", errors="replace") as log:
        log.write("\nCOMMAND: " + subprocess.list2cmdline(command) + "\n")
        log.flush()

        if quiet:
            exit_code = _run_quiet(command, log, log_path, optional=optional)
            log.write(f"COMMAND EXIT CODE: {exit_code}\n")
            log.flush()
            return exit_code

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

        exit_code = process.wait()
        log.write(f"COMMAND EXIT CODE: {exit_code}\n")
        log.flush()
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
