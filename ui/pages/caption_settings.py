import customtkinter as ctk
from tkinter import filedialog
from pathlib import Path

from files.ui import theme
from files.ui.components.header import SteamHeader
from files.system_setup.settings import get_settings
from files.system_setup.system_logger import Logger
from files.closed_captions import caption_controls, subscribe, get_current_caption


class CaptionsSettingsPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app

        is_enabled = caption_controls.is_enabled()

        SteamHeader(
            self,
            title="Closed Captions",
            subtitle=(
                "Writes the currently-spoken text to a file so OBS can "
                "display it as an on-stream overlay."
            ),
            action_text="✓ Enabled" if is_enabled else "Enable Captions",
            action_command=self._toggle_enabled_from_header,
        ).pack(fill="x")

        nav_frame = ctk.CTkFrame(self, fg_color="transparent")
        nav_frame.pack(fill="x", padx=24, pady=(12, 0))
        ctk.CTkButton(
            nav_frame,
            text="← Back",
            width=100,
            fg_color="gray30",
            command=lambda: self.app.show_page("TTS"),
        ).pack(side="left")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=20)

        left = ctk.CTkScrollableFrame(body, fg_color=theme.CARD_BG, corner_radius=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        content = ctk.CTkFrame(left, fg_color="transparent")
        content.pack(fill="both", expand=True)
        self._build_left_column(content)

        right_outer = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=10, width=340)
        right_outer.pack(side="right", fill="y", padx=(10, 0))
        right_outer.pack_propagate(False)

        right = ctk.CTkScrollableFrame(right_outer, fg_color="transparent", corner_radius=10)
        right.pack(fill="both", expand=True)
        self._build_right_column(right)
        self._unsubscribe = subscribe(self._on_caption_changed)

    def _build_left_column(self, parent):
        ctk.CTkLabel(
            parent,
            text="Captions Settings",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=18, pady=(18, 12))

        # Master switch
        self.enabled_var = ctk.BooleanVar(value=caption_controls.is_enabled())
        ctk.CTkSwitch(
            parent,
            text="Enable Captions",
            variable=self.enabled_var,
            command=self._on_enabled_toggle,
            text_color=theme.TEXT,
            progress_color=theme.ACCENT,
        ).pack(anchor="w", padx=18, pady=(0, 18))

        ctk.CTkLabel(
            parent,
            text="What gets captioned",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=18, pady=(0, 6))

        self.narrator_var = ctk.BooleanVar(
            value=self._bool("captions_for_narrator", default=True)
        )
        ctk.CTkSwitch(
            parent,
            text="Narrator-read chat messages",
            variable=self.narrator_var,
            command=lambda: caption_controls.set_caption_narrator(
                bool(self.narrator_var.get())
            ),
            text_color=theme.TEXT,
            progress_color=theme.ACCENT,
        ).pack(anchor="w", padx=18, pady=(0, 4))

        self.user_var = ctk.BooleanVar(
            value=self._bool("captions_for_user_messages", default=True)
        )
        ctk.CTkSwitch(
            parent,
            text="My messages (manual + STT)",
            variable=self.user_var,
            command=lambda: caption_controls.set_caption_user_messages(
                bool(self.user_var.get())
            ),
            text_color=theme.TEXT,
            progress_color=theme.ACCENT,
        ).pack(anchor="w", padx=18, pady=(0, 4))

        self.llm_var = ctk.BooleanVar(
            value=self._bool("captions_for_llm_replies", default=False)
        )
        ctk.CTkSwitch(
            parent,
            text="AI replies (the character's response)",
            variable=self.llm_var,
            command=lambda: caption_controls.set_caption_llm_replies(
                bool(self.llm_var.get())
            ),
            text_color=theme.TEXT,
            progress_color=theme.ACCENT,
        ).pack(anchor="w", padx=18, pady=(0, 18))

        ctk.CTkLabel(
            parent,
            text="Display Mode",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=18, pady=(0, 4))

        self.typed_var = ctk.BooleanVar(
            value=self._bool("captions_typed_mode", default=False)
        )
        ctk.CTkSwitch(
            parent,
            text="Typed mode (word-by-word, synced to audio)",
            variable=self.typed_var,
            command=lambda: caption_controls.set_typed_mode(
                bool(self.typed_var.get())
            ),
            text_color=theme.TEXT,
            progress_color=theme.ACCENT,
        ).pack(anchor="w", padx=18, pady=(0, 4))

        ctk.CTkLabel(
            parent,
            text=(
                "When on, captions reveal one word at a time, paced to "
                "finish at the same moment audio playback ends. Works "
                "for NovelAI, Edge, ElevenLabs, and OpenAI TTS. Streaming "
                "providers (Inworld, Kokoro, Qwen3, FishSpeech) fall back "
                "to whole-message display."
            ),
            text_color=theme.MUTED_TEXT,
            wraplength=440,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 18))

        ctk.CTkLabel(
            parent,
            text="Wrap & Sliding Window",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=18, pady=(0, 4))

        ctk.CTkLabel(
            parent,
            text=(
                "Long captions wrap to multiple lines. Only the most "
                "recent lines stay on screen — older lines scroll off "
                "as new words arrive, similar to broadcast captions."
            ),
            text_color=theme.MUTED_TEXT,
            wraplength=440,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 8))

        # Wrap width slider
        initial_wrap = self._int("captions_wrap_width", default=60, minimum=10)

        wrap_row = ctk.CTkFrame(parent, fg_color="transparent")
        wrap_row.pack(fill="x", padx=18, pady=(0, 8))
        ctk.CTkLabel(
            wrap_row,
            text="Line width (chars)",
            text_color=theme.TEXT,
            width=140,
            anchor="w",
        ).pack(side="left")

        self.wrap_value_label = ctk.CTkLabel(
            wrap_row,
            text=str(initial_wrap),
            text_color=theme.MUTED_TEXT,
            width=40,
        )
        self.wrap_value_label.pack(side="right")

        self.wrap_slider = ctk.CTkSlider(
            wrap_row,
            from_=20,
            to=120,
            number_of_steps=100,
            command=self._on_wrap_slide,
            progress_color=theme.ACCENT,
        )
        self.wrap_slider.set(initial_wrap)
        self.wrap_slider.pack(side="left", fill="x", expand=True, padx=8)

        initial_window = self._int("captions_window_lines", default=2, minimum=1)

        window_row = ctk.CTkFrame(parent, fg_color="transparent")
        window_row.pack(fill="x", padx=18, pady=(0, 18))
        ctk.CTkLabel(
            window_row,
            text="Lines on screen",
            text_color=theme.TEXT,
            width=140,
            anchor="w",
        ).pack(side="left")

        self.window_value_label = ctk.CTkLabel(
            window_row,
            text=str(initial_window),
            text_color=theme.MUTED_TEXT,
            width=40,
        )
        self.window_value_label.pack(side="right")

        self.window_slider = ctk.CTkSlider(
            window_row,
            from_=1,
            to=6,
            number_of_steps=5,
            command=self._on_window_slide,
            progress_color=theme.ACCENT,
        )
        self.window_slider.set(initial_window)
        self.window_slider.pack(side="left", fill="x", expand=True, padx=8)

        # File path override
        ctk.CTkLabel(
            parent,
            text="Caption File Path",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=18, pady=(0, 4))

        ctk.CTkLabel(
            parent,
            text="Point your OBS Text Source's \"read from file\" option at this path.",
            text_color=theme.MUTED_TEXT,
            wraplength=440,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 6))

        path_row = ctk.CTkFrame(parent, fg_color="transparent")
        path_row.pack(fill="x", padx=18, pady=(0, 18))

        self.path_var = ctk.StringVar(value=caption_controls.get_file_path())
        ctk.CTkEntry(
            path_row,
            textvariable=self.path_var,
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            path_row,
            text="Browse",
            width=80,
            fg_color="gray30",
            command=self._on_browse,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            path_row,
            text="Save",
            width=80,
            command=self._on_save_path,
        ).pack(side="left")

    def _build_right_column(self, parent):
        ctk.CTkLabel(
            parent,
            text="Captions Summary",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 8))

        self.status_label = ctk.CTkLabel(
            parent,
            text=caption_controls.status_text(),
            text_color=theme.MUTED_TEXT,
            wraplength=280,
            justify="left",
        )
        self.status_label.pack(anchor="w", padx=16, pady=8)

        # Live preview
        ctk.CTkFrame(parent, fg_color="#2d3f52", height=1).pack(
            fill="x", padx=16, pady=(12, 0)
        )
        ctk.CTkLabel(
            parent,
            text="Live Preview",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(14, 6))

        self.preview_box = ctk.CTkLabel(
            parent,
            text=get_current_caption() or "(no caption)",
            text_color=theme.TEXT,
            fg_color="#0e141b",
            corner_radius=8,
            wraplength=280,
            justify="left",
            anchor="w",
            padx=12,
            pady=10,
        )
        self.preview_box.pack(fill="x", padx=16, pady=(0, 8))

        # Test row
        ctk.CTkFrame(parent, fg_color="#2d3f52", height=1).pack(
            fill="x", padx=16, pady=(12, 0)
        )
        ctk.CTkLabel(
            parent,
            text="Test",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(14, 6))

        self.test_text_var = ctk.StringVar(value="Caption preview test.")
        ctk.CTkEntry(
            parent,
            textvariable=self.test_text_var,
        ).pack(fill="x", padx=16, pady=(0, 8))

        test_row = ctk.CTkFrame(parent, fg_color="transparent")
        test_row.pack(fill="x", padx=16, pady=(0, 16))

        ctk.CTkButton(
            test_row,
            text="Push Preview",
            command=self._on_preview,
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))

        ctk.CTkButton(
            test_row,
            text="Clear",
            fg_color="gray30",
            command=self._on_clear,
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        ctk.CTkButton(
            parent,
            text="Back to TTS Library",
            command=lambda: self.app.show_page("TTS"),
            fg_color="gray30",
        ).pack(fill="x", padx=16, pady=(8, 16))

    def _bool(self, name: str, default: bool = False) -> bool:
        try:
            raw = get_settings(name)
        except Exception:
            return default
        if raw is None or raw == "":
            return default
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("true", "1", "yes", "on")

    def _int(self, name: str, default: int, minimum: int = 1) -> int:
        try:
            raw = get_settings(name)
        except Exception:
            return default
        if raw is None or raw == "":
            return default
        try:
            value = int(raw)
        except Exception:
            return default
        return value if value >= minimum else default

    def _on_caption_changed(self, text: str) -> None:
        try:
            self.after(0, lambda: self.preview_box.configure(text=text or "(no caption)"))
        except Exception:
            pass

    def _toggle_enabled_from_header(self):
        new_value = not bool(self.enabled_var.get())
        self.enabled_var.set(new_value)
        caption_controls.set_enabled(new_value)
        self.status_label.configure(text=caption_controls.status_text())

    def _on_enabled_toggle(self):
        caption_controls.set_enabled(bool(self.enabled_var.get()))
        self.status_label.configure(text=caption_controls.status_text())

    def _on_browse(self):
        current_path = caption_controls.get_file_path()
        path = filedialog.asksaveasfilename(
            title="Choose caption file location",
            defaultextension=".txt",
            initialdir=str(Path(current_path).parent),
            initialfile="closed_captions.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.path_var.set(path)
            caption_controls.set_file_path(path)
            self.path_var.set(caption_controls.get_file_path())
            Logger.print(f"Captions file path set to: {path}")

    def _on_save_path(self):
        path = self.path_var.get().strip()
        caption_controls.set_file_path(path)
        self.path_var.set(caption_controls.get_file_path())
        Logger.print(f"Captions file path set to: {path or '(default)'}")

    def _on_wrap_slide(self, value):
        v = int(round(float(value)))
        self.wrap_value_label.configure(text=str(v))
        caption_controls.set_wrap_width(v)

    def _on_window_slide(self, value):
        v = int(round(float(value)))
        self.window_value_label.configure(text=str(v))
        caption_controls.set_window_lines(v)

    def _on_preview(self):
        text = self.test_text_var.get().strip() or "Caption preview test."
        caption_controls.preview_sync(text)

    def _on_clear(self):
        caption_controls.clear_sync()

    def destroy(self):
        try:
            self._unsubscribe()
        except Exception:
            pass
        super().destroy()
