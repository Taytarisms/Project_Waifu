import customtkinter as ctk
import threading
import urllib.request
from io import BytesIO
from pathlib import Path
from datetime import datetime
from files.ui import theme
from files.ui.components.twitch import TwitchAuthDialog

try:
    from PIL import Image, ImageDraw, ImageFilter
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

try:
    from files.system_setup.settings import get_auth, save_auth, get_settings, save_settings
except Exception:
    get_auth = save_auth = get_settings = save_settings = None

try:
    from files.twitch_chat import twitchchat
except Exception as e:
    print(f"Failed to import twitchchat file: {e}")
    twitchchat = None

_BLACKLIST_PATH = Path(__file__).resolve().parents[2] / "twitch_chat" / "blacklist.txt"
TIER_BROADCASTER = ("BROADCASTER", "#FFD700", "#3A2F00")
TIER_MOD         = ("MOD",         "#2ECC71", "#0D3320")
TIER_SUB         = ("SUB",         "#6441A4", "#2A1A4A")
TIER_FIRST       = ("FIRST",       "#1ABC9C", "#0A2E28")
TIER_RETURNING   = ("RETURN",      "#5DADE2", "#0D2035")
TIER_REGULAR     = None

def normalize_color(value, fallback="#FFFFFF") -> str:
    if not value:
        return fallback
    color = str(value).strip()
    if color.startswith("#") and len(color) == 7:
        return color
    if color.lower().startswith("0x") and len(color) == 8:
        return "#" + color[2:]
    return fallback

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
    "follow":      ("♥",  "#E91E8C", "Followed you"),
    "sub":         ("★",  "#6441A4", "Subscribed"),
    "resub":       ("★",  "#9B59B6", "Resubscribed ({detail})"),
    "raid":        ("⚡", "#F39C12", "Raided with {detail} viewers"),
    "bits":        ("💎", "#00BFFF", "Cheered {detail} bits"),
    "first_msg":   ("👋", "#1ABC9C", "First message"),
    "milestone":   ("🔥", "#E74C3C", "Reached {detail}"),
    "gift_sub":    ("🎁", "#27AE60", "Gifted a sub to {detail}"),
    "poll":        ("📊", "#8E44AD", "Poll: {detail}"),
    "prediction":  ("🎯", "#D35400", "Prediction: {detail}"),
    "redemption":  ("🏆", "#F1C40F", "Redeemed: {detail}"),
}

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

class _ImageCache:
    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._inflight: set[str] = set() 

    def get(self, user_id: str) -> dict | None:
        return self._cache.get(user_id)

    def has(self, user_id: str) -> bool:
        return user_id in self._cache

    def is_fetching(self, user_id: str) -> bool:
        return user_id in self._inflight

    def store(self, user_id: str, profile, banner):
        self._cache[user_id] = {"profile": profile, "banner": banner}
        self._inflight.discard(user_id)

    def mark_fetching(self, user_id: str):
        self._inflight.add(user_id)

    def fetch_async(self, user_id: str, on_done):
        if self.has(user_id):
            on_done(self._cache[user_id])
            return
        if self.is_fetching(user_id):
            return
        self.mark_fetching(user_id)

        def _worker():
            profile_img = banner_img = None
            try:
                if not twitchchat:
                    print(f"twitchchat unavailable")
                elif not hasattr(twitchchat, "fetch_user_profile"):
                    print(f"twitchchat profile loader is missing, check your install.")
                else:
                    urls = twitchchat.fetch_user_profile(user_id)
                    if urls:
                        profile_url = urls.get("profile_image_url") or ""
                        banner_url  = (urls.get("banner_image_url") or
                                       urls.get("offline_image_url") or "")
                        if _HAS_PIL and profile_url:
                            profile_img = _download_image(profile_url)
                        if _HAS_PIL and banner_url:
                            banner_img = _download_image(banner_url)
            except Exception as e:
                print(f"Picture retrieval failed for {user_id}: {e}")
            self.store(user_id, profile_img, banner_img)
            try:
                on_done(self._cache[user_id])
            except Exception as e:
                print(f"Result callback failed: {e}")

        threading.Thread(target=_worker, daemon=True).start()


def _download_image(url: str, timeout: float = 6.0):
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TwitchUI/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        return Image.open(BytesIO(data)).convert("RGBA")
    except Exception:
        return None


def _circular_avatar(img, size: int):
    if not _HAS_PIL or img is None:
        return None
    img = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return ctk.CTkImage(light_image=out, dark_image=out, size=(size, size))


