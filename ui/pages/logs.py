import os
import sys
import traceback
import warnings
import subprocess
import threading
from pathlib import Path
from datetime import datetime, date
from typing import Optional
import customtkinter as ctk
from files.ui import theme

ROOT_DIR = Path(__file__).resolve().parents[2]
LOG_DIR  = ROOT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

class _Cat:
    ERROR   = "error"
    GENERAL = "general"
    DAILY   = "daily"


# Basically ensuring everything is saved to a "catalogue" of sorts based on what's needed.
_LEVEL_TO_CAT: dict[str, str] = {
    "CRASH":        _Cat.ERROR,
    "THREAD CRASH": _Cat.ERROR,
    "ERROR":        _Cat.ERROR,
    "STDERR":       _Cat.ERROR,
    "WARNING":      _Cat.ERROR,
    "EXCEPTION":    _Cat.ERROR,
    "LATENCY":      _Cat.DAILY,
    "PERF":         _Cat.DAILY,
    "TIMING":       _Cat.DAILY,
    "METRIC":       _Cat.DAILY,
    "INFO":         _Cat.GENERAL,
    "STDOUT":       _Cat.GENERAL,
    "SUGGEST":      _Cat.GENERAL,
    "MODEL":        _Cat.GENERAL,
    "CONFIG":       _Cat.GENERAL,
    "DEBUG":        _Cat.GENERAL,
}

def _categorize(level: str) -> str:
    return _LEVEL_TO_CAT.get(level.upper(), _Cat.GENERAL)

def _log_path(category: str, for_date: date | None = None) -> Path:
    d = for_date or date.today()
    stamp = d.strftime("%m%d%y")
    prefix = {
        _Cat.ERROR:   "Error_Log",
        _Cat.GENERAL: "General_Log",
        _Cat.DAILY:   "Daily_Log",
    }.get(category, "General_Log")
    return LOG_DIR / f"{prefix}_{stamp}.txt"

class UILogStream:
    def __init__(self, original_stream, callback, level="INFO"):
        self.original_stream = original_stream
        self.callback = callback
        self.level = level
        self.buffer = ""

    def write(self, text):
        self.original_stream.write(text)
        self.original_stream.flush()
        if not text:
            return
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.strip()
            if line:
                try:
                    self.callback(self.level, line)
                except Exception:
                    pass

    def flush(self):
        self.original_stream.flush()

class LogManager:
    _lock = threading.Lock()
    _recording = False
    _viewer: Optional["LogsPage"] = None

    @classmethod
    def info(cls, text: str):
        cls.record("INFO", text)

    @classmethod
    def error(cls, text: str, *, exc_info: bool = False):
        if exc_info:
            text += "\n" + traceback.format_exc()
        cls.record("ERROR", text)

    @classmethod
    def perf(cls, label: str, *, duration_ms: float | None = None,
             detail: str = ""):
        parts = [label]
        if duration_ms is not None:
            parts.append(f"{duration_ms:.1f} ms")
        if detail:
            parts.append(detail)
        cls.record("LATENCY", " │ ".join(parts))

    @classmethod
    def suggest(cls, text: str):
        cls.record("SUGGEST", text)

    @classmethod
    def model(cls, text: str):
        cls.record("MODEL", text)

    @classmethod
    def record(cls, level: str, text: str):
        with cls._lock:
            if cls._recording:
                return
            cls._recording = True

        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = f"[{timestamp}] [{level}]\n{text}\n\n"

            cat = _categorize(level)
            path = _log_path(cat)

            try:
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(entry)
            except OSError:
                pass  # last resort iff anything, we're gonna do nothing!

            viewer = cls._viewer
            if viewer is not None:
                try:
                    viewer.after(0, lambda c=cat, e=entry, v=viewer: v._on_new_entry(c, e))
                except RuntimeError:
                    pass
                except Exception:
                    pass
        finally:
            with cls._lock:
                cls._recording = False

    @classmethod
    def set_viewer(cls, viewer: "LogsPage"):
        cls._viewer = viewer

    @classmethod
    def unset_viewer(cls):
        cls._viewer = None

