import json
import customtkinter as ctk
from files.ui import theme

try:
    from files.system_setup.settings import get_settings, save_settings
except Exception:
    get_settings = None
    save_settings = None

## Models can be found here: https://developers.openai.com/api/docs/models/all
## OpenAI has a ton of models that are deprecated so I included the cheap options too.
## I did not include Davinci since I'm using the chat api and not completions.

OPENAI_MODELS = [
    "gpt-5.5",
    "gpt-5.5-pro",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.2",
    "gpt-5.1",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-3.5-turbo",
]

class OpenAISettingsPage(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="OpenAI Chat Settings",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=theme.TEXT,
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 10))

        self.model_var = ctk.StringVar(value=self._setting("openai_model", "gpt-4.1"))
        self.temperature_var = ctk.DoubleVar(value=float(self._setting("openai_temperature", 0.7)))
        self.max_tokens_var = ctk.DoubleVar(value=float(self._setting("openai_max_tokens", 512)))
        self.top_p_var = ctk.DoubleVar(value=float(self._setting("openai_top_p", 1.0)))
        self.presence_penalty_var = ctk.DoubleVar(value=float(self._setting("openai_presence_penalty", 0.0)))
        self.frequency_penalty_var = ctk.DoubleVar(value=float(self._setting("openai_frequency_penalty", 0.0)))
        self.stop_var = ctk.StringVar(value=str(self._setting("openai_stop", "")))

        self._combo_row("Model", self.model_var, OPENAI_MODELS, row=1)
        self._slider_row("Temperature", self.temperature_var, 0.0, 2.0, row=2)
        self._slider_row("Max Tokens", self.max_tokens_var, 64, 8192, row=3, is_int=True)
        self._slider_row("Top P", self.top_p_var, 0.0, 1.0, row=4)
        self._slider_row("Presence Penalty", self.presence_penalty_var, -2.0, 2.0, row=5)
        self._slider_row("Frequency Penalty", self.frequency_penalty_var, -2.0, 2.0, row=6)

        ctk.CTkLabel(
            self,
            text="Stop Sequence",
            text_color=theme.TEXT,
            anchor="w",
        ).grid(row=7, column=0, sticky="w", padx=18, pady=(14, 4))

        ctk.CTkEntry(
            self,
            textvariable=self.stop_var,
            placeholder_text="Optional, comma-separated",
            width=360,
        ).grid(row=8, column=0, sticky="w", padx=18, pady=(0, 14))

        ctk.CTkButton(
            self,
            text="Save OpenAI Settings",
            command=self.save,
        ).grid(row=9, column=0, sticky="w", padx=18, pady=(10, 8))

        self.status_label = ctk.CTkLabel(
            self,
            text="No changes saved yet.",
            text_color=theme.MUTED_TEXT,
            anchor="w",
        )
        self.status_label.grid(row=10, column=0, sticky="w", padx=18, pady=(0, 18))

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

        combo = ctk.CTkComboBox(frame, variable=var, values=values, width=340)
        combo.grid(row=0, column=1, sticky="w")

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
            "openai_model": self.model_var.get().strip(),
            "openai_temperature": float(self.temperature_var.get()),
            "openai_max_tokens": int(self.max_tokens_var.get()),
            "openai_top_p": float(self.top_p_var.get()),
            "openai_presence_penalty": float(self.presence_penalty_var.get()),
            "openai_frequency_penalty": float(self.frequency_penalty_var.get()),
            "openai_stop": self.stop_var.get().strip(),
        }

        if save_settings:
            for key, value in payload.items():
                save_settings(key, value)
            self.status_label.configure(text="OpenAI settings saved.")
        else:
            with open("openai_settings.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4)
            self.status_label.configure(text="Saved to openai_settings.json.")