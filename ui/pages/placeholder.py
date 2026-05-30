import customtkinter as ctk
from files.ui import theme


class PlaceholderPage(ctk.CTkFrame):
    def __init__(self, master, title):
        super().__init__(master, fg_color=theme.APP_BG)

        ctk.CTkLabel(
            self,
            text=title,
            font=ctk.CTkFont(size=28, weight="bold"),
            text_color=theme.TEXT,
        ).pack(pady=40)

        ctk.CTkLabel(
            self,
            text="Page skeleton ready.",
            text_color=theme.MUTED_TEXT,
        ).pack()