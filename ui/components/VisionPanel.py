import io
import base64
import threading
from typing import Optional

import customtkinter as ctk
from PIL import Image

from files.ui import theme
from files.system_setup.system_logger import Logger
from files.system_setup.settings import save_settings

# Lazy-loaded vision references
_vision_loaded = False
_ctx = None
_watcher = None

PREVIEW_W  = 300
PREVIEW_H  = 170
REFRESH_MS = 1000


def _ensure_vision() -> bool:
    global _vision_loaded, _ctx, _watcher
    if _vision_loaded:
        return True
    try:
        from files.vision.VisionContext import get_shared_context
        from files.vision.VisionWatcher import get_watcher
        _ctx     = get_shared_context()
        _watcher = get_watcher()
        _vision_loaded = True
        return True
    except Exception as e:
        Logger.warn(f"Could not load vision modules: {e}")
        return False


class VisionPanel(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._preview_photo = None
        self._active = False
        ctk.CTkFrame(self, fg_color="#2d3f52", height=1).pack(fill="x", pady=(8, 0))

        ctk.CTkLabel(
            self, text="Screen Vision",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", pady=(12, 6))

        preview_border = ctk.CTkFrame(self, fg_color="#0e141b", corner_radius=8)
        preview_border.pack(fill="x", pady=(0, 6))

        self.preview_label = ctk.CTkLabel(
            preview_border, text="No frame captured",
            text_color=theme.MUTED_TEXT,
            font=ctk.CTkFont(size=9),
            width=PREVIEW_W, height=PREVIEW_H,
        )
        self.preview_label.pack(padx=4, pady=4)

        status_row = ctk.CTkFrame(self, fg_color="transparent")
        status_row.pack(fill="x", pady=(0, 6))

        self.status_dot = ctk.CTkLabel(
            status_row, text="\u25cf",
            text_color=theme.DANGER, font=ctk.CTkFont(size=10), width=14,
        )
        self.status_dot.pack(side="left")

        self.status_label = ctk.CTkLabel(
            status_row, text="Vision off",
            text_color=theme.MUTED_TEXT, font=ctk.CTkFont(size=10), anchor="w",
        )
        self.status_label.pack(side="left", fill="x", expand=True, padx=(4, 0))

        mode_frame = ctk.CTkFrame(self, fg_color="transparent")
        mode_frame.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            mode_frame, text="Mode",
            text_color=theme.TEXT, font=ctk.CTkFont(size=11, weight="bold"),
            width=50, anchor="w",
        ).pack(side="left")

        self.mode_menu = ctk.CTkSegmentedButton(
            mode_frame, values=["Off", "Reactive", "Passive"],
            command=self._on_mode_changed, font=ctk.CTkFont(size=10),
        )
        _initial_mode_label = "Off"
        if _ensure_vision():
            from files.vision.VisionWatcher import WatchMode as _WM
            _initial_mode_label = {
                _WM.DISABLED: "Off",
                _WM.REACTIVE: "Reactive",
                _WM.PASSIVE:  "Passive",
            }.get(_watcher.mode, "Off")
        self.mode_menu.set(_initial_mode_label)
        self.mode_menu.pack(side="left", fill="x", expand=True, padx=(6, 0))

        source_frame = ctk.CTkFrame(self, fg_color="transparent")
        source_frame.pack(fill="x", pady=(2, 4))

        ctk.CTkLabel(
            source_frame, text="Source",
            text_color=theme.TEXT, font=ctk.CTkFont(size=11, weight="bold"),
            width=50, anchor="w",
        ).pack(side="left")

        self.source_var = ctk.StringVar(value="Full Screen")
        self.source_menu = ctk.CTkOptionMenu(
            source_frame, values=self._build_source_list(),
            variable=self.source_var, command=self._on_source_changed,
            font=ctk.CTkFont(size=10), width=140,
        )
        self.source_menu.pack(side="left", padx=(6, 0))

        self.browse_btn = ctk.CTkButton(
            source_frame, text="\U0001f4c2", width=30,
            font=ctk.CTkFont(size=12),
            fg_color="#21262d", hover_color="#30363d",
            command=self._on_browse_image, state="disabled",
        )
        self.browse_btn.pack(side="left", padx=(4, 0))

        interval_frame = ctk.CTkFrame(self, fg_color="transparent")
        interval_frame.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            interval_frame, text="Interval",
            text_color=theme.MUTED_TEXT, font=ctk.CTkFont(size=10),
            width=50, anchor="w",
        ).pack(side="left")

        self.interval_var = ctk.StringVar(value="5.0")
        interval_entry = ctk.CTkEntry(
            interval_frame, textvariable=self.interval_var,
            width=50, font=ctk.CTkFont(size=10),
        )
        interval_entry.pack(side="left", padx=(6, 0))
        interval_entry.bind("<Return>",   self._on_interval_changed)
        interval_entry.bind("<FocusOut>", self._on_interval_changed)

        ctk.CTkLabel(
            interval_frame, text="sec  (passive mode)",
            text_color=theme.MUTED_TEXT, font=ctk.CTkFont(size=9),
        ).pack(side="left", padx=(4, 0))

        ctk.CTkButton(
            self, text="\U0001f4f7  Capture Now",
            font=ctk.CTkFont(size=11),
            fg_color=theme.ACCENT, hover_color="#5a9bd4",
            height=28, command=self._on_capture_now,
        ).pack(fill="x", pady=(2, 6))
        self._active = True
        self._refresh_tick()

    def _build_source_list(self) -> list:
        sources = ["Full Screen"]
        if _ensure_vision():
            from files.vision.VisionContext import VisionContext
            monitors = VisionContext.list_monitors()
            for i in range(1, len(monitors)):
                sources.append(f"Monitor {i}")
        sources += ["Region", "Image File"]
        return sources

    def _on_mode_changed(self, value: str) -> None:
        if not _ensure_vision():
            return
        from files.vision.VisionWatcher import WatchMode

        mode_map = {"Off": WatchMode.DISABLED, "Reactive": WatchMode.REACTIVE, "Passive": WatchMode.PASSIVE}
        mode = mode_map.get(value, WatchMode.DISABLED)

        # Persist the choice so the next launch starts in the same mode
        save_settings("vision_mode", {
            WatchMode.DISABLED: "off",
            WatchMode.REACTIVE: "reactive",
            WatchMode.PASSIVE:  "passive",
        }[mode])

        def _apply():
            _watcher.set_mode_sync(mode)
            if mode == WatchMode.PASSIVE:
                try:
                    _ctx.interval = float(self.interval_var.get() or 5.0)
                except ValueError:
                    pass
            Logger.print(f"[Vision] Mode \u2192 {mode.name}")

        threading.Thread(target=_apply, daemon=True).start()

    def _on_source_changed(self, value: str) -> None:
        if not _ensure_vision():
            return

        self.browse_btn.configure(state="normal" if value == "Image File" else "disabled")

        if value == "Full Screen":
            _ctx.set_source_fullscreen()
        elif value.startswith("Monitor "):
            try:
                _ctx.set_source_monitor(int(value.split()[-1]))
            except ValueError:
                _ctx.set_source_fullscreen()
        elif value == "Region":
            self._open_region_dialog()
        Logger.print(f"[Vision] Source \u2192 {_ctx.source_label}")

    def _on_browse_image(self) -> None:
        if not _ensure_vision():
            return
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Select image for vision",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.webp *.gif"), ("All", "*.*")],
        )
        if path:
            try:
                _ctx.set_source_image(path)
                self.source_var.set("Image File")
                Logger.print(f"[Vision] Source \u2192 {_ctx.source_label}")
            except FileNotFoundError as e:
                Logger.warn(f"[Vision] {e}")

    def _on_interval_changed(self, _event=None) -> None:
        if not _ensure_vision():
            return
        try:
            val = max(0.5, float(self.interval_var.get()))
            _ctx.interval = val
            self.interval_var.set(f"{val:.1f}")
        except ValueError:
            self.interval_var.set(f"{_ctx.interval:.1f}")

    def _on_capture_now(self) -> None:
        if not _ensure_vision():
            return
        def _do():
            try:
                _ctx.capture()
            except Exception as e:
                Logger.warn(f"[Vision] Capture error: {e}")
        threading.Thread(target=_do, daemon=True).start()

    def _open_region_dialog(self) -> None:
        if not _ensure_vision():
            return
        dlg = ctk.CTkToplevel(self)
        dlg.title("Set capture region")
        dlg.geometry("250x220")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.grab_set()

        entries = {}
        for i, name in enumerate(["Left", "Top", "Right", "Bottom"]):
            ctk.CTkLabel(dlg, text=name, font=ctk.CTkFont(size=11)).grid(
                row=i, column=0, padx=12, pady=6, sticky="e"
            )
            var = ctk.StringVar(value="0")
            ctk.CTkEntry(dlg, textvariable=var, width=80).grid(
                row=i, column=1, padx=12, pady=6
            )
            entries[name.lower()] = var

        def _apply():
            try:
                _ctx.set_source_bbox(
                    int(entries["left"].get()), int(entries["top"].get()),
                    int(entries["right"].get()), int(entries["bottom"].get()),
                )
                Logger.print(f"[Vision] Source \u2192 {_ctx.source_label}")
                dlg.destroy()
            except ValueError:
                pass

        ctk.CTkButton(dlg, text="Apply", command=_apply).grid(
            row=4, column=0, columnspan=2, pady=12
        )

    def _refresh_tick(self) -> None:
        if not self._active:
            return
        self._update_preview()
        self._update_status()
        self.app.after(REFRESH_MS, self._refresh_tick)

    def _update_preview(self) -> None:
        if not _ensure_vision() or _ctx.latest_frame is None:
            return
        try:
            frame = _ctx.latest_frame
            raw   = base64.b64decode(frame.b64)
            img   = Image.open(io.BytesIO(raw)).convert("RGB")
            img.thumbnail((PREVIEW_W, PREVIEW_H), Image.LANCZOS)

            photo = ctk.CTkImage(light_image=img, dark_image=img,
                                 size=(img.width, img.height))
            self.preview_label.configure(image=photo, text="")
            self._preview_photo = photo
        except Exception:
            pass

    def _update_status(self) -> None:
        if not _ensure_vision():
            return
        from files.vision.VisionWatcher import WatchMode

        mode  = _watcher.mode
        frame = _ctx.latest_frame

        colors = {
            WatchMode.DISABLED: theme.DANGER,
            WatchMode.REACTIVE: "#f9e2af",
            WatchMode.PASSIVE:  theme.SUCCESS,
        }
        self.status_dot.configure(text_color=colors.get(mode, theme.DANGER))

        parts = [mode.name.capitalize()]
        if frame:
            parts.append(f"age {frame.age_seconds:.0f}s")
        parts.append(f"src: {_ctx.source_label}")

        injects = _watcher.stats.get("inject_count", 0)
        if injects > 0:
            parts.append(f"{injects} injects")

        self.status_label.configure(text="  \u00b7  ".join(parts))

        mode_map = {WatchMode.DISABLED: "Off", WatchMode.REACTIVE: "Reactive", WatchMode.PASSIVE: "Passive"}
        expected = mode_map.get(mode, "Off")
        try:
            if self.mode_menu.get() != expected:
                self.mode_menu.set(expected)
        except Exception:
            pass

    def destroy(self) -> None:
        self._active = False
        super().destroy()