class LogsPage(ctk.CTkFrame):
    _active_cat: str = _Cat.ERROR

    def __init__(self, master, app):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app
        LogManager.set_viewer(self)

        self._build_ui()
        self._install_hooks()
        self._load_active_log()

    def destroy(self):
        LogManager.unset_viewer()
        super().destroy()

    def _build_ui(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=20)
        left = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        header = ctk.CTkFrame(left, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(18, 4))

        self._title_label = ctk.CTkLabel(
            header,
            text="Error Logs",
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=theme.TEXT,
        )
        self._title_label.pack(side="left")

        self._date_label = ctk.CTkLabel(
            header,
            text="",
            font=ctk.CTkFont(size=13),
            text_color=theme.MUTED_TEXT,
        )
        self._date_label.pack(side="right")
        tab_bar = ctk.CTkFrame(left, fg_color="transparent")
        tab_bar.pack(fill="x", padx=18, pady=(4, 4))

        self._tab_buttons: dict[str, ctk.CTkButton] = {}
        for cat, label in [
            (_Cat.ERROR,   "Error Logs"),
            (_Cat.GENERAL, "General Logs"),
            (_Cat.DAILY,   "Daily Logs"),
        ]:
            btn = ctk.CTkButton(
                tab_bar,
                text=label,
                width=130,
                height=32,
                corner_radius=8,
                font=ctk.CTkFont(size=13, weight="bold"),
                command=lambda c=cat: self._switch_tab(c),
            )
            btn.pack(side="left", padx=(0, 6))
            self._tab_buttons[cat] = btn

        self.log_box = ctk.CTkTextbox(
            left,
            fg_color="#0e141b",
            text_color=theme.TEXT,
            font=ctk.CTkFont(family="Consolas", size=13),
            wrap="word",
        )
        self.log_box.pack(fill="both", expand=True, padx=18, pady=(8, 18))
        right = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=10, width=340)
        right.pack(side="right", fill="y", padx=(10, 0))
        right.pack_propagate(False)

        ctk.CTkLabel(
            right,
            text="Log Tools",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 8))

        for text, cmd in [
            ("Refresh",          self._load_active_log),
            ("Export Snapshot",  self._export_snapshot),
            ("Clear View",      self._clear_view),
        ]:
            ctk.CTkButton(
                right,
                text=text,
                command=cmd,
                fg_color="gray30" if text == "Clear View" else None,
            ).pack(fill="x", padx=16, pady=4)

        ctk.CTkFrame(right, fg_color=theme.MUTED_TEXT, height=1).pack(
            fill="x", padx=16, pady=12,
        )

        ctk.CTkLabel(
            right,
            text="Day Navigation",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(4, 4))

        nav = ctk.CTkFrame(right, fg_color="transparent")
        nav.pack(fill="x", padx=16, pady=4)

        ctk.CTkButton(nav, text="◀ Prev Day", width=100,
                       command=self._prev_day).pack(side="left", padx=(0, 4))
        ctk.CTkButton(nav, text="Today", width=80,
                       command=self._go_today).pack(side="left", padx=(0, 4))
        ctk.CTkButton(nav, text="Next Day ▶", width=100,
                       command=self._next_day).pack(side="left")
        ctk.CTkFrame(right, fg_color=theme.MUTED_TEXT, height=1).pack(
            fill="x", padx=16, pady=12,
        )

        # quick-log helpers
        ctk.CTkLabel(
            right,
            text="Quick Log",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(4, 4))

        self._quick_entry = ctk.CTkEntry(
            right,
            placeholder_text="Type a note…",
            font=ctk.CTkFont(size=13),
        )
        self._quick_entry.pack(fill="x", padx=16, pady=(4, 4))
        self._quick_entry.bind("<Return>", lambda _: self._quick_log())

        ql_row = ctk.CTkFrame(right, fg_color="transparent")
        ql_row.pack(fill="x", padx=16, pady=(0, 4))

        for label, level in [("Info", "INFO"), ("Suggest", "SUGGEST"), ("Perf", "LATENCY")]:
            ctk.CTkButton(
                ql_row, text=label, width=90,
                command=lambda lv=level: self._quick_log(lv),
            ).pack(side="left", padx=(0, 4))

        ctk.CTkFrame(right, fg_color=theme.MUTED_TEXT, height=1).pack(
            fill="x", padx=16, pady=12,
        )

        for text, cmd in [
            ("Open Logs Folder", lambda: self._open_path(LOG_DIR)),
            ("Open Files Folder", lambda: self._open_path(ROOT_DIR)),
        ]:
            ctk.CTkButton(right, text=text, command=cmd).pack(
                fill="x", padx=16, pady=4,
            )

        self._status_label = ctk.CTkLabel(
            right,
            text="",
            text_color=theme.MUTED_TEXT,
            wraplength=280,
            justify="left",
            font=ctk.CTkFont(size=12),
        )
        self._status_label.pack(anchor="w", padx=16, pady=(12, 8))
        self._viewed_date: date = date.today()
        self._update_tab_styles()
        self._update_date_label()

    _TAB_TITLES = {
        _Cat.ERROR:   "Error Logs",
        _Cat.GENERAL: "General Logs",
        _Cat.DAILY:   "Daily Logs",
    }

    def _switch_tab(self, cat: str):
        self._active_cat = cat
        self._update_tab_styles()
        self._title_label.configure(text=self._TAB_TITLES[cat])
        self._load_active_log()

    def _update_tab_styles(self):
        for cat, btn in self._tab_buttons.items():
            if cat == self._active_cat:
                btn.configure(fg_color=theme.TEXT, text_color=theme.APP_BG)
            else:
                btn.configure(fg_color="gray30", text_color=theme.TEXT)

    def _prev_day(self):
        from datetime import timedelta
        self._viewed_date -= timedelta(days=1)
        self._update_date_label()
        self._load_active_log()

    def _next_day(self):
        from datetime import timedelta
        self._viewed_date += timedelta(days=1)
        self._update_date_label()
        self._load_active_log()

    def _go_today(self):
        self._viewed_date = date.today()
        self._update_date_label()
        self._load_active_log()

    def _update_date_label(self):
        d = self._viewed_date
        if d == date.today():
            label = d.strftime("%B %d, %Y") + "  (today)"
        else:
            label = d.strftime("%B %d, %Y")
        self._date_label.configure(text=label)

    def _load_active_log(self):
        path = _log_path(self._active_cat, self._viewed_date)
        self.log_box.delete("0.0", "end")
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8")
                self.log_box.insert("0.0", content)
            except OSError:
                self.log_box.insert("0.0", f"Could not read {path.name}")
        else:
            self.log_box.insert(
                "0.0",
                f"No {self._TAB_TITLES[self._active_cat].lower()} for "
                f"{self._viewed_date.strftime('%m/%d/%Y')} yet.",
            )
        self._set_status(f"Viewing: {path.name}")

    def _on_new_entry(self, cat: str, entry: str):
        if cat == self._active_cat and self._viewed_date == date.today():
            self.log_box.insert("end", entry)
            self.log_box.see("end")

    def _clear_view(self):
        self.log_box.delete("0.0", "end")
        self._set_status("View cleared — log files were not deleted.")

    def _export_snapshot(self):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{self._TAB_TITLES[self._active_cat].replace(' ', '_')}_snapshot_{stamp}.txt"
        export_path = LOG_DIR / name
        text = self.log_box.get("0.0", "end-1c")
        export_path.write_text(text, encoding="utf-8")
        self._set_status(f"Exported → {export_path.name}")

    def _quick_log(self, level: str = "INFO"):
        text = self._quick_entry.get().strip()
        if not text:
            return
        LogManager.record(level, text)
        self._quick_entry.delete(0, "end")
        self._set_status(f"Logged [{level}]: {text[:60]}")

    def _install_hooks(self):
        sys.excepthook = self._handle_exception
        original_showwarning = warnings.showwarning
        def warning_hook(message, category, filename, lineno, file=None, line=None):
            text = warnings.formatwarning(message, category, filename, lineno, line)
            LogManager.record("WARNING", text)
            original_showwarning(message, category, filename, lineno, file, line)
        warnings.showwarning = warning_hook

        def thread_hook(args):
            text = "".join(
                traceback.format_exception(
                    args.exc_type,
                    args.exc_value,
                    args.exc_traceback,
                )
            )
            LogManager.record("THREAD CRASH", text)
        threading.excepthook = thread_hook
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = UILogStream(self._original_stdout, LogManager.record, level="STDOUT")
        sys.stderr = UILogStream(self._original_stderr, LogManager.record, level="STDERR")

    def _handle_exception(self, exc_type, exc_value, exc_traceback):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        LogManager.record("CRASH", text)

    def _set_status(self, msg: str):
        self._status_label.configure(text=msg)

    @staticmethod
    def _open_path(path: Path):
        path = Path(path)
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin": # Mac OS in case I decide to move on to it
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)]) # Same thing with Linux