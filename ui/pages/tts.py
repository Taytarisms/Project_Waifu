from pathlib import Path
import customtkinter as ctk
from tkinter import filedialog
import threading
import asyncio

from files.ui import theme
from files.ui.components.cards import ProviderCard
from files.ui.components.header import SteamHeader
from files.ui.components.auth_dialog import AuthTokenDialog
from files.ui.pages.narrator_settings import NarratorSettingsPage
from files.ui.pages.caption_settings import CaptionsSettingsPage
from files.system_setup.audio_tools import list_output_devices, parse_output_device

try:
    from files.system_setup.settings import get_auth, save_auth
except Exception:
    get_auth = None
    save_auth = None

try:
    from files.system_setup.settings import get_settings, save_settings
except Exception:
    get_settings = None
    save_settings = None

try:
    from files.system_setup.initializer import change_tts
except Exception:
    change_tts = None

try:
    import files.main_loop.gen_response as gen_response
except Exception:
    import traceback
    traceback.print_exc()
    gen_response = None

ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
DEFAULT_KOKORO_MODEL_DIR = Path(__file__).resolve().parents[2] / "tts" / "models"

try:
    from files.ui.components.quota_checker import (
        ElevenLabsCard, OpenAICard, MiniFishSpeechCard,
    )
    _QUOTA_CARD_MAP = {
        "ElevenLabs": ElevenLabsCard,
        "OpenAI":     OpenAICard,
        "FishSpeech": MiniFishSpeechCard,
    }
except Exception:
    _QUOTA_CARD_MAP = {}
CLOUD_TTS_PROVIDERS = [
    ("ElevenLabs", "Cloud TTS with streaming, expressive controls, and custom voice IDs.\nCOSTS MONEY TO USE", ASSETS_DIR / "elevenlabs.png"),
    ("FishSpeech", "Local/server expressive TTS backend with reference-audio support.", ASSETS_DIR / "fishspeech.png"),    
    ("NovelAI", "NovelAI voice generation using voice seeds and style options.\nCOSTS MONEY TO USE", ASSETS_DIR / "novelai.png"),
    ("OpenAI", "OpenAI voice models, speed controls, and optional instructions.\nCOSTS MONEY TO USE", ASSETS_DIR / "openai.png"),
    ("InWorld", "InWorld voice models for low-latency character speech.\nCOSTS MONEY TO USE", ASSETS_DIR / "inworld.png"),
]

LOCAL_TTS_PROVIDERS = [
    ("EdgeTTS", "Microsoft Edge voices for lightweight free cloud playback.\nNO API KEY REQUIRED", ASSETS_DIR / "edge.png"),
    ("Kokoro", "Lightweight local ONNX TTS. CPU-friendly, multilingual, and fast.", ASSETS_DIR / "kokoro.png"),
    ("Qwen3 TTS", "Local Qwen3 TTS with built-in voices, cloning, CUDA graph support, and streaming.", ASSETS_DIR / "qwen.png")
]


ALL_TTS_PROVIDERS = {
    name: (desc, img) for name, desc, img in CLOUD_TTS_PROVIDERS + LOCAL_TTS_PROVIDERS
}

TTS_ENGINE_KEYS = {
    "ElevenLabs": "elevenlabs",
    "NovelAI": "novelai_tts",
    "OpenAI": "openai_tts",
    "InWorld": "inworld",
    "EdgeTTS": "edge",
    "Edge TTS": "edge",
    "FishSpeech": "fishspeech",
    "Kokoro": "kokoro",
    "Qwen3 TTS": "qwen3",
}

ENGINE_TO_DISPLAY = {
    "elevenlabs": "ElevenLabs",
    "novelai_tts": "NovelAI",
    "openai_tts": "OpenAI",
    "inworld": "InWorld",
    "edge": "EdgeTTS",
    "fishspeech": "FishSpeech",
    "kokoro": "Kokoro",
    "qwen3": "Qwen3 TTS",
}


def _active_tts_display() -> str:
    if not get_settings:
        return ""
    engine = get_settings("tts_engine") or ""
    if engine:
        return ENGINE_TO_DISPLAY.get(engine, engine)
    return get_settings("tts_model") or ""


class TTSHomePage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app

        ctk.CTkLabel(
            self,
            text="TTS Library",
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=24, pady=(24, 4))

        ctk.CTkLabel(
            self,
            text="Choose a provider category to configure your text-to-speech engine.",
            text_color=theme.MUTED_TEXT,
        ).pack(anchor="w", padx=24, pady=(0, 32))

        active = _active_tts_display()
        self.active_label = ctk.CTkLabel(
            self,
            text=f"Active TTS: {active}" if active else "No TTS provider selected.",
            text_color=theme.SUCCESS if active else theme.MUTED_TEXT,
            font=ctk.CTkFont(size=13),
        )
        self.active_label.pack(anchor="w", padx=24, pady=(0, 28))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(anchor="center", pady=20)

        categories = [
            ("🌐  CLOUD / API TTS", "cloud", "#1a4a6e"),
            ("💾  LOCAL / DOWNLOADABLE TTS", "local", "#1e3d2f"),
            ("🎙️  NARRATOR", "narrator", "#3d2a5c"),
            ("💬  CLOSED CAPTIONS", "captions", "#5c2a3d")
        ]

        for label, tag, color in categories:
            ctk.CTkButton(
                btn_frame,
                text=label,
                width=380,
                height=80,
                font=ctk.CTkFont(size=18, weight="bold"),
                fg_color=color,
                hover_color=self._lighten(color),
                corner_radius=12,
                command=lambda t=tag: self.open_category(t),
            ).pack(pady=12)

    @staticmethod
    def _lighten(hex_color: str) -> str:
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
        return "#{:02x}{:02x}{:02x}".format(
            min(r + 30, 255), min(g + 30, 255), min(b + 30, 255)
        )
 
    def open_category(self, category_tag: str):
        if category_tag == "narrator":
            self.app.show_dynamic_page(NarratorSettingsPage)
            return
        if category_tag == "captions":
            self.app.show_dynamic_page(CaptionsSettingsPage)
            return
        self.app.show_dynamic_page(TTSCategoryPage, category=category_tag)

    def refresh_active_provider(self):
        active = _active_tts_display()
        self.active_label.configure(
            text=f"Active TTS: {active}" if active else "No TTS provider selected.",
            text_color=theme.SUCCESS if active else theme.MUTED_TEXT,
        )

    def refresh_active_engine(self):
        self.refresh_active_provider()


