import customtkinter as ctk

from files.ui.components.scrolling import AppScrollableFrame
from files.ui import theme
import queue
import psutil
import threading
import uuid
from files.ui.components.LabelMaker import ChatBubble
from files.system_setup.system_logger import Logger
from files.speech_to_text import stt_controls
from files.system_setup.settings import save_settings, get_settings
from files.ui.components.quota_checker import ActiveProviderStatusBar
from files.ui.components.VisionPanel import VisionPanel
from files.ui.components.captionpreviewpanel import CaptionPreviewPanel

try:
    import GPUtil
except Exception:
    GPUtil = None

try:
    from files.main_loop.main_loop import is_turn_active, message_queue
except Exception:
    is_turn_active = None
    message_queue = None

try:
    from files.main_loop import gen_response
except Exception:
    gen_response = None

try:
    from files.tts.narrator import narrator_controls
except Exception:
    narrator_controls = None

try:
    from files.closed_captions import caption_controls
except Exception:
    caption_controls = None

class ResourceGauge(ctk.CTkFrame):
    def __init__(self, master, label):
        super().__init__(master, fg_color="transparent")

        self.label = label

        self.title = ctk.CTkLabel(
            self,
            text=f"{label}: 0%",
            text_color=theme.TEXT,
            anchor="w",
        )
        self.title.pack(anchor="w")

        self.bar = ctk.CTkProgressBar(self, height=12)
        self.bar.pack(fill="x", pady=(4, 10))
        self.bar.set(0)

    def update_value(self, value):
        value = max(0, min(float(value), 100))
        self.title.configure(text=f"{self.label}: {value:.1f}%")
        self.bar.set(value / 100)

