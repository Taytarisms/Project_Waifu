from dataclasses import dataclass
from typing import Callable, Optional
import threading
import time
import speech_recognition as sr

from files.system_setup.system_logger import Logger

@dataclass
class GoogleSTTConfig:
    language: str = "en-US"
    timeout: float = 0.5
    phrase_time_limit: float = 8.0
    adjust_ambient: bool = True
    energy_threshold: Optional[int] = None

class GoogleSTTCore:
    def __init__(self, input_device_index: Optional[int] = None, cfg: Optional[GoogleSTTConfig] = None):
        self.input_device_index = input_device_index
        self.cfg = cfg or GoogleSTTConfig()
        self.recognizer = sr.Recognizer()
        if self.cfg.energy_threshold is not None:
            self.recognizer.energy_threshold = int(self.cfg.energy_threshold)
            self.recognizer.dynamic_energy_threshold = False
        self.running = False
        self.paused = False
        self.on_text: Optional[Callable[[str, bool], None]] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._pause = threading.Event()

    def start(self, on_text: Callable[[str, bool], None]):
        if self.running:
            return
        self.running = True
        self.paused = False
        self.on_text = on_text
        self._stop.clear()
        self._pause.clear()

        self._worker_thread = threading.Thread(
            target=self._listen_loop,
            name="google_stt_listener",
            daemon=True,
        )
        self._worker_thread.start()

    def stop(self):
        self.running = False
        self._stop.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.5)
        self._worker_thread = None

    def pause(self):
        self.paused = True
        self._pause.set()

    def resume(self):
        self.paused = False
        self._pause.clear()

    def _listen_loop(self):
        try:
            device_index = self.input_device_index
            try:
                names = sr.Microphone.list_microphone_names()
            except Exception:
                names = []

            if device_index is not None:
                if names and 0 <= int(device_index) < len(names):
                    Logger.print(
                        f"Google STT input device [{device_index}]: {names[int(device_index)]}"
                    )
                else:
                    Logger.warn(
                        f"Google STT device index {device_index} is out of range for its audio "
                        f"backend (PyAudio sees {len(names)} devices). The 'List Input Devices' "
                        f"button uses sounddevice, which numbers devices differently than Google's "
                        f"PyAudio backend, so the index may not match. Falling back to the default "
                        f"microphone."
                    )
                    device_index = None

            with sr.Microphone(device_index=device_index) as source:
                if self.cfg.adjust_ambient:
                    try:
                        self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                    except Exception as e:
                        Logger.warn(f"Google STT ambient calibration failed: {e}")

                while not self._stop.is_set():
                    if self._pause.is_set():
                        time.sleep(0.05)
                        continue

                    try:
                        audio = self.recognizer.listen(
                            source,
                            timeout=max(0.1, float(self.cfg.timeout)),
                            phrase_time_limit=max(0.5, float(self.cfg.phrase_time_limit)),
                        )
                    except sr.WaitTimeoutError:
                        continue
                    except Exception as e:
                        Logger.warn(f"Google STT listen failed: {e}")
                        time.sleep(0.25)
                        continue

                    if self._stop.is_set() or self._pause.is_set():
                        continue

                    try:
                        text = self.recognizer.recognize_google(
                            audio,
                            language=self.cfg.language or "en-US",
                        ).strip()
                    except sr.UnknownValueError:
                        continue
                    except sr.RequestError as e:
                        Logger.warn(f"Google STT request failed: {e}")
                        time.sleep(1.0)
                        continue
                    except Exception as e:
                        Logger.warn(f"Google STT recognition failed: {e}")
                        continue

                    if text and self.on_text:
                        self.on_text(text, False)
        except Exception as e:
            Logger.warn(f"Google STT microphone failed: {e}")
        finally:
            self.running = False