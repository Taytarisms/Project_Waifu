import customtkinter as ctk
import queue
import random
import threading
import time
import uuid
import importlib
from files.ui import theme
from files.ui.components.cards import ProviderCard
from files.ui.components.header import SteamHeader
from files.ui.components.auth_dialog import AuthTokenDialog
from pathlib import Path

LLM_DIR = Path(__file__).resolve().parents[2] / "llm"

try:
    from files.system_setup.settings import get_auth, save_auth
except Exception:
    get_auth = None
    save_auth = None

try:
    from files.system_setup.initializer import change_llm
except Exception:
    change_llm = None

try:
    from files.system_setup.settings import get_settings, save_settings
except Exception:
    get_settings = None
    save_settings = None

try:
    from files.main_loop.main_loop import is_turn_active, message_queue
except Exception:
    is_turn_active = None
    message_queue = None

ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"

try:
    from files.ui.components.quota_checker import (
        ClaudeCard, GeminiCard, GrokCard, OpenAICard,
    )
    _QUOTA_CARD_MAP = {
        "Claude":  ClaudeCard,
        "Gemini":  GeminiCard,
        "Grok":    GrokCard,
        "OpenAI":  OpenAICard,
    }
except Exception:
    _QUOTA_CARD_MAP = {}

LOCAL_MODEL_ICONS = [
    ASSETS_DIR / "gemma.png",
    ASSETS_DIR / "deepseek-color.png",
    ASSETS_DIR / "llava-color.png",
    ASSETS_DIR / "meta-color-dark.png",
    ASSETS_DIR / "minimax-color.png",
    ASSETS_DIR / "mistral-color.png",
    ASSETS_DIR / "bytedance-color.png",
    ASSETS_DIR / "qwen.png"
]

PAID_PROVIDERS = [
    ("OpenAI",  "Cloud LLM provider for GPT models and fine-tuned models.\nCOSTS MONEY TO USE",       ASSETS_DIR / "openai.png"),
    ("NovelAI", "NovelAI text generation backend and story-style parameters.\nCOSTS MONEY TO USE",     ASSETS_DIR / "novelai.png"),
    ("Claude",  "Anthropic's Claude models via the Messages API.\nCOSTS MONEY TO USE",                ASSETS_DIR / "claude.png"),
    ("Gemini",  "Google's Gemini model family via the GenAI SDK.\nCOSTS MONEY TO USE",                ASSETS_DIR / "gemini.png"),
    ("Grok",    "xAI's Grok models via the xai_sdk.\nCOSTS MONEY TO USE",                             ASSETS_DIR / "grok.png"),
]

LOCAL_PROVIDERS = [
    ("Local",    "Local model runtime for GGUF / llama-cpp style models.\nENTIRELY LOCAL",             LOCAL_MODEL_ICONS),
    ("Personal", "Custom/personal backend route.\nNo true routing attached. DO NOT USE!!",             None),
]

ALL_PROVIDERS = {name: (desc, img) for name, desc, img in PAID_PROVIDERS + LOCAL_PROVIDERS}



