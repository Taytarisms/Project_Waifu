import customtkinter as ctk
from files.ui import theme
from files.ui.components.header import SteamHeader
from files.system_setup.settings import get_settings
from files.system_setup.system_logger import Logger
from files.tts.narrator import narrator_controls
_DEVICE_CACHE: list[tuple[int, str]] | None = None

def _list_output_devices() -> list[tuple[int, str]]:
    global _DEVICE_CACHE
    if _DEVICE_CACHE is not None:
        return _DEVICE_CACHE
    devices: list[tuple[int, str]] = []
    try:
        import pyaudio
    except Exception as exc:
        Logger.warn(f"PyAudio unavailable for narrator output devices: {exc}")
        _DEVICE_CACHE = devices
        return devices
    try:
        p = pyaudio.PyAudio()
    except Exception as exc:
        Logger.warn(f"Could not initialize PyAudio for narrator devices: {exc}")
        _DEVICE_CACHE = devices
        return devices
    try:
        for i in range(p.get_device_count()):
            try:
                info = p.get_device_info_by_index(i)
            except Exception:
                continue
            if int(info.get("maxOutputChannels", 0)) <= 0:
                continue
            name = str(info.get("name", f"Device {i}"))
            devices.append((i, name))
    finally:
        try:
            p.terminate()
        except Exception:
            pass
    _DEVICE_CACHE = devices
    return devices

class NarratorSettingsPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app
        self._syncing_from_settings = False
        self._refresh_after_id = None

        is_enabled = narrator_controls.is_enabled()

        SteamHeader(
            self,
            title="Narrator",
            subtitle="Reads incoming chat messages aloud before the character responds.",
            action_text="✓ Enabled" if is_enabled else "Enable Narrator",
            action_command=self._toggle_enabled_from_header,
        ).pack(fill="x")

        nav_frame = ctk.CTkFrame(self, fg_color="transparent")
        nav_frame.pack(fill="x", padx=24, pady=(12, 0))
        ctk.CTkButton(
            nav_frame,
            text="← Back",
            width=100,
            fg_color="gray30",
            command=lambda: self.app.show_page("TTS"),
        ).pack(side="left")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=20)

        left = ctk.CTkScrollableFrame(body, fg_color=theme.CARD_BG, corner_radius=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        content = ctk.CTkFrame(left, fg_color="transparent")
        content.pack(fill="both", expand=True)
        self._build_left_column(content)

        right_outer = ctk.CTkFrame(
            body,
            fg_color=theme.CARD_BG,
            corner_radius=10,
            width=340,
        )
        right_outer.pack(side="right", fill="y", padx=(10, 0))
        right_outer.pack_propagate(False)

        right = ctk.CTkScrollableFrame(
            right_outer,
            fg_color="transparent",
            corner_radius=10,
        )
        right.pack(fill="both", expand=True)
        self._build_right_column(right)
        self.refresh_from_settings()
        self._refresh_status_loop()

    def _setting_bool(self, key: str, default: bool = False) -> bool:
        raw = get_settings(key)

        if raw in ("", None):
            return default

        if isinstance(raw, bool):
            return raw

        if isinstance(raw, (int, float)):
            return bool(raw)

        return str(raw).strip().lower() in {
            "true",
            "1",
            "yes",
            "y",
            "on",
            "enabled",
        }

    def _apply_switch_state(self, switch, variable, value: bool) -> None:
        variable.set(bool(value))

        try:
            if value:
                switch.select()
            else:
                switch.deselect()
        except Exception:
            pass

    def refresh_from_settings(self):
        try:
            self._syncing_from_settings = True

            enabled = self._setting_bool("narrator_enabled", default=False)
            read_own = self._setting_bool("narrator_read_own_messages", default=False)

            if hasattr(self, "enabled_switch"):
                self._apply_switch_state(self.enabled_switch, self.enabled_var, enabled)
            elif hasattr(self, "enabled_var"):
                self.enabled_var.set(enabled)

            if hasattr(self, "read_own_switch"):
                self._apply_switch_state(self.read_own_switch, self.read_own_var, read_own)
            elif hasattr(self, "read_own_var"):
                self.read_own_var.set(read_own)

            if hasattr(self, "status_label"):
                self.status_label.configure(text=narrator_controls.status_text())

        except Exception as e:
            Logger.warn(f"Failed to refresh narrator settings UI: {e}")
        finally:
            self._syncing_from_settings = False

    def _build_left_column(self, parent):
        ctk.CTkLabel(
            parent,
            text="Narrator Settings",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=18, pady=(18, 12))

        self.enabled_var = ctk.BooleanVar(
            value=self._setting_bool("narrator_enabled", default=False)
        )
        self.enabled_switch = ctk.CTkSwitch(
            parent,
            text="Enable Narrator",
            variable=self.enabled_var,
            command=self._on_enabled_toggle,
            text_color=theme.TEXT,
            progress_color=theme.ACCENT,
        )
        self.enabled_switch.pack(anchor="w", padx=18, pady=(0, 6))

        self.read_own_var = ctk.BooleanVar(
            value=self._setting_bool("narrator_read_own_messages", default=False)
        )
        self.read_own_switch = ctk.CTkSwitch(
            parent,
            text="Also read my own messages (manual + STT)",
            variable=self.read_own_var,
            command=self._on_read_own_toggle,
            text_color=theme.TEXT,
            progress_color=theme.ACCENT,
        )
        self.read_own_switch.pack(anchor="w", padx=18, pady=(0, 18))

        ctk.CTkLabel(
            parent,
            text="Backend",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=18, pady=(0, 4))

        backends = narrator_controls.list_backends()
        backend_choices = [
            f"{display_name}{'' if available else '  (unavailable)'}"
            for _, display_name, available in backends
        ]
        self._backend_id_by_choice = {
            f"{display_name}{'' if available else '  (unavailable)'}": backend_id
            for backend_id, display_name, available in backends
        }
        current_backend_id = (get_settings("narrator_backend") or "novelai").strip().lower()
        current_choice = next(
            (
                f"{display_name}{'' if available else '  (unavailable)'}"
                for backend_id, display_name, available in backends
                if backend_id == current_backend_id
            ),
            backend_choices[0] if backend_choices else "",
        )
        self.backend_var = ctk.StringVar(value=current_choice)
        self.backend_menu = ctk.CTkOptionMenu(
            parent,
            values=backend_choices or ["(no backends)"],
            variable=self.backend_var,
            command=self._on_backend_change,
            fg_color=theme.HEADER_BG,
            button_color=theme.HEADER_BG,
            text_color=theme.TEXT,
        )
        self.backend_menu.pack(anchor="w", padx=18, pady=(0, 18), fill="x")

        ctk.CTkLabel(
            parent,
            text="Volume Adjustment (dB)",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=18, pady=(0, 4))

        initial_vol = 0
        try:
            initial_vol = int(get_settings("narrator_volume_db") or 0)
        except Exception:
            initial_vol = 0

        vol_row = ctk.CTkFrame(parent, fg_color="transparent")
        vol_row.pack(fill="x", padx=18, pady=(0, 18))

        self.volume_slider = ctk.CTkSlider(
            vol_row,
            from_=-20,
            to=20,
            number_of_steps=40,
            command=self._on_volume_slide,
            progress_color=theme.ACCENT,
        )
        self.volume_slider.set(initial_vol)
        self.volume_slider.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.volume_value_label = ctk.CTkLabel(
            vol_row,
            text=f"{initial_vol:+d} dB",
            text_color=theme.MUTED_TEXT,
            width=70,
        )
        self.volume_value_label.pack(side="left")

        ctk.CTkLabel(
            parent,
            text=(
                "Tip: NovelAI voices can be numeric preset IDs, custom seed strings, "
                "or v2 seedmix strings.\n"
                "Edge voices look like \"en-US-AvaNeural\"."
            ),
            text_color=theme.MUTED_TEXT,
            wraplength=440,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 18))

    def _build_right_column(self, parent):
        ctk.CTkLabel(
            parent,
            text="Narrator Summary",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 8))

        self.status_label = ctk.CTkLabel(
            parent,
            text=narrator_controls.status_text(),
            text_color=theme.MUTED_TEXT,
            wraplength=280,
            justify="left",
        )
        self.status_label.pack(anchor="w", padx=16, pady=8)

        ctk.CTkFrame(parent, fg_color="#2d3f52", height=1).pack(
            fill="x", padx=16, pady=(12, 0)
        )
        ctk.CTkLabel(
            parent,
            text="Voice",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(14, 6))

        self.voice_var = ctk.StringVar(value=str(get_settings("narrator_voice") or ""))
        self.voice_combo = ctk.CTkComboBox(
            parent,
            values=self._voices_for_current_backend(),
            variable=self.voice_var,
            command=self._on_voice_change,
            fg_color=theme.HEADER_BG,
            button_color=theme.HEADER_BG,
            text_color=theme.TEXT,
        )
        self.voice_combo.pack(anchor="w", padx=16, pady=(0, 4), fill="x")
        self.voice_combo.bind(
            "<FocusOut>",
            lambda _e: self._on_voice_change(self.voice_var.get()),
        )

        ctk.CTkFrame(parent, fg_color="#2d3f52", height=1).pack(
            fill="x", padx=16, pady=(12, 0)
        )
        ctk.CTkLabel(
            parent,
            text="Output Device",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(14, 6))

        device_options = ["(default)"] + [name for _, name in _list_output_devices()]
        self._device_index_by_name = {"(default)": ""}
        for idx, name in _list_output_devices():
            self._device_index_by_name[name] = idx

        current_device = get_settings("narrator_output_device")
        if current_device in (None, ""):
            current_device_name = "(default)"
        else:
            current_device_name = next(
                (
                    name
                    for idx, name in _list_output_devices()
                    if str(idx) == str(current_device)
                ),
                "(default)",
            )
        self.device_var = ctk.StringVar(value=current_device_name)
        ctk.CTkOptionMenu(
            parent,
            values=device_options,
            variable=self.device_var,
            command=self._on_device_change,
            fg_color=theme.HEADER_BG,
            button_color=theme.HEADER_BG,
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(0, 8), fill="x")

        ctk.CTkFrame(parent, fg_color="#2d3f52", height=1).pack(
            fill="x", padx=16, pady=(12, 0)
        )
        ctk.CTkLabel(
            parent,
            text="Test",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(14, 6))

        self.test_text_var = ctk.StringVar(value="Testing the narrator voice.")
        ctk.CTkEntry(
            parent,
            textvariable=self.test_text_var,
        ).pack(fill="x", padx=16, pady=(0, 8))

        test_row = ctk.CTkFrame(parent, fg_color="transparent")
        test_row.pack(fill="x", padx=16, pady=(0, 16))

        ctk.CTkButton(
            test_row,
            text="Play Test",
            command=self._on_test,
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))

        ctk.CTkButton(
            test_row,
            text="Stop",
            fg_color=theme.DANGER,
            hover_color="#922b21",
            command=narrator_controls.interrupt,
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        ctk.CTkButton(
            parent,
            text="Back to TTS Library",
            command=lambda: self.app.show_page("TTS"),
            fg_color="gray30",
        ).pack(fill="x", padx=16, pady=(8, 16))

    def _voices_for_current_backend(self) -> list[str]:
        current_choice = self.backend_var.get() if hasattr(self, "backend_var") else ""
        backend_id = self._backend_id_by_choice.get(current_choice, "")
        return narrator_controls.list_voices_for(backend_id) or [""]

    def _refresh_status_loop(self) -> None:
        try:
            self.refresh_from_settings()
        except Exception:
            pass
        self._refresh_after_id = self.after(2000, self._refresh_status_loop)

    def _toggle_enabled_from_header(self):
        new_value = not bool(self.enabled_var.get())
        self._set_enabled_value(new_value)

    def _set_enabled_value(self, value: bool) -> None:
        if self._syncing_from_settings:
            return

        narrator_controls.set_enabled(bool(value))
        self._apply_switch_state(self.enabled_switch, self.enabled_var, bool(value))
        self.status_label.configure(text=narrator_controls.status_text())
        Logger.print(
            f"enabled={bool(value)}, "
            f"saved={get_settings('narrator_enabled')!r}"
        )

    def _set_read_own_value(self, value: bool) -> None:
        if self._syncing_from_settings:
            return

        narrator_controls.set_read_own_messages(bool(value))
        self._apply_switch_state(self.read_own_switch, self.read_own_var, bool(value))
        self.status_label.configure(text=narrator_controls.status_text())
        Logger.print(
            f"read_own={bool(value)}, "
            f"saved={get_settings('narrator_read_own_messages')!r}"
        )

    def _on_enabled_toggle(self):
        self._set_enabled_value(bool(self.enabled_var.get()))

    def _on_read_own_toggle(self):
        self._set_read_own_value(bool(self.read_own_var.get()))

    def _on_backend_change(self, choice: str):
        backend_id = self._backend_id_by_choice.get(choice, "novelai")
        narrator_controls.set_backend(backend_id)
        new_voices = narrator_controls.list_voices_for(backend_id) or [""]
        self.voice_combo.configure(values=new_voices)
        saved_voice = str(get_settings("narrator_voice") or "").strip()

        if not saved_voice or saved_voice not in new_voices:
            saved_voice = new_voices[0] if new_voices else ""
            narrator_controls.set_voice(saved_voice)

        self.voice_var.set(saved_voice)
        self.status_label.configure(text=narrator_controls.status_text())
        Logger.print(f"Narrator backend set to: {choice}")

    def _on_voice_change(self, value: str):
        voice = str(value or "").strip()
        narrator_controls.set_voice(voice)
        self.status_label.configure(text=narrator_controls.status_text())

    def _on_device_change(self, name: str):
        idx = self._device_index_by_name.get(name, "")
        narrator_controls.set_output_device(idx if idx != "" else None)
        self.status_label.configure(text=narrator_controls.status_text())

    def _on_volume_slide(self, value):
        v = int(round(float(value)))
        self.volume_value_label.configure(text=f"{v:+d} dB")
        narrator_controls.set_volume_db(v)

    def _on_test(self):
        text = self.test_text_var.get().strip() or "Testing the narrator voice."
        narrator_controls.test_voice(text)
        self.status_label.configure(text=narrator_controls.status_text())

    def destroy(self):
        try:
            if self._refresh_after_id is not None:
                self.after_cancel(self._refresh_after_id)
        except Exception:
            pass
        super().destroy()