class TTSCategoryPage(ctk.CTkFrame):
    def __init__(self, master, app, category: str):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app
        self.category = category

        is_cloud = category == "cloud"
        title = "Cloud / API TTS" if is_cloud else "Local / Downloadable TTS"
        subtitle = (
            "Cloud-hosted providers. Some require API keys or accounts."
            if is_cloud else
            "Self-hosted or local engines that run on your hardware or local services."
        )
        providers = CLOUD_TTS_PROVIDERS if is_cloud else LOCAL_TTS_PROVIDERS

        ctk.CTkLabel(
            self,
            text=title,
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=24, pady=(24, 4))

        ctk.CTkLabel(self, text=subtitle, text_color=theme.MUTED_TEXT).pack(
            anchor="w", padx=24, pady=(0, 20)
        )

        ctk.CTkButton(
            self,
            text="← Back to TTS Library",
            width=180,
            fg_color="gray30",
            command=lambda: self.app.show_page("TTS"),
        ).pack(anchor="w", padx=24, pady=(0, 16))

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=24, pady=10)

        grid = ctk.CTkFrame(scroll, fg_color="transparent")
        grid.pack(fill="both", expand=True)
        grid.grid_columnconfigure((0, 1), weight=1)

        active = _active_tts_display()
        self.provider_cards = {}

        for i, (name, desc, image_path) in enumerate(providers):
            img = image_path if image_path and image_path.exists() else None
            card = ProviderCard(
                grid,
                name=name,
                description=desc,
                image_path=img,
                command=lambda n=name: self._open_provider(n),
            )
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=10, pady=10, ipady=4)
            self.provider_cards[name] = card
            card.set_active(name == active)

    def _open_provider(self, provider_name: str):
        self.app.show_dynamic_page(TTSProviderPage, provider_name=provider_name)


