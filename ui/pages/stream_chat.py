from datetime import datetime
from pathlib import Path
import customtkinter as ctk

from files.ui.components.scrolling import AppScrollableFrame
from PIL import Image

from files.stream_chat import StreamChatEvent, StreamChatMessage, get_stream_chat_hub
from files.system_setup.settings import get_auth, get_bool_setting, get_settings, save_auth, save_settings
from files.ui import theme
from files.ui.components.twitch import TwitchAuthDialog
from files.ui.components.youtube import YouTubeAuthDialog
from files.ui.pages.twitch import MiddleColumn, TwitchPage as LegacyTwitchPage, _chatter_store
from files.stream_chat.providers.youtube import get_youtube_provider
from files.stream_chat.providers.kick import get_kick_provider

try:
    from files.twitch_chat import twitchchat
except Exception:
    twitchchat = None


PROVIDER_INFO = {
    "twitch": ("T", "Twitch", "Receive Twitch chat and EventSub activity.", "#9146FF"),
    "youtube": ("Y", "YouTube", "Monitor an active YouTube live broadcast.", "#FF0033"),
    "kick": ("K", "Kick", "Receive Kick events through an official webhook transport.", "#53FC18"),
}

ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
PROVIDER_ICON_FILES = {
    "twitch": ASSETS_DIR / "twitch=logo-png.png",
    "youtube": ASSETS_DIR / "youtube-png.png",
    "kick": ASSETS_DIR / "kick-logo-.png",
}
PROVIDER_ICON_SIZES = {
    "twitch": (29, 30),
    "youtube": (34, 24),
    "kick": (44, 15),
}


