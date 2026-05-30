import threading
import time
import webbrowser
from typing import Optional

import customtkinter as ctk
import requests

try:
    from files.ui import theme
    APP_BG  = theme.APP_BG
    CARD_BG = theme.CARD_BG
    TEXT    = theme.TEXT
    MUTED   = theme.MUTED_TEXT
    SUCCESS = theme.SUCCESS
    DANGER  = theme.DANGER
except Exception:
    APP_BG  = "#0d1117"
    CARD_BG = "#161b22"
    TEXT    = "#e6edf3"
    MUTED   = "#8b949e"
    SUCCESS = "#3fb950"
    DANGER  = "#f85149"

try:
    from files.system_setup.settings import get_auth, get_settings
except Exception:
    def get_auth(group: str, key: str) -> str:      # type: ignore
        return ""
    def get_settings(key: str) -> str:              # type: ignore
        return ""

PROPRIETARY_LLM = {"openai", "claude", "gemini", "grok"}
PROPRIETARY_TTS = {"elevenlabs", "openai", "fishspeech"}


def _fmt_credits(value: float) -> str:
    if value >= 1_000:
        return f"${value:,.2f}"
    return f"${value:.4f}".rstrip("0").rstrip(".")


def _pct_color(pct: float) -> str:
    if pct >= 0.85:
        return "#f85149"
    if pct >= 0.60:
        return "#d29922"
    return "#3fb950"


def _lighten(hex_color: str) -> str:
    r, g, b = (int(hex_color[i:i+2], 16) for i in (1, 3, 5))
    return "#{:02x}{:02x}{:02x}".format(
        min(r + 30, 255), min(g + 30, 255), min(b + 30, 255)
    )

class _BaseCard(ctk.CTkFrame):
    TITLE  = "Provider"
    ACCENT = "#1f6feb"

    def __init__(self, master: ctk.CTkFrame):
        super().__init__(master, fg_color=CARD_BG, corner_radius=10)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 4))

        ctk.CTkLabel(
            header,
            text=self.TITLE,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=TEXT,
        ).pack(side="left")

        self._refresh_btn = ctk.CTkButton(
            header,
            text="↻",
            width=28, height=22,
            font=ctk.CTkFont(size=13),
            fg_color=self.ACCENT,
            hover_color=_lighten(self.ACCENT),
            corner_radius=6,
            command=self._refresh,
        )
        self._refresh_btn.pack(side="right")

        self._status = ctk.CTkLabel(
            self,
            text="Loading…",
            text_color=MUTED,
            font=ctk.CTkFont(size=11),
            anchor="w",
            justify="left",
            wraplength=260,
        )
        self._status.pack(fill="x", padx=14, pady=(0, 4))

        self._bar = ctk.CTkProgressBar(
            self, height=6, corner_radius=3,
            progress_color=SUCCESS, fg_color="#30363d",
        )
        self._bar.set(0)
        self._bar.pack(fill="x", padx=14, pady=(2, 12))
        self._bar.pack_forget()

        self._ts_label = ctk.CTkLabel(
            self, text="", text_color=MUTED,
            font=ctk.CTkFont(size=9), anchor="e",
        )
        self._ts_label.pack(fill="x", padx=14, pady=(0, 10))

        self._fetch_thread()

    def _alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except Exception:
            return False

    def _refresh(self):
        self._set_status("Refreshing...", MUTED)
        self._refresh_btn.configure(state="disabled")
        self._fetch_thread()

    def _fetch(self) -> None:
        raise NotImplementedError

    def _fetch_thread(self):
        threading.Thread(target=self._fetch_wrapper, daemon=True).start()

    def _fetch_wrapper(self):
        try:
            self._fetch()
        except Exception as exc:
            self._set_status(f"Error: {exc}", DANGER)
        finally:
            if self._alive():
                self.after(0, lambda: self._refresh_btn.configure(state="normal") if self._alive() else None)
            self._stamp()

    def _set_status(self, text: str, color: str = TEXT):
        if self._alive():
            self.after(0, lambda: self._status.configure(text=text, text_color=color) if self._alive() else None)

    def _set_progress(self, value: float, color: Optional[str] = None):
        c = color or _pct_color(value)
        def _apply():
            if not self._alive():
                return
            self._bar.set(value)
            self._bar.configure(progress_color=c)
            self._bar.pack(fill="x", padx=14, pady=(2, 12))
        if self._alive():
            self.after(0, _apply)

    def _hide_progress(self):
        if self._alive():
            self.after(0, lambda: self._bar.pack_forget() if self._alive() else None)

    def _stamp(self):
        ts = time.strftime("%H:%M:%S")
        if self._alive():
            self.after(0, lambda: self._ts_label.configure(text=f"Last updated {ts}") if self._alive() else None)

    def _set_dot(self, color: str): pass

