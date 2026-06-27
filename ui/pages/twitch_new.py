import customtkinter as ctk
from pathlib import Path
from datetime import datetime
from files.ui import theme
from files.ui.components.auth_dialog import AuthTokenDialog

try:
    from files.system_setup.settings import get_auth, save_auth, get_settings, save_settings
except Exception:
    get_auth = save_auth = get_settings = save_settings = None

try:
    from files.twitch_chat import twitchchat
except Exception as e:
    print(f"[Twitch UI] Failed to import twitchchat: {e}")
    twitchchat = None

_BLACKLIST_PATH = Path(__file__).resolve().parent / "blacklist.txt"


TIER_BROADCASTER = ("BROADCASTER", "#FFD700", "#3A2F00")
TIER_MOD         = ("MOD",         "#2ECC71", "#0D3320")
TIER_SUB         = ("SUB",         "#6441A4", "#2A1A4A")
TIER_FIRST       = ("FIRST",       "#1ABC9C", "#0A2E28")
TIER_RETURNING   = ("RETURN",      "#5DADE2", "#0D2035")
TIER_REGULAR     = None


def resolve_tier(tags: dict):
    badges = tags.get("badges", "")
    if "broadcaster" in badges:                                              return TIER_BROADCASTER
    if tags.get("mod") == "1" or tags.get("user-type") in ("mod", "global_mod", "admin", "staff"):
        return TIER_MOD
    if tags.get("subscriber") == "1":                                        return TIER_SUB
    if tags.get("first-msg") == "1":                                         return TIER_FIRST
    if tags.get("returning-chatter") == "1":                                 return TIER_RETURNING
    return TIER_REGULAR


def tier_from_role(role: str):
    return {"broadcaster": TIER_BROADCASTER, "mod": TIER_MOD, "sub": TIER_SUB,
            "first": TIER_FIRST, "returning": TIER_RETURNING}.get(role, TIER_REGULAR)

EVENT_STYLES = {
    "follow":     ("♥",  "#E91E8C", "Followed you"),
    "sub":        ("★",  "#6441A4", "Subscribed"),
    "resub":      ("★",  "#9B59B6", "Resubscribed ({detail})"),
    "raid":       ("⚡", "#F39C12", "Raided with {detail} viewers"),
    "bits":       ("💎", "#00BFFF", "Cheered {detail} bits"),
    "first_msg":  ("👋", "#1ABC9C", "First message"),
    "milestone":  ("🔥", "#E74C3C", "Reached {detail}"),
    "gift_sub":   ("🎁", "#27AE60", "Gifted a sub to {detail}"),
    "poll":       ("📊", "#8E44AD", "Poll: {detail}"),
    "prediction": ("🎯", "#D35400", "Prediction: {detail}"),
}

