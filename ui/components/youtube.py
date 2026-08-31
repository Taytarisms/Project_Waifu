import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser

import customtkinter as ctk
import requests

from files.system_setup.settings import get_auth, save_auth
from files.ui import theme

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"


class _OAuthCallback(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        type(self).result = {key: values[0] for key, values in query.items() if values}
        body = b"YouTube authorization received. You can close this window and return to AI Companion."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


class YouTubeAuthDialog(ctk.CTkToplevel):
    def __init__(self, master, on_complete=None):
        super().__init__(master)
        self.title("YouTube Broadcaster Authorization")
        self.geometry("620x430")
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self._on_complete = on_complete
        self._client_id = ctk.StringVar(value=get_auth("youtube", "client_id") or "")
        self._client_secret = ctk.StringVar(value=get_auth("youtube", "client_secret") or "")
        self._build()

    def _build(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=22, pady=20)
        ctk.CTkLabel(
            body,
            text="YouTube Broadcaster Account",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=theme.TEXT,
        ).pack(anchor="w")
        ctk.CTkLabel(
            body,
            text=(
                "Create a Desktop OAuth client in Google Cloud, enable YouTube Data API v3, "
                "then enter the client credentials below. Authorization opens in your browser "
                "and returns to a temporary localhost callback."
            ),
            wraplength=560,
            justify="left",
            text_color=theme.MUTED_TEXT,
        ).pack(anchor="w", pady=(6, 16))
        self._field(body, "Client ID", self._client_id)
        self._field(body, "Client Secret", self._client_secret, show="*")
        self._status = ctk.CTkLabel(
            body,
            text=self._saved_status(),
            wraplength=560,
            justify="left",
            text_color=theme.MUTED_TEXT,
        )
        self._status.pack(anchor="w", pady=(16, 8))
        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.pack(fill="x", pady=(8, 0))
        self._authorize_button = ctk.CTkButton(actions, text="Authorize with Google", command=self._authorize)
        self._authorize_button.pack(side="left")
        ctk.CTkButton(actions, text="Close", fg_color=theme.CARD_BG, command=self.destroy).pack(side="right")

    @staticmethod
    def _field(parent, label, variable, show=""):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=5)
        ctk.CTkLabel(row, text=label, width=110, anchor="w", text_color=theme.TEXT).pack(side="left")
        ctk.CTkEntry(row, textvariable=variable, show=show).pack(side="left", fill="x", expand=True)

    @staticmethod
    def _saved_status() -> str:
        name = get_auth("youtube", "channel_name") or ""
        return f"Connected account: {name}" if name else "No YouTube account is connected."

    def _authorize(self):
        client_id = self._client_id.get().strip()
        client_secret = self._client_secret.get().strip()
        if not client_id:
            self._status.configure(text="Client ID is required.", text_color=theme.DANGER)
            return
        save_auth("youtube", "client_id", client_id)
        save_auth("youtube", "client_secret", client_secret)
        self._authorize_button.configure(state="disabled")
        self._status.configure(text="Waiting for Google authorization…", text_color=theme.MUTED_TEXT)
        threading.Thread(
            target=self._authorize_worker,
            args=(client_id, client_secret),
            daemon=True,
        ).start()

    def _authorize_worker(self, client_id: str, client_secret: str):
        server = None
        try:
            state = secrets.token_urlsafe(24)
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            _OAuthCallback.result = {}
            server = http.server.ThreadingHTTPServer(("localhost", 0), _OAuthCallback)
            server.timeout = 180
            redirect_uri = f"http://localhost:{server.server_port}/"
            query = urllib.parse.urlencode({
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": READ_SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            })
            webbrowser.open(f"{AUTH_URL}?{query}")
            server.handle_request()
            result = dict(_OAuthCallback.result)
            if result.get("state") != state:
                raise RuntimeError("Google authorization state did not match")
            if result.get("error"):
                raise RuntimeError(result.get("error_description") or result["error"])
            code = result.get("code")
            if not code:
                raise RuntimeError("Authorization timed out or returned no code")
            token_data = {
                "client_id": client_id,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            }
            if client_secret:
                token_data["client_secret"] = client_secret
            response = requests.post(TOKEN_URL, data=token_data, timeout=30)
            response.raise_for_status()
            tokens = response.json()
            access_token = str(tokens.get("access_token") or "")
            if not access_token:
                raise RuntimeError("Google returned no access token")
            identity_response = requests.get(
                CHANNELS_URL,
                params={"part": "id,snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30,
            )
            identity_response.raise_for_status()
            items = identity_response.json().get("items") or []
            if not items:
                raise RuntimeError("The authorized Google account has no YouTube channel")
            channel = items[0]
            save_auth("youtube", "access_token", access_token)
            if tokens.get("refresh_token"):
                save_auth("youtube", "refresh_token", tokens["refresh_token"])
            save_auth("youtube", "token_expires_at", time.time() + int(tokens.get("expires_in") or 3600))
            save_auth("youtube", "channel_id", str(channel.get("id") or ""))
            save_auth("youtube", "channel_name", str(channel.get("snippet", {}).get("title") or ""))
            self.after(0, self._authorization_complete)
        except Exception as exc:
            self.after(0, self._authorization_failed, str(exc))
        finally:
            if server is not None:
                server.server_close()

    def _authorization_complete(self):
        self._authorize_button.configure(state="normal")
        self._status.configure(text=self._saved_status(), text_color=theme.SUCCESS)
        if self._on_complete:
            self._on_complete()

    def _authorization_failed(self, message: str):
        self._authorize_button.configure(state="normal")
        self._status.configure(text=f"Authorization failed: {message}", text_color=theme.DANGER)
