import json
import customtkinter as ctk
from files.ui import theme
from files.provider_capabilities import CLAUDE_CHAT_MODELS

try:
    from files.system_setup.settings import get_settings, save_settings
except Exception:
    get_settings = None
    save_settings = None

## Models can be found here: https://platform.claude.com/docs/en/about-claude/models/overview
## Every model listed here is up to date. According to the docs, Claude 4 will be discontinued June 15th

CLAUDE_MODELS = CLAUDE_CHAT_MODELS

class ClaudeSettingsPage(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="Claude Chat Settings",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=theme.TEXT,
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 10))

        self.model_var       = ctk.StringVar(value=self._setting("claude_model",       "claude-sonnet-4-6"))
        self.temperature_var = ctk.DoubleVar(value=float(self._setting("claude_temperature", 1.0)))
        self.max_tokens_var  = ctk.DoubleVar(value=float(self._setting("claude_max_tokens",  4096)))
        self.top_p_var       = ctk.DoubleVar(value=float(self._setting("claude_top_p",       1.0)))
        self.top_k_var       = ctk.DoubleVar(value=float(self._setting("claude_top_k",       0)))
        saved_thinking_effort = str(self._setting("claude_thinking_effort", "medium"))
        saved_thinking_mode = str(self._setting("claude_thinking_mode", "adaptive"))
        if saved_thinking_effort.lower() in {"none", "off", "disabled"}:
            saved_thinking_mode = "disabled"
            saved_thinking_effort = "medium"
        self.thinking_mode_var = ctk.StringVar(value=saved_thinking_mode)
        self.thinking_effort_var = ctk.StringVar(
            value=saved_thinking_effort
        )

        self._combo_row("Model",       self.model_var,       CLAUDE_MODELS, row=1)
        self._slider_row("Temperature", self.temperature_var, 0.0,  1.0,  row=2)
        self._slider_row("Max Tokens",  self.max_tokens_var,  64,   8192, row=3, is_int=True)
        self._slider_row("Top P",       self.top_p_var,       0.0,  1.0,  row=4)
        self._slider_row("Top K",       self.top_k_var,       0,    500,  row=5, is_int=True)
        self._combo_row(
            "Thinking Mode",
            self.thinking_mode_var,
            ["adaptive", "disabled"],
            row=6,
        )
        self._combo_row(
            "Adaptive Effort",
            self.thinking_effort_var,
            ["low", "medium", "high", "max"],
            row=7,
        )

        ctk.CTkLabel(
            self,
            text=(
                "Sampling controls are saved for compatible older models. "
                "They are automatically omitted for Opus 4.7+ and Claude 5. "
                "Claude 4.6+ uses the selected adaptive thinking mode. While it "
                "is enabled, incompatible sampling controls are omitted to prevent "
                "invalid-request errors. Max Tokens is shared by thinking and the "
                "visible answer; 4096 or more is recommended with adaptive thinking."
            ),
            text_color=theme.MUTED_TEXT,
            anchor="w",
            font=ctk.CTkFont(size=11),
            wraplength=560,
        ).grid(row=8, column=0, sticky="w", padx=18, pady=(0, 12))

        ctk.CTkButton(
            self,
            text="Save Claude Settings",
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
            "claude_model":       self.model_var.get().strip(),
            "claude_temperature": float(self.temperature_var.get()),
            "claude_max_tokens":  int(self.max_tokens_var.get()),
            "claude_top_p":       float(self.top_p_var.get()),
            "claude_top_k":       int(self.top_k_var.get()),
            "claude_thinking_mode": self.thinking_mode_var.get().strip(),
            "claude_thinking_effort": self.thinking_effort_var.get().strip(),
        }
        if save_settings:
            for key, value in payload.items():
                save_settings(key, value)
            self.status_label.configure(text="Claude settings saved.")
        else:
            with open("claude_settings.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4)
            self.status_label.configure(text="Saved to claude_settings.json.")
