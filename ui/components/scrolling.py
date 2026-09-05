import sys
import weakref

import customtkinter as ctk


_GLOBAL_SCROLL_SEQUENCES = {
    "<MouseWheel>",
    "<KeyPress-Shift_L>",
    "<KeyPress-Shift_R>",
    "<KeyRelease-Shift_L>",
    "<KeyRelease-Shift_R>",
}
_SCROLLABLES = weakref.WeakValueDictionary()
_NATIVE_WHEEL_CLASSES = {"Listbox", "Text", "Treeview"}


class AppScrollableFrame(ctk.CTkScrollableFrame):
    _DRAG_FRAME_MS = 16

    def __init__(self, *args, **kwargs):
        self._pending_scrollbar_args = None
        self._scrollbar_after_id = None
        self._scrollbar_redraw_after_id = None
        self.suppress_bindings = True
        super().__init__(*args, **kwargs)
        self.suppress_bindings = False

        self._scroll_view = (
            self._parent_canvas.xview
            if self._orientation == "horizontal"
            else self._parent_canvas.yview
        )
        self._scrollbar.configure(command=self._handle_scrollbar_command)
        self._scrollbar.bind(
            "<ButtonRelease-1>",
            self._finish_scrollbar_drag,
            add="+",
        )
        _SCROLLABLES[str(self._parent_canvas)] = self

    def bind_all(self, sequence=None, func=None, add=None):
        if (
            getattr(self, "suppress_bindings", False)
            and sequence in _GLOBAL_SCROLL_SEQUENCES
        ):
            return None
        return super().bind_all(sequence, func, add)

    def _handle_scrollbar_command(self, *args):
        if args and args[0] == "moveto":
            self._pending_scrollbar_args = args
            if self._scrollbar_after_id is None:
                self._scrollbar_after_id = self.after(
                    self._DRAG_FRAME_MS,
                    self._apply_pending_scrollbar,
                )
            return

        self._cancel_scrollbar_callback()
        self._apply_pending_scrollbar()
        self._scroll_view(*args)

    def _cancel_scrollbar_callback(self):
        if self._scrollbar_after_id is None:
            return
        try:
            self.after_cancel(self._scrollbar_after_id)
        except Exception:
            pass
        self._scrollbar_after_id = None

    def _apply_pending_scrollbar(self):
        self._scrollbar_after_id = None
        args = self._pending_scrollbar_args
        self._pending_scrollbar_args = None
        if args is not None and self.winfo_exists():
            self._scroll_view(*args)

    def _finish_scrollbar_drag(self, _event=None):
        self._cancel_scrollbar_callback()
        self._apply_pending_scrollbar()

        if self._scrollbar_redraw_after_id is not None:
            try:
                self.after_cancel(self._scrollbar_redraw_after_id)
            except Exception:
                pass
        self._scrollbar_redraw_after_id = self.after_idle(
            self._finish_scrollbar_redraw
        )

    def _finish_scrollbar_redraw(self):
        self._scrollbar_redraw_after_id = None
        if self.winfo_exists():
            self._parent_canvas.update_idletasks()

    def destroy(self):
        for after_id in (
            self._scrollbar_after_id,
            self._scrollbar_redraw_after_id,
        ):
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except Exception:
                    pass
        self._scrollbar_after_id = None
        self._scrollbar_redraw_after_id = None
        self._pending_scrollbar_args = None

        canvas = getattr(self, "_parent_canvas", None)
        if canvas is not None:
            _SCROLLABLES.pop(str(canvas), None)
        super().destroy()


def window_scroll(widget):
    current = widget
    while current is not None:
        frame = _SCROLLABLES.get(str(current))
        if frame is not None:
            return frame
        current = getattr(current, "master", None)
    return None


def _native_scroll_owns_wheel(widget, horizontal: bool = False) -> bool:
    current = widget
    view_name = "xview" if horizontal else "yview"
    while current is not None:
        try:
            if _SCROLLABLES.get(str(current)) is not None:
                return False
            widget_class = str(current.winfo_class())
        except Exception:
            widget_class = ""

        view = getattr(current, view_name, None)
        if widget_class in _NATIVE_WHEEL_CLASSES and callable(view):
            try:
                first, last = view()
                return float(first) > 0.0 or float(last) < 1.0
            except Exception:
                return False
        current = getattr(current, "master", None)
    return False


def set_bindings(root) -> None:
    if getattr(root, "app_scroll", False):
        return
    root.app_scroll = True
    shift_pressed = {"value": False}

    def set_shift(value):
        def handler(_event):
            shift_pressed["value"] = value
        return handler

    def on_mousewheel(event):
        event_widget = getattr(event, "widget", None)
        horizontal = shift_pressed["value"] or bool(
            getattr(event, "state", 0) & 0x0001
        )
        if _native_scroll_owns_wheel(event_widget, horizontal):
            return

        frame = window_scroll(event_widget)
        if frame is None:
            return

        canvas = frame._parent_canvas
        view = canvas.xview if horizontal else canvas.yview
        if view() == (0.0, 1.0):
            return

        if sys.platform.startswith("win"):
            delta = int(getattr(event, "delta", 0))
            units = -int(delta / 6) if delta else 0
        elif sys.platform == "darwin":
            units = -int(getattr(event, "delta", 0))
        else:
            event_number = getattr(event, "num", 0)
            units = -4 if event_number == 4 else 4

        if units:
            view("scroll", units, "units")

    root.bind_all("<MouseWheel>", on_mousewheel, add="+")
    root.bind_all("<Button-4>", on_mousewheel, add="+")
    root.bind_all("<Button-5>", on_mousewheel, add="+")
    root.bind_all("<KeyPress-Shift_L>", set_shift(True), add="+")
    root.bind_all("<KeyPress-Shift_R>", set_shift(True), add="+")
    root.bind_all("<KeyRelease-Shift_L>", set_shift(False), add="+")
    root.bind_all("<KeyRelease-Shift_R>", set_shift(False), add="+")