STUB_CHATTERS = [
    {"username": "CappiePine",   "color": "#9B59B6", "role": "sub",       "msg_count": 48,
     "first_seen": "5/2/2026, 10:27:35 PM", "last_seen": "5/2/2026, 10:37:34 PM",
     "auto_notes": "(no auto-notes yet — needs more activity)", "private_notes": "",
     "recent_messages": [("10:32:09 PM", "they sound very different now"),
                         ("10:31:03 PM", "Free plush"),
                         ("10:30:13 PM", "How much rizadin is a plush worth?"),
                         ("10:29:25 PM", "You are plush")]},
    {"username": "yoman21a",     "color": "#2ECC71", "role": "",          "msg_count": 28,
     "first_seen": "5/2/2026, 9:55:00 PM",  "last_seen": "5/2/2026, 10:35:00 PM",
     "auto_notes": "Frequently asks about game mechanics.", "private_notes": "",
     "recent_messages": [("10:34:00 PM", "GG let's go!"), ("10:33:10 PM", "what map is next?")]},
    {"username": "TheRMan",      "color": "#E74C3C", "role": "mod",       "msg_count": 37,
     "first_seen": "5/2/2026, 10:00:00 PM", "last_seen": "5/2/2026, 10:38:00 PM",
     "auto_notes": "Often comments on audio quality.", "private_notes": "Mod candidate — check later.",
     "recent_messages": [("10:37:00 PM", "audio is peaking again"),
                         ("10:36:20 PM", "can you turn up game audio?")]},
    {"username": "MikotheeCat_", "color": "#E91E8C", "role": "first",     "msg_count": 5,
     "first_seen": "5/2/2026, 10:20:00 PM", "last_seen": "5/2/2026, 10:29:00 PM",
     "auto_notes": "(no auto-notes yet — needs more activity)", "private_notes": "",
     "recent_messages": [("10:29:00 PM", "hiii!!")]},
    {"username": "jed115",       "color": "#3498DB", "role": "sub",       "msg_count": 8,
     "first_seen": "5/2/2026, 10:15:00 PM", "last_seen": "5/2/2026, 10:33:00 PM",
     "auto_notes": "(no auto-notes yet — needs more activity)", "private_notes": "",
     "recent_messages": [("10:33:00 PM", "PogChamp"), ("10:30:00 PM", "sub hype!")]},
    {"username": "kon_ata",      "color": "#ECF0F1", "role": "returning",  "msg_count": 5,
     "first_seen": "5/2/2026, 10:10:00 PM", "last_seen": "5/2/2026, 10:28:00 PM",
     "auto_notes": "(no auto-notes yet — needs more activity)", "private_notes": "",
     "recent_messages": [("10:28:00 PM", "lurking o/")]},
    {"username": "machinedude",  "color": "#E74C3C", "role": "",           "msg_count": 2,
     "first_seen": "5/2/2026, 10:05:00 PM", "last_seen": "5/2/2026, 10:06:00 PM",
     "auto_notes": "(no auto-notes yet — needs more activity)", "private_notes": "",
     "recent_messages": [("10:06:00 PM", "hi")]},
]

STUB_CHAT = [
    ("CappiePine",   "#9B59B6", "sub",      "they sound very different now"),
    ("yoman21a",     "#2ECC71", "",         "GG let's go!"),
    ("TheRMan",      "#2ECC71", "mod",      "audio is peaking again"),
    ("jed115",       "#6441A4", "sub",      "PogChamp"),
    ("CappiePine",   "#9B59B6", "sub",      "Free plush"),
    ("MikotheeCat_", "#E91E8C", "first",    "hiii!!"),
    ("kon_ata",      "#5DADE2", "returning","lurking o/"),
    ("TheRMan",      "#2ECC71", "mod",      "can you turn up game audio?"),
    ("yoman21a",     "#2ECC71", "",         "what map is next?"),
    ("CappiePine",   "#9B59B6", "sub",      "How much rizadin is a plush worth?"),
]

STUB_EVENTS = [
    ("follow",    "gigiottyy",   "",                          "4 days ago"),
    ("raid",      "TheRMan",     "2",                         "4 days ago"),
    ("raid",      "Ursinnet",    "2",                         "4 days ago"),
    ("resub",     "CappiePine",  "13 months",                 "4 days ago"),
    ("bits",      "jed115",      "100",                       "2 days ago"),
    ("first_msg", "MikotheeCat_","",                          "just now"),
    ("milestone", "TheRMan",     "20-Stream Streak",          "4 days ago"),
    ("gift_sub",  "kon_ata",     "yoman21a",                  "3 days ago"),
    ("poll",      "—",           "Do you want a coding stream?","4 days ago"),
    ("sub",       "machinedude", "",                          "1 day ago"),
]

def _pill(parent, text, fg, bg, font_size=10):
    return ctk.CTkLabel(
        parent, text=text,
        font=ctk.CTkFont(size=font_size, weight="bold"),
        text_color=fg, fg_color=bg,
        corner_radius=5, height=20, padx=6,
    )


def _divider(parent, padx=14):
    ctk.CTkFrame(parent, fg_color=theme.MUTED_TEXT, height=1).pack(
        fill="x", padx=padx, pady=6)


def _section_lbl(parent, text, padx=14):
    ctk.CTkLabel(
        parent, text=text,
        font=ctk.CTkFont(size=13, weight="bold"),
        text_color=theme.TEXT,
    ).pack(anchor="w", padx=padx, pady=(10, 4))


