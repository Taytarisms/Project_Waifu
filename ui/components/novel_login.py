import customtkinter as ctk
from files.ui import theme

try:
    from files.system_setup.settings import get_auth, save_auth
except Exception:
    get_auth = None
    save_auth = None


class NovelAILoginDialog(ctk.CTkToplevel):
    _BG        = "#0d1117"       # near-black background
    _CARD      = "#161b22"       # card / inner panel
    _BORDER    = "#30363d"       # subtle border
    _TEXT      = "#e6edf3"       # primary text
    _MUTED     = "#8b949e"       # secondary / helper text
    _ACCENT    = "#58a6ff"       # blue highlight (matches Steam-style blue)
    _DANGER    = "#f85149"       # red for warnings / cancel
    _INPUT_BG  = "#0d1117"       # entry field background
    _BTN_GREEN = "#238636"       # confirm button green
    _BTN_HOVER = "#2ea043"

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Sign in to NovelAI")
        self.geometry("440x520")
        self.resizable(False, False)
        self.configure(fg_color=self._BG)
        self.transient(parent)
        self.grab_set()
        self.focus_set()
        self._email:    str | None = None
        self._password: str | None = None
        self._save_creds: bool = False

        self._build()

        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width()  - self.winfo_width())  // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{px}+{py}")
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.wait_window(self)

    def _build(self):
        accent_bar = ctk.CTkFrame(self, height=3, fg_color=self._ACCENT, corner_radius=0)
        accent_bar.pack(fill="x", side="top")

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=32, pady=(24, 0))

        ctk.CTkLabel(
            header,
            text="🔑",
            font=ctk.CTkFont(size=28),
        ).pack(side="left", padx=(0, 10))

        title_col = ctk.CTkFrame(header, fg_color="transparent")
        title_col.pack(side="left")

        ctk.CTkLabel(
            title_col,
            text="Sign in to NovelAI",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=self._TEXT,
            anchor="w",
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_col,
            text="Your credentials are used once to obtain a 30-day token.",
            text_color=self._MUTED,
            font=ctk.CTkFont(size=12),
            anchor="w",
        ).pack(anchor="w")

        # Divider
        ctk.CTkFrame(self, height=1, fg_color=self._BORDER, corner_radius=0).pack(
            fill="x", padx=32, pady=(1, 0)
        )

        # Form card
        card = ctk.CTkFrame(self, fg_color=self._CARD, corner_radius=10)
        card.pack(fill="x", padx=28, pady=18)

        # Email field
        ctk.CTkLabel(
            card,
            text="Email address",
            text_color=self._MUTED,
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).pack(anchor="w", padx=18, pady=(18, 4))

        self._email_entry = ctk.CTkEntry(
            card,
            placeholder_text="you@example.com",
            fg_color=self._INPUT_BG,
            border_color=self._BORDER,
            text_color=self._TEXT,
            placeholder_text_color=self._MUTED,
            height=38,
            corner_radius=6,
        )
        self._email_entry.pack(fill="x", padx=18, pady=(0, 12))

        # Pre-fill from saved settings if available
        if get_auth:
            saved_email = get_auth("novelai", "mail")
            if saved_email:
                self._email_entry.insert(0, saved_email)

        # Password field
        ctk.CTkLabel(
            card,
            text="Password",
            text_color=self._MUTED,
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).pack(anchor="w", padx=18, pady=(0, 4))

        self._pass_entry = ctk.CTkEntry(
            card,
            placeholder_text="••••••••••",
            show="•",
            fg_color=self._INPUT_BG,
            border_color=self._BORDER,
            text_color=self._TEXT,
            placeholder_text_color=self._MUTED,
            height=38,
            corner_radius=6,
        )
        self._pass_entry.pack(fill="x", padx=18, pady=(0, 12))

        # Show/hide password toggle
        self._show_pass = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            card,
            text="Show password",
            variable=self._show_pass,
            command=self._toggle_password_visibility,
            text_color=self._MUTED,
            font=ctk.CTkFont(size=12),
            fg_color=self._ACCENT,
            hover_color=self._ACCENT,
            border_color=self._BORDER,
            checkmark_color="#ffffff",
        ).pack(anchor="w", padx=18, pady=(0, 6))

        # Save credentials checkbox
        self._save_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            card,
            text="Remember credentials (stored in settings.json)",
            variable=self._save_var,
            text_color=self._MUTED,
            font=ctk.CTkFont(size=12),
            fg_color=self._ACCENT,
            hover_color=self._ACCENT,
            border_color=self._BORDER,
            checkmark_color="#ffffff",
        ).pack(anchor="w", padx=18, pady=(0, 18))

        # Error / status label
        self._error_label = ctk.CTkLabel(
            self,
            text="",
            text_color=self._DANGER,
            font=ctk.CTkFont(size=12),
            wraplength=360,
            justify="left",
            anchor="w",
        )
        self._error_label.pack(fill="x", padx=32, pady=(0, 6))

        # Security notice
        notice = ctk.CTkFrame(self, fg_color="#1c2128", corner_radius=8)
        notice.pack(fill="x", padx=32, pady=(0, 16))

        ctk.CTkLabel(
            notice,
            text="🔒  Credentials are only used to generate a session token.\n"
                 "    They never leave your machine.",
            text_color=self._MUTED,
            font=ctk.CTkFont(size=11),
            justify="left",
            anchor="w",
        ).pack(anchor="w", padx=14, pady=10)

        # Button row
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=28, pady=(0, 24))

        # push buttons to the right
        spacer = ctk.CTkFrame(btn_row, fg_color="transparent")
        spacer.pack(side="left", expand=True)

        ctk.CTkButton(
            btn_row,
            text="Cancel",
            width=120,
            fg_color="#21262d",
            hover_color="#30363d",
            text_color=self._TEXT,
            border_color=self._BORDER,
            border_width=1,
            corner_radius=6,
            command=self._on_cancel,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_row,
            text="Sign in",
            width=140,
            fg_color=self._BTN_GREEN,
            hover_color=self._BTN_HOVER,
            text_color="#ffffff",
            corner_radius=6,
            command=self._on_confirm,
        ).pack(side="left")

        # Allow Enter to submit
        self.bind("<Return>", lambda _: self._on_confirm())
        self._email_entry.focus()

    def _toggle_password_visibility(self):
        self._pass_entry.configure(show="" if self._show_pass.get() else "•")

    def _on_confirm(self):
        email    = self._email_entry.get().strip()
        password = self._pass_entry.get().strip()

        if not email:
            self._error_label.configure(text="⚠  Please enter your email address.")
            self._email_entry.focus()
            return

        if not password:
            self._error_label.configure(text="⚠  Please enter your password.")
            self._pass_entry.focus()
            return

        if "@" not in email:
            self._error_label.configure(text="⚠  That doesn't look like a valid email address.")
            self._email_entry.focus()
            return

        self._email    = email
        self._password = password
        self._save_creds = self._save_var.get()

        if self._save_creds and save_auth:
            save_auth("novelai", "mail", email)
            save_auth("novelai", "password", password)

        self.grab_release()
        self.destroy()

    def _on_cancel(self):
        self._email    = None
        self._password = None
        self.grab_release()
        self.destroy()

    @classmethod
    def prompt(cls, parent) -> tuple[str | None, str | None]:
        dialog = cls(parent)
        return dialog._email, dialog._password