import customtkinter as ctk

from files.ui import theme

try:
    from files.system_setup.settings import get_auth, save_auth
except Exception:
    get_auth = None
    save_auth = None


class NovelAILoginDialog(ctk.CTkToplevel):
    """Compatibility dialog for older NovelAI callers.

    NovelAI auth in this app uses API tokens only.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title("NovelAI API Token")
        self.geometry("460x300")
        self.resizable(False, False)
        self.configure(fg_color=theme.APP_BG)
        self.transient(parent)
        self.grab_set()
        self.focus_set()

        self._token: str | None = None
        self._show_token = ctk.BooleanVar(value=False)

        self._build()
        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{px}+{py}")
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.wait_window(self)

    def _build(self):
        ctk.CTkLabel(
            self,
            text="NovelAI API Token",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=24, pady=(24, 8))

        ctk.CTkLabel(
            self,
            text="Paste your NovelAI API token. Account sign-in is not supported in-app.",
            text_color=theme.MUTED_TEXT,
            wraplength=400,
            justify="left",
        ).pack(anchor="w", padx=24, pady=(0, 14))

        self._token_entry = ctk.CTkEntry(
            self,
            placeholder_text="NovelAI API token",
            show="*",
        )
        self._token_entry.pack(fill="x", padx=24, pady=(0, 10))
        self._token_entry.focus()

        if get_auth:
            token = get_auth("novelai", "token")
            if token:
                self._token_entry.insert(0, token)

        ctk.CTkCheckBox(
            self,
            text="Show token",
            variable=self._show_token,
            command=self._toggle_token_visibility,
            text_color=theme.MUTED_TEXT,
        ).pack(anchor="w", padx=24, pady=(0, 12))

        self._error_label = ctk.CTkLabel(
            self,
            text="",
            text_color=theme.DANGER,
            wraplength=400,
            justify="left",
        )
        self._error_label.pack(anchor="w", padx=24, pady=(0, 10))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=24, pady=(0, 20))

        ctk.CTkButton(
            btn_row,
            text="Cancel",
            fg_color="gray30",
            command=self._on_cancel,
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            btn_row,
            text="Save Token",
            command=self._on_confirm,
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

        self.bind("<Return>", lambda _event: self._on_confirm())

    def _toggle_token_visibility(self):
        self._token_entry.configure(show="" if self._show_token.get() else "*")

    def _on_confirm(self):
        token = self._token_entry.get().strip()
        if not token:
            self._error_label.configure(text="Please paste your NovelAI API token.")
            self._token_entry.focus()
            return

        self._token = token
        if save_auth:
            save_auth("novelai", "token", token)
            save_auth("novelai", "mail", "")
            save_auth("novelai", "password", "")
        self.grab_release()
        self.destroy()

    def _on_cancel(self):
        self._token = None
        self.grab_release()
        self.destroy()

    @classmethod
    def prompt(cls, parent) -> str | None:
        dialog = cls(parent)
        return dialog._token
