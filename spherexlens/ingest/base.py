"""
spherexlens.ingest.base
The one shape every archive adapter has, plus the shared network behaviour
(retries with backoff, timeout, per-archive rate limit, response cache,
provenance record).

    class MyArchive(Adapter):
        name = "jpl_sb_ident"
        def coverage(self, ra, dec, radius_deg, t_start=None, t_end=None): ...
        def fetch(self, ra, dec, radius_deg, t_start=None, t_end=None): ...

coverage() must be cheap and return a Coverage; fetch() returns a Result whose
``rows`` is a list of plain dicts and whose ``provenance`` lists every request.

"Fail, don't fake": when an archive cannot be reached the adapter raises
ArchiveUnavailable.  Callers must surface that as "check unavailable" -- never
as an empty (and therefore success-shaped) result.
"""
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field

import requests

import config


class ArchiveUnavailable(RuntimeError):
    """The archive could not answer (network, 5xx, timeout, bad payload)."""


@dataclass
class Coverage:
    archive: str
    available: bool
    count: int = 0
    detail: str = ""
    error: str = ""

    def to_dict(self):
        return {"archive": self.archive, "available": self.available,
                "count": self.count, "detail": self.detail,
                **({"error": self.error} if self.error else {})}


@dataclass
class Result:
    archive: str
    rows: list = field(default_factory=list)
    provenance: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def provenance_record(archive, url, params=None, n_rows=None, body=b""):
    return {
        "archive": archive,
        "url": url,
        "params": params or {},
        "queried_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_rows": n_rows,
        "sha256": hashlib.sha256(body or b"").hexdigest()[:16],
    }


_RATE_LOCK = threading.Lock()
_LAST_CALL = {}


def _rate_limit(archive, min_interval_s):
    with _RATE_LOCK:
        now = time.monotonic()
        wait = _LAST_CALL.get(archive, 0) + min_interval_s - now
        _LAST_CALL[archive] = max(now, _LAST_CALL.get(archive, 0) + min_interval_s)
    if wait > 0:
        time.sleep(wait)


def cache_dir():
    root = os.environ.get("SPHEREX_CACHE", "data_cache")
    path = os.path.join(root, "archive_cache")
    os.makedirs(path, exist_ok=True)
    return path


class Adapter:
    name = "archive"
    min_interval_s = 0.5            # politeness: at most 2 requests/s
    cache_ttl_s = 7 * 86400         # responses reused for a week by default
    user_agent = "astro-archive-client/1.0"     # generic: no project details

    def __init__(self, session=None, use_cache=True):
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", self.user_agent)
        self.use_cache = use_cache and os.environ.get(
            "SPHEREX_ARCHIVE_CACHE", "true").lower() != "false"

    # -- to implement ------------------------------------------------------
    def coverage(self, ra, dec, radius_deg, t_start=None, t_end=None):
        raise NotImplementedError

    def fetch(self, ra, dec, radius_deg, t_start=None, t_end=None):
        raise NotImplementedError

    # -- shared HTTP -------------------------------------------------------
    def _cache_path(self, url, params, data):
        key = json.dumps([url, params, data], sort_keys=True, default=str)
        digest = hashlib.sha256(key.encode()).hexdigest()[:24]
        return os.path.join(cache_dir(), f"{self.name}_{digest}.bin")

    def http(self, url, params=None, data=None, method=None, expect_json=False,
             ttl_s=None):
        """GET/POST with retries, backoff, rate limit and an on-disk cache.

        Returns (body_bytes, provenance_dict) or raises ArchiveUnavailable.
        """
        ttl = self.cache_ttl_s if ttl_s is None else ttl_s
        path = self._cache_path(url, params, data)
        if self.use_cache and ttl > 0 and os.path.exists(path) \
                and time.time() - os.path.getmtime(path) < ttl:
            with open(path, "rb") as f:
                body = f.read()
            rec = provenance_record(self.name, url, params, body=body)
            rec["cached"] = True
            return body, rec

        method = method or ("POST" if data is not None else "GET")
        last = None
        for attempt in range(config.HTTP_RETRIES):
            _rate_limit(self.name, self.min_interval_s)
            try:
                r = self.session.request(method, url, params=params, data=data,
                                         timeout=config.HTTP_TIMEOUT_S)
                if r.status_code >= 500 or r.status_code == 429:
                    last = f"HTTP {r.status_code}"
                elif r.status_code >= 400:
                    raise ArchiveUnavailable(
                        f"{self.name}: HTTP {r.status_code}: {r.text[:200]}")
                else:
                    body = r.content
                    if expect_json:
                        try:
                            json.loads(body)
                        except ValueError:
                            raise ArchiveUnavailable(
                                f"{self.name}: response is not JSON: {body[:200]!r}")
                    if self.use_cache and ttl > 0:
                        tmp = path + ".tmp"
                        with open(tmp, "wb") as f:
                            f.write(body)
                        os.replace(tmp, path)
                    return body, provenance_record(self.name, r.url, params, body=body)
            except ArchiveUnavailable:
                raise
            except requests.RequestException as e:
                last = f"{type(e).__name__}: {e}"
            time.sleep(config.HTTP_BACKOFF_S * (2 ** attempt))
        raise ArchiveUnavailable(f"{self.name}: {last}")

    def http_json(self, url, params=None, data=None, ttl_s=None):
        body, rec = self.http(url, params=params, data=data, expect_json=True,
                              ttl_s=ttl_s)
        return json.loads(body), rec