class ConfirmDialog(ctk.CTkToplevel):
    def __init__(self, master, title: str, message: str, on_confirm):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.grab_set()

        ctk.CTkLabel(self, text=message, wraplength=300,
                     font=ctk.CTkFont(size=13), text_color=theme.TEXT,
                     justify="center").pack(padx=24, pady=(24, 16))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(padx=24, pady=(0, 20))

        ctk.CTkButton(btns, text="Cancel", width=100,
                      command=self.destroy).pack(side="left", padx=(0, 10))

        ctk.CTkButton(btns, text="Remove", width=100,
                      fg_color=theme.DANGER, hover_color="#922b21",
                      command=lambda: [on_confirm(), self.destroy()]).pack(side="left")

class ChatterDetailPanel(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=theme.APP_BG, corner_radius=8, **kwargs)
        self._frame = None
        self._ph = ctk.CTkLabel(
            self, text="Select a chatter\nto view their profile.",
            text_color=theme.MUTED_TEXT, font=ctk.CTkFont(size=13), justify="center",
        )
        self._ph.place(relx=0.5, rely=0.5, anchor="center")

    def load(self, chatter: dict):
        self._ph.place_forget()
        if self._frame:
            self._frame.destroy()

        s = ctk.CTkScrollableFrame(self, fg_color="transparent")
        s.pack(fill="both", expand=True)
        self._frame = s

        px = 14
        tier = tier_from_role(chatter.get("role", ""))
        name_color = tier[1] if tier else chatter.get("color", "#FFFFFF")

        # Header
        hdr = ctk.CTkFrame(s, fg_color="transparent")
        hdr.pack(fill="x", padx=px, pady=(14, 2))
        ctk.CTkLabel(hdr, text=chatter["username"],
                     font=ctk.CTkFont(size=17, weight="bold"),
                     text_color=name_color).pack(side="left")
        if tier:
            _pill(hdr, tier[0], tier[1], tier[2]).pack(side="left", padx=(8, 0))

        ctk.CTkLabel(
            s, text=f"first seen {chatter['first_seen']}  ·  last seen {chatter['last_seen']}",
            font=ctk.CTkFont(size=11), text_color=theme.MUTED_TEXT,
            wraplength=340, justify="left",
        ).pack(anchor="w", padx=px, pady=(0, 8))

        _divider(s, px)

        # Auto-notes
        r = ctk.CTkFrame(s, fg_color="transparent")
        r.pack(fill="x", padx=px, pady=(8, 2))
        ctk.CTkLabel(r, text="Auto-built notes",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=theme.TEXT).pack(side="left")
        ctk.CTkButton(r, text="Rebuild now", width=90, height=24,
                      font=ctk.CTkFont(size=11),
                      command=lambda: None).pack(side="right")  # stub

        ctk.CTkLabel(s, text=chatter["auto_notes"],
                     font=ctk.CTkFont(size=12), text_color=theme.MUTED_TEXT,
                     wraplength=340, justify="left").pack(anchor="w", padx=px, pady=(0, 4))
        ctk.CTkLabel(
            s,
            text="Generated from this viewer's chat activity. Refines automatically every ~20 messages.",
            font=ctk.CTkFont(size=10), text_color=theme.MUTED_TEXT,
            wraplength=340, justify="left",
        ).pack(anchor="w", padx=px, pady=(0, 8))

        _divider(s, px)

        # Private notes
        _section_lbl(s, "Your private notes", px)
        self._notes = ctk.CTkTextbox(s, height=80, font=ctk.CTkFont(size=12))
        self._notes.pack(fill="x", padx=px, pady=(0, 4))
        if chatter.get("private_notes"):
            self._notes.insert("1.0", chatter["private_notes"])
        ctk.CTkLabel(
            s,
            text="Free-text. Will be injected into the AI's context when this viewer chats.",
            font=ctk.CTkFont(size=10), text_color=theme.MUTED_TEXT,
            wraplength=340, justify="left",
        ).pack(anchor="w", padx=px, pady=(0, 6))
        ctk.CTkButton(s, text="Save Notes", height=28, font=ctk.CTkFont(size=12),
                      command=lambda: None).pack(anchor="w", padx=px, pady=(0, 10))  # stub

        _divider(s, px)

        # Recent messages
        _section_lbl(s, f"Recent messages (last {len(chatter['recent_messages'])})", px)
        box = ctk.CTkFrame(s, fg_color=theme.APP_BG, corner_radius=6)
        box.pack(fill="x", padx=px, pady=(0, 14))
        for ts, msg in chatter["recent_messages"]:
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill="x", padx=8, pady=3)
            ctk.CTkLabel(row, text=ts, font=ctk.CTkFont(size=10),
                         text_color=theme.MUTED_TEXT, width=80, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=msg, font=ctk.CTkFont(size=12),
                         text_color=theme.TEXT, wraplength=260,
                         justify="left", anchor="w").pack(side="left", padx=(6, 0))

