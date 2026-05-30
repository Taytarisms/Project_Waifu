import base64
import tkinter as tk
from tkinter import filedialog
from pathlib import Path
import customtkinter as ctk
from files.ui import theme
from files.system_setup.system_logger import Logger

FILES_DIR = Path(__file__).resolve().parents[2]
MODERATION_DIR = FILES_DIR / "moderation"
BAD_WORDS_FILE = MODERATION_DIR / "bad_words_b64"

def _decode_b64_file(path: Path = BAD_WORDS_FILE) -> list[str]:
    if not path.exists():
        Logger.warn(f"Bad-words file not found: {path}")
        return []
    try:
        encoded = "".join(path.read_text(encoding="utf-8").split())
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except Exception as exc:
        Logger.warn(f"Failed to decode bad-words file: {exc}")
        return []
    return sorted(
        {
            " ".join(line.strip().lower().split())
            for line in decoded.splitlines()
            if line.strip()
        }
    )

def _encode_to_b64_file(words: list[str], path: Path = BAD_WORDS_FILE) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        cleaned_words = sorted(
            {
                " ".join(w.strip().lower().split())
                for w in words
                if w and w.strip()
            }
        )
        plain = "\n".join(cleaned_words)
        if plain:
            plain += "\n"
        encoded = base64.b64encode(plain.encode("utf-8")).decode("ascii")
        path.write_text(encoded, encoding="utf-8")
        return True
    except Exception as exc:
        Logger.warn(f"Failed to encode bad-words file: {exc}")
        return False

class _WordListFrame(ctk.CTkFrame):
    def __init__(self, master, **kw):
        super().__init__(master, fg_color=theme.APP_BG, **kw)
        self._scroll = ctk.CTkScrollableFrame(
            self,
            fg_color=theme.APP_BG,
            scrollbar_button_color=theme.MUTED_TEXT,
            scrollbar_button_hover_color=theme.TEXT,
        )
        self._scroll.pack(fill="both", expand=True)
        self._checkboxes: list[ctk.CTkCheckBox] = []
        self._check_vars: list[tk.BooleanVar] = []
        self._displayed_words: list[str] = []

    def set_words(self, words: list[str]):
        self._clear()
        MAX_RENDERED_WORDS = 500

        shown_words = list(words[:MAX_RENDERED_WORDS])
        self._displayed_words = shown_words

        for word in shown_words:
            var = tk.BooleanVar(value=False)

            cb = ctk.CTkCheckBox(
                self._scroll,
                text=word,
                variable=var,
                font=ctk.CTkFont(family="Consolas", size=14),
                text_color=theme.TEXT,
                fg_color=theme.MUTED_TEXT,
                hover_color=theme.TEXT,
                border_color=theme.MUTED_TEXT,
                checkmark_color=theme.APP_BG,
            )
            cb.pack(anchor="w", padx=8, pady=2)

            self._checkboxes.append(cb)
            self._check_vars.append(var)

    def get_selected(self) -> list[str]:
        return [
            word
            for word, var in zip(self._displayed_words, self._check_vars)
            if var.get()
        ]

    def select_all(self):
        for var in self._check_vars:
            var.set(True)

    def deselect_all(self):
        for var in self._check_vars:
            var.set(False)

    def _clear(self):
        for cb in self._checkboxes:
            cb.destroy()
        self._checkboxes.clear()
        self._check_vars.clear()
        self._displayed_words.clear()

