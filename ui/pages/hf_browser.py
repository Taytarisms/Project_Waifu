import threading
import customtkinter as ctk
from pathlib import Path
from files.ui import theme

try:
    from files.system_setup.model_checker import (
        fetch_hf_models,
        fetch_gguf_files,
        download_gguf,
        detect_vram,
        get_gpu_name,
        get_compatibility,
        estimate_vram_gb,
    )
    _checker_ok = True
except Exception:
    _checker_ok = False

try:
    from files.system_setup.settings import save_settings
except Exception:
    save_settings = None

_COMPAT_COLOURS = {
    "compatible":    "#238636",
    "tight":         "#b08800",
    "non-compliant": "#b62324",
    "unknown":       "#4a4a4a",
}
_COMPAT_LABELS = {
    "compatible":    "✔  Compatible",
    "tight":         "⚠  Tight",
    "non-compliant": "✖  Non-compliant",
    "unknown":       "?  Unknown",
}

def _format_gb(value, estimated: bool = False) -> str:
    if value is None:
        return "unknown"
    prefix = "~" if estimated else ""
    return f"{prefix}{float(value):.1f} GB"


def _infer_family_from_filename(filename: str) -> str:
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

class _ModelCard(ctk.CTkFrame):
    def __init__(self, master, model: dict, on_download):
        super().__init__(
            master,
            fg_color="#161b22",
            corner_radius=10,
            border_width=1,
            border_color="#30363d",
        )
        self.model       = model
        self.on_download = on_download

        self.grid_columnconfigure(0, weight=1)

        name_row = ctk.CTkFrame(self, fg_color="transparent")
        name_row.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 4))
        name_row.grid_columnconfigure(0, weight=1)

        short_name = model["model_id"].split("/")[-1]
        ctk.CTkLabel(
            name_row,
            text=short_name,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#e6edf3",
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            name_row,
            text=f"by {model['author']}",
            font=ctk.CTkFont(size=11),
            text_color="#8b949e",
        ).grid(row=1, column=0, sticky="w")

        badge_row = ctk.CTkFrame(self, fg_color="transparent")
        badge_row.grid(row=1, column=0, sticky="w", padx=14, pady=(2, 6))

        compat = model.get("compatibility", "unknown")
        ctk.CTkLabel(
            badge_row,
            text=_COMPAT_LABELS.get(compat, "? Unknown"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=_COMPAT_COLOURS.get(compat, "#4a4a4a"),
        ).pack(side="left", padx=(0, 10))

        if model.get("has_vision"):
            ctk.CTkLabel(
                badge_row,
                text="👁  Supports Vision",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color="#58a6ff",
            ).pack(side="left", padx=(0, 10))

        if model.get("params_b"):
            ctk.CTkLabel(
                badge_row,
                text=f"{model['params_b']:.0f}B",
                font=ctk.CTkFont(size=11),
                text_color="#8b949e",
            ).pack(side="left", padx=(0, 10))

        if model.get("vram_estimate"):
            ctk.CTkLabel(
                badge_row,
                text=f"~{model['vram_estimate']:.1f} GB VRAM",
                font=ctk.CTkFont(size=11),
                text_color="#8b949e",
            ).pack(side="left")

        stats_row = ctk.CTkFrame(self, fg_color="transparent")
        stats_row.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 10))

        dl  = model.get("downloads", 0)
        lk  = model.get("likes", 0)
        dl_fmt = f"{dl:,}" if dl < 1_000_000 else f"{dl/1_000_000:.1f}M"

        ctk.CTkLabel(
            stats_row,
            text=f"⬇ {dl_fmt}   ♥ {lk}",
            font=ctk.CTkFont(size=11),
            text_color="#8b949e",
        ).pack(side="left")

        self._dl_btn = ctk.CTkButton(
            self,
            text="Download GGUF",
            height=30,
            font=ctk.CTkFont(size=12),
            fg_color="#238636",
            hover_color="#2ea043",
            command=self._start_download,
        )
        self._dl_btn.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 14))

        # Progress bar (hidden until download starts)
        self._progress = ctk.CTkProgressBar(self, mode="determinate")
        self._progress_label = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="#8b949e",
        )

    def _alive(self) -> bool:
        try:
            return self.winfo_exists()
        except Exception:
            return False

    def _start_download(self):
        self._dl_btn.configure(text="Fetching file list…", state="disabled")
        threading.Thread(target=self._fetch_and_pick, daemon=True).start()

    def _fetch_and_pick(self):
        files = fetch_gguf_files(self.model["model_id"]) if _checker_ok else []
        def _show():
            if not self._alive():
                return
            self._show_file_picker(files)
        self.after(0, _show)

    def _show_file_picker(self, files: list):
        if not files:
            self._dl_btn.configure(text="No GGUF files found", state="disabled")
            return
        
        self._picker = _FilePickerDialog(self, self.model["model_id"], files, self._on_file_chosen)
        self._dl_btn.configure(text="Download GGUF", state="normal")

    def _on_file_chosen(self, file_info: dict):
        self._dl_btn.grid_remove()
        self._progress.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 4))
        self._progress_label.grid(row=4, column=0, sticky="w", padx=14, pady=(0, 14))
        self._progress.set(0)

        threading.Thread(
            target=self._do_download,
            args=(file_info,),
            daemon=True,
        ).start()

    def _do_download(self, file_info: dict):
        def _progress(downloaded, total):
            if total and self._alive():
                pct = downloaded / total
                self.after(0, lambda p=pct: self._alive() and self._progress.set(p))
                mb_d = downloaded / (1024 ** 2)
                mb_t = total      / (1024 ** 2)
                self.after(0, lambda d=mb_d, t=mb_t: self._alive() and self._progress_label.configure(
                    text=f"{d:.0f} MB / {t:.0f} MB"
                ))

        dest = download_gguf(
            model_id=self.model["model_id"],
            filename=file_info["filename"],
            url=file_info["url"],
            on_progress=_progress,
        )

        if dest:
            def _done():
                if not self._alive():
                    return
                self._progress.set(1.0)
                self._progress_label.configure(text=f"✔  Saved to models/{dest.parent.name}/")
                if save_settings:
                    save_settings("local_model_filename", file_info["filename"])
                    save_settings("local_model_family", _infer_family_from_filename(file_info["filename"]))
                    try:
                        from files.llm.LocalLLM import reset_local_model
                        reset_local_model()
                    except Exception:
                        pass
                if self.on_download:
                    self.on_download(dest)
            self.after(0, _done)
        else:
            def _fail():
                if not self._alive():
                    return
                self._progress_label.configure(text="✖  Download failed.")
                self._progress.grid_remove()
                self._dl_btn.configure(text="Retry Download", state="normal")
                self._dl_btn.grid()
            self.after(0, _fail)