class TTSProviderPage(ctk.CTkFrame):
    def __init__(self, master, app, provider_name):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app
        self.provider_name = provider_name

        self.AUTH_REQUIREMENTS = {
            "ElevenLabs": {
                "group": "elevenlabs",
                "key": "api_key",
                "title": "ElevenLabs API Key Required",
                "label": "ElevenLabs needs an API key before it can be used.",
            },
            "FishSpeech": {
                "group": "fishspeech",
                "key": "token",
                "title": "FishSpeech Token Required",
                "label": "FishSpeech needs an API token before it can be used.",
            },
            "OpenAI": {
                "group": "openai",
                "key": "token",
                "title": "OpenAI API Key Required",
                "label": "OpenAI TTS needs an OpenAI API key before it can be used.",
            },
            "NovelAI": {
                "group": "novelai",
                "key": "token",
                "title": "NovelAI Token Required",
                "label": "NovelAI TTS needs a NovelAI token before it can be used.",
            },
            "InWorld": {
                "group": "inworld",
                "key": "token",
                "title": "InWorld API Key Required",
                "label": "InWorld TTS needs an API key before it can be used.",
            },
        }

        desc, _image_path = ALL_TTS_PROVIDERS.get(provider_name, ("", None))
        active = _active_tts_display()
        is_active = provider_name == active

        SteamHeader(
            self,
            title=provider_name,
            subtitle=desc or f"Configure {provider_name} as a text-to-speech provider.",
            action_text="✓ Active" if is_active else f"Use {provider_name}",
            action_command=self.activate_provider,
        ).pack(fill="x")

        nav_frame = ctk.CTkFrame(self, fg_color="transparent")
        nav_frame.pack(fill="x", padx=24, pady=(12, 0))
        back_category = "local" if provider_name in [n for n, _, _ in LOCAL_TTS_PROVIDERS] else "cloud"
        ctk.CTkButton(
            nav_frame,
            text="← Back",
            width=100,
            fg_color="gray30",
            command=lambda: self.app.show_dynamic_page(TTSCategoryPage, category=back_category),
        ).pack(side="left")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=20)

        left = ctk.CTkScrollableFrame(body, fg_color=theme.CARD_BG, corner_radius=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        content = ctk.CTkFrame(left, fg_color="transparent")
        content.pack(fill="both", expand=True)
        self.load_provider_ui(content, provider_name)

        right_outer = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=10, width=340)
        right_outer.pack(side="right", fill="y", padx=(10, 0))
        right_outer.pack_propagate(False)

        right = ctk.CTkScrollableFrame(right_outer, fg_color="transparent", corner_radius=10)
        right.pack(fill="both", expand=True)

        ctk.CTkLabel(
            right,
            text="Provider Summary",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 8))

        self.status_label = ctk.CTkLabel(
            right,
            text=f"{provider_name} is active." if is_active else "Not active yet.",
            text_color=theme.SUCCESS if is_active else theme.MUTED_TEXT,
            wraplength=280,
            justify="left",
        )
        self.status_label.pack(anchor="w", padx=16, pady=8)

        self._build_auth_tools(right)

        quota_card_cls = _QUOTA_CARD_MAP.get(provider_name)
        if quota_card_cls:
            ctk.CTkFrame(right, fg_color="#2d3f52", height=1).pack(fill="x", padx=16, pady=(12, 0))
            ctk.CTkLabel(
                right,
                text="API Quota & Status",
                font=ctk.CTkFont(size=16, weight="bold"),
                text_color=theme.TEXT,
            ).pack(anchor="w", padx=16, pady=(14, 6))
            quota_card_cls(right).pack(fill="x", padx=16, pady=(0, 8))

        self._build_test_speak_tools(right)

        ctk.CTkButton(
            right,
            text="Back to TTS Library",
            command=lambda: self.app.show_page("TTS"),
            fg_color="gray30",
        ).pack(fill="x", padx=16, pady=16)

    def activate_provider(self):
        if not self._ensure_auth_token():
            return

        engine_key = TTS_ENGINE_KEYS.get(self.provider_name, "")
        if save_settings:
            save_settings("tts_model", self.provider_name)
            if engine_key:
                save_settings("tts_engine", engine_key)

        if gen_response:
            gen_response.provider = self.provider_name

        self.status_label.configure(
            text=f"{self.provider_name} selected as active TTS.",
            text_color=theme.SUCCESS,
        )

    def _ensure_auth_token(self):
        requirement = self.AUTH_REQUIREMENTS.get(self.provider_name)
        if not requirement:
            return True

        if not get_auth or not save_auth:
            self.status_label.configure(text="Auth system is not available.")
            return False

        existing = get_auth(requirement["group"], requirement["key"])
        if existing:
            return True

        AuthTokenDialog(
            self,
            title=requirement["title"],
            label=requirement["label"],
            on_save=lambda token: self._save_auth_and_activate(requirement, token),
        )
        return False

    def _save_auth_and_activate(self, requirement, token):
        save_auth(requirement["group"], requirement["key"], token)
        self.status_label.configure(text="Token saved. Activating provider...")
        self.activate_provider()

    def load_provider_ui(self, parent, provider):
        if provider == "ElevenLabs":
            ElevenLabsTTSSettings(parent).pack(fill="both", expand=True)
        elif provider == "NovelAI":
            NovelAITTSSettings(parent).pack(fill="both", expand=True)
        elif provider == "EdgeTTS" or provider == "Edge TTS":
            EdgeTTSSettings(parent).pack(fill="both", expand=True)
        elif provider == "FishSpeech":
            FishSpeechSettings(parent).pack(fill="both", expand=True)
        elif provider == "OpenAI" or provider == "OpenAI TTS":
            OpenAITTSSettings(parent).pack(fill="both", expand=True)
        elif provider == "InWorld" or provider == "Inworld":
            InWorldTTSSettings(parent).pack(fill="both", expand=True)
        elif provider == "Kokoro":
            KokoroTTSSettings(parent).pack(fill="both", expand=True)
        elif provider == "Qwen3 TTS":
            Qwen3TTSSettings(parent).pack(fill="both", expand=True)
        else:
            self._fallback_provider_ui(parent, provider)

    def _fallback_provider_ui(self, parent, provider, error=None):
        text = f"{provider} TTS settings page not wired yet."
        if error:
            text += f"\n\n{error}"
        ctk.CTkLabel(parent, text=text, text_color=theme.MUTED_TEXT, wraplength=520, justify="left").pack(
            anchor="w", padx=18, pady=18
        )

    def _build_auth_tools(self, parent):
        requirement = self.AUTH_REQUIREMENTS.get(self.provider_name)
        if not requirement:
            return

        ctk.CTkLabel(
            parent,
            text="Authentication",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 6))

        has_token = bool(get_auth and get_auth(requirement["group"], requirement["key"]))
        self.auth_status_label = ctk.CTkLabel(
            parent,
            text="Token saved." if has_token else "No token saved.",
            text_color=theme.SUCCESS if has_token else theme.MUTED_TEXT,
        )
        self.auth_status_label.pack(anchor="w", padx=16, pady=(0, 8))

        ctk.CTkButton(
            parent,
            text="Clear Token",
            fg_color=theme.DANGER,
            hover_color="#922b21",
            command=lambda: self._clear_auth_token(requirement),
        ).pack(fill="x", padx=16, pady=4)

    def _clear_auth_token(self, requirement):
        if save_auth:
            save_auth(requirement["group"], requirement["key"], "")
        self.auth_status_label.configure(text="Token cleared.", text_color=theme.MUTED_TEXT)

    def _build_test_speak_tools(self, parent):
        ctk.CTkLabel(
            parent,
            text="Test Voice",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 6))

        self.test_textbox = ctk.CTkTextbox(parent, height=120, fg_color="#0e141b", text_color=theme.TEXT, wrap="word")
        self.test_textbox.pack(fill="x", padx=16, pady=(0, 8))
        self.test_textbox.insert("0.0", "This is a quick test of the current TTS provider.")

        ctk.CTkButton(parent, text="Speak Test", command=self.speak_test).pack(fill="x", padx=16, pady=4)

    def speak_test(self):
        text = self.test_textbox.get("0.0", "end-1c").strip()
        if not text:
            self.status_label.configure(text="Enter test text first.")
            return
        if not gen_response:
            self.status_label.configure(text="Response file was not available.")
            return

        self.activate_provider()
        self.status_label.configure(text=f"Sending test speak to {self.provider_name}...")

        def runner():
            try:
                result = asyncio.run(gen_response.speak(text))
                self.after(0, lambda: self.status_label.configure(text=f"Test speak sent to {self.provider_name}."))
            except Exception as e:
                self.after(0, lambda: self.status_label.configure(text=f"Test speak failed: {e}"))

        threading.Thread(target=runner, daemon=True).start()