class _MiniCard(ctk.CTkFrame):
    TITLE       = "Provider"
    ACCENT      = "#1f6feb"
    CONSOLE_URL = ""

    def __init__(self, master: ctk.CTkFrame, role_tag: str = ""):
        super().__init__(
            master,
            fg_color="#1a2230",
            corner_radius=8,
            border_width=1,
            border_color="#2d3f52",
        )

        title_text = f"{self.TITLE}  ·  {role_tag}" if role_tag else self.TITLE

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(8, 2))

        self._dot = ctk.CTkLabel(
            row, text="●", width=14,
            font=ctk.CTkFont(size=9),
            text_color=MUTED,
        )
        self._dot.pack(side="left", padx=(0, 4))

        ctk.CTkLabel(
            row,
            text=title_text,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        btn_row = ctk.CTkFrame(row, fg_color="transparent")
        btn_row.pack(side="right")

        if self.CONSOLE_URL:
            ctk.CTkButton(
                btn_row,
                text="Console",
                width=56, height=18,
                font=ctk.CTkFont(size=9),
                fg_color=self.ACCENT,
                hover_color=_lighten(self.ACCENT),
                corner_radius=4,
                command=lambda: webbrowser.open(self.CONSOLE_URL),
            ).pack(side="left", padx=(0, 4))

        self._refresh_btn = ctk.CTkButton(
            btn_row,
            text="↻",
            width=22, height=18,
            font=ctk.CTkFont(size=11),
            fg_color="#2d3f52",
            hover_color="#3d5266",
            corner_radius=4,
            command=self._refresh,
        )
        self._refresh_btn.pack(side="left")

        self._status = ctk.CTkLabel(
            self,
            text="Loading…",
            text_color=MUTED,
            font=ctk.CTkFont(size=10),
            anchor="w",
            justify="left",
            wraplength=280,
        )
        self._status.pack(fill="x", padx=10, pady=(0, 2))
        self._bar = ctk.CTkProgressBar(
            self, height=4, corner_radius=2,
            progress_color=SUCCESS, fg_color="#2d3f52",
        )
        self._bar.set(0)
        self._bar.pack(fill="x", padx=10, pady=(0, 8))
        self._bar.pack_forget()

        self._fetch_thread()

    def _alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except Exception:
            return False

    def _refresh(self):
        self._set_status("Refreshing...", MUTED)
        if self._alive():
            self._dot.configure(text_color=MUTED)
            self._refresh_btn.configure(state="disabled")
        self._fetch_thread()

    def _fetch(self) -> None:
        raise NotImplementedError

    def _fetch_thread(self):
        threading.Thread(target=self._fetch_wrapper, daemon=True).start()

    def _fetch_wrapper(self):
        try:
            self._fetch()
        except Exception as exc:
            self._set_status(f"Error: {exc}", DANGER)
            if self._alive():
                self.after(0, lambda: self._dot.configure(text_color=DANGER) if self._alive() else None)
        finally:
            if self._alive():
                self.after(0, lambda: self._refresh_btn.configure(state="normal") if self._alive() else None)

    def _set_status(self, text: str, color: str = TEXT):
        if self._alive():
            self.after(0, lambda: self._status.configure(text=text, text_color=color) if self._alive() else None)

    def _set_dot(self, color: str):
        if self._alive():
            self.after(0, lambda: self._dot.configure(text_color=color) if self._alive() else None)

    def _set_progress(self, value: float, color: Optional[str] = None):
        c = color or _pct_color(value)
        def _apply():
            if not self._alive():
                return
            self._bar.set(value)
            self._bar.configure(progress_color=c)
            self._bar.pack(fill="x", padx=10, pady=(0, 8))
        if self._alive():
            self.after(0, _apply)

    def _hide_progress(self):
        if self._alive():
            self.after(0, lambda: self._bar.pack_forget() if self._alive() else None)

    def _stamp(self): pass

class _ElevenLabsFetch:
    def _fetch(self):
        api_key = get_auth("elevenlabs", "api_key")
        if not api_key:
            self._set_status("No API key — set it in Settings → TTS → ElevenLabs.", MUTED)
            self._set_dot(MUTED); self._hide_progress(); return
        resp = requests.get(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": api_key}, timeout=10,
        )
        if resp.status_code == 401:
            self._set_status("Invalid API key (401 Unauthorized).", DANGER)
            self._set_dot(DANGER); self._hide_progress(); return
        resp.raise_for_status()
        data      = resp.json()
        used      = data.get("character_count", 0)
        limit     = data.get("character_limit", 0)
        tier      = data.get("tier", "unknown").replace("_", " ").title()
        remaining = max(0, limit - used)
        pct       = used / limit if limit > 0 else 0.0
        color     = _pct_color(pct)
        self._set_status(
            f"{tier}  ·  {remaining:,} / {limit:,} chars remaining  ({(1-pct)*100:.0f}% left)",
            color if pct >= 0.60 else TEXT,
        )
        self._set_dot(color)
        self._set_progress(pct)

    def _set_dot(self, color: str): pass


class _OpenAIFetch:
    def _fetch(self):
        api_key = get_auth("openai", "token")
        if not api_key:
            self._set_status("No API key — set it in Settings → LLM/TTS → OpenAI.", MUTED)
            self._set_dot(MUTED); self._hide_progress(); return
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        resp = requests.get(
            "https://api.openai.com/dashboard/billing/credit_grants",
            headers=headers, timeout=10,
        )
        if resp.status_code in (401, 403):
            self._set_status("Invalid / restricted key — needs 'Billing Read' scope.", DANGER)
            self._set_dot(DANGER); self._hide_progress(); return
        if resp.status_code == 404:
            self._fetch_usage_fallback(headers); return
        resp.raise_for_status()
        data            = resp.json()
        total_granted   = data.get("total_granted",   0.0)
        total_used      = data.get("total_used",      0.0)
        total_available = data.get("total_available", 0.0)
        if total_granted <= 0 and total_available <= 0:
            self._fetch_usage_fallback(headers); return
        pct = total_used / total_granted if total_granted > 0 else 0.0
        self._set_status(
            f"Remaining: {_fmt_credits(total_available)}  of  {_fmt_credits(total_granted)}  granted",
            _pct_color(pct) if pct >= 0.60 else TEXT,
        )
        self._set_dot(_pct_color(pct))
        self._set_progress(pct)

    def _fetch_usage_fallback(self, headers: dict):
        import datetime
        today = datetime.date.today()
        r = requests.get(
            "https://api.openai.com/dashboard/billing/usage",
            headers=headers,
            params={"start_date": today.replace(day=1).isoformat(), "end_date": today.isoformat()},
            timeout=10,
        )
        if r.status_code != 200:
            self._set_status("Could not retrieve billing data — check key permissions.", MUTED)
            self._set_dot(MUTED); self._hide_progress(); return
        dollars = r.json().get("total_usage", 0) / 100.0
        self._set_status(f"Pay-as-you-go  ·  Spent this month: {_fmt_credits(dollars)}", TEXT)
        self._set_dot(SUCCESS); self._hide_progress()

    def _set_dot(self, color: str): pass


class _GeminiFetch:
    def _fetch(self):
        api_key = get_auth("google", "token")
        if not api_key:
            self._set_status("No API key — set it in Settings → LLM → Gemini.", MUTED)
            self._set_dot(MUTED); self._hide_progress(); return
        resp = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key}, timeout=10,
        )
        if resp.status_code == 400:
            self._set_status("Invalid API key (400).", DANGER); self._set_dot(DANGER)
        elif resp.status_code == 403:
            self._set_status("Key lacks permission or quota exhausted (403).", DANGER)
            self._set_dot(DANGER)
        elif resp.status_code == 200:
            self._set_status("API key valid ✓  ·  Use Console for quota details.", SUCCESS)
            self._set_dot(SUCCESS)
        else:
            self._set_status(f"Unexpected response: {resp.status_code}", MUTED)
            self._set_dot(MUTED)
        self._hide_progress()

    def _set_dot(self, color: str): pass


