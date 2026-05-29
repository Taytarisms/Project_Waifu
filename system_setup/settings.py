import json
import traceback
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
USERDATA_DIR = ROOT_DIR / "userdata"
OLD_SETTINGS_PATH = Path("settings.json")
USERDATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILES = {
    "auth": USERDATA_DIR / "auth.json",
    "llm": USERDATA_DIR / "llm.json",
    "tts": USERDATA_DIR / "tts.json",
    "app": USERDATA_DIR / "app.json",
    "stt": USERDATA_DIR / "stt.json",
    "twitch": USERDATA_DIR / "twitch.json",
}

settings: dict[str, Any] = {}
sections: dict[str, dict[str, Any]] = {}

KEY_ROUTES = {'NOVELAI_TOKEN': 'auth',
 'NOVELAI_USERNAME': 'auth',
 'NOVELAI_EMAIL': 'auth',
 'NOVELAI_PASS': 'auth',
 'OPENAI_API_KEY': 'auth',
 'ANTHROPIC_API_KEY': 'auth',
 'GEMINI_API_KEY': 'auth',
 'XAI_API_KEY': 'auth',
 'ELEVENLABS_API_KEY': 'auth',
 'FISH_SPEECH_TOKEN': 'auth',
 'INWORLD_TOKEN': 'auth',
 'chat_model': 'llm',
 'openai_model': 'llm',
 'openai_temperature': 'llm',
 'openai_max_tokens': 'llm',
 'openai_top_p': 'llm',
 'openai_presence_penalty': 'llm',
 'openai_frequency_penalty': 'llm',
 'openai_stop': 'llm',
 'novel_model': 'llm',
 'NOVELAI_MODEL': 'llm',
 'novel_temperature': 'llm',
 'novel_max_tokens': 'llm',
 'novel_top_p': 'llm',
 'novel_top_k': 'llm',
 'novel_banlist_mode': 'llm',
 'novel_generation_route': 'llm',
 'max_output_length': 'llm',
 'temperature': 'llm',
 'repetition_penalty': 'llm',
 'active_novelai_character': 'llm',
 'claude_model': 'llm',
 'claude_temperature': 'llm',
 'claude_max_tokens': 'llm',
 'claude_top_p': 'llm',
 'claude_top_k': 'llm',
 'gemini_model': 'llm',
 'gemini_temperature': 'llm',
 'gemini_max_tokens': 'llm',
 'gemini_top_p': 'llm',
 'gemini_top_k': 'llm',
 'grok_model': 'llm',
 'grok_temperature': 'llm',
 'grok_max_tokens': 'llm',
 'local_model_filename': 'llm',
 'local_model_family': 'llm',
 'local_mmproj_filename': 'llm',
 'local_system_prompt_filename': 'llm',
 'local_n_ctx': 'llm',
 'local_n_batch': 'llm',
 'local_n_threads': 'llm',
 'local_n_gpu_layers': 'llm',
 'local_main_gpu': 'llm',
 'local_seed': 'llm',
 'local_temperature': 'llm',
 'local_max_tokens': 'llm',
 'local_top_k': 'llm',
 'local_top_p': 'llm',
 'local_min_p': 'llm',
 'local_repeat_penalty': 'llm',
 'local_force_new_context': 'llm',
 'local_screen_max_dim': 'llm',
 'local_screenshot_quality': 'llm',
 'local_screenshot_subsampling': 'llm',
 'local_enable_thinking': 'llm',
 'monologue_idle_enabled': 'llm',
 'monologue_idle_seconds': 'llm',
 'monologue_topics': 'llm',
 'monologue_use_random_memory': 'llm',
 'monologue_memory_count': 'llm',
 'tts_model': 'tts',
 'tts_engine': 'tts',
 'tts_output_device': 'tts',
 'edge_voice': 'tts',
 'edge_rate': 'tts',
 'edge_pitch': 'tts',
 'edge_volume': 'tts',
 'eleven_voice': 'tts',
 'eleven_voice_id': 'tts',
 'eleven_model_id': 'tts',
 'eleven_stability': 'tts',
 'eleven_similarity': 'tts',
 'eleven_style': 'tts',
 'eleven_speed': 'tts',
 'eleven_speaker_boost': 'tts',
 'eleven_streaming': 'tts',
 'fish_api_url': 'tts',
 'fish_model': 'tts',
 'fish_voice_id': 'tts',
 'fish_reference_id': 'tts',
 'fish_rate': 'tts',
 'fish_channels': 'tts',
 'fish_chunk_length': 'tts',
 'fish_min_chunk_length': 'tts',
 'fish_latency': 'tts',
 'fish_format': 'tts',
 'fish_normalize': 'tts',
 'fish_condition_previous_chunks': 'tts',
 'novel_tts_voice_seed': 'tts',
 'NOVELAI_VOICE_SEED': 'tts',
 'novel_tts_version': 'tts',
 'novel_tts_mode': 'tts',
 'novel_tts_output_format': 'tts',
 'openai_tts_model': 'tts',
 'openai_tts_voice': 'tts',
 'openai_tts_speed': 'tts',
 'openai_tts_instructions': 'tts',
 'inworld_tts_voice': 'tts',
 'inworld_tts_model': 'tts',
 'inworld_tts_voice_id': 'tts',
 'inworld_tts_model_id': 'tts',
 'inworld_tts_audio_encoding': 'tts',
 'inworld_tts_sample_rate': 'tts',
 'inworld_tts_bit_rate': 'tts',
 'inworld_tts_language': 'tts',
 'inworld_tts_delivery_mode': 'tts',
 'inworld_tts_temperature': 'tts',
 'inworld_tts_apply_text_normalization': 'tts',
 'inworld_tts_timestamp_type': 'tts',
 'inworld_tts_warmup': 'tts',
 'kokoro_voice': 'tts',
 'kokoro_speed': 'tts',
 'kokoro_model_file': 'tts',
 'kokoro_voices_file': 'tts',
 'qwen3_tts_preset': 'tts',
 'qwen3_tts_model_id': 'tts',
 'qwen3_tts_backend': 'tts',
 'qwen3_tts_device': 'tts',
 'qwen3_tts_speaker': 'tts',
 'qwen3_tts_language': 'tts',
 'qwen3_tts_instruct': 'tts',
 'qwen3_tts_ref_audio': 'tts',
 'qwen3_tts_ref_text': 'tts',
 'qwen3_tts_xvec_only': 'tts',
 'qwen3_tts_streaming_chunk_size': 'tts',
 'narrator_enabled': 'tts',
 'narrator_backend': 'tts',
 'narrator_voice': 'tts',
 'narrator_read_own_messages': 'tts',
 'narrator_read_user_message': 'tts',
 'narrator_output_device': 'tts',
 'narrator_volume_db': 'tts',
 'narrator_piper_models_dir': 'tts',
 'captions_enabled': 'app',
 'captions_for_narrator': 'app',
 'captions_for_user_messages': 'app',
 'captions_for_llm_replies': 'app',
 'captions_file_path': 'app',
 'FILTER_LLM_OUTPUT': 'app',
 'user_name': 'app',
 'username': 'app',
 'character_name': 'app',
 'live_memory_recall_max_distance': 'app',
 'reminder_state': 'app',
 'enable_finetune_sampling': 'app',
 'enable_vts': 'app',
 'enable_vts_idle_animation': 'app',
 'enable_vts_emotion_inference': 'app',
 'vts_emotion_inference_default_on': 'app',
 'vts_idle_await_response': 'app',
 'vts_idle_fps': 'app',
 'vts_emotion_reset_delay': 'app',
 'vts_emotion_reset_after_tts': 'app',
 'stt_mode': 'stt',
 'stt_engine': 'stt',
 'stt_ptt_key': 'stt',
 'stt_wake_word_enabled': 'stt',
 'stt_wake_word': 'stt',
 'stt_openai_model': 'stt',
 'stt_openai_chunk_seconds': 'stt',
 'stt_google_timeout': 'stt',
 'stt_google_phrase_time_limit': 'stt',
 'stt_google_adjust_ambient': 'stt',
 'stt_google_energy_threshold': 'stt',
 'stt_device_index': 'stt',
 'stt_model_name': 'stt',
 'stt_device': 'stt',
 'stt_compute_type': 'stt',
 'stt_language': 'stt',
 'stt_vad_aggressiveness': 'stt',
 'stt_pre_speech_ms': 'stt',
 'stt_post_speech_ms': 'stt',
 'stt_segment_max_ms': 'stt',
 'stt_min_length_ms': 'stt',
 'stt_enable_partials': 'stt',
 'TWITCH_POLICY': 'twitch',
 'TWITCH_PAUSED': 'twitch',
 'TWITCH_IGNORED_PREFIXES': 'twitch'}

