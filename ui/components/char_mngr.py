import json
from pathlib import Path
import customtkinter as ctk
from files.ui import theme


class NovelAICharacterManager(ctk.CTkFrame):
    FIELDS = {
        "name": "Name",
        "description": "Description",
        "personality": "Personality",
        "greeting": "Greeting",
        "mes_example": "Message Example",
        "voice": "Voice",
    }

    def __init__(self, master):
        super().__init__(master, fg_color=theme.CARD_BG, corner_radius=10)

        self.characters_dir = Path(__file__).resolve().parents[2] / "characters"
        self.characters_dir.mkdir(parents=True, exist_ok=True)

        self.inputs = {}

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="NovelAI Character",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=theme.TEXT,
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 8))

        self.character_var = ctk.StringVar(value="Select character")

        self.character_combo = ctk.CTkComboBox(
            self,
            variable=self.character_var,
            values=self._character_files(),
            width=230,
        )
        self.character_combo.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 8))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=2, column=0, sticky="ew", padx=16, pady=6)
        btn_row.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            btn_row, text="Load", command=self.load_character
        ).grid(row=0, column=0, padx=2, pady=2, sticky="ew")

        ctk.CTkButton(
            btn_row, text="Save", command=self.save_character
        ).grid(row=0, column=1, padx=2, pady=2, sticky="ew")

        ctk.CTkButton(
            btn_row, text="Clear", command=self.clear_form
        ).grid(row=1, column=0, padx=2, pady=2, sticky="ew")

        ctk.CTkButton(
            btn_row,
            text="Delete",
            fg_color=theme.DANGER,
            hover_color="#922b21",
            command=self.delete_character,
        ).grid(row=1, column=1, padx=2, pady=2, sticky="ew")

        row = 3
        for key, label in self.FIELDS.items():
            ctk.CTkLabel(
                self,
                text=label,
                text_color=theme.TEXT,
                anchor="w",
            ).grid(row=row, column=0, sticky="w", padx=16, pady=(10, 2))
            row += 1

            if key in ("description", "personality", "greeting", "mes_example"):
                box = ctk.CTkTextbox(
                    self,
                    height=58 if key != "mes_example" else 76,
                    fg_color="#0e141b",
                    text_color=theme.TEXT,
                    wrap="word",
                )
                box.grid(row=row, column=0, sticky="ew", padx=16, pady=(0, 4))
            else:
                box = ctk.CTkEntry(self, fg_color="#0e141b", text_color=theme.TEXT)
                box.grid(row=row, column=0, sticky="ew", padx=16, pady=(0, 4))

            self.inputs[key] = box
            row += 1

        self.status_label = ctk.CTkLabel(
            self,
            text="No character loaded.",
            text_color=theme.MUTED_TEXT,
            anchor="w",
        )
        self.status_label.grid(row=row, column=0, sticky="w", padx=16, pady=(10, 14))

    def _character_files(self):
        files = sorted(self.characters_dir.glob("*.json"))
        return [file.name for file in files] or ["No characters found"]

    def _selected_path(self):
        name = self.character_var.get().strip()

        if name in ("", "Select character", "No characters found"):
            return None

        if not name.endswith(".json"):
            name += ".json"

        return self.characters_dir / name

    def _get_value(self, key):
        widget = self.inputs[key]

        if isinstance(widget, ctk.CTkTextbox):
            return widget.get("0.0", "end-1c").strip()

        return widget.get().strip()

    def _set_value(self, key, value):
        widget = self.inputs[key]
        value = "" if value is None else str(value)

        if isinstance(widget, ctk.CTkTextbox):
            widget.delete("0.0", "end")
            widget.insert("0.0", value)
        else:
            widget.delete(0, "end")
            widget.insert(0, value)

    def refresh_character_list(self):
        self.character_combo.configure(values=self._character_files())

    def load_character(self):
        path = self._selected_path()

        if not path or not path.exists():
            self.status_label.configure(text="No valid character selected.")
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            self.status_label.configure(text=f"Failed to load JSON: {e}")
            return

        for key in self.FIELDS:
            self._set_value(key, data.get(key, ""))

        self.status_label.configure(text=f"Loaded {path.name}.")

    def save_character(self):
        path = self._selected_path()

        if not path:
            name = self._get_value("name")
            if not name:
                self.status_label.configure(text="Enter a name or filename first.")
                return
            path = self.characters_dir / f"{name}.json"

        data = {key: self._get_value(key) for key in self.FIELDS}

        try:
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            self.status_label.configure(text=f"Failed to save: {e}")
            return

        self.character_var.set(path.name)
        self.refresh_character_list()
        self.status_label.configure(text=f"Saved {path.name}.")

    def clear_form(self):
        for key in self.FIELDS:
            self._set_value(key, "")

        self.status_label.configure(text="Editor cleared. File was not changed.")

    def delete_character(self):
        path = self._selected_path()

        if not path or not path.exists():
            self.status_label.configure(text="No character file exists to delete.")
            return

        deleted_name = path.name
        path.unlink()

        self.clear_form()
        self.character_var.set("Select character")
        self.refresh_character_list()
        self.status_label.configure(text=f"Deleted {deleted_name}.")