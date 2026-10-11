import copy
import customtkinter as ctk
from files.vtube_studio.advanced import initial_advanced, validate_actions


class AdvancedVTSEditor(ctk.CTkFrame):
    def __init__(self, parent, page, emotions):
        super().__init__(parent, fg_color="transparent")
        self.page = page
        self.manager = page.manager
        mapping = self.manager.load_vts_mapping()
        self.draft = copy.deepcopy(mapping.get("advanced", initial_advanced(mapping)))
        emotions = list(dict.fromkeys(emotions + list(self.draft.get("emotions", {}))))
        self.emotion = ctk.StringVar(value=emotions[0])
        self.phase = ctk.StringVar(value="talking")
        self.action = ctk.StringVar(value="Hotkey")
        self.expression = ctk.StringVar()
        self.hotkey = ctk.StringVar()
        self.seconds = ctk.StringVar(value="0.1")
        self.end = ctk.StringVar(value="idle")
        self.editing = None
        ctk.CTkLabel(self, text="Advanced emotion sequences", font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", padx=12, pady=8)
        ctk.CTkLabel(self, text=("Combine expressions and hotkeys in order. After speech, run idle actions or reset this emotion.\n"
                    "Expressions stay active until reset or an explicit Off action. A new speech cycle resets the previous effects.\n"
                    "Hotkeys can start animations, toggles or other effects; add their matching reset hotkeys when needed."),
                    wraplength=760, justify="left").pack(anchor="w", padx=12, pady=6)
        selectors = ctk.CTkFrame(self, fg_color="transparent")
        selectors.pack(fill="x", padx=12, pady=6)
        ctk.CTkOptionMenu(selectors, variable=self.emotion, values=emotions + ["Full Reset"], command=self.select).pack(side="left", padx=(0, 8))
        self.phase_menu = ctk.CTkOptionMenu(selectors, variable=self.phase, values=["talking", "idle", "reset"], command=self.select)
        self.phase_menu.pack(side="left", padx=8)
        ctk.CTkLabel(selectors, text="After speech:").pack(side="left", padx=8)
        self.end_menu = ctk.CTkOptionMenu(selectors, variable=self.end, values=["idle", "reset"], command=self.change_end)
        self.end_menu.pack(side="left", padx=8)
        self.rows = ctk.CTkFrame(self, fg_color="transparent")
        self.rows.pack(fill="x", padx=12, pady=8)
        ctk.CTkOptionMenu(self, variable=self.action, values=["Hotkey", "Expression On", "Expression Off", "Delay"], command=self.action_changed).pack(anchor="w", padx=12, pady=6)
        self.hotkey_menu = ctk.CTkOptionMenu(self, variable=self.hotkey, values=self.manager.get_cached_hotkey_names() or ["Refresh hotkeys first"])
        self.hotkey_menu.pack(fill="x", padx=12, pady=4)
        self.expression_menu = ctk.CTkComboBox(self, variable=self.expression, values=[""])
        self.expression_menu.pack(fill="x", padx=12, pady=4)
        self.timing_label = ctk.CTkLabel(self, text="Fade-in / delay seconds")
        self.timing_label.pack(anchor="w", padx=12)
        self.timing = ctk.CTkEntry(self, textvariable=self.seconds)
        self.timing.pack(anchor="w", padx=12, pady=4)
        self.add_button = ctk.CTkButton(self, text="Add action", command=self.add_action)
        self.add_button.pack(anchor="w", padx=12, pady=6)
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=12, pady=10)
        ctk.CTkButton(buttons, text="Refresh expressions", command=self.refresh_expressions).pack(side="left", padx=(0, 8))
        ctk.CTkButton(buttons, text="Save sequences", command=self.save).pack(side="left", padx=8)
        ctk.CTkButton(buttons, text="Preview sequence", command=self.preview).pack(side="left", padx=8)
        self.status = ctk.CTkLabel(self, text="Draft changes are saved together. Preview uses the selected draft sequence.", wraplength=760, justify="left")
        self.status.pack(anchor="w", padx=12, pady=8)
        self.select()
        self.action_changed()

    def emotion_config(self):
        return self.draft.setdefault("emotions", {}).setdefault(self.emotion.get(),
            {"talking": [], "idle": [], "reset": [], "after_speech": "idle"})

    def sequence(self):
        return self.draft.setdefault("full_reset", []) if self.emotion.get() == "Full Reset" else self.emotion_config().setdefault(self.phase.get(), [])

    def select(self, *_):
        self.editing = None
        self.add_button.configure(text="Add action")
        full = self.emotion.get() == "Full Reset"
        self.phase_menu.configure(state="disabled" if full else "normal")
        self.end_menu.configure(state="disabled" if full else "normal")
        if not full:
            self.end.set(self.emotion_config().get("after_speech", "idle"))
        self.render()

    def change_end(self, value):
        self.emotion_config()["after_speech"] = value

    def action_changed(self, *_):
        kind = self.action.get()
        self.hotkey_menu.configure(state="normal" if kind == "Hotkey" else "disabled")
        self.expression_menu.configure(state="normal" if kind.startswith("Expression") else "disabled")
        self.timing.configure(state="disabled" if kind == "Hotkey" else "normal")
        self.timing_label.configure(text="Delay seconds (0–5)" if kind == "Delay" else "Expression fade-in seconds (0–2)")

    def add_action(self):
        try:
            kind = self.action.get()
            if kind == "Hotkey":
                entry = self.manager.build_mapping_entry_from_name(self.hotkey.get())
                if not entry:
                    raise ValueError("Refresh hotkeys and choose one from the current model.")
                step = dict(entry, action="hotkey")
            elif kind == "Delay":
                step = {"action": "wait", "seconds": float(self.seconds.get())}
            else:
                step = {"action": "expression", "file": self.expression.get().strip(),
                        "active": kind == "Expression On", "fade": float(self.seconds.get())}
            sequence = self.sequence()
            proposed = list(sequence)
            if self.editing is None:
                proposed.append(step)
            else:
                proposed[self.editing] = step
            sequence[:] = validate_actions(proposed)
            self.select()
            self.status.configure(text="Draft updated. Save sequences to use it during conversation.")
        except (ValueError, TypeError) as exc:
            self.status.configure(text=str(exc))

    @staticmethod
    def describe(step):
        if step["action"] == "wait":
            return f"Wait {step['seconds']} seconds"
        if step["action"] == "expression":
            return f"Expression {'ON' if step['active'] else 'OFF'}: {step['file']}"
        return f"Hotkey: {step.get('name') or step.get('hotkey_id')}"

    def render(self):
        for child in self.rows.winfo_children():
            child.destroy()
        if not self.sequence():
            ctk.CTkLabel(self.rows, text="No actions. Add an expression, hotkey, or delay below.").pack(anchor="w")
        for index, step in enumerate(self.sequence()):
            row = ctk.CTkFrame(self.rows, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=f"{index + 1}. {self.describe(step)}", wraplength=420, justify="left").pack(side="left", expand=True, fill="x")
            for label, command in [("↑", lambda i=index: self.move(i, -1)), ("↓", lambda i=index: self.move(i, 1)),
                                   ("Edit", lambda i=index: self.edit(i)), ("Remove", lambda i=index: self.remove(i))]:
                ctk.CTkButton(row, text=label, width=65, command=command).pack(side="left", padx=3)

    def move(self, index, delta):
        sequence = self.sequence()
        other = index + delta
        if 0 <= other < len(sequence):
            sequence[index], sequence[other] = sequence[other], sequence[index]
        self.select()

    def remove(self, index):
        self.sequence().pop(index)
        self.select()

    def edit(self, index):
        step = self.sequence()[index]
        self.editing = index
        self.action.set({"hotkey": "Hotkey", "wait": "Delay"}.get(step["action"], "Expression On" if step.get("active") else "Expression Off"))
        self.hotkey.set(self.manager.display_name_for_mapping_entry(step) or step.get("name", ""))
        self.expression.set(step.get("file", ""))
        self.seconds.set(str(step.get("seconds", step.get("fade", 0.1))))
        self.action_changed()
        self.add_button.configure(text="Update action")

    def save(self):
        try:
            validate_actions(self.draft.get("full_reset", []))
            for config in self.draft.get("emotions", {}).values():
                for phase in ("talking", "idle", "reset"):
                    validate_actions(config.get(phase, []))
            with self.manager._storage_lock:
                mapping = self.manager.load_vts_mapping()
                mapping["advanced"] = copy.deepcopy(self.draft)
                self.manager.save_vts_mapping(mapping)
            self.status.configure(text="Advanced sequences saved.")
        except Exception as exc:
            self.status.configure(text=f"Save failed: {exc}")

    def refresh_expressions(self):
        async def refresh():
            try:
                expressions = await self.manager.list_expressions()
                names = [e['file'] for e in expressions]
                self.page._ui(lambda: self.expression_menu.configure(values=names or [""]))
                self.page._ui(lambda: self.status.configure(text=f"Found {len(names)} expressions. Choose one or enter its filename."))
            except Exception as exc:
                self.page._ui(lambda message=str(exc): self.status.configure(text=message))
        self.page._run_async_thread(refresh)

    def preview(self):
        from files.main_loop import main_loop
        if self.page._hotkey_test_in_progress() or main_loop.is_turn_active():
            self.status.configure(text="Wait for the active turn or preview to finish.")
            return
        actions = copy.deepcopy(self.sequence())
        phase = "reset" if self.emotion.get() == "Full Reset" else self.phase.get()
        reset_actions = (copy.deepcopy(self.emotion_config().get("reset", []))
                         if self.emotion.get() != "Full Reset" else [])
        async def run():
            try:
                await self.manager.advanced.preview(actions, phase, reset_actions)
                self.page._ui(lambda: self.status.configure(text="Preview finished; expressions activated by the preview were cleaned up."))
            except Exception as exc:
                self.page._ui(lambda message=str(exc): self.status.configure(text=f"Preview failed: {message}"))
        self.page._start_hotkey_test(run)