DEFAULTS = {'auth': {'novelai': {'mail': '', 'username': '', 'password': '', 'token': ''},
          'openai': {'token': ''},
          'anthropic': {'token': ''},
          'google': {'token': ''},
          'xai': {'token': ''},
          'elevenlabs': {'api_key': ''},
          'fishspeech': {'token': ''},
          'inworld': {'token': ''},
          'twitch': {'channel_name': '',
                     'access_token': '',
                     'refresh_token': '',
                     'channel_access_token': '',
                     'channel_refresh_token': '',
                     'client_id': '',
                     'client_secret': '',
                     'bot_id': '',
                     'owner_id': ''},
          'username': {'user': ''}},
 'llm': {'chat_model': '',
         'openai_model': '',
         'openai_temperature': 0.0,
         'openai_max_tokens': 512,
         'openai_top_p': 1.0,
         'openai_presence_penalty': 0.0,
         'openai_frequency_penalty': 0.0,
         'openai_stop': '',
         'novel_model': 'kayra',
         'NOVELAI_MODEL': 'kayra',
         'novel_temperature': 0.8,
         'novel_max_tokens': 512,
         'novel_top_p': 0.9,
         'novel_top_k': 40,
         'novel_banlist_mode': 'off',
         'novel_generation_route': 'auto',
         'max_output_length': 150,
         'temperature': 0.8,
         'repetition_penalty': 1.25,
         'active_novelai_character': 'character',
         'claude_model': 'claude-sonnet-4-6',
         'claude_temperature': 1.0,
         'claude_max_tokens': 1024,
         'claude_top_p': 1.0,
         'claude_top_k': 0,
         'gemini_model': 'gemini-2.5-flash',
         'gemini_temperature': 1.0,
         'gemini_max_tokens': 1024,
         'gemini_top_p': 1.0,
         'gemini_top_k': 0,
         'grok_model': 'grok-4.3',
         'grok_temperature': 1.0,
         'grok_max_tokens': 1024,
         'local_model_filename': '',
         'local_model_family': 'gemma4',
         'local_mmproj_filename': '',
         'local_system_prompt_filename': 'system_message_local.txt',
         'local_n_ctx': 16384,
         'local_n_batch': 1024,
         'local_n_threads': 12,
         'local_n_gpu_layers': -1,
         'local_main_gpu': 0,
         'local_seed': -1,
         'local_temperature': 0.5,
         'local_max_tokens': 256,
         'local_top_k': 40,
         'local_top_p': 0.95,
         'local_min_p': 0.05,
         'local_repeat_penalty': 1.1,
         'local_force_new_context': False,
         'local_screen_max_dim': 960,
          'local_screenshot_quality': 82,
          'local_screenshot_subsampling': '4:2:0',
          'local_enable_thinking': False,
          'monologue_idle_enabled': False,
          'monologue_idle_seconds': 300,
          'monologue_topics': '',
          'monologue_use_random_memory': False,
          'monologue_memory_count': 3},
 'tts': {'tts_model': 'ElevenLabs',
         'tts_engine': '',
         'tts_output_device': None,
         'edge_voice': 'en-US-AvaNeural',
         'edge_rate': '+0%',
         'edge_pitch': '+0Hz',
         'edge_volume': '+0%',
         'eleven_voice': '',
         'eleven_voice_id': '',
         'eleven_model_id': 'eleven_v3',
         'eleven_stability': 0.5,
         'eleven_similarity': 0.75,
         'eleven_style': 0.0,
         'eleven_speed': 1.0,
         'eleven_speaker_boost': False,
         'eleven_streaming': True,
         'fish_api_url': 'https://api.fish.audio/v1/tts',
         'fish_model': 's2-pro',
         'fish_voice_id': '',
         'fish_reference_id': '',
         'fish_rate': 24000,
         'fish_channels': 2,
         'fish_chunk_length': 200,
         'fish_min_chunk_length': 50,
         'fish_latency': 'low',
         'fish_format': 'pcm',
         'fish_normalize': True,
         'fish_condition_previous_chunks': True,
         'novel_tts_voice_seed': '',
         'NOVELAI_VOICE_SEED': '',
         'novel_tts_version': 'v2',
         'novel_tts_mode': 'Streamed',
         'novel_tts_output_format': 'mp3',
         'openai_tts_model': 'gpt-4o-mini-tts',
         'openai_tts_voice': 'nova',
         'openai_tts_speed': 1.0,
         'openai_tts_instructions': '',
         'inworld_tts_voice': 'Ashley',
         'inworld_tts_model': 'inworld-tts-1.5-mini',
         'inworld_tts_voice_id': 'Ashley',
         'inworld_tts_model_id': 'inworld-tts-1.5-mini',
         'inworld_tts_audio_encoding': 'MP3',
         'inworld_tts_sample_rate': 24000,
         'inworld_tts_bit_rate': 32000,
         'inworld_tts_language': '',
         'inworld_tts_delivery_mode': 'BALANCED',
         'inworld_tts_temperature': 1.0,
         'inworld_tts_apply_text_normalization': True,
         'inworld_tts_timestamp_type': 'NONE',
         'inworld_tts_warmup': True,
         'kokoro_voice': 'af_heart',
         'kokoro_speed': 1.0,
         'kokoro_model_file': 'kokoro-v1.0.onnx',
         'kokoro_voices_file': 'voices-v1.0.bin',
         'qwen3_tts_preset': '',
         'qwen3_tts_model_id': 'Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice',
         'qwen3_tts_backend': 'faster',
         'qwen3_tts_device': 'auto',
         'qwen3_tts_speaker': 'ryan',
         'qwen3_tts_language': 'English',
         'qwen3_tts_instruct': '',
         'qwen3_tts_ref_audio': '',
         'qwen3_tts_ref_text': '',
         'qwen3_tts_xvec_only': True,
         'qwen3_tts_streaming_chunk_size': 8,
         'narrator_enabled': False,
         'narrator_backend': 'novelai',
         'narrator_voice': '',
         'narrator_read_own_messages': False,
         'narrator_read_user_message': False,
         'narrator_output_device': None,
         'narrator_volume_db': 0,
         'narrator_piper_models_dir': ''},
 'app': {'FILTER_LLM_OUTPUT': True,
         'user_name': 'User',
         'username': 'User',
         'character_name': 'Character',
         'live_memory_recall_max_distance': 1.0,
         'captions_enabled': False,
         'captions_for_narrator': True,
         'captions_for_user_messages': True,
         'captions_for_llm_replies': False,
         'captions_file_path': '',
         'reminder_state': False,
         'enable_finetune_sampling': False,
         'enable_vts': True,
         'enable_vts_idle_animation': True,
         'enable_vts_emotion_inference': True,
         'vts_emotion_inference_default_on': True,
         'vts_idle_await_response': True,
         'vts_idle_fps': 10,
         'vts_emotion_reset_delay': 0.35,
         'vts_emotion_reset_after_tts': True},
  'stt': {'stt_mode': 'Push To Talk',
          'stt_engine': 'Local Whisper',
          'stt_ptt_key': 'space',
          'stt_wake_word_enabled': False,
         'stt_wake_word': '',
         'stt_openai_model': 'gpt-4o-transcribe',
         'stt_openai_chunk_seconds': 5.0,
         'stt_google_timeout': 0.5,
         'stt_google_phrase_time_limit': 8.0,
         'stt_google_adjust_ambient': True,
         'stt_google_energy_threshold': '',
         'stt_device_index': '',
         'stt_model_name': 'tiny',
         'stt_device': 'cuda',
         'stt_compute_type': 'float16',
         'stt_language': 'en',
         'stt_vad_aggressiveness': 1,
         'stt_pre_speech_ms': 1000,
         'stt_post_speech_ms': 600,
         'stt_segment_max_ms': 15000,
         'stt_min_length_ms': 1000,
         'stt_enable_partials': True},
 'twitch': {'TWITCH_POLICY': 'latest-buffered',
            'TWITCH_PAUSED': False,
            'TWITCH_IGNORED_PREFIXES': ''}}