class BaseTTSSettings(ctk.CTkFrame):
    def __init__(self, master, title):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text=title,
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=theme.TEXT,
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 10))

        self.output_devices = list_output_devices()
        device_labels = ["System Default"] + [d["label"] for d in self.output_devices]

        saved_output = get_settings("tts_output_device") if get_settings else None

        saved_label = "System Default"
        if saved_output not in ("", None):
            for d in self.output_devices:
                if str(d["index"]) == str(saved_output):
                    saved_label = d["label"]
                    break

        self.tts_output_device_var = ctk.StringVar(value=saved_label)

        self._combo_row(
            "Output Device",
            self.tts_output_device_var,
            device_labels,
            1,
        )

    def _setting(self, key, default):
        if not get_settings:
            return default
        value = get_settings(key)
        return default if value in (None, "") else value

    def _readonly_row(self, label, value, row):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=18, pady=6)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            frame,
            text=label,
            text_color=theme.TEXT,
            width=180,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            frame,
            text=value,
            text_color=theme.MUTED_TEXT,
            wraplength=520,
            justify="left",
            anchor="w",
        ).grid(row=0, column=1, sticky="ew", padx=(10, 0))

    def _entry_row(self, label, var, row):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=18, pady=6)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            frame,
            text=label,
            text_color=theme.TEXT,
            width=180,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkEntry(
            frame,
            textvariable=var,
        ).grid(row=0, column=1, sticky="ew", padx=(10, 0))

    def _file_row(self, label, var, row, filetypes=None):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=18, pady=6)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            frame,
            text=label,
            text_color=theme.TEXT,
            width=180,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkEntry(
            frame,
            textvariable=var,
        ).grid(row=0, column=1, sticky="ew", padx=(10, 8))

        def browse():
            selected = filedialog.askopenfilename(
                title=f"Select {label}",
                filetypes=filetypes or [
                    ("Audio files", "*.wav *.mp3 *.flac *.ogg *.m4a"),
                    ("WAV files", "*.wav"),
                    ("All files", "*.*"),
                ],
            )
            if selected:
                var.set(selected)

        ctk.CTkButton(
            frame,
            text="Pick File",
            width=90,
            command=browse,
        ).grid(row=0, column=2, sticky="e")

    def _combo_row(self, label, var, values, row):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=18, pady=6)

        ctk.CTkLabel(
            frame,
            text=label,
            text_color=theme.TEXT,
            width=180,
            anchor="w",
        ).pack(side="left")

        ctk.CTkComboBox(
            frame,
            variable=var,
            values=values,
            width=240,
        ).pack(side="left", padx=10)

    def _slider_row(self, label, var, min_value, max_value, row, is_int=False):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=18, pady=6)
        frame.grid_columnconfigure(1, weight=1)

        value_label = ctk.CTkLabel(
            frame,
            text=self._format(label, var.get(), is_int),
            text_color=theme.TEXT,
            width=190,
            anchor="w",
        )
        value_label.grid(row=0, column=0, sticky="w")

        ctk.CTkSlider(
            frame,
            from_=min_value,
            to=max_value,
            variable=var,
            command=lambda v: value_label.configure(
                text=self._format(label, v, is_int)
            ),
        ).grid(row=0, column=1, sticky="ew", padx=10)

    def _checkbox_row(self, label, var, row):
        ctk.CTkCheckBox(
            self,
            text=label,
            variable=var,
            text_color=theme.TEXT,
        ).grid(row=row, column=0, sticky="w", padx=18, pady=8)

    def _format(self, label, value, is_int=False):
        return f"{label}: {int(float(value))}" if is_int else f"{label}: {float(value):.2f}"

    def _save_payload(self, payload, label):
        selected = self.tts_output_device_var.get()
        payload["tts_output_device"] = parse_output_device(selected)
        if save_settings:
            for key, value in payload.items():
                save_settings(key, value)
        self.status_label.configure(text=f"{label} settings saved.")


