import customtkinter as ctk
from files.ui import theme


class AuthTokenDialog(ctk.CTkToplevel):
    def __init__(self, master, title, label, on_save):
        super().__init__(master)

        self.title(title)
        self.geometry("420x220")
        self.resizable(False, False)
        self.configure(fg_color=theme.APP_BG)

        self.on_save = on_save
        self.token_var = ctk.StringVar()

        self.grab_set()

        ctk.CTkLabel(
            self,
            text=title,
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=20, pady=(20, 8))

        ctk.CTkLabel(
            self,
            text=label,
            text_color=theme.MUTED_TEXT,
            wraplength=360,
            justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 12))

        self.entry = ctk.CTkEntry(
            self,
            textvariable=self.token_var,
            placeholder_text="Paste token / API key here...",
            show="*",
        )
        self.entry.pack(fill="x", padx=20, pady=(0, 14))
        self.entry.focus()

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=20)

        ctk.CTkButton(
            btn_row,
            text="Save",
            command=self.save,
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            btn_row,
            text="Cancel",
            fg_color="gray30",
            command=self.destroy,
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

    def save(self):
        token = self.token_var.get().strip()
        if token:
            self.on_save(token)
            self.destroy()