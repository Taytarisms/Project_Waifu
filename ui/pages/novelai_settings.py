import customtkinter as ctk
from files.ui import theme

try:
    from files.system_setup.settings import get_settings, save_settings
except Exception:
    get_settings = None
    save_settings = None


NOVELAI_MODELS = [
    "kayra",
    "clio",
    "erato",   # newer / higher tier
    "custom",
]

class NovelAISettingsPage(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="NovelAI Generation Settings",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=theme.TEXT,
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 10))

        self.model_var = ctk.StringVar(value=self._setting("novel_model", "kayra"))
        self.temperature_var = ctk.DoubleVar(value=float(self._setting("novel_temperature", 0.7)))
        self.max_tokens_var = ctk.DoubleVar(value=float(self._setting("novel_max_tokens", 512)))
        self.top_p_var = ctk.DoubleVar(value=float(self._setting("novel_top_p", 0.9)))
        self.top_k_var = ctk.DoubleVar(value=float(self._setting("novel_top_k", 50)))

        # Model itself (Kayra, Clio, etc.)
        self._combo_row("Model", self.model_var, NOVELAI_MODELS, 1)

        # Creativity and Token output
        self._slider_row("Creativity (Temperature)", self.temperature_var, 0.0, 2.0, 2)
        self._slider_row("Max Length", self.max_tokens_var, 64, 2048, 3, is_int=True)

        # Model Sampling (I wouldn't recommend adjusting)
        self._slider_row("Top P", self.top_p_var, 0.0, 1.0, 4)
        self._slider_row("Top K", self.top_k_var, 0, 200, 5, is_int=True)

        ctk.CTkButton(
            self,
            text="Save NovelAI Settings",
            command=self.save,
        ).grid(row=6, column=0, sticky="w", padx=18, pady=18)

        self.status_label = ctk.CTkLabel(
            self,
            text="No changes saved yet.",
            text_color=theme.MUTED_TEXT,
        )
        self.status_label.grid(row=7, column=0, sticky="w", padx=18)

    def _setting(self, key, default):
        if not get_settings:
            return default
        value = get_settings(key)
        return default if value in (None, "") else value

    def _combo_row(self, label, var, values, row):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=18, pady=8)

        ctk.CTkLabel(frame, text=label, text_color=theme.TEXT).pack(side="left")

        ctk.CTkComboBox(frame, variable=var, values=values, width=260).pack(side="left", padx=10)

    def _slider_row(self, label, var, min_val, max_val, row, is_int=False):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=18, pady=8)

        label_widget = ctk.CTkLabel(
            frame,
            text=self._format(label, var.get(), is_int),
            text_color=theme.TEXT,
            width=200,
            anchor="w",
        )
        label_widget.pack(side="left")

        ctk.CTkSlider(
            frame,
            from_=min_val,
            to=max_val,
            variable=var,
            command=lambda v: label_widget.configure(
                text=self._format(label, v, is_int)
            ),
        ).pack(side="left", fill="x", expand=True, padx=10)

    def _format(self, label, value, is_int=False):
        return f"{label}: {int(float(value))}" if is_int else f"{label}: {float(value):.2f}"

    def save(self):
        payload = {
            "novel_model": self.model_var.get(),
            "novel_temperature": float(self.temperature_var.get()),
            "novel_max_tokens": int(self.max_tokens_var.get()),
            "novel_top_p": float(self.top_p_var.get()),
            "novel_top_k": int(self.top_k_var.get()),
        }

        if save_settings:
            for k, v in payload.items():
                save_settings(k, v)

        self.status_label.configure(text="NovelAI settings saved.")