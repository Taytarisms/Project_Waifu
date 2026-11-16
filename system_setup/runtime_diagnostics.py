from datetime import datetime, timezone
import faulthandler
import hashlib
import os
from pathlib import Path
import platform
import sys
import threading
import time
import traceback


class RuntimeDiagnostics:
    def __init__(self, directory=None, *, hang_seconds=15.0):
        root = Path(__file__).resolve().parents[1]
        directory = Path(directory) if directory else root / 'logs'
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        self.path = directory / f'Runtime_Diagnostics_{stamp}_{os.getpid()}.log'
        self.file = self.path.open('a', encoding='utf-8', buffering=1)
        self.hang_seconds = hang_seconds
        self.last_beat = time.monotonic()
        self.reported = False
        self.stopped = threading.Event()
        self.thread = None
        self.timer = None
        self.root = None
        self.last_dropped = 0
        self._write_lock = threading.Lock()
        self._owns_fault_handler = not faulthandler.is_enabled()
        if self._owns_fault_handler:
            faulthandler.enable(file=self.file, all_threads=True)
        from files.system_setup.app_version import APP_VERSION
        self.write(f'START app={APP_VERSION} python={sys.version} os={platform.platform()} pid={os.getpid()}')
        # Fingerprints distinguish local builds with the same display version.
        for relative in ('ui/app.py', 'ui/dispatch.py', 'ui/pages/logs.py', 'main_loop/main_loop.py', 'tts/FishTTS.py'):
            path = root / relative
            if path.is_file():
                self.write(f'BUILD {relative} sha256={hashlib.sha256(path.read_bytes()).hexdigest()}')

    def write(self, text):
        with self._write_lock:
            self.file.write(f'{datetime.now(timezone.utc).isoformat()} {text}\n')
            self.file.flush()

    def attach(self, root):
        self.root = root
        self._beat()
        self.thread = threading.Thread(target=self._watch, name='ui-hang-watchdog', daemon=True)
        self.thread.start()

    def _beat(self):
        if self.stopped.is_set():
            return
        self.last_beat = time.monotonic()
        self.timer = self.root.after(1000, self._beat)

    def check(self):
        dispatcher = getattr(self.root, '_ui_dispatcher', None)
        dropped = getattr(dispatcher, 'dropped', 0)
        if dropped != self.last_dropped:
            self.write(f'UI_QUEUE_OVERFLOW dropped_total={dropped}; disk logs are retained')
            self.last_dropped = dropped
        elapsed = time.monotonic() - self.last_beat
        if elapsed < self.hang_seconds:
            if self.reported:
                self.write('UI_RECOVERED')
            self.reported = False
        elif not self.reported:
            self.reported = True
            self.write(f'UI_UNRESPONSIVE heartbeat_age={elapsed:.1f}s')
            self.write('THREADS ' + repr([(t.ident, t.name) for t in threading.enumerate()]))
            faulthandler.dump_traceback(file=self.file, all_threads=True)
            self.file.flush()

    def _watch(self):
        while not self.stopped.wait(1.0):
            self.check()

    def exception(self, kind, exc_type, exc_value, exc_traceback):
        self.write(kind + '\n' + ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback)))

    def close(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        if self.root is not None and self.timer is not None:
            try:
                self.root.after_cancel(self.timer)
            except Exception:
                pass
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        if self.thread and self.thread.is_alive():
            return  # Keep the fault-handler file valid until process exit.
        self.write('STOP')
        if self._owns_fault_handler:
            faulthandler.disable()
        self.file.close()