def _banner_with_gradient(img, width: int, height: int):
    if not _HAS_PIL or img is None:
        return None
    img = img.convert("RGBA")
    src_w, src_h = img.size
    ratio = max(width / src_w, height / src_h)
    new_w, new_h = int(src_w * ratio), int(src_h * ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    # Center crop
    left = (new_w - width) // 2
    top  = (new_h - height) // 2
    img = img.crop((left, top, left + width, top + height))
    grad_col = Image.new("RGBA", (1, height), (0, 0, 0, 0))
    for y in range(height):
        alpha = int(180 * (y / height) ** 1.5)
        grad_col.putpixel((0, y), (10, 10, 18, alpha))
    overlay = grad_col.resize((width, height), Image.NEAREST)
    img = Image.alpha_composite(img, overlay)
    return ctk.CTkImage(light_image=img, dark_image=img, size=(width, height))

_image_cache = _ImageCache()

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

class ChatterDetailPanel(ctk.CTkScrollableFrame):
    def __init__(self, master, on_close=None, **kwargs):
        super().__init__(master, fg_color=theme.APP_BG, corner_radius=8, **kwargs)
        self._content = None
        self._on_close = on_close
        self._resize_after_id = None
        self._pending_resize_width = None

    def _clear(self):
        if self._content is not None:
            self._content.destroy()
            self._content = None
        if self._on_close:
            self._on_close()

    def load(self, chatter: dict):
        # Destroy any prior content
        if self._content is not None:
            self._content.destroy()
            self._content = None

        s = ctk.CTkFrame(self, fg_color="transparent")
        s.pack(fill="both", expand=True)
        self._content = s

        px = 16
        tier = tier_from_role(chatter.get("role", ""))
        name_color = tier[1] if tier else chatter.get("color", "#FFFFFF")
        user_id = chatter.get("user_id", "")

        BANNER_H = 90
        self._banner_frame = ctk.CTkFrame(
            s, fg_color="#1A1F2E", height=BANNER_H, corner_radius=0,
        )
        self._banner_frame.pack(fill="x")
        self._banner_frame.pack_propagate(False)

        self._banner_lbl = ctk.CTkLabel(self._banner_frame, text="", fg_color="transparent")
        self._banner_lbl.place(relx=0, rely=0, relwidth=1, relheight=1)

        ctk.CTkButton(
            self._banner_frame, text="✕", width=26, height=24,
            font=ctk.CTkFont(size=12),
            fg_color="#1A1F2E",     # solid dark to blend with banner placeholder, juust incase theres' nothing there from the Twitchio call
            hover_color=theme.DANGER,
            text_color="#FFFFFF",
            command=self._clear,
        ).place(relx=1.0, x=-8, y=8, anchor="ne")

        head = ctk.CTkFrame(s, fg_color="transparent")
        head.pack(fill="x", padx=px, pady=(0, 0))
        head.pack_configure(pady=(0, 8))

        AVATAR_SIZE = 56
        self._avatar_lbl = ctk.CTkLabel(
            head, text="", width=AVATAR_SIZE, height=AVATAR_SIZE,
            fg_color=theme.CARD_BG, corner_radius=AVATAR_SIZE // 2,
        )
        self._avatar_lbl.pack(side="left", pady=(0, 4))

        name_block = ctk.CTkFrame(head, fg_color="transparent")
        name_block.pack(side="left", fill="x", expand=True, padx=(12, 0), pady=(8, 0))

        ctk.CTkLabel(
            name_block, text=chatter["username"],
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=name_color, anchor="w",
        ).pack(anchor="w")

        if tier:
            _pill(name_block, tier[0], tier[1], tier[2]).pack(anchor="w", pady=(4, 0))

        ts_frame = ctk.CTkFrame(s, fg_color="transparent")
        ts_frame.pack(fill="x", padx=px, pady=(4, 12))

        self._first_seen_lbl = ctk.CTkLabel(
            ts_frame, text=f"First seen: {chatter['first_seen']}",
            font=ctk.CTkFont(size=11), text_color=theme.MUTED_TEXT,
            anchor="w", justify="left",
        )
        self._first_seen_lbl.pack(anchor="w", fill="x")

        self._last_seen_lbl = ctk.CTkLabel(
            ts_frame, text=f"Last seen:  {chatter['last_seen']}",
            font=ctk.CTkFont(size=11), text_color=theme.MUTED_TEXT,
            anchor="w", justify="left",
        )
        self._last_seen_lbl.pack(anchor="w", fill="x")

        _divider(s, px)
        r = ctk.CTkFrame(s, fg_color="transparent")
        r.pack(fill="x", padx=px, pady=(10, 4))
        ctk.CTkLabel(r, text="Auto-built notes",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=theme.TEXT).pack(side="left")
        ctk.CTkButton(r, text="Rebuild now", width=90, height=24,
                      font=ctk.CTkFont(size=11),
                      command=lambda: None).pack(side="right")

        self._auto_notes_lbl = ctk.CTkLabel(
            s, text=chatter["auto_notes"],
            font=ctk.CTkFont(size=12), text_color=theme.MUTED_TEXT,
            anchor="w", justify="left",
        )
        self._auto_notes_lbl.pack(anchor="w", fill="x", padx=px, pady=(0, 4))

        self._auto_help_lbl = ctk.CTkLabel(
            s, text="NOT IMPLETEMENTED YET!!! Generated from chat activity. Refines automatically every ~20 messages.",
            font=ctk.CTkFont(size=10), text_color=theme.MUTED_TEXT,
            anchor="w", justify="left",
        )
        self._auto_help_lbl.pack(anchor="w", fill="x", padx=px, pady=(0, 12))
        _divider(s, px)
        _section_lbl(s, "Your private notes", px)
        self._notes = ctk.CTkTextbox(s, height=80, font=ctk.CTkFont(size=12))
        self._notes.pack(fill="x", padx=px, pady=(0, 4))
        if chatter.get("private_notes"):
            self._notes.insert("1.0", chatter["private_notes"])

        self._notes_help_lbl = ctk.CTkLabel(
            s, text="Free-text. Injected into the AI's context when this viewer chats.",
            font=ctk.CTkFont(size=10), text_color=theme.MUTED_TEXT,
            anchor="w", justify="left",
        )
        self._notes_help_lbl.pack(anchor="w", fill="x", padx=px, pady=(0, 8))
        ctk.CTkButton(s, text="Save Notes", height=28,
                      font=ctk.CTkFont(size=12),
                      command=lambda: None).pack(anchor="w", padx=px, pady=(0, 14))
        _divider(s, px)
        _section_lbl(s, f"Recent messages (last {len(chatter['recent_messages'])})", px)
        box = ctk.CTkFrame(s, fg_color="#0E1118", corner_radius=6)
        box.pack(fill="x", padx=px, pady=(0, 16))

        self._msg_labels = []  # for dynamic rewrap
        for ts, msg in chatter["recent_messages"]:
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=4)
            ctk.CTkLabel(row, text=ts, font=ctk.CTkFont(size=10),
                         text_color=theme.MUTED_TEXT, width=80, anchor="w").pack(side="left")
            lbl = ctk.CTkLabel(row, text=msg,
                               font=ctk.CTkFont(size=12),
                               text_color=theme.TEXT,
                               justify="left", anchor="w")
            lbl.pack(side="left", fill="x", expand=True, padx=(6, 0))
            self._msg_labels.append(lbl)
        self._content.bind("<Configure>", self._on_resize)
        if user_id and _HAS_PIL:
            _image_cache.fetch_async(user_id, self._on_images_ready)

    def _on_resize(self, event):
        if self._content is None:
            return
        self._pending_resize_width = event.width
        if self._resize_after_id is not None:
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after(40, self._apply_pending_resize)

    def _apply_pending_resize(self):
        self._resize_after_id = None
        if self._content is None or self._pending_resize_width is None:
            return
        w = max(100, self._pending_resize_width - 40)
        for lbl in (
            getattr(self, "_auto_notes_lbl", None),
            getattr(self, "_auto_help_lbl", None),
            getattr(self, "_notes_help_lbl", None),
            getattr(self, "_first_seen_lbl", None),
            getattr(self, "_last_seen_lbl", None),
        ):
            if lbl is not None:
                lbl.configure(wraplength=w)
        msg_w = max(80, w - 100)
        for lbl in getattr(self, "_msg_labels", []):
            lbl.configure(wraplength=msg_w)

    def _on_images_ready(self, images: dict):
        try:
            self.after(0, self._apply_images, images)
        except Exception:
            pass

    def _apply_images(self, images: dict):
        if not _HAS_PIL or self._content is None:
            return
        try:
            banner = images.get("banner")
            if banner is not None and hasattr(self, "_banner_lbl"):
                w = self._banner_frame.winfo_width() or 320
                ctk_img = _banner_with_gradient(banner, w, 90)
                if ctk_img is not None:
                    self._banner_lbl.configure(image=ctk_img, text="")
                    self._banner_lbl.image = ctk_img  # keep reference

            profile = images.get("profile")
            if profile is not None and hasattr(self, "_avatar_lbl"):
                ctk_img = _circular_avatar(profile, 56)
                if ctk_img is not None:
                    self._avatar_lbl.configure(image=ctk_img, text="", fg_color="transparent")
                    self._avatar_lbl.image = ctk_img
        except Exception as e:
            print(f"image apply failed: {e}")

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
            print(f"Blacklist had a read error: {e}")
        return []

    def _save(self, names: list[str]):
        try:
            self._path().write_text("\n".join(names) + ("\n" if names else ""))
        except Exception as e:
            print(f"Blacklist had a write error: {e}")

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

