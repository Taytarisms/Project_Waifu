import customtkinter as ctk
from files.ui import theme
from files.closed_captions import (
    caption_controls,
    subscribe,
    get_current_caption,
)


class CaptionPreviewPanel(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        ctk.CTkLabel(
            self,
            text="Closed Captions",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=0, pady=(0, 6))

        self.status_label = ctk.CTkLabel(
            self,
            text=caption_controls.status_text(),
            text_color=theme.MUTED_TEXT,
            wraplength=280,
            justify="left",
        )
        self.status_label.pack(anchor="w", padx=0, pady=(0, 6))
        self.caption_box = ctk.CTkLabel(
            self,
            text=get_current_caption() or "(no caption)",
            text_color=theme.TEXT,
            fg_color="#0e141b",
            corner_radius=8,
            wraplength=300,
            justify="left",
            anchor="w",
            padx=12,
            pady=10,
        )
        self.caption_box.pack(fill="x", pady=(0, 6))
        self.enabled_var = ctk.BooleanVar(value=caption_controls.is_enabled())
        ctk.CTkSwitch(
            self,
            text="Enable Captions",
            variable=self.enabled_var,
            command=self._on_enabled_toggle,
            text_color=theme.TEXT,
            progress_color=theme.ACCENT,
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkButton(
            self,
            text="Clear Caption",
            fg_color="gray30",
            command=self._on_clear,
        ).pack(fill="x", pady=(0, 4))
        self._unsubscribe = subscribe(self._on_caption_changed)
        self._refresh_status_loop()

    def _on_caption_changed(self, text: str) -> None:
        try:
            self.after(0, lambda: self._apply_caption(text))
        except Exception:
            pass

    def _apply_caption(self, text: str) -> None:
        try:
            self.caption_box.configure(text=text or "(no caption)")
        except Exception:
            pass

    def _on_enabled_toggle(self) -> None:
        caption_controls.set_enabled(bool(self.enabled_var.get()))
        self.status_label.configure(text=caption_controls.status_text())

    def _on_clear(self) -> None:
        if hasattr(caption_controls, "clear_sync"):
            caption_controls.clear_sync()
        else:
            try:
                self.status_label.configure(text="Caption clear unavailable.")
            except Exception:
                pass

    def _refresh_status_loop(self) -> None:
        try:
            self.status_label.configure(text=caption_controls.status_text())
        except Exception:
            pass
        self.after(2500, self._refresh_status_loop)

    def destroy(self) -> None:
        try:
            self._unsubscribe()
        except Exception:
            pass
        super().destroy()