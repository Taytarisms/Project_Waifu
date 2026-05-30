import customtkinter

class ChatBubble(customtkinter.CTkFrame):
    def __init__(self, master, sender: str, text: str, role: str, **kwargs):
        super().__init__(master, **kwargs)
        self.sender = sender
        self.role   = role.lower()

        # pick style by role
        if self.role == "user":
            bubble_color, anchor, justify = "#147efb", "e", "right"
        elif self.role == "ai":
            bubble_color, anchor, justify = "#8e8e93", "w", "left"
        elif self.role == "system":
            bubble_color, anchor, justify = "#8e8e93", "w", "left"
        else:  # external/twitch chat
            bubble_color, anchor, justify = "#5caf4c", "e", "right"

        self.anchor = anchor

        self.configure(fg_color=bubble_color, corner_radius=10, border_width=1, border_color="#cccccc")
        self.label = customtkinter.CTkLabel(self, text=text, wraplength=300, anchor=anchor, justify=justify)
        self.label.pack(padx=10, pady=5)
