import customtkinter as ctk
from files.ui import theme

from files.speech_to_text.stt_config import STTConfig
from files.speech_to_text.openai_whisper import OpenAIWhisperCore, OpenAIWhisperConfig
from files.speech_to_text.google_stt import GoogleSTTCore, GoogleSTTConfig
from files.speech_to_text import stt_controls

try:
    import sounddevice as sd
except Exception:
    sd = None

try:
    from files.system_setup.settings import get_settings, save_settings as save_setting
except Exception:
    get_settings = None
    save_setting = None

try:
    from files.main_loop.main_loop import message_queue
except Exception as e:
    message_queue = None
    

class STTPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app
        self.core = None
        self._capturing_ptt_key = False
        self._ptt_capture_modifiers = set()
        stt_controls.bind_push_to_talk(app)
        app.bind_all("<KeyPress>", self._capture_ptt_key, add="+")
        app.bind_all("<KeyRelease>", self._capture_ptt_key_release, add="+")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=20)

        left = ctk.CTkScrollableFrame(body, fg_color=theme.CARD_BG, corner_radius=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        right_outer = ctk.CTkFrame(
            body,
            fg_color=theme.CARD_BG,
            corner_radius=10,
            width=360,
        )
        right_outer.pack(side="right", fill="y", padx=(10, 0))
        right_outer.pack_propagate(False)

        right = ctk.CTkScrollableFrame(
            right_outer,
            fg_color="transparent",
            corner_radius=10,
        )
        right.pack(fill="both", expand=True)

        self._build_settings(left)
        self._build_runtime(right)

    def _build_settings(self, parent):
        ctk.CTkLabel(
            parent,
            text="Speech-to-Text Settings",
            font=ctk.CTkFont(size=28, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=18, pady=(18, 8))

        self.engine_var = ctk.StringVar(value=self._setting("stt_engine", "Local Whisper"))
        self.wake_word_enabled_var = ctk.BooleanVar(value=bool(self._setting("stt_wake_word_enabled", False)))
        self.wake_word_var = ctk.StringVar(value=str(self._setting("stt_wake_word", "")))

        self._combo_row(
            parent,
            "STT Engine",
            self.engine_var,
            ["Local Whisper", "OpenAI Whisper", "Google"],
            command=lambda value: self._save_single_setting("stt_engine", value),
        )
        self.device_index_var = ctk.StringVar(value=str(self._setting("stt_device_index", "")))
        self.mode_var = ctk.StringVar(value=self._setting("stt_mode", "Hot Mic"))
        self.ptt_key_var = ctk.StringVar(value=str(self._setting("stt_ptt_key", "space")))
        self.model_var = ctk.StringVar(value=self._setting("stt_model_name", "tiny"))
        self.device_var = ctk.StringVar(value=self._setting("stt_device", "cuda"))
        self.compute_var = ctk.StringVar(value=self._setting("stt_compute_type", "float16"))
        self.language_var = ctk.StringVar(value=self._setting("stt_language", "en"))
        self.google_timeout_var = ctk.DoubleVar(value=float(self._setting("stt_google_timeout", 0.5)))
        self.google_phrase_limit_var = ctk.DoubleVar(value=float(self._setting("stt_google_phrase_time_limit", 8.0)))
        self.google_adjust_ambient_var = ctk.BooleanVar(value=bool(self._setting("stt_google_adjust_ambient", True)))
        self.google_energy_var = ctk.StringVar(value=str(self._setting("stt_google_energy_threshold", "")))

        self.vad_var = ctk.DoubleVar(value=float(self._setting("stt_vad_aggressiveness", 1)))
        self.pre_speech_var = ctk.DoubleVar(value=float(self._setting("stt_pre_speech_ms", 1000)))
        self.post_speech_var = ctk.DoubleVar(value=float(self._setting("stt_post_speech_ms", 600)))
        self.segment_max_var = ctk.DoubleVar(value=float(self._setting("stt_segment_max_ms", 15000)))
        self.min_length_var = ctk.DoubleVar(value=float(self._setting("stt_min_length_ms", 1000)))
        self.partial_var = ctk.BooleanVar(value=bool(self._setting("stt_enable_partials", True)))

        self._entry_row(parent, "Input Device Index", self.device_index_var)
        self._combo_row(
            parent,
            "STT Mode",
            self.mode_var,
            ["Hot Mic", "Push To Talk"],
            command=lambda value: self._save_single_setting("stt_mode", value),
        )
        self._ptt_key_row(parent)
        self._combo_row(
            parent,
            "Model",
            self.model_var,
            ["tiny", "base", "small", "medium", "large-v3"],
            command=lambda value: self._save_single_setting("stt_model_name", value),
        )
        self._combo_row(
            parent,
            "Device",
            self.device_var,
            ["cuda", "cpu"],
            command=lambda value: self._save_single_setting("stt_device", value),
        )
        self._combo_row(
            parent,
            "Compute Type",
            self.compute_var,
            ["float16", "int8", "float32"],
            command=lambda value: self._save_single_setting("stt_compute_type", value),
        )
        self._entry_row(parent, "Language", self.language_var)
        self._entry_row(parent, "Wake Word", self.wake_word_var)
        self._entry_row(parent, "Google Energy", self.google_energy_var)

        self._slider_row(parent, "VAD Aggressiveness", self.vad_var, 0, 3, is_int=True)
        self._slider_row(parent, "Pre Speech ms", self.pre_speech_var, 0, 2000, is_int=True)
        self._slider_row(parent, "Post Speech ms", self.post_speech_var, 100, 2000, is_int=True)
        self._slider_row(parent, "Segment Max ms", self.segment_max_var, 3000, 30000, is_int=True)
        self._slider_row(parent, "Min Length ms", self.min_length_var, 200, 3000, is_int=True)
        self._slider_row(parent, "Google Timeout", self.google_timeout_var, 0.1, 3.0)
        self._slider_row(parent, "Google Phrase Limit", self.google_phrase_limit_var, 1.0, 20.0)

        ctk.CTkCheckBox(
            parent,
            text="Enable Partials",
            variable=self.partial_var,
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=18, pady=8)

        ctk.CTkCheckBox(
            parent,
            text="Require Wake Word",
            variable=self.wake_word_enabled_var,
            text_color=theme.TEXT,
            command=lambda: self._save_single_setting("stt_wake_word_enabled", bool(self.wake_word_enabled_var.get())),
        ).pack(anchor="w", padx=18, pady=8)

        ctk.CTkCheckBox(
            parent,
            text="Google Ambient Calibration",
            variable=self.google_adjust_ambient_var,
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=18, pady=8)

        ctk.CTkButton(
            parent,
            text="Save STT Settings",
            command=self.save_settings,
        ).pack(anchor="w", padx=18, pady=(16, 8))

    def _build_runtime(self, parent):
        ctk.CTkLabel(
            parent,
            text="STT Runtime",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 8))

        self.status_label = ctk.CTkLabel(
            parent,
            text="Stopped.",
            text_color=theme.MUTED_TEXT,
            wraplength=280,
            justify="left",
        )
        self.status_label.pack(anchor="w", padx=16, pady=8)

        ctk.CTkButton(parent, text="Start STT", command=self.start_stt).pack(fill="x", padx=16, pady=4)
        ctk.CTkButton(parent, text="Stop STT", fg_color=theme.DANGER, command=self.stop_stt).pack(fill="x", padx=16, pady=4)
        ctk.CTkButton(parent, text="Pause", command=self.pause_stt).pack(fill="x", padx=16, pady=4)
        ctk.CTkButton(parent, text="Resume", command=self.resume_stt).pack(fill="x", padx=16, pady=4)

        ctk.CTkButton(
            parent,
            text="List Input Devices",
            command=self.list_devices,
        ).pack(fill="x", padx=16, pady=(18, 4))

        self.preview_box = ctk.CTkTextbox(
            parent,
            height=220,
            fg_color="#0e141b",
            text_color=theme.TEXT,
            wrap="word",
        )
        self.preview_box.pack(fill="x", padx=16, pady=(10, 16))

    def _setting(self, key, default):
        if not get_settings:
            return default
        value = get_settings(key)
        return default if value in ("", None) else value

    def _entry_row(self, parent, label, var):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=18, pady=6)
        ctk.CTkLabel(frame, text=label, text_color=theme.TEXT, width=170, anchor="w").pack(side="left")
        ctk.CTkEntry(frame, textvariable=var).pack(side="left", fill="x", expand=True, padx=(10, 0))

    def _combo_row(self, parent, label, var, values, command=None):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=18, pady=6)
        ctk.CTkLabel(frame, text=label, text_color=theme.TEXT, width=170, anchor="w").pack(side="left")
        ctk.CTkComboBox(
            frame,
            variable=var,
            values=values,
            width=220,
            command=command,
        ).pack(side="left", padx=(10, 0))

    def _slider_row(self, parent, label, var, min_value, max_value, is_int=False):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=18, pady=6)
        value_label = ctk.CTkLabel(
            frame,
            text=self._format(label, var.get(), is_int),
            text_color=theme.TEXT,
            width=190,
            anchor="w",
        )
        value_label.pack(side="left")
        ctk.CTkSlider(
            frame,
            from_=min_value,
            to=max_value,
            variable=var,
            command=lambda v: value_label.configure(text=self._format(label, v, is_int)),
        ).pack(side="left", fill="x", expand=True, padx=(10, 0))

    def _format(self, label, value, is_int=False):
        return f"{label}: {int(float(value))}" if is_int else f"{label}: {float(value):.2f}"

    def save_settings(self):
        if not save_setting:
            return

        values = {
            "stt_device_index": self.device_index_var.get().strip(),
            "stt_mode": self.mode_var.get().strip(),
            "stt_ptt_key": stt_controls.normalize_ptt_combo(self.ptt_key_var.get()),
            "stt_wake_word_enabled": bool(self.wake_word_enabled_var.get()),
            "stt_wake_word": self.wake_word_var.get().strip(),
            "stt_model_name": self.model_var.get().strip(),
            "stt_device": self.device_var.get().strip(),
            "stt_compute_type": self.compute_var.get().strip(),
            "stt_language": self.language_var.get().strip(),
            "stt_vad_aggressiveness": int(self.vad_var.get()),
            "stt_pre_speech_ms": int(self.pre_speech_var.get()),
            "stt_post_speech_ms": int(self.post_speech_var.get()),
            "stt_segment_max_ms": int(self.segment_max_var.get()),
            "stt_min_length_ms": int(self.min_length_var.get()),
            "stt_enable_partials": bool(self.partial_var.get()),
            "stt_engine": self.engine_var.get().strip(),
            "stt_google_timeout": float(self.google_timeout_var.get()),
            "stt_google_phrase_time_limit": float(self.google_phrase_limit_var.get()),
            "stt_google_adjust_ambient": bool(self.google_adjust_ambient_var.get()),
            "stt_google_energy_threshold": self.google_energy_var.get().strip(),
        }

        for key, value in values.items():
            save_setting(key, value)

        if hasattr(self, "status_label"):
            self.status_label.configure(text="STT settings saved.")

    def _ptt_key_row(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=18, pady=6)
        ctk.CTkLabel(
            frame,
            text="Push-To-Talk Key",
            text_color=theme.TEXT,
            width=170,
            anchor="w",
        ).pack(side="left")
        ctk.CTkEntry(
            frame,
            textvariable=self.ptt_key_var,
            state="readonly",
        ).pack(side="left", fill="x", expand=True, padx=(10, 8))
        ctk.CTkButton(
            frame,
            text="Set",
            width=70,
            command=self.start_ptt_key_capture,
        ).pack(side="left")

    def start_ptt_key_capture(self):
        self._capturing_ptt_key = True
        self._ptt_capture_modifiers.clear()
        if hasattr(self, "status_label"):
            self.status_label.configure(text="Press the key or key combo to use for Push-To-Talk.")

    def _capture_ptt_key(self, event):
        if not self._capturing_ptt_key:
            return
        key = self._normalize_capture_key(getattr(event, "keysym", ""))
        if not key:
            return
        if key in {"ctrl", "shift", "alt"}:
            self._ptt_capture_modifiers.add(key)
            if hasattr(self, "status_label"):
                current = "+".join(self._ordered_capture_modifiers())
                self.status_label.configure(text=f"Modifier held: {current}. Press the main key.")
            return "break"

        self._save_ptt_capture(key)
        return "break"

    def _capture_ptt_key_release(self, event):
        if not self._capturing_ptt_key:
            return
        key = self._normalize_capture_key(getattr(event, "keysym", ""))
        if key not in {"ctrl", "shift", "alt"}:
            return
        self._ptt_capture_modifiers.discard(key)
        if not self._ptt_capture_modifiers:
            self._save_ptt_capture(key)
        return "break"

    def _normalize_capture_key(self, key):
        raw = str(key or "").strip()
        if not raw:
            return ""
        return stt_controls.normalize_ptt_combo(raw)

    def _ordered_capture_modifiers(self):
        return [m for m in ("ctrl", "shift", "alt") if m in self._ptt_capture_modifiers]

    def _save_ptt_capture(self, key):
        combo = stt_controls.normalize_ptt_combo(
            "+".join(self._ordered_capture_modifiers() + [key])
        )
        self.ptt_key_var.set(combo)
        self._capturing_ptt_key = False
        self._ptt_capture_modifiers.clear()
        try:
            (self.app or self).focus_set()
        except Exception:
            pass
        self._save_single_setting("stt_ptt_key", combo)
        if hasattr(self, "status_label"):
            self.status_label.configure(text=f"Push-To-Talk key saved: {combo}")

    def _save_single_setting(self, key, value):
        if save_setting:
            save_setting(key, value)
        if hasattr(self, "status_label"):
            self.status_label.configure(text=f"Saved {key}.")

    def build_config(self):
        return STTConfig(
            device=self.device_var.get().strip(),
            model_name=self.model_var.get().strip(),
            compute_type=self.compute_var.get().strip(),
            vad_aggressiveness=int(self.vad_var.get()),
            pre_speech_ms=int(self.pre_speech_var.get()),
            post_speech_ms=int(self.post_speech_var.get()),
            segment_max_ms=int(self.segment_max_var.get()),
            min_length_ms=int(self.min_length_var.get()),
            enable_partials=bool(self.partial_var.get()),
            language=self.language_var.get().strip() or None,
        )

    def input_device_index(self):
        raw = self.device_index_var.get().strip()
        return None if raw == "" else int(raw)

    def start_stt(self):
        self.save_settings()
        ok = stt_controls.start_stt(on_text=self._console_stt_callback)

        self.status_label.configure(
            text=stt_controls.status_text() if ok else "Failed to start STT."
        )

    def stop_stt(self):
        stt_controls.stop_stt()
        self.status_label.configure(text=stt_controls.status_text())


    def pause_stt(self):
        stt_controls.pause_stt()
        self.status_label.configure(text=stt_controls.status_text())


    def resume_stt(self):
        stt_controls.resume_stt()
        self.status_label.configure(text=stt_controls.status_text())

    def _console_stt_callback(self, text, is_partial):
        console = getattr(self.app, "pages", {}).get("Console") if self.app else None
        if console is not None and hasattr(console, "on_stt_text"):
            console.on_stt_text(text, is_partial=is_partial)
            return
        stt_controls.handle_stt_text(text, is_partial=is_partial)

    def list_devices(self):
        if not sd:
            self.preview_box.insert("end", "sounddevice is unavailable.\n")
            return

        try:
            devices = sd.query_devices()
            self.preview_box.insert("end", "\nInput devices:\n")
            for i, dev in enumerate(devices):
                if dev.get("max_input_channels", 0) > 0:
                    self.preview_box.insert("end", f"{i}: {dev['name']}\n")
            self.preview_box.see("end")
        except Exception as e:
            self.preview_box.insert("end", f"Failed to list devices: {e}\n")
