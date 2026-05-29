from files.system_setup.settings import save_settings, get_settings, save_auth, get_auth
from files.llm.boilerplate_novel import NovelAIClient, login_with_credentials, fetch_user_info, extract_username
from files.llm.openai_llm import system_message
from files.llm.LocalLLM import local_init

import asyncio
import traceback

novelai_voice_seed: str = "galette"

system_msg:    str = ""
Novel_Model:   str = "kayra"
novel_client:  NovelAIClient | None = None
provider:      str = ""
chat_model:    str = ""


voiceid:       str = ""
fish_voiceid:  str = ""


def set_novelai_seed(seed: str) -> None:
    global novelai_voice_seed
    novelai_voice_seed = seed

def set_novel_model(model_name: str) -> None:
    global Novel_Model

    aliases = {
        "kayro":      "kayra",
        "kayra":      "kayra",
        "erato":      "erato",
        "crio":       "clio",
        "clio":       "clio",
        "xialong":    "xialong-v1",
        "xialong-v1": "xialong-v1",
    }

    key = (model_name or "").strip().lower()
    Novel_Model = aliases.get(key, "kayra")

    save_settings("NOVELAI_MODEL", Novel_Model)
    print(f"NovelAI model set to: {Novel_Model}")


def get_or_create_novelai_model() -> str:
    saved_model = get_settings("NOVELAI_MODEL")
    if saved_model:
        set_novel_model(saved_model)
        return Novel_Model

    print("Choose NovelAI model:")
    print("Options: Kayra, Erato, Clio, Xialong")
    selected = input("NovelAI model: ").strip()
    set_novel_model(selected)
    return Novel_Model


def initialize_model(chat_model_name: str) -> None:
    global novel_client

    if chat_model_name == "OpenAI":
        try:
            system_message(system_msg)
            print("System Message Set!")
        except Exception:
            print("Error when reading API key:")
            print(traceback.format_exc())

    elif chat_model_name == "NovelAI":
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            print("Logging into NovelAI!")
            email, password = get_or_create_novelai_credentials()
            token = loop.run_until_complete(
                login_with_credentials(email=email, password=password)
            )
            if not token:
                print("NovelAI login failed.")
                return

            save_auth("novelai", "token", token)

            user_info = loop.run_until_complete(fetch_user_info(token))
            username = extract_username(user_info)
            if username == "Unknown" and email:
                username = email.split("@")[0]

            save_auth("novelai", "username", username)
            get_or_create_novelai_model()

            novel_client = NovelAIClient(api_token=token)
            print(f"Welcome: {username}!")

        except Exception:
            print("Error when loading NovelAI credentials:")
            print(traceback.format_exc())
        finally:
            loop.close()

    elif chat_model_name == "Local":
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            print("Loading Local Model!")
            loop.run_until_complete(local_init())
            print("Local Model Loaded!")
        except Exception:
            print("Error when loading local model:")
            print(traceback.format_exc())
        finally:
            loop.close()

def change_tts(model: str) -> None:
    global provider
    provider = model
    initialize_tts(provider)


def change_llm(model: str) -> None:
    global chat_model
    chat_model = model
    save_settings("chat_model", chat_model)
    initialize_model(chat_model)

def initialize_tts(tts_provider: str) -> None:
    if tts_provider == "Elevenlabs":
        print("Switched to Elevenlabs!")
        set_voiceid(voiceid)

    elif tts_provider == "FishSpeech":
        print("Switched to FishSpeech!")
        set_fish(fish_voiceid)

    elif tts_provider == "EdgeTTS":
        print("Switched to EdgeTTS!")

    elif tts_provider == "NovelAI":
        print("Switched to NovelAI-TTS!")

    else:
        print(f"Unknown TTS provider: {tts_provider!r}")


def set_voiceid(voice_id: str) -> str:
    global voiceid
    voiceid = voice_id
    return voiceid


def set_fish(fish_id: str) -> str:
    global fish_voiceid
    fish_voiceid = fish_id
    return fish_voiceid

def get_or_create_novelai_credentials() -> tuple[str, str]:
    email = get_auth("novelai", "mail")
    password = get_auth("novelai", "password")

    if email and password:
        print("Using saved NovelAI credentials.")
        return email, password

    print("NovelAI credentials not found. One-time login required.")
    email    = input("📧 Email: ").strip()
    password = input("🔒 Password: ").strip()

    save_auth("novelai", "mail", email)
    save_auth("novelai", "password", password)

    return email, password

if __name__ == "__main__":
    print("Press Ctrl+C to exit.")

    saved_seed = get_settings("NOVELAI_VOICE_SEED")
    if saved_seed:
        set_novelai_seed(saved_seed)

    llm_array = ["OpenAI", "Local", "NovelAI"]
    chat_model_settings = get_settings("chat_model")
    if chat_model_settings not in llm_array:
        chat_model_settings = ""
        print("No model found!\n")
        chat_model_input = input(f"Enter a model! (Options: {llm_array}): ")
        initialize_model(chat_model_input)
        print(f"Chat Model Set to {chat_model_input}")
        save_settings("chat_model", chat_model_input)
    else:
        change_llm(chat_model_settings)

    tts_array = ["Elevenlabs", "FishSpeech", "EdgeTTS", "NovelAI"]
    tts_model_settings = get_settings("tts_model")
    if tts_model_settings not in tts_array:
        tts_model_settings = ""
        print("No TTS model found!\n")
        provider_input = input(f"Enter TTS model! (Options: {tts_array}): ")
        initialize_tts(provider_input)
        print(f"TTS Model Set to: {provider_input}")
        save_settings("tts_model", provider_input)
    else:
        change_tts(tts_model_settings)

    print(f"\n\nTTS = {tts_model_settings}  LLM = {chat_model_settings}\n\n")