AUTH_ALIASES = {'NOVELAI_USERNAME': ('novelai', 'username'),
 'NOVELAI_EMAIL': ('novelai', 'mail'),
 'NOVELAI_PASS': ('novelai', 'password'),
 'NOVELAI_TOKEN': ('novelai', 'token'),
 'OPENAI_API_KEY': ('openai', 'token'),
 'ANTHROPIC_API_KEY': ('anthropic', 'token'),
 'GEMINI_API_KEY': ('google', 'token'),
 'XAI_API_KEY': ('xai', 'token'),
 'ELEVENLABS_API_KEY': ('elevenlabs', 'api_key'),
 'FISH_SPEECH_TOKEN': ('fishspeech', 'token'),
 'INWORLD_TOKEN': ('inworld', 'token')}

SETTING_ALIASES = {
    "narrator_read_user_message": "narrator_read_own_messages",
}


def _route_for_key(key: str) -> str:
    return KEY_ROUTES.get(key, "app")


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        if not path.exists():
            _write_json(path, deepcopy(default))
            return deepcopy(default)

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        return data if isinstance(data, dict) else deepcopy(default)
    except Exception:
        print(f"Failed reading {path}:")
        print(traceback.format_exc())
        return deepcopy(default)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def _merge_defaults(data: dict[str, Any], default: dict[str, Any]) -> bool:
    changed = False

    for key, value in default.items():
        if key not in data:
            data[key] = deepcopy(value)
            changed = True
            continue

        if isinstance(value, dict) and isinstance(data.get(key), dict):
            if _merge_defaults(data[key], value):
                changed = True

    return changed