class ChatterStore:
    MAX_RECENT   = 5
    MAX_MESSAGES = 20

    def __init__(self):
        self._store:  dict[str, dict] = {}
        self._recent: list[str]       = []

    def record(self, username: str, color: str, role: str, message: str,
               user_id: str = "") -> None:
        key = username.lower()
        now = datetime.now().strftime("%m/%d/%Y, %I:%M:%S %p")

        if key not in self._store:
            self._store[key] = {
                "username":        username,
                "user_id":         user_id,
                "color":           color,
                "role":            role,
                "msg_count":       0,
                "first_seen":      now,
                "last_seen":       now,
                "auto_notes":      "(no auto-notes yet — needs more activity)",
                "private_notes":   "",
                "recent_messages": [],
            }

        rec = self._store[key]
        rec["msg_count"]  += 1
        rec["last_seen"]   = now
        rec["color"]       = color
        rec["role"]        = role
        if user_id and not rec.get("user_id"):
            rec["user_id"] = user_id

        ts = datetime.now().strftime("%I:%M:%S %p")
        rec["recent_messages"].append((ts, message))
        if len(rec["recent_messages"]) > self.MAX_MESSAGES:
            rec["recent_messages"].pop(0)

        if key in self._recent:
            self._recent.remove(key)
        self._recent.append(key)
        if len(self._recent) > self.MAX_RECENT:
            self._recent.pop(0)

    def get_recent(self) -> list[dict]:
        return [self._store[k] for k in reversed(self._recent) if k in self._store]

    def search(self, query: str) -> list[dict]:
        q = query.strip().lower()
        if not q:
            return self.get_recent()
        return [v for k, v in self._store.items() if q in k]

    def get(self, username: str) -> dict | None:
        return self._store.get(username.lower())

