from pathlib import Path
import asyncio
import contextlib
import threading
import queue
import time
import numpy as np
import pyaudio
from typing import Optional
from files.system_setup.audio_tools import ffmpeg_cmd
from files.system_setup.settings import get_auth, get_settings
from files.system_setup.system_logger import Logger
from files.main_loop import turn_control

# You can read the docs here:
# https://docs.fish.audio/developer-guide/core-features/text-to-speech
# The REST endpoint returns mono PCM. Matching sample rates use a direct
# NumPy upmix; FFmpeg remains as a fallback for future rate conversions.

OUTPUT_DEVICE = None # Cable-B
CHUNK_DATA = 2048
PCM_FORMAT = pyaudio.paInt16
CHANNELS = 2
RATE = 24000
_CAPTION_CHARS_PER_SEC = 15.0

FISH_TOKEN = get_auth("fishspeech", "token")
MODEL = "s2-pro"
API_URL = "https://api.fish.audio/v1/tts"
voice_id = get_settings("fish_voice_id")
SUPPORTED_MODELS = {"s1", "s2-pro", "s2.1-pro", "s2.1-pro-free"}
_MODEL_ALIASES = {
    "s2": "s2-pro",
}


def request_interrupt() -> None:
    turn_control.request_interrupt()

def normalize_model(value: Optional[str]) -> str:
    requested = str(value or MODEL).strip().lower()
    normalized = _MODEL_ALIASES.get(requested, requested)
    if normalized not in SUPPORTED_MODELS:
        normalized = MODEL
    if requested != normalized:
        Logger.warn(
            f"FishSpeech model '{requested}' is not supported by the current API; "
            f"using '{normalized}'."
        )
    return normalized


def playback(pcm_queue: queue.Queue, started_at: Optional[float] = None):
    p = pyaudio.PyAudio()
    stream = None
    first_write = True

    try:
        stream = p.open(
            format=PCM_FORMAT,
            channels=CHANNELS,
            rate=RATE,
            output=True,
            output_device_index=OUTPUT_DEVICE,
        )
        while True:
            chunk = pcm_queue.get()
            try:
                if chunk is None or turn_control.is_interrupted():
                    return
                while turn_control.is_paused() and not turn_control.is_interrupted():
                    time.sleep(0.05)
                if turn_control.is_interrupted():
                    return
                if first_write:
                    first_write = False
                    if started_at is not None:
                        Logger.quiet_print(
                            "FishSpeech playback started in "
                            f"{(time.perf_counter() - started_at) * 1000:.0f} ms"
                        )
                stream.write(chunk)
            finally:
                pcm_queue.task_done()

    finally:
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.stop_stream()
            with contextlib.suppress(Exception):
                stream.close()
        with contextlib.suppress(Exception):
            p.terminate()


async def _close_stdin(proc) -> None:
    writer = getattr(proc, "stdin", None)
    if writer is None:
        return
    with contextlib.suppress(BrokenPipeError, ConnectionResetError, RuntimeError):
        if not writer.is_closing():
            writer.close()
    with contextlib.suppress(BrokenPipeError, ConnectionResetError, RuntimeError):
        await writer.wait_closed()


async def _stop_process(proc) -> None:
    await _close_stdin(proc)
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
    await asyncio.sleep(0)


def _convert_mono_pcm(data: bytes, channels: int) -> bytes:
    if channels == 1:
        return data
    if channels == 2:
        samples = np.frombuffer(data, dtype="<i2")
        return np.repeat(samples, 2).tobytes()
    raise RuntimeError(f"FishSpeech playback channels must be 1 or 2, got {channels}.")


