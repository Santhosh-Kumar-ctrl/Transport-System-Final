"""Login throttling: counts failed attempts per email and per client IP in a sliding window.

State is in process memory, so with N worker processes the effective limit is N x the setting.
For multi-worker or multi-host deployments put a shared limiter (Redis, or the reverse proxy)
in front as well. Behind a proxy, run uvicorn with --proxy-headers and --forwarded-allow-ips
set to the proxy, otherwise every client shares the proxy's IP.
"""
import time
from collections import defaultdict, deque

from app.core.config import settings
from app.core.errors import TooManyRequests

_failures: dict[str, deque[float]] = defaultdict(deque)
_MAX_KEYS = 50_000


def _recent(key: str, now: float) -> deque[float]:
    q = _failures[key]
    cutoff = now - settings.login_lockout_seconds
    while q and q[0] <= cutoff:
        q.popleft()
    if not q:
        _failures.pop(key, None)
    return q


def _keys(email: str, ip: str) -> list[tuple[str, int]]:
    # IP limit is looser so one shared college NAT is not locked out by a few typos.
    return [(f"e:{email.strip().lower()}", settings.login_max_failures),
            (f"i:{ip}", settings.login_max_failures * 10)]


def check(email: str, ip: str) -> None:
    now = time.monotonic()
    for key, limit in _keys(email, ip):
        q = _recent(key, now)
        if len(q) >= limit:
            wait = max(1, int(q[0] + settings.login_lockout_seconds - now))
            raise TooManyRequests("Too many failed login attempts. Try again later.",
                                  extra={"retry_after": wait})


def record_failure(email: str, ip: str) -> None:
    now = time.monotonic()
    if len(_failures) > _MAX_KEYS:  # bound memory under a spray of random emails
        for k in list(_failures):
            _recent(k, now)
        if len(_failures) > _MAX_KEYS:
            _failures.clear()
    for key, _ in _keys(email, ip):
        _failures[key].append(now)


def record_success(email: str) -> None:
    _failures.pop(f"e:{email.strip().lower()}", None)


def reset() -> None:
    _failures.clear()
