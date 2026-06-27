from pathlib import Path
import uuid, os, pyaudio, wave, asyncio
import httpx, json
from novelai_api.Preset import Model, Preset
from novelai_api.Tokenizer import Tokenizer
from novelai_api.utils import b64_to_tokens, tokens_to_b64
from typing import Union, List, Dict, Any, AsyncGenerator
from files.moderation.text_cleaner import separate_sentences, sanitize_text
from files.system_setup.audio_tools import tts_output_dir
import hashlib
import shutil
import subprocess
import sys

audio = pyaudio.PyAudio()
device_index = None
interrupt_next = False
NOVELAI_OUTPUT_DIR = tts_output_dir("novelai")


def _find_ffmpeg() -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    cwd = os.environ.get("CWD", os.getcwd())
    local_path = os.path.abspath(os.path.join(cwd, "ffmpeg", "ffmpeg.exe"))
    if os.path.isfile(local_path):
        return local_path
    return ""

def id_to_seeds(voice_seed: str | int | None) -> tuple[int, str]:
    if voice_seed is None:
        return -1, "Ligeia"
    raw = str(voice_seed).strip()
    if not raw:
        return -1, "Ligeia"
    try:
        return int(raw), "-1"
    except ValueError:
        return -1, raw

FFMPEG_PATH = _find_ffmpeg()

class NovelTTSClient:
    def __init__(self, api_token: str, tier: str = "Tablet"):
        self.api_token = api_token
        self.tier = tier

    @classmethod
    def audio_path_for(cls, file_id: str, ext: str = "mp3") -> str:
        return str(NOVELAI_OUTPUT_DIR / f"{file_id}.{ext.lstrip('.')}")

    def get_auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    async def generate_tts(self, speak: str, voice_seed: str) -> str:
        url = "https://api.novelai.net/ai/generate-voice"
        headers = self.get_auth_headers()

        try:
            voice_id, seed = id_to_seeds(voice_seed)
        except ValueError:
            voice_id = 0

        payload = {
            "text": speak,
            "voice": voice_id,
            "seed": seed,
            "opus": False,
            "version": "v2",
        }

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code != 200:
                raise Exception("TTS request failed", response.status_code, response.text)

            tts_bytes = response.content
            new_id = str(uuid.uuid4())
            mp3_path = self.audio_path_for(new_id, "mp3")
            with open(mp3_path, "wb") as f:
                f.write(tts_bytes)
            return new_id

    def play_tts(self, file_id: str, device: int = None, volume_modifier: int = 10) -> bool:
        global audio, device_index, interrupt_next
        interrupt_next = False

        if not FFMPEG_PATH:
            print("Could not find ffmpeg. Please install it or place ffmpeg.exe in ./ffmpeg/.", file=sys.stderr)
            return False

        mp3_path = self.audio_path_for(file_id, "mp3")
        wav_path = self.audio_path_for(file_id, "wav")

        subprocess.run(
            [
                FFMPEG_PATH,
                "-loglevel", "quiet",
                "-y",
                "-i", mp3_path,
                "-filter:a", f"volume={volume_modifier}dB",
                wav_path,
            ]
        )

        final_device = device_index if device is None else device
        try:
            wave_file = wave.open(wav_path, "rb")
            virtual_cable_stream = audio.open(
                format=audio.get_format_from_width(wave_file.getsampwidth()),
                channels=wave_file.getnchannels(),
                rate=wave_file.getframerate(),
                output=True,
                output_device_index=final_device,
            )
        except Exception as e:
            print("Cannot use selected audio device as output.", file=sys.stderr)
            wave_file.close()
            return False

        data = wave_file.readframes(8192)
        while data:
            if interrupt_next:
                interrupt_next = False
                break
            virtual_cable_stream.write(data)
            data = wave_file.readframes(8192)

        virtual_cable_stream.stop_stream()
        virtual_cable_stream.close()
        wave_file.close()

        try:
            os.remove(mp3_path)
        except OSError:
            pass
        try:
            os.remove(wav_path)
        except OSError:
            pass
        return True

    async def generate_stream_for_tts(
        self,
        custom_prompt: Union[str, List[int]],
        parameters: Dict[str, Any],
    ) -> AsyncGenerator[str, None]:
        model = Model.Kayra
        preset = Preset.from_official(model, "Carefree")
        preset.max_length = parameters["max_output_length"]
        preset.repetition_penalty = parameters["repetition_penalty"]
        preset.temperature = parameters["temperature"]
        preset.length_penalty = parameters["length_penalty"]

        params = {
            "max_length": preset.max_length,
            "min_length": 1,
            "repetition_penalty": preset.repetition_penalty,
            "temperature": preset.temperature,
            "top_k": 15,
            "top_p": 0.85,
            "cfg_scale": 1,
        }

        if isinstance(custom_prompt, str):
            prompt = Tokenizer.encode(model, custom_prompt)
        else:
            prompt = custom_prompt

        prompt_b64 = tokens_to_b64(prompt, 4 if model is Model.Erato else 2)
        data = {"input": prompt_b64, "model": model.value, "parameters": params}

        endpoint = "/ai/generate-stream"
        base_address = (
            "https://text.novelai.net"
            if model in (Model.Kayra, Model.Erato)
            else "https://api.novelai.net"
        )
        url = f"{base_address}{endpoint}"
        headers = self.get_auth_headers()

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", url, headers=headers, json=data) as response:
                expected_status = 200 if model in (Model.Kayra, Model.Erato) else 201
                if response.status_code != expected_status:
                    error_text = await response.aread()
                    print(f"Error {response.status_code}: {error_text}")
                    return

                buffer = ""
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[len("data:"):].strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        content = json.loads(line)
                        if "token" in content:
                            token = b64_to_tokens(content["token"])
                            decoded_token = Tokenizer.decode(model, token)
                            buffer += decoded_token
                            if buffer and buffer[-1] in (".", "!", "?"):
                                clean_sentence = sanitize_text(separate_sentences(buffer))
                                yield clean_sentence
                                buffer = ""
                    except json.JSONDecodeError as e:
                        print(f"JSON decode error: {e}", file=sys.stderr)
                        continue
                if buffer:
                    yield sanitize_text(separate_sentences(buffer))