async def fish_stream(
    message: str,
    voice_id: str,
    pcm_queue: queue.Queue,
    started_at: Optional[float] = None,
):
    fish_token = get_auth("fishspeech", "token") or FISH_TOKEN
    if not fish_token:
        raise RuntimeError("FishSpeech API token is missing.")

    import aiohttp

    raw_rate = int(get_settings("fish_rate") or RATE)
    valid_rates = {8000, 16000, 24000, 32000, 44100}
    RAW_RATE = raw_rate if raw_rate in valid_rates else 24000
    RAW_CHANNELS_IN = 1 # Most PCM endpoints
    RAW_FORMAT = "s16le"

    model = normalize_model(get_settings("fish_model") or MODEL)
    headers = {
        "model": model,
        "Authorization": f"Bearer {fish_token}",
        "Content-Type": "application/json"
    }

    chunk_length = max(100, min(300, int(get_settings("fish_chunk_length") or 300)))
    min_chunk_length = max(0, min(100, int(get_settings("fish_min_chunk_length") or 50)))
    latency = str(get_settings("fish_latency") or "normal").lower()
    if latency not in {"low", "balanced", "normal"}:
        latency = "normal"
    payload = {
        "text": message,
        "latency": latency,
        "chunk_length": chunk_length,
        "min_chunk_length": min_chunk_length,
        "condition_on_previous_chunks": bool(
            get_settings("fish_condition_previous_chunks")
        ),
        "format": "pcm",
        "normalize": bool(get_settings("fish_normalize")),
        "sample_rate": RAW_RATE
    }
    if voice_id:
        payload["reference_id"] = voice_id

    t0 = started_at if started_at is not None else time.perf_counter()
    total_out = 0
    net_chunk = 1024
    proc = None
    worker_tasks = []
    stderr_task = None
    stderr_data = b""

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    try:
        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
        ) as session:
            async with session.post(API_URL, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    body = (await resp.text()).strip()
                    raise RuntimeError(
                        f"FishSpeech API returned HTTP {resp.status}: "
                        f"{body[:500] or 'No error details were returned.'}"
                    )

                Logger.quiet_print(
                    "FishSpeech response headers received in "
                    f"{(time.perf_counter() - t0) * 1000:.0f} ms"
                )

                direct_pcm = RAW_RATE == RATE and CHANNELS in {1, 2}
                if direct_pcm:
                    first_chunk = True
                    carry = b""
                    seen = 0
                    async for chunk in resp.content.iter_any():
                        if turn_control.is_interrupted():
                            break
                        if not chunk:
                            continue
                        seen += 1
                        if seen <= 3:
                            Logger.quiet_print(
                                f"FishSpeech input chunk: {len(chunk)} bytes"
                            )
                        if first_chunk:
                            first_chunk = False
                            Logger.quiet_print(
                                "FishSpeech first audio bytes received in "
                                f"{(time.perf_counter() - t0) * 1000:.0f} ms"
                            )

                        aligned = carry + chunk
                        if len(aligned) % 2:
                            carry = aligned[-1:]
                            aligned = aligned[:-1]
                        else:
                            carry = b""
                        if not aligned:
                            continue

                        pcm = _convert_mono_pcm(aligned, CHANNELS)
                        total_out += len(pcm)
                        pcm_queue.put(pcm)
                else:
                    proc = await asyncio.create_subprocess_exec(
                        ffmpeg_cmd(),
                        "-hide_banner", "-loglevel", "error",
                        "-fflags", "nobuffer",
                        "-flags", "low_delay",
                        "-f", RAW_FORMAT,
                        "-ar", str(RAW_RATE),
                        "-ac", str(RAW_CHANNELS_IN),
                        "-probesize", "32",
                        "-analyzeduration", "0",
                        "-i", "pipe:0",
                        "-f", "s16le",
                        "-acodec", "pcm_s16le",
                        "-ar", str(RATE),
                        "-ac", str(CHANNELS),
                        "pipe:1",
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stderr_task = asyncio.create_task(proc.stderr.read())

                    async def feed_audio():
                        seen = 0
                        first_chunk = True
                        try:
                            async for chunk in resp.content.iter_any():
                                if turn_control.is_interrupted():
                                    break
                                if not chunk:
                                    continue
                                seen += 1
                                if seen <= 3:
                                    Logger.quiet_print(
                                        f"FishSpeech input chunk: {len(chunk)} bytes"
                                    )
                                if first_chunk:
                                    first_chunk = False
                                    Logger.quiet_print(
                                        "FishSpeech first audio bytes received in "
                                        f"{(time.perf_counter() - t0) * 1000:.0f} ms"
                                    )
                                proc.stdin.write(chunk)
                                await proc.stdin.drain()
                        finally:
                            await _close_stdin(proc)

                    async def read_feed():
                        nonlocal total_out
                        while True:
                            if turn_control.is_interrupted():
                                break
                            pcm = await proc.stdout.read(net_chunk)
                            if not pcm:
                                break
                            total_out += len(pcm)
                            pcm_queue.put(pcm)

                    worker_tasks = [
                        asyncio.create_task(feed_audio()),
                        asyncio.create_task(read_feed()),
                    ]
                    await asyncio.gather(*worker_tasks)

        if turn_control.is_interrupted():
            if proc is not None:
                await _stop_process(proc)
        elif proc is not None:
            return_code = await proc.wait()
            stderr_data = await stderr_task
            if return_code != 0:
                detail = stderr_data.decode("utf-8", errors="replace").strip()
                raise RuntimeError(
                    f"FishSpeech FFmpeg conversion failed ({return_code}): "
                    f"{detail or 'No FFmpeg error details were returned.'}"
                )
            if total_out <= 0:
                raise RuntimeError("FishSpeech returned no playable PCM audio.")
        elif total_out <= 0:
            raise RuntimeError("FishSpeech returned no playable PCM audio.")

        audio_seconds = total_out / max(1, RATE * CHANNELS * 2)
        Logger.quiet_print(
            f"FishSpeech streamed {total_out / 1024:.1f} KiB "
            f"({audio_seconds:.2f}s audio) in {(time.perf_counter() - t0):.2f}s"
        )
    finally:
        for task in worker_tasks:
            if not task.done():
                task.cancel()
        if worker_tasks:
            await asyncio.gather(*worker_tasks, return_exceptions=True)
        if proc is not None:
            await _stop_process(proc)
        if stderr_task is not None and not stderr_task.done():
            with contextlib.suppress(Exception):
                stderr_data = await stderr_task

async def run_tts(message: str, voice_id: Optional[str] = None):
    if turn_control.is_interrupted():
        return
    if not (get_auth("fishspeech", "token") or FISH_TOKEN):
        raise RuntimeError("FishSpeech API token is missing.")
    try:
        from files.closed_captions import caption_coordinator
        est_ms = max(500, int(len(message or "") / _CAPTION_CHARS_PER_SEC * 1000))
        caption_coordinator.audio_ready_with_duration(est_ms)
    except Exception as e:
        Logger.warn(f"Caption sync failed: {e}")

    started_at = time.perf_counter()
    pcm_queue = queue.Queue()
    playback_errors = []

    def playback_worker():
        try:
            playback(pcm_queue, started_at)
        except Exception as exc:
            playback_errors.append(exc)

    playback_thread = threading.Thread(
        target=playback_worker,
        daemon=True,
        name="fishspeech_playback",
    )
    playback_thread.start()
    try:
        await fish_stream(
            message=message,
            voice_id=voice_id,
            pcm_queue=pcm_queue,
            started_at=started_at,
        )
    except Exception as exc:
        Logger.error(f"FishSpeech TTS failed: {exc}")
        raise
    finally:
        pcm_queue.put(None)
        if playback_thread.is_alive():
            await asyncio.to_thread(playback_thread.join)

    if playback_errors:
        raise RuntimeError(f"FishSpeech playback failed: {playback_errors[0]}")

if __name__ == "__main__":
    async def main():
        text = "[surprised] Can you believe what's going on right now? I was made in under two hours!"
        output = await run_tts(text)
        return output
    while True:
        asyncio.run(main())