def load_settings() -> None:
    global sections
    try:
        sections = {}

        for section, path in CONFIG_FILES.items():
            default = DEFAULTS.get(section, {})
            data = _read_json(path, default)

            if _merge_defaults(data, default):
                _write_json(path, data)

            sections[section] = data

        _merge_sections()
    except Exception:
        print("Unexpected error loading settings:")
        print(traceback.format_exc())


def _merge_sections() -> None:
    global settings
    settings = {}
    for section_name, data in sections.items():
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            canonical_key = SETTING_ALIASES.get(key, key)
            routed_section = _route_for_key(canonical_key)

            if routed_section == section_name:
                settings[canonical_key] = value
    for section_name, data in sections.items():
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            canonical_key = SETTING_ALIASES.get(key, key)
            if canonical_key in settings:
                continue
            if canonical_key not in KEY_ROUTES and canonical_key not in AUTH_ALIASES:
                settings[canonical_key] = value
    auth = sections.get("auth", {})
    for flat_key, (group, nested_key) in AUTH_ALIASES.items():
        settings[flat_key] = auth.get(group, {}).get(nested_key, "")
    for alias, canonical in SETTING_ALIASES.items():
        if canonical in settings:
            settings[alias] = settings[canonical]

def save_settings(key: str, value: Any) -> None:
    try:
        if not sections:
            load_settings()
        key = SETTING_ALIASES.get(key, key)

        if key in AUTH_ALIASES:
            group, nested_key = AUTH_ALIASES[key]
            sections.setdefault("auth", {}).setdefault(group, {})[nested_key] = value
            _write_json(CONFIG_FILES["auth"], sections["auth"])
            settings[key] = value
        else:
            section = _route_for_key(key)
            sections.setdefault(section, {})[key] = value
            _write_json(CONFIG_FILES[section], sections[section])
            settings[key] = value

        _merge_sections()
        print(f"Saved setting: {key} = {value!r}")
    except Exception:
        print(f"Failed to save setting {key!r}:")
        print(traceback.format_exc())


