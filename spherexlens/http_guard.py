"""
spherexlens.http_guard
Small request guards shared by the Phase 9 routes: the visitor key and a
per-visitor sliding-window rate limit.

The routers in spherexlens/routes/ must not import app.py at import time
(app.py imports them), so they cannot use app.py's slowapi limiter decorator.
This limiter is the same idea -- in-memory, per instance -- keyed by the same
visitor address app._client_key uses.  It only damps abuse; the expensive
paths have hard concurrency limits as well.
"""
import hashlib
import os
import threading
import time
from collections import deque

from fastapi import HTTPException


def client_key(request):
    """The visitor's address as app._client_key derives it (Netlify / Cloud Run aware)."""
    try:
        from app import _client_key           # lazy: app imports the routers
        return _client_key(request)
    except Exception:
        pass
    headers = getattr(request, "headers", {}) or {}
    netlify_ip = headers.get("x-nf-client-connection-ip", "").strip()
    if netlify_ip:
        return netlify_ip
    hops = [h.strip() for h in headers.get("x-forwarded-for", "").split(",") if h.strip()]
    if hops:
        return hops[-1]
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def hashed_client(request_or_key, salt=None):
    """A salted SHA-256 of the visitor key (never store the raw address)."""
    key = (request_or_key if isinstance(request_or_key, str)
           else client_key(request_or_key))
    salt = salt if salt is not None else os.environ.get("SPHEREX_CLIENT_SALT", "")
    return hashlib.sha256(f"{salt}|{key}".encode("utf-8")).hexdigest()[:24]


class RateLimiter:
    """``limit`` calls per ``window_s`` per key, in memory."""

    def __init__(self, limit, window_s=60.0, max_keys=20000):
        self.limit = int(limit)
        self.window_s = float(window_s)
        self.max_keys = max_keys
        self._hits = {}
        self._lock = threading.Lock()

    def hit(self, key, now=None):
        """Record one call; returns seconds to wait (0 when allowed)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._hits) > self.max_keys:          # bound memory
                cutoff = now - self.window_s
                for k in [k for k, q in self._hits.items() if not q or q[-1] < cutoff]:
                    del self._hits[k]
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1, int(hits[0] + self.window_s - now) + 1)
            hits.append(now)
            return 0

    def check(self, request):
        """Raise HTTP 429 (with Retry-After) when the visitor is over the limit."""
        wait = self.hit(client_key(request))
        if wait:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {self.limit} per "
                       f"{int(self.window_s)} s. Try again shortly.",
                headers={"Retry-After": str(wait)})

    def reset(self):
        with self._lock:
            self._hits.clear()
