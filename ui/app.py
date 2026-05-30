from pathlib import Path
import atexit
import os
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import multiprocessing
import customtkinter as ctk

from files.ui import theme
from files.ui.components.navigation import TopNav
from files.ui.pages.landing_page import ConsolePage
from files.ui.pages.llms import LLMHomePage
from files.ui.pages.tts import TTSHomePage
from files.ui.pages.stt import STTPage
from files.ui.pages.twitch import TwitchPage
from files.ui.pages.memory import MemoryPage
from files.ui.pages.logs import LogsPage
from files.ui.pages.moderation import ModerationPage
from files.ui.pages.vtube_studio import VTubeStudioPage

from files.main_loop.main_loop import context_snapshot, start_loop

try:
    from files.system_setup.settings import load_settings
except Exception:
    load_settings = None

def _cleanup_backend() -> None:
    try:
        context_snapshot()
    except Exception:
        pass

    try:
        from files.speech_to_text import stt_controls
        stt_controls.stop_stt()
    except Exception:
        pass

    try:
        from files.main_loop import gen_response
        gen_response.interrupt()
    except Exception:
        pass

    try:
        from files.closed_captions import caption_coordinator
        caption_coordinator.cancel_pending()
    except Exception:
        pass

    try:
        from files.twitch_chat import twitchchat
        twitchchat.stop_read_chat()
    except Exception:
        pass

atexit.register(_cleanup_backend)


class WaifuCenter(ctk.CTk):
    def __init__(self):
        super().__init__()
        self._closing = False

        self.title("Project Waifu!")
        self.geometry("1180x760")
        self.configure(fg_color=theme.APP_BG)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.page_names = ["Console", "LLM", "TTS", "STT", "Twitch", "Memory", "Logs", "VTube Studio", "Moderation"]

        self.nav = TopNav(self, nav_callback=self.show_page, pages=self.page_names)
        self.nav.pack(fill="x")

        self.container = ctk.CTkFrame(self, fg_color=theme.APP_BG)
        self.container.pack(fill="both", expand=True)

        self.pages = {
            "Console": ConsolePage(self.container, self),
            "LLM": LLMHomePage(self.container, self),
            "TTS": TTSHomePage(self.container, self),
            "STT": STTPage(self.container, self),
            "Twitch": TwitchPage(self.container, self),
            "Memory": MemoryPage(self.container, self),
            "Logs": LogsPage(self.container, self),
            "VTube Studio": VTubeStudioPage(self.container, self),
            "Moderation": ModerationPage(self.container, self),
        }

        self.dynamic_page = None
        self.active_page_name = None

        self.show_page("Console")

    def show_page(self, page_name):
        if self.dynamic_page:
            self.dynamic_page.destroy()
            self.dynamic_page = None

        page = self.pages.get(page_name)
        if page:
            active_page = self.pages.get(self.active_page_name)
            if active_page and active_page is not page:
                active_page.place_forget()

            if hasattr(page, "refresh_active_provider"):
                page.refresh_active_provider()

            page.place(relx=0, rely=0, relwidth=1, relheight=1)
            page.lift()
            self.active_page_name = page_name
            self.nav.set_active(page_name)

    def show_dynamic_page(self, page_class, **kwargs):
        if self.dynamic_page:
            self.dynamic_page.destroy()

        active_page = self.pages.get(self.active_page_name)
        if active_page:
            active_page.place_forget()

        self.dynamic_page = page_class(self.container, self, **kwargs)
        self.dynamic_page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.dynamic_page.lift()

    def bootstrap_backend(self):
        if load_settings:
            load_settings()

        if start_loop:
            start_loop()

    def on_close(self):
        if self._closing:
            return
        self._closing = True
        _cleanup_backend()
        try:
            self.destroy()
        finally:
            os._exit(0)


def main():
    multiprocessing.freeze_support()
    ctk.set_appearance_mode("dark")

    app = WaifuCenter()
    app.bootstrap_backend()
    app.mainloop()


if __name__ == "__main__":
    main()
