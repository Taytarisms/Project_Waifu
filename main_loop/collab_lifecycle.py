import threading


class CollabLifecycle:
    def __init__(self, client, connect, warn):
        self.client = client
        self._connect = connect
        self._warn = warn
        self._lock = threading.Lock()
        self._enabled = False
        self._revision = 0
        self._ready_revision = -1
        self._thread = None

    @property
    def active(self):
        with self._lock:
            return (self._enabled and self._ready_revision == self._revision
                    and self.client.connected)

    def start(self):
        self._set_enabled(True)

    def stop(self):
        self._set_enabled(False)

    def _set_enabled(self, enabled):
        with self._lock:
            if self._enabled == enabled:
                return
            self._enabled = enabled
            self._revision += 1
            self._ready_revision = -1
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="collab-lifecycle", daemon=True)
                self._thread.start()

    def _run(self):
        while True:
            with self._lock:
                revision, enabled = self._revision, self._enabled
            try:
                self.client.disconnect()
                if enabled:
                    self._connect()
            except Exception as exc:
                self._warn(f"Collab transition failed ({type(exc).__name__}); stop/start to retry.")
                try:
                    self.client.disconnect()
                except Exception:
                    pass
            with self._lock:
                if revision == self._revision:
                    self._ready_revision = revision if enabled else -1
                    self._thread = None
                    return