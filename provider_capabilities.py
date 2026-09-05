# It was brought to my attention that several models/API calls have either been deprecated or not accepted.
# This is just a primary hub where all models will live and has been updated as of the latest release of Claude Opus 5.
# Some TTS models are a bit different too with calls so I've taken that into account too.
# Most models should be active, but let me know if they aren't or if they become deprecated eventually.

OPENAI_CHAT_MODELS = [
    "gpt-5.6",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-4o",
    "gpt-40-mini",
    "gpt-4o-nano",
    "gpt-3.5-turbo"
]

CLAUDE_CHAT_MODELS = [
    "claude-fable-5",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-opus-4-5-20251101",
    "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5-20251001",
]

GEMINI_CHAT_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

GEMINI_MODEL_ALIASES = {
    "gemini-3.1-pro": "gemini-3.1-pro-preview",
    "gemini-3-flash": "gemini-3-flash-preview",
}

GROK_CHAT_MODELS = [
    "grok-4.6",
    "grok-4.6-latest",
    "grok-4.5",
    "grok-4.5-latest",
]

ELEVENLABS_TTS_MODELS = [
    "eleven_v3",
    "eleven_multilingual_v2",
    "eleven_flash_v2_5",
]

INWORLD_TTS_MODELS = [
    "inworld-tts-2",
    "inworld-tts-1.5-max",
    "inworld-tts-1.5-mini",
    "inworld-tts-1-max",
    "inworld-tts-1",
]

FISH_TTS_MODELS = [
    "s2.1-pro",
    "s2.1-pro-free",
    "s2-pro",
    "s1",
]

OPENAI_TTS_MODELS = [
    "gpt-4o-mini-tts",
    "tts-1",
    "tts-1-hd",
]

OPENAI_TTS_VOICES = [
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
]

OPENAI_LEGACY_TTS_VOICES = [
    "alloy",
    "ash",
    "coral",
    "echo",
    "fable",
    "nova",
    "onyx",
    "sage",
    "shimmer",
]


def claude_supports_sampling(model: str) -> bool:
    name = str(model or "").lower()
    unsupported = (
        "claude-fable-",
        "claude-mythos-",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-opus-4-7",
        "claude-opus-4-8",
    )
    return not name.startswith(unsupported)


def gemini_supports_sampling(model: str) -> bool:
    name = str(model or "").lower()
    return not name.startswith(("gemini-3", "gemini-4"))


def normalize_gemini_model(model: str) -> str:
    name = str(model or "gemini-2.5-flash").strip()
    return GEMINI_MODEL_ALIASES.get(name.lower(), name)


def inworld_uses_delivery_mode(model: str) -> bool:
    return str(model or "").lower() == "inworld-tts-2"


def elevenlabs_v3(model: str) -> bool:
    return str(model or "").lower() == "eleven_v3"


def openai_tts_supports_instructions(model: str) -> bool:
    return str(model or "").lower() == "gpt-4o-mini-tts"


def openai_tts_voice_supported(model: str, voice: str) -> bool:
    choices = (
        OPENAI_LEGACY_TTS_VOICES
        if str(model or "").lower() in {"tts-1", "tts-1-hd"}
        else OPENAI_TTS_VOICES
    )
    return str(voice or "").lower() in choices