class _ClaudeFetch:
    def _fetch(self):
        api_key = get_auth("anthropic", "token")
        if not api_key:
            self._set_status("No API key — set it in Settings → LLM → Claude.", MUTED)
            self._set_dot(MUTED); self._hide_progress(); return
        resp = requests.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=10,
        )
        if resp.status_code == 401:
            self._set_status("Invalid API key (401).", DANGER); self._set_dot(DANGER)
        elif resp.status_code == 403:
            self._set_status("Key lacks permission (403).", DANGER); self._set_dot(DANGER)
        elif resp.status_code == 200:
            self._set_status("API key valid ✓  ·  Use Console to check balance.", SUCCESS)
            self._set_dot(SUCCESS)
        else:
            self._set_status(f"Unexpected response: {resp.status_code}", MUTED)
            self._set_dot(MUTED)
        self._hide_progress()

    def _set_dot(self, color: str): pass


class _GrokFetch:
    def _fetch(self):
        api_key = get_auth("xai", "token")
        if not api_key:
            self._set_status("No API key — set it in Settings → LLM → Grok.", MUTED)
            self._set_dot(MUTED); self._hide_progress(); return
        resp = requests.get(
            "https://api.x.ai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if resp.status_code == 401:
            self._set_status("Invalid API key (401).", DANGER); self._set_dot(DANGER)
        elif resp.status_code == 403:
            self._set_status("Key lacks permission (403).", DANGER); self._set_dot(DANGER)
        elif resp.status_code == 200:
            self._set_status("API key valid ✓  ·  Use Console to check balance.", SUCCESS)
            self._set_dot(SUCCESS)
        else:
            self._set_status(f"Unexpected response: {resp.status_code}", MUTED)
            self._set_dot(MUTED)
        self._hide_progress()

    def _set_dot(self, color: str): pass


class _FishSpeechFetch:
    def _fetch(self):
        api_key = get_auth("fishspeech", "token")
        if not api_key:
            self._set_status("No token — set it in Settings → TTS → FishSpeech.", MUTED)
            self._set_dot(MUTED); self._hide_progress(); return
        self._set_status("Token configured ✓  ·  Manage usage at fish.audio.", SUCCESS)
        self._set_dot(SUCCESS); self._hide_progress()

    def _set_dot(self, color: str): pass

class ElevenLabsCard(_ElevenLabsFetch, _BaseCard):
    TITLE = "🎙  ElevenLabs  —  TTS Credits"
    ACCENT = "#2d4a7a"
    def __init__(self, master): _BaseCard.__init__(self, master)

class OpenAICard(_OpenAIFetch, _BaseCard):
    TITLE = "🤖  OpenAI  —  Billing Balance"
    ACCENT = "#1a6b4a"
    def __init__(self, master): _BaseCard.__init__(self, master)

class GeminiCard(_GeminiFetch, _BaseCard):
    TITLE = "✨  Gemini  —  API Quota"
    ACCENT = "#4a3080"
    def __init__(self, master):
        _BaseCard.__init__(self, master)
        ctk.CTkButton(
            self, text="Open Google Cloud Console →",
            height=26, font=ctk.CTkFont(size=11),
            fg_color="#4a3080", hover_color="#6040a0", corner_radius=6,
            command=lambda: webbrowser.open(
                "https://console.cloud.google.com/apis/api/"
                "generativelanguage.googleapis.com/quotas"
            ),
        ).pack(fill="x", padx=14, pady=(0, 12))

class ClaudeCard(_ClaudeFetch, _BaseCard):
    TITLE = "🧠  Claude  —  API Status"
    ACCENT = "#7c3d1a"
    def __init__(self, master):
        _BaseCard.__init__(self, master)
        ctk.CTkButton(
            self, text="Open Anthropic Console →",
            height=26, font=ctk.CTkFont(size=11),
            fg_color="#7c3d1a", hover_color="#a0521f", corner_radius=6,
            command=lambda: webbrowser.open(
                "https://console.anthropic.com/settings/billing"
            ),
        ).pack(fill="x", padx=14, pady=(0, 12))

class GrokCard(_GrokFetch, _BaseCard):
    TITLE = "⚡  Grok  —  API Status"
    ACCENT = "#1a3a5c"
    def __init__(self, master):
        _BaseCard.__init__(self, master)
        ctk.CTkButton(
            self, text="Open xAI Console →",
            height=26, font=ctk.CTkFont(size=11),
            fg_color="#1a3a5c", hover_color="#204d7a", corner_radius=6,
            command=lambda: webbrowser.open("https://console.x.ai/"),
        ).pack(fill="x", padx=14, pady=(0, 12))

class MiniElevenLabsCard(_ElevenLabsFetch, _MiniCard):
    TITLE       = "🎙  ElevenLabs"
    ACCENT      = "#2d4a7a"
    CONSOLE_URL = "https://elevenlabs.io/subscription"
    def __init__(self, master, role_tag="TTS"): _MiniCard.__init__(self, master, role_tag)

class MiniOpenAICard(_OpenAIFetch, _MiniCard):
    TITLE       = "🤖  OpenAI"
    ACCENT      = "#1a6b4a"
    CONSOLE_URL = "https://platform.openai.com/usage"
    def __init__(self, master, role_tag=""): _MiniCard.__init__(self, master, role_tag)

class MiniGeminiCard(_GeminiFetch, _MiniCard):
    TITLE       = "✨  Gemini"
    ACCENT      = "#4a3080"
    CONSOLE_URL = (
        "https://console.cloud.google.com/apis/api/"
        "generativelanguage.googleapis.com/quotas"
    )
    def __init__(self, master, role_tag="LLM"): _MiniCard.__init__(self, master, role_tag)

class MiniClaudeCard(_ClaudeFetch, _MiniCard):
    TITLE       = "🧠  Claude"
    ACCENT      = "#7c3d1a"
    CONSOLE_URL = "https://console.anthropic.com/settings/billing"
    def __init__(self, master, role_tag="LLM"): _MiniCard.__init__(self, master, role_tag)

class MiniGrokCard(_GrokFetch, _MiniCard):
    TITLE       = "⚡  Grok"
    ACCENT      = "#1a3a5c"
    CONSOLE_URL = "https://console.x.ai/"
    def __init__(self, master, role_tag="LLM"): _MiniCard.__init__(self, master, role_tag)

class MiniFishSpeechCard(_FishSpeechFetch, _MiniCard):
    TITLE       = "🐟  FishSpeech"
    ACCENT      = "#2a5a3a"
    CONSOLE_URL = "https://fish.audio/"
    def __init__(self, master, role_tag="TTS"): _MiniCard.__init__(self, master, role_tag)

_MINI_LLM_MAP: dict[str, type] = {
    "openai": MiniOpenAICard,
    "claude": MiniClaudeCard,
    "gemini": MiniGeminiCard,
    "grok":   MiniGrokCard,
}

_MINI_TTS_MAP: dict[str, type] = {
    "elevenlabs": MiniElevenLabsCard,
    "openai":     MiniOpenAICard,
    "fishspeech": MiniFishSpeechCard,
}

class ActiveProviderStatusBar(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._inner: Optional[ctk.CTkFrame] = None
        self._build()

    def refresh(self):
        if self._inner:
            self._inner.destroy()
            self._inner = None
        self._build()

    def _build(self):
        llm_raw = (get_settings("chat_model") or "").strip().lower()
        tts_raw = (get_settings("tts_model")  or "").strip().lower()

        llm_prop = llm_raw in PROPRIETARY_LLM
        tts_prop = tts_raw in PROPRIETARY_TTS

        if not llm_prop and not tts_prop:
            self.pack_forget()
            return
        self.pack_configure()

        self._inner = ctk.CTkFrame(self, fg_color="transparent")
        self._inner.pack(fill="x")

        ctk.CTkLabel(
            self._inner,
            text="Active Service Status",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=MUTED,
            anchor="w",
        ).pack(anchor="w", pady=(0, 4))
        same = llm_prop and tts_prop and (llm_raw == tts_raw)

        if llm_prop:
            cls = _MINI_LLM_MAP.get(llm_raw)
            if cls:
                cls(self._inner, role_tag="LLM" if same else "").pack(
                    fill="x", pady=(0, 4)
                )

        if tts_prop:
            cls = _MINI_TTS_MAP.get(tts_raw)
            if cls:
                cls(self._inner, role_tag="TTS" if same else "").pack(
                    fill="x", pady=(0, 4)
                )

class QuotaIndicatorsPanel(ctk.CTkFrame):
    ALL_PROVIDERS = ["elevenlabs", "openai", "gemini", "claude", "grok"]

    def __init__(self, master, providers: Optional[list[str]] = None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        providers = [p.lower() for p in (providers or self.ALL_PROVIDERS)]

        ctk.CTkLabel(
            self,
            text="Service Quota Status",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=TEXT,
            anchor="w",
        ).pack(anchor="w", padx=4, pady=(0, 8))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x")

        CARD_MAP: dict[str, type] = {
            "elevenlabs": ElevenLabsCard,
            "openai":     OpenAICard,
            "gemini":     GeminiCard,
            "claude":     ClaudeCard,
            "grok":       GrokCard,
        }

        shown = [p for p in providers if p in CARD_MAP]
        for i, key in enumerate(shown):
            card = CARD_MAP[key](row)
            card.grid(
                row=0, column=i, sticky="nsew",
                padx=(0, 10 if i < len(shown) - 1 else 0),
            )
            row.grid_columnconfigure(i, weight=1)

    def refresh_all(self):
        for widget in self.winfo_children():
            if hasattr(widget, "_refresh"):
                widget._refresh()

if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.title("Quota Indicators — Preview")
    root.geometry("1400x360")
    root.configure(fg_color=APP_BG)

    # Full panel
    QuotaIndicatorsPanel(root).pack(fill="x", padx=20, pady=(20, 10))

    # Mini bar simulation
    ctk.CTkLabel(
        root, text="— Sidebar mini-bar preview (OpenAI LLM · ElevenLabs TTS) —",
        text_color=MUTED, font=ctk.CTkFont(size=10),
    ).pack()
    sidebar = ctk.CTkFrame(root, fg_color="#111820", width=340, corner_radius=10)
    sidebar.pack(padx=20, pady=(4, 20), anchor="w")
    sidebar.pack_propagate(False)
    MiniOpenAICard(sidebar,     role_tag="LLM").pack(fill="x", padx=10, pady=(10, 4))
    MiniElevenLabsCard(sidebar, role_tag="TTS").pack(fill="x", padx=10, pady=(0, 10))

    root.mainloop()