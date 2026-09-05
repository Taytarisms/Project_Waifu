import customtkinter as ctk

from files.ui.components.scrolling import AppScrollableFrame
from files.speech_to_text import stt_controls
from files.speech_to_text.audio_devices import parse_preferred_patterns, resolve_input_device
from files.system_setup.settings import get_auth, get_settings, save_auth, save_settings
from files.ui.components.auth_dialog import AuthTokenDialog
from files.ui import theme


class STTPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app
        self._capturing_ptt_key = False
        self._ptt_capture_modifiers: set[str] = set()
        self._devices = []
        self._device_by_label = {}
        self._provider_frames = {}
        self._status_after_id = None
        self._page_active = False

        stt_controls.bind_push_to_talk(app)
        app.bind_all("<KeyPress>", self._capture_ptt_key, add="+")
        app.bind_all("<KeyRelease>", self._capture_ptt_key_release, add="+")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=16)

        left = AppScrollableFrame(body, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=10, width=350)
        right.pack(side="right", fill="y", padx=(8, 0))
        right.pack_propagate(False)

        self._build_settings(left)
        self._build_runtime(right)
        self.refresh_devices()
        self._show_provider_settings(self.engine_var.get())

    def _setting(self, key, default):
        value = get_settings(key)
        return default if value in ("", None) else value

    @staticmethod
    def _card(parent, title: str, description: str = ""):
        card = ctk.CTkFrame(parent, fg_color=theme.CARD_BG, corner_radius=10)
        card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            card,
            text=title,
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(14, 2))
        if description:
            ctk.CTkLabel(
                card,
                text=description,
                text_color=theme.MUTED_TEXT,
                wraplength=700,
                justify="left",
            ).pack(anchor="w", padx=16, pady=(0, 8))
        return card

    def _build_settings(self, parent):
        ctk.CTkLabel(
            parent,
            text="Speech-to-Text",
            font=ctk.CTkFont(size=28, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", pady=(2, 12))

        self.engine_var = ctk.StringVar(value=self._setting("stt_engine", "Local Whisper"))
        if self.engine_var.get() == "OpenAI Whisper":
            self.engine_var.set("OpenAI Transcription")
        self.mode_var = ctk.StringVar(value=self._setting("stt_mode", "Hot Mic"))
        self.ptt_key_var = ctk.StringVar(value=str(self._setting("stt_ptt_key", "space")))
        self.language_var = ctk.StringVar(value=str(self._setting("stt_language", "en")))
        self.wake_word_enabled_var = ctk.BooleanVar(
            value=bool(self._setting("stt_wake_word_enabled", False))
        )
        self.wake_word_var = ctk.StringVar(value=str(self._setting("stt_wake_word", "")))
        self.pause_playback_var = ctk.BooleanVar(
            value=bool(self._setting("stt_pause_during_playback", True))
        )

        common = self._card(
            parent,
            "Conversation Input",
            "Choose an engine and decide when captured speech should become a conversation turn.",
        )
        self._combo_row(
            common,
            "STT Engine",
            self.engine_var,
            ["Local Whisper", "OpenAI Transcription", "Google"],
            command=self._engine_changed,
        )
        self._combo_row(
            common,
            "Capture Mode",
            self.mode_var,
            ["Hot Mic", "Push To Talk"],
            command=lambda value: self._save_single("stt_mode", value),
        )
        self._ptt_key_row(common)
        self._entry_row(common, "Language", self.language_var)
        self._entry_row(common, "Wake Word", self.wake_word_var)
        ctk.CTkCheckBox(
            common,
            text="Require wake word at the start of each final transcript",
            variable=self.wake_word_enabled_var,
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=7)
        ctk.CTkCheckBox(
            common,
            text="Pause hot-mic capture while companion audio is playing",
            variable=self.pause_playback_var,
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(7, 14))

        self.device_mode_var = ctk.StringVar(
            value=str(self._setting("stt_device_selection_mode", "automatic")).title()
        )
        self.device_choice_var = ctk.StringVar(value="Scanning input devices…")
        device = self._card(
            parent,
            "Input Device",
            "Automatic selection matches active input endpoints by saved identity and preferred name patterns. Device indices are resolved fresh each session.",
        )
        self._combo_row(
            device,
            "Selection",
            self.device_mode_var,
            ["Automatic", "Manual"],
            command=self._device_mode_changed,
        )
        row = ctk.CTkFrame(device, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=6)
        ctk.CTkLabel(row, text="Active Input", width=160, anchor="w", text_color=theme.TEXT).pack(side="left")
        self.device_combo = ctk.CTkComboBox(
            row,
            variable=self.device_choice_var,
            values=["Scanning input devices…"],
            command=self._manual_device_selected,
        )
        self.device_combo.pack(side="left", fill="x", expand=True, padx=(10, 8))
        ctk.CTkButton(row, text="Rescan", width=80, command=self.refresh_devices).pack(side="left")

        ctk.CTkLabel(
            device,
            text="Preferred patterns (one per line)",
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(8, 2))
        self.device_patterns_box = ctk.CTkTextbox(device, height=74)
        self.device_patterns_box.pack(fill="x", padx=16, pady=(0, 6))
        self.device_patterns_box.insert(
            "0.0",
            str(self._setting("stt_device_preferred_patterns", "CABLE Output\nVB-Audio")),
        )
        self.device_resolution_label = ctk.CTkLabel(
            device,
            text="",
            text_color=theme.MUTED_TEXT,
            wraplength=700,
            justify="left",
        )
        self.device_resolution_label.pack(anchor="w", padx=16, pady=(2, 14))

        self.vad_var = ctk.DoubleVar(value=float(self._setting("stt_vad_aggressiveness", 1)))
        self.pre_speech_var = ctk.DoubleVar(value=float(self._setting("stt_pre_speech_ms", 1000)))
        self.post_speech_var = ctk.DoubleVar(value=float(self._setting("stt_post_speech_ms", 600)))
        self.segment_max_var = ctk.DoubleVar(value=float(self._setting("stt_segment_max_ms", 15000)))
        self.min_length_var = ctk.DoubleVar(value=float(self._setting("stt_min_length_ms", 1000)))
        utterance = self._card(
            parent,
            "Utterance Detection",
            "These shared settings define speech boundaries for every engine in Hot Mic mode.",
        )
        self._slider_row(utterance, "VAD Aggressiveness", self.vad_var, 0, 3, integer=True)
        self._slider_row(utterance, "Pre-roll ms", self.pre_speech_var, 0, 2000, integer=True)
        self._slider_row(utterance, "End Silence ms", self.post_speech_var, 100, 2000, integer=True)
        self._slider_row(utterance, "Maximum Utterance ms", self.segment_max_var, 3000, 30000, integer=True)
        self._slider_row(utterance, "Minimum Voiced ms", self.min_length_var, 100, 3000, integer=True)

        self._provider_container = ctk.CTkFrame(parent, fg_color="transparent")
        self._provider_container.pack(fill="x")
        self._build_local_settings()
        self._build_openai_settings()
        self._build_google_settings()

        ctk.CTkButton(
            parent,
            text="Save STT Settings",
            command=self.save_settings,
            height=38,
        ).pack(anchor="w", pady=(4, 18))

    def _build_local_settings(self):
        frame = self._card(
            self._provider_container,
            "Local Whisper",
            "Runs locally through faster-whisper. Rolling partials are generated from the current utterance while VAD is active.",
        )
        self._provider_frames["Local Whisper"] = frame
        self.model_var = ctk.StringVar(value=str(self._setting("stt_model_name", "tiny")))
        self.local_device_var = ctk.StringVar(value=str(self._setting("stt_device", "cuda")))
        self.compute_var = ctk.StringVar(value=str(self._setting("stt_compute_type", "float16")))
        self.beam_var = ctk.DoubleVar(value=float(self._setting("stt_beam_size", 1)))
        self.partial_var = ctk.BooleanVar(value=bool(self._setting("stt_enable_partials", True)))
        self.partial_interval_var = ctk.DoubleVar(
            value=float(self._setting("stt_partial_interval_ms", 500))
        )
        self._combo_row(frame, "Model", self.model_var, ["tiny", "base", "small", "medium", "large-v3"])
        self._combo_row(frame, "Inference Device", self.local_device_var, ["cuda", "cpu"])
        self._combo_row(frame, "Compute Type", self.compute_var, ["float16", "int8", "float32"])
        self._slider_row(frame, "Beam Size", self.beam_var, 1, 5, integer=True)
        self._slider_row(frame, "Partial Interval ms", self.partial_interval_var, 250, 2000, integer=True)
        ctk.CTkCheckBox(
            frame,
            text="Enable rolling partial transcripts",
            variable=self.partial_var,
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(7, 14))

    def _build_openai_settings(self):
        frame = self._card(
            self._provider_container,
            "OpenAI Transcription",
            "Completed utterances are uploaded through the Audio Transcription API. An OpenAI API key, internet connection, and available API quota are required.",
        )
        self._provider_frames["OpenAI Transcription"] = frame
        self.openai_model_var = ctk.StringVar(
            value=str(self._setting("stt_openai_model", "gpt-4o-transcribe"))
        )
        self._combo_row(
            frame,
            "Model",
            self.openai_model_var,
            ["gpt-4o-transcribe", "gpt-4o-mini-transcribe"],
        )
        has_key = bool(get_auth("openai", "token"))
        self.openai_auth_status_label = ctk.CTkLabel(
            frame,
            text="OpenAI API key saved." if has_key else "No OpenAI API key saved.",
            text_color=theme.SUCCESS if has_key else theme.MUTED_TEXT,
        )
        self.openai_auth_status_label.pack(anchor="w", padx=16, pady=(8, 4))
        auth_buttons = ctk.CTkFrame(frame, fg_color="transparent")
        auth_buttons.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkButton(
            auth_buttons,
            text="Set STT API Key",
            command=self._prompt_openai_key,
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(
            auth_buttons,
            text="Clear Key",
            fg_color=theme.DANGER,
            command=self._clear_openai_key,
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))
        ctk.CTkLabel(
            frame,
            text=(
                "This shared OpenAI credential can also be used by OpenAI LLM/TTS, "
                "but saving it here never changes the active LLM provider. Audio "
                "leaves this computer only after an utterance completes."
            ),
            text_color=theme.MUTED_TEXT,
            wraplength=700,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(6, 14))

    def _prompt_openai_key(self):
        AuthTokenDialog(
            self,
            title="OpenAI Speech-to-Text API Key",
            label=(
                "Paste the OpenAI API key used for transcription. Saving it will "
                "not switch your active language-model provider."
            ),
            on_save=self._save_openai_key,
        )

    def _save_openai_key(self, token: str):
        save_auth("openai", "token", token)
        self.openai_auth_status_label.configure(
            text="OpenAI API key saved. Active LLM unchanged.",
            text_color=theme.SUCCESS,
        )

    def _clear_openai_key(self):
        save_auth("openai", "token", "")
        self.openai_auth_status_label.configure(
            text="OpenAI API key cleared.",
            text_color=theme.MUTED_TEXT,
        )

    def _build_google_settings(self):
        frame = self._card(
            self._provider_container,
            "Google Recognition",
            "Completed utterances use SpeechRecognition's Google adapter. Shared SoundDevice capture keeps device selection and PTT behavior consistent with the other engines.",
        )
        self._provider_frames["Google"] = frame
        ctk.CTkLabel(
            frame,
            text="Google recognition is best treated as a network fallback; it does not provide partial transcripts here.",
            text_color=theme.MUTED_TEXT,
            wraplength=700,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(6, 14))

    def _build_runtime(self, parent):
        ctk.CTkLabel(
            parent,
            text="STT Runtime",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 8))
        self.status_label = ctk.CTkLabel(
            parent,
            text=stt_controls.status_text(),
            text_color=theme.MUTED_TEXT,
            wraplength=310,
            justify="left",
        )
        self.status_label.pack(anchor="w", padx=16, pady=(0, 10))
        ctk.CTkButton(parent, text="Start STT", command=self.start_stt).pack(fill="x", padx=16, pady=4)
        ctk.CTkButton(parent, text="Stop STT", fg_color=theme.DANGER, command=self.stop_stt).pack(fill="x", padx=16, pady=4)
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=4)
        ctk.CTkButton(row, text="Pause", command=self.pause_stt).pack(side="left", fill="x", expand=True, padx=(0, 3))
        ctk.CTkButton(row, text="Resume", command=self.resume_stt).pack(side="left", fill="x", expand=True, padx=(3, 0))

        ctk.CTkLabel(
            parent,
            text="Transcript Preview",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 6))
        self.preview_box = ctk.CTkTextbox(
            parent,
            fg_color="#0e141b",
            text_color=theme.TEXT,
            wrap="word",
        )
        self.preview_box.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    def _entry_row(self, parent, label, variable):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=6)
        ctk.CTkLabel(row, text=label, width=160, anchor="w", text_color=theme.TEXT).pack(side="left")
        ctk.CTkEntry(row, textvariable=variable).pack(side="left", fill="x", expand=True, padx=(10, 0))

    def _combo_row(self, parent, label, variable, values, command=None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=6)
        ctk.CTkLabel(row, text=label, width=160, anchor="w", text_color=theme.TEXT).pack(side="left")
        ctk.CTkComboBox(
            row,
            variable=variable,
            values=values,
            command=command,
        ).pack(side="left", fill="x", expand=True, padx=(10, 0))

    def _slider_row(self, parent, label, variable, minimum, maximum, *, integer=False):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=6)
        value_label = ctk.CTkLabel(row, width=205, anchor="w", text_color=theme.TEXT)
        value_label.pack(side="left")

        def display(value):
            shown = str(int(float(value))) if integer else f"{float(value):.2f}"
            value_label.configure(text=f"{label}: {shown}")

        slider = ctk.CTkSlider(row, from_=minimum, to=maximum, variable=variable, command=display)
        slider.pack(side="left", fill="x", expand=True, padx=(10, 0))
        display(variable.get())

    def _ptt_key_row(self, parent):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=6)
        ctk.CTkLabel(row, text="Push-To-Talk Key", width=160, anchor="w", text_color=theme.TEXT).pack(side="left")
        ctk.CTkEntry(row, textvariable=self.ptt_key_var, state="readonly").pack(side="left", fill="x", expand=True, padx=(10, 8))
        ctk.CTkButton(row, text="Set", width=70, command=self.start_ptt_key_capture).pack(side="left")

    def _engine_changed(self, value):
        if value == "OpenAI Whisper":
            value = "OpenAI Transcription"
            self.engine_var.set(value)
        self._save_single("stt_engine", value)
        self._show_provider_settings(value)
        if stt_controls.is_running():
            self.status_label.configure(text="Stop and restart STT to apply the new engine.")

    def _show_provider_settings(self, engine):
        for frame in self._provider_frames.values():
            frame.pack_forget()
        frame = self._provider_frames.get(engine)
        if frame:
            frame.pack(fill="x", pady=(0, 10))

    def _device_mode_changed(self, value):
        mode = str(value).strip().lower()
        save_settings("stt_device_selection_mode", mode)
        self.refresh_devices()

    def refresh_devices(self):
        self._devices = stt_controls.discover_input_devices()
        self._device_by_label = {
            f"[{device.index}] {device.name} — {device.host_api}": device
            for device in self._devices
        }
        values = list(self._device_by_label) or ["No active input devices"]
        self.device_combo.configure(values=values)

        mode = self.device_mode_var.get().strip().lower()
        resolution = resolve_input_device(
            self._devices,
            mode=mode,
            saved_id=str(get_settings("stt_device_id") or ""),
            saved_name=str(get_settings("stt_device_name") or ""),
            saved_host_api=str(get_settings("stt_device_host_api") or ""),
            preferred_patterns=parse_preferred_patterns(
                self.device_patterns_box.get("0.0", "end-1c")
            ),
            legacy_index=get_settings("stt_device_index"),
        )
        if resolution.device:
            selected_label = next(
                (label for label, device in self._device_by_label.items() if device == resolution.device),
                values[0],
            )
            self.device_choice_var.set(selected_label)
            ambiguity = " Selection is ambiguous." if resolution.ambiguous else ""
            self.device_resolution_label.configure(
                text=f"{resolution.reason}{ambiguity} Current endpoint: {resolution.device.label}"
            )
        else:
            self.device_choice_var.set(values[0])
            self.device_resolution_label.configure(text=resolution.reason)
        self.device_combo.configure(state="normal" if mode == "manual" else "disabled")

    def _manual_device_selected(self, label):
        device = self._device_by_label.get(label)
        if not device:
            return
        self.device_mode_var.set("Manual")
        values = {
            "stt_device_selection_mode": "manual",
            "stt_device_id": device.stable_id,
            "stt_device_name": device.name,
            "stt_device_host_api": device.host_api,
            "stt_device_index": str(device.index),
        }
        for key, value in values.items():
            save_settings(key, value)
        self.device_resolution_label.configure(text=f"Manual endpoint selected: {device.label}")

    def save_settings(self):
        engine = self.engine_var.get().strip()
        values = {
            "stt_engine": engine,
            "stt_mode": self.mode_var.get().strip(),
            "stt_ptt_key": stt_controls.normalize_ptt_combo(self.ptt_key_var.get()),
            "stt_language": self.language_var.get().strip(),
            "stt_wake_word_enabled": bool(self.wake_word_enabled_var.get()),
            "stt_wake_word": self.wake_word_var.get().strip(),
            "stt_pause_during_playback": bool(self.pause_playback_var.get()),
            "stt_device_selection_mode": self.device_mode_var.get().strip().lower(),
            "stt_device_preferred_patterns": self.device_patterns_box.get("0.0", "end-1c").strip(),
            "stt_vad_aggressiveness": int(self.vad_var.get()),
            "stt_pre_speech_ms": int(self.pre_speech_var.get()),
            "stt_post_speech_ms": int(self.post_speech_var.get()),
            "stt_segment_max_ms": int(self.segment_max_var.get()),
            "stt_min_length_ms": int(self.min_length_var.get()),
            "stt_model_name": self.model_var.get().strip(),
            "stt_device": self.local_device_var.get().strip(),
            "stt_compute_type": self.compute_var.get().strip(),
            "stt_beam_size": int(self.beam_var.get()),
            "stt_enable_partials": bool(self.partial_var.get()),
            "stt_partial_interval_ms": int(self.partial_interval_var.get()),
            "stt_openai_model": self.openai_model_var.get().strip(),
        }
        for key, value in values.items():
            save_settings(key, value)
        self.refresh_devices()
        stt_controls.configured_device_resolution(persist=True)
        self.status_label.configure(text="STT settings saved. Start STT to apply capture changes.")

    def start_stt(self):
        self.save_settings()
        stt_controls.bind_push_to_talk(self.app)
        try:
            self.focus_set()
        except Exception:
            pass
        ok = stt_controls.restart_stt(on_text=self._console_stt_callback)
        self.status_label.configure(text=stt_controls.status_text() if ok else "Failed to start STT.")

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
        transcript = (text or "").strip()
        if not transcript:
            return

        def render():
            prefix = "Partial" if is_partial else "Final"
            self.preview_box.insert("end", f"{prefix}: {transcript}\n")
            self.preview_box.see("end")
            console = getattr(self.app, "pages", {}).get("Console") if self.app else None
            if console is not None and hasattr(console, "on_stt_text"):
                console.on_stt_text(transcript, is_partial=is_partial)
            else:
                stt_controls.handle_stt_text(transcript, is_partial=is_partial)

        try:
            self.after(0, render)
        except Exception:
            pass

    def _refresh_runtime_status(self):
        self._status_after_id = None
        if not self._page_active:
            return
        try:
            self.status_label.configure(text=stt_controls.status_text())
            self._status_after_id = self.after(400, self._refresh_runtime_status)
        except Exception:
            self._status_after_id = None

    def on_show(self):
        if self._page_active:
            return
        self._page_active = True
        self._refresh_runtime_status()

    def on_hide(self):
        self._page_active = False
        if self._status_after_id is not None:
            try:
                self.after_cancel(self._status_after_id)
            except Exception:
                pass
            self._status_after_id = None

    def start_ptt_key_capture(self):
        self._capturing_ptt_key = True
        self._ptt_capture_modifiers.clear()
        stt_controls.suspend_push_to_talk_binding(True)
        self.status_label.configure(text="Press the key or key combination to use for push-to-talk.")

    def _capture_ptt_key(self, event):
        if not self._capturing_ptt_key:
            return
        key = stt_controls.normalize_ptt_combo(getattr(event, "keysym", ""))
        if not key:
            return
        if key in {"ctrl", "shift", "alt"}:
            self._ptt_capture_modifiers.add(key)
            return "break"
        self._save_ptt_capture(key)
        return "break"

    def _capture_ptt_key_release(self, event):
        if not self._capturing_ptt_key:
            return
        key = stt_controls.normalize_ptt_combo(getattr(event, "keysym", ""))
        if key not in {"ctrl", "shift", "alt"}:
            return
        self._ptt_capture_modifiers.discard(key)
        if not self._ptt_capture_modifiers:
            self._save_ptt_capture(key)
        return "break"

    def _save_ptt_capture(self, key):
        ordered = [name for name in ("ctrl", "shift", "alt") if name in self._ptt_capture_modifiers]
        combo = stt_controls.normalize_ptt_combo("+".join(ordered + [key]))
        self.ptt_key_var.set(combo)
        self._capturing_ptt_key = False
        self._ptt_capture_modifiers.clear()
        stt_controls.suspend_push_to_talk_binding(False)
        self._save_single("stt_ptt_key", combo)
        self.status_label.configure(text=f"Push-to-talk key saved: {combo}")

    @staticmethod
    def _save_single(key, value):
        save_settings(key, value)

    def destroy(self):
        if self._capturing_ptt_key:
            stt_controls.suspend_push_to_talk_binding(False)
        self.on_hide()
        super().destroy()