class ElevenLabsTTSSettings(BaseTTSSettings):
    def __init__(self, master):
        super().__init__(master, "ElevenLabs TTS Settings")

        self.voice_id_var = ctk.StringVar(value=self._setting("eleven_voice_id", ""))
        self.model_id_var = ctk.StringVar(value=self._setting("eleven_model_id", "eleven_v3"))
        self.stability_var = ctk.DoubleVar(value=float(self._setting("eleven_stability", 0.7)))
        self.similarity_var = ctk.DoubleVar(value=float(self._setting("eleven_similarity", 0.8)))
        self.style_var = ctk.DoubleVar(value=float(self._setting("eleven_style", 0.34)))
        self.speed_var = ctk.DoubleVar(value=float(self._setting("eleven_speed", 1.0)))
        self.speaker_boost_var = ctk.BooleanVar(value=bool(self._setting("eleven_speaker_boost", False)))
        self.streaming_var = ctk.BooleanVar(value=bool(self._setting("eleven_streaming", True)))

        row = 2
        self._entry_row("Voice ID", self.voice_id_var, row); row += 1
        self._combo_row(
            "Model",
            self.model_id_var,
            [
                "eleven_v3",
                "eleven_multilingual_v2",
                "eleven_flash_v2_5",
                "eleven_turbo_v2_5",
                "eleven_turbo_v2",
                "eleven_tts_v1",
            ],
            row,
        ); row += 1
        self._slider_row("Stability", self.stability_var, 0.0, 1.0, row); row += 1
        self._slider_row("Similarity", self.similarity_var, 0.0, 1.0, row); row += 1
        self._slider_row("Style", self.style_var, 0.0, 1.0, row); row += 1
        self._slider_row("Speed", self.speed_var, 0.5, 2.0, row); row += 1
        self._checkbox_row("Use Speaker Boost", self.speaker_boost_var, row); row += 1
        self._checkbox_row("Use Streaming Playback", self.streaming_var, row); row += 1

        ctk.CTkButton(self, text="Save ElevenLabs Settings", command=self.save).grid(
            row=row, column=0, sticky="w", padx=18, pady=(16, 8)
        )
        row += 1

        self.status_label = ctk.CTkLabel(
            self,
            text="No changes saved yet.",
            text_color=theme.MUTED_TEXT,
        )
        self.status_label.grid(row=row, column=0, sticky="w", padx=18)

    def save(self):
        self._save_payload(
            {
                "eleven_voice_id": self.voice_id_var.get().strip(),
                "eleven_model_id": self.model_id_var.get().strip(),
                "eleven_stability": float(self.stability_var.get()),
                "eleven_similarity": float(self.similarity_var.get()),
                "eleven_style": float(self.style_var.get()),
                "eleven_speed": float(self.speed_var.get()),
                "eleven_speaker_boost": bool(self.speaker_boost_var.get()),
                "eleven_streaming": bool(self.streaming_var.get()),
            },
            "ElevenLabs",
        )


class NovelAITTSSettings(BaseTTSSettings):
    def __init__(self, master):
        super().__init__(master, "NovelAI TTS Settings")

        self.voice_seed_var = ctk.StringVar(value=self._setting("novel_tts_voice_seed", "galette"))
        self.version_var = ctk.StringVar(value=self._setting("novel_tts_version", "v2"))
        self.mode_var = ctk.StringVar(value=self._setting("novel_tts_mode", "Streamed"))
        self.output_format_var = ctk.StringVar(value=self._setting("novel_tts_output_format", "mp3"))

        row = 2
        self._entry_row("Voice Seed", self.voice_seed_var, row); row += 1
        self._combo_row("Version", self.version_var, ["v1", "v2"], row); row += 1
        self._combo_row("Mode", self.mode_var, ["Streamed", "Full"], row); row += 1
        self._combo_row("Output Format", self.output_format_var, ["mp3", "wav", "opus"], row); row += 1

        ctk.CTkButton(self, text="Save NovelAI TTS Settings", command=self.save).grid(
            row=row, column=0, sticky="w", padx=18, pady=(16, 8)
        )
        row += 1

        self.status_label = ctk.CTkLabel(
            self,
            text="No changes saved yet.",
            text_color=theme.MUTED_TEXT,
        )
        self.status_label.grid(row=row, column=0, sticky="w", padx=18)

    def save(self):
        self._save_payload(
            {
                "novel_tts_voice_seed": self.voice_seed_var.get().strip(),
                "novel_tts_version": self.version_var.get().strip(),
                "novel_tts_mode": self.mode_var.get().strip(),
                "novel_tts_output_format": self.output_format_var.get().strip(),
            },
            "NovelAI TTS",
        )


class EdgeTTSSettings(BaseTTSSettings):
    def __init__(self, master):
        super().__init__(master, "EdgeTTS Settings")

        self.voice_var = ctk.StringVar(value=self._setting("edge_voice", "en-US-AriaNeural"))
        self.rate_var = ctk.StringVar(value=self._setting("edge_rate", "+0%"))
        self.pitch_var = ctk.StringVar(value=self._setting("edge_pitch", "+0Hz"))
        self.volume_var = ctk.StringVar(value=self._setting("edge_volume", "+0%"))

        row = 2
        self._entry_row("Voice", self.voice_var, row); row += 1
        self._entry_row("Rate", self.rate_var, row); row += 1
        self._entry_row("Pitch", self.pitch_var, row); row += 1
        self._entry_row("Volume", self.volume_var, row); row += 1

        ctk.CTkButton(self, text="Save EdgeTTS Settings", command=self.save).grid(
            row=row, column=0, sticky="w", padx=18, pady=(16, 8)
        )
        row += 1

        self.status_label = ctk.CTkLabel(
            self,
            text="No changes saved yet.",
            text_color=theme.MUTED_TEXT,
        )
        self.status_label.grid(row=row, column=0, sticky="w", padx=18)

    def save(self):
        self._save_payload(
            {
                "edge_voice": self.voice_var.get().strip(),
                "edge_rate": self.rate_var.get().strip(),
                "edge_pitch": self.pitch_var.get().strip(),
                "edge_volume": self.volume_var.get().strip(),
            },
            "EdgeTTS",
        )

