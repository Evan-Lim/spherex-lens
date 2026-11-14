"""
Queued "Open any field" runs (the synchronous /api/field and /api/field/stream
stay as they are):

    POST /api/jobs/field   body {"ra","dec","name"?,"size"?,"slots"?,"epochs"?,"catalogs"?}
                           -> 202 {"id","state":"queued","status_url"}
    GET  /api/jobs/{id}    -> {"id","state","progress","result"?,"error"?,...}

A succeeded job's ``result`` is exactly what GET /api/field returns.
"""
import re

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from spherexlens import jobs
from spherexlens.http_guard import RateLimiter, client_key, hashed_client
from spherexlens.logging_setup import logged_route

router = APIRouter()

SUBMIT_LIMIT = RateLimiter(5, 60.0)
STATUS_LIMIT = RateLimiter(120, 60.0)


class FieldJobIn(BaseModel):
    ra: float
    dec: float
    name: str = Field("", max_length=80)
    size: float = 0.2
    slots: int = Field(0, ge=0, le=64)
    epochs: str = Field("firstlast", pattern="^(firstlast|all)$")
    catalogs: int = Field(1, ge=0, le=1)


def _run_field(params):
    """The worker body: the same pipeline as GET /api/field."""
    def run(report):
        import app
        token = app._PROGRESS_CALLBACK.set(report)
        acquired = app.FIELD_REQUEST_LIMIT.acquire(timeout=600)
        if not acquired:
            return {"_job_error": True,
                    "error": "The field loader stayed busy; try again later."}
        try:
            result = app._register_field(
                params["ra"], params["dec"], params["name"], params["size"],
                params["slots"], params["epochs"], params["catalogs"])
        finally:
            app.FIELD_REQUEST_LIMIT.release()
            app._PROGRESS_CALLBACK.reset(token)
        if isinstance(result, dict):
            return result
        try:                                   # a JSONResponse error
            import json
            body = json.loads(result.body.decode("utf-8"))
        except Exception:
            body = {"error": "The field run failed."}
        return {"_job_error": True, "status": getattr(result, "status_code", 500), **body}
    return run


@router.post("/api/jobs/field", status_code=202)
@logged_route("/api/jobs/field")
def submit_field_job(body: FieldJobIn, request: Request):
    SUBMIT_LIMIT.check(request)
    import app
    error = app.field_input_error(body.ra, body.dec, body.name, body.size, body.slots)
    if error:
        raise HTTPException(status_code=422, detail=error)
    params = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    try:
        job = jobs.get_queue().submit(
            "field", _run_field(params), hashed_client(client_key(request)),
            params={k: params[k] for k in ("ra", "dec", "name", "size", "slots", "epochs")})
    except jobs.QueueFull as exc:
        raise HTTPException(status_code=429, detail=str(exc),
                            headers={"Retry-After": "60"})
    except jobs.BackendUnavailable as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    return JSONResponse(status_code=202, content={
        "id": job.id, "state": job.state, "status_url": f"/api/jobs/{job.id}"},
        headers={"Cache-Control": "no-store"})


@router.get("/api/jobs/{job_id}")
@logged_route("/api/jobs/{id}")
def job_status(request: Request, job_id: str = Path(..., max_length=32)):
    STATUS_LIMIT.check(request)
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(status_code=404, detail="Unknown job.")
    status = jobs.get_queue().status(job_id)
    if status is None:
        raise HTTPException(status_code=404,
                            detail="Unknown or expired job (jobs are kept in memory "
                                   "on the instance that accepted them).")
    return JSONResponse(status, headers={"Cache-Control": "no-store"})