class BlacklistPanel(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._build()

    @staticmethod
    def _path() -> Path:
        return _BLACKLIST_PATH

    def _load(self) -> list[str]:
        try:
            p = self._path()
            if p.exists():
                return [ln.strip() for ln in p.read_text().splitlines() if ln.strip()]
        except Exception as e:
            print(f"[Blacklist] read error: {e}")
        return []

    def _save(self, names: list[str]):
        try:
            self._path().write_text("\n".join(names) + ("\n" if names else ""))
        except Exception as e:
            print(f"[Blacklist] write error: {e}")

    def _build(self):
        add_bar = ctk.CTkFrame(self, fg_color="transparent")
        add_bar.pack(fill="x", padx=12, pady=(12, 6))

        self._entry = ctk.CTkEntry(add_bar, placeholder_text="Username to blacklist…",
                                   font=ctk.CTkFont(size=12))
        self._entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._entry.bind("<Return>", lambda e: self._add_from_entry())

        ctk.CTkButton(add_bar, text="Add", width=52, height=28,
                      font=ctk.CTkFont(size=11),
                      command=self._add_from_entry).pack(side="right")

        self._status = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=11),
                                    text_color=theme.MUTED_TEXT)
        self._status.pack(anchor="w", padx=14, pady=(0, 4))

        _divider(self)

        # Scrollable list
        self._list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._list_frame.pack(fill="both", expand=True, padx=6, pady=(0, 10))

        self.refresh()

    def refresh(self):
        for w in self._list_frame.winfo_children():
            w.destroy()

        names = self._load()
        if not names:
            ctk.CTkLabel(self._list_frame, text="No users blacklisted.",
                         text_color=theme.MUTED_TEXT,
                         font=ctk.CTkFont(size=12)).pack(pady=20)
            return

        for name in names:
            self._add_row(name)

        self._status.configure(text=f"{len(names)} user{'s' if len(names) != 1 else ''} blacklisted.")

    def _add_row(self, name: str):
        row = ctk.CTkFrame(self._list_frame, fg_color=theme.APP_BG, corner_radius=6)
        row.pack(fill="x", pady=2, padx=2)

        ctk.CTkLabel(row, text="🚫", font=ctk.CTkFont(size=12),
                     width=24).pack(side="left", padx=(8, 4), pady=6)

        ctk.CTkLabel(row, text=name, font=ctk.CTkFont(size=12),
                     text_color=theme.TEXT, anchor="w").pack(
            side="left", fill="x", expand=True)

        ctk.CTkButton(
            row, text="Remove", width=64, height=24,
            font=ctk.CTkFont(size=11),
            fg_color=theme.DANGER, hover_color="#922b21",
            command=lambda n=name: self._confirm_remove(n),
        ).pack(side="right", padx=(4, 8), pady=4)

    def _add_from_entry(self):
        name = self._entry.get().strip().lower()
        if not name:
            return
        names = self._load()
        if name in names:
            self._status.configure(text=f"{name} is already blacklisted.",
                                   text_color=theme.MUTED_TEXT)
            return
        names.append(name)
        self._save(names)
        self._entry.delete(0, "end")
        self._status.configure(text=f"Added {name}.", text_color=theme.SUCCESS)
        self.refresh()

    def add_user(self, username: str):
        name = username.strip().lower()
        names = self._load()
        if name not in names:
            names.append(name)
            self._save(names)
        self.refresh()

    def _confirm_remove(self, name: str):
        ConfirmDialog(
            self,
            title="Remove from Blacklist",
            message=f"Remove '{name}' from the blacklist?",
            on_confirm=lambda n=name: self._do_remove(n),
        )

    def _do_remove(self, name: str):
        names = [n for n in self._load() if n != name]
        self._save(names)
        self._status.configure(text=f"Removed {name}.", text_color=theme.MUTED_TEXT)
        self.refresh()

