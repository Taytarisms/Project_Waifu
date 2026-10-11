import asyncio
from contextlib import aclosing
from files.llm.streaming import GenerationStream


async def bounded_stream(provider, callback, consume):
    events = GenerationStream(callback)
    for attempt in range(2):
        try:
            async with asyncio.timeout(120):
                result = await consume(events)
            events.emit("complete")
            return result
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if attempt == 0 and not events.first_event and status in (429, 500, 502, 503, 504, 529):
                events.emit("retry", f"{provider} temporarily unavailable; retrying once.")
                await asyncio.sleep(1)
                continue
            if isinstance(exc, TimeoutError):
                raise RuntimeError(f"{provider} exceeded the 120-second generation deadline.") from exc
            events.emit("failure", str(exc))
            raise


async def openai_stream(client, kwargs, callback):
    client = client.with_options(max_retries=0)
    request = dict(kwargs, stream=True, timeout=120.0)

    async def consume(events):
        try:
            stream = await client.responses.create(**request)
        except Exception as exc:
            reason = request.get("reasoning", {})
            if getattr(exc, "status_code", None) not in (400, 403) or "summary" not in reason or not any(
                s in str(exc).lower() for s in ("summary", "verification")
            ):
                raise
            request["reasoning"] = {k: v for k, v in reason.items() if k != "summary"}
            events.emit("retry", "OpenAI reasoning summary unavailable; retrying without it.")
            stream = await client.responses.create(**request)
        final = None
        async with stream:
            async for event in stream:
                events.emit("activity")
                if event.type == "response.output_text.delta":
                    events.emit("answer", event.delta)
                elif event.type == "response.reasoning_summary_text.delta":
                    events.emit("reasoning", event.delta)
                elif event.type == "response.completed":
                    final = event.response
                elif event.type in ("response.failed", "response.incomplete", "error"):
                    raise RuntimeError(f"OpenAI stream ended with {event.type}.")
        if final is None or not str(final.output_text or "").strip():
            raise RuntimeError("OpenAI stream ended without a completed answer.")
        return final
    return await bounded_stream("OpenAI", callback, consume)


async def claude_stream(client, kwargs, callback):
    client = client.with_options(max_retries=0, timeout=120.0)

    async def consume(events):
        stopped = False
        async with client.messages.stream(**kwargs) as stream:
            async for event in stream:
                events.emit("activity")
                if event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        events.emit("answer", delta.text)
                    elif delta.type == "thinking_delta":
                        events.emit("reasoning", delta.thinking)
                elif event.type == "message_stop":
                    stopped = True
            final = await stream.get_final_message()
        if not stopped or not any(getattr(b, "text", "").strip() for b in final.content):
            raise RuntimeError("Claude stream ended without a completed text answer.")
        return final  # SDK retains thinking signatures and usage tokens
    return await bounded_stream("Claude", callback, consume)


async def grok_stream(chat, callback):
    async def consume(events):
        final = None
        async with aclosing(chat.stream()) as stream:
            async for response, chunk in stream:
                events.emit("activity")
                if chunk.content:
                    events.emit("answer", chunk.content)
                if getattr(chunk, "reasoning_content", ""):
                    events.emit("reasoning", chunk.reasoning_content)
                final = response
        if final is None or not final.content.strip() or getattr(final, "finish_reason", "") not in ("REASON_STOP", "REASON_MAX_LEN"):
            raise RuntimeError("Grok stream ended without a completed answer.")
        return final
    return await bounded_stream("Grok", callback, consume)