_chatter_store = ChatterStore()

class LegendPanel(ctk.CTkFrame):
    _ROLES = [
        (TIER_BROADCASTER, "Channel owner / broadcaster"),
        (TIER_MOD,         "Moderator — granted by the broadcaster"),
        (TIER_SUB,         "Active subscriber to the channel"),
        (TIER_FIRST,       "Sending their very first message ever"),
        (TIER_RETURNING,   "Returning chatter (seen before, not sub)"),
        (None,             "Regular / new chatter — color from Twitch profile"),
    ]

    _EVENTS = [
        ("♥",  "#E91E8C", "Followed the channel"),
        ("★",  "#6441A4", "New subscription"),
        ("★",  "#9B59B6", "Resubscription (shows month count)"),
        ("⚡", "#F39C12", "Incoming raid (shows viewer count)"),
        ("💎", "#00BFFF", "Cheer / Bits donation"),
        ("👋", "#1ABC9C", "First-ever message in this channel"),
        ("🔥", "#E74C3C", "Stream streak or gifted-sub upgrade"),
        ("🎁", "#27AE60", "Gifted subscription to another user"),
        ("🏆", "#F1C40F", "Channel point redemption (e.g. Item Swap)"),
        ("📊", "#8E44AD", "Poll event (requires EventSub)"),
        ("🎯", "#D35400", "Prediction event (requires EventSub)"),
    ]

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._build()

    def _build(self):
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=6, pady=(8, 10))

        ctk.CTkLabel(scroll, text="Role Badges",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=12, pady=(8, 4))

        for tier, description in self._ROLES:
            row = ctk.CTkFrame(scroll, fg_color=theme.APP_BG, corner_radius=6)
            row.pack(fill="x", padx=6, pady=2)
            if tier:
                _pill(row, tier[0], tier[1], tier[2]).pack(
                    side="left", padx=(10, 0), pady=8)
            else:
                ctk.CTkLabel(row, text="●", font=ctk.CTkFont(size=12),
                             text_color="#AAAAAA", width=40).pack(
                    side="left", padx=(10, 0), pady=8)
            ctk.CTkLabel(row, text=description,
                         font=ctk.CTkFont(size=12), text_color=theme.MUTED_TEXT,
                         anchor="w").pack(side="left", padx=(10, 10), pady=8)

        ctk.CTkFrame(scroll, fg_color=theme.MUTED_TEXT, height=1).pack(
            fill="x", padx=12, pady=(14, 6))

        ctk.CTkLabel(scroll, text="Event Feed Icons",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=12, pady=(6, 4))

        for icon, color, description in self._EVENTS:
            row = ctk.CTkFrame(scroll, fg_color=theme.APP_BG, corner_radius=6)
            row.pack(fill="x", padx=6, pady=2)
            ctk.CTkLabel(row, text=icon, font=ctk.CTkFont(size=16),
                         text_color=color, width=36).pack(
                side="left", padx=(10, 0), pady=8)
            ctk.CTkLabel(row, text=description,
                         font=ctk.CTkFont(size=12), text_color=theme.MUTED_TEXT,
                         anchor="w").pack(side="left", padx=(8, 10), pady=8)

        ctk.CTkFrame(scroll, fg_color=theme.MUTED_TEXT, height=1).pack(
            fill="x", padx=12, pady=(14, 6))

        ctk.CTkLabel(scroll, text="Chat Column Idenitfiers",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=12, pady=(6, 4))

        ctk.CTkLabel(
            scroll,
            text=(
                "In the Twitch Chat column the badge is condensed to a single letter "
                "to save horizontal space:\n\n"
                "  B = Broadcaster     M = Moderator\n"
                "  S = Subscriber      F = First-time chatter\n"
                "  R = Returning chatter\n\n"
                "No letter = regular or new chatter (shown in their Twitch color)."
            ),
            font=ctk.CTkFont(size=12),
            text_color=theme.MUTED_TEXT,
            justify="left",
            wraplength=400,
        ).pack(anchor="w", padx=12, pady=(0, 14))

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
        self._tab_bl_btn.pack(side="left", padx=(0, 6))

        self._tab_legend_btn = ctk.CTkButton(
            tab_bar, text="Legend",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=30, corner_radius=6,
            command=lambda: self._show("legend"),
        )
        self._tab_legend_btn.pack(side="left")
        self._history_panel = ctk.CTkFrame(self, fg_color="transparent")
        self._build_history(self._history_panel)

        self._blacklist_panel = BlacklistPanel(self)
        self._legend_panel    = LegendPanel(self)

        self._show("history")

    def _show(self, which: str):
        self._history_panel.pack_forget()
        self._blacklist_panel.pack_forget()
        self._legend_panel.pack_forget()

        active   = theme.BUTTON_BG if hasattr(theme, "BUTTON_BG") else "#1f6aa5"
        inactive = "#2B2B2B"

        self._tab_history_btn.configure(fg_color=inactive)
        self._tab_bl_btn.configure(fg_color=inactive)
        self._tab_legend_btn.configure(fg_color=inactive)

        if which == "history":
            self._history_panel.pack(fill="both", expand=True)
            self._tab_history_btn.configure(fg_color=active)
        elif which == "blacklist":
            self._blacklist_panel.pack(fill="both", expand=True)
            self._tab_bl_btn.configure(fg_color=active)
            self._blacklist_panel.refresh()
        else:
            self._legend_panel.pack(fill="both", expand=True)
            self._tab_legend_btn.configure(fg_color=active)

    def _build_history(self, parent):
        search_bar = ctk.CTkFrame(parent, fg_color="transparent")
        search_bar.pack(fill="x", padx=10, pady=(8, 4))

        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._do_search())

        self._search_entry = ctk.CTkEntry(
            search_bar, textvariable=self._search_var,
            placeholder_text="Search chatters…",
            font=ctk.CTkFont(size=12))
        self._search_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._search_entry.bind("<Return>", lambda e: self._do_search())

        ctk.CTkButton(
            search_bar, text="Search", width=70, height=28,
            font=ctk.CTkFont(size=11),
            command=self._do_search,
        ).pack(side="right")

        self._roster_label = ctk.CTkLabel(
            parent, text="Recently active",
            font=ctk.CTkFont(size=10),
            text_color=theme.MUTED_TEXT)
        self._roster_label.pack(anchor="w", padx=14, pady=(0, 2))

        split = ctk.CTkFrame(parent, fg_color="transparent")
        split.pack(fill="both", expand=True, padx=0, pady=(0, 10))
        self._split = split

        self._roster_frame = ctk.CTkScrollableFrame(
            split, fg_color="transparent", width=230)
        self._roster_frame.pack(side="left", fill="both", expand=True,
                                padx=(10, 4))

        self._detail = ChatterDetailPanel(split, on_close=self._hide_detail)
        self._render_roster(_chatter_store.get_recent(), "Recently active")

    def _show_detail(self):
        self._roster_frame.pack_configure(fill="y", expand=False)
        self._detail.pack(side="left", fill="both", expand=True, padx=(4, 10))

    def _hide_detail(self):
        self._detail.pack_forget()
        self._roster_frame.pack_configure(fill="both", expand=True)

    def _do_search(self):
        query   = self._search_var.get().strip()
        results = _chatter_store.search(query)
        label   = (f"{len(results)} result{'s' if len(results) != 1 else ''} for \"{query}\""
                   if query else "Recently active")
        self._render_roster(results, label)

    def _render_roster(self, chatters: list[dict], label: str):
        self._roster_label.configure(text=label)
        for w in self._roster_frame.winfo_children():
            w.destroy()

        if not chatters:
            ctk.CTkLabel(self._roster_frame,
                         text="No chatters found.",
                         text_color=theme.MUTED_TEXT,
                         font=ctk.CTkFont(size=12)).pack(pady=20)
            return

        for chatter in chatters:
            self._add_roster_row(chatter)

    def _refresh_recent_if_idle(self):
        if self._search_var.get().strip() == "":
            self._render_roster(_chatter_store.get_recent(), "Recently active")

    def _add_roster_row(self, chatter: dict):
        tier = tier_from_role(chatter.get("role", ""))
        name_color = tier[1] if tier else chatter.get("color", "#FFFFFF")

        row = ctk.CTkFrame(self._roster_frame, fg_color=theme.APP_BG, corner_radius=8)
        row.pack(fill="x", pady=3)

        if tier:
            _pill(row, tier[0], tier[1], tier[2]).pack(side="left", padx=(8, 6), pady=8)
        else:
            ctk.CTkLabel(row, text="●", font=ctk.CTkFont(size=10),
                         text_color=normalize_color(chatter.get("color"), "#AAAAAA"),
                         width=20).pack(side="left", padx=(8, 4), pady=8)

        ctk.CTkLabel(row, text=chatter["username"],
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=name_color, anchor="w").pack(
            side="left", fill="x", expand=True)

        def _open(c=chatter):
            self._show_detail()
            self._detail.load(c)

        ctk.CTkButton(
            row, text="›", width=28, height=24,
            font=ctk.CTkFont(size=18, weight="bold"),
            fg_color="transparent",
            hover_color=theme.CARD_BG,
            text_color=theme.MUTED_TEXT,
            command=_open,
        ).pack(side="right", padx=(0, 6))

        ctk.CTkLabel(row, text=f"{chatter['msg_count']} msgs",
                     font=ctk.CTkFont(size=10),
                     text_color=theme.MUTED_TEXT).pack(side="right", padx=(4, 4))

        row.bind("<Button-1>", lambda e, c=chatter: _open(c))
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

        menu.place(x=event.x_root - self.winfo_rootx(),
                   y=event.y_root - self.winfo_rooty())

        self.bind("<Button-1>", lambda e: menu.destroy(), add="+")

    @property
    def blacklist(self) -> BlacklistPanel:
        return self._blacklist_panel

class TwitchPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color=theme.APP_BG)
        self.app = app
        self._event_labels = []
        self._events_resize_after_id = None
        self._pending_events_width = None
        self._queue_rows = {}
        self._queue_refresh_after_id = None
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14, pady=14)
        c_chat = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=10, width=240)
        c_chat.pack(side="left", fill="y", padx=(0, 8))
        c_chat.pack_propagate(False)
        self._build_chat(c_chat)
        self._middle = MiddleColumn(body)
        self._middle.pack(side="left", fill="both", expand=True, padx=(0, 8))
        c_right = ctk.CTkFrame(body, fg_color="transparent", width=300)
        c_right.pack(side="left", fill="y")
        c_right.pack_propagate(False)
        f_queue = ctk.CTkFrame(c_right, fg_color=theme.CARD_BG, corner_radius=10, height=220)
        f_queue.pack(fill="x", pady=(0, 8))
        f_queue.pack_propagate(False)
        self._build_queue(f_queue)
        f_settings = ctk.CTkFrame(c_right, fg_color=theme.CARD_BG, corner_radius=10)
        f_settings.pack(fill="both", expand=True, pady=(0, 8))
        self._build_settings(f_settings)
        f_events = ctk.CTkFrame(c_right, fg_color=theme.CARD_BG, corner_radius=10)
        f_events.pack(fill="both", expand=True)
        self._build_events(f_events)

        if twitchchat and hasattr(twitchchat, "set_ui_page"):
            twitchchat.set_ui_page(self)

        if get_settings and bool(get_settings("TWITCH_PAUSED")):
            self.after(0, lambda: self._apply_pause(True))
        self.refresh_queue_panel()
        self._schedule_queue_refresh()

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

        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.pack(side="bottom", fill="x", padx=8, pady=(0, 10))
        self._chat_input = ctk.CTkEntry(
            bar, placeholder_text="Send a message…", font=ctk.CTkFont(size=12))
        self._chat_input.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._chat_input.bind("<Return>", self._send_msg)
        ctk.CTkButton(bar, text="Send", width=52, height=28,
                      font=ctk.CTkFont(size=11),
                      command=self._send_msg).pack(side="right")

        self._pause_banner = ctk.CTkLabel(
            parent,
            text="⏸  CHAT PAUSED",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#1A1A1A",
            fg_color="#F1C40F",
            corner_radius=4,
            height=22,
        )
        self._chat_area = ctk.CTkScrollableFrame(
            parent, fg_color=theme.APP_BG, corner_radius=6)
        self._chat_area.pack(fill="both", expand=True, padx=8, pady=(0, 6))

    def _append_chat(self, username: str, color: str, role: str = "", message: str = ""):
        safe_color = normalize_color(color, "#FFFFFF")
        tier = tier_from_role(role)
        name_color = tier[1] if tier else safe_color
        if self._chat_paused:
            name_color = theme.MUTED_TEXT
            msg_color  = theme.MUTED_TEXT
        else:
            msg_color  = theme.TEXT

        row = ctk.CTkFrame(self._chat_area, fg_color="transparent")
        row.pack(fill="x", pady=(2, 4), padx=4)
        header = ctk.CTkFrame(row, fg_color="transparent")
        header.pack(fill="x", anchor="w")
        if tier:
            _pill(header, tier[0][0], tier[1], tier[2], font_size=9).pack(
                side="left", padx=(0, 4)
            )
        ctk.CTkLabel(
            header,
            text=f"{username}:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=name_color,
            anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            row,
            text=message,
            font=ctk.CTkFont(size=11),
            text_color=msg_color,
            wraplength=175,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=(22 if tier else 4, 0), pady=(1, 0))
        
    def _toggle_pause(self):
        self._apply_pause(not self._chat_paused)

    def _apply_pause(self, paused: bool):
        self._chat_paused = bool(paused)
        if twitchchat and hasattr(twitchchat, "pause_chat_twitch"):
            try:
                twitchchat.pause_chat_twitch(self._chat_paused)
            except Exception as e:
                print(f"[TwitchPage] pause_chat_twitch failed: {e}")
        if hasattr(self, "paused_var") and bool(self.paused_var.get()) != self._chat_paused:
            self.paused_var.set(self._chat_paused)

        # 3. Button icon
        if hasattr(self, "_pause_btn"):
            self._pause_btn.configure(text="▶" if self._chat_paused else "⏸")
        if hasattr(self, "_pause_banner") and hasattr(self, "_chat_area"):
            self._chat_area.pack_forget()
            if self._chat_paused:
                self._pause_banner.pack(fill="x", padx=8, pady=(0, 4))
            else:
                self._pause_banner.pack_forget()
            self._chat_area.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        if save_settings:
            try:
                save_settings("TWITCH_PAUSED", self._chat_paused)
            except Exception:
                pass

    def _send_msg(self, event=None):
        text = self._chat_input.get().strip()
        if not text:
            return
        self._chat_input.delete(0, "end")
        self._append_chat("You", "#FFFFFF", "", text)

    def _build_queue(self, parent):
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(12, 6))
        ctk.CTkLabel(
            hdr,
            text="Pending Queue",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=theme.TEXT,
        ).pack(side="left")
        self._queue_count_label = ctk.CTkLabel(
            hdr,
            text="0",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=theme.MUTED_TEXT,
        )
        self._queue_count_label.pack(side="right")

        self._queue_status = ctk.CTkLabel(
            parent,
            text="Waiting for chat reader.",
            text_color=theme.MUTED_TEXT,
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        self._queue_status.pack(fill="x", padx=14, pady=(0, 4))

        self._queue_list = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        self._queue_list.pack(fill="both", expand=True, padx=8, pady=(0, 10))

    def _schedule_queue_refresh(self):
        try:
            self._queue_refresh_after_id = self.after(1000, self._queue_refresh_tick)
        except Exception:
            self._queue_refresh_after_id = None

    def _queue_refresh_tick(self):
        self._queue_refresh_after_id = None
        self.refresh_queue_panel()
        if self.winfo_exists():
            self._schedule_queue_refresh()

    def refresh_queue_panel(self):
        if not hasattr(self, "_queue_list"):
            return
        if not twitchchat or not hasattr(twitchchat, "list_pending_messages"):
            self._queue_count_label.configure(text="0")
            self._queue_status.configure(text="Queue backend unavailable.")
            self._render_queue_items([])
            return

        try:
            items = twitchchat.list_pending_messages()
        except Exception as e:
            self._queue_count_label.configure(text="!")
            self._queue_status.configure(text=f"Queue read failed: {e}")
            return

        active = bool(
            twitchchat.get_twitch_bot()
            and getattr(twitchchat.get_twitch_bot(), "in_pipeline", False)
        )
        policy = self.policy_var.get() if hasattr(self, "policy_var") else ""
        active_text = "Active turn running" if active else "Ready"
        suffix = f" | policy: {policy}" if policy else ""
        self._queue_count_label.configure(text=str(len(items)))
        self._queue_status.configure(text=f"{active_text}{suffix}")
        self._render_queue_items(items)

    def _render_queue_items(self, items):
        if not items:
            for item_id, row in list(self._queue_rows.items()):
                if item_id is not None:
                    row.destroy()
                    del self._queue_rows[item_id]
            if None not in self._queue_rows:
                empty = ctk.CTkLabel(
                    self._queue_list,
                    text="No pending messages.",
                    text_color=theme.MUTED_TEXT,
                    font=ctk.CTkFont(size=12),
                )
                empty.pack(pady=18)
                self._queue_rows[None] = empty
            return

        current_ids = {item.get("id") for item in items}
        for item_id, row in list(self._queue_rows.items()):
            if item_id not in current_ids:
                row.destroy()
                del self._queue_rows[item_id]

        empty = self._queue_rows.pop(None, None)
        if empty is not None:
            empty.destroy()

        for item in items:
            item_id = item.get("id")
            if item_id not in self._queue_rows:
                self._queue_rows[item_id] = self._add_queue_row(item)
            self._update_queue_row(self._queue_rows[item_id], item)

    def _add_queue_row(self, item):
        row = ctk.CTkFrame(self._queue_list, fg_color=theme.APP_BG, corner_radius=6)
        row.pack(fill="x", pady=3, padx=2)

        top = ctk.CTkFrame(row, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(7, 1))
        row._author_label = ctk.CTkLabel(
            top,
            text="",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=theme.TEXT,
            anchor="w",
        )
        row._author_label.pack(side="left", fill="x", expand=True)
        row._remove_btn = ctk.CTkButton(
            top,
            text="Delete",
            width=62,
            height=22,
            font=ctk.CTkFont(size=10),
            fg_color=theme.DANGER,
            hover_color="#922b21",
            command=lambda item_id=item.get("id"): self._remove_queue_item(item_id),
        )
        row._remove_btn.pack(side="right", padx=(6, 0))

        row._message_label = ctk.CTkLabel(
            row,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=theme.MUTED_TEXT,
            anchor="w",
            justify="left",
            wraplength=220,
        )
        row._message_label.pack(fill="x", padx=8, pady=(0, 7))
        return row

    def _update_queue_row(self, row, item):
        event_type = item.get("event_type") or ""
        priority = f"{event_type} priority" if event_type else ("priority" if item.get("priority") else "standard")
        age = self._format_queue_age(item.get("age_seconds", 0))
        row._author_label.configure(text=f"{item.get('author', 'unknown')} - {priority} - {age}")
        row._message_label.configure(text=item.get("content", ""))

    def _format_queue_age(self, seconds):
        try:
            seconds = int(seconds)
        except Exception:
            seconds = 0
        if seconds < 60:
            return f"{seconds}s"
        minutes = seconds // 60
        return f"{minutes}m {seconds % 60}s"

    def _remove_queue_item(self, item_id):
        if not twitchchat or not hasattr(twitchchat, "remove_pending_message"):
            self._queue_status.configure(text="Queue backend unavailable.")
            return
        removed = twitchchat.remove_pending_message(item_id)
        if removed:
            self._queue_status.configure(text="Message removed from queue.")
        else:
            self._queue_status.configure(text="Message already active or no longer queued.")
        self.refresh_queue_panel()

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
        self._combo_row(
            inner,
            "Message Policy",
            self.policy_var,
            ["latest", "latest-buffered", "all"],
            command=self._on_policy_changed,
        )

        ctk.CTkCheckBox(
            inner, text="Pause Chat", variable=self.paused_var,
            text_color=theme.TEXT,
            command=lambda: self._apply_pause(self.paused_var.get()),
        ).pack(anchor="w", padx=14, pady=6)

        _section_lbl(inner, "Ignored Prefixes")
        self.ignored_prefixes_box = ctk.CTkTextbox(
            inner,
            height=86,
            fg_color=theme.APP_BG,
            text_color=theme.TEXT,
            wrap="none",
        )
        self.ignored_prefixes_box.pack(fill="x", padx=14, pady=(2, 6))
        saved_prefixes = get_settings("TWITCH_IGNORED_PREFIXES") if get_settings else ""
        if saved_prefixes:
            self.ignored_prefixes_box.insert("0.0", str(saved_prefixes))

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

        ctk.CTkButton(inner, text="Authorize Account",
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
        self._events_area.bind("<Configure>", self._schedule_event_rewrap)

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
        col.pack(side="left", fill="both", expand=True, pady=6, padx=(0, 10))
        main = ctk.CTkLabel(
            col,
            text=f"{username} • {description}",
            font=ctk.CTkFont(size=12),
            text_color=theme.TEXT,
            anchor="w",
            justify="left",
            wraplength=210,
        )
        main.pack(anchor="w", fill="x")
        self._event_labels.append(main)
        self._apply_event_wrap(main)

        ctk.CTkLabel(col, text=time_ago, font=ctk.CTkFont(size=10),
                     text_color=theme.MUTED_TEXT, anchor="w").pack(anchor="w")

    def _clear_events(self):
        for w in self._events_area.winfo_children():
            w.destroy()
        self._event_labels.clear()

    def _schedule_event_rewrap(self, event):
        self._pending_events_width = event.width
        if self._events_resize_after_id is not None:
            self.after_cancel(self._events_resize_after_id)
        self._events_resize_after_id = self.after(40, self._apply_pending_event_wraps)

    def _apply_pending_event_wraps(self):
        self._events_resize_after_id = None
        labels = []
        for lbl in self._event_labels:
            if lbl.winfo_exists():
                self._apply_event_wrap(lbl)
                labels.append(lbl)
        self._event_labels = labels

    def _apply_event_wrap(self, label):
        width = self._pending_events_width or self._events_area.winfo_width()
        label.configure(wraplength=max(80, width - 50))

    def push_chat_message(self, username: str, tags: dict, message: str):
        color = normalize_color(tags.get("color"), "#FFFFFF")
        tier  = resolve_tier(tags)
        role  = ""
        if tier is TIER_BROADCASTER: role = "broadcaster"
        elif tier is TIER_MOD:       role = "mod"
        elif tier is TIER_SUB:       role = "sub"
        elif tier is TIER_FIRST:     role = "first"
        elif tier is TIER_RETURNING: role = "returning"

        _chatter_store.record(username, color, role, message,
                              user_id=tags.get("user-id", ""))
        self.after(0, self._append_chat, username, color, role, message)
        self.after(0, self._middle._refresh_recent_if_idle)

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

    def _combo_row(self, parent, label, var, values, command=None):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=14, pady=5)
        ctk.CTkLabel(f, text=label, text_color=theme.TEXT,
                     width=120, anchor="w").pack(side="left")
        combo = ctk.CTkComboBox(
            f,
            variable=var,
            values=values,
            width=160,
            command=command,
        )
        combo.pack(side="left", padx=(6, 0))
        return combo

    def _on_policy_changed(self, value=None):
        policy = (value or self.policy_var.get()).strip()
        if save_settings:
            save_settings("TWITCH_POLICY", policy)
        if twitchchat and hasattr(twitchchat, "set_twitch_policy"):
            policy = twitchchat.set_twitch_policy(policy)
            self.policy_var.set(policy)
        if hasattr(self, "left_status"):
            self.left_status.configure(text=f"Queue policy: {policy}.")
        self.refresh_queue_panel()

    def _runtime_status(self):
        if not twitchchat:
            return "Twitch backend unavailable."
        return ("Chat reader is running."
                if twitchchat.read_chat_running else "Chat reader is stopped.")

    def open_access_dialog(self):
        TwitchAuthDialog(self, on_complete=self._on_auth_complete)

    def _on_auth_complete(self):
        self.auth_status_label.configure(
            text="All accounts authorized.",
            text_color=theme.SUCCESS,
        )
        self.status_label.configure(text=self._runtime_status())

    def clear_access_token(self):
        if save_auth:
            save_auth("twitch", "access_token", "")
            save_auth("twitch", "refresh_token", "")
            save_auth("twitch", "bot_id", "")
            save_auth("twitch", "owner_id", "")
        self.auth_status_label.configure(
            text="Twitch auth cleared.",
            text_color=theme.MUTED_TEXT,
        )

    def save_access_token(self, token):
        if save_auth:
            save_auth("twitch", "access_token", token)
        self.auth_status_label.configure(
            text="Access token saved.", text_color=theme.SUCCESS)

    def save_twitch_settings(self):
        channel = self.channel_var.get().strip()
        policy = self.policy_var.get().strip()
        ignored_prefixes = self.ignored_prefixes_box.get("0.0", "end-1c").strip()
        if save_auth:
            save_auth("twitch", "channel_name", channel)
        if save_settings:
            save_settings("TWITCH_POLICY", policy)
            save_settings("TWITCH_PAUSED", bool(self.paused_var.get()))
            save_settings("TWITCH_IGNORED_PREFIXES", ignored_prefixes)
        if twitchchat and hasattr(twitchchat, "set_twitch_policy"):
            policy = twitchchat.set_twitch_policy(policy)
            self.policy_var.set(policy)
        count = len([line for line in ignored_prefixes.splitlines() if line.strip()])
        self.left_status.configure(text=f"Settings saved. Queue policy: {policy}. Ignored prefixes: {count}.")
        self.refresh_queue_panel()

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
        self._apply_pause(bool(self.paused_var.get()))
        self.status_label.configure(text=f"Started for #{channel}.")

    def refresh_status(self):
        self.status_label.configure(text=self._runtime_status())
