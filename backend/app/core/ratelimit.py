"""In-memory sliding-window rate limits.

    login_limit = RateLimiter("login", limit=10, window_seconds=60)
    login_limit.hit(f"{ip}|{email}")          # raises TooManyRequests (429) once over the limit

Per process, like the WebSocket hub: correct because the API runs as a single worker.
"""

import time
from collections import deque

from app.core.errors import TooManyRequests

_all: list["RateLimiter"] = []


class RateLimiter:
    def __init__(self, name: str, *, limit: int, window_seconds: float, message: str | None = None):
        self.name = name
        self.limit = limit
        self.window = window_seconds
        self.message = message or "Too many attempts. Wait a moment and try again."
        self._hits: dict[str, deque[float]] = {}
        _all.append(self)

    def hit(self, key: str) -> None:
        now = time.monotonic()
        q = self._hits.setdefault(key, deque())
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            retry = max(1, round(q[0] + self.window - now))
            raise TooManyRequests(self.message, extra={"retry_after_seconds": retry})
        q.append(now)
        if len(self._hits) > 10_000:
            self._prune(now)

    def _prune(self, now: float) -> None:
        for key in [k for k, q in self._hits.items() if not q or q[-1] <= now - self.window]:
            del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()


def reset_all() -> None:
    """Forget every hit (tests)."""
    for limiter in _all:
        limiter.reset()
