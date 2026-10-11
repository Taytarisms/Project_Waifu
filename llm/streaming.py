import time
import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class StreamEvent:
    kind: str
    text: str = ""
    elapsed: float = 0.0


class GenerationStream:
    def __init__(self, callback: Callable[[StreamEvent], None]):
        self.callback = callback
        self.started = time.perf_counter()
        self.first_event = False
        self.first_answer = False
        self.answer = ""
        self.first_phrase = False

    def emit(self, kind, text=""):
        elapsed = time.perf_counter() - self.started
        if kind in ("answer", "reasoning", "activity") and not self.first_event:
            self.first_event = True
            self.callback(StreamEvent("first_event", elapsed=elapsed))
        if kind == "answer" and text.strip() and not self.first_answer:
            self.first_answer = True
            self.callback(StreamEvent("first_answer", elapsed=elapsed))
        if kind == "answer":
            self.answer += text
            if not self.first_phrase and re.search(r"[.!?](?:\s|$)|\n", self.answer):
                self.first_phrase = True
                self.callback(StreamEvent("first_phrase", elapsed=elapsed))
        self.callback(StreamEvent(kind, text, elapsed))


async def collect_gemini_stream(client, model, contents, config, events):
    import asyncio
    from google.genai import types

    # Create the retry budget here, rather than stacking it on SDK retries.
    config.http_options = types.HttpOptions(
        timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1)
    )
    for attempt in range(2):
        parts, answers, thoughts = [], [], []
        usage = None
        stream = None
        received = False
        try:
            async with asyncio.timeout(120):
                stream = await client.aio.models.generate_content_stream(
                    model=model, contents=contents, config=config
                )
                async for chunk in stream:
                    received = True
                    events.emit("activity")
                    if chunk.usage_metadata is not None:
                        usage = chunk.usage_metadata
                    candidates = chunk.candidates or []
                    content = candidates[0].content if candidates else None
                    for part in getattr(content, "parts", None) or []:
                        parts.append(part)
                        if part.text:
                            kind = "reasoning" if part.thought else "answer"
                            (thoughts if part.thought else answers).append(part.text)
                            events.emit(kind, part.text)
            text = "".join(answers).strip()
            if not text:
                raise RuntimeError("Gemini completed without answer text (possibly blocked or token-limited).")
            events.emit("complete")
            return text, "".join(thoughts).strip(), types.Content(role="model", parts=parts), usage
        except Exception as exc:
            # Never replay an in-progress stream, even if only thinking arrived.
            if attempt == 0 and not received and getattr(exc, "code", None) in (429, 500, 502, 503, 504):
                events.emit("retry", "Gemini temporarily unavailable; retrying once.")
                await asyncio.sleep(1)
                continue
            if isinstance(exc, TimeoutError):
                exc = RuntimeError("Gemini stream exceeded the 120-second generation deadline.")
                events.emit("failure", str(exc))
                raise exc
            events.emit("failure", str(exc))
            raise
        finally:
            if stream is not None and hasattr(stream, "aclose"):
                await stream.aclose()
