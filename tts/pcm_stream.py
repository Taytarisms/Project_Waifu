import queue
import struct
import threading


class PCMDecoder:
    def __init__(self, rate):
        self.rate = rate
        self.buffer = bytearray()
        self.state = "detect"
        self.format_seen = False
        self.headers_removed = 0

    def feed_event(self, data):
        if (self.state == "pcm" and len(data) >= 12
                and data[:4] == b"RIFF" and data[8:12] == b"WAVE"):
            if self.buffer:
                raise ValueError("New WAV event follows an incomplete PCM sample")
            self.state = "detect"
            self.format_seen = False
        return self.feed(data)

    def feed(self, data):
        self.buffer.extend(data)
        if self.state == "detect":
            if len(self.buffer) < 12:
                return b""
            if self.buffer[:4] == b"RIFF":
                if self.buffer[8:12] != b"WAVE":
                    raise ValueError("Invalid WAV stream")
                del self.buffer[:12]
                self.headers_removed += 1
                self.state = "header"
            else:
                self.state = "pcm"
        while self.state == "header":
            if len(self.buffer) < 8:
                return b""
            tag = self.buffer[:4]
            size = int.from_bytes(self.buffer[4:8], "little")
            if tag == b"data":
                if not self.format_seen:
                    raise ValueError("WAV data precedes format")
                del self.buffer[:8]
                self.state = "pcm"
                break
            if size > 65536:
                raise ValueError("Oversized WAV header")
            end = 8 + size + size % 2
            if len(self.buffer) < end:
                return b""
            if tag == b"fmt ":
                if size < 16:
                    raise ValueError("Invalid WAV format")
                kind, channels, rate, _, align, bits = struct.unpack_from("<HHIIHH", self.buffer, 8)
                if (kind, channels, rate, align, bits) != (1, 1, self.rate, 2, 16):
                    raise ValueError("Expected mono 16-bit PCM at requested sample rate")
                self.format_seen = True
            del self.buffer[:end]
        size = len(self.buffer) // 2 * 2
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def finish(self):
        if self.state == "detect" and self.buffer and not self.buffer.startswith(b"RIFF"):
            self.state = "pcm"
            return self.feed(b"") + self.finish()
        if self.buffer or self.state != "pcm":
            raise ValueError("Truncated PCM/WAV stream")
        return b""


def prefetched(source, check, stop):
    items = queue.Queue(maxsize=8)
    ended = threading.Event()
    def put(item):
        while not ended.is_set():
            try:
                items.put(item, timeout=0.05)
                return
            except queue.Full:
                pass
    def produce():
        try:
            for block in source:
                if ended.is_set():
                    break
                put((block, None))
        except Exception as exc:
            put((None, exc))
        finally:
            put((None, None))
    worker = threading.Thread(target=produce, daemon=True, name="InWorld audio receive")
    worker.start()
    try:
        while True:
            check()
            try:
                block, error = items.get(timeout=0.05)
            except queue.Empty:
                continue
            if error:
                raise error
            if block is None:
                break
            yield block
    finally:
        ended.set()
        stop()
        worker.join(timeout=1)
