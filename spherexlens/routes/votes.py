"""
Public voting on detections (spherexlens/CONTRACT.md):

    POST /api/votes          {"target","detection_id","vote":"real"|"bogus"|"unsure"}
    GET  /api/votes/queue?n=10   detections to vote on, most-uncertain first
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

import config
from spherexlens import votes as votes_store
from spherexlens.http_guard import RateLimiter, client_key
from spherexlens.logging_setup import logged_route
from spherexlens.routes import _detections

router = APIRouter()

VOTE_LIMIT = RateLimiter(config.Production.VOTE_RATE_PER_MIN, 60.0)
QUEUE_LIMIT = RateLimiter(60, 60.0)


class VoteIn(BaseModel):
    target: str = Field(..., max_length=votes_store.MAX_TARGET_LEN)
    detection_id: str = Field(..., max_length=votes_store.MAX_DETECTION_ID_LEN)
    vote: str = Field(..., max_length=16)


def _known_target(target):
    """Votes are accepted only for fields this deployment actually serves."""
    try:
        from step1_ingest import TARGETS
        if target in TARGETS:
            return True
    except Exception:
        pass
    import app
    if target in app.FIELD_REGISTRY:
        return True
    try:
        store = app._snapshot_store()
        manifest = app._optional_published_manifest(store) if store else None
    except Exception:
        manifest = None
    return target in ((manifest or {}).get("targets") or {})


@router.post("/api/votes")
@logged_route("/api/votes")
def post_vote(body: VoteIn, request: Request):
    VOTE_LIMIT.check(request)
    try:
        target, detection_id, vote = votes_store.validate(
            body.target, body.detection_id, body.vote)
    except votes_store.VoteError as error:
        raise HTTPException(status_code=400, detail=str(error))
    if not _known_target(target):
        raise HTTPException(status_code=400, detail="Unknown target.")
    try:
        votes_store.record_vote(target, detection_id, vote, client_key(request))
    except votes_store.VotesNotConfigured as error:
        raise HTTPException(status_code=503, detail=str(error))
    except votes_store.VoteError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        votes_store_logger().error("vote write failed: %s", type(error).__name__)
        raise HTTPException(status_code=503,
                            detail="The vote could not be stored; try again later.")
    return {"ok": True}


def votes_store_logger():
    from spherexlens.logging_setup import get_logger
    return get_logger("spherexlens.votes")


def _rank_fallback(rows):
    """Most uncertain first: calibrated P(real) nearest 0.5 when the ML layer
    scored the detection, otherwise SNR nearest the detection threshold."""
    sigma = float(config.DETECTION_SIGMA)

    def key(row):
        p = row.get("ml_real_bogus")
        if p is not None:
            return (0, abs(p - 0.5))
        snr = row.get("snr")
        return (1, abs(snr - sigma) if snr is not None else float("inf"))
    return sorted(rows, key=key)


def _rank(rows, n):
    try:
        from spherexlens.ml import active
        queue_fn = getattr(active, "uncertainty_queue")
    except Exception:
        queue_fn = None
    if queue_fn is not None:
        try:
            ranked = list(queue_fn([r["_detection"] for r in rows], n))
            by_id = {r["detection_id"]: r for r in rows}
            out = []
            for item in ranked:
                det_id = item.get("detection_id") if isinstance(item, dict) else item
                if det_id in by_id:
                    out.append(by_id[det_id])
            if out:
                return out[:n], "ml.active.uncertainty_queue"
        except Exception as error:
            votes_store_logger().warning(
                "uncertainty_queue failed, using SNR fallback: %s",
                type(error).__name__)
    return _rank_fallback(rows)[:n], "snr_near_threshold"


def _public(row, counts):
    det = row["_detection"]
    out = {k: v for k, v in row.items() if not k.startswith("_")}
    out.update({
        "pair": det.get("pair"),
        "position": det.get("position"),
        "reasons": det.get("reasons"),
        "links": det.get("links"),
        "votes": counts.get(row["detection_id"],
                            dict.fromkeys(votes_store.VALID_VOTES, 0)),
    })
    return out


@router.get("/api/votes/queue")
@logged_route("/api/votes/queue")
def votes_queue(request: Request, n: int = Query(10, ge=1, le=50),
                target: Optional[str] = Query(None, max_length=80)):
    QUEUE_LIMIT.check(request)
    data = _detections.all_detections()
    rows = [r for r in data["rows"]
            if not str(r.get("category") or "").startswith("KNOWN_")
            and votes_store.DETECTION_ID_RE.fullmatch(
                votes_store.normalise_detection_id(str(r["detection_id"])))]
    if target:
        rows = [r for r in rows if r["target"] == target]
    ranked, method = _rank(rows, n)
    try:
        counts = votes_store.tally()
    except Exception:
        counts = {}
    return {
        "n": len(ranked),
        "ranking": method,
        "source": data["source"],
        "generated_utc": data["generated_utc"],
        "detections": [_public(r, counts) for r in ranked],
    }
