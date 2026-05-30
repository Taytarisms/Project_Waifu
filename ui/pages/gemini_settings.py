import json
import customtkinter as ctk
from files.ui import theme

try:
    from files.system_setup.settings import get_settings, save_settings
except Exception:
    get_settings = None
    save_settings = None

## Active Models can be found here: https://ai.google.dev/gemini-api/docs/models
## These models are current according to the docs, the Gemini 3 series was discontinued back in March of this year.

GEMINI_MODELS = [
    "gemini-3.1-pro",
    "gemini-3-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]


class GeminiSettingsPage(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="Gemini Chat Settings",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=theme.TEXT,
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 10))

        self.model_var       = ctk.StringVar(value=self._setting("gemini_model", "gemini-2.5-flash"))
        self.temperature_var = ctk.DoubleVar(value=float(self._setting("gemini_temperature", 1.0)))
        self.max_tokens_var  = ctk.DoubleVar(value=float(self._setting("gemini_max_tokens",  1024)))
        self.top_p_var       = ctk.DoubleVar(value=float(self._setting("gemini_top_p",       1.0)))
        self.top_k_var       = ctk.DoubleVar(value=float(self._setting("gemini_top_k",       0)))

        self._combo_row("Model",        self.model_var,       GEMINI_MODELS, row=1)
        self._slider_row("Temperature", self.temperature_var, 0.0,  2.0,  row=2)
        self._slider_row("Max Tokens",  self.max_tokens_var,  64,   8192, row=3, is_int=True)
        self._slider_row("Top P",       self.top_p_var,       0.0,  1.0,  row=4)
        self._slider_row("Top K",       self.top_k_var,       0,    100,  row=5, is_int=True)

        ctk.CTkLabel(
            self,
            text="Top K (0 = disabled)",
            text_color=theme.MUTED_TEXT,
            anchor="w",
            font=ctk.CTkFont(size=11),
        ).grid(row=6, column=0, sticky="w", padx=18, pady=(0, 12))

        ctk.CTkButton(
            self,
            text="Save Gemini Settings",
            command=self.save,
        ).grid(row=7, column=0, sticky="w", padx=18, pady=(10, 8))

        self.status_label = ctk.CTkLabel(
            self,
            text="No changes saved yet.",
            text_color=theme.MUTED_TEXT,
            anchor="w",
        )
        self.status_label.grid(row=8, column=0, sticky="w", padx=18, pady=(0, 18))

    def _setting(self, key, default):
        if not get_settings:
            return default
        value = get_settings(key)
        return default if value in (None, "") else value

    def _combo_row(self, label, var, values, row):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=18, pady=8)
        frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(frame, text=label, text_color=theme.TEXT, width=150, anchor="w").grid(row=0, column=0)
        ctk.CTkComboBox(frame, variable=var, values=values, width=340).grid(row=0, column=1, sticky="w")

    def _slider_row(self, label, var, min_value, max_value, row, is_int=False):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=18, pady=8)
        frame.grid_columnconfigure(1, weight=1)

        value_label = ctk.CTkLabel(
            frame,
            text=self._format(label, var.get(), is_int),
            text_color=theme.TEXT,
            width=170,
            anchor="w",
        )
        value_label.grid(row=0, column=0, sticky="w")
        ctk.CTkSlider(
            frame,
            from_=min_value,
            to=max_value,
            variable=var,
            command=lambda v: value_label.configure(text=self._format(label, v, is_int)),
        ).grid(row=0, column=1, sticky="ew", padx=10)

    def _format(self, label, value, is_int=False):
        return f"{label}: {int(float(value))}" if is_int else f"{label}: {float(value):.2f}"

    def save(self):
        payload = {
            "gemini_model":       self.model_var.get().strip(),
            "gemini_temperature": float(self.temperature_var.get()),
            "gemini_max_tokens":  int(self.max_tokens_var.get()),
            "gemini_top_p":       float(self.top_p_var.get()),
            "gemini_top_k":       int(self.top_k_var.get()),
        }
        if save_settings:
            for key, value in payload.items():
                save_settings(key, value)
            self.status_label.configure(text="Gemini settings saved.")
        else:
            with open("gemini_settings.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4)
            self.status_label.configure(text="Saved to gemini_settings.json.")