class _FilePickerDialog(ctk.CTkToplevel):
    def __init__(self, parent, model_id: str, files: list, on_choose):
        owner = parent
        root = parent.winfo_toplevel()
        super().__init__(root)
        self._owner = owner
        self._file_scroll = None
        self.title("Choose quantisation")
        self.geometry("620x480")
        self.resizable(False, False)
        self.configure(fg_color="#0d1117")
        self.transient(root)
        self.grab_set()
        self.on_choose = on_choose

        ctk.CTkLabel(
            self,
            text=f"Select a GGUF file",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#e6edf3",
        ).pack(anchor="w", padx=20, pady=(18, 4))

        ctk.CTkLabel(
            self,
            text=model_id,
            font=ctk.CTkFont(size=11),
            text_color="#8b949e",
        ).pack(anchor="w", padx=20, pady=(0, 6))

        available_vram = detect_vram() if _checker_ok else 0
        gpu_text = (
            f"Detected GPU VRAM: {available_vram:.1f} GB"
            if available_vram
            else "Detected GPU VRAM: unknown"
        )
        ctk.CTkLabel(
            self,
            text=gpu_text,
            font=ctk.CTkFont(size=11),
            text_color="#8b949e",
        ).pack(anchor="w", padx=20, pady=(0, 10))

        scroll = ctk.CTkScrollableFrame(self, fg_color="#161b22", corner_radius=8)
        scroll.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        self._file_scroll = scroll
        self.bind("<MouseWheel>", self._on_dialog_mousewheel, add="+")
        self.bind("<Button-4>", self._on_dialog_mousewheel, add="+")
        self.bind("<Button-5>", self._on_dialog_mousewheel, add="+")

        for f in files:
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", padx=(0, 4), pady=6)
            row.grid_columnconfigure(0, weight=1)

            size_gb = f.get("size_gb")
            size_is_estimate = bool(f.get("size_is_estimate"))
            size_text = _format_gb(size_gb, size_is_estimate)
            vram_est = estimate_vram_gb(size_gb) if size_gb else None

            # Compatibility for this specific file
            compat_text = ""
            compat_colour = "#4a4a4a"
            if vram_est and available_vram:
                compat   = get_compatibility(vram_est, available_vram)
                compat_text   = f"  {_COMPAT_LABELS.get(compat, '')}"
                compat_colour = _COMPAT_COLOURS.get(compat, "#4a4a4a")

            ctk.CTkLabel(
                row,
                text=f["filename"],
                font=ctk.CTkFont(size=12),
                text_color="#e6edf3",
                anchor="w",
                wraplength=420,
                justify="left",
            ).grid(row=0, column=0, sticky="ew")

            info_row = ctk.CTkFrame(row, fg_color="transparent")
            info_row.grid(row=1, column=0, sticky="w")

            ctk.CTkLabel(
                info_row,
                text=f"Disk: {size_text}",
                font=ctk.CTkFont(size=11),
                text_color="#8b949e",
            ).pack(side="left")

            ctk.CTkLabel(
                info_row,
                text=f"  VRAM: {_format_gb(vram_est, True) if vram_est else 'unknown'}",
                font=ctk.CTkFont(size=11),
                text_color="#8b949e",
            ).pack(side="left")

            if f.get("role"):
                ctk.CTkLabel(
                    info_row,
                    text=f"  {f['role']}",
                    font=ctk.CTkFont(size=11, weight="bold"),
                    text_color="#d29922",
                ).pack(side="left")

            if compat_text:
                ctk.CTkLabel(
                    info_row,
                    text=compat_text,
                    font=ctk.CTkFont(size=11, weight="bold"),
                    text_color=compat_colour,
                ).pack(side="left")

            ctk.CTkButton(
                row,
                text="Select",
                width=72,
                height=26,
                font=ctk.CTkFont(size=11),
                fg_color="#238636",
                hover_color="#2ea043",
                command=lambda fi=f: self._choose(fi),
            ).grid(row=0, column=1, rowspan=2, padx=(10, 0))

        ctk.CTkButton(
            self,
            text="Cancel",
            fg_color="#21262d",
            hover_color="#30363d",
            command=self.destroy,
        ).pack(fill="x", padx=20, pady=(0, 18))

        self.update_idletasks()
        px = self._owner.winfo_rootx() + (self._owner.winfo_width()  - self.winfo_width())  // 2
        py = self._owner.winfo_rooty() + (self._owner.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{px}+{py}")

    def _on_dialog_mousewheel(self, event):
        scroll = self._file_scroll
        canvas = getattr(scroll, "_parent_canvas", None)
        if canvas is None or canvas.yview() == (0.0, 1.0):
            return "break"

        if getattr(event, "num", None) == 4:
            units = -3
        elif getattr(event, "num", None) == 5:
            units = 3
        else:
            delta = getattr(event, "delta", 0)
            units = -int(delta / 120) if delta else 0
            if units == 0 and delta:
                units = -1 if delta > 0 else 1
            units *= 3

        if units:
            canvas.yview_scroll(units, "units")
        return "break"

    def _choose(self, file_info: dict):
        self.destroy()
        self.on_choose(file_info)

class HFModelBrowserPage(ctk.CTkFrame):
    def __init__(self, master, app=None):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 8))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header,
            text="HuggingFace Model Browser",
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=theme.TEXT,
        ).grid(row=0, column=0, sticky="w")

        # GPU info
        if _checker_ok:
            try:
                vram  = detect_vram()
                gname = get_gpu_name()
                gpu_text = f"{gname}  •  {vram:.1f} GB VRAM"
            except Exception:
                gpu_text = ""
        else:
            gpu_text = "model_checker unavailable"

        ctk.CTkLabel(
            header,
            text=gpu_text,
            font=ctk.CTkFont(size=12),
            text_color=theme.MUTED_TEXT,
        ).grid(row=1, column=0, sticky="w")

        # Back button
        ctk.CTkButton(
            header,
            text="← Back",
            width=80,
            fg_color="gray30",
            command=self._go_back,
        ).grid(row=0, column=2, rowspan=2, padx=(12, 0))

        search_row = ctk.CTkFrame(self, fg_color="transparent")
        search_row.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 12))
        search_row.grid_columnconfigure(0, weight=1)

        self._search_var = ctk.StringVar(value="")
        search_entry = ctk.CTkEntry(
            search_row,
            textvariable=self._search_var,
            placeholder_text="Search models (e.g. Llama, Mistral, Qwen)…",
            height=36,
            fg_color="#0d1117",
            border_color="#30363d",
            text_color="#e6edf3",
        )
        search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        search_entry.bind("<Return>", lambda _: self._do_search())

        ctk.CTkButton(
            search_row,
            text="Search",
            width=90,
            height=36,
            fg_color="#238636",
            hover_color="#2ea043",
            command=self._do_search,
        ).grid(row=0, column=1)

        self._vision_only = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            search_row,
            text="Vision models only",
            variable=self._vision_only,
            text_color=theme.MUTED_TEXT,
            fg_color="#58a6ff",
            checkmark_color="#ffffff",
            command=self._do_search,
        ).grid(row=0, column=2, padx=(12, 0))

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 16))
        self._scroll.grid_columnconfigure((0, 1), weight=1)

        self._status = ctk.CTkLabel(
            self,
            text="",
            text_color=theme.MUTED_TEXT,
            font=ctk.CTkFont(size=12),
        )
        self._status.grid(row=3, column=0, pady=(0, 12))

        # Load defaults on open
        self._do_search()

    def _do_search(self):
        query = self._search_var.get().strip() or "GGUF"
        self._status.configure(text="Searching HuggingFace…")

        # Clear existing cards just so the Ui isn't being overwhelmed
        for w in self._scroll.winfo_children():
            w.destroy()

        threading.Thread(
            target=self._fetch,
            args=(query,),
            daemon=True,
        ).start()

    def _fetch(self, query: str):
        if not _checker_ok:
            self.after(0, lambda: self._status.configure(text="model_checker not available."))
            return

        models = fetch_hf_models(query=query, limit=30)

        if self._vision_only.get():
            models = [m for m in models if m.get("has_vision")]

        self.after(0, lambda: self._render_cards(models))

    def _render_cards(self, models: list):
        if not models:
            self._status.configure(text="No models found.")
            return

        self._status.configure(text=f"{len(models)} models found")

        for i, model in enumerate(models):
            card = _ModelCard(
                self._scroll,
                model=model,
                on_download=self._on_downloaded,
            )
            card.grid(
                row=i // 2,
                column=i % 2,
                sticky="nsew",
                padx=8,
                pady=8,
            )

    def _on_downloaded(self, path: Path):
        self._status.configure(text=f"✔  Downloaded to {path.parent.name}/")

    def _go_back(self):
        if self.app and hasattr(self.app, "show_page"):
            self.app.show_page("LLM")
        elif self.app and hasattr(self.app, "go_back"):
            self.app.go_back()
