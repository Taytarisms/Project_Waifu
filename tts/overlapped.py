import asyncio
from pathlib import Path
import time

from files.main_loop import turn_control
from files.system_setup.settings import get_settings, get_auth
from files.system_setup.system_logger import Logger
from files.tts.sentence_pipeline import run_pipeline, split_sentences

ENGINES = {'edge', 'elevenlabs', 'openai_tts', 'novelai_tts'}


def make_preparer(engine):
    if engine == 'edge':
        from files.tts.EdgeTTS import read_chat, VOICE
        voice = get_settings('edge_voice') or VOICE
        return lambda text: asyncio.run(read_chat(text, prepare_only=True, voice=voice))
    if engine == 'elevenlabs':
        from files.tts.elevenlabs_tts import synthesize
        voice = get_settings('eleven_voice_id') or get_settings('eleven_voice')
        return lambda text: synthesize(text, voice, prepare_only=True)
    if engine == 'openai_tts':
        from files.tts.openai_tts import openai_tts
        model = get_settings('openai_tts_model') or 'gpt-4o-mini-tts'
        voice = get_settings('openai_tts_voice') or 'nova'
        return lambda text: openai_tts(text, model, voice, prepare_only=True)
    if engine == 'novelai_tts':
        from files.tts.novel_ai_tts import NovelTTSClient
        from pydub import AudioSegment
        client = NovelTTSClient(get_auth('novelai', 'token') or get_settings('NOVELAI_TOKEN'))
        voice = get_settings('novel_tts_voice_seed') or 'Ligeia'
        version = get_settings('novel_tts_version') or 'v2'
        def prepare(text):
            file_id = asyncio.run(client.generate_tts(text, voice, version))
            path = Path(client.audio_path_for(file_id, 'mp3'))
            try:
                return AudioSegment.from_mp3(path).apply_gain(10)
            finally:
                path.unlink(missing_ok=True)
        return prepare
    raise ValueError(f'Unsupported sentence TTS engine: {engine}')


def play_segment(audio, sentence, index, device, first_audio):
    import pyaudio
    from files.closed_captions import caption_coordinator, SOURCE_LLM
    pa = pyaudio.PyAudio()
    stream = None
    try:
        turn_control.raise_if_interrupted()
        stream = pa.open(format=pa.get_format_from_width(audio.sample_width),
                         channels=audio.channels, rate=audio.frame_rate,
                         output=True, output_device_index=device)
        while turn_control.is_paused():
            turn_control.raise_if_interrupted()
            time.sleep(0.025)
        try:
            caption_coordinator.set_pending(sentence, source=SOURCE_LLM)
            caption_coordinator.audio_ready_with_duration(len(audio))
        except Exception as exc:
            Logger.warn(f'Sentence caption setup failed: {exc}')
        first = True
        for offset in range(0, len(audio), 25):
            turn_control.raise_if_interrupted()
            while turn_control.is_paused():
                turn_control.raise_if_interrupted()
                time.sleep(0.025)
            stream.write(audio[offset:offset + 25].raw_data)
            if first:
                first = False
                first_audio(index)
        try:
            caption_coordinator.finish_pending()
        except Exception as exc:
            Logger.warn(f'Sentence caption completion failed: {exc}')
    finally:
        try:
            if stream is not None:
                try:
                    stream.stop_stream()
                finally:
                    stream.close()
        finally:
            pa.terminate()


def speak_overlapped(text, engine, turn_started=None, overlap=True, turn_id=None):
    started = time.perf_counter()
    raw_device = get_settings('tts_output_device')
    device = None if raw_device in ('', None) else int(raw_device)
    prepare = make_preparer(engine)
    sentences = split_sentences(text) if overlap else [text]
    Logger.detail(f'TTS sentence pipeline: {engine}, segments={len(sentences)}, lookahead={int(overlap)}')
    def first_audio(index):
        if index != 0:
            return
        now = time.perf_counter()
        Logger.turn_metric(f'TTS first audio write: {now - started:.2f}s since speech request', turn_id)
        if turn_started is not None:
            Logger.turn_metric(f'Turn to first assistant audio write: {now - turn_started:.2f}s', turn_id)
    waits = []
    def report(label, duration):
        Logger.detail(f'{label}: {duration:.2f}s')
        if label == 'TTS segment wait':
            waits.append(duration)
    result = run_pipeline(sentences, prepare,
        lambda audio, sentence, index: play_segment(audio, sentence, index, device, first_audio),
        turn_control.raise_if_interrupted, report)
    Logger.turn_metric(f'Speech: {len(sentences)} segments · {sum(waits):.2f}s total synthesis wait between segments', turn_id)
    return result
