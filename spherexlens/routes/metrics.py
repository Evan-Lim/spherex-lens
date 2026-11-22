"""
Evaluation and model endpoints (Phases 4.5 and 5.7):

    GET /api/metrics          eval/metrics.json (CONTRACT.md)
    GET /api/metrics/history  [{"pipeline_version","generated_utc","precision","recall",...}]
    GET /api/model            the latest real/bogus model card as JSON

Missing files give a 404 JSON with the command that creates them.  app is
never imported here (it imports this package).
"""
import json
import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _eval_dir():
    from spherexlens.ml import paths
    return paths.eval_dir()


@router.get("/api/metrics")
def get_metrics():
    m = _load(os.path.join(_eval_dir(), "metrics.json"))
    if m is None:
        return JSONResponse(status_code=404, content={
            "error": "No evaluation metrics yet.",
            "hint": "Run `python -m spherexlens.eval.run --cache data_cache` "
                    "to write eval/metrics.json."})
    return m


@router.get("/api/metrics/history")
def get_metrics_history():
    h = _load(os.path.join(_eval_dir(), "metrics_history.json"))
    return h if isinstance(h, list) else []


@router.get("/api/model")
def get_model():
    from spherexlens.ml import paths
    latest = _load(os.path.join(paths.model_dir(), "LATEST.json"))
    card = _load(os.path.join(paths.model_dir(), latest["json"])) if latest else None
    if card is None:
        return JSONResponse(status_code=404, content={
            "error": "No trained real/bogus model.",
            "hint": "Build labels with `python -m spherexlens.eval.run --cache data_cache "
                    "--labels ml/data/labels.parquet`, then "
                    "`python -m spherexlens.ml.train --labels ml/data/labels.parquet`.",
            "ml_status": "no_model"})
    md_path = os.path.join(os.path.dirname(paths.model_dir().rstrip("/\\")), "MODEL_CARD.md")
    try:
        with open(md_path) as f:
            card["model_card_markdown"] = f.read()
    except OSError:
        pass
    card.pop("medians", None)
    return card
