import customtkinter as ctk
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
    from files.main_loop.main_loop import message_queue
except Exception:
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
        self._conversation_paused = False
        self._external_turns_started = set()

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=20)

        left = ctk.CTkFrame(body, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        right_outer = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=10, width=360)
        right_outer.pack(side="right", fill="y", padx=(10, 0))
        right_outer.pack_propagate(False)

        right = ctk.CTkScrollableFrame(right_outer, fg_color="transparent", corner_radius=10)
        right.pack(fill="both", expand=True)

        ctk.CTkLabel(
            left,
            text="Console",
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=0, pady=(0, 10))

        self.chat_frame = ctk.CTkScrollableFrame(
            left,
            fg_color="#0e141b",
            corner_radius=10,
        )
        self.chat_frame.pack(fill="both", expand=True, padx=0, pady=(0, 10))

        Logger.bind_ui(self)

        input_frame = ctk.CTkFrame(left, fg_color="transparent")
        input_frame.pack(fill="x", padx=0, pady=(0, 8))

        saved_username = get_settings("username") if get_settings else ""
        self.username_var = ctk.StringVar(value=saved_username or "User")

        self.username_entry = ctk.CTkEntry(
            input_frame,
            textvariable=self.username_var,
            width=120,
        )
        self.username_entry.pack(side="left", padx=(0, 8))
        self.username_entry.bind("<FocusOut>", self.save_username)

        self.message_var = ctk.StringVar()

        entry = ctk.CTkEntry(
            input_frame,
            textvariable=self.message_var,
            placeholder_text="Send a test message to your companion...",
        )
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        entry.bind("<Return>", lambda _e: self.send_message())

        ctk.CTkButton(
            input_frame,
            text="Send",
            command=self.send_message,
        ).pack(side="left")

        controls = ctk.CTkFrame(left, fg_color="transparent")
        controls.pack(fill="x", padx=0, pady=(0, 0))

        self.interrupt_button = ctk.CTkButton(
            controls,
            text="Interrupt",
            command=self.interrupt_current_output,
        )
        self.interrupt_button.pack(side="left", padx=(0, 6))

        self.pause_button = ctk.CTkButton(
            controls,
            text="Pause",
            command=self.toggle_pause,
        )
        self.pause_button.pack(side="left", padx=6)

        self.clear_chat_button = ctk.CTkButton(
            controls,
            text="Clear Chat",
            command=self.clear_chat_display,
        )
        self.clear_chat_button.pack(side="left", padx=6)

        saved_ai_name = get_settings("character_name") if get_settings else ""
        self.ai_name_var = ctk.StringVar(value=saved_ai_name or "Character")

        ctk.CTkLabel(
            controls,
            text="AI Name:",
            text_color=theme.MUTED_TEXT,
        ).pack(side="left", padx=(18, 6))

        self.ai_name_entry = ctk.CTkEntry(
            controls,
            textvariable=self.ai_name_var,
            width=150,
        )
        self.ai_name_entry.pack(side="left", padx=(0, 6))
        self.ai_name_entry.bind("<FocusOut>", self.save_ai_name)
        self.ai_name_entry.bind("<Return>", self.save_ai_name)

        ctk.CTkButton(
            controls,
            text="Save Name",
            width=90,
            command=self.save_ai_name,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(
            right,
            text="System Monitor",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 12))

        self.cpu_gauge = ResourceGauge(right, "CPU")
        self.cpu_gauge.pack(fill="x", padx=16)

        self.ram_gauge = ResourceGauge(right, "RAM")
        self.ram_gauge.pack(fill="x", padx=16)

        self.disk_gauge = ResourceGauge(right, "Disk")
        self.disk_gauge.pack(fill="x", padx=16)

        self.gpu_gauge = ResourceGauge(right, "GPU")
        self.gpu_gauge.pack(fill="x", padx=16)

        self.finetune_sampling_var = ctk.BooleanVar(
            value=bool(get_settings("enable_finetune_sampling")) if get_settings else False
        )

        ctk.CTkSwitch(
            right,
            text="Enable Fine-Tune Sampling",
            variable=self.finetune_sampling_var,
            command=self.toggle_finetune_sampling,
            text_color=theme.TEXT,
            progress_color=theme.ACCENT,
        ).pack(anchor="w", padx=16, pady=(12, 6))

        self._quota_bar: "ActiveProviderStatusBar | None" = None
        if ActiveProviderStatusBar is not None:
            ctk.CTkFrame(right, fg_color="#2d3f52", height=1).pack(
                fill="x", padx=16, pady=(8, 0)
            )
            self._quota_bar = ActiveProviderStatusBar(right)
            self._quota_bar.pack(fill="x", padx=16, pady=(8, 4))

        self.vision_panel = VisionPanel(right, self)
        self.vision_panel.pack(fill="x", padx=16, pady=(8, 4))

        ctk.CTkFrame(right, fg_color="#2d3f52", height=1).pack(
            fill="x", padx=16, pady=(8, 0)
        )

        ctk.CTkFrame(right, fg_color="#2d3f52", height=1).pack(
            fill="x", padx=16, pady=(8, 0)
        )

        ctk.CTkLabel(
            right,
            text="Speech Input",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 8))

        self.stt_status_label = ctk.CTkLabel(
            right,
            text=stt_controls.status_text(),
            text_color=theme.MUTED_TEXT,
            wraplength=280,
            justify="left",
        )
        self.stt_status_label.pack(anchor="w", padx=16, pady=(0, 8))

        stt_btn_row = ctk.CTkFrame(right, fg_color="transparent")
        stt_btn_row.pack(fill="x", padx=16, pady=4)

        ctk.CTkButton(
            stt_btn_row,
            text="Start STT",
            command=self.start_stt,
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))

        ctk.CTkButton(
            stt_btn_row,
            text="Stop",
            fg_color=theme.DANGER,
            hover_color="#922b21",
            command=self.stop_stt,
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        stt_pause_row = ctk.CTkFrame(right, fg_color="transparent")
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

        ctk.CTkLabel(
            right,
            text="Pending Jobs & Messages",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 8))

        self.queue_box = ctk.CTkTextbox(
            right,
            height=180,
            fg_color="#0e141b",
            text_color=theme.TEXT,
            wrap="word",
        )
        self.queue_box.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        ctk.CTkFrame(right, fg_color="#2d3f52", height=1).pack(
            fill="x", padx=16, pady=(12, 0)
        )
 
        self.caption_panel = CaptionPreviewPanel(right)
        self.caption_panel.pack(fill="x", padx=16, pady=(12, 16))

        self.update_monitor()

    def refresh_active_provider(self):
        if self._quota_bar is not None:
            self._quota_bar.refresh()

    def _refresh_stt_status(self):
        if hasattr(self, "stt_status_label"):
            self.stt_status_label.configure(text=stt_controls.status_text())

    def start_stt(self):
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

        self.log_status("Interrupt signal sent." if triggered else "No interrupt handler is currently available.")

    def clear_chat_display(self):
        for widget in self.chat_frame.winfo_children():
            widget.destroy()
        self.turn_logs.clear()
        self._external_turns_started.clear()
        self._open_log_close_fn = None

        try:
            if caption_controls is not None and hasattr(caption_controls, "clear_sync"):
                caption_controls.clear_sync()
        except Exception:
            pass
        self.log_status("Chat window reset.")

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
        anchor = "e" if bubble_role in ("user", "twitch", "external") else "w"
        bubble.pack(anchor=anchor, padx=10, pady=5)
        self.after(150, self._scroll_chat_to_bottom)

    def outside_chat(self, turn_id, username, text, source=""):
        if not turn_id or turn_id in self._external_turns_started:
            return
        self._external_turns_started.add(turn_id)
        self.turn_logs.setdefault(turn_id, [])
        label = username or "Twitch"
        self.add_bubble(label, text or "", "twitch")
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
        Logger.unbind_ui()
        super().destroy()

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
        self.cpu_gauge.update_value(psutil.cpu_percent(interval=None))
        self.ram_gauge.update_value(psutil.virtual_memory().percent)
        self.disk_gauge.update_value(psutil.disk_usage("/").percent)
        self.gpu_gauge.update_value(self.get_gpu_usage())
        self._refresh_stt_status()

        self.update_queue_preview()

        self.after(1500, self.update_monitor)

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
        self.queue_box.delete("0.0", "end")
        if not message_queue:
            self.queue_box.insert("0.0", "message queue unavailable.")
            return
        try:
            items = list(message_queue.queue)
        except Exception:
            self.queue_box.insert("0.0", "Could not inspect queue.")
            return
        if not items:
            self.queue_box.insert("0.0", "(none)")
            return
        lines = []
        for i, item in enumerate(items[-10:], start=1):
            if isinstance(item, tuple):
                text = str(item[0])
            else:
                text = str(item)
            lines.append(f"{i}. {text[:120]}")
        self.queue_box.insert("0.0", "\n".join(lines))

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
        self.after(150, self._scroll_chat_to_bottom)
