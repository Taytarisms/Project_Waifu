import threading
import webbrowser
import secrets
import urllib.parse
import urllib.request
import urllib.error
import json
import tkinter as tk
import customtkinter as ctk
from http.server import HTTPServer, BaseHTTPRequestHandler
from files.ui import theme

try:
    from files.system_setup.settings import save_auth, get_auth
except Exception:
    save_auth = get_auth = None

REDIRECT_URI   = "http://localhost:3000"
AUTH_URL       = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL      = "https://id.twitch.tv/oauth2/token"
VALIDATE_URL   = "https://id.twitch.tv/oauth2/validate"

BOT_SCOPES = (
    "user:read:chat "
    "user:write:chat "
    "user:bot"
)

CHANNEL_SCOPES = (
    "channel:bot "
    "channel:read:redemptions "
    "channel:read:subscriptions "
    "bits:read "
    "channel:read:polls "
    "channel:read:predictions "
    "channel:read:hype_train "
    "channel:read:goals"
)

SINGLE_ACCOUNT_SCOPES = (
    "user:read:chat "
    "user:write:chat "
    "user:bot "
    "channel:bot "
    "channel:read:redemptions "
    "channel:read:subscriptions "
    "bits:read "
    "channel:read:polls "
    "channel:read:predictions "
    "channel:read:hype_train "
    "channel:read:goals"
)

class RedirectHandler(BaseHTTPRequestHandler):
    captured_code  = None
    captured_error = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            RedirectHandler.captured_code  = params["code"][0]
            RedirectHandler.captured_error = None
            body = b"<h2>Authorized! You can close this tab and return to the app.</h2>"
        elif "error" in params:
            RedirectHandler.captured_code  = None
            RedirectHandler.captured_error = params.get("error_description", ["Unknown error"])[0]
            body = b"<h2>Authorization failed. You can close this tab.</h2>"
        else:
            body = b"<h2>Waiting...</h2>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence console output
        pass


def server_response(timeout: int = 120) -> tuple[str | None, str | None]:
    RedirectHandler.captured_code  = None
    RedirectHandler.captured_error = None

    server = HTTPServer(("localhost", 3000), RedirectHandler)
    server.timeout = timeout

    # Handle exactly one request then shut down
    server.handle_request()
    server.server_close()

    return RedirectHandler.captured_code, RedirectHandler.captured_error

