import customtkinter as ctk
from files.ui import theme


class SteamHeader(ctk.CTkFrame):
    def __init__(self, master, title, subtitle="", action_text=None, action_command=None):
        super().__init__(master, fg_color=theme.HEADER_BG, corner_radius=0, height=140)
        self.pack_propagate(False)

        text_frame = ctk.CTkFrame(self, fg_color="transparent")
        text_frame.pack(side="left", fill="both", expand=True, padx=24, pady=20)

        ctk.CTkLabel(
            text_frame,
            text=title,
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color=theme.TEXT,
            anchor="w",
        ).pack(anchor="w")

        ctk.CTkLabel(
            text_frame,
            text=subtitle,
            font=ctk.CTkFont(size=14),
            text_color=theme.MUTED_TEXT,
            anchor="w",
            wraplength=680,
        ).pack(anchor="w", pady=(8, 0))

        if action_text and action_command:
            ctk.CTkButton(
                self,
                text=action_text,
                fg_color=theme.SUCCESS,
                hover_color="#4a9f35",
                command=action_command,
                width=140,
            ).pack(side="right", padx=24)