class ConsolePage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app
        self.turn_logs = {}
        self._open_log_close_fn = None
        self.max_turn_logs = 50
        self.max_rendered_chat_items = 240
        self._rendered_chat_items = []
        self._conversation_paused = False
        self._external_turns_started = set()
        self._tool_frames = {}
        self._reasoning_cards = []
        self.max_reasoning_cards = 30
        self._page_active = False
        self._monitor_after_id = None
        self._monitor_sample_in_flight = False

        shell = ctk.CTkFrame(self, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=24, pady=(16, 20))

        title_row = ctk.CTkFrame(shell, fg_color="transparent")
        title_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            title_row,
            text="Live Session",
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color=theme.TEXT,
        ).pack(side="left")
        ctk.CTkLabel(
            title_row,
            text="Conversation console",
            font=ctk.CTkFont(size=13),
            text_color=theme.MUTED_TEXT,
        ).pack(side="left", padx=(12, 0), pady=(8, 0))

        status_strip = ctk.CTkFrame(shell, fg_color="transparent")
        status_strip.pack(fill="x", pady=(0, 12))
        self.llm_status_chip = self._status_chip(
            status_strip, "LLM", lambda: self.app.show_page("LLM")
        )
        self.tts_status_chip = self._status_chip(
            status_strip, "TTS", lambda: self.app.show_page("TTS")
        )
        self.stt_status_chip = self._status_chip(
            status_strip, "STT", lambda: self.app.show_page("STT")
        )
        self.queue_status_chip = self._status_chip(status_strip, "Queue", None)
        self.session_status_chip = self._status_chip(status_strip, "Session", None)

        workspace = ctk.CTkFrame(shell, fg_color="transparent")
        workspace.pack(fill="both", expand=True)

        left = ctk.CTkFrame(workspace, fg_color=theme.CARD_BG, corner_radius=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        conversation_header = ctk.CTkFrame(left, fg_color="transparent")
        conversation_header.pack(fill="x", padx=18, pady=(14, 10))
        ctk.CTkLabel(
            conversation_header,
            text="Conversation",
            font=ctk.CTkFont(size=19, weight="bold"),
            text_color=theme.TEXT,
        ).pack(side="left")
        self.conversation_state_label = ctk.CTkLabel(
            conversation_header,
            text="Ready",
            text_color=theme.SUCCESS,
            font=ctk.CTkFont(size=12),
        )
        self.conversation_state_label.pack(side="right")

        self.chat_frame = AppScrollableFrame(
            left,
            fg_color="#0e141b",
            corner_radius=8,
        )
        self.chat_frame.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        Logger.bind_ui(self)

        saved_username = get_settings("username") if get_settings else ""
        saved_ai_name = get_settings("character_name") if get_settings else ""
        self.username_var = ctk.StringVar(value=saved_username or "User")
        self.ai_name_var = ctk.StringVar(value=saved_ai_name or "Character")
        self.message_var = ctk.StringVar()

        composer = ctk.CTkFrame(left, fg_color="#1b2c3a", corner_radius=8)
        composer.pack(fill="x", padx=14, pady=(0, 14))
        input_frame = ctk.CTkFrame(composer, fg_color="transparent")
        input_frame.pack(fill="x", padx=12, pady=(10, 6))
        self.composer_identity_label = ctk.CTkLabel(
            input_frame,
            text=self.username_var.get(),
            width=90,
            anchor="w",
            text_color=theme.MUTED_TEXT,
        )
        self.composer_identity_label.pack(side="left", padx=(0, 8))
        self.message_entry = ctk.CTkEntry(
            input_frame,
            textvariable=self.message_var,
            placeholder_text="Message your companion...",
        )
        self.message_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.message_entry.bind("<Return>", lambda _e: self.send_message())
        ctk.CTkButton(
            input_frame,
            text="Send",
            width=90,
            command=self.send_message,
        ).pack(side="left")

        controls = ctk.CTkFrame(composer, fg_color="transparent")
        controls.pack(fill="x", padx=12, pady=(0, 10))
        self.interrupt_button = ctk.CTkButton(
            controls,
            text="Interrupt",
            width=100,
            fg_color=theme.DANGER,
            hover_color="#922b21",
            command=self.interrupt_current_output,
        )
        self.interrupt_button.pack(side="left", padx=(0, 6))
        self.pause_button = ctk.CTkButton(
            controls,
            text="Pause",
            width=90,
            fg_color="#435566",
            command=self.toggle_pause,
        )
        self.pause_button.pack(side="left", padx=6)
        self.clear_chat_button = ctk.CTkButton(
            controls,
            text="Clear",
            width=70,
            fg_color="gray30",
            command=self.clear_chat_display,
        )
        self.clear_chat_button.pack(side="right")

        right_outer = ctk.CTkFrame(
            workspace,
            fg_color=theme.CARD_BG,
            corner_radius=10,
            width=360,
        )
        right_outer.pack(side="right", fill="y", padx=(10, 0))
        right_outer.pack_propagate(False)

        self.rail_selector = ctk.CTkSegmentedButton(
            right_outer,
            values=["Session", "Thoughts", "Tools"],
            command=self._show_rail_view,
        )
        self.rail_selector.pack(fill="x", padx=16, pady=(16, 10))

        self.session_view = ctk.CTkFrame(right_outer, fg_color="transparent")
        self.reasoning_view = ctk.CTkFrame(right_outer, fg_color="transparent")
        self.tools_view = ctk.CTkFrame(right_outer, fg_color="transparent")
        self._build_session_view(self.session_view)
        self._build_reasoning_view(self.reasoning_view)
        self._build_tools_view(self.tools_view)
        self.rail_selector.set("Session")
        self._show_rail_view("Session")

    def _status_chip(self, parent, label, command):
        chip = ctk.CTkButton(
            parent,
            text=f"{label}: —",
            height=30,
            width=155 if label != "Session" else 135,
            anchor="w",
            fg_color="#203647",
            hover_color="#315a78",
            text_color=theme.TEXT,
            command=command,
            hover=command is not None,
        )
        chip.pack(side="left", padx=(0, 8))
        return chip

    def _build_session_view(self, parent):
        activity = ctk.CTkFrame(parent, fg_color="#1b2c3a", corner_radius=8)
        activity.pack(fill="x", padx=16, pady=(2, 12))
        ctk.CTkLabel(
            activity,
            text="Current Activity",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=14, pady=(12, 4))
        self.current_activity_label = ctk.CTkLabel(
            activity,
            text="Ready for a message.",
            text_color=theme.MUTED_TEXT,
            wraplength=290,
            justify="left",
        )
        self.current_activity_label.pack(anchor="w", padx=14, pady=(0, 12))

        queue_header = ctk.CTkFrame(parent, fg_color="transparent")
        queue_header.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(
            queue_header,
            text="Up Next",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(side="left")
        self.queue_count_label = ctk.CTkLabel(
            queue_header,
            text="0 queued",
            text_color=theme.MUTED_TEXT,
            font=ctk.CTkFont(size=11),
        )
        self.queue_count_label.pack(side="right")
        self.queue_box = ctk.CTkTextbox(
            parent,
            height=125,
            fg_color="#0e141b",
            text_color=theme.TEXT,
            wrap="word",
        )
        self.queue_box.pack(fill="x", padx=16, pady=(0, 14))

        ctk.CTkFrame(parent, fg_color="#2d3f52", height=1).pack(
            fill="x", padx=16, pady=(0, 12)
        )
        ctk.CTkLabel(
            parent,
            text="Speech Input",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(0, 6))
        self.stt_status_label = ctk.CTkLabel(
            parent,
            text=stt_controls.status_text(),
            text_color=theme.MUTED_TEXT,
            wraplength=300,
            justify="left",
        )
        self.stt_status_label.pack(anchor="w", padx=16, pady=(0, 8))

        stt_btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        stt_btn_row.pack(fill="x", padx=16, pady=4)
        ctk.CTkButton(
            stt_btn_row,
            text="Start",
            command=self.start_stt,
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(
            stt_btn_row,
            text="Stop",
            fg_color=theme.DANGER,
            hover_color="#922b21",
            command=self.stop_stt,
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        stt_pause_row = ctk.CTkFrame(parent, fg_color="transparent")
        stt_pause_row.pack(fill="x", padx=16, pady=4)
        ctk.CTkButton(
            stt_pause_row,
            text="Pause",
            fg_color="#21262d",
            hover_color="#30363d",
            command=self.pause_stt,
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(
            stt_pause_row,
            text="Resume",
            fg_color="#21262d",
            hover_color="#30363d",
            command=self.resume_stt,
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

    def _build_tools_view(self, parent):
        self.tool_selector = ctk.CTkSegmentedButton(
            parent,
            values=["Production", "System", "Identity"],
            command=self._show_tool_tab,
        )
        self.tool_selector.pack(fill="x", padx=16, pady=(2, 10))

        production = AppScrollableFrame(parent, fg_color="transparent")
        system = AppScrollableFrame(parent, fg_color="transparent")
        identity = AppScrollableFrame(parent, fg_color="transparent")
        self._tool_frames = {
            "Production": production,
            "System": system,
            "Identity": identity,
        }

        self.vision_panel = VisionPanel(production, self)
        self.vision_panel.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkFrame(production, fg_color="#2d3f52", height=1).pack(
            fill="x", padx=12, pady=8
        )
        self.caption_panel = CaptionPreviewPanel(production)
        self.caption_panel.pack(fill="x", padx=12, pady=(4, 12))

        ctk.CTkLabel(
            system,
            text="System Health",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=12, pady=(4, 12))
        self.cpu_gauge = ResourceGauge(system, "CPU")
        self.cpu_gauge.pack(fill="x", padx=12)
        self.ram_gauge = ResourceGauge(system, "RAM")
        self.ram_gauge.pack(fill="x", padx=12)
        self.disk_gauge = ResourceGauge(system, "Disk")
        self.disk_gauge.pack(fill="x", padx=12)
        self.gpu_gauge = ResourceGauge(system, "GPU")
        self.gpu_gauge.pack(fill="x", padx=12)

        self.finetune_sampling_var = ctk.BooleanVar(
            value=bool(get_settings("enable_finetune_sampling")) if get_settings else False
        )
        ctk.CTkSwitch(
            system,
            text="Save Chat for fine-tuning.",
            variable=self.finetune_sampling_var,
            command=self.toggle_finetune_sampling,
            text_color=theme.TEXT,
            progress_color=theme.ACCENT,
        ).pack(anchor="w", padx=12, pady=(10, 8))

        self._quota_bar: "ActiveProviderStatusBar | None" = None
        if ActiveProviderStatusBar is not None:
            ctk.CTkFrame(system, fg_color="#2d3f52", height=1).pack(
                fill="x", padx=12, pady=(8, 0)
            )
            self._quota_bar = ActiveProviderStatusBar(system)
            self._quota_bar.pack(fill="x", padx=12, pady=(10, 12))

        ctk.CTkLabel(
            identity,
            text="Conversation Identity",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=12, pady=(4, 6))
        ctk.CTkLabel(
            identity,
            text="These names label manual messages and companion responses.",
            text_color=theme.MUTED_TEXT,
            wraplength=285,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 14))
        ctk.CTkLabel(identity, text="Your Name", text_color=theme.TEXT).pack(
            anchor="w", padx=12, pady=(0, 4)
        )
        self.username_entry = ctk.CTkEntry(identity, textvariable=self.username_var)
        self.username_entry.pack(fill="x", padx=12, pady=(0, 12))
        self.username_entry.bind("<FocusOut>", self.save_username)
        self.username_entry.bind("<Return>", self.save_username)
        ctk.CTkLabel(identity, text="Companion Name", text_color=theme.TEXT).pack(
            anchor="w", padx=12, pady=(0, 4)
        )
        self.ai_name_entry = ctk.CTkEntry(identity, textvariable=self.ai_name_var)
        self.ai_name_entry.pack(fill="x", padx=12, pady=(0, 12))
        self.ai_name_entry.bind("<FocusOut>", self.save_ai_name)
        self.ai_name_entry.bind("<Return>", self.save_ai_name)
        self.identity_status_label = ctk.CTkLabel(
            identity,
            text="",
            text_color=theme.MUTED_TEXT,
        )
        self.identity_status_label.pack(anchor="w", padx=12, pady=(0, 6))
        ctk.CTkButton(
            identity,
            text="Save Identity",
            command=self._save_identity,
        ).pack(fill="x", padx=12, pady=(0, 12))

        self.tool_selector.set("Production")
        self._show_tool_tab("Production")

    def _build_reasoning_view(self, parent):
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(2, 8))
        ctk.CTkLabel(
            header,
            text="Model Thoughts",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=theme.TEXT,
        ).pack(side="left")
        self.reasoning_count_label = ctk.CTkLabel(
            header,
            text="0 turns",
            text_color=theme.MUTED_TEXT,
            font=ctk.CTkFont(size=11),
        )
        self.reasoning_count_label.pack(side="right")

        ctk.CTkLabel(
            parent,
            text=(
                "Shows model-emitted traces or provider summaries. "
                "These may not represent the model's complete internal reasoning."
            ),
            text_color=theme.MUTED_TEXT,
            wraplength=300,
            justify="left",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=16, pady=(0, 10))

        self.reasoning_scroll = AppScrollableFrame(
            parent,
            fg_color="#0e141b",
            corner_radius=8,
        )
        self.reasoning_scroll.pack(
            fill="both", expand=True, padx=16, pady=(0, 10)
        )
        self.reasoning_empty_label = ctk.CTkLabel(
            self.reasoning_scroll,
            text="Reasoning summaries will appear here after a response.",
            text_color=theme.MUTED_TEXT,
            wraplength=270,
            justify="left",
        )
        self.reasoning_empty_label.pack(anchor="w", padx=8, pady=12)

        ctk.CTkButton(
            parent,
            text="Clear Thoughts",
            height=30,
            fg_color="gray30",
            command=self.clear_reasoning_display,
        ).pack(fill="x", padx=16, pady=(0, 14))

    def _show_rail_view(self, name):
        self.session_view.pack_forget()
        self.reasoning_view.pack_forget()
        self.tools_view.pack_forget()
        target = {
            "Session": self.session_view,
            "Thoughts": self.reasoning_view,
            "Tools": self.tools_view,
        }.get(name, self.session_view)
        target.pack(fill="both", expand=True)

    def _show_tool_tab(self, name):
        for frame in self._tool_frames.values():
            frame.pack_forget()
        target = self._tool_frames.get(name)
        if target is not None:
            target.pack(fill="both", expand=True)

    def _save_identity(self):
        self.save_username()
        self.save_ai_name()
        self.identity_status_label.configure(
            text="Identity saved.",
            text_color=theme.SUCCESS,
        )

    def refresh_active_provider(self):
        if self._quota_bar is not None:
            self._quota_bar.refresh()
        self._refresh_session_status()

    def _refresh_session_status(self):
        def setting(name, fallback):
            try:
                value = get_settings(name) if get_settings else ""
            except Exception:
                value = ""
            return str(value or fallback)

        llm_name = setting("chat_model", "Not set")
        tts_name = setting("tts_model", "") or setting("tts_engine", "Not set")
        snapshot = stt_controls.runtime_snapshot()
        stt_engine = str(snapshot.get("engine") or setting("stt_engine", "Not set"))
        stt_phase = str(snapshot.get("phase") or "stopped").replace("_", " ").title()
        queue_size = getattr(self, "_queue_size", 0)

        self.llm_status_chip.configure(text=f"LLM: {llm_name}")
        self.tts_status_chip.configure(text=f"TTS: {tts_name}")
        self.stt_status_chip.configure(text=f"STT: {stt_engine} · {stt_phase}")
        self.queue_status_chip.configure(text=f"Queue: {queue_size}")

        try:
            turn_running = bool(is_turn_active and is_turn_active())
        except Exception:
            turn_running = False

        if self._conversation_paused:
            session_text = "Paused"
            activity_text = "Conversation input and companion playback are paused."
            state_text = "Paused"
            state_color = "#f9e2af"
            chip_color = "#5a4820"
        elif turn_running:
            session_text = "Responding"
            activity_text = "The companion is generating or speaking a response."
            state_text = "Responding"
            state_color = theme.ACCENT
            chip_color = "#1d536f"
        elif queue_size:
            session_text = "Queued"
            activity_text = f"Waiting to process {queue_size} queued item{'s' if queue_size != 1 else ''}."
            state_text = f"{queue_size} queued"
            state_color = "#f9e2af"
            chip_color = "#5a4820"
        else:
            session_text = "Ready"
            activity_text = "Ready for a message."
            state_text = "Ready"
            state_color = theme.SUCCESS
            chip_color = "#284b35"

        self.session_status_chip.configure(
            text=f"Session: {session_text}",
            fg_color=chip_color,
        )
        self.current_activity_label.configure(text=activity_text)
        self.conversation_state_label.configure(
            text=state_text,
            text_color=state_color,
        )

    def _refresh_stt_status(self):
        if hasattr(self, "stt_status_label"):
            self.stt_status_label.configure(text=stt_controls.status_text())

    def start_stt(self):
        stt_controls.bind_push_to_talk(self.app)
        stt_controls.start_stt(on_text=self.on_stt_text)
        self._refresh_stt_status()

    def stop_stt(self):
        stt_controls.stop_stt()
        self._refresh_stt_status()

    def pause_stt(self):
        stt_controls.pause_stt()
        self._refresh_stt_status()

    def resume_stt(self):
        stt_controls.resume_stt()
        self._refresh_stt_status()

    def save_username(self, _event=None):
        username = self.username_var.get().strip() or "User"
        self.username_var.set(username)
        if hasattr(self, "composer_identity_label"):
            self.composer_identity_label.configure(text=username)
        if save_settings:
            save_settings("username", username)

    def save_ai_name(self, _event=None):
        ai_name = self.ai_name_var.get().strip() or "Character"
        self.ai_name_var.set(ai_name)
        if save_settings:
            save_settings("character_name", ai_name)
        Logger.print(f"AI name set to: {ai_name}")

    def _current_ai_name(self) -> str:
        try:
            saved = get_settings("character_name") if get_settings else ""
        except Exception:
            saved = ""
        return (saved or self.ai_name_var.get() or "Character").strip() or "Character"

    def toggle_pause(self):
        self._conversation_paused = not self._conversation_paused
        if gen_response is not None:
            try:
                if self._conversation_paused and hasattr(gen_response, "pause_tts"):
                    gen_response.pause_tts()
                elif not self._conversation_paused and hasattr(gen_response, "resume_tts"):
                    gen_response.resume_tts()
            except Exception as exc:
                self.log_status(f"TTS pause toggle failed: {exc}")
        self.pause_button.configure(text="Resume" if self._conversation_paused else "Pause")
        self._refresh_session_status()
        self.log_status(
            "Conversation input and TTS playback paused."
            if self._conversation_paused
            else "Conversation input and TTS playback resumed."
        )

    def interrupt_current_output(self):
        triggered = False
        try:
            if narrator_controls is not None:
                narrator_controls.interrupt()
                triggered = True
        except Exception as exc:
            self.log_status(f"Narrator interrupt failed: {exc}")

        try:
            if gen_response is not None:
                for name in (
                    "interrupt",
                    "interrupt_tts",
                    "interrupt_tts_playback",
                    "stop_speaking",
                    "stop_tts",
                ):
                    fn = getattr(gen_response, name, None)
                    if callable(fn):
                        fn()
                        triggered = True
                        break
        except Exception as exc:
            self.log_status(f"TTS interrupt failed: {exc}")

        if triggered and self._conversation_paused:
            self._conversation_paused = False
            self.pause_button.configure(text="Pause")
            self._refresh_session_status()

        self.log_status("Interrupt signal sent." if triggered else "No interrupt handler is currently available.")

    def clear_chat_display(self):
        for widget in self.chat_frame.winfo_children():
            widget.destroy()
        self._rendered_chat_items.clear()
        self.turn_logs.clear()
        self._external_turns_started.clear()
        self._open_log_close_fn = None
        self.clear_reasoning_display()

        try:
            if caption_controls is not None and hasattr(caption_controls, "clear_sync"):
                caption_controls.clear_sync()
        except Exception:
            pass
        self.log_status("Chat window reset.")

    def clear_reasoning_display(self):
        for card in list(self._reasoning_cards):
            try:
                card.destroy()
            except Exception:
                pass
        self._reasoning_cards.clear()
        if hasattr(self, "reasoning_empty_label"):
            self.reasoning_empty_label.pack(anchor="w", padx=8, pady=12)
        if hasattr(self, "reasoning_count_label"):
            self.reasoning_count_label.configure(text="0 turns")

    def _copy_reasoning(self, text):
        try:
            self.clipboard_clear()
            self.clipboard_append(text or "")
            self.update_idletasks()
        except Exception as exc:
            self.log_status(f"Could not copy model thoughts: {exc}")

    def show_reasoning(self, payload):
        if not isinstance(payload, dict):
            return
        reasoning = str(payload.get("reasoning") or "").strip()
        if not reasoning:
            return

        if hasattr(self, "reasoning_empty_label"):
            self.reasoning_empty_label.pack_forget()

        provider = str(payload.get("provider") or "Model").strip() or "Model"
        model = str(payload.get("model") or "").strip()
        kind = str(payload.get("reasoning_kind") or "provider_summary")
        kind_label = "Local model trace" if kind == "local_trace" else "Provider reasoning summary"

        card = ctk.CTkFrame(
            self.reasoning_scroll,
            fg_color="#1b2c3a",
            corner_radius=8,
        )
        card.pack(fill="x", padx=4, pady=(4, 8))

        title = provider if not model else f"{provider} / {model}"
        ctk.CTkLabel(
            card,
            text=title,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=theme.TEXT,
            wraplength=265,
            justify="left",
        ).pack(anchor="w", padx=10, pady=(9, 1))
        ctk.CTkLabel(
            card,
            text=kind_label,
            text_color=theme.MUTED_TEXT,
            font=ctk.CTkFont(size=10),
        ).pack(anchor="w", padx=10, pady=(0, 6))

        line_estimate = reasoning.count("\n") + max(1, len(reasoning) // 46)
        text_height = max(82, min(220, 25 + line_estimate * 16))
        text_box = ctk.CTkTextbox(
            card,
            height=text_height,
            fg_color="#0e141b",
            text_color=theme.TEXT,
            wrap="word",
            activate_scrollbars=True,
        )
        text_box.pack(fill="x", padx=10, pady=(0, 7))
        text_box.insert("0.0", reasoning)
        text_box.configure(state="disabled")

        ctk.CTkButton(
            card,
            text="Copy",
            width=62,
            height=26,
            fg_color="#435566",
            command=lambda value=reasoning: self._copy_reasoning(value),
        ).pack(anchor="e", padx=10, pady=(0, 9))

        self._reasoning_cards.append(card)
        while len(self._reasoning_cards) > self.max_reasoning_cards:
            oldest = self._reasoning_cards.pop(0)
            oldest.destroy()
        count = len(self._reasoning_cards)
        self.reasoning_count_label.configure(
            text=f"{count} turn{'s' if count != 1 else ''}"
        )

    def shared_state(self, text, username="User", source="manual"):
        if self._conversation_paused:
            self.log_status("Input ignored while paused. Click Resume to continue.")
            return

        self.add_bubble(username, text, "user")
        if not message_queue:
            self.log_status("Queue not available.")
            return
        resp_q = queue.Queue(maxsize=1)
        raw_text = f"{username}: {text}"
        turn_id = str(uuid.uuid4())
        self.turn_logs[turn_id] = []
        meta = {
            "turn_id": turn_id,
            "source": source,
            "character_name": self._current_ai_name(),
        }
        message_queue.put((raw_text, True, resp_q, meta))

        def wait_for_response():
            try:
                response = resp_q.get(timeout=180)
                def render():
                    self.add_bubble(self._current_ai_name(), response, "ai")
                    self.append_turn_log(turn_id)
                self.after(150, render)
            except queue.Empty:
                def render_timeout():
                    self.log_status("No response returned before timeout.", turn_id=turn_id)
                    self.append_turn_log(turn_id)
                self.after(150, render_timeout)
        threading.Thread(target=wait_for_response, daemon=True).start()

    def send_message(self):
        text = self.message_var.get().strip()
        if not text:
            return
        self.message_var.set("")
        username = self.username_var.get().strip() or "User"
        self.shared_state(text=text, username=username, source="manual")

    def on_stt_text(self, text, is_partial=False):
        stt_text = (text or "").strip()
        if not stt_text:
            return
        def update_ui():
            if is_partial:
                self.log_status(f"STT partial: {stt_text}")
                return
            filtered_text = stt_controls.apply_wake_word(stt_text)
            if not filtered_text:
                return
            username = self.username_var.get().strip() or "User"
            self.shared_state(
                text=filtered_text,
                username=username,
                source="stt",
            )

        self.after(0, update_ui)

    def toggle_finetune_sampling(self):
        enabled = bool(self.finetune_sampling_var.get())

        if save_settings:
            save_settings("enable_finetune_sampling", enabled)

        Logger.print("Fine-tune sampling enabled." if enabled else "Fine-tune sampling disabled.")

    def add_bubble(self, sender, text, role):
        bubble = ChatBubble(
            self.chat_frame,
            sender=sender,
            text=f"{sender}: {text}" if sender else text,
            role=role,
            fg_color="transparent",
        )

        bubble_role = (role or "").lower()
        anchor = "e" if bubble_role in ("user", "twitch", "youtube", "kick", "external") else "w"
        bubble.pack(anchor=anchor, padx=10, pady=5)
        self._track_chat_item(bubble)
        self.after(150, self._scroll_chat_to_bottom)

    def _track_chat_item(self, widget):
        self._rendered_chat_items.append(widget)
        while len(self._rendered_chat_items) > self.max_rendered_chat_items:
            oldest = self._rendered_chat_items.pop(0)
            cleanup = getattr(oldest, "_before_chat_trim", None)
            if callable(cleanup):
                cleanup()
            try:
                oldest.destroy()
            except Exception:
                pass

    def outside_chat(self, turn_id, username, text, source=""):
        if not turn_id or turn_id in self._external_turns_started:
            return
        self._external_turns_started.add(turn_id)
        self.turn_logs.setdefault(turn_id, [])
        source_label = {
            "youtube": "YouTube",
            "twitch": "Twitch",
            "kick": "Kick",
        }.get(source, (source or "Stream Chat").replace("_", " ").title())
        label = f"{username} · {source_label}" if username else source_label
        self.add_bubble(label, text or "", source or "external")
        if source:
            self.log_status(f"Received from {source}.", turn_id=turn_id)

    def outside_chat_finish(self, turn_id, response, source=""):
        if not turn_id:
            return
        if turn_id not in self._external_turns_started:
            self._external_turns_started.add(turn_id)
            self.turn_logs.setdefault(turn_id, [])
        self.add_bubble(self._current_ai_name(), response or "", "ai")
        self.append_turn_log(turn_id)

    def destroy(self):
        self.on_hide()
        Logger.unbind_ui()
        super().destroy()

    def on_show(self):
        if self._page_active:
            return
        self._page_active = True
        self.update_monitor()

    def on_hide(self):
        self._page_active = False
        if self._monitor_after_id is not None:
            try:
                self.after_cancel(self._monitor_after_id)
            except Exception:
                pass
            self._monitor_after_id = None

    def _scroll_chat_to_bottom(self):
        try:
            canvas = self.chat_frame._parent_canvas
            if canvas.yview()[1] >= 0.85:
                canvas.yview_moveto(1.0)
        except Exception:
            pass

    def log_status(self, text, turn_id=None):
        if turn_id and turn_id in self.turn_logs:
            logs = self.turn_logs[turn_id]
        else:
            logs = self.turn_logs.setdefault("global", [])

        logs.append(text)

        if len(logs) > self.max_turn_logs:
            del logs[:-self.max_turn_logs]

    def update_monitor(self):
        self._monitor_after_id = None
        if not self._page_active:
            return

        if not self._monitor_sample_in_flight:
            self._monitor_sample_in_flight = True
            threading.Thread(target=self._sample_resource_usage, daemon=True).start()
        self._refresh_stt_status()

        self.update_queue_preview()
        self._refresh_session_status()

        self._monitor_after_id = self.after(1500, self.update_monitor)

    def _sample_resource_usage(self):
        try:
            sample = (
                psutil.cpu_percent(interval=None),
                psutil.virtual_memory().percent,
                psutil.disk_usage("/").percent,
                self.get_gpu_usage(),
            )
            self.after(0, lambda values=sample: self._apply_resource_usage(values))
        except Exception:
            self._monitor_sample_in_flight = False

    def _apply_resource_usage(self, sample):
        self._monitor_sample_in_flight = False
        if not self._page_active:
            return
        cpu, ram, disk, gpu = sample
        self.cpu_gauge.update_value(cpu)
        self.ram_gauge.update_value(ram)
        self.disk_gauge.update_value(disk)
        self.gpu_gauge.update_value(gpu)

    def get_gpu_usage(self):
        if not GPUtil:
            return 0
        try:
            gpus = GPUtil.getGPUs()
            if not gpus:
                return 0
            return gpus[0].load * 100
        except Exception:
            return 0

    def update_queue_preview(self):
        self.queue_box.configure(state="normal")
        self.queue_box.delete("0.0", "end")
        if not message_queue:
            self._queue_size = 0
            self.queue_count_label.configure(text="Unavailable")
            self.queue_box.insert("0.0", "message queue unavailable.")
            self.queue_box.configure(state="disabled")
            return
        try:
            with message_queue.mutex:
                items = list(message_queue.queue)
        except Exception:
            self._queue_size = 0
            self.queue_count_label.configure(text="Unavailable")
            self.queue_box.insert("0.0", "Could not inspect queue.")
            self.queue_box.configure(state="disabled")
            return
        self._queue_size = len(items)
        self.queue_count_label.configure(
            text=f"{self._queue_size} queued"
        )
        if not items:
            self.queue_box.insert("0.0", "Queue is clear.")
            self.queue_box.configure(state="disabled")
            return
        lines = []
        for i, item in enumerate(items[:5], start=1):
            source = "Message"
            if isinstance(item, tuple):
                text = str(item[0])
                if len(item) > 3 and isinstance(item[3], dict):
                    source = str(item[3].get("source") or source).replace("_", " ").title()
            else:
                text = str(item)
            lines.append(f"{i}. {source} · {text[:92]}")
        self.queue_box.insert("0.0", "\n".join(lines))
        self.queue_box.configure(state="disabled")

    def append_turn_log(self, turn_id):
        turn_logs = list(self.turn_logs.get(turn_id, []))
        if not turn_logs:
            return
        group = ctk.CTkFrame(self.chat_frame, fg_color="transparent")
        group.pack(fill="x", padx=10, pady=4)
        count = len(turn_logs)
        header = ctk.CTkButton(
            group,
            text=f"> Turn Logs ({count})",
            fg_color="#182430",
            hover_color="#223348",
            text_color=theme.MUTED_TEXT,
            anchor="w",
        )
        header.pack(fill="x")

        body = ctk.CTkTextbox(
            group,
            height=120,
            fg_color="#0e141b",
            text_color=theme.MUTED_TEXT,
            wrap="word",
        )
        body.insert("0.0", "\n".join(f"> {m.strip()}" for m in turn_logs if m.strip()))
        body.configure(state="disabled")

        state = {"open": False, "animating": False}

        def close_this_log():
            if state["open"] and not state["animating"]:
                state["open"] = False
                state["animating"] = True
                header.configure(text=f"> Turn Logs ({count})")
                self.after(16, lambda: _animate_close(body, step=12))

        def _animate_open(widget, target, step):
            if not state["animating"]:
                return
            canvas = self.chat_frame._parent_canvas
            scroll_pos = canvas.yview()[0]
            current = widget.cget("height")
            next_h = min(current + step, target)
            widget.configure(height=next_h)
            canvas.yview_moveto(scroll_pos)
            if next_h < target:
                self.after(16, lambda: _animate_open(widget, target, step))
            else:
                state["animating"] = False

        def _animate_close(widget, step):
            if not state["animating"]:
                return
            canvas = self.chat_frame._parent_canvas
            scroll_pos = canvas.yview()[0]
            current = int(widget.cget("height"))
            next_h = max(current - step, 0)
            widget.configure(height=next_h)
            canvas.yview_moveto(scroll_pos)
            if next_h > 0:
                self.after(16, lambda: _animate_close(widget, step))
            else:
                widget.pack_forget()
                widget.configure(height=0)
                state["animating"] = False

                if self._open_log_close_fn == close_this_log:
                    self._open_log_close_fn = None

        def toggle():
            if state["animating"]:
                return

            if not state["open"]:
                if self._open_log_close_fn and self._open_log_close_fn != close_this_log:
                    self._open_log_close_fn()

                self._open_log_close_fn = close_this_log

            state["open"] = not state["open"]
            state["animating"] = True

            header.configure(text=f"{'˅' if state['open'] else '>'} Turn Logs ({count})")

            if state["open"]:
                body.configure(height=0)
                body.pack(fill="x", pady=(4, 0))
                self.after(16, lambda: _animate_open(body, target=120, step=12))
            else:
                if self._open_log_close_fn == close_this_log:
                    self._open_log_close_fn = None
                self.after(16, lambda: _animate_close(body, step=12))

        header.configure(command=toggle)
        def before_trim():
            state["open"] = False
            state["animating"] = False
            if self._open_log_close_fn == close_this_log:
                self._open_log_close_fn = None

        group._before_chat_trim = before_trim
        self._track_chat_item(group)
        self.after(150, self._scroll_chat_to_bottom)
