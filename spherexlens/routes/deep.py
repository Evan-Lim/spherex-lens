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
    if os.path.isdir(_dir()):
        for fn in sorted(os.listdir(_dir())):
            if not fn.endswith(".json"):
                continue
            try:
                d = _load(os.path.join(_dir(), fn))
            except (OSError, ValueError):
                continue
            out.append({k: d.get(k) for k in (
                "name", "ra", "dec", "n_exposures_used", "n_analysed", "n_variable",
                "n_periodic", "n_moving", "generated_utc", "pipeline")})
    return {"fields": out}


@router.get("/api/deep/{name}")
def get_deep(name: str, request: Request):
    DEEP_LIMIT.check(request)
    if not _NAME.fullmatch(name):
        raise HTTPException(status_code=400, detail="invalid deep-field name")
    path = os.path.join(_dir(), f"{name}.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404,
                            detail=f"No deep-field result named '{name}'.")
    try:
        return _load(path)
    except (OSError, ValueError):
        raise HTTPException(status_code=503, detail="Deep-field result unreadable.")
