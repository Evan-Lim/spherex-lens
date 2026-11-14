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


def netlify_signature_ok(headers, secret=None, now=None):
    """Security review S8: is this request really from our Netlify proxy?

    netlify.toml marks the /live-api and /data-live-api proxies as *signed*
    with the Netlify environment variable SPHEREX_PROXY_SECRET.  Netlify then
    sends ``X-Nf-Sign``: an HS256 JWT signed with that secret (claims include
    ``iss: "netlify"``).  Only when it verifies do we trust Netlify's
    ``x-nf-client-connection-ip`` header -- otherwise anyone calling Cloud Run
    directly could forge it to dodge rate limits or stuff votes.

    Returns None when no secret is configured on this service (old behaviour),
    else True / False.
    """
    import base64
    import hashlib
    import hmac
    import json
    import os

    secret = secret if secret is not None else os.environ.get("SPHEREX_PROXY_SECRET", "")
    if not secret:
        return None
    token = (headers.get("x-nf-sign") or "").strip()
    parts = token.split(".")
    if len(parts) != 3:
        return False

    def b64(s):
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

    try:
        header = json.loads(b64(parts[0]))
        claims = json.loads(b64(parts[1]))
        sig = b64(parts[2])
    except Exception:
        return False
    if header.get("alg") != "HS256":
        return False
    expected = hmac.new(secret.encode(), f"{parts[0]}.{parts[1]}".encode(),
                        hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return False
    if claims.get("iss") != "netlify":
        return False
    exp = claims.get("exp")
    if exp is not None and float(exp) < (now if now is not None else time.time()):
        return False
    return True


def trusted_client_ip(headers, fallback):
    """Visitor address: Netlify's header only when the request is trusted."""
    netlify_ip = (headers.get("x-nf-client-connection-ip") or "").strip()
    if netlify_ip and netlify_signature_ok(headers) is not False:
        return netlify_ip
    hops = [h.strip() for h in (headers.get("x-forwarded-for") or "").split(",") if h.strip()]
    if hops:
        return hops[-1]          # appended by Cloud Run's front end: not forgeable
    return fallback


def client_key(request):
    """The visitor's address as app._client_key derives it (Netlify / Cloud Run aware)."""
    try:
        from app import _client_key           # lazy: app imports the routers
        return _client_key(request)
    except Exception:
        pass
    headers = getattr(request, "headers", {}) or {}
    client = getattr(request, "client", None)
    return trusted_client_ip(headers, getattr(client, "host", None) or "unknown")


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
