import asyncio
import functools
import heapq
import itertools
import weakref
import time
from contextlib import asynccontextmanager

_runtimes = weakref.WeakKeyDictionary()


class NovelRuntime:
    def __init__(self):
        self.owner = None
        self.waiters = []
        self.sequence = itertools.count()
        self.http = None
        self.sessions = {}

    def advance(self):
        while self.waiters:
            _, _, task, ready = heapq.heappop(self.waiters)
            if ready.cancelled():
                continue
            self.owner = task
            ready.set_result(None)
            return
        self.owner = None

    @asynccontextmanager
    async def generation(self, priority=0):
        task = asyncio.current_task()
        if self.owner is task:
            yield  # Nested high/low route calls share the same reservation.
            return
        ready = asyncio.get_running_loop().create_future()
        heapq.heappush(self.waiters, (priority, next(self.sequence), task, ready))
        if self.owner is None:
            self.advance()
        try:
            await ready
            yield
        finally:
            ready.cancel()
            if self.owner is task:
                self.advance()

    def http_client(self):
        import httpx
        if self.http is None or self.http.is_closed:
            self.http = httpx.AsyncClient(timeout=60)
        return self.http

    def session(self, token, headers):
        from aiohttp import ClientSession
        session = self.sessions.get(token)
        if session is None or session.closed:
            session = self.sessions[token] = ClientSession(headers=headers)
        return session

    async def close(self):
        if self.http is not None:
            await self.http.aclose()
        for session in self.sessions.values():
            await session.close()
        self.sessions.clear()


def runtime():
    loop = asyncio.get_running_loop()
    if loop not in _runtimes:
        _runtimes[loop] = NovelRuntime()
    return _runtimes[loop]


async def close_runtime():
    current = _runtimes.pop(asyncio.get_running_loop(), None)
    if current is not None:
        await current.close()


def coordinated(function):
    @functools.wraps(function)
    async def wrapped(*args, request_priority=0, **kwargs):
        lane = runtime()
        nested = lane.owner is asyncio.current_task()
        started = time.perf_counter()
        async with lane.generation(request_priority):
            if not nested:
                from files.system_setup.system_logger import Logger
                label = "summary" if request_priority > 0 else "foreground"
                Logger.detail(f"NovelAI {label} queue wait: {time.perf_counter() - started:.3f}s")
            return await function(*args, **kwargs)
    return wrapped


def status_code(error):
    status = getattr(error, "status_code", None) or getattr(error, "status", None) or getattr(getattr(error, "response", None), "status_code", None)
    if status is not None:
        return status
    # novelai_api.NovelAIError stores (URL, HTTP status, message).
    return next((arg for arg in error.args if isinstance(arg, int) and 100 <= arg <= 599), None)