client: NovelTTSClient | None = None

def prep_handle(message, websocket):
    asyncio.run(handle(message, websocket))

def clear_audio_files():
    if not os.path.isdir("audio"):
        os.mkdir("audio")
        return
    for file in os.listdir("audio"):
        os.remove(os.path.join("audio", file))

async def handle(message, websocket):
    split_message = message.split(" ")
    prefix = split_message[0]
    split_message.remove(prefix)
    payload = str.join(" ", split_message)
    match prefix:
        case "GENERATE":
            await prepare_generate(payload, websocket)
        case "PLAY":
            await prepare_play(payload, websocket)
        case "INTERRUPT":
            interrupt()

def interrupt():
    global interrupt_next
    interrupt_next = True

async def prepare_generate(payload, websocket):
    global client
    if client is None:
        print("NovelTTSClient not initialised.", file=sys.stderr)
        websocket.send("ERROR UNDEFINED Client not initialised.")
        return

    params = json.loads(payload)
    text = params["prompt"]
    options = params["options"]
    concurrent_id = params["concurrent_id"]
    response = ""
    try:
        response = await client.generate_tts(speak=text, voice_seed=options["voice_seed"])
    except Exception as e:
        if len(e.args) < 2:
            print(e, file=sys.stderr)
            response = "ERROR UNDEFINED Could not get error info."
        else:
            match e.args[1]:
                case 401:
                    response = "ERROR WRONG_AUTH Missing or incorrect NovelAI API key."
                case 502:
                    response = "ERROR RESPONSE_FAILURE API responded with 502."
                case _:
                    print(e, file=sys.stderr)
                    response = "ERROR UNDEFINED " + str(e.args[2])
    websocket.send(str(concurrent_id) + " " + response)

#
async def prepare_play(payload, websocket): # Not necessary but something that might be used later if I find a true use for it
    global client
    if client is None:
        print("NovelTTSClient not initialised.", file=sys.stderr)
        websocket.send("PLAY DONE")
        return

    params = json.loads(payload)
    file_id = params["id"]
    options = params["options"]
    try:
        client.play_tts(
            file_id=file_id,
            device=options["device"],
            volume_modifier=options["volume_modifier"],
        )
    except Exception as e:
        print(e, file=sys.stderr)
    websocket.send("PLAY DONE")

def init_client(api_token: str, tier: str = "Tablet"):
    global client
    client = NovelTTSClient(api_token=api_token, tier=tier)

async def main(): # Just as with the Text version, you can run this as a preliminary to see if it works
    clear_audio_files()
    token = input("API Key: ").strip()

    global client
    client = NovelTTSClient(api_token=token)

    prompt = input("Prompt: ")
    voice = input("Voice name (e.g. Ligeia): ").strip() or "Ligeia"
    choice = input("Stream output? (y/n): ").strip().lower()

    parameters = {
        "max_output_length": 150,
        "repetition_penalty": 1.1,
        "temperature": 0.85,
        "length_penalty": 1.0,
    }

    if choice == "y":
        print("\nStreaming Output for TTS:\n")
        async for chunk in client.generate_stream_for_tts(prompt, parameters):
            print(chunk, flush=True)
    else:
        print("\nFull TTS Output (Generating Audio File):\n")
        try:
            file_id = await client.generate_tts(speak=prompt, voice_seed=voice)
            print("Generated audio file ID:", file_id)
            client.play_tts(file_id=file_id)
        except Exception as e:
            print("Error during TTS generation:", e, file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())