class StreamChatPage(LegacyTwitchPage):
    def __init__(self, master, app):
        ctk.CTkFrame.__init__(self, master, fg_color=theme.APP_BG)
        self.app = app
        self.hub = get_stream_chat_hub()
        self._event_labels = []
        self._event_cards = []
        self._chat_rows = []
        self._events_resize_after_id = None
        self._pending_events_width = None
        self._queue_rows = {}
        self._queue_refresh_after_id = None
        self._page_active = False
        self._chat_paused = bool(get_settings("STREAM_CHAT_PAUSED"))
        self.policy_var = ctk.StringVar(value=self.hub.policy)
        self.paused_var = ctk.BooleanVar(value=self._chat_paused)
        self.ignore_commands_var = ctk.BooleanVar(
            value=get_bool_setting("STREAM_CHAT_IGNORE_COMMANDS", default=False)
        )
        self.channel_var = ctk.StringVar(value=get_auth("twitch", "channel_name") or "")
        self.youtube_video_var = ctk.StringVar(value=get_settings("YOUTUBE_VIDEO_ID") or "")
        self.youtube_auto_var = ctk.BooleanVar(value=bool(get_settings("YOUTUBE_AUTO_CONNECT")))
        self.youtube_monitor_var = ctk.BooleanVar(value=bool(get_settings("YOUTUBE_AUTO_MONITOR")))
        self.kick_channel_var = ctk.StringVar(value=get_settings("KICK_CHANNEL_SLUG") or "")
        self.kick_webhook_var = ctk.StringVar(value=get_settings("KICK_WEBHOOK_URL") or "")
        self._provider_vars: dict[str, ctk.BooleanVar] = {}
        self._provider_images: dict[str, ctk.CTkImage] = {}
        self._provider_status_labels = {}
        self._view_frames = {}
        self._chat_title = "Combined Chat"
        self._events_title = "Stream Events"

        self._build_shell()
        self.hub.set_ui_page(self)
        if twitchchat and hasattr(twitchchat, "set_ui_page"):
            twitchchat.set_ui_page(self)
        self._show_view("live")
        self.refresh_queue_panel()
        self.refresh_platform_status()
        self.after(250, self._start_auto_connect)

    def _build_shell(self):
        header = ctk.CTkFrame(self, fg_color=theme.HEADER_BG, corner_radius=0)
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text="Stream Chat",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=theme.TEXT,
        ).pack(side="left", padx=(16, 18), pady=12)

        self._view_buttons = {}
        for key, label in (("live", "Live"), ("platforms", "Platforms"), ("viewers", "Viewers")):
            button = ctk.CTkButton(
                header,
                text=label,
                width=92,
                height=30,
                corner_radius=6,
                command=lambda selected=key: self._show_view(selected),
            )
            button.pack(side="left", padx=3)
            self._view_buttons[key] = button

        self._global_pause_btn = ctk.CTkButton(
            header,
            text="Resume All" if self._chat_paused else "Pause All",
            width=96,
            height=30,
            fg_color=theme.DANGER if not self._chat_paused else theme.SUCCESS,
            command=self._toggle_pause,
        )
        self._global_pause_btn.pack(side="right", padx=(6, 16))

        self._global_reader_btn = ctk.CTkButton(
            header,
            text="Start Readers",
            width=108,
            height=30,
            fg_color=theme.SUCCESS,
            command=self._toggle_readers,
        )
        self._global_reader_btn.pack(side="right", padx=(6, 0))

        self._provider_summary = ctk.CTkLabel(
            header,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=theme.MUTED_TEXT,
        )
        self._provider_summary.pack(side="right", padx=8)

        self._content = ctk.CTkFrame(self, fg_color="transparent")
        self._content.pack(fill="both", expand=True, padx=14, pady=14)

        self._view_frames["live"] = ctk.CTkFrame(self._content, fg_color="transparent")
        self._view_frames["platforms"] = ctk.CTkFrame(self._content, fg_color="transparent")
        self._view_frames["viewers"] = ctk.CTkFrame(self._content, fg_color="transparent")
        self._build_live_view(self._view_frames["live"])
        self._build_platforms_view(self._view_frames["platforms"])
        self._build_viewers_view(self._view_frames["viewers"])
        self._apply_pause(bool(get_settings("STREAM_CHAT_PAUSED")))

    def _show_view(self, name: str):
        for frame in self._view_frames.values():
            frame.pack_forget()
        self._view_frames[name].pack(fill="both", expand=True)
        for key, button in self._view_buttons.items():
            button.configure(
                fg_color=theme.ACCENT if key == name else theme.CARD_BG,
                text_color=theme.APP_BG if key == name else theme.TEXT,
            )
        if name == "platforms":
            self.refresh_platform_status()

    def _build_live_view(self, parent):
        chat = ctk.CTkFrame(parent, fg_color=theme.CARD_BG, corner_radius=10)
        chat.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self._build_chat(chat)
        self._chat_bar.pack_forget()

        side = ctk.CTkFrame(parent, fg_color="transparent", width=370)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        queue_frame = ctk.CTkFrame(side, fg_color=theme.CARD_BG, corner_radius=10)
        queue_frame.pack(fill="both", expand=True, pady=(0, 8))
        self._build_queue(queue_frame)

        events_frame = ctk.CTkFrame(side, fg_color=theme.CARD_BG, corner_radius=10, height=230)
        events_frame.pack(fill="x")
        events_frame.pack_propagate(False)
        self._build_events(events_frame)

    def _build_viewers_view(self, parent):
        panel = MiddleColumn(parent)
        panel.pack(fill="both", expand=True)
        self._middle = panel

    def _build_platforms_view(self, parent):
        self._platform_overview = AppScrollableFrame(parent, fg_color="transparent")
        self._platform_overview.pack(fill="both", expand=True)

        ctk.CTkLabel(
            self._platform_overview,
            text="Platforms",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=4, pady=(2, 2))
        ctk.CTkLabel(
            self._platform_overview,
            text="Enable one platform for a focused queue, or several to merge them.",
            text_color=theme.MUTED_TEXT,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=4, pady=(0, 12))

        for provider in ("twitch", "youtube", "kick"):
            self._build_platform_row(self._platform_overview, provider)

        self._build_shared_settings(self._platform_overview)

        self._platform_details = {
            "twitch": AppScrollableFrame(parent, fg_color="transparent"),
            "youtube": AppScrollableFrame(parent, fg_color="transparent"),
            "kick": AppScrollableFrame(parent, fg_color="transparent"),
        }
        self._platform_detail = self._platform_details["twitch"]
        self._build_twitch_detail(self._platform_details["twitch"])
        self._build_youtube_detail(self._platform_details["youtube"])
        self._build_kick_detail(self._platform_details["kick"])

    def _build_platform_row(self, parent, provider: str):
        icon, name, description, color = PROVIDER_INFO[provider]
        row = ctk.CTkFrame(parent, fg_color=theme.CARD_BG, corner_radius=8, height=70)
        row.pack(fill="x", pady=5)
        row.pack_propagate(False)
        icon_slot = ctk.CTkFrame(
            row,
            width=48,
            height=42,
            fg_color="transparent",
        )
        icon_slot.pack(side="left", padx=(10, 8))
        icon_slot.pack_propagate(False)

        provider_image = self._load_provider_icon(provider)
        if provider_image is not None:
            ctk.CTkLabel(
                icon_slot,
                text="",
                image=provider_image,
                fg_color="transparent",
            ).place(relx=0.5, rely=0.5, anchor="center")
        else:
            ctk.CTkLabel(
                icon_slot,
                text=icon,
                width=38,
                height=38,
                corner_radius=7,
                fg_color=color,
                text_color="#111111" if provider == "kick" else "#FFFFFF",
                font=ctk.CTkFont(size=18, weight="bold"),
            ).place(relx=0.5, rely=0.5, anchor="center")
        text = ctk.CTkFrame(row, fg_color="transparent")
        text.pack(side="left", fill="both", expand=True, pady=10)
        ctk.CTkLabel(
            text, text=name, font=ctk.CTkFont(size=14, weight="bold"), text_color=theme.TEXT
        ).pack(anchor="w")
        status = ctk.CTkLabel(
            text,
            text=description,
            font=ctk.CTkFont(size=11),
            text_color=theme.MUTED_TEXT,
        )
        status.pack(anchor="w")
        self._provider_status_labels[provider] = status

        enabled = provider in self.hub.enabled_providers
        var = ctk.BooleanVar(value=enabled)
        self._provider_vars[provider] = var
        ctk.CTkSwitch(
            row,
            text="Enabled",
            variable=var,
            command=self._provider_selection_changed,
            text_color=theme.TEXT,
        ).pack(side="right", padx=12)
        ctk.CTkButton(
            row,
            text="Configure",
            width=92,
            command=lambda selected=provider: self._open_platform_detail(selected),
        ).pack(side="right", padx=4)

    def _load_provider_icon(self, provider: str) -> ctk.CTkImage | None:
        path = PROVIDER_ICON_FILES.get(provider)
        if path is None or not path.is_file():
            return None
        try:
            with Image.open(path) as source:
                image = source.convert("RGBA")
            rendered = ctk.CTkImage(
                light_image=image,
                dark_image=image,
                size=PROVIDER_ICON_SIZES.get(provider, (36, 36)),
            )
            self._provider_images[provider] = rendered
            return rendered
        except (OSError, ValueError):
            return None

    def _build_shared_settings(self, parent):
        card = ctk.CTkFrame(parent, fg_color=theme.CARD_BG, corner_radius=8)
        card.pack(fill="x", pady=(14, 5))
        ctk.CTkLabel(
            card,
            text="Shared Queue Settings",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=14, pady=(12, 6))

        policy_row = ctk.CTkFrame(card, fg_color="transparent")
        policy_row.pack(fill="x", padx=14, pady=4)
        ctk.CTkLabel(policy_row, text="Message policy", width=150, anchor="w", text_color=theme.TEXT).pack(side="left")
        ctk.CTkComboBox(
            policy_row,
            values=["latest", "latest-buffered", "all"],
            variable=self.policy_var,
            command=self._on_policy_changed,
            width=180,
        ).pack(side="left")

        ctk.CTkSwitch(
            card,
            text="Ignore ! commands in the AI queue",
            variable=self.ignore_commands_var,
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=14, pady=(8, 3))

        ctk.CTkLabel(
            card,
            text="Additional ignored message prefixes (one per line)",
            text_color=theme.MUTED_TEXT,
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=14, pady=(5, 0))

        self.ignored_prefixes_box = ctk.CTkTextbox(
            card, height=74, fg_color=theme.APP_BG, text_color=theme.TEXT, wrap="none"
        )
        self.ignored_prefixes_box.pack(fill="x", padx=14, pady=6)
        ignored = get_settings("STREAM_CHAT_IGNORED_PREFIXES") or get_settings("TWITCH_IGNORED_PREFIXES")
        if ignored:
            self.ignored_prefixes_box.insert("0.0", str(ignored))
        ctk.CTkButton(card, text="Save Shared Settings", command=self.save_shared_settings).pack(
            anchor="e", padx=14, pady=(4, 12)
        )

    def _build_twitch_detail(self, parent):
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.pack(fill="x", pady=(0, 10))
        ctk.CTkButton(top, text="Back to Platforms", width=130, command=self._close_platform_detail).pack(side="left")
        ctk.CTkLabel(
            top,
            text="Twitch",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=theme.TEXT,
        ).pack(side="left", padx=14)

        accounts = ctk.CTkFrame(parent, fg_color=theme.CARD_BG, corner_radius=8)
        accounts.pack(fill="x", pady=5)
        ctk.CTkLabel(
            accounts,
            text="Accounts",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=14, pady=(12, 4))
        self._twitch_broadcaster_account = self._account_row(
            accounts,
            "Broadcaster Account",
            "Owns the channel and authorizes channel events.",
            self.open_access_dialog,
        )
        self._twitch_bot_account = self._account_row(
            accounts,
            "Bot Account — Optional",
            "Uses the broadcaster identity unless a separate bot is authorized.",
            self.open_access_dialog,
        )

        connection = ctk.CTkFrame(parent, fg_color=theme.CARD_BG, corner_radius=8)
        connection.pack(fill="x", pady=5)
        ctk.CTkLabel(
            connection,
            text="Channel and Services",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=14, pady=(12, 6))
        row = ctk.CTkFrame(connection, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=5)
        ctk.CTkLabel(row, text="Channel name", width=150, anchor="w", text_color=theme.TEXT).pack(side="left")
        ctk.CTkEntry(row, textvariable=self.channel_var).pack(side="left", fill="x", expand=True)

        buttons = ctk.CTkFrame(connection, fg_color="transparent")
        buttons.pack(fill="x", padx=14, pady=6)
        ctk.CTkButton(buttons, text="Connect", command=self.start_chat_reader).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            buttons,
            text="Disconnect",
            fg_color=theme.DANGER,
            hover_color="#922b21",
            command=self._stop_twitch,
        ).pack(side="left")
        self.status_label = ctk.CTkLabel(
            connection,
            text=self._runtime_status(),
            text_color=theme.MUTED_TEXT,
            font=ctk.CTkFont(size=11),
        )
        self.status_label.pack(anchor="w", padx=14, pady=(2, 12))

        advanced = ctk.CTkFrame(parent, fg_color=theme.CARD_BG, corner_radius=8)
        advanced.pack(fill="x", pady=5)
        ctk.CTkLabel(
            advanced,
            text="Application Authorization",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=14, pady=(12, 4))
        self.auth_status_label = ctk.CTkLabel(
            advanced, text="", text_color=theme.MUTED_TEXT, font=ctk.CTkFont(size=11)
        )
        self.auth_status_label.pack(anchor="w", padx=14, pady=3)
        actions = ctk.CTkFrame(advanced, fg_color="transparent")
        actions.pack(fill="x", padx=14, pady=(4, 12))
        ctk.CTkButton(actions, text="Manage Authorization", command=self.open_access_dialog).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            actions,
            text="Forget Twitch Accounts",
            fg_color=theme.DANGER,
            hover_color="#922b21",
            command=self.clear_access_token,
        ).pack(side="left")

    def _account_row(self, parent, title, description, command):
        row = ctk.CTkFrame(parent, fg_color=theme.APP_BG, corner_radius=7)
        row.pack(fill="x", padx=14, pady=5)
        text = ctk.CTkFrame(row, fg_color="transparent")
        text.pack(side="left", fill="both", expand=True, padx=12, pady=9)
        ctk.CTkLabel(
            text, text=title, font=ctk.CTkFont(size=13, weight="bold"), text_color=theme.TEXT
        ).pack(anchor="w")
        detail = ctk.CTkLabel(text, text=description, font=ctk.CTkFont(size=11), text_color=theme.MUTED_TEXT)
        detail.pack(anchor="w")
        ctk.CTkButton(row, text="Manage", width=82, command=command).pack(side="right", padx=12)
        row._detail_label = detail
        return row

    def _detail_header(self, parent, title: str):
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.pack(fill="x", pady=(0, 10))
        ctk.CTkButton(top, text="Back to Platforms", width=130, command=self._close_platform_detail).pack(side="left")
        ctk.CTkLabel(
            top,
            text=title,
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=theme.TEXT,
        ).pack(side="left", padx=14)

    def _build_youtube_detail(self, parent):
        self._detail_header(parent, "YouTube")
        accounts = ctk.CTkFrame(parent, fg_color=theme.CARD_BG, corner_radius=8)
        accounts.pack(fill="x", pady=5)
        ctk.CTkLabel(
            accounts, text="Accounts", font=ctk.CTkFont(size=15, weight="bold"), text_color=theme.TEXT
        ).pack(anchor="w", padx=14, pady=(12, 4))
        self._youtube_broadcaster_account = self._account_row(
            accounts,
            "Broadcaster Account",
            "Required to discover and read the channel's active live chat.",
            self._open_youtube_auth,
        )
        self._youtube_bot_account = self._account_row(
            accounts,
            "Bot Account — Future Outbound Chat",
            "Not required for receiving messages; outbound chat is not enabled yet.",
            self._youtube_bot_notice,
        )

        broadcast = ctk.CTkFrame(parent, fg_color=theme.CARD_BG, corner_radius=8)
        broadcast.pack(fill="x", pady=5)
        ctk.CTkLabel(
            broadcast, text="Broadcast Monitoring", font=ctk.CTkFont(size=15, weight="bold"), text_color=theme.TEXT
        ).pack(anchor="w", padx=14, pady=(12, 6))
        row = ctk.CTkFrame(broadcast, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=5)
        ctk.CTkLabel(row, text="Video ID override", width=160, anchor="w", text_color=theme.TEXT).pack(side="left")
        ctk.CTkEntry(
            row,
            textvariable=self.youtube_video_var,
            placeholder_text="Leave blank to discover your active broadcast",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkSwitch(
            broadcast,
            text="Automatically connect when AI Companion starts",
            variable=self.youtube_auto_var,
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=14, pady=5)
        ctk.CTkSwitch(
            broadcast,
            text="Keep checking until a live broadcast appears",
            variable=self.youtube_monitor_var,
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=14, pady=5)
        buttons = ctk.CTkFrame(broadcast, fg_color="transparent")
        buttons.pack(fill="x", padx=14, pady=7)
        ctk.CTkButton(buttons, text="Save and Connect", command=self._save_and_start_youtube).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            buttons,
            text="Disconnect",
            fg_color=theme.DANGER,
            hover_color="#922b21",
            command=self._stop_youtube,
        ).pack(side="left")
        self.youtube_status_label = ctk.CTkLabel(
            broadcast, text="", text_color=theme.MUTED_TEXT, font=ctk.CTkFont(size=11)
        )
        self.youtube_status_label.pack(anchor="w", padx=14, pady=(2, 12))

        forget = ctk.CTkFrame(parent, fg_color=theme.CARD_BG, corner_radius=8)
        forget.pack(fill="x", pady=5)
        ctk.CTkLabel(
            forget,
            text="Authorization",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=14, pady=(12, 4))
        ctk.CTkButton(
            forget,
            text="Forget YouTube Account",
            fg_color=theme.DANGER,
            hover_color="#922b21",
            command=self._forget_youtube,
        ).pack(anchor="w", padx=14, pady=(4, 12))

    def _build_kick_detail(self, parent):
        self._detail_header(parent, "Kick")
        notice = ctk.CTkFrame(parent, fg_color="#493A16", corner_radius=8)
        notice.pack(fill="x", pady=5)
        ctk.CTkLabel(
            notice,
            text="Official webhook transport required",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#F1C40F",
        ).pack(anchor="w", padx=14, pady=(12, 4))
        ctk.CTkLabel(
            notice,
            text=(
                "Kick sends chat.message.sent events to a public HTTPS webhook. "
                "Configure a relay or tunnel URL here; Kick remains capability-gated "
                "until signature-verified delivery is available."
            ),
            wraplength=760,
            justify="left",
            text_color=theme.TEXT,
        ).pack(anchor="w", padx=14, pady=(0, 12))

        accounts = ctk.CTkFrame(parent, fg_color=theme.CARD_BG, corner_radius=8)
        accounts.pack(fill="x", pady=5)
        ctk.CTkLabel(
            accounts, text="Accounts", font=ctk.CTkFont(size=15, weight="bold"), text_color=theme.TEXT
        ).pack(anchor="w", padx=14, pady=(12, 4))
        self._kick_broadcaster_account = self._account_row(
            accounts,
            "Broadcaster Account",
            "Not connected · Kick OAuth will be enabled with the webhook receiver.",
            self._kick_unavailable_notice,
        )
        self._kick_bot_account = self._account_row(
            accounts,
            "Bot Account — Optional",
            "Reserved for future outbound Kick messages.",
            self._kick_unavailable_notice,
        )

        config = ctk.CTkFrame(parent, fg_color=theme.CARD_BG, corner_radius=8)
        config.pack(fill="x", pady=5)
        ctk.CTkLabel(
            config, text="Webhook Preparation", font=ctk.CTkFont(size=15, weight="bold"), text_color=theme.TEXT
        ).pack(anchor="w", padx=14, pady=(12, 6))
        for label, variable, placeholder in (
            ("Channel slug", self.kick_channel_var, "your-kick-channel"),
            ("Public webhook URL", self.kick_webhook_var, "https://relay.example.com/kick/events"),
        ):
            row = ctk.CTkFrame(config, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=5)
            ctk.CTkLabel(row, text=label, width=160, anchor="w", text_color=theme.TEXT).pack(side="left")
            ctk.CTkEntry(row, textvariable=variable, placeholder_text=placeholder).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(config, text="Save Kick Preparation", command=self._save_kick_settings).pack(
            anchor="e", padx=14, pady=(6, 12)
        )
        self.kick_status_label = ctk.CTkLabel(
            config, text="", text_color=theme.MUTED_TEXT, font=ctk.CTkFont(size=11)
        )
        self.kick_status_label.pack(anchor="w", padx=14, pady=(0, 12))

    def _open_platform_detail(self, provider: str):
        self._platform_overview.pack_forget()
        for frame in self._platform_details.values():
            frame.pack_forget()
        self._platform_details[provider].pack(fill="both", expand=True)
        self.refresh_platform_status()

    def _show_provider_notice(self, provider: str):
        _icon, name, _description, _color = PROVIDER_INFO[provider]
        status = self.hub.provider_status(provider)
        detail = status.get("detail") or "Configuration is not yet complete."
        dialog = ctk.CTkToplevel(self)
        dialog.title(f"{name} Configuration")
        dialog.geometry("480x220")
        dialog.transient(self.winfo_toplevel())
        ctk.CTkLabel(dialog, text=name, font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(22, 8))
        ctk.CTkLabel(dialog, text=detail, wraplength=420, justify="left", text_color=theme.MUTED_TEXT).pack(padx=24, pady=8)
        ctk.CTkButton(dialog, text="Close", command=dialog.destroy).pack(pady=14)

    def _close_platform_detail(self):
        for frame in self._platform_details.values():
            frame.pack_forget()
        self._platform_overview.pack(fill="both", expand=True)
        self.refresh_platform_status()

    def _provider_selection_changed(self):
        enabled = {name for name, var in self._provider_vars.items() if var.get()}
        self.hub.set_enabled_providers(enabled)
        if "twitch" not in enabled and twitchchat and twitchchat.read_chat_running:
            twitchchat.stop_read_chat()
        if "youtube" not in enabled:
            get_youtube_provider().stop()
        if "kick" in enabled:
            get_kick_provider().start()
        else:
            get_kick_provider().stop()
        self.refresh_platform_status()

    def _start_auto_connect(self):
        if (
            "youtube" in self.hub.enabled_providers
            and bool(get_settings("YOUTUBE_AUTO_CONNECT"))
            and get_auth("youtube", "refresh_token")
        ):
            get_youtube_provider().start()
        if "kick" in self.hub.enabled_providers:
            get_kick_provider().start()

    def _open_youtube_auth(self):
        YouTubeAuthDialog(self, on_complete=self._youtube_auth_complete)

    def _youtube_auth_complete(self):
        self.refresh_platform_status()

    def _youtube_bot_notice(self):
        self._show_provider_notice("youtube")

    def _save_and_start_youtube(self):
        save_settings("YOUTUBE_VIDEO_ID", self.youtube_video_var.get().strip())
        save_settings("YOUTUBE_AUTO_CONNECT", bool(self.youtube_auto_var.get()))
        save_settings("YOUTUBE_AUTO_MONITOR", bool(self.youtube_monitor_var.get()))
        if "youtube" not in self.hub.enabled_providers:
            self._provider_vars["youtube"].set(True)
            self._provider_selection_changed()
        get_youtube_provider().start()
        self.refresh_platform_status()

    def _stop_youtube(self):
        get_youtube_provider().stop()
        self.refresh_platform_status()

    def _forget_youtube(self):
        get_youtube_provider().stop()
        for key in (
            "access_token", "refresh_token", "token_expires_at", "channel_id", "channel_name",
            "bot_access_token", "bot_refresh_token", "bot_channel_id", "bot_channel_name",
        ):
            save_auth("youtube", key, "")
        self.refresh_platform_status()

    def _kick_unavailable_notice(self):
        self._show_provider_notice("kick")

    def _save_kick_settings(self):
        channel = self.kick_channel_var.get().strip()
        webhook = self.kick_webhook_var.get().strip()
        save_settings("KICK_CHANNEL_SLUG", channel)
        save_settings("KICK_WEBHOOK_URL", webhook)
        detail = (
            "Saved. A signature-verifying receiver still needs to be connected."
            if webhook else "Enter a public HTTPS webhook or relay URL."
        )
        self.hub.update_provider_status("kick", "needs_setup", detail)
        get_kick_provider().start()
        self.refresh_platform_status()

    def save_shared_settings(self):
        ignored = self.ignored_prefixes_box.get("0.0", "end-1c").strip()
        policy = self.hub.set_policy(self.policy_var.get())
        save_settings("STREAM_CHAT_IGNORE_COMMANDS", bool(self.ignore_commands_var.get()))
        save_settings("STREAM_CHAT_IGNORED_PREFIXES", ignored)
        save_settings("TWITCH_POLICY", policy)
        save_settings("TWITCH_IGNORED_PREFIXES", ignored)
        self.refresh_queue_panel()

    def _on_policy_changed(self, value=None):
        policy = self.hub.set_policy(value or self.policy_var.get())
        self.policy_var.set(policy)
        self.refresh_queue_panel()

    def _apply_pause(self, paused: bool):
        self._chat_paused = bool(paused)
        self.paused_var.set(self._chat_paused)
        self.hub.set_paused(self._chat_paused)
        if twitchchat and twitchchat.get_twitch_bot():
            twitchchat.get_twitch_bot().start_chat_pause = self._chat_paused
        if hasattr(self, "_pause_btn"):
            self._pause_btn.configure(
                text="▶" if self._chat_paused else "⏸",
            )
        if hasattr(self, "_global_pause_btn"):
            self._global_pause_btn.configure(
                text="Resume All" if self._chat_paused else "Pause All",
                fg_color=theme.SUCCESS if self._chat_paused else theme.DANGER,
            )
        if hasattr(self, "_pause_banner") and hasattr(self, "_chat_area"):
            self._chat_area.pack_forget()
            if self._chat_paused:
                self._pause_banner.configure(text="CHAT INGESTION PAUSED")
                self._pause_banner.pack(fill="x", padx=8, pady=(0, 4))
            else:
                self._pause_banner.pack_forget()
            self._chat_area.pack(fill="both", expand=True, padx=8, pady=(0, 6))

    def _toggle_pause(self):
        self._apply_pause(not self._chat_paused)

    def _any_reader_running(self) -> bool:
        twitch_running = bool(twitchchat and twitchchat.read_chat_running)
        return bool(
            twitch_running
            or get_youtube_provider().is_running()
            or get_kick_provider().is_running()
        )

    def _toggle_readers(self):
        if self._any_reader_running():
            self._stop_all_readers()
        else:
            self._start_enabled_readers()

    def _start_enabled_readers(self):
        enabled = set(self.hub.enabled_providers)
        if not enabled:
            self._show_view("platforms")
            return

        # Use the same paths as the platform-specific controls so current UI
        # values are saved and each provider retains its normal validation.
        if "twitch" in enabled:
            self.start_chat_reader()
        if "youtube" in enabled:
            self._save_and_start_youtube()
        if "kick" in enabled:
            self._save_kick_settings()
        self.refresh_platform_status()

    def _stop_all_readers(self):
        if twitchchat:
            twitchchat.stop_read_chat()
        get_youtube_provider().stop()
        get_kick_provider().stop()
        self.refresh_platform_status()

    def push_stream_message(self, message: StreamChatMessage):
        color = ""
        if isinstance(message.raw, dict):
            color = str(message.raw.get("color") or "")
        display_name = f"{message.author_name} · {message.source_label}"
        _chatter_store.record(
            display_name,
            color,
            message.role,
            message.text,
            user_id=message.author_id,
        )
        self._append_chat(display_name, color, message.role, message.text)
        if hasattr(self, "_middle"):
            self._middle._refresh_recent_if_idle()

    def push_stream_event(self, event: StreamChatEvent):
        self._append_event(
            event.event_type,
            f"{event.username} · {event.provider.title()}",
            event.detail,
            datetime.now().strftime("%I:%M:%S %p"),
        )

    def refresh_queue_panel(self):
        if not hasattr(self, "_queue_list"):
            return
        items = self.hub.list_pending()
        active_text = "Active turn running" if self.hub.in_pipeline else "Ready"
        if self.hub.paused:
            active_text = "Paused"
        self._queue_count_label.configure(text=str(len(items)))
        self._queue_status.configure(text=f"{active_text} | policy: {self.hub.policy}")
        self._render_queue_items(items)

    def _update_queue_row(self, row, item):
        provider = str(item.get("source_label") or item.get("provider") or "Chat")
        event_type = item.get("event_type") or ""
        priority = f"{event_type} priority" if event_type else ("priority" if item.get("priority") else "standard")
        age = self._format_queue_age(item.get("age_seconds", 0))
        row._author_label.configure(
            text=f"[{provider}] {item.get('author', 'unknown')} · {priority} · {age}"
        )
        row._message_label.configure(text=item.get("content", ""))

    def _remove_queue_item(self, item_id):
        removed = self.hub.remove(str(item_id))
        self._queue_status.configure(
            text="Message removed from queue." if removed else "Message already active or no longer queued."
        )
        self.refresh_queue_panel()

    def refresh_platform_status(self):
        enabled = sorted(self.hub.enabled_providers)
        summary = " · ".join(name.title() for name in enabled) if enabled else "No platforms enabled"
        self._provider_summary.configure(text=summary)
        if hasattr(self, "_global_reader_btn"):
            running = self._any_reader_running()
            self._global_reader_btn.configure(
                text="Stop Readers" if running else "Start Readers",
                fg_color=theme.DANGER if running else theme.SUCCESS,
                state="normal" if enabled or running else "disabled",
            )
        for provider, label in self._provider_status_labels.items():
            status = self.hub.provider_status(provider)
            state = status.get("state", "disconnected").replace("_", " ").title()
            account = status.get("account_name") or ""
            detail = status.get("detail") or PROVIDER_INFO[provider][2]
            text = f"{state}{f' as {account}' if account else ''} · {detail}"
            label.configure(
                text=text,
                text_color=theme.SUCCESS if state == "Connected" else theme.MUTED_TEXT,
            )
        self._refresh_twitch_accounts()
        self._refresh_youtube_account()
        if hasattr(self, "kick_status_label"):
            kick = self.hub.provider_status("kick")
            self.kick_status_label.configure(text=kick.get("detail") or "Webhook receiver not configured.")

    def _refresh_youtube_account(self):
        if not hasattr(self, "_youtube_broadcaster_account"):
            return
        name = get_auth("youtube", "channel_name") or ""
        channel_id = get_auth("youtube", "channel_id") or ""
        self._youtube_broadcaster_account._detail_label.configure(
            text=(f"Connected as {name or channel_id}" if channel_id else "Not connected · broadcaster authorization required")
        )
        status = self.hub.provider_status("youtube")
        state = status.get("state", "disconnected").replace("_", " ").title()
        detail = status.get("detail") or "YouTube chat reader is stopped."
        self.youtube_status_label.configure(text=f"{state} · {detail}")

    def _refresh_twitch_accounts(self):
        if not hasattr(self, "_twitch_broadcaster_account"):
            return
        owner_id = get_auth("twitch", "owner_id") or ""
        bot_id = get_auth("twitch", "bot_id") or ""
        channel = get_auth("twitch", "channel_name") or self.channel_var.get().strip()
        broadcaster_text = (
            f"Connected as {channel or owner_id} · channel owner"
            if owner_id else "Not connected · required for channel events"
        )
        if bot_id:
            bot_text = (
                "Using broadcaster account for chat"
                if owner_id and str(owner_id) == str(bot_id)
                else f"Separate bot connected · ID {bot_id}"
            )
        else:
            bot_text = "No bot account connected"
        self._twitch_broadcaster_account._detail_label.configure(text=broadcaster_text)
        self._twitch_bot_account._detail_label.configure(text=bot_text)
        has_auth = bool(get_auth("twitch", "access_token"))
        self.auth_status_label.configure(
            text="Twitch authorization is saved." if has_auth else "Twitch is not authorized.",
            text_color=theme.SUCCESS if has_auth else theme.MUTED_TEXT,
        )
        self.status_label.configure(text=self._runtime_status())

    def _on_auth_complete(self):
        self.refresh_platform_status()

    def clear_access_token(self):
        if twitchchat and twitchchat.read_chat_running:
            twitchchat.stop_read_chat()
        for key in (
            "access_token", "refresh_token", "channel_access_token",
            "channel_refresh_token", "bot_id", "owner_id", "channel_name",
        ):
            save_auth("twitch", key, "")
        self.channel_var.set("")
        self.refresh_platform_status()

    def start_chat_reader(self):
        if "twitch" not in self.hub.enabled_providers:
            self._provider_vars["twitch"].set(True)
            self._provider_selection_changed()
        save_auth("twitch", "channel_name", self.channel_var.get().strip())
        super().start_chat_reader()
        self.refresh_platform_status()

    def _stop_twitch(self):
        if twitchchat:
            twitchchat.stop_read_chat()
        self.refresh_platform_status()

    def destroy(self):
        self.on_hide()
        self.hub.clear_ui_page(self)
        super().destroy()

    def on_show(self):
        if self._page_active:
            return
        self._page_active = True
        self.refresh_queue_panel()
        self.refresh_platform_status()
        self._schedule_queue_refresh()

    def on_hide(self):
        self._page_active = False
        if self._queue_refresh_after_id is not None:
            try:
                self.after_cancel(self._queue_refresh_after_id)
            except Exception:
                pass
            self._queue_refresh_after_id = None

TwitchPage = StreamChatPage