def reload_settings() -> None:
    load_settings()

def cleanup_misplaced_settings() -> None:
    if not sections:
        load_settings()
    changed_sections: set[str] = set()
    for section_name, data in sections.items():
        if not isinstance(data, dict):
            continue
        for key in list(data.keys()):
            canonical_key = SETTING_ALIASES.get(key, key)
            if canonical_key in AUTH_ALIASES:
                continue
            expected_section = _route_for_key(canonical_key)
            if canonical_key in KEY_ROUTES and expected_section != section_name:
                data.pop(key, None)
                changed_sections.add(section_name)
    for section_name in changed_sections:
        _write_json(CONFIG_FILES[section_name], sections[section_name])

    _merge_sections()


def get_bool_setting(key: str, default: bool = False) -> bool:
    value = get_settings(key)
    if value in ("", None):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"true", "1", "yes", "y", "on", "enabled"}:
            return True
        if cleaned in {"false", "0", "no", "n", "off", "disabled"}:
            return False
    return default


def get_settings(key: str) -> Any:
    try:
        if not sections:
            load_settings()
        canonical_key = SETTING_ALIASES.get(key, key)
        if canonical_key in AUTH_ALIASES:
            group, nested_key = AUTH_ALIASES[canonical_key]
            return sections.get("auth", {}).get(group, {}).get(nested_key, "")
        section = _route_for_key(canonical_key)
        section_data = sections.get(section, {})
        if isinstance(section_data, dict) and canonical_key in section_data:
            return section_data[canonical_key]
        if canonical_key in settings:
            return settings[canonical_key]
        if key in settings:
            return settings[key]
        print(f"Setting [{key!r}] not found, returning empty string.")
        return ""

    except Exception:
        print(f"Unexpected error reading setting {key!r}:")
        print(traceback.format_exc())
        return ""

def save_auth(group: str, key: str, value: Any) -> None:
    try:
        if not sections:
            load_settings()

        sections.setdefault("auth", {}).setdefault(group, {})[key] = value
        _write_json(CONFIG_FILES["auth"], sections["auth"])
        _merge_sections()
        print(f"Saved auth setting: {group}.{key}")
    except Exception:
        print(f"Failed to save auth setting {group}.{key}:")
        print(traceback.format_exc())


def get_auth(group: str, key: str) -> Any:
    try:
        if not sections:
            load_settings()

        return sections.get("auth", {}).get(group, {}).get(key, "")
    except Exception:
        print(f"Unexpected error reading auth setting {group}.{key}:")
        print(traceback.format_exc())
        return ""
