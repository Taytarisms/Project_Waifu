import customtkinter as ctk
from files.ui import theme


class TopNav(ctk.CTkFrame):
    def __init__(self, master, nav_callback, pages):
        super().__init__(master, fg_color=theme.HEADER_BG, height=58)
        self.nav_callback = nav_callback
        self.buttons = {}

        self.pack_propagate(False)

        title = ctk.CTkLabel(
            self,
            text="Home Page",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=theme.TEXT,
        )
        title.pack(side="left", padx=(18, 28))

        for page in pages:
            btn = ctk.CTkButton(
                self,
                text=page,
                width=90,
                fg_color="transparent",
                hover_color=theme.CARD_HOVER,
                text_color=theme.TEXT,
                command=lambda p=page: self.nav_callback(p),
            )
            btn.pack(side="left", padx=4)
            self.buttons[page] = btn

    def set_active(self, page_name):
        for name, btn in self.buttons.items():
            btn.configure(
                fg_color=theme.ACCENT if name == page_name else "transparent",
                text_color="#0e141b" if name == page_name else theme.TEXT,
            )