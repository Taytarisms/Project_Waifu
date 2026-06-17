import threading
import sys
import customtkinter as ctk
from pathlib import Path
from files.ui import theme

try:
    from files.system_setup.settings import get_settings, save_settings
except Exception:
    get_settings = None
    save_settings = None

try:
    from files.system_setup.model_checker import (
        scan_local_models,
        detect_vram,
        get_gpu_name,
        MODELS_DIR,
    )
    _checker_available = True
except Exception:
    _checker_available = False

_COMPAT_COLOURS = {
    "compatible":    "#238636",   # green
    "tight":         "#b08800",   # amber
    "non-compliant": "#b62324",   # red
    "unknown":       "#4a4a4a",   # grey
}

_COMPAT_LABELS = {
    "compatible":    "✔  Compatible",
    "tight":         "⚠  Tight",
    "non-compliant": "✖  Non-compliant",
    "unknown":       "?  Unknown",
}


class LocalSettingsPage(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self,
            text="Local Model Settings",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=theme.TEXT,
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 4))

        # GPU info line
        if _checker_available:
            try:
                vram  = detect_vram()
                gname = get_gpu_name()
                gpu_text = f"{gname}  •  {vram:.1f} GB VRAM"
            except Exception:
                gpu_text = "GPU info unavailable"
        else:
            gpu_text = "The model check module is not installed, check your install."

        ctk.CTkLabel(
            self,
            text=gpu_text,
            text_color=theme.MUTED_TEXT,
            font=ctk.CTkFont(size=12),
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 14))

        row = 2
        self._section("Installed Model", row)
        row += 1

        model_frame = ctk.CTkFrame(self, fg_color="transparent")
        model_frame.grid(row=row, column=0, sticky="ew", padx=18, pady=(0, 6))
        model_frame.grid_columnconfigure(0, weight=1)
        row += 1

        # Dropdown of installed models (filenames)
        self._local_models: list[dict] = []
        self._model_names:  list[str]  = ["(scanning…)"]

        saved_model = self._setting("local_model_filename", "")
        self.model_filename_var = ctk.StringVar(value=saved_model or "(scanning…)")

        self._model_combo = ctk.CTkComboBox(
            model_frame,
            variable=self.model_filename_var,
            values=self._model_names,
            width=360,
            command=self._on_model_selected,
        )
        self._model_combo.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        ctk.CTkButton(
            model_frame,
            text="⟳ Refresh",
            width=90,
            fg_color="#21262d",
            hover_color="#30363d",
            border_color=theme.BORDER if hasattr(theme, "BORDER") else "#30363d",
            border_width=1,
            command=self._refresh_models,
        ).grid(row=0, column=1)

        # Compatibility along with vision badge row
        badge_row = ctk.CTkFrame(self, fg_color="transparent")
        badge_row.grid(row=row, column=0, sticky="w", padx=18, pady=(2, 10))
        row += 1

        self._compat_badge = ctk.CTkLabel(
            badge_row,
            text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#4a4a4a",
        )
        self._compat_badge.pack(side="left", padx=(0, 12))

        self._vision_badge = ctk.CTkLabel(
            badge_row,
            text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#58a6ff",
        )
        self._vision_badge.pack(side="left")
        hf_row = ctk.CTkFrame(self, fg_color="transparent")
        hf_row.grid(row=row, column=0, sticky="w", padx=18, pady=(0, 14))
        row += 1

        ctk.CTkButton(
            hf_row,
            text="🤗  Browse HuggingFace",
            fg_color="#21262d",
            hover_color="#30363d",
            border_color=theme.BORDER if hasattr(theme, "BORDER") else "#30363d",
            border_width=1,
            command=self._open_hf_browser,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(
            hf_row,
            text="Find and download GGUF models",
            text_color=theme.MUTED_TEXT,
            font=ctk.CTkFont(size=12),
        ).pack(side="left")

        self.family_var = ctk.StringVar(
            value=self._setting("local_model_family", "auto")
        )

        self._combo_row(
            "Model Family / Template",
            self.family_var,
            [
                "auto",
                "gemma3",
                "gemma4",
                "qwen35",
                "qwen25vl",
                "qwen3vl",
                "llava15",
                "llava16",
                "moondream",
                "nanollava",
                "llama3vis",
                "minicpm26",
                "minicpm45",
                "glm41v",
                "glm46v",
                "lfm2vl",
            ],
            row,
        )
        row += 1

        self.enable_thinking_var = ctk.BooleanVar(
            value=bool(self._setting("local_enable_thinking", False))
        )

        ctk.CTkCheckBox(
            self,
            text="Enable Thinking / Reasoning Mode",
            variable=self.enable_thinking_var,
            text_color=theme.TEXT,
            fg_color="#238636",
        ).grid(row=row, column=0, sticky="w", padx=18, pady=8)
        row += 1

        self.mmproj_filename_var = ctk.StringVar(
            value=self._setting("local_mmproj_filename", "mmproj-BF16.gguf")
        )
        self._entry_row("MMProj Filename (vision)", self.mmproj_filename_var, row)
        row += 1

        self.system_prompt_filename_var = ctk.StringVar(
            value=self._setting("local_system_prompt_filename", "system_message_local.txt")
        )
        self._entry_row("System Prompt File", self.system_prompt_filename_var, row)
        row += 1

        self._section("Runtime", row); row += 1

        self.n_ctx_var        = ctk.DoubleVar(value=float(self._setting("local_n_ctx",        16384)))
        self.n_batch_var      = ctk.DoubleVar(value=max(128, float(self._setting("local_n_batch",      1024))))
        self.n_threads_var    = ctk.DoubleVar(value=max(1, float(self._setting("local_n_threads",    12))))
        self.n_gpu_layers_var = ctk.DoubleVar(value=float(self._setting("local_n_gpu_layers", -1)))
        self.main_gpu_var     = ctk.DoubleVar(value=float(self._setting("local_main_gpu",     0)))
        self.seed_var         = ctk.DoubleVar(value=float(self._setting("local_seed",         -1)))

        self._slider_row("Context Size",        self.n_ctx_var,        2048,   131072, row, is_int=True); row += 1
        self._slider_row("Batch Size",          self.n_batch_var,      128,    8192,   row, is_int=True); row += 1
        self._slider_row("Threads",             self.n_threads_var,    1,      32,     row, is_int=True); row += 1
        self._slider_row("GPU Layers (-1=all)", self.n_gpu_layers_var, -1,     200,    row, is_int=True); row += 1
        self._slider_row("Main GPU",            self.main_gpu_var,     0,      4,      row, is_int=True); row += 1
        self._slider_row("Seed (-1=random)",    self.seed_var,         -1,     999999, row, is_int=True); row += 1

        self._section("Generation", row); row += 1

        self.temperature_var   = ctk.DoubleVar(value=float(self._setting("local_temperature",   0.5)))
        self.max_tokens_var    = ctk.DoubleVar(value=float(self._setting("local_max_tokens",    256)))
        self.top_k_var         = ctk.DoubleVar(value=float(self._setting("local_top_k",         40)))
        self.top_p_var         = ctk.DoubleVar(value=float(self._setting("local_top_p",         0.95)))
        self.min_p_var         = ctk.DoubleVar(value=float(self._setting("local_min_p",         0.05)))
        self.repeat_penalty_var = ctk.DoubleVar(value=float(self._setting("local_repeat_penalty", 1.1)))

        self.force_new_context_var = ctk.BooleanVar(
            value=bool(self._setting("local_force_new_context", False))
        )

        self._slider_row("Temperature",    self.temperature_var,    0.0, 2.0,  row);        row += 1
        self._slider_row("Max Tokens",     self.max_tokens_var,     64,  4096, row, is_int=True); row += 1
        self._slider_row("Top K",          self.top_k_var,          0,   200,  row, is_int=True); row += 1
        self._slider_row("Top P",          self.top_p_var,          0.0, 1.0,  row);        row += 1
        self._slider_row("Min P",          self.min_p_var,          0.0, 1.0,  row);        row += 1
        self._slider_row("Repeat Penalty", self.repeat_penalty_var, 0.8, 2.0,  row);        row += 1

        ctk.CTkCheckBox(
            self,
            text="Force New Context",
            variable=self.force_new_context_var,
            text_color=theme.TEXT,
            fg_color="#238636",
        ).grid(row=row, column=0, sticky="w", padx=18, pady=8)
        row += 1

        self._section("Vision", row); row += 1

        self.screen_max_dim_var          = ctk.DoubleVar(value=float(self._setting("local_screen_max_dim",         960)))
        self.screenshot_quality_var      = ctk.DoubleVar(value=float(self._setting("local_screenshot_quality",     82)))
        self.screenshot_subsampling_var  = ctk.StringVar(value=self._setting("local_screenshot_subsampling", "4:2:0"))

        self._slider_row("Screen Max Dimension",  self.screen_max_dim_var,     320, 2048, row, is_int=True); row += 1
        self._slider_row("Screenshot Quality",    self.screenshot_quality_var, 20,  100,  row, is_int=True); row += 1
        self._combo_row( "Screenshot Subsampling",self.screenshot_subsampling_var, ["4:2:0", "4:2:2", "4:4:4"], row); row += 1

        ctk.CTkButton(
            self,
            text="Save Local Settings",
            command=self.save,
        ).grid(row=row, column=0, sticky="w", padx=18, pady=(16, 8))
        row += 1

        self.status_label = ctk.CTkLabel(
            self,
            text="No changes saved yet.",
            text_color=theme.MUTED_TEXT,
        )
        self.status_label.grid(row=row, column=0, sticky="w", padx=18, pady=(0, 18))
        self._refresh_models()

    def _refresh_models(self):
        self._model_combo.configure(values=["(scanning…)"])
        self.model_filename_var.set("(scanning…)")
        threading.Thread(target=self._do_scan, daemon=True).start()


    def template_type_from_name(self, filename: str) -> str:
        name = (filename or "").lower()

        if "qwen3.5" in name or "qwen35" in name:
            return "qwen35"
        if "qwen3-vl" in name or "qwen3vl" in name:
            return "qwen3vl"
        if "qwen2.5-vl" in name or "qwen25vl" in name:
            return "qwen25vl"
        if "gemma-4" in name or "gemma4" in name:
            return "gemma4"
        if "gemma-3" in name or "gemma3" in name:
            return "gemma3"
        if "llava-1.6" in name:
            return "llava16"
        if "llava" in name:
            return "llava15"
        if "moondream" in name:
            return "moondream"
        if "minicpm" in name and "4.5" in name:
            return "minicpm45"
        if "minicpm" in name:
            return "minicpm26"

        return "auto"

    def _do_scan(self):
        if not _checker_available:
            self.after(0, lambda: self._model_combo.configure(values=["model_checker unavailable"]))
            return

        models = scan_local_models()
        self._local_models = models

        if models:
            names = [m["filename"] for m in models]
        else:
            names = ["No models found — browse HuggingFace"]

        def _update():
            if not self.winfo_exists():
                return
            self._model_combo.configure(values=names)
            saved = self._setting("local_model_filename", "")
            if saved and saved in names:
                self.model_filename_var.set(saved)
                self._on_model_selected(saved)
            else:
                self.model_filename_var.set(names[0])
                if models:
                    self._on_model_selected(names[0])
                else:
                    self._compat_badge.configure(text="", text_color="#4a4a4a")
                    self._vision_badge.configure(text="")

        self.after(0, _update)

    def _on_model_selected(self, filename: str):
        match = next((m for m in self._local_models if m["filename"] == filename), None)
        if hasattr(self, "family_var"):
            inferred = self.template_type_from_name(filename)
            if inferred != "auto":
                self.family_var.set(inferred)
        if not match:
            self._compat_badge.configure(text="", text_color="#4a4a4a")
            self._vision_badge.configure(text="")
            return

        compat = match.get("compatibility", "unknown")
        colour = _COMPAT_COLOURS.get(compat, "#4a4a4a")
        label  = _COMPAT_LABELS.get(compat, "? Unknown")

        self._compat_badge.configure(text=label, text_color=colour)

        if match.get("has_mmproj"):
            self._vision_badge.configure(text="👁  Supports Vision", text_color="#58a6ff")
        else:
            self._vision_badge.configure(text="")

    def _open_hf_browser(self):
        try:
            from files.ui.pages.hf_browser import HFModelBrowserPage
            widget = self
            app    = None
            for _ in range(10):
                widget = widget.master
                if hasattr(widget, "show_dynamic_page"):
                    app = widget
                    break
            if app:
                app.show_dynamic_page(HFModelBrowserPage)
            else:
                import webbrowser
                webbrowser.open("https://huggingface.co/models?library=gguf&sort=downloads")
        except Exception:
            import webbrowser
            webbrowser.open("https://huggingface.co/models?library=gguf&sort=downloads")

    def _setting(self, key, default):
        if not get_settings:
            return default
        value = get_settings(key)
        return default if value in (None, "") else value

    def _section(self, text, row):
        ctk.CTkLabel(
            self,
            text=text,
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=theme.TEXT,
        ).grid(row=row, column=0, sticky="w", padx=18, pady=(18, 6))

    def _entry_row(self, label, var, row):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=18, pady=6)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text=label, text_color=theme.TEXT, width=200, anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkEntry(frame, textvariable=var, width=360).grid(row=0, column=1, sticky="ew", padx=(10, 0))

    def _combo_row(self, label, var, values, row):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=18, pady=6)

        ctk.CTkLabel(frame, text=label, text_color=theme.TEXT, width=200, anchor="w").pack(side="left")
        ctk.CTkComboBox(frame, variable=var, values=values, width=180).pack(side="left", padx=10)

    def _slider_row(self, label, var, min_value, max_value, row, is_int=False):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=18, pady=6)
        frame.grid_columnconfigure(1, weight=1)

        value_label = ctk.CTkLabel(
            frame,
            text=self._format(label, var.get(), is_int),
            text_color=theme.TEXT,
            width=210,
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
            "local_model_filename":         self.model_filename_var.get().strip(),
            "local_model_family":           self.family_var.get().strip(),
            "local_enable_thinking":        bool(self.enable_thinking_var.get()),
            "local_mmproj_filename":        self.mmproj_filename_var.get().strip(),
            "local_system_prompt_filename": self.system_prompt_filename_var.get().strip(),
            "local_n_ctx":                  int(self.n_ctx_var.get()),
            "local_n_batch":                max(128, int(self.n_batch_var.get())),
            "local_n_threads":              max(1, int(self.n_threads_var.get())),
            "local_n_gpu_layers":           int(self.n_gpu_layers_var.get()),
            "local_main_gpu":               int(self.main_gpu_var.get()),
            "local_seed":                   int(self.seed_var.get()),
            "local_temperature":            float(self.temperature_var.get()),
            "local_max_tokens":             int(self.max_tokens_var.get()),
            "local_top_k":                  int(self.top_k_var.get()),
            "local_top_p":                  float(self.top_p_var.get()),
            "local_min_p":                  float(self.min_p_var.get()),
            "local_repeat_penalty":         float(self.repeat_penalty_var.get()),
            "local_force_new_context":      bool(self.force_new_context_var.get()),
            "local_screen_max_dim":         int(self.screen_max_dim_var.get()),
            "local_screenshot_quality":     int(self.screenshot_quality_var.get()),
            "local_screenshot_subsampling": self.screenshot_subsampling_var.get(),
        }

        if save_settings:
            for key, value in payload.items():
                save_settings(key, value)

        try:
            from files.llm.local_worker_client import reset_local_worker_if_running
            reset_local_worker_if_running()
        except Exception:
            pass

        module = sys.modules.get("files.llm.LocalLLM")
        reset_fn = getattr(module, "reset_local_model", None) if module else None
        if callable(reset_fn):
            try:
                reset_fn()
            except Exception:
                pass
        reload_fn = getattr(module, "reload_system_message", None) if module else None
        if callable(reload_fn):
            try:
                reload_fn(force=True)
            except Exception:
                pass

        self.status_label.configure(text="Local model settings saved.")
