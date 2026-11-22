"""
spherexlens.logging_setup
Structured JSON logs for the production routes.

Cloud Logging parses one JSON object per stdout line into a structured entry
(``severity`` and ``message`` are recognised), so every request handled by a
Phase 9 route is logged as::

    {"severity": "INFO", "message": "GET /api/votes/queue 200",
     "request_id": "...", "route": "/api/votes/queue", "method": "GET",
     "status": 200, "duration_ms": 12.3, "logger": "spherexlens.http"}

Use ``logged_route`` as a FastAPI route dependency-free wrapper::

    @router.get("/api/x")
    @logged_route("/api/x")
    def handler(request: Request): ...

Nothing here logs client IP addresses, tokens or request bodies.
"""
import functools
import inspect
import json
import logging
import sys
import time
import uuid

_CONFIGURED = {"done": False}
_LOGGER_NAME = "spherexlens.http"


class JsonFormatter(logging.Formatter):
    """One JSON object per line, the shape Cloud Logging understands."""

    def format(self, record):
        entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            entry.update(extra)
        if record.exc_info:
            # Exception type only; tracebacks can carry paths and parameters.
            entry["error_type"] = record.exc_info[0].__name__
        return json.dumps(entry, default=str, ensure_ascii=False)


def get_logger(name=_LOGGER_NAME):
    """A logger that writes JSON lines to stdout (configured once, idempotent)."""
    logger = logging.getLogger(name)
    if not _CONFIGURED["done"]:
        base = logging.getLogger("spherexlens")
        if not any(getattr(h, "_spherex_json", False) for h in base.handlers):
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(JsonFormatter())
            handler._spherex_json = True
            base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
        _CONFIGURED["done"] = True
    return logger


def request_id(request):
    """The caller's trace id when Cloud Run / a proxy sent one, else a new id."""
    if request is not None:
        headers = getattr(request, "headers", {}) or {}
        trace = headers.get("x-cloud-trace-context", "")
        if trace:
            return trace.split("/", 1)[0][:64]
        rid = headers.get("x-request-id", "")
        if rid and len(rid) <= 64 and rid.replace("-", "").isalnum():
            return rid
    return uuid.uuid4().hex


def log_request(route, method, status, started, rid, **fields):
    get_logger().info(
        "%s %s %s", method, route, status,
        extra={"fields": {"request_id": rid, "route": route, "method": method,
                          "status": status,
                          "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                          **fields}})


def _status_of(result):
    return getattr(result, "status_code", 200)


def logged_route(route):
    """Decorator: log request id, route, duration and status of a handler.

    Works for sync and async handlers and keeps the signature FastAPI reads.
    The handler must accept a ``request`` parameter (a starlette Request).
    """
    def wrap(func):
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def handler(*args, **kwargs):
                request = kwargs.get("request")
                rid, started, status = request_id(request), time.perf_counter(), 500
                try:
                    result = await func(*args, **kwargs)
                    status = _status_of(result)
                    return result
                except Exception as error:
                    status = getattr(error, "status_code", 500)
                    raise
                finally:
                    log_request(route, getattr(request, "method", "?"), status,
                                started, rid)
        else:
            @functools.wraps(func)
            def handler(*args, **kwargs):
                request = kwargs.get("request")
                rid, started, status = request_id(request), time.perf_counter(), 500
                try:
                    result = func(*args, **kwargs)
                    status = _status_of(result)
                    return result
                except Exception as error:
                    status = getattr(error, "status_code", 500)
                    raise
                finally:
                    log_request(route, getattr(request, "method", "?"), status,
                                started, rid)
        return handler
    return wrap
