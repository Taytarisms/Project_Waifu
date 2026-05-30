import asyncio
import json
from pathlib import Path
import customtkinter as ctk
import sys

from files.ui import theme
from files.vtube_studio.hotkeys import UNMAPPED_OPTION, VTSHotkeyManager, USERDATA_DIR
from files.vtube_studio.idle_animations import IDLE_PROFILES
from files.main_loop import main_loop as app_loop
from files.system_setup.settings import get_settings, save_settings, get_bool_setting

DEFAULT_ACTION_SLOTS = [
    "neutral", "happy", "sad", "angry", "confused",
    "surprised", "embarrassed", "love", "thinking", "sleepy",
    "idle", "reset",
]

AVAILABLE_PORTS = ["8001", "8002", "8003", "8004"]
AUTOCONNECT_PATH = USERDATA_DIR / "vts_autoconnect.json"

def card_bg():
    return getattr(theme, "CARD_BG", "#161b22")

def _load_autoconnect() -> dict:
    try:
        if AUTOCONNECT_PATH.exists():
            return json.loads(AUTOCONNECT_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"enabled": False, "port": 8001}

def _save_autoconnect(enabled: bool, port: int) -> None:
    try:
        USERDATA_DIR.mkdir(parents=True, exist_ok=True)
        AUTOCONNECT_PATH.write_text(
            json.dumps({"enabled": enabled, "port": port}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

class VTubeStudioPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app
        self.manager = app_loop.get_vts_manager()
        if self.manager is None:
            self.manager = VTSHotkeyManager(logger=self._log_async_safe)
            app_loop.set_vts_manager(self.manager)
        else:
            self.manager.set_logger(self._log_async_safe)
        self.status_var = ctk.StringVar(value="VTube Studio page ready.")
        self.hotkey_count_var = ctk.StringVar(value="Cached hotkeys: 0")
        self.connection_var = ctk.StringVar(value="Disconnected")
        self.idle_status_var = ctk.StringVar(value="Idle: Off")
        self.slot_dropdowns = {}
        ac = _load_autoconnect()
        self._ac_port = ac.get("port", 8001)
        self._ac_enabled = ac.get("enabled", False)
        self._autoconnect_attempt = 0
        self._autoconnect_max_attempts = 12
        self._autoconnect_retry_ms = 2500
        self._autoconnect_scan_ports = True
        self._build()
        self.refresh_from_cache()
        if self._ac_enabled:
            self.after(500, self._attempt_autoconnect)

    @staticmethod
    def _raw_print(message: str) -> None:
        try:
            sys.__stdout__.write(str(message) + "\n")
            sys.__stdout__.flush()
        except Exception:
            pass

    def _attempt_autoconnect(self):
        self._autoconnect_attempt += 1
        try:
            saved_port = int(self._ac_port or 8001)
        except Exception:
            saved_port = 8001
        self.manager.settings.port = saved_port
        self.port_var.set(str(saved_port))

        self._log(
            f"Auto-connecting to VTS on port {saved_port} "
            f"(attempt {self._autoconnect_attempt}/{self._autoconnect_max_attempts})..."
        )

        self._run_async_thread(lambda port=saved_port: self._autoconnect_async(port))

    async def _autoconnect_async(self, saved_port: int):
        ports_to_try = [int(saved_port)]
        if self._autoconnect_scan_ports:
            for port in AVAILABLE_PORTS:
                try:
                    p = int(port)
                except Exception:
                    continue
                if p not in ports_to_try:
                    ports_to_try.append(p)
        connected_port = None
        last_error = ""
        for port in ports_to_try:
            try:
                # Force a clean client/socket before trying this port.
                self.manager.set_logger(self._log_async_safe)
                await self.manager.close()
                self.manager.settings.port = port
                ok = await self.manager.connect()
                if ok:
                    connected_port = port
                    break
            except Exception as e:
                last_error = str(e)
                self._raw_print(f"VTS auto-connect attempt failed on port {port}: {e}")
                self._raw_print(f"Async error: {e}")

        def update_ui():
            if connected_port is not None:
                self._ac_port = connected_port
                self.port_var.set(str(connected_port))
                self.connection_var.set(f"Connected (:{connected_port})")
                self._log(f"Auto-connected to VTS on port {connected_port}.")

                if self.autoconnect_var.get():
                    _save_autoconnect(True, connected_port)

                return
            self.connection_var.set("Auto-connect failed")
            if (
                self.autoconnect_var.get()
                and self._autoconnect_attempt < self._autoconnect_max_attempts
            ):
                self._log("Auto-connect failed. VTS may not be ready yet; retrying...")
                self.after(self._autoconnect_retry_ms, self._attempt_autoconnect)
            else:
                detail = f" Last error: {last_error}" if last_error else ""
                self._log(
                    "Auto-connect failed. Make sure VTube Studio is open, "
                    "the API is enabled, and the port matches."
                    f"{detail}"
                )
        self._ui(update_ui)

    def _build(self):
        outer = ctk.CTkFrame(self, fg_color=theme.APP_BG)
        outer.pack(fill="both", expand=True, padx=24, pady=24)

        ctk.CTkLabel(outer, text="VTube Studio", font=ctk.CTkFont(size=28, weight="bold"), text_color=theme.TEXT).pack(anchor="w")
        ctk.CTkLabel(outer, text="Persistent VTS websocket control with cached hotkeys, emotion sequences, and idle animation.", text_color=theme.MUTED_TEXT).pack(anchor="w", pady=(4, 18))
        controls = ctk.CTkFrame(outer, fg_color=card_bg(), corner_radius=10)
        controls.pack(fill="x", pady=(0, 10))

        ctk.CTkButton(controls, text="Connect", command=self.connect_clicked).pack(side="left", padx=12, pady=12)
        ctk.CTkButton(controls, text="Close", command=self.close_clicked).pack(side="left", padx=(0, 12), pady=12)
        ctk.CTkButton(controls, text="Refresh Hotkeys", command=self.refresh_hotkeys_clicked).pack(side="left", padx=(0, 12), pady=12)
        ctk.CTkButton(controls, text="Save Mapping", command=self.save_mapping_clicked).pack(side="left", padx=(0, 12), pady=12)
        ctk.CTkButton(controls, text="Test First Mapped", command=self.test_selected_clicked).pack(side="left", padx=(0, 12), pady=12)
        ctk.CTkButton(controls, text="Full Reset", command=self.full_reset_clicked, fg_color=getattr(theme, "DANGER", "#8b2f2f")).pack(side="left", padx=(0, 12), pady=12)
        port_row = ctk.CTkFrame(outer, fg_color=card_bg(), corner_radius=10)
        port_row.pack(fill="x", pady=(0, 18))

        ctk.CTkLabel(port_row, text="Port:", text_color=theme.MUTED_TEXT).pack(side="left", padx=(12, 6), pady=10)
        self.port_var = ctk.StringVar(value=str(self._ac_port))
        self.port_dropdown = ctk.CTkOptionMenu(
            port_row, values=AVAILABLE_PORTS, variable=self.port_var,
            command=self._on_port_change, width=90,
        )
        self.port_dropdown.pack(side="left", padx=(0, 18), pady=10)

        self.autoconnect_var = ctk.BooleanVar(value=self._ac_enabled)
        ctk.CTkCheckBox(
            port_row, text="Auto-connect on startup",
            variable=self.autoconnect_var, text_color=theme.TEXT,
            command=self._on_autoconnect_toggle,
        ).pack(side="left", padx=(0, 12), pady=10)
        info_row = ctk.CTkFrame(outer, fg_color="transparent")
        info_row.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(info_row, textvariable=self.connection_var, text_color=theme.MUTED_TEXT).pack(side="left", padx=(0, 18))
        ctk.CTkLabel(info_row, textvariable=self.hotkey_count_var, text_color=theme.MUTED_TEXT).pack(side="left", padx=(0, 18))
        ctk.CTkLabel(info_row, textvariable=self.idle_status_var, text_color=theme.MUTED_TEXT).pack(side="left")

        self.status_label = ctk.CTkLabel(outer, textvariable=self.status_var, text_color=theme.MUTED_TEXT)
        self.status_label.pack(side="bottom", anchor="w", pady=(12, 0))

        scroll_container = ctk.CTkScrollableFrame(outer, fg_color="transparent")
        scroll_container.pack(fill="both", expand=True)

        self._build_emotion_panel(scroll_container)
        self._build_idle_panel(scroll_container)

        self.mapping_frame = ctk.CTkScrollableFrame(
            scroll_container,
            fg_color=card_bg(),
            label_text="Action Mapping",
            height=320,
        )
        self.mapping_frame.pack(fill="x", pady=(0, 4))

    def _on_port_change(self, new_port_str: str):
        try:
            new_port = int(new_port_str)
        except ValueError:
            return

        old_port = self.manager.settings.port

        if new_port == old_port:
            return

        self.manager.settings.port = new_port
        self._ac_port = new_port

        self._log(f"VTS port changed to {new_port}.")

        if self.autoconnect_var.get():
            _save_autoconnect(True, new_port)

        if self.manager._connected:
            self._log(f"Reconnecting from port {old_port} to {new_port}...")
            self._run_async_thread(
                lambda port=new_port: self._reconnect_on_port_change(port)
            )

    async def _reconnect_on_port_change(self, selected_port: int):
        last_error = ""

        try:
            await self.manager.close()
            self.manager.settings.port = selected_port
            ok = await self.manager.connect()
        except Exception as e:
            ok = False
            last_error = str(e)
            print(f"Reconnect failed on port {selected_port}: {e}")

        def update_ui():
            if ok:
                self.connection_var.set(f"Connected (:{selected_port})")
                self._log(f"Reconnected to VTS on port {selected_port}.")

                if self.autoconnect_var.get():
                    _save_autoconnect(True, selected_port)

            else:
                self.connection_var.set("Reconnect failed")
                self.idle_status_var.set("Idle: Off")
                detail = f" Last error: {last_error}" if last_error else ""
                self._log(
                    f"Reconnect failed on port {selected_port}. "
                    "Make sure VTube Studio is open and API access is enabled."
                    f"{detail}"
                )

        self._ui(update_ui)

    def _on_autoconnect_toggle(self):
        enabled = self.autoconnect_var.get()
        port = int(self.port_var.get())
        _save_autoconnect(enabled, port)
        self._log(f"Auto-connect on startup: {'enabled' if enabled else 'disabled'} (port {port})")

    def _build_emotion_panel(self, parent):
        emotion_frame = ctk.CTkFrame(parent, fg_color=card_bg(), corner_radius=10)
        emotion_frame.pack(fill="x", pady=(0, 18))

        header = ctk.CTkFrame(emotion_frame, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 6))
        ctk.CTkLabel(
            header, text="Emotion Inference",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(side="left")

        ctk.CTkLabel(
            emotion_frame,
            text=(
                "Decide how the VTS expression is chosen after each reply. "
                "LLM is most accurate but adds a second API call per turn; "
                "Heuristic is keyword-based (free and instant); Off skips it entirely."
            ),
            text_color=theme.MUTED_TEXT,
            justify="left",
            wraplength=720,
        ).pack(anchor="w", padx=12, pady=(0, 8))

        control_row = ctk.CTkFrame(emotion_frame, fg_color="transparent")
        control_row.pack(fill="x", padx=12, pady=(0, 12))

        ctk.CTkLabel(
            control_row, text="Mode:",
            text_color=theme.MUTED_TEXT,
        ).pack(side="left", padx=(0, 8))

        self.emotion_mode_button = ctk.CTkSegmentedButton(
            control_row,
            values=["LLM", "Heuristic", "Off"],
            command=self._on_emotion_mode_change,
        )
        self.emotion_mode_button.set(self._current_emotion_mode_label())
        self.emotion_mode_button.pack(side="left", padx=(0, 12))

        self.emotion_mode_status_var = ctk.StringVar(
            value=self._emotion_mode_status_text()
        )
        ctk.CTkLabel(
            control_row,
            textvariable=self.emotion_mode_status_var,
            text_color=theme.MUTED_TEXT,
        ).pack(side="left")

    @staticmethod
    def _resolve_emotion_mode() -> str:
        raw = get_settings("vts_emotion_mode")
        if raw in ("", None):
            legacy_enabled = get_bool_setting(
                "enable_vts_emotion_inference", default=True
            )
            return "llm" if legacy_enabled else "off"
        mode = str(raw).strip().lower()
        return mode if mode in ("llm", "heuristic", "off") else "llm"

    def _current_emotion_mode_label(self) -> str:
        return {"llm": "LLM", "heuristic": "Heuristic", "off": "Off"}.get(
            self._resolve_emotion_mode(), "LLM"
        )

    def _emotion_mode_status_text(self) -> str:
        mode = self._resolve_emotion_mode()
        if mode == "llm":
            return "LLM call after each reply"
        if mode == "heuristic":
            return "Keyword matching only (instant)"
        return "VTS will not change expression"

    def _on_emotion_mode_change(self, value: str) -> None:
        mapping = {"LLM": "llm", "Heuristic": "heuristic", "Off": "off"}
        mode = mapping.get(value, "llm")
        save_settings("vts_emotion_mode", mode)
        save_settings("enable_vts_emotion_inference", mode != "off")
        self.emotion_mode_status_var.set(self._emotion_mode_status_text())
        self._log(f"Emotion inference set to: {mode}")

    def _build_idle_panel(self, parent):
        accent = getattr(theme, "ACCENT", "#58a6ff")
        idle_frame = ctk.CTkFrame(parent, fg_color=card_bg(), corner_radius=10)
        idle_frame.pack(fill="x", pady=(0, 18))

        header = ctk.CTkFrame(idle_frame, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 6))
        ctk.CTkLabel(header, text="Idle Animation", font=ctk.CTkFont(size=16, weight="bold"), text_color=theme.TEXT).pack(side="left")
        ctk.CTkButton(header, text="Stop Idle", width=90, fg_color=getattr(theme, "DANGER", "#8b2f2f"), command=self.stop_idle_clicked).pack(side="right", padx=(6, 0))
        ctk.CTkButton(header, text="Start Idle", width=90, fg_color=accent, command=self.start_idle_clicked).pack(side="right")

        # Profile selector + toggles
        profile_row = ctk.CTkFrame(idle_frame, fg_color="transparent")
        profile_row.pack(fill="x", padx=12, pady=(6, 4))
        ctk.CTkLabel(profile_row, text="Profile:", text_color=theme.MUTED_TEXT).pack(side="left", padx=(0, 6))
        self.profile_var = ctk.StringVar(value="calm")
        self.profile_dropdown = ctk.CTkOptionMenu(profile_row, values=sorted(IDLE_PROFILES.keys()), variable=self.profile_var, command=lambda _: self._on_profile_change(), width=140)
        self.profile_dropdown.pack(side="left", padx=(0, 18))
        self.auto_profile_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(profile_row, text="Auto-rotate", variable=self.auto_profile_var, text_color=theme.TEXT, command=self._on_auto_profile_toggle).pack(side="left", padx=(0, 18))
        self.gesture_enabled_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(profile_row, text="Gestures", variable=self.gesture_enabled_var, text_color=theme.TEXT, command=self._on_idle_config_change).pack(side="left")

        # Head
        head_section = ctk.CTkFrame(idle_frame, fg_color="transparent")
        head_section.pack(fill="x", padx=12, pady=(6, 4))
        self.head_enabled_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(head_section, text="Head", variable=self.head_enabled_var, text_color=theme.TEXT, command=self._on_idle_config_change, width=80).pack(side="left")
        ctk.CTkLabel(head_section, text="Range:", text_color=theme.MUTED_TEXT).pack(side="left", padx=(12, 4))
        self.head_range_slider = ctk.CTkSlider(head_section, from_=1, to=25, number_of_steps=24, command=lambda _: self._on_idle_config_change())
        self.head_range_slider.set(8)
        self.head_range_slider.pack(side="left", padx=(0, 4), fill="x", expand=True)
        self.head_range_label = ctk.CTkLabel(head_section, text="±8°", text_color=theme.MUTED_TEXT, width=40)
        self.head_range_label.pack(side="left")
        ctk.CTkLabel(head_section, text="Speed:", text_color=theme.MUTED_TEXT).pack(side="left", padx=(10, 4))
        self.head_speed_slider = ctk.CTkSlider(head_section, from_=0.05, to=0.50, number_of_steps=18, command=lambda _: self._on_idle_config_change())
        self.head_speed_slider.set(0.18)
        self.head_speed_slider.pack(side="left", padx=(0, 4), fill="x", expand=True)
        self.head_speed_label = ctk.CTkLabel(head_section, text="0.18", text_color=theme.MUTED_TEXT, width=36)
        self.head_speed_label.pack(side="left")

        # Eyes
        eye_section = ctk.CTkFrame(idle_frame, fg_color="transparent")
        eye_section.pack(fill="x", padx=12, pady=(4, 4))
        self.eye_enabled_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(eye_section, text="Eyes", variable=self.eye_enabled_var, text_color=theme.TEXT, command=self._on_idle_config_change, width=80).pack(side="left")
        ctk.CTkLabel(eye_section, text="Gaze:", text_color=theme.MUTED_TEXT).pack(side="left", padx=(12, 4))
        self.eye_range_slider = ctk.CTkSlider(eye_section, from_=0.05, to=0.90, number_of_steps=17, command=lambda _: self._on_idle_config_change())
        self.eye_range_slider.set(0.35)
        self.eye_range_slider.pack(side="left", padx=(0, 4), fill="x", expand=True)
        self.eye_range_label = ctk.CTkLabel(eye_section, text="±0.35", text_color=theme.MUTED_TEXT, width=44)
        self.eye_range_label.pack(side="left")
        ctk.CTkLabel(eye_section, text="Snap:", text_color=theme.MUTED_TEXT).pack(side="left", padx=(10, 4))
        self.saccade_speed_slider = ctk.CTkSlider(eye_section, from_=0.2, to=2.5, number_of_steps=23, command=lambda _: self._on_idle_config_change())
        self.saccade_speed_slider.set(0.8)
        self.saccade_speed_slider.pack(side="left", padx=(0, 4), fill="x", expand=True)
        self.saccade_speed_label = ctk.CTkLabel(eye_section, text="0.8", text_color=theme.MUTED_TEXT, width=30)
        self.saccade_speed_label.pack(side="left")

        # Bounce
        bounce_section = ctk.CTkFrame(idle_frame, fg_color="transparent")
        bounce_section.pack(fill="x", padx=12, pady=(4, 4))
        self.bounce_enabled_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(bounce_section, text="Bounce", variable=self.bounce_enabled_var, text_color=theme.TEXT, command=self._on_idle_config_change, width=80).pack(side="left")
        ctk.CTkLabel(bounce_section, text="Amp:", text_color=theme.MUTED_TEXT).pack(side="left", padx=(12, 4))
        self.bounce_amp_slider = ctk.CTkSlider(bounce_section, from_=0.0, to=5.0, number_of_steps=25, command=lambda _: self._on_idle_config_change())
        self.bounce_amp_slider.set(0.8)
        self.bounce_amp_slider.pack(side="left", padx=(0, 4), fill="x", expand=True)
        self.bounce_amp_label = ctk.CTkLabel(bounce_section, text="0.8°", text_color=theme.MUTED_TEXT, width=36)
        self.bounce_amp_label.pack(side="left")
        ctk.CTkLabel(bounce_section, text="Freq:", text_color=theme.MUTED_TEXT).pack(side="left", padx=(10, 4))
        self.bounce_freq_slider = ctk.CTkSlider(bounce_section, from_=0.1, to=1.5, number_of_steps=14, command=lambda _: self._on_idle_config_change())
        self.bounce_freq_slider.set(0.35)
        self.bounce_freq_slider.pack(side="left", padx=(0, 4), fill="x", expand=True)
        self.bounce_freq_label = ctk.CTkLabel(bounce_section, text="0.35Hz", text_color=theme.MUTED_TEXT, width=48)
        self.bounce_freq_label.pack(side="left")

        # Blink
        blink_section = ctk.CTkFrame(idle_frame, fg_color="transparent")
        blink_section.pack(fill="x", padx=12, pady=(4, 12))
        self.blink_enabled_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(blink_section, text="Blink", variable=self.blink_enabled_var, text_color=theme.TEXT, command=self._on_idle_config_change, width=80).pack(side="left")
        ctk.CTkLabel(blink_section, text="Interval:", text_color=theme.MUTED_TEXT).pack(side="left", padx=(12, 4))
        self.blink_interval_slider = ctk.CTkSlider(blink_section, from_=1.0, to=10.0, number_of_steps=18, command=lambda _: self._on_idle_config_change())
        self.blink_interval_slider.set(4.5)
        self.blink_interval_slider.pack(side="left", padx=(0, 4), fill="x", expand=True)
        self.blink_interval_label = ctk.CTkLabel(blink_section, text="2-7s", text_color=theme.MUTED_TEXT, width=40)
        self.blink_interval_label.pack(side="left")

    def _on_idle_config_change(self):
        engine = self.manager.idle_engine
        if engine is None:
            return
        head_range = self.head_range_slider.get()
        head_freq = self.head_speed_slider.get()
        eye_range = self.eye_range_slider.get()
        saccade_spd = self.saccade_speed_slider.get()
        bounce_amp = self.bounce_amp_slider.get()
        bounce_freq = self.bounce_freq_slider.get()
        blink_mid = self.blink_interval_slider.get()

        self.head_range_label.configure(text=f"±{head_range:.0f}°")
        self.head_speed_label.configure(text=f"{head_freq:.2f}")
        self.eye_range_label.configure(text=f"±{eye_range:.2f}")
        self.saccade_speed_label.configure(text=f"{saccade_spd:.1f}")
        self.bounce_amp_label.configure(text=f"{bounce_amp:.1f}°")
        self.bounce_freq_label.configure(text=f"{bounce_freq:.2f}Hz")
        blink_lo = max(1.0, blink_mid - 2.5)
        blink_hi = blink_mid + 2.5
        self.blink_interval_label.configure(text=f"{blink_lo:.0f}-{blink_hi:.0f}s")

        engine.update_head_config(enabled=self.head_enabled_var.get(), x_amplitude=head_range, y_amplitude=head_range * 0.65, z_amplitude=head_range * 0.50, frequency=head_freq)
        engine.update_body_config(enabled=self.head_enabled_var.get() or self.bounce_enabled_var.get(), x_amplitude=head_range * 0.38, y_amplitude=head_range * 0.25, z_amplitude=head_range * 0.20, frequency=max(0.04, head_freq * 0.55))
        engine.update_eye_config(enabled=self.eye_enabled_var.get(), gaze_x_range=eye_range, gaze_y_range=eye_range * 0.65, saccade_speed=saccade_spd)
        engine.update_bounce_config(enabled=self.bounce_enabled_var.get(), amplitude=bounce_amp, frequency=bounce_freq)
        engine.update_blink_config(enabled=self.blink_enabled_var.get(), interval_range=(blink_lo, blink_hi))
        engine.update_gesture_config(enabled=self.gesture_enabled_var.get())

    def _on_profile_change(self):
        engine = self.manager.idle_engine
        if engine is None:
            return
        name = self.profile_var.get()
        engine.set_idle_profile(name)
        self._sync_sliders_from_engine()
        self._log(f"Idle profile: {name}")

    def _on_auto_profile_toggle(self):
        engine = self.manager.idle_engine
        if engine is None:
            return
        engine.set_auto_profiles(enabled=self.auto_profile_var.get())
        self._log(f"Auto-rotate profiles: {'on' if self.auto_profile_var.get() else 'off'}")

    def _sync_sliders_from_engine(self):
        engine = self.manager.idle_engine
        if engine is None:
            return
        cfg = engine.config
        self.head_enabled_var.set(cfg.head.enabled)
        self.head_range_slider.set(cfg.head.x_amplitude)
        self.head_speed_slider.set(cfg.head.frequency)
        self.eye_enabled_var.set(cfg.eyes.enabled)
        self.eye_range_slider.set(cfg.eyes.gaze_x_range)
        self.saccade_speed_slider.set(cfg.eyes.saccade_speed)
        self.bounce_enabled_var.set(cfg.bounce.enabled)
        self.bounce_amp_slider.set(cfg.bounce.amplitude)
        self.bounce_freq_slider.set(cfg.bounce.frequency)
        self.blink_enabled_var.set(cfg.blink.enabled)
        blink_mid = (cfg.blink.interval_range[0] + cfg.blink.interval_range[1]) / 2.0
        self.blink_interval_slider.set(blink_mid)
        self.gesture_enabled_var.set(cfg.gesture.enabled)
        self.profile_var.set(engine.get_idle_profile())
        self._refresh_idle_labels_only()

    def _refresh_idle_labels_only(self):
        head_range = self.head_range_slider.get()
        head_freq = self.head_speed_slider.get()
        eye_range = self.eye_range_slider.get()
        saccade_spd = self.saccade_speed_slider.get()
        bounce_amp = self.bounce_amp_slider.get()
        bounce_freq = self.bounce_freq_slider.get()
        blink_mid = self.blink_interval_slider.get()
        self.head_range_label.configure(text=f"±{head_range:.0f}°")
        self.head_speed_label.configure(text=f"{head_freq:.2f}")
        self.eye_range_label.configure(text=f"±{eye_range:.2f}")
        self.saccade_speed_label.configure(text=f"{saccade_spd:.1f}")
        self.bounce_amp_label.configure(text=f"{bounce_amp:.1f}°")
        self.bounce_freq_label.configure(text=f"{bounce_freq:.2f}Hz")
        blink_lo = max(1.0, blink_mid - 2.5)
        blink_hi = blink_mid + 2.5
        self.blink_interval_label.configure(text=f"{blink_lo:.0f}-{blink_hi:.0f}s")

    def start_idle_clicked(self):
        self._log("Starting idle animation...")
        async def task():
            ok = await self.manager.start_idle_animation()
            def update_ui():
                if ok:
                    engine = self.manager.idle_engine
                    if engine is not None:
                        profile = self.profile_var.get() or "calm"
                        if engine.get_idle_profile() != profile:
                            engine.set_idle_profile(profile)
                    self._sync_sliders_from_engine()
                    self.idle_status_var.set("Idle: On")
                    self._log("Idle animation started.")
                else:
                    self._log("Failed to start idle animation. Is VTS connected?")
            self._ui(update_ui)
        self._run_async_thread(task)

    def stop_idle_clicked(self):
        self._log("Stopping idle animation...")
        self._run_async_thread(self._stop_idle_async)

    async def _stop_idle_async(self):
        await self.manager.stop_idle_animation()
        def update_ui():
            self.idle_status_var.set("Idle: Off")
            self._log("Idle animation stopped.")
        self.after(0, update_ui)

    def connect_clicked(self):
        try:
            selected_port = int(self.port_var.get())
        except ValueError:
            selected_port = self.manager.settings.port or 8001

        self.manager.settings.port = selected_port

        self._log(f"Connecting to VTube Studio on port {selected_port}...")
        self._run_async_thread(lambda port=selected_port: self._connect_async(port))

    async def _connect_async(self, selected_port: int):
        connected_port = None
        last_error = ""
        ports_to_try = [int(selected_port)]
        for port in AVAILABLE_PORTS:
            try:
                p = int(port)
            except Exception:
                continue
            if p not in ports_to_try:
                ports_to_try.append(p)
        for port in ports_to_try:
            try:
                await self.manager.close()
                self.manager.settings.port = port
                ok = await self.manager.connect()
                if ok:
                    connected_port = port
                    break
            except Exception as e:
                last_error = str(e)
                print(f"Connection attempt failed on port {port}: {e}")

        def update_ui():
            if connected_port is not None:
                self._ac_port = connected_port
                self.port_var.set(str(connected_port))
                self.connection_var.set(f"Connected (:{connected_port})")
                self._log(f"Connected to VTS on port {connected_port}.")

                if self.autoconnect_var.get():
                    _save_autoconnect(True, connected_port)

            else:
                self.connection_var.set("Connection failed")
                detail = f" Last error: {last_error}" if last_error else ""
                self._log(
                    "Connection failed. Check that VTube Studio is open, "
                    "the API is enabled, and the plugin is allowed."
                    f"{detail}"
                )

        self._ui(update_ui)

    def close_clicked(self):
        self._log("Closing VTube Studio connection...")
        self._run_async_thread(self._close_async)

    async def _close_async(self):
        await self.manager.close()
        def update_ui():
            self.connection_var.set("Disconnected")
            self.idle_status_var.set("Idle: Off")
            self._log("Closed VTS connection.")
        self._ui(update_ui)

    def refresh_hotkeys_clicked(self):
        self._log("Refreshing VTS hotkeys...")
        self._run_async_thread(self._refresh_hotkeys_async)

    async def _refresh_hotkeys_async(self):
        hotkeys = await self.manager.refresh_hotkey_cache()
        def update_ui():
            self.hotkey_count_var.set(f"Cached hotkeys: {len(hotkeys)}")
            self.refresh_from_cache()
            if hotkeys:
                self._log(f"Refreshed {len(hotkeys)} VTS hotkeys.")
                self.connection_var.set(f"Connected (:{self.manager.settings.port})")
            else:
                self._log("No hotkeys found. Make sure VTube Studio is open, API is enabled, and a model is loaded.")
        self._ui(update_ui)

    def _build_mapping_rows(self, hotkey_names):
        for child in self.mapping_frame.winfo_children():
            child.destroy()
        self.slot_dropdowns.clear()
        if not hotkey_names:
            hotkey_names = ["No cached hotkeys found"]
        else:
            hotkey_names = [UNMAPPED_OPTION] + hotkey_names
        saved_map = self.manager.load_vts_mapping()
        saved_emotions = saved_map.get("emotions", {})
        for row_index, slot in enumerate(DEFAULT_ACTION_SLOTS):
            row = ctk.CTkFrame(self.mapping_frame, fg_color="transparent")
            row.grid(row=row_index, column=0, sticky="ew", padx=12, pady=6)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text=slot.title(), text_color=theme.TEXT, width=140, anchor="w").grid(row=0, column=0, sticky="w", padx=(0, 12))
            emotion_config = saved_emotions.get(slot, {})
            talking_sequence = emotion_config.get("talking_hotkey_sequence", [])
            selected_name = None
            if talking_sequence:
                selected_name = talking_sequence[0].get("name")
            if selected_name not in hotkey_names:
                selected_name = hotkey_names[0]
            dropdown = ctk.CTkOptionMenu(row, values=hotkey_names)
            dropdown.set(selected_name)
            dropdown.grid(row=0, column=1, sticky="ew")
            ctk.CTkButton(row, text="Test", width=70, command=lambda s=slot: self.test_slot_clicked(s)).grid(row=0, column=2, sticky="e", padx=(8, 0))
            self.slot_dropdowns[slot] = dropdown

    def refresh_from_cache(self):
        hotkey_names = self.manager.get_cached_hotkey_names()
        self.hotkey_count_var.set(f"Cached hotkeys: {len(hotkey_names)}")
        self._build_mapping_rows(hotkey_names)

    def save_mapping_clicked(self):
        mapping = {"current_emotion": "", "full_reset_hotkey_sequence": [], "emotions": {}}
        for slot, dropdown in self.slot_dropdowns.items():
            selected_name = dropdown.get()
            if not selected_name or selected_name == "No cached hotkeys found" or selected_name == UNMAPPED_OPTION:
                continue
            entry = self.manager.build_mapping_entry_from_name(selected_name)
            if not entry:
                continue
            mapping["emotions"][slot] = {"talking_hotkey_sequence": [entry], "idle_hotkey_sequence": [], "reset_hotkey_sequence": []}
            if slot == "reset":
                mapping["full_reset_hotkey_sequence"] = [entry]
        self.manager.save_vts_mapping(mapping)
        self._log(f"Saved {len(mapping['emotions'])} VTS emotion mappings.")

    def test_selected_clicked(self):
        for slot, dropdown in self.slot_dropdowns.items():
            selected_name = dropdown.get()
            if selected_name and selected_name != "No cached hotkeys found" and selected_name != UNMAPPED_OPTION:
                self.test_slot_clicked(slot)
                return
        self._log("No valid mapped emotion to test.")

    def test_slot_clicked(self, slot_name):
        dropdown = self.slot_dropdowns.get(slot_name)
        if not dropdown:
            self._log(f"No dropdown found for slot: {slot_name}")
            return
        selected_name = dropdown.get()
        if not selected_name or selected_name == "No cached hotkeys found" or selected_name == UNMAPPED_OPTION:
            self._log(f"{slot_name} is unmapped.")
            return
        self.save_mapping_clicked()
        self._log(f"Testing VTS emotion slot: {slot_name}")
        self._run_async_thread(lambda slot=slot_name: self._test_emotion_async(slot))

    async def _test_emotion_async(self, emotion_name):
        result = await self.manager.set_emotion_and_animate_talking(emotion_name)
        if result:
            self.after(0, lambda n=emotion_name: self._log(f"Triggered talking sequence for: {n}"))
            self.after(0, lambda: self.connection_var.set(f"Connected (:{self.manager.settings.port})"))
        else:
            self.after(0, lambda n=emotion_name: self._log(f"Failed to trigger talking sequence for: {n}"))

    def full_reset_clicked(self):
        self._log("Triggering VTS full reset...")
        self._run_async_thread(self._full_reset_async)

    async def _full_reset_async(self):
        result = await self.manager.full_reset()
        if result:
            self.after(0, lambda: self._log("Triggered VTS full reset."))
        else:
            self.after(0, lambda: self._log("No full reset sequence mapped, or reset failed."))

    def _ui(self, func, *args, **kwargs):
        try:
            self.after(0, lambda: func(*args, **kwargs))
        except RuntimeError:
            pass
        except Exception:
            pass

    def _run_async_thread(self, async_func):
        try:
            loop = app_loop.async_loop
            if loop is None or not loop.is_running():
                print("Main async loop is not running yet.")
                return
            future = asyncio.run_coroutine_threadsafe(async_func(), loop)
            def done_callback(fut):
                try:
                    fut.result()
                except Exception as e:
                    print(f"Async error: {e}")
            future.add_done_callback(done_callback)
        except Exception as e:
            print(f"Async submit error: {e}")

    @staticmethod
    def _log_async_safe(message):
        print(message)

    def _log(self, message):
        print(message)
        try:
            self.status_var.set(message)
        except Exception:
            pass