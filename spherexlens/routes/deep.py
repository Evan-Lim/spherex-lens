"""Deep-field results (Phase 7): GET /api/deep and /api/deep/{name}."""
import json
import os
import re

from fastapi import APIRouter, HTTPException

router = APIRouter()
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _dir():
    return os.environ.get("SPHEREX_DEEP_DIR", "deep")


@router.get("/api/deep")
def list_deep():
    out = []
    if os.path.isdir(_dir()):
        for fn in sorted(os.listdir(_dir())):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(_dir(), fn)) as f:
                    d = json.load(f)
            except (OSError, ValueError):
                continue
            out.append({k: d.get(k) for k in (
                "name", "ra", "dec", "n_exposures_used", "n_analysed", "n_variable",
                "n_periodic", "n_moving", "generated_utc", "pipeline")})
    return {"fields": out}


@router.get("/api/deep/{name}")
def get_deep(name: str):
    if not _NAME.match(name):
        raise HTTPException(status_code=400, detail="invalid deep-field name")
    path = os.path.join(_dir(), f"{name}.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404,
                            detail=f"No deep-field result '{name}'. Run "
                                   f"`python -m spherexlens.deep.run --name {name} ...`.")
    with open(path) as f:
        return json.load(f)