class FishSpeechSettings(BaseTTSSettings):
    def __init__(self, master):
        super().__init__(master, "FishSpeech Settings")

        self.api_url_var = ctk.StringVar(
            value=self._setting("fish_api_url", "https://api.fish.audio/v1/tts")
        )
        self.model_var = ctk.StringVar(
            value=self._setting("fish_model", "s2-pro")
        )
        self.voice_id_var = ctk.StringVar(
            value=self._setting("fish_voice_id", "")
        )

        self.rate_var = ctk.DoubleVar(
            value=float(self._setting("fish_rate", 24000))
        )
        self.channels_var = ctk.DoubleVar(
            value=float(self._setting("fish_channels", 2))
        )
        self.chunk_length_var = ctk.DoubleVar(
            value=float(self._setting("fish_chunk_length", 200))
        )
        self.min_chunk_length_var = ctk.DoubleVar(
            value=float(self._setting("fish_min_chunk_length", 50))
        )

        self.latency_var = ctk.StringVar(
            value=self._setting("fish_latency", "low")
        )
        self.format_var = ctk.StringVar(
            value=self._setting("fish_format", "pcm")
        )
        self.normalize_var = ctk.BooleanVar(
            value=bool(self._setting("fish_normalize", True))
        )
        self.condition_previous_var = ctk.BooleanVar(
            value=bool(self._setting("fish_condition_previous_chunks", True))
        )

        row = 2
        self._entry_row("API URL", self.api_url_var, row); row += 1
        self._combo_row("Model", self.model_var, ["s2-pro"], row); row += 1
        self._entry_row("Voice / Reference ID", self.voice_id_var, row); row += 1
        self._slider_row("Sample Rate", self.rate_var, 16000, 48000, row, is_int=True); row += 1
        self._slider_row("Channels", self.channels_var, 1, 2, row, is_int=True); row += 1
        self._slider_row("Chunk Length", self.chunk_length_var, 50, 500, row, is_int=True); row += 1
        self._slider_row("Min Chunk Length", self.min_chunk_length_var, 20, 200, row, is_int=True); row += 1

        self._combo_row("Latency", self.latency_var, ["low", "normal"], row); row += 1
        self._combo_row("Format", self.format_var, ["pcm"], row); row += 1

        self._checkbox_row("Normalize Audio", self.normalize_var, row); row += 1
        self._checkbox_row("Condition on Previous Chunks", self.condition_previous_var, row); row += 1

        ctk.CTkButton(
            self,
            text="Save FishSpeech Settings",
            command=self.save,
        ).grid(row=row, column=0, sticky="w", padx=18, pady=(16, 8))
        row += 1

        self.status_label = ctk.CTkLabel(
            self,
            text="No changes saved yet.",
            text_color=theme.MUTED_TEXT,
        )
        self.status_label.grid(row=row, column=0, sticky="w", padx=18)

    def save(self):
        self._save_payload(
            {
                "fish_api_url": self.api_url_var.get().strip(),
                "fish_model": self.model_var.get().strip(),
                "fish_voice_id": self.voice_id_var.get().strip(),
                "fish_rate": int(self.rate_var.get()),
                "fish_channels": int(self.channels_var.get()),
                "fish_chunk_length": int(self.chunk_length_var.get()),
                "fish_min_chunk_length": int(self.min_chunk_length_var.get()),
                "fish_latency": self.latency_var.get().strip(),
                "fish_format": self.format_var.get().strip(),
                "fish_normalize": bool(self.normalize_var.get()),
                "fish_condition_previous_chunks": bool(self.condition_previous_var.get()),
            },
            "FishSpeech",
        )