def _build_auth_url(client_id: str, scopes: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id":     client_id,
        "redirect_uri":  REDIRECT_URI,
        "scope":         scopes,
        "state":         state,
        "force_verify":  "true",   # always show the account picker
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def _exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    data = urllib.parse.urlencode({
        "client_id":     client_id,
        "client_secret": client_secret,
        "code":          code,
        "grant_type":    "authorization_code",
        "redirect_uri":  REDIRECT_URI,
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _validate_token(access_token: str) -> dict:
    req = urllib.request.Request(VALIDATE_URL)
    req.add_header("Authorization", f"OAuth {access_token}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())

class TwitchAuthDialog(ctk.CTkToplevel):
    def __init__(self, master, on_complete=None):
        super().__init__(master)
        self.title("Twitch Authentication")
        self.resizable(False, False)
        self.grab_set()
        self.geometry("560x460")
        self._on_complete = on_complete
        self._client_id     = ctk.StringVar(value=get_auth("twitch", "client_id") if get_auth else "")
        self._client_secret = ctk.StringVar(value=get_auth("twitch", "client_secret") if get_auth else "")
        self._state         = secrets.token_hex(16)
        self._bot_access    = ""
        self._bot_refresh   = ""
        self._bot_id        = ""
        self._bot_login     = ""
        self._chan_access    = ""
        self._chan_refresh   = ""
        self._owner_id      = ""
        self._owner_login   = ""
        self._closed        = False
        self._container = ctk.CTkFrame(self, fg_color="transparent")
        self._auth_mode = ctk.StringVar(value="single")
        self._container.pack(fill="both", expand=True, padx=20, pady=20)
        self.protocol("WM_DELETE_WINDOW", self._finish)
        self._show_page_credentials()

    def _alive(self):
        if self._closed:
            return False
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    def _widget_alive(self, widget):
        if not self._alive() or widget is None:
            return False
        try:
            return bool(widget.winfo_exists())
        except tk.TclError:
            return False

    def _safe_after(self, delay_ms, callback, *args, **kwargs):
        def run_if_alive():
            if not self._alive():
                return
            try:
                callback(*args, **kwargs)
            except tk.TclError:
                return

        try:
            self.after(delay_ms, run_if_alive)
        except tk.TclError:
            return

    def _configure_if_alive(self, widget, **kwargs):
        if not self._widget_alive(widget):
            return
        try:
            widget.configure(**kwargs)
        except tk.TclError:
            return

    def _configure_later(self, widget, **kwargs):
        self._safe_after(0, self._configure_if_alive, widget, **kwargs)

    def _clear(self):
        for w in self._container.winfo_children():
            w.destroy()

    def _title(self, text: str):
        ctk.CTkLabel(
            self._container, text=text,
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", pady=(0, 12))

    def _status(self, text: str, color=None):
        lbl = ctk.CTkLabel(
            self._container, text=text,
            font=ctk.CTkFont(size=11),
            text_color=color or theme.MUTED_TEXT,
            wraplength=420, justify="left",
        )
        lbl.pack(anchor="w", pady=(6, 0))
        return lbl

    def _show_page_credentials(self):
        self._clear()
        self._title("Step 1 — App credentials")

        ctk.CTkLabel(
            self._container,
            text=(
                "Twitch Developer Console (https://dev.twitch.tv/console).\n"
                "You should choose Single Account Mode.\n"
                "Unless you have a Bot account, then use it."
            ),
            font=ctk.CTkFont(size=12),
            text_color=theme.MUTED_TEXT,
            justify="left",
            wraplength=420,
        ).pack(anchor="w", pady=(0, 14))

        self._field(self._container, "Client ID", self._client_id)
        self._field(self._container, "Client Secret", self._client_secret, show="*")

        ctk.CTkLabel(
            self._container,
            text="Authorization Mode",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w", pady=(14, 4))

        ctk.CTkRadioButton(
            self._container,
            text="Single Account — streamer account is also the bot",
            variable=self._auth_mode,
            value="single",
            text_color=theme.TEXT,
        ).pack(anchor="w", pady=2)

        ctk.CTkRadioButton(
            self._container,
            text="Separate Bot Account — bot account + channel account",
            variable=self._auth_mode,
            value="separate",
            text_color=theme.TEXT,
        ).pack(anchor="w", pady=2)

        self._cred_status = self._status("")

        ctk.CTkButton(
            self._container,
            text="Next →",
            command=self._validate_credentials,
        ).pack(anchor="e", pady=(18, 0))

    def _field(self, parent, label: str, var: ctk.StringVar, show: str = ""):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=4)
        ctk.CTkLabel(row, text=label, width=110, anchor="w",
                     text_color=theme.TEXT).pack(side="left")
        ctk.CTkEntry(row, textvariable=var, show=show,
                     width=300).pack(side="left", padx=(8, 0))

    def _validate_credentials(self):
        cid = self._client_id.get().strip()
        sec = self._client_secret.get().strip()
        if not cid or not sec:
            self._cred_status.configure(
                text="Both Client ID and Client Secret are required.",
                text_color=theme.DANGER)
            return
        if save_auth:
            save_auth("twitch", "client_id", cid)
            save_auth("twitch", "client_secret", sec)
        if self._auth_mode.get() == "single":
            self._show_page_single_auth()
        else:
            self._show_page_bot_auth()

    def _show_page_single_auth(self):
        self._clear()
        self._title("Step 2 — Authorize your Twitch account")

        ctk.CTkLabel(
            self._container,
            text=(
                "Your browser will open a Twitch login page.\n"
                "Log into the streamer account. This account will read chat, send chat, "
                "and receive channel events like redemptions, subs, bits, polls, and predictions."
            ),
            font=ctk.CTkFont(size=12),
            text_color=theme.MUTED_TEXT,
            justify="left",
            wraplength=420,
        ).pack(anchor="w", pady=(0, 14))

        ctk.CTkLabel(
            self._container,
            text=(
                "Scopes: user:read:chat, user:write:chat, user:bot, channel:bot, "
                "channel:read:redemptions, channel:read:subscriptions, bits:read, "
                "channel:read:polls, channel:read:predictions, channel:read:hype_train, "
                "channel:read:goals"
            ),
            font=ctk.CTkFont(size=11),
            text_color=theme.MUTED_TEXT,
            wraplength=420,
            justify="left",
        ).pack(anchor="w", pady=(0, 14))

        self._single_status = self._status("Waiting for browser…")

        ctk.CTkButton(
            self._container,
            text="Open Browser & Authorize",
            command=self._start_single_auth,
        ).pack(anchor="w", pady=(12, 0))

        ctk.CTkButton(
            self._container,
            text="← Back",
            fg_color="transparent",
            hover_color="#333333",
            command=self._show_page_credentials,
        ).pack(anchor="w", pady=(6, 0))


    def _start_single_auth(self):
        url = _build_auth_url(
            self._client_id.get().strip(),
            SINGLE_ACCOUNT_SCOPES,
            self._state,
        )
        webbrowser.open(url)

        self._configure_if_alive(
            self._single_status,
            text="Browser opened. Waiting for authorization…",
            text_color=theme.MUTED_TEXT,
        )
        threading.Thread(target=self._receive_single_code, daemon=True).start()

    def _receive_single_code(self):
        code, err = server_response()
        if err or not code:
            self._configure_later(
                self._single_status,
                text=f"Authorization failed: {err or 'no code received'}",
                text_color=theme.DANGER,
            )
            return

        try:
            tokens = _exchange_code(
                self._client_id.get().strip(),
                self._client_secret.get().strip(),
                code,
            )
            validate = _validate_token(tokens["access_token"])
            access = tokens["access_token"]
            refresh = tokens.get("refresh_token", "")
            user_id = str(validate["user_id"])
            login = validate["login"]
            self._bot_access = access
            self._bot_refresh = refresh
            self._bot_id = user_id
            self._bot_login = login
            self._chan_access = access
            self._chan_refresh = refresh
            self._owner_id = user_id
            self._owner_login = login
            self._safe_after(0, self._on_single_success)
        except Exception as e:
            self._configure_later(
                self._single_status,
                text=f"Token exchange failed: {e}",
                text_color=theme.DANGER,
            )

    def _on_single_success(self):
        if not self._widget_alive(self._single_status):
            return
        self._configure_if_alive(
            self._single_status,
            text=f"✓ Account authorized: {self._owner_login} (ID: {self._owner_id})",
            text_color=theme.SUCCESS,
        )
        self._save_all()
        self._safe_after(800, self._show_page_done)

    def _show_page_bot_auth(self):
        self._clear()
        self._title("Step 2 — Authorize Bot account")

        ctk.CTkLabel(
            self._container,
            text="Your browser will open a Twitch login page.\n"
                 "Log in and click Authorize.",
            font=ctk.CTkFont(size=12),
            text_color=theme.MUTED_TEXT,
            justify="left",
        ).pack(anchor="w", pady=(0, 14))

        ctk.CTkLabel(
            self._container,
            text="Scopes: user:read:chat  user:write:chat  user:bot",
            font=ctk.CTkFont(size=11),
            text_color=theme.MUTED_TEXT,
        ).pack(anchor="w", pady=(0, 14))

        self._bot_status = self._status("Waiting for browser…")

        ctk.CTkButton(
            self._container, text="Open Browser & Authorize",
            command=self._start_bot_auth,
        ).pack(anchor="w", pady=(12, 0))

        ctk.CTkButton(
            self._container, text="← Back",
            fg_color="transparent",
            hover_color="#333333",
            command=self._show_page_credentials,
        ).pack(anchor="w", pady=(6, 0))

    def _start_bot_auth(self):
        url = _build_auth_url(
            self._client_id.get().strip(), BOT_SCOPES, self._state)
        webbrowser.open(url)
        if not self._widget_alive(self._bot_status):
            return
        self._configure_if_alive(
            self._bot_status,
            text="Browser opened. Waiting for authorization…",
            text_color=theme.MUTED_TEXT)
        threading.Thread(target=self._receive_bot_code, daemon=True).start()

    def _receive_bot_code(self):
        code, err = server_response()
        if err or not code:
            self._configure_later(
                self._bot_status,
                text=f"Authorization failed: {err or 'no code received'}",
                text_color=theme.DANGER,
            )
            return
        try:
            tokens   = _exchange_code(
                self._client_id.get().strip(),
                self._client_secret.get().strip(),
                code)
            validate = _validate_token(tokens["access_token"])

            self._bot_access  = tokens["access_token"]
            self._bot_refresh = tokens.get("refresh_token", "")
            self._bot_id      = str(validate["user_id"])
            self._bot_login   = validate["login"]

            self._safe_after(0, self._on_bot_success)
        except Exception as e:
            self._configure_later(
                self._bot_status,
                text=f"Token exchange failed: {e}",
                text_color=theme.DANGER,
            )

    def _on_bot_success(self):
        if not self._widget_alive(self._bot_status):
            return
        self._configure_if_alive(
            self._bot_status,
            text=f"✓ Bot account authorized: {self._bot_login} (ID: {self._bot_id})",
            text_color=theme.SUCCESS)
        self._safe_after(800, self._show_page_channel_auth)

    def _show_page_channel_auth(self):
        self._clear()
        self._title("Step 3 — Authorize Channel account")

        ctk.CTkLabel(
            self._container,
            text="Your browser will open again.\n"
                 "This time, you'll be asked to authorize the bot, make sure all the scopes are correct.\n"
                 "and click Authorize. This grants the bot permission to join your chat.",
            font=ctk.CTkFont(size=12),
            text_color=theme.MUTED_TEXT,
            justify="left",
        ).pack(anchor="w", pady=(0, 14))

        ctk.CTkLabel(
            self._container,
            text=("Scopes: channel:bot, channel:read:redemptions, ""channel:read:subscriptions, bits:read, channel:read:polls, ""channel:read:predictions, channel:read:hype_train, channel:read:goals"),
            font=ctk.CTkFont(size=11),
            text_color=theme.MUTED_TEXT,
        ).pack(anchor="w", pady=(0, 14))

        self._chan_status = self._status("Waiting for browser…")

        ctk.CTkButton(
            self._container, text="Open Browser & Authorize",
            command=self._start_channel_auth,
        ).pack(anchor="w", pady=(12, 0))

    def _start_channel_auth(self):
        url = _build_auth_url(
            self._client_id.get().strip(), CHANNEL_SCOPES, self._state)
        webbrowser.open(url)
        if not self._widget_alive(self._chan_status):
            return
        self._configure_if_alive(
            self._chan_status,
            text="Browser opened. Waiting for authorization…",
            text_color=theme.MUTED_TEXT)
        threading.Thread(target=self._receive_channel_code, daemon=True).start()

    def _receive_channel_code(self):
        code, err = server_response()
        if err or not code:
            self._configure_later(
                self._chan_status,
                text=f"Authorization failed: {err or 'no code received'}",
                text_color=theme.DANGER,
            )
            return
        try:
            tokens   = _exchange_code(
                self._client_id.get().strip(),
                self._client_secret.get().strip(),
                code)
            validate = _validate_token(tokens["access_token"])

            self._chan_access  = tokens["access_token"]
            self._chan_refresh = tokens.get("refresh_token", "")
            self._owner_id    = str(validate["user_id"])
            self._owner_login = validate["login"]

            self._safe_after(0, self._on_channel_success)
        except Exception as e:
            self._configure_later(
                self._chan_status,
                text=f"Token exchange failed: {e}",
                text_color=theme.DANGER,
            )

    def _on_channel_success(self):
        if not self._widget_alive(self._chan_status):
            return
        self._configure_if_alive(
            self._chan_status,
            text=f"✓ Channel account authorized: {self._owner_login} (ID: {self._owner_id})",
            text_color=theme.SUCCESS)
        self._save_all()
        self._safe_after(800, self._show_page_done)

    def _save_all(self):
        if not save_auth:
            return

        save_auth("twitch", "access_token", self._bot_access)
        save_auth("twitch", "refresh_token", self._bot_refresh)
        save_auth("twitch", "bot_id", self._bot_id)

        save_auth("twitch", "channel_access_token", self._chan_access)
        save_auth("twitch", "channel_refresh_token", self._chan_refresh)
        save_auth("twitch", "owner_id", self._owner_id)
        save_auth("twitch", "channel_name", self._owner_login)

    def _show_page_done(self):
        self._clear()
        self._title("All done!")

        mode = (
            "Single Account Mode"
            if self._bot_id == self._owner_id
            else "Separate Bot Account Mode"
        )

        summary = (
            f"Mode: {mode}\n\n"
            f"Bot account:     {self._bot_login}  (ID: {self._bot_id})\n"
            f"Channel account: {self._owner_login}  (ID: {self._owner_id})\n\n"
            "All tokens and IDs have been saved.\n"
            "You can now start the chat reader."
        )

        ctk.CTkLabel(
            self._container,
            text=summary,
            font=ctk.CTkFont(size=12),
            text_color=theme.TEXT,
            justify="left",
            wraplength=420,
        ).pack(anchor="w", pady=(0, 20))

        ctk.CTkButton(
            self._container,
            text="Close",
            command=self._finish,
        ).pack(anchor="e")

    def _finish(self):
        if self._closed:
            return
        self._closed = True
        if self._on_complete:
            self._on_complete()
        try:
            self.destroy()
        except tk.TclError:
            pass
