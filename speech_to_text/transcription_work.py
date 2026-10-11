from dataclasses import dataclass
import threading
import time


@dataclass
class Work:
    audio: object
    epoch: int
    utterance: int
    queued_at: float


class WorkGate:
    def __init__(self):
        self.lock = threading.RLock()
        self.epoch = 0
        self.utterance = 0
        self.accepting = True

    def invalidate(self):
        with self.lock:
            self.epoch += 1
            self.accepting = False

    def resume(self):
        with self.lock:
            self.accepting = True

    def make(self, audio, *, partial=False):
        with self.lock:
            if not self.accepting:
                return None
            work = Work(audio, self.epoch, self.utterance, time.perf_counter())
            if not partial:
                self.utterance += 1
            return work

    def valid(self, work, *, partial=False):
        return work.epoch == self.epoch and (not partial or work.utterance == self.utterance)

    def deliver(self, work, text, callback, *, partial=False):
        with self.lock:
            if self.valid(work, partial=partial) and text and callback:
                callback(text, partial)


def drain(jobs):
    import queue
    while True:
        try:
            jobs.get_nowait()
        except queue.Empty:
            return