class OpenAITTSSettings(BaseTTSSettings):
    def __init__(self, master):
        super().__init__(master, "OpenAI TTS Settings")

        self.model_var = ctk.StringVar(value=self._setting("openai_tts_model", "gpt-4o-mini-tts"))
        self.voice_var = ctk.StringVar(value=self._setting("openai_tts_voice", "nova"))
        self.speed_var = ctk.DoubleVar(value=float(self._setting("openai_tts_speed", 1.0)))
        self.instructions_var = ctk.StringVar(value=self._setting("openai_tts_instructions", ""))

        row = 2
        self._combo_row(
            "Model",
            self.model_var,
            ["gpt-4o-mini-tts", "tts-1", "tts-1-hd"],
            row,
        ); row += 1
        self._combo_row(
            "Voice",
            self.voice_var,
            ["alloy", "ash", "ballad", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer"],
            row,
        ); row += 1
        self._slider_row("Speed", self.speed_var, 0.25, 4.0, row); row += 1
        self._entry_row("Instructions", self.instructions_var, row); row += 1

        ctk.CTkButton(self, text="Save OpenAI TTS Settings", command=self.save).grid(
            row=row, column=0, sticky="w", padx=18, pady=(16, 8)
        )
        row += 1

        self.status_label = ctk.CTkLabel(
            self,
            text="No changes saved yet.",
            text_color=theme.MUTED_TEXT,
        )
        self.status_label.grid(row=row, column=0, sticky="w", padx=18)

    def save(self):
        self._save_payload(
            {
                "openai_tts_model": self.model_var.get().strip(),
                "openai_tts_voice": self.voice_var.get().strip(),
                "openai_tts_speed": float(self.speed_var.get()),
                "openai_tts_instructions": self.instructions_var.get().strip(),
            },
            "OpenAI TTS",
        )

class InWorldTTSSettings(BaseTTSSettings):
    def __init__(self, master):
        super().__init__(master, "InWorld TTS Settings")

        saved_voice = (
            self._setting("inworld_tts_voice_id", "")
            or self._setting("inworld_tts_voice", "Ashley")
        )
        saved_model = (
            self._setting("inworld_tts_model_id", "")
            or self._setting("inworld_tts_model", "inworld-tts-1.5-mini")
        )

        self.model_var = ctk.StringVar(value=saved_model)
        self.voice_var = ctk.StringVar(value=saved_voice)

        self.audio_encoding_var = ctk.StringVar(
            value=self._setting("inworld_tts_audio_encoding", "MP3")
        )
        self.sample_rate_var = ctk.DoubleVar(
            value=float(self._setting("inworld_tts_sample_rate", 24000))
        )
        self.bit_rate_var = ctk.DoubleVar(
            value=float(self._setting("inworld_tts_bit_rate", 32000))
        )
        self.language_var = ctk.StringVar(
            value=self._setting("inworld_tts_language", "")
        )
        self.delivery_mode_var = ctk.StringVar(
            value=self._setting("inworld_tts_delivery_mode", "BALANCED")
        )
        self.temperature_var = ctk.DoubleVar(
            value=float(self._setting("inworld_tts_temperature", 1.0))
        )
        self.text_normalization_var = ctk.BooleanVar(
            value=bool(self._setting("inworld_tts_apply_text_normalization", True))
        )
        self.timestamp_type_var = ctk.StringVar(
            value=self._setting("inworld_tts_timestamp_type", "NONE")
        )
        self.warmup_var = ctk.BooleanVar(
            value=bool(self._setting("inworld_tts_warmup", True))
        )

        row = 2

        self._entry_row("Voice ID / Custom Voice", self.voice_var, row); row += 1

        self._combo_row(
            "Model",
            self.model_var,
            [
                "inworld-tts-2",
                "inworld-tts-1.5-max",
                "inworld-tts-1.5-mini",
                "inworld-tts-1",
            ],
            row,
        ); row += 1

        self._combo_row(
            "Delivery Mode",
            self.delivery_mode_var,
            ["STABLE", "BALANCED", "CREATIVE"],
            row,
        ); row += 1

        self._slider_row("Temperature", self.temperature_var, 0.1, 2.0, row); row += 1

        self._combo_row(
            "Audio Encoding",
            self.audio_encoding_var,
            ["MP3", "PCM", "LINEAR16"],
            row,
        ); row += 1

        self._slider_row("Sample Rate", self.sample_rate_var, 8000, 48000, row, is_int=True); row += 1
        self._slider_row("Bit Rate", self.bit_rate_var, 16000, 128000, row, is_int=True); row += 1

        self._entry_row("Language / Locale", self.language_var, row); row += 1

        self._combo_row(
            "Timestamp Type",
            self.timestamp_type_var,
            ["NONE", "CHARACTER", "WORD", "PHONEME", "VISEME"],
            row,
        ); row += 1

        self._checkbox_row("Apply Text Normalization", self.text_normalization_var, row); row += 1
        self._checkbox_row("Warmup Connection Before Request", self.warmup_var, row); row += 1

        ctk.CTkLabel(
            self,
            text=(
                "Voice ID can be a built-in voice like Sarah/Dennis or a custom/cloned "
                "voiceId returned by InWorld's Voices API. For TTS-2, delivery mode is "
                "the primary expressiveness control; older models may use temperature."
            ),
            text_color=theme.MUTED_TEXT,
            wraplength=560,
            justify="left",
        ).grid(row=row, column=0, sticky="w", padx=18, pady=(8, 8))
        row += 1

        ctk.CTkButton(self, text="Save InWorld Settings", command=self.save).grid(
            row=row, column=0, sticky="w", padx=18, pady=(16, 8)
        )
        row += 1

        self.status_label = ctk.CTkLabel(
            self,
            text="No changes saved yet.",
            text_color=theme.MUTED_TEXT,
        )
        self.status_label.grid(row=row, column=0, sticky="w", padx=18)

    def save(self):
        voice_id = self.voice_var.get().strip()
        model_id = self.model_var.get().strip()

        self._save_payload(
            {
                "inworld_tts_voice": voice_id,
                "inworld_tts_model": model_id,
                "inworld_tts_voice_id": voice_id,
                "inworld_tts_model_id": model_id,
                "inworld_tts_audio_encoding": self.audio_encoding_var.get().strip(),
                "inworld_tts_sample_rate": int(self.sample_rate_var.get()),
                "inworld_tts_bit_rate": int(self.bit_rate_var.get()),
                "inworld_tts_language": self.language_var.get().strip(),
                "inworld_tts_delivery_mode": self.delivery_mode_var.get().strip().upper(),
                "inworld_tts_temperature": float(self.temperature_var.get()),
                "inworld_tts_apply_text_normalization": bool(self.text_normalization_var.get()),
                "inworld_tts_timestamp_type": self.timestamp_type_var.get().strip(),
                "inworld_tts_warmup": bool(self.warmup_var.get()),
            },
            "InWorld TTS",
        )

class KokoroTTSSettings(BaseTTSSettings):
    def __init__(self, master):
        super().__init__(master, "Kokoro Local TTS Settings")

        self.voice_var = ctk.StringVar(value=self._setting("kokoro_voice", "af_heart"))
        self.speed_var = ctk.DoubleVar(value=float(self._setting("kokoro_speed", 1.0)))
        self.model_dir_var = ctk.StringVar(value=str(DEFAULT_KOKORO_MODEL_DIR))
        self.model_file_var = ctk.StringVar(value=self._setting("kokoro_model_file", "kokoro-v1.0.onnx"))
        self.voices_file_var = ctk.StringVar(value=self._setting("kokoro_voices_file", "voices-v1.0.bin"))

        row = 2
        self._combo_row("Voice", self.voice_var, [
            "af_heart", "af_bella", "af_nova", "af_sarah", "af_sky",
            "am_adam", "am_echo", "am_eric", "bf_emma", "bf_isabella",
            "bm_george", "jf_alpha", "jm_kumo", "zf_xiaobei", "ff_siwis",
        ], row); row += 1
        self._slider_row("Speed", self.speed_var, 0.5, 2.0, row); row += 1
        self._readonly_row("Model Directory", self.model_dir_var.get(), row); row += 1
        self._entry_row("Model File", self.model_file_var, row); row += 1
        self._entry_row("Voices File", self.voices_file_var, row); row += 1

        ctk.CTkButton(self, text="Save Kokoro Settings", command=self.save).grid(
            row=row, column=0, sticky="w", padx=18, pady=(16, 8)
        )
        row += 1
        self.status_label = ctk.CTkLabel(self, text="No changes saved yet.", text_color=theme.MUTED_TEXT)
        self.status_label.grid(row=row, column=0, sticky="w", padx=18)

    def save(self):
        payload = {
            "kokoro_voice": self.voice_var.get().strip(),
            "kokoro_speed": float(self.speed_var.get()),
            "kokoro_model_file": self.model_file_var.get().strip(),
            "kokoro_voices_file": self.voices_file_var.get().strip(),
        }
        self._save_payload(payload, "Kokoro")


class Qwen3TTSSettings(BaseTTSSettings):
    def __init__(self, master):
        super().__init__(master, "Qwen3 Local TTS Settings")

        self.preset_var = ctk.StringVar(value=self._setting("qwen3_tts_preset", ""))
        self.model_id_var = ctk.StringVar(value=self._setting("qwen3_tts_model_id", "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"))
        self.backend_var = ctk.StringVar(value=self._setting("qwen3_tts_backend", "faster"))
        self.device_var = ctk.StringVar(value=self._setting("qwen3_tts_device", "auto"))
        self.speaker_var = ctk.StringVar(value=self._setting("qwen3_tts_speaker", "ryan"))
        self.language_var = ctk.StringVar(value=self._setting("qwen3_tts_language", "English"))
        self.instruct_var = ctk.StringVar(value=self._setting("qwen3_tts_instruct", ""))
        self.ref_audio_var = ctk.StringVar(value=self._setting("qwen3_tts_ref_audio", ""))
        self.ref_text_var = ctk.StringVar(value=self._setting("qwen3_tts_ref_text", ""))
        self.xvec_only_var = ctk.BooleanVar(value=bool(self._setting("qwen3_tts_xvec_only", True)))
        self.chunk_var = ctk.DoubleVar(value=float(self._setting("qwen3_tts_streaming_chunk_size", 8)))

        row = 2
        self._entry_row("Preset Name", self.preset_var, row); row += 1
        self._entry_row("Model ID", self.model_id_var, row); row += 1
        self._combo_row("Backend", self.backend_var, ["auto", "faster", "upstream"], row); row += 1
        self._entry_row("Device", self.device_var, row); row += 1
        self._combo_row("Speaker", self.speaker_var, ["aiden", "dylan", "eric", "ono_anna", "ryan", "serena", "sohee", "uncle_fu", "vivian"], row); row += 1
        self._combo_row("Language", self.language_var, ["Chinese", "English", "Japanese", "Korean", "German", "French", "Russian", "Portuguese", "Spanish", "Italian"], row); row += 1
        self._entry_row("Instruction", self.instruct_var, row); row += 1
        self._file_row("Reference Audio", self.ref_audio_var, row); row += 1
        self._entry_row("Reference Text", self.ref_text_var, row); row += 1
        self._checkbox_row("XVec Only / Faster Clone", self.xvec_only_var, row); row += 1
        self._slider_row("Streaming Chunk Size", self.chunk_var, 1, 32, row, is_int=True); row += 1

        ctk.CTkButton(self, text="Save Qwen3 TTS Settings", command=self.save).grid(
            row=row, column=0, sticky="w", padx=18, pady=(16, 8)
        )
        row += 1
        self.status_label = ctk.CTkLabel(self, text="No changes saved yet.", text_color=theme.MUTED_TEXT)
        self.status_label.grid(row=row, column=0, sticky="w", padx=18)

    def save(self):
        self._save_payload(
            {
                "qwen3_tts_preset": self.preset_var.get().strip(),
                "qwen3_tts_model_id": self.model_id_var.get().strip(),
                "qwen3_tts_backend": self.backend_var.get().strip(),
                "qwen3_tts_device": self.device_var.get().strip(),
                "qwen3_tts_speaker": self.speaker_var.get().strip(),
                "qwen3_tts_language": self.language_var.get().strip(),
                "qwen3_tts_instruct": self.instruct_var.get().strip(),
                "qwen3_tts_ref_audio": self.ref_audio_var.get().strip(),
                "qwen3_tts_ref_text": self.ref_text_var.get().strip(),
                "qwen3_tts_xvec_only": bool(self.xvec_only_var.get()),
                "qwen3_tts_streaming_chunk_size": int(self.chunk_var.get()),
            },
            "Qwen3 TTS",
        )