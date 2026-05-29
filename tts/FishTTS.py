from pathlib import Path
import asyncio
import threading
import queue
import pyaudio
from typing import Optional
from files.system_setup.audio_tools import ffmpeg_cmd
from files.system_setup.settings import get_auth, get_settings
from files.system_setup.system_logger import Logger
from files.main_loop import turn_control

# You can read the docs here:
# https://docs.fish.audio/developer-guide/core-features/text-to-speech
# Please note that I'm NOT using the SDK, rather I'm feeding in from ffmpeg from the API stream response

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


def request_interrupt() -> None:
    turn_control.request_interrupt()

async def playback(pcm_queue: queue.Queue):
    first_play = True
    p = pyaudio.PyAudio()
    stream = p.open(
        format=PCM_FORMAT,
        channels=CHANNELS,
        rate=RATE,
        output=True,
        output_device_index=OUTPUT_DEVICE
    )

    try:
        bytes_per_ms = RATE * CHANNELS * 2 / 1000.0
        buffered_ms = 0.0
        BUFFER_TARGET = 80 # Targeted ms
        
        while buffered_ms < BUFFER_TARGET:
            if turn_control.is_interrupted():
                return
            chunk = pcm_queue.get()
            if chunk is None:
                pcm_queue.task_done()
                return
            buffered_ms += len(chunk) / bytes_per_ms
            stream.write(chunk)
            pcm_queue.task_done()

        while True:
            if turn_control.is_interrupted():
                break
            chunk = pcm_queue.get()
            if chunk is None:
                pcm_queue.task_done()
                break
            if first_play:
                first_play = False
            stream.write(chunk)
            pcm_queue.task_done()

    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

async def fish_stream(message: str, voice_id: str, pcm_queue: queue.Queue):
    fish_token = get_auth("fishspeech", "token") or FISH_TOKEN
    import time, aiohttp

    RAW_RATE = RATE # 44100
    RAW_CHANNELS_IN = 1 # Most PCM endpoints
    RAW_FORMAT = "s16le"

    headers = {
        "model": MODEL,
        "Authorization": f"Bearer {fish_token}",
        "Content-Type": "application/json"
    }

    payload = {
        "text": message,
        "reference_id": voice_id,
        "latency": "low",
        "chunk_length": 200,
        "min_chunk_length": 50,
        "condition_on_previous_chunks": True,
        "format": "pcm",
        "normalize": True,
        "sample_rate": RAW_RATE
    }

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
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
        stdout=asyncio.subprocess.PIPE
    )

    t0 = time.perf_counter()
    first = True
    total_out = 0
    net_chunk = 1024

    async with aiohttp.ClientSession() as session:
        async with session.post(API_URL, json=payload, headers=headers) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Fish Response: {resp.status}: {body}")
            
            async def feed_audio():
                nonlocal first
                seen = 0
                async for chunk in resp.content.iter_chunked(net_chunk):
                    if turn_control.is_interrupted():
                        break
                    if not chunk:
                        continue
                    seen += 1
                    if seen <= 3:
                        print(f"Chunks Yielded: {len(chunk)}" )
                    proc.stdin.write(chunk)
                    await proc.stdin.drain()
                
                proc.stdin.close()
                await proc.stdin.wait_closed()

            async def read_feed():
                nonlocal total_out
                out_seen = 0
                while True:
                    if turn_control.is_interrupted():
                        break
                    pcm = await proc.stdout.read(net_chunk)
                    if not pcm:
                        break
                    out_seen += 1
                    if out_seen <= 3:
                        print(f"Chunk Info: {len(pcm)}")
                    total_out += len(pcm)
                    pcm_queue.put(pcm)

            await asyncio.gather(feed_audio(), read_feed())

    await proc.wait()
    pcm_queue.put(None)
    print(f"Streamed {total_out/1024:.1f} KiB in {(time.perf_counter()-t0):.2f}s")

async def run_tts(message: str, voice_id: Optional[str] = None):
    if turn_control.is_interrupted():
        return
    try:
        from files.closed_captions import caption_coordinator
        est_ms = max(500, int(len(message or "") / _CAPTION_CHARS_PER_SEC * 1000))
        caption_coordinator.audio_ready_with_duration(est_ms)
    except Exception as e:
        Logger.warn(f"Caption sync failed: {e}")

    pcm_queue = queue.Queue()
    playback_thread = threading.Thread(
        target=lambda: asyncio.run(playback(pcm_queue)),
        daemon=True
    )
    playback_thread.start()
    try:
        await fish_stream(message=message, voice_id=voice_id, pcm_queue=pcm_queue)
    finally:
        await asyncio.to_thread(pcm_queue.join)
        if playback_thread.is_alive():
            playback_thread.join(timeout=2.0)

if __name__ == "__main__":
    async def main():
        text = "[surprised] Can you believe what's going on right now? I was made in under two hours!"
        output = await run_tts(text)
        return output
    while True:
        asyncio.run(main())
