import customtkinter as ctk
from files.ui import theme
from files.ui.components.header import SteamHeader

try:
    from files.system_setup.initializer import change_llm
except Exception:
    change_llm = None


class LLMProviderPage(ctk.CTkFrame):
    def __init__(self, master, app, provider_name):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app
        self.provider_name = provider_name

        SteamHeader(
            self,
            title=provider_name,
            subtitle=f"Configure {provider_name} as a language model provider.",
            action_text=f"Use {provider_name}",
            action_command=self.activate_provider,
        ).pack(fill="x")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=20)

        left = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        right = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=10, width=300)
        right.pack(side="right", fill="y", padx=(10, 0))
        right.pack_propagate(False)

        ctk.CTkLabel(
            left,
            text="Settings",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=18, pady=(18, 10))

        self.model_var = ctk.StringVar(value="Select model")
        ctk.CTkComboBox(
            left,
            variable=self.model_var,
            values=self.get_model_options(provider_name),
            width=280,
        ).pack(anchor="w", padx=18, pady=8)

        self.temp_var = ctk.DoubleVar(value=0.7)
        self._slider_row(left, "Temperature", self.temp_var, 0.0, 2.0)

        self.max_tokens_var = ctk.IntVar(value=512)
        self._slider_row(left, "Max Tokens", self.max_tokens_var, 64, 4096)

        ctk.CTkButton(
            left,
            text="Save Provider Settings",
            command=self.save_settings,
        ).pack(anchor="w", padx=18, pady=20)

        ctk.CTkLabel(
            right,
            text="Provider Summary",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 8))

        self.status_label = ctk.CTkLabel(
            right,
            text="Not active yet.",
            text_color=theme.MUTED_TEXT,
            wraplength=250,
            justify="left",
        )
        self.status_label.pack(anchor="w", padx=16, pady=8)

        ctk.CTkButton(
            right,
            text="Back to LLM Library",
            command=lambda: self.app.show_page("LLM"),
            fg_color="gray30",
        ).pack(side="bottom", fill="x", padx=16, pady=16)

    def _slider_row(self, master, label, var, min_value, max_value):
        row = ctk.CTkFrame(master, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=8)

        value_label = ctk.CTkLabel(
            row,
            text=f"{label}: {var.get()}",
            text_color=theme.TEXT,
            width=160,
            anchor="w",
        )
        value_label.pack(side="left")

        slider = ctk.CTkSlider(
            row,
            from_=min_value,
            to=max_value,
            variable=var,
            command=lambda v: value_label.configure(
                text=f"{label}: {int(float(v)) if isinstance(var, ctk.IntVar) else float(v):.2f}"
            ),
        )
        slider.pack(side="left", fill="x", expand=True, padx=10)

    def get_model_options(self, provider):
        return {
            "OpenAI": ["gpt-4.1", "gpt-4.1-mini", "fine-tuned model"],
            "NovelAI": ["kayra", "clio", "custom"],
            "Local": ["local gguf", "gemma", "qwen"],
            "Personal": ["personal"],
        }.get(provider, ["default"])

    def activate_provider(self):
        if change_llm:
            change_llm(self.provider_name)
        self.status_label.configure(text=f"{self.provider_name} selected as active LLM.")

    def save_settings(self):
        self.status_label.configure(
            text=(
                f"Saved draft settings:\n"
                f"Model: {self.model_var.get()}\n"
                f"Temperature: {self.temp_var.get():.2f}\n"
                f"Max Tokens: {self.max_tokens_var.get()}"
            )
        )