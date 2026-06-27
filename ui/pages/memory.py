import customtkinter as ctk
from pathlib import Path
from tkinter import filedialog
from files.ui import theme

try:
    from files.memory import chromadb as memory_backend
except Exception as e:
    print(f"Failed to import memory backend: {e}")
    memory_backend = None

try:
    from files.memory import w_ai_fu_importer
except Exception as e:
    print(f"Failed to import w-AI-fu importer: {e}")
    w_ai_fu_importer = None

try:
    from files.system_setup.settings import get_settings, save_settings
except Exception:
    get_settings = None
    save_settings = None

SPEAKER_OPTIONS = ["any", "user", "assistant"]
SCOPE_OPTIONS = ["any", "chatter", "global"]
SOURCE_OPTIONS = ["any", "chat", "twitch", "discord", "system", "ui"]
MEMORY_TYPE_OPTIONS = ["any", "user_fact", "assistant_response", "ai_self", "preference", "context", "chat"]

class MemoryPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=20)
        left = ctk.CTkScrollableFrame(body, fg_color=theme.CARD_BG, corner_radius=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        right_outer = ctk.CTkFrame(
            body,
            fg_color=theme.CARD_BG,
            corner_radius=10,
            width=380,
        )
        right_outer.pack(side="right", fill="y", padx=(10, 0))
        right_outer.pack_propagate(False)
        right = ctk.CTkScrollableFrame(
            right_outer,
            fg_color="transparent",
            corner_radius=10,
        )
        right.pack(fill="both", expand=True)
        self._build_recall_panel(left)
        self._build_live_recall_panel(right)
        self._build_import_panel(right)
        self._build_store_panel(right)

    def _build_recall_panel(self, parent):
        ctk.CTkLabel(
            parent,
            text="Memory Recall",
            font=ctk.CTkFont(size=28, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=18, pady=(18, 4))

        ctk.CTkLabel(
            parent,
            text="Semantic search with metadata filters.",
            text_color=theme.MUTED_TEXT,
        ).pack(anchor="w", padx=18, pady=(0, 14))

        self.recall_query_var = ctk.StringVar(value="")
        self.recall_user_id_var = ctk.StringVar(value="")
        self.recall_n_results_var = ctk.StringVar(value="5")
        self.recall_max_distance_var = ctk.StringVar(value="1.0")

        self._entry_row(parent, "Query", self.recall_query_var)
        self._entry_row(parent, "User ID", self.recall_user_id_var)
        self._entry_row(parent, "Results", self.recall_n_results_var)
        self._entry_row(parent, "Max Distance", self.recall_max_distance_var)

        self.recall_speaker_var = ctk.StringVar(value="any")
        self.recall_scope_var = ctk.StringVar(value="any")
        self.recall_source_var = ctk.StringVar(value="any")
        self.recall_tag_var = ctk.StringVar(value="")

        self._dropdown_row(parent, "Speaker", self.recall_speaker_var, SPEAKER_OPTIONS)
        self._dropdown_row(parent, "Scope", self.recall_scope_var, SCOPE_OPTIONS)
        self._dropdown_row(parent, "Source", self.recall_source_var, SOURCE_OPTIONS)
        self._entry_row(parent, "Tag", self.recall_tag_var)

        self.recall_after_var = ctk.StringVar(value="")
        self.recall_before_var = ctk.StringVar(value="")

        self._entry_row(parent, "After (UTC)", self.recall_after_var)
        self._entry_row(parent, "Before (UTC)", self.recall_before_var)

        btn_frame = ctk.CTkFrame(parent, fg_color="transparent")
        btn_frame.pack(fill="x", padx=18, pady=(14, 4))

        ctk.CTkButton(
            btn_frame,
            text="Recall Memories",
            command=self.recall_memories,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_frame,
            text="Clear Results",
            command=lambda: self._write_results(""),
            fg_color="gray30",
        ).pack(side="left")

        self.results_box = ctk.CTkTextbox(
            parent,
            height=360,
            fg_color="#0e141b",
            text_color=theme.TEXT,
            wrap="word",
        )
        self.results_box.pack(fill="both", expand=True, padx=18, pady=(8, 18))

    def _build_live_recall_panel(self, parent):
        ctk.CTkLabel(
            parent,
            text="Live Conversation Recall",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 8))

        ctk.CTkLabel(
            parent,
            text="Max semantic distance used while live chat is generating replies.",
            text_color=theme.MUTED_TEXT,
            font=ctk.CTkFont(size=12),
            wraplength=320,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 8))

        current = "1.0"
        if get_settings:
            try:
                current = str(get_settings("live_memory_recall_max_distance") or "1.0")
            except Exception:
                current = "1.0"
        self.live_recall_distance_var = ctk.StringVar(value=current)
        self._entry_row(parent, "Max Distance", self.live_recall_distance_var, pad=16)

        ctk.CTkButton(
            parent,
            text="Save Live Recall",
            command=self.save_live_recall_settings,
        ).pack(fill="x", padx=16, pady=(4, 10))

        ctk.CTkFrame(parent, fg_color="#2d3f52", height=1).pack(
            fill="x", padx=16, pady=(4, 0)
        )

    def _build_import_panel(self, parent):
        ctk.CTkLabel(
            parent,
            text="Import Memories",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 8))

        ctk.CTkLabel(
            parent,
            text="Import contextual memories and VectorDB long-term memories from a w-AI-fu folder or zip.",
            text_color=theme.MUTED_TEXT,
            font=ctk.CTkFont(size=12),
            wraplength=320,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 8))

        self.import_path_var = ctk.StringVar(value="")
        self._entry_row(parent, "Path", self.import_path_var, pad=16)

        ctk.CTkButton(
            parent,
            text="Choose w-AI-fu Folder",
            command=self.choose_w_ai_fu_folder,
            fg_color="gray30",
        ).pack(fill="x", padx=16, pady=4)

        ctk.CTkButton(
            parent,
            text="Choose w-AI-fu Zip",
            command=self.choose_w_ai_fu_zip,
            fg_color="gray30",
        ).pack(fill="x", padx=16, pady=4)

        ctk.CTkButton(
            parent,
            text="Preview Import",
            command=self.preview_w_ai_fu_import,
        ).pack(fill="x", padx=16, pady=(8, 4))

        ctk.CTkButton(
            parent,
            text="Import w-AI-fu / VectorDB Memories",
            command=self.import_w_ai_fu_memories,
        ).pack(fill="x", padx=16, pady=4)

        ctk.CTkFrame(parent, fg_color="#2d3f52", height=1).pack(
            fill="x", padx=16, pady=(12, 0)
        )

    def _build_store_panel(self, parent):
        ctk.CTkLabel(
            parent,
            text="Add Memory",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 8))

        self.store_user_id_var = ctk.StringVar(value="")
        self.store_speaker_var = ctk.StringVar(value="user")
        self.store_scope_var = ctk.StringVar(value="chatter")
        self.store_memory_type_var = ctk.StringVar(value="user_fact")
        self.store_source_var = ctk.StringVar(value="ui")
        self.store_tags_var = ctk.StringVar(value="")

        self._entry_row(parent, "User ID", self.store_user_id_var, pad=16)
        self._dropdown_row(parent, "Speaker", self.store_speaker_var, ["user", "assistant"], pad=16)
        self._dropdown_row(parent, "Scope", self.store_scope_var, ["chatter", "global"], pad=16)
        self._dropdown_row(parent, "Type", self.store_memory_type_var,
                           ["user_fact", "assistant_response", "ai_self", "preference", "context", "chat"], pad=16)
        self._dropdown_row(parent, "Source", self.store_source_var,
                           ["ui", "chat", "twitch", "discord", "system"], pad=16)
        self._entry_row(parent, "Tags", self.store_tags_var, pad=16)

        ctk.CTkLabel(
            parent,
            text="(comma-separated, e.g. preference,game)",
            text_color=theme.MUTED_TEXT,
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=16, pady=(0, 6))

        ctk.CTkLabel(
            parent,
            text="Memory Text",
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(10, 4))

        self.memory_textbox = ctk.CTkTextbox(
            parent,
            height=180,
            fg_color="#0e141b",
            text_color=theme.TEXT,
            wrap="word",
        )
        self.memory_textbox.pack(fill="x", padx=16, pady=(0, 10))

        ctk.CTkButton(
            parent,
            text="Store Memory",
            command=self.store_memory,
        ).pack(fill="x", padx=16, pady=4)

        ctk.CTkButton(
            parent,
            text="Clear Editor",
            command=self.clear_editor,
            fg_color="gray30",
        ).pack(fill="x", padx=16, pady=4)

        self.status_label = ctk.CTkLabel(
            parent,
            text="Memory backend ready." if memory_backend else "Memory backend unavailable.",
            text_color=theme.MUTED_TEXT,
            wraplength=320,
            justify="left",
        )
        self.status_label.pack(anchor="w", padx=16, pady=(12, 18))

    def _entry_row(self, parent, label, var, pad=18):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=pad, pady=5)

        ctk.CTkLabel(
            frame,
            text=label,
            text_color=theme.TEXT,
            width=130,
            anchor="w",
        ).pack(side="left")

        ctk.CTkEntry(
            frame,
            textvariable=var,
        ).pack(side="left", fill="x", expand=True, padx=(10, 0))

    def _dropdown_row(self, parent, label, var, options, pad=18):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=pad, pady=5)

        ctk.CTkLabel(
            frame,
            text=label,
            text_color=theme.TEXT,
            width=130,
            anchor="w",
        ).pack(side="left")

        ctk.CTkOptionMenu(
            frame,
            variable=var,
            values=options,
            width=160,
        ).pack(side="left", padx=(10, 0))

    def _set_status(self, text, **kwargs):
        label = getattr(self, "status_label", None)
        if label is None:
            return
        try:
            if label.winfo_exists():
                label.configure(text=text, **kwargs)
        except Exception:
            return

    def recall_memories(self):
        if not memory_backend:
            self._write_results("Memory backend unavailable.")
            return

        query = self.recall_query_var.get().strip()
        if not query:
            self._write_results("Enter a query first.")
            return

        try:
            n_results = int(self.recall_n_results_var.get().strip() or 5)
            max_distance = float(self.recall_max_distance_var.get().strip() or 1.0)
        except ValueError:
            self._write_results("Results must be an integer and Max Distance must be a number.")
            return

        kwargs = {
            "query": query,
            "n_results": n_results,
            "max_distance": max_distance,
        }

        user_id = self.recall_user_id_var.get().strip()
        if user_id:
            kwargs["user_id"] = user_id

        speaker = self.recall_speaker_var.get()
        if speaker != "any":
            kwargs["speaker"] = speaker

        scope = self.recall_scope_var.get()
        if scope != "any":
            kwargs["scope"] = scope

        source = self.recall_source_var.get()
        if source != "any":
            kwargs["source"] = source

        tag = self.recall_tag_var.get().strip()
        if tag:
            kwargs["tag"] = tag

        after = self.recall_after_var.get().strip()
        if after:
            kwargs["after"] = after

        before = self.recall_before_var.get().strip()
        if before:
            kwargs["before"] = before

        try:
            memories = memory_backend.recall_memories(**kwargs)
        except Exception as e:
            self._write_results(f"Recall failed:\n{e}")
            return

        if not memories:
            self._write_results("No memories found.")
            return

        self._write_results(self._format_results(memories))

    def save_live_recall_settings(self):
        if not save_settings:
            self._set_status("Settings backend unavailable.")
            return
        try:
            max_distance = float(self.live_recall_distance_var.get().strip() or 1.0)
        except ValueError:
            self._set_status("Live recall max distance must be a number.")
            return
        max_distance = max(0.0, max_distance)
        self.live_recall_distance_var.set(str(max_distance))
        save_settings("live_memory_recall_max_distance", max_distance)
        self._set_status(f"Live recall max distance saved: {max_distance}.")

    def choose_w_ai_fu_folder(self):
        path = filedialog.askdirectory(title="Choose w-AI-fu folder")
        if path:
            self.import_path_var.set(path)

    def choose_w_ai_fu_zip(self):
        path = filedialog.askopenfilename(
            title="Choose w-AI-fu zip",
            filetypes=[("Zip archives", "*.zip"), ("All files", "*.*")],
        )
        if path:
            self.import_path_var.set(path)

    def _import_path(self) -> Path | None:
        raw = self.import_path_var.get().strip()
        if not raw:
            self._set_status("Choose a w-AI-fu folder or zip first.")
            return None
        path = Path(raw)
        if not path.exists():
            self._set_status("Selected import path does not exist.")
            return None
        return path

    def preview_w_ai_fu_import(self):
        if not w_ai_fu_importer:
            self._set_status("w-AI-fu importer unavailable.")
            return
        path = self._import_path()
        if path is None:
            return
        try:
            preview = w_ai_fu_importer.preview_import(path)
        except Exception as e:
            self._set_status(f"Preview failed: {e}")
            return

        lines = [
            "w-AI-fu import preview",
            f"Config: {preview.config_path}",
            f"Contextual memories: {preview.contextual_count}",
            f"VectorDB memories: {preview.vectordb_count}",
            f"VectorDB database: {preview.database_path or 'not found'}",
            f"Character files found: {preview.character_count}",
            "",
        ]
        if preview.sample:
            lines.append("Sample:")
            for item in preview.sample:
                lines.append(f"- {item}")
        else:
            lines.append("No importable memories found.")
        self._write_results("\n".join(lines))
        self._set_status(
            "Preview found "
            f"{preview.contextual_count} contextual and "
            f"{preview.vectordb_count} VectorDB memories."
        )

    def import_w_ai_fu_memories(self):
        if not memory_backend:
            self._set_status("Memory backend unavailable.")
            return
        if not w_ai_fu_importer:
            self._set_status("w-AI-fu importer unavailable.")
            return
        path = self._import_path()
        if path is None:
            return
        try:
            result = w_ai_fu_importer.import_contextual_memories(path)
        except Exception as e:
            self._set_status(f"Import failed: {e}")
            return

        summary = (
            "w-AI-fu import complete\n"
            f"Config: {result.config_path}\n"
            f"VectorDB database: {result.database_path or 'not found'}\n"
            f"Found: {result.found}\n"
            f"Contextual found: {result.contextual_found}\n"
            f"VectorDB found: {result.vectordb_found}\n"
            f"Imported: {result.imported}\n"
            f"Skipped duplicates: {result.skipped}\n"
            f"Failed: {result.failed}"
        )
        self._write_results(summary)
        self._set_status(
            f"Imported {result.imported}; skipped {result.skipped}; failed {result.failed}."
        )

    def store_memory(self):
        if not memory_backend:
            self._set_status("Memory backend unavailable.")
            return

        text = self.memory_textbox.get("0.0", "end-1c").strip()
        if not text:
            self._set_status("Enter memory text first.")
            return

        user_id = self.store_user_id_var.get().strip()
        speaker = self.store_speaker_var.get()
        scope = self.store_scope_var.get()
        memory_type = self.store_memory_type_var.get()
        source = self.store_source_var.get()
        if scope == "global":
            user_id = "__global__"
        elif not user_id:
            self._set_status("User ID is required for chatter-scoped memories.")
            return
        raw_tags = self.store_tags_var.get().strip()
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()] if raw_tags else None
        try:
            ok = memory_backend.add_memory(
                text=text,
                user_id=user_id,
                speaker=speaker,
                scope=scope,
                memory_type=memory_type,
                source=source,
                tags=tags,
            )
        except Exception as e:
            self._set_status(f"Store failed: {e}")
            return

        if ok:
            self._set_status("Memory stored successfully.")
        else:
            self._set_status("Memory was not stored (duplicate or empty).")

    def clear_editor(self):
        self.memory_textbox.delete("0.0", "end")
        self.store_tags_var.set("")
        self._set_status("Editor cleared.")

    def _write_results(self, text):
        self.results_box.delete("0.0", "end")
        self.results_box.insert("0.0", text or "")

    @staticmethod
    def _format_results(memories: list) -> str:
        lines = []
        for i, mem in enumerate(memories, 1):
            meta = mem.get("metadata", {})
            dist = mem.get("distance")

            header_parts = [f"[{i}]"]
            speaker = meta.get("speaker", "?")
            scope = meta.get("scope", "?")
            header_parts.append(f"{speaker}/{scope}")

            user_id = meta.get("user_id", "")
            if user_id and user_id != "__global__":
                header_parts.append(f"user={user_id}")

            source = meta.get("source", "")
            if source:
                header_parts.append(f"src={source}")

            tags = meta.get("tags", "")
            if tags:
                header_parts.append(f"tags=[{tags}]")

            if dist is not None:
                header_parts.append(f"dist={dist:.3f}")

            created = meta.get("created_at", "")
            if created:
                display_time = created[:19].replace("T", " ")
                header_parts.append(display_time)

            lines.append(" ".join(header_parts))
            lines.append(f"  {mem.get('text', '')}")
            lines.append("")

        return "\n".join(lines).rstrip()
