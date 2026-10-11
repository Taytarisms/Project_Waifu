import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError


def split_sentences(text):
    # Require whitespace after punctuation: leave decimals, URLs and initials intact.
    parts = re.split(r'(?<=[.!?。！？])\s+', str(text or '').strip())
    sentences = []
    current = ''
    for part in parts:
        current = f'{current} {part}'.strip()
        if re.search(r'\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|e\.g|i\.e|[A-Z])\.$', current):
            continue
        if current:
            sentences.append(current)
        current = ''
    if current:
        sentences.append(current)
    return sentences


def run_pipeline(sentences, prepare, play, check_interrupt, report=lambda *_: None):
    if not sentences:
        return False
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='tts_prepare')
    future = None
    try:
        check_interrupt()
        future = executor.submit(prepare, sentences[0])
        for index, sentence in enumerate(sentences):
            waiting = time.perf_counter()
            while True:
                check_interrupt()
                try:
                    audio = future.result(timeout=0.05)
                    break
                except TimeoutError:
                    if future.done():
                        audio = future.result()
                        break
            check_interrupt()
            if audio is None or audio is False or len(audio) == 0:
                raise RuntimeError(f'TTS returned no audio for sentence {index + 1}')
            report('First segment ready' if index == 0 else 'TTS segment wait',
                   time.perf_counter() - waiting)
            future = (executor.submit(prepare, sentences[index + 1])
                      if index + 1 < len(sentences) else None)
            play(audio, sentence, index)
            check_interrupt()
        return True
    finally:
        if future is not None:
            future.cancel()
        # Blocking SDK calls may finish later, but can only prepare audio, never play it.
        executor.shutdown(wait=False, cancel_futures=True)
