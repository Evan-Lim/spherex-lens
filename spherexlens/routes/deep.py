"""Deep-field results (Phase 7): GET /api/deep and /api/deep/{name}.

Security review S21: names are matched with fullmatch, both routes are rate
limited, results are read through a small mtime-keyed cache instead of being
re-parsed on every request, and the 404 message does not reveal server-side
commands.
"""
import json
import os
import re
import threading

from fastapi import APIRouter, HTTPException, Request

from spherexlens.http_guard import RateLimiter

router = APIRouter()
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
DEEP_LIMIT = RateLimiter(60, 60.0)
_CACHE = {}                 # path -> (mtime, parsed json)
_LOCK = threading.Lock()


def _dir():
    return os.environ.get("SPHEREX_DEEP_DIR", "deep")


_BUCKET_TTL_S = 300.0
_BUCKET_CACHE = {}          # key -> (fetched_at, value)


def _store():
    """The snapshot bucket on the cloud (deep jobs publish deep/<name>.json
    there); None on a laptop, where results are local files."""
    if not os.environ.get("SPHEREX_SNAPSHOT_BUCKET"):
        return None
    try:
        from snapshot_store import SnapshotStore
        return SnapshotStore()
    except Exception:
        return None


def _bucket_cached(key, fetch):
    import time
    now = time.monotonic()
    with _LOCK:
        hit = _BUCKET_CACHE.get(key)
        if hit and now - hit[0] < _BUCKET_TTL_S:
            return hit[1]
    value = fetch()
    with _LOCK:
        _BUCKET_CACHE[key] = (now, value)
    return value


def _bucket_names(store):
    return sorted(b.name[len("deep/"):-len(".json")]
                  for b in store.bucket.list_blobs(prefix="deep/")
                  if b.name.endswith(".json"))


def _results():
    """{name: payload-or-loader} from the bucket (cloud) or the local dir."""
    store = _store()
    if store is not None:
        names = _bucket_cached("names", lambda: _bucket_names(store))
        return {n: (lambda n=n: _bucket_cached(
            n, lambda: store.read_json(f"deep/{n}.json"))) for n in names}
    out = {}
    if os.path.isdir(_dir()):
        for fn in sorted(os.listdir(_dir())):
            if fn.endswith(".json"):
                path = os.path.join(_dir(), fn)
                out[fn[:-5]] = (lambda path=path: _load(path))
    return out


def _load(path):
    mtime = os.path.getmtime(path)
    with _LOCK:
        hit = _CACHE.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
    with open(path) as f:
        data = json.load(f)
    with _LOCK:
        _CACHE[path] = (mtime, data)
    return data


@router.get("/api/deep")
def list_deep(request: Request):
    DEEP_LIMIT.check(request)
    out = []
    for _name, loader in _results().items():
        try:
            d = loader()
        except Exception:
            continue
        if d:
            out.append({k: d.get(k) for k in (
                "name", "ra", "dec", "n_exposures_used", "n_analysed", "n_variable",
                "n_periodic", "n_moving", "generated_utc", "pipeline")})
    return {"fields": out}


@router.get("/api/deep/{name}")
def get_deep(name: str, request: Request):
    DEEP_LIMIT.check(request)
    if not _NAME.fullmatch(name):
        raise HTTPException(status_code=400, detail="invalid deep-field name")
    loader = _results().get(name)
    if loader is None:
        raise HTTPException(status_code=404,
                            detail=f"No deep-field result named '{name}'.")
    try:
        data = loader()
    except Exception:
        raise HTTPException(status_code=503, detail="Deep-field result unreadable.")
    if data is None:
        raise HTTPException(status_code=404,
                            detail=f"No deep-field result named '{name}'.")
    return data
