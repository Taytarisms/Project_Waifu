import sys
from datetime import datetime, timezone

## Creates instances of colored text for the terminal and makes things more noticeable/readable.
## As well as ensuring that you know what you get from logging from basic prints to error logging.
## Sourced from GitHub at: https://gist.github.com/kamito/704813

class Logger:
    ui_ref = None
    log_buffer = []
    current_turn_id = None

    @classmethod
    def set_turn_id(cls, turn_id):
        cls.current_turn_id = turn_id

    @classmethod
    def clear_turn_id(cls):
        cls.current_turn_id = None

    @classmethod
    def stream_answer(cls, turn_id, speaker, text):
        ui = cls.ui_ref
        if ui is not None and turn_id:
            try:
                ui.after(0, lambda: ui.stream_answer(turn_id, speaker, text))
            except Exception:
                pass

    @classmethod
    def bind_ui(cls, ui):
        cls.ui_ref = ui

    @classmethod
    def turn_metric(cls, text, turn_id):
        cls.quiet_print(text)
        ui = cls.ui_ref
        if ui is not None and turn_id:
            try:
                ui.after(0, lambda: ui.log_status(text, turn_id))
            except Exception:
                pass

    @classmethod
    def detail(cls, text):
        cls.quiet_print(text)
        try:
            from files.ui.pages.logs import LogManager
            LogManager.record("LATENCY", text)
        except Exception:
            pass

    @classmethod
    def memory_summary(cls, summary):
        ui = cls.ui_ref
        if ui is not None and hasattr(ui, "show_memory_summary"):
            payload = {"id": summary["id"], "text": summary["text"],
                       "count": len(summary.get("turn_ids", [])),
                       "created_at": summary.get("created_at")}
            ui.after(0, lambda: ui.show_memory_summary(payload))

    @classmethod
    def unbind_ui(cls):
        cls.ui_ref = None

    @classmethod
    def quiet_print(cls, *args):
        result = cls._stringify(args)
        sys.stdout.write(result + "\n") # Plain text
        cls.log_buffer.append({"text": result, "time": datetime.now(timezone.utc).timestamp()})

    @classmethod
    def print(cls, *args):
        result = cls._stringify(args)
        sys.stdout.write(result + "\n")
        sys.stdout.flush()
        cls.log_buffer.append({"text": result, "time": datetime.now(timezone.utc).timestamp()})
        cls._forward_to_ui(result)

    @classmethod
    def warn(cls, *args):
        result = cls._stringify(args)
        sys.stdout.write('\033[93m' + result + '\033[0m\n')
        sys.stdout.flush()
        cls.log_buffer.append({"text": result, "time": datetime.now(timezone.utc).timestamp()})
        cls._forward_to_ui(result)

    @classmethod
    def success(cls, *args):
        result = cls._stringify(args)
        sys.stdout.write('\033[1;32m' + result + '\033[0m\n')  # Green in console
        cls.log_buffer.append({"text": result, "time": datetime.now(timezone.utc).timestamp()})

    @classmethod
    def error(cls, *args):
        result = cls._stringify(args)
        sys.stdout.write('\033[91m' + result + '\033[0m\n')  # Red in console
        sys.stdout.flush()
        cls.log_buffer.append({"text": result, "time": datetime.now(timezone.utc).timestamp()})
        cls._forward_to_ui(result)

    @classmethod
    def notify(cls, *args):
        result = cls._stringify(args)
        sys.stdout.write('\033[1;34m' + result + '\033[0m\n') # Blue in console
        cls.log_buffer.append({"text": result, "time": datetime.now(timezone.utc).timestamp()})

    @classmethod
    def _forward_to_ui(cls, result):
        if cls.ui_ref is not None:
            try:
                cls.ui_ref.after(0,lambda r=result, tid=cls.current_turn_id: cls.ui_ref.log_status(r, tid))
            except Exception as e:
                sys.stdout.write(f"[Logger UI bind failed] {e}\n")

    @staticmethod
    def _stringify(args):
        return " ".join([str(a) if not isinstance(a, (dict, list)) else repr(a) for a in args])