class ModerationPage(ctk.CTkFrame):
    def __init__(self, master, app=None):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app

        self._words: list[str] = []
        self._dirty: bool = False
        self._page_size: int = 500
        self._page_index: int = 0
        self._filtered_words: list[str] = []

        self._build_header()
        self._build_search_bar()
        self._build_paging_bar()
        self._build_word_list()
        self._build_add_bar()
        self._build_action_buttons()
        self._build_status_bar()

        self._do_decode()
        self.after(100, self._safe_initial_decode)

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color=theme.APP_BG)
        header.pack(fill="x", padx=20, pady=(18, 4))
        ctk.CTkLabel(
            header,
            text="Moderation · Bad-Word Manager",
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=theme.TEXT,
        ).pack(side="left")

        self._dirty_label = ctk.CTkLabel(
            header,
            text="",
            font=ctk.CTkFont(size=14),
            text_color="#e8a838",
        )
        self._dirty_label.pack(side="left", padx=12)

    def _build_search_bar(self):
        bar = ctk.CTkFrame(self, fg_color=theme.APP_BG)
        bar.pack(fill="x", padx=20, pady=(6, 2))
        ctk.CTkLabel(
            bar,
            text="🔍",
            font=ctk.CTkFont(size=16),
            text_color=theme.MUTED_TEXT,
        ).pack(side="left", padx=(0, 6))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
        self._search_entry = ctk.CTkEntry(
            bar,
            textvariable=self._search_var,
            placeholder_text="Search words…",
            font=ctk.CTkFont(size=14),
            text_color=theme.TEXT,
            fg_color=theme.APP_BG,
            border_color=theme.MUTED_TEXT,
            width=320,
        )
        self._search_entry.pack(side="left", fill="x", expand=True)
        self._count_label = ctk.CTkLabel(
            bar,
            text="",
            font=ctk.CTkFont(size=13),
            text_color=theme.MUTED_TEXT,
        )
        self._count_label.pack(side="right", padx=(10, 0))

    def _build_paging_bar(self):
        bar = ctk.CTkFrame(self, fg_color=theme.APP_BG)
        bar.pack(fill="x", padx=20, pady=(2, 2))

        btn_cfg = dict(
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=theme.MUTED_TEXT,
            hover_color=theme.TEXT,
            text_color=theme.APP_BG,
            height=30,
            corner_radius=8,
        )

        self._prev_button = ctk.CTkButton(
            bar,
            text="← Previous 500",
            command=self._prev_page,
            width=130,
            **btn_cfg,
        )
        self._prev_button.pack(side="left", padx=(0, 6))

        self._next_button = ctk.CTkButton(
            bar,
            text="Next 500 →",
            command=self._next_page,
            width=130,
            **btn_cfg,
        )
        self._next_button.pack(side="left", padx=(0, 12))

        self._page_label = ctk.CTkLabel(
            bar,
            text="",
            font=ctk.CTkFont(size=13),
            text_color=theme.MUTED_TEXT,
        )
        self._page_label.pack(side="left")

    def _max_page_index(self) -> int:
        if not self._filtered_words:
            return 0

        return max(0, (len(self._filtered_words) - 1) // self._page_size)


    def _next_page(self):
        max_page = self._max_page_index()

        if self._page_index < max_page:
            self._page_index += 1
            self._render_current_page()


    def _prev_page(self):
        if self._page_index > 0:
            self._page_index -= 1
            self._render_current_page()

    def _build_word_list(self):
        self._word_list = _WordListFrame(self)
        self._word_list.pack(fill="both", expand=True, padx=20, pady=6)

    def _build_add_bar(self):
        bar = ctk.CTkFrame(self, fg_color=theme.APP_BG)
        bar.pack(fill="x", padx=20, pady=(2, 4))
        self._add_entry = ctk.CTkEntry(
            bar,
            placeholder_text="Add a new word or phrase…",
            font=ctk.CTkFont(size=14),
            text_color=theme.TEXT,
            fg_color=theme.APP_BG,
            border_color=theme.MUTED_TEXT,
        )
        self._add_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._add_entry.bind("<Return>", lambda _: self._do_add())

        ctk.CTkButton(
            bar,
            text="＋ Add",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=theme.MUTED_TEXT,
            hover_color=theme.TEXT,
            text_color=theme.APP_BG,
            width=90,
            command=self._do_add,
        ).pack(side="left")

    def _safe_initial_decode(self):
        try:
            self._do_decode()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self._set_status(f"Moderation page load error: {exc}")

    def _build_action_buttons(self):
        bar = ctk.CTkFrame(self, fg_color=theme.APP_BG)
        bar.pack(fill="x", padx=20, pady=(2, 4))
        btn_cfg = dict(
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=theme.MUTED_TEXT,
            hover_color=theme.TEXT,
            text_color=theme.APP_BG,
            height=34,
            corner_radius=8,
        )

        ctk.CTkButton(
            bar,
            text="Remove Selected",
            command=self._do_remove,
            **btn_cfg,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            bar,
            text="Select All",
            command=self._word_list.select_all,
            **btn_cfg,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            bar,
            text="Deselect All",
            command=self._word_list.deselect_all,
            **btn_cfg,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            bar,
            text="💾 Encode & Save",
            command=self._do_encode,
            width=140,
            fg_color="#3a8a5c",
            hover_color="#2e6e49",
            text_color="#ffffff",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=34,
            corner_radius=8,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            bar,
            text="📂 Decode / Reload",
            command=self._do_decode,
            width=150,
            **btn_cfg,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            bar,
            text="Export .txt",
            command=self._do_export,
            width=100,
            **btn_cfg,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            bar,
            text="Import .txt",
            command=self._do_import,
            width=100,
            **btn_cfg,
        ).pack(side="right", padx=(6, 0))

    def _build_status_bar(self):
        self._status = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=12),
            text_color=theme.MUTED_TEXT,
            anchor="w",
        )
        self._status.pack(fill="x", padx=24, pady=(0, 10))

    def _do_decode(self):
        self._words = _decode_b64_file()
        self._dirty = False
        self._refresh()
        self._set_status(f"Decoded {len(self._words)} words from {BAD_WORDS_FILE.name}")

    def _do_encode(self):
        if _encode_to_b64_file(self._words):
            self._words = _decode_b64_file()
            self._dirty = False
            self._refresh()
            self._set_status(f"Encoded {len(self._words)} words → {BAD_WORDS_FILE.name}")
        else:
            self._set_status("⚠ Encode failed — check logs.")

    def _do_add(self):
        raw = self._add_entry.get().strip().lower()
        if not raw:
            return
        word = " ".join(raw.split())
        if word in self._words:
            self._set_status(f'"{word}" is already in the list.')
            return
        self._words.append(word)
        self._words = sorted(set(self._words))
        self._dirty = True

        self._add_entry.delete(0, "end")
        self._refresh()
        self._set_status(f'Added "{word}" — remember to Encode & Save.')

    def _do_remove(self):
        selected = self._word_list.get_selected()
        if not selected:
            self._set_status("Nothing selected.")
            return
        selected_set = set(selected)
        self._words = [word for word in self._words if word not in selected_set]
        self._dirty = True
        old_page = self._page_index
        self._apply_filter()
        self._page_index = min(old_page, self._max_page_index())
        self._render_current_page()
        self._dirty_label.configure(text="● Unsaved changes" if self._dirty else "")
        self._set_status(f"Removed {len(selected)} word(s) — remember to Encode & Save.")

    def _do_import(self):
        path = filedialog.askopenfilename(
            title="Import bad-word list",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
            new_words = {
                " ".join(line.strip().lower().split())
                for line in text.splitlines()
                if line.strip()
            }

            before = len(self._words)
            self._words = sorted(set(self._words) | new_words)
            added = len(self._words) - before
            self._dirty = True
            self._refresh()
            self._set_status(f"Imported {added} new word(s) from {Path(path).name}.")
        except Exception as exc:
            self._set_status(f"Import error: {exc}")

    def _do_export(self):
        path = filedialog.asksaveasfilename(
            title="Export bad-word list",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_text(
                "\n".join(self._words) + "\n",
                encoding="utf-8",
            )
            self._set_status(f"Exported {len(self._words)} words → {Path(path).name}")

        except Exception as exc:
            self._set_status(f"Export error: {exc}")

    def _apply_filter(self):
        query = self._search_var.get().strip().lower()
        if query:
            self._filtered_words = [word for word in self._words if query in word]
        else:
            self._filtered_words = list(self._words)
        self._page_index = 0
        self._render_current_page()

    def _refresh(self):
        self._apply_filter()
        self._dirty_label.configure(
            text="● Unsaved changes" if self._dirty else ""
        )

    def _set_status(self, msg: str):
        self._status.configure(text=msg)

    def _render_current_page(self):
        total_filtered = len(self._filtered_words)
        total_words = len(self._words)
        max_page = self._max_page_index()
        if self._page_index > max_page:
            self._page_index = max_page
        start = self._page_index * self._page_size
        end = start + self._page_size
        page_words = self._filtered_words[start:end]
        self._word_list.set_words(page_words)
        shown_start = start + 1 if total_filtered else 0
        shown_end = min(end, total_filtered)
        self._count_label.configure(
            text=f"{shown_start}-{shown_end} shown / {total_filtered} matched / {total_words} total"
        )
        self._page_label.configure(
            text=f"Page {self._page_index + 1} of {max_page + 1}"
        )
        prev_state = "normal" if self._page_index > 0 else "disabled"
        next_state = "normal" if self._page_index < max_page else "disabled"
        self._prev_button.configure(state=prev_state)
        self._next_button.configure(state=next_state)