class MiddleColumn(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=theme.CARD_BG, corner_radius=10, **kwargs)

        tab_bar = ctk.CTkFrame(self, fg_color="transparent")
        tab_bar.pack(fill="x", padx=12, pady=(12, 0))

        self._tab_history_btn = ctk.CTkButton(
            tab_bar, text="Chatter History",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=30, corner_radius=6,
            command=lambda: self._show("history"),
        )
        self._tab_history_btn.pack(side="left", padx=(0, 6))

        self._tab_bl_btn = ctk.CTkButton(
            tab_bar, text="Blacklist",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=30, corner_radius=6,
            command=lambda: self._show("blacklist"),
        )
        self._tab_bl_btn.pack(side="left")

        self._history_panel = ctk.CTkFrame(self, fg_color="transparent")
        self._build_history(self._history_panel)

        self._blacklist_panel = BlacklistPanel(self)

        self._show("history")

    def _show(self, which: str):
        self._history_panel.pack_forget()
        self._blacklist_panel.pack_forget()

        active   = theme.BUTTON_BG if hasattr(theme, "BUTTON_BG") else "#1f6aa5"
        inactive = "#2B2B2B"

        if which == "history":
            self._history_panel.pack(fill="both", expand=True)
            self._tab_history_btn.configure(fg_color=active)
            self._tab_bl_btn.configure(fg_color=inactive)
        else:
            self._blacklist_panel.pack(fill="both", expand=True)
            self._tab_bl_btn.configure(fg_color=active)
            self._tab_history_btn.configure(fg_color=inactive)
            self._blacklist_panel.refresh()

    def _build_history(self, parent):
        split = ctk.CTkFrame(parent, fg_color="transparent")
        split.pack(fill="both", expand=True, padx=0, pady=(8, 10))

        roster = ctk.CTkScrollableFrame(split, fg_color="transparent", width=230)
        roster.pack(side="left", fill="y", padx=(10, 4))

        self._detail = ChatterDetailPanel(split)
        self._detail.pack(side="left", fill="both", expand=True, padx=(4, 10))

        self._roster_frame = roster

        for c in STUB_CHATTERS:
            self._add_roster_row(c)

    def _add_roster_row(self, chatter: dict):
        tier = tier_from_role(chatter.get("role", ""))
        name_color = tier[1] if tier else chatter.get("color", "#FFFFFF")

        row = ctk.CTkFrame(self._roster_frame, fg_color=theme.APP_BG, corner_radius=8)
        row.pack(fill="x", pady=3)

        if tier:
            _pill(row, tier[0], tier[1], tier[2]).pack(side="left", padx=(8, 6), pady=8)
        else:
            ctk.CTkLabel(row, text="●", font=ctk.CTkFont(size=10),
                         text_color=chatter.get("color", "#AAAAAA"),
                         width=20).pack(side="left", padx=(8, 4), pady=8)

        ctk.CTkLabel(row, text=chatter["username"],
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=name_color, anchor="w").pack(
            side="left", fill="x", expand=True)

        ctk.CTkLabel(row, text=f"{chatter['msg_count']} msgs",
                     font=ctk.CTkFont(size=10),
                     text_color=theme.MUTED_TEXT).pack(side="right", padx=(4, 10))

        # Left-click → load detail
        row.bind("<Button-1>", lambda e, c=chatter: self._detail.load(c))

        # Right-click → context menu (Blacklist)
        row.bind("<Button-3>", lambda e, c=chatter: self._roster_context(e, c))

    def _roster_context(self, event, chatter: dict):
        menu = ctk.CTkFrame(self, fg_color=theme.CARD_BG, corner_radius=6,
                            border_width=1, border_color=theme.MUTED_TEXT)

        def _bl():
            menu.destroy()
            self._blacklist_panel.add_user(chatter["username"])
            self._show("blacklist")

        ctk.CTkButton(
            menu,
            text=f"🚫  Blacklist {chatter['username']}",
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            hover_color=theme.DANGER,
            anchor="w",
            command=_bl,
        ).pack(padx=4, pady=4)

        # Position near cursor
        menu.place(x=event.x_root - self.winfo_rootx(),
                   y=event.y_root - self.winfo_rooty())

        # Dismiss on any click elsewhere
        self.bind("<Button-1>", lambda e: menu.destroy(), add="+")

    @property
    def blacklist(self) -> BlacklistPanel:
        return self._blacklist_panel

class TwitchPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14, pady=14)

        # Col 1 — Chat (fixed narrow)
        c_chat = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=10, width=240)
        c_chat.pack(side="left", fill="y", padx=(0, 8))
        c_chat.pack_propagate(False)
        self._build_chat(c_chat)

        # Col 2 — Chatter History + Blacklist tabs (expands)
        self._middle = MiddleColumn(body)
        self._middle.pack(side="left", fill="both", expand=True, padx=(0, 8))

        # Col 3 — Settings (top) + Events (bottom), fixed width
        c_right = ctk.CTkFrame(body, fg_color="transparent", width=300)
        c_right.pack(side="left", fill="y")
        c_right.pack_propagate(False)

        f_settings = ctk.CTkFrame(c_right, fg_color=theme.CARD_BG, corner_radius=10)
        f_settings.pack(fill="both", expand=True, pady=(0, 8))
        self._build_settings(f_settings)

        f_events = ctk.CTkFrame(c_right, fg_color=theme.CARD_BG, corner_radius=10)
        f_events.pack(fill="both", expand=True)
        self._build_events(f_events)

        # Register this page with the twitchchat backend so it can push live data
        if twitchchat and hasattr(twitchchat, "set_ui_page"):
            twitchchat.set_ui_page(self)

    def _build_chat(self, parent):
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", padx=12, pady=(12, 6))
        ctk.CTkLabel(hdr, text="Twitch Chat",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=theme.TEXT).pack(side="left")

        self._chat_paused = False
        self._pause_btn = ctk.CTkButton(
            hdr, text="⏸", width=32, height=26,
            font=ctk.CTkFont(size=12),
            command=self._toggle_pause,
        )
        self._pause_btn.pack(side="right")

        self._chat_area = ctk.CTkScrollableFrame(
            parent, fg_color=theme.APP_BG, corner_radius=6)
        self._chat_area.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        for username, color, role, message in STUB_CHAT:
            self._append_chat(username, color, role, message)

        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.pack(fill="x", padx=8, pady=(0, 10))
        self._chat_input = ctk.CTkEntry(
            bar, placeholder_text="Send a message…", font=ctk.CTkFont(size=12))
        self._chat_input.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._chat_input.bind("<Return>", self._send_msg)
        ctk.CTkButton(bar, text="Send", width=52, height=28,
                      font=ctk.CTkFont(size=11),
                      command=self._send_msg).pack(side="right")

    def _append_chat(self, username: str, color: str, role: str = "", message: str = ""):
        """Add one message row — no timestamp, compact pill + name: msg."""
        if self._chat_paused:
            return

        tier = tier_from_role(role)
        name_color = tier[1] if tier else color

        row = ctk.CTkFrame(self._chat_area, fg_color="transparent")
        row.pack(fill="x", pady=1, padx=4)

        # Single-letter tier pill (saves width in narrow column)
        if tier:
            _pill(row, tier[0][0], tier[1], tier[2], font_size=9).pack(
                side="left", padx=(0, 4))

        ctk.CTkLabel(
            row,
            text=username + ":",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=name_color, anchor="w",
        ).pack(side="left", padx=(0, 3))

        ctk.CTkLabel(
            row,
            text=message,
            font=ctk.CTkFont(size=11),
            text_color=theme.TEXT,
            wraplength=120,      # wider now that timestamps are gone
            justify="left", anchor="w",
        ).pack(side="left", fill="x", expand=True)

    def _toggle_pause(self):
        self._chat_paused = not self._chat_paused
        self._pause_btn.configure(text="▶" if self._chat_paused else "⏸")

    def _send_msg(self, event=None):
        text = self._chat_input.get().strip()
        if not text:
            return
        self._chat_input.delete(0, "end")
        # stub: twitchchat.send_message(text)
        self._append_chat("You", "#FFFFFF", "", text)

    def _build_settings(self, parent):
        inner = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        inner.pack(fill="both", expand=True)

        ctk.CTkLabel(inner, text="Twitch Settings",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=14, pady=(14, 8))

        self.channel_var = ctk.StringVar(
            value=get_auth("twitch", "channel_name") if get_auth else "")
        self.policy_var = ctk.StringVar(
            value=get_settings("TWITCH_POLICY") if get_settings else "latest-buffered")
        self.paused_var = ctk.BooleanVar(
            value=bool(get_settings("TWITCH_PAUSED")) if get_settings else False)

        self._entry_row(inner, "Channel Name", self.channel_var)
        self._combo_row(inner, "Message Policy", self.policy_var,
                        ["latest", "latest-buffered", "all"])

        ctk.CTkCheckBox(inner, text="Pause Chat", variable=self.paused_var,
                        text_color=theme.TEXT).pack(anchor="w", padx=14, pady=6)

        ctk.CTkButton(inner, text="Save Settings",
                      command=self.save_twitch_settings).pack(fill="x", padx=14, pady=(10, 4))

        self.left_status = ctk.CTkLabel(
            inner, text="No changes saved yet.",
            text_color=theme.MUTED_TEXT, font=ctk.CTkFont(size=11))
        self.left_status.pack(anchor="w", padx=14, pady=(0, 8))

        _divider(inner)
        _section_lbl(inner, "Authentication")

        has_token = bool(get_auth and get_auth("twitch", "access_token"))
        self.auth_status_label = ctk.CTkLabel(
            inner,
            text="Access token saved." if has_token else "No access token saved.",
            text_color=theme.SUCCESS if has_token else theme.MUTED_TEXT,
            font=ctk.CTkFont(size=11))
        self.auth_status_label.pack(anchor="w", padx=14, pady=(0, 6))

        ctk.CTkButton(inner, text="Set Access Token",
                      command=self.open_access_dialog).pack(fill="x", padx=14, pady=3)
        ctk.CTkButton(inner, text="Clear Access Token",
                      fg_color=theme.DANGER, hover_color="#922b21",
                      command=self.clear_access_token).pack(fill="x", padx=14, pady=3)

        _divider(inner)
        _section_lbl(inner, "Runtime")

        self.status_label = ctk.CTkLabel(
            inner, text=self._runtime_status(),
            text_color=theme.MUTED_TEXT, font=ctk.CTkFont(size=11),
            wraplength=240, justify="left")
        self.status_label.pack(anchor="w", padx=14, pady=(0, 6))

        ctk.CTkButton(inner, text="Start Chat Reader",
                      command=self.start_chat_reader).pack(fill="x", padx=14, pady=3)
        ctk.CTkButton(inner, text="Refresh Status",
                      command=self.refresh_status).pack(fill="x", padx=14, pady=(3, 14))

    def _build_events(self, parent):
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(12, 6))
        ctk.CTkLabel(hdr, text="Twitch Events",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=theme.TEXT).pack(side="left")
        ctk.CTkButton(hdr, text="Clear", width=52, height=24,
                      font=ctk.CTkFont(size=11),
                      fg_color=theme.DANGER, hover_color="#922b21",
                      command=self._clear_events).pack(side="right")

        self._events_area = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        self._events_area.pack(fill="both", expand=True, padx=6, pady=(0, 10))

        for evt_type, username, detail, time_ago in STUB_EVENTS:
            self._append_event(evt_type, username, detail, time_ago)

    def _append_event(self, event_type: str, username: str, detail: str,
                      time_ago: str = "just now"):
        style = EVENT_STYLES.get(event_type, ("•", theme.MUTED_TEXT, "{detail}"))
        icon, color, template = style
        description = template.replace("{detail}", detail).replace("{count}", detail)

        card = ctk.CTkFrame(self._events_area, fg_color=theme.APP_BG, corner_radius=6)
        card.pack(fill="x", pady=3, padx=2)

        ctk.CTkLabel(card, text=icon, font=ctk.CTkFont(size=16),
                     text_color=color, width=30).pack(side="left", padx=(8, 4), pady=8)

        col = ctk.CTkFrame(card, fg_color="transparent")
        col.pack(side="left", fill="x", expand=True, pady=6, padx=(0, 8))

        top = ctk.CTkFrame(col, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text=username,
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=color, anchor="w").pack(side="left")
        ctk.CTkLabel(top, text=f" • {description}",
                     font=ctk.CTkFont(size=12),
                     text_color=theme.TEXT, anchor="w",
                     wraplength=220, justify="left").pack(side="left")

        ctk.CTkLabel(col, text=time_ago, font=ctk.CTkFont(size=10),
                     text_color=theme.MUTED_TEXT, anchor="w").pack(anchor="w")

    def _clear_events(self):
        for w in self._events_area.winfo_children():
            w.destroy()

    def push_chat_message(self, username: str, tags: dict, message: str):
        color = tags.get("color") or "#FFFFFF"
        tier  = resolve_tier(tags)
        role  = ""
        if tier is TIER_BROADCASTER: role = "broadcaster"
        elif tier is TIER_MOD:       role = "mod"
        elif tier is TIER_SUB:       role = "sub"
        elif tier is TIER_FIRST:     role = "first"
        elif tier is TIER_RETURNING: role = "returning"

        self.after(0, self._append_chat, username, color, role, message)

        if tags.get("first-msg") == "1":
            self.after(0, self.push_event, "first_msg", username, "")

        bits = tags.get("bits", "")
        if bits:
            self.after(0, self.push_event, "bits", username, bits)

    def push_event(self, event_type: str, username: str, detail: str):
        ts = datetime.now().strftime("%I:%M:%S %p")
        self._append_event(event_type, username, detail, ts)


    def _entry_row(self, parent, label, var):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=14, pady=5)
        ctk.CTkLabel(f, text=label, text_color=theme.TEXT,
                     width=120, anchor="w").pack(side="left")
        ctk.CTkEntry(f, textvariable=var).pack(
            side="left", fill="x", expand=True, padx=(6, 0))

    def _combo_row(self, parent, label, var, values):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=14, pady=5)
        ctk.CTkLabel(f, text=label, text_color=theme.TEXT,
                     width=120, anchor="w").pack(side="left")
        ctk.CTkComboBox(f, variable=var, values=values, width=160).pack(
            side="left", padx=(6, 0))

    def _runtime_status(self):
        if not twitchchat:
            return "Twitch backend unavailable."
        return ("Chat reader is running."
                if twitchchat.read_chat_running else "Chat reader is stopped.")

    def open_access_dialog(self):
        AuthTokenDialog(
            self,
            title="Twitch Access Token",
            label="Paste your Twitch access token. It will be saved to userdata/auth.json.",
            on_save=self.save_access_token,
        )

    def save_access_token(self, token):
        if save_auth:
            save_auth("twitch", "access_token", token)
        self.auth_status_label.configure(
            text="Access token saved.", text_color=theme.SUCCESS)

    def clear_access_token(self):
        if save_auth:
            save_auth("twitch", "access_token", "")
        self.auth_status_label.configure(
            text="Access token cleared.", text_color=theme.MUTED_TEXT)

    def save_twitch_settings(self):
        channel = self.channel_var.get().strip()
        if save_auth:
            save_auth("twitch", "channel_name", channel)
        if save_settings:
            save_settings("TWITCH_POLICY", self.policy_var.get())
            save_settings("TWITCH_PAUSED", bool(self.paused_var.get()))
        self.left_status.configure(text="Settings saved.")

    def start_chat_reader(self):
        if not twitchchat:
            self.status_label.configure(text="Twitch backend unavailable.")
            return
        token   = get_auth("twitch", "access_token") if get_auth else ""
        channel = self.channel_var.get().strip()
        if not token:
            self.status_label.configure(text="Access token required before starting.")
            return
        if not channel:
            self.status_label.configure(text="Channel name required before starting.")
            return
        twitchchat.twitch_access_token = token
        twitchchat.twitch_channel      = channel
        if twitchchat.read_chat_running:
            self.status_label.configure(text="Chat reader is already running.")
            return
        twitchchat.read_chat()
        self.status_label.configure(text=f"Started for #{channel}.")

    def refresh_status(self):
        self.status_label.configure(text=self._runtime_status())