class LLMHomePage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app
        self._active_section = "library"
        self._last_activity = time.monotonic()
        self._last_idle_fire = 0.0
        self._monologue_running = False

        ctk.CTkLabel(
            self,
            text="LLM Library",
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=24, pady=(24, 4))

        ctk.CTkLabel(
            self,
            text="Choose a provider category to configure your language model.",
            text_color=theme.MUTED_TEXT,
        ).pack(anchor="w", padx=24, pady=(0, 14))

        tab_row = ctk.CTkFrame(self, fg_color="transparent")
        tab_row.pack(anchor="w", padx=24, pady=(0, 18))

        self.library_tab_btn = ctk.CTkButton(
            tab_row,
            text="Library",
            width=120,
            command=lambda: self._switch_section("library"),
        )
        self.library_tab_btn.pack(side="left", padx=(0, 8))

        self.monologue_tab_btn = ctk.CTkButton(
            tab_row,
            text="Monologue",
            width=140,
            command=lambda: self._switch_section("monologue"),
        )
        self.monologue_tab_btn.pack(side="left")

        self.content_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.content_frame.pack(fill="both", expand=True, padx=0, pady=0)

        self.library_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        self.monologue_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")

        active = (get_settings("chat_model") or "") if get_settings else ""
        self.active_label = ctk.CTkLabel(
            self.library_frame,
            text=f"Active provider: {active}" if active else "No provider selected.",
            text_color=theme.SUCCESS if active else theme.MUTED_TEXT,
            font=ctk.CTkFont(size=13),
        )
        self.active_label.pack(anchor="w", padx=24, pady=(0, 28))

        btn_frame = ctk.CTkFrame(self.library_frame, fg_color="transparent")
        btn_frame.pack(anchor="center", pady=20)

        CATEGORIES = [
            ("🌐  PAID / PROPRIETARY LLMs",  "paid",  "#1a4a6e"),
            ("💾  LOCAL / DOWNLOADABLE LLMs", "local", "#1e3d2f"),
        ]

        for label, tag, color in CATEGORIES:
            btn = ctk.CTkButton(
                btn_frame,
                text=label,
                width=380,
                height=80,
                font=ctk.CTkFont(size=18, weight="bold"),
                fg_color=color,
                hover_color=self._lighten(color),
                corner_radius=12,
                command=lambda t=tag: self.open_category(t),
            )
            btn.pack(pady=12)

        self._build_monologue_section()
        self._switch_section("library")
        self._bind_activity_tracking()
        self.after(5000, self._check_idle_monologue)

    @staticmethod
    def _lighten(hex_color: str) -> str:
        r, g, b = (int(hex_color[i:i+2], 16) for i in (1, 3, 5))
        return "#{:02x}{:02x}{:02x}".format(
            min(r + 30, 255), min(g + 30, 255), min(b + 30, 255)
        )

    def open_category(self, category_tag: str):
        self.app.show_dynamic_page(LLMCategoryPage, category=category_tag)

    def refresh_active_provider(self):
        active = (get_settings("chat_model") or "") if get_settings else ""
        self.active_label.configure(
            text=f"Active provider: {active}" if active else "No provider selected.",
            text_color=theme.SUCCESS if active else theme.MUTED_TEXT,
        )

    def _switch_section(self, section: str):
        self._active_section = section
        self.library_frame.pack_forget()
        self.monologue_frame.pack_forget()
        if section == "monologue":
            self.monologue_frame.pack(fill="both", expand=True)
        else:
            self.library_frame.pack(fill="both", expand=True)
        active_color = theme.ACCENT
        inactive_color = "#253447"
        self.library_tab_btn.configure(fg_color=active_color if section == "library" else inactive_color)
        self.monologue_tab_btn.configure(fg_color=active_color if section == "monologue" else inactive_color)

    def _build_monologue_section(self):
        panel = ctk.CTkFrame(self.monologue_frame, fg_color=theme.CARD_BG, corner_radius=10)
        panel.pack(fill="both", expand=True, padx=24, pady=(0, 24))

        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(18, 10))

        ctk.CTkLabel(
            header,
            text="Monologue",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w")

        ctk.CTkLabel(
            header,
            text="Queue an in-character thought without saving the internal prompt as user memory.",
            text_color=theme.MUTED_TEXT,
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        controls = ctk.CTkFrame(panel, fg_color="transparent")
        controls.pack(fill="x", padx=18, pady=(6, 12))

        self.monologue_idle_enabled_var = ctk.BooleanVar(
            value=bool(get_settings("monologue_idle_enabled")) if get_settings else False
        )
        ctk.CTkSwitch(
            controls,
            text="Idle monologue",
            variable=self.monologue_idle_enabled_var,
            command=self._save_monologue_settings,
            text_color=theme.TEXT,
            progress_color=theme.ACCENT,
        ).pack(side="left", padx=(0, 14))

        ctk.CTkLabel(controls, text="Idle seconds", text_color=theme.MUTED_TEXT).pack(side="left", padx=(0, 8))
        self.monologue_idle_seconds_var = ctk.StringVar(
            value=str(get_settings("monologue_idle_seconds") or 300) if get_settings else "300"
        )
        ctk.CTkEntry(
            controls,
            textvariable=self.monologue_idle_seconds_var,
            width=80,
        ).pack(side="left", padx=(0, 14))

        self.monologue_random_memory_var = ctk.BooleanVar(
            value=bool(get_settings("monologue_use_random_memory")) if get_settings else False
        )
        ctk.CTkSwitch(
            controls,
            text="Include random memories",
            variable=self.monologue_random_memory_var,
            command=self._save_monologue_settings,
            text_color=theme.TEXT,
            progress_color=theme.ACCENT,
        ).pack(side="left", padx=(0, 14))

        ctk.CTkLabel(controls, text="Memory count", text_color=theme.MUTED_TEXT).pack(side="left", padx=(0, 8))
        self.monologue_memory_count_var = ctk.StringVar(
            value=str(get_settings("monologue_memory_count") or 3) if get_settings else "3"
        )
        ctk.CTkEntry(
            controls,
            textvariable=self.monologue_memory_count_var,
            width=60,
        ).pack(side="left")

        topic_row = ctk.CTkFrame(panel, fg_color="transparent")
        topic_row.pack(fill="x", padx=18, pady=(4, 10))

        self.monologue_topic_var = ctk.StringVar()
        self.monologue_topic_entry = ctk.CTkEntry(
            topic_row,
            textvariable=self.monologue_topic_var,
            placeholder_text="Send a one-off topic...",
        )
        self.monologue_topic_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.monologue_topic_entry.bind("<Return>", lambda _e: self.send_monologue_now())

        ctk.CTkButton(
            topic_row,
            text="Send Topic",
            width=120,
            command=self.send_monologue_now,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            topic_row,
            text="Random Topic",
            width=130,
            command=self.send_random_topic,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            topic_row,
            text="Save",
            width=90,
            command=self._save_monologue_settings,
        ).pack(side="left")

        ctk.CTkLabel(
            panel,
            text="Monologue Topics",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=18, pady=(8, 6))

        self.monologue_topics_box = ctk.CTkTextbox(
            panel,
            height=210,
            fg_color="#0e141b",
            text_color=theme.TEXT,
            wrap="word",
        )
        self.monologue_topics_box.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        saved_topics = get_settings("monologue_topics") if get_settings else ""
        if saved_topics:
            self.monologue_topics_box.insert("0.0", str(saved_topics))

        self.monologue_status_label = ctk.CTkLabel(
            panel,
            text="Ready.",
            text_color=theme.MUTED_TEXT,
            wraplength=820,
            justify="left",
        )
        self.monologue_status_label.pack(anchor="w", padx=18, pady=(0, 18))

    def _bind_activity_tracking(self):
        for event_name in ("<Key>", "<Button>", "<MouseWheel>"):
            try:
                self.app.bind_all(event_name, self._mark_activity, add="+")
            except Exception:
                pass

    def _mark_activity(self, _event=None):
        self._last_activity = time.monotonic()

    def _topic_lines(self) -> list[str]:
        if not hasattr(self, "monologue_topics_box"):
            raw = get_settings("monologue_topics") if get_settings else ""
        else:
            raw = self.monologue_topics_box.get("0.0", "end-1c")
        return [line.strip() for line in str(raw or "").splitlines() if line.strip()]

    def _save_monologue_settings(self):
        if not save_settings:
            return
        try:
            idle_seconds = max(30, int(float(self.monologue_idle_seconds_var.get() or 300)))
        except Exception:
            idle_seconds = 300
            self.monologue_idle_seconds_var.set(str(idle_seconds))
        try:
            memory_count = max(1, min(10, int(float(self.monologue_memory_count_var.get() or 3))))
        except Exception:
            memory_count = 3
            self.monologue_memory_count_var.set(str(memory_count))

        save_settings("monologue_idle_enabled", bool(self.monologue_idle_enabled_var.get()))
        save_settings("monologue_idle_seconds", idle_seconds)
        save_settings("monologue_topics", self.monologue_topics_box.get("0.0", "end-1c").strip())
        save_settings("monologue_use_random_memory", bool(self.monologue_random_memory_var.get()))
        save_settings("monologue_memory_count", memory_count)
        self.monologue_status_label.configure(text="Monologue settings saved.", text_color=theme.SUCCESS)

    def send_random_topic(self):
        topics = self._topic_lines()
        self._queue_monologue(random.choice(topics) if topics else "")

    def send_monologue_now(self):
        topic = self.monologue_topic_var.get().strip()
        if not topic:
            topics = self._topic_lines()
            topic = random.choice(topics) if topics else ""
        self.monologue_topic_var.set("")
        self._queue_monologue(topic)

    def _build_monologue_prompt(self, topic: str) -> str:
        topic = (topic or "").strip()
        topic_text = topic if topic else "Choose a small, natural thought that fits the character and recent context."
        return (
            "Write a brief in-character monologue for the live companion while the app is quiet.\n"
            "Use the topic only as inspiration, do not quote these instructions, and do not mention inactivity.\n"
            "Ensure to speak as if it came to mind and you are not responding to a simple query.\n"
            "Keep it suitable to speak aloud in one short turn.\n\n"
            f"Topic:\n{topic_text}"
        )

    def _queue_monologue(self, topic: str, *, idle: bool = False):
        if self._monologue_running:
            self.monologue_status_label.configure(text="A monologue is already queued or running.", text_color=theme.MUTED_TEXT)
            return
        if not message_queue:
            self.monologue_status_label.configure(text="Main message queue is unavailable.", text_color=theme.DANGER)
            return

        self._save_monologue_settings()
        self._monologue_running = True
        turn_id = str(uuid.uuid4())
        resp_q = queue.Queue(maxsize=1)
        topic = (topic or "").strip()
        message_text = topic or "idle monologue"
        meta = {
            "turn_id": turn_id,
            "source": "monologue",
            "llm_prompt": self._build_monologue_prompt(topic),
            "store_input_memory": False,
            "store_output_memory": True,
            "use_live_recall": False,
            "use_random_memory": bool(self.monologue_random_memory_var.get()),
            "memory_count": int(float(self.monologue_memory_count_var.get() or 3)),
            "allow_finetune": False,
            "speak_input": False,
        }
        message_queue.put((f"Monologue: {message_text}", True, resp_q, meta))
        self._last_activity = time.monotonic()
        self._last_idle_fire = self._last_activity
        self.monologue_status_label.configure(
            text=("Idle monologue queued." if idle else "Monologue queued."),
            text_color=theme.SUCCESS,
        )

        def wait_for_response():
            try:
                response = resp_q.get(timeout=240)
                status = f"Last monologue: {response[:500]}" if response else "Monologue completed with no text."
                color = theme.SUCCESS if response else theme.MUTED_TEXT
            except queue.Empty:
                status = "Monologue timed out before a response was returned."
                color = theme.DANGER

            def render():
                self._monologue_running = False
                self.monologue_status_label.configure(text=status, text_color=color)

            self.after(0, render)

        threading.Thread(target=wait_for_response, daemon=True).start()

    def _check_idle_monologue(self):
        try:
            enabled = bool(get_settings("monologue_idle_enabled")) if get_settings else bool(self.monologue_idle_enabled_var.get())
            idle_seconds = int(get_settings("monologue_idle_seconds") or self.monologue_idle_seconds_var.get() or 300)
        except Exception:
            enabled = False
            idle_seconds = 300

        now = time.monotonic()
        try:
            queue_empty = bool(message_queue is None or message_queue.empty())
        except Exception:
            queue_empty = True
        try:
            turn_running = bool(is_turn_active and is_turn_active())
        except Exception:
            turn_running = False

        if (
            enabled
            and queue_empty
            and not turn_running
            and not self._monologue_running
            and now - self._last_activity >= max(30, idle_seconds)
            and now - self._last_idle_fire >= max(30, idle_seconds)
        ):
            topics = self._topic_lines()
            self._queue_monologue(random.choice(topics) if topics else "", idle=True)

        self.after(5000, self._check_idle_monologue)

class LLMCategoryPage(ctk.CTkFrame):
    def __init__(self, master, app, category: str):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app
        self.category = category

        is_paid = category == "paid"
        title    = "Paid / Proprietary LLMs" if is_paid else "Local / Downloadable LLMs"
        subtitle = (
            "Cloud-hosted providers. An API key or account is required."
            if is_paid else
            "Self-hosted models that run entirely on your hardware."
        )
        providers = PAID_PROVIDERS if is_paid else LOCAL_PROVIDERS

        ctk.CTkLabel(
            self,
            text=title,
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=24, pady=(24, 4))

        ctk.CTkLabel(
            self,
            text=subtitle,
            text_color=theme.MUTED_TEXT,
        ).pack(anchor="w", padx=24, pady=(0, 20))

        ctk.CTkButton(
            self,
            text="← Back to LLM Library",
            width=180,
            fg_color="gray30",
            command=lambda: self.app.show_page("LLM"),
        ).pack(anchor="w", padx=24, pady=(0, 16))

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=24, pady=10)

        grid = ctk.CTkFrame(scroll, fg_color="transparent")
        grid.pack(fill="both", expand=True)
        grid.grid_columnconfigure((0, 1), weight=1)

        active_provider = (get_settings("chat_model") or "") if get_settings else ""
        self.provider_cards = {}

        for i, (name, desc, image_path) in enumerate(providers):
            card = ProviderCard(
                grid,
                name=name,
                description=desc,
                image_path=image_path,
                command=lambda n=name: self._open_provider(n),
            )
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=10, pady=10, ipady=4)
            self.provider_cards[name] = card

            if name == active_provider:
                card.set_active(True)

    def _open_provider(self, provider_name: str):
        self.app.show_dynamic_page(LLMProviderPage, provider_name=provider_name)

class LLMProviderPage(ctk.CTkFrame):
    def __init__(self, master, app, provider_name):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app
        self.provider_name = provider_name

        self.AUTH_REQUIREMENTS = {
            "OpenAI": {
                "group": "openai",
                "key":   "token",
                "title": "OpenAI API Key Required",
                "label": "OpenAI needs an API key before it can be used.",
            },
            "NovelAI": {
                "group": "novelai",
                "key":   "token",
                "title": "NovelAI API Token Required",
                "label": "NovelAI now requires an API token. Paste your NovelAI API token before using NovelAI LLM or TTS.",
            },
            "Claude": {
                "group": "anthropic",
                "key":   "token",
                "title": "Anthropic API Key Required",
                "label": "Claude needs an Anthropic API key before it can be used.",
            },
            "Gemini": {
                "group": "google",
                "key":   "token",
                "title": "Google API Key Required",
                "label": "Gemini needs a Google AI Studio API key before it can be used.",
            },
            "Grok": {
                "group": "xai",
                "key":   "token",
                "title": "xAI API Key Required",
                "label": "Grok needs an xAI API key before it can be used.",
            },
        }

        SteamHeader(
            self,
            title=provider_name,
            subtitle=f"Configure {provider_name} as a language model provider.",
            action_text=f"Use {provider_name}",
            action_command=self.activate_provider,
        ).pack(fill="x")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=20)

        # Left: settings panel
        left = ctk.CTkScrollableFrame(body, fg_color=theme.CARD_BG, corner_radius=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        content = ctk.CTkFrame(left, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=0, pady=0)

        self.load_provider_ui(content, provider_name)

        # Right: summary panel
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
            text="Not active yet.",
            text_color=theme.MUTED_TEXT,
            wraplength=250,
            justify="left",
        )
        self.status_label.pack(anchor="w", padx=16, pady=8)

        self._build_auth_tools(right)

        quota_card_cls = _QUOTA_CARD_MAP.get(provider_name)
        if quota_card_cls:
            ctk.CTkFrame(right, fg_color="#2d3f52", height=1).pack(
                fill="x", padx=16, pady=(12, 0)
            )
            ctk.CTkLabel(
                right,
                text="API Quota & Status",
                font=ctk.CTkFont(size=16, weight="bold"),
                text_color=theme.TEXT,
            ).pack(anchor="w", padx=16, pady=(14, 6))
            quota_card_cls(right).pack(fill="x", padx=16, pady=(0, 8))

        if provider_name in ("OpenAI", "Claude", "Gemini", "Grok"):
            fname = f"system_message_{provider_name.lower()}.txt"
            self._build_system_message_tools(right, LLM_DIR / fname)

        if provider_name == "Local":
            local_prompt_file = (
                (get_settings("local_system_prompt_filename") if get_settings else "")
                or "system_message_local.txt"
            )
            self._build_system_message_tools(right, LLM_DIR / local_prompt_file)

        if provider_name == "NovelAI":
            self._build_novelai_character_tools(right)
            
        _category = "paid" if provider_name in {n for n, _, _ in PAID_PROVIDERS} else "local"
        ctk.CTkButton(
            right,
            text=f"← Back",
            fg_color="gray30",
            command=lambda: self.app.show_dynamic_page(LLMCategoryPage, category=_category),
        ).pack(fill="x", padx=16, pady=(8, 4))

        ctk.CTkButton(
            right,
            text="⌂ LLM Library",
            fg_color="gray25",
            command=lambda: self.app.show_page("LLM"),
        ).pack(fill="x", padx=16, pady=(0, 16))

    def activate_provider(self):
        if not self._ensure_auth_token():
            return
        self._finish_activation()

    def _finish_activation(self):
        if change_llm:
            change_llm(self.provider_name)
        if save_settings:
            save_settings("chat_model", self.provider_name)
        self.status_label.configure(
            text=f"{self.provider_name} selected as active LLM."
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

        ctk.CTkButton(
            parent,
            text="Set Token",
            command=lambda: self._prompt_auth_token(requirement),
        ).pack(fill="x", padx=16, pady=4)

    def _clear_auth_token(self, requirement):
        if save_auth:
            save_auth(requirement["group"], requirement["key"], "")
        self.auth_status_label.configure(
            text="Token cleared.", text_color=theme.MUTED_TEXT
        )

    def _prompt_auth_token(self, requirement):
        AuthTokenDialog(
            self,
            title=requirement["title"],
            label=requirement["label"],
            on_save=lambda token: self._save_auth_and_activate(requirement, token)
        )

    def _ensure_auth_token(self) -> bool:
        requirement = self.AUTH_REQUIREMENTS.get(self.provider_name)
        if not requirement:
            return True
        token = get_auth(requirement["group"], requirement["key"]) if get_auth else ""
        if token:
            return True
        self._prompt_auth_token(requirement)
        return False

    def _save_auth_and_activate(self, requirement, token):
        save_auth(requirement["group"], requirement["key"], token)
        if requirement["group"] == "novelai":
            save_auth("novelai", "mail", "")
            save_auth("novelai", "password", "")
            self._refresh_novelai_user_info(token)
        if hasattr(self, "auth_status_label"):
            self.auth_status_label.configure(
                text="Token saved.",
                text_color=theme.SUCCESS,
            )
        self.status_label.configure(text="Token saved. Activating provider...")
        self.activate_provider()

    def _refresh_novelai_user_info(self, token: str):
        if not token or not save_auth:
            return

        import asyncio
        from files.llm.boilerplate_novel import fetch_user_info, extract_username

        loop = asyncio.new_event_loop()
        try:
            user_info = loop.run_until_complete(fetch_user_info(token))
            username = extract_username(user_info)
            if username != "Unknown":
                save_auth("novelai", "username", username)
        except Exception:
            pass
        finally:
            loop.close()

    def load_provider_ui(self, parent, provider):
        PAGE_MAP = {
            "OpenAI":  ("files.ui.pages.openai_settings",  "OpenAISettingsPage"),
            "NovelAI": ("files.ui.pages.novelai_settings", "NovelAISettingsPage"),
            "Local":   ("files.ui.pages.local_settings",   "LocalSettingsPage"),
            "Claude":  ("files.ui.pages.claude_settings",  "ClaudeSettingsPage"),
            "Gemini":  ("files.ui.pages.gemini_settings",  "GeminiSettingsPage"),
            "Grok":    ("files.ui.pages.grok_settings",    "GrokSettingsPage"),
        }
        entry = PAGE_MAP.get(provider)
        if not entry:
            self._fallback_provider_ui(parent, provider)
            return

        module_path, class_name = entry
        try:
            import importlib
            mod   = importlib.import_module(module_path)
            klass = getattr(mod, class_name)
            klass(parent).pack(fill="both", expand=True)
        except Exception as e:
            self._fallback_provider_ui(parent, provider, e)

    def _fallback_provider_ui(self, parent, provider, error=None):
        text = f"{provider} settings page not wired yet."
        if error:
            text += f"\n\n{error}"
        ctk.CTkLabel(
            parent,
            text=text,
            text_color=theme.MUTED_TEXT,
            wraplength=520,
            justify="left",
        ).pack(anchor="w", padx=18, pady=18)

    def _build_novelai_character_tools(self, parent):
        try:
            from files.ui.components.char_mngr import NovelAICharacterManager
            NovelAICharacterManager(parent).pack(fill="both", expand=True, padx=12, pady=(18, 12))
        except Exception as e:
            ctk.CTkLabel(
                parent,
                text=f"Character manager failed to load:\n{e}",
                text_color=theme.MUTED_TEXT,
                wraplength=250,
                justify="left",
            ).pack(anchor="w", padx=16, pady=(18, 8))

    def _build_system_message_tools(self, parent, path):
        self.system_message_path = path

        ctk.CTkLabel(
            parent,
            text="System Message",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=16, pady=(18, 6))

        self.system_status_label = ctk.CTkLabel(
            parent,
            text=self._system_message_status(),
            text_color=theme.MUTED_TEXT,
            wraplength=250,
            justify="left",
        )
        self.system_status_label.pack(anchor="w", padx=16, pady=(0, 8))

        ctk.CTkButton(parent, text="Load",         command=self.load_system_message).pack(fill="x", padx=16, pady=4)
        ctk.CTkButton(parent, text="Save / Create", command=self.save_system_message).pack(fill="x", padx=16, pady=4)

        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=4)
        ctk.CTkButton(btn_row, text="Clear",  command=self.clear_system_message_box).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(btn_row, text="Delete", fg_color=theme.DANGER, hover_color="#922b21", command=self.delete_system_message_file).pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.system_textbox = ctk.CTkTextbox(parent, height=180, fg_color="#0e141b", text_color=theme.TEXT, wrap="word")
        self.system_textbox.pack(fill="x", padx=16, pady=(8, 12))

    def _system_message_status(self):
        if self.system_message_path.exists():
            return f"Loaded from: {self.system_message_path.name}"
        return f"No {self.system_message_path.name} found yet."

    def load_system_message(self):
        if not self.system_message_path.exists():
            self.system_status_label.configure(text="No system message file found.")
            return
        text = self.system_message_path.read_text(encoding="utf-8")
        self.system_textbox.delete("0.0", "end")
        self.system_textbox.insert("0.0", text)
        self.system_status_label.configure(text="System message loaded.")

    def save_system_message(self):
        text = self.system_textbox.get("0.0", "end-1c").strip()
        self.system_message_path.write_text(text, encoding="utf-8")
        applied = self.apply_system_message()
        suffix = " Applied immediately." if applied else " Saved; it will apply on next provider load."
        self.system_status_label.configure(text=f"Saved to {self.system_message_path.name}.{suffix}")

    def clear_system_message_box(self):
        self.system_textbox.delete("0.0", "end")
        self.system_status_label.configure(text="Editor cleared. File was not changed.")

    def delete_system_message_file(self):
        self.system_textbox.delete("0.0", "end")
        if self.system_message_path.exists():
            self.system_message_path.unlink()
            applied = self.apply_system_message()
            suffix = " Runtime prompt cleared." if applied else ""
            self.system_status_label.configure(text=f"No {self.system_message_path.name} found.{suffix}")
        else:
            self.system_status_label.configure(text="No system message file exists to delete.")

    def apply_system_message(self) -> bool:
        module_name = {
            "OpenAI": "files.llm.openai_llm",
            "Claude": "files.llm.claude",
            "Gemini": "files.llm.gemini",
            "Grok": "files.llm.grok",
            "Local": "files.llm.LocalLLM",
        }.get(self.provider_name)
        if not module_name:
            return False
        try:
            module = importlib.import_module(module_name)
            reload_fn = getattr(module, "reload_system_message", None)
            if callable(reload_fn):
                reload_fn(force=True)
                return True
            system_fn = getattr(module, "system_message", None)
            if callable(system_fn):
                text = ""
                if self.system_message_path.exists():
                    text = self.system_message_path.read_text(encoding="utf-8").strip()
                system_fn(text)
                return True
        except Exception as e:
            if hasattr(self, "system_status_label"):
                self.system_status_label.configure(text=f"Saved, but live apply failed: {e}")
        return False
