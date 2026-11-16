import queue
import threading
import time


class UIDispatcher:
    def __init__(self, root):
        self.root = root
        self.owner = threading.get_ident()
        self.pending = queue.Queue(maxsize=4096)
        self.closed = False
        self.dropped = 0
        self.timer = root.after(20, self._drain)

    def post(self, widget, callback, *args, **kwargs):
        if self.closed:
            return False
        try:
            self.pending.put_nowait((widget, callback, args, kwargs))
            return True
        except queue.Full:
            # This is to prevent a block during an active turn
            self.dropped += 1
            return False

    def _drain(self):
        self.timer = None
        deadline = time.monotonic() + 0.008
        for _ in range(128):
            if self.closed:
                return
            try:
                widget, callback, args, kwargs = self.pending.get_nowait()
            except queue.Empty:
                break
            try:
                if widget.winfo_exists():
                    callback(*args, **kwargs)
            except Exception:
                import sys
                self.root.report_callback_exception(*sys.exc_info())
            if time.monotonic() >= deadline:
                break
        if not self.closed:
            self.timer = self.root.after(20, self._drain)

    def close(self):
        self.closed = True
        if self.timer is not None:
            self.root.after_cancel(self.timer)
            self.timer = None
        while True:
            try:
                self.pending.get_nowait()
            except queue.Empty:
                break


_dispatcher = None


def install(root):
    global _dispatcher
    _dispatcher = UIDispatcher(root)
    return _dispatcher


def post(widget, callback, *args, **kwargs):
    dispatcher = _dispatcher
    return bool(dispatcher and dispatcher.post(widget, callback, *args, **kwargs))
