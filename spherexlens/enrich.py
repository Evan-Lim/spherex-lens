"""
spherexlens.enrich
The single hook through which v2 subsystems add information to a dashboard.

app.get_dashboard builds its payload, then calls ``run_enrichers(payload, ctx)``.
Each enricher is a function ``fn(payload, ctx) -> None`` that adds fields in
place (see CONTRACT.md for which fields belong to whom).  Enrichers run in
``order``; one failing never breaks the dashboard -- its error is recorded in
``payload["enrichment"][name] = {"status": "error", "error": ...}`` so the UI
can say "asteroid check unavailable" instead of silently passing.

Enricher modules register themselves on import; ENRICHER_MODULES lists the
modules to import.  A module missing from a worktree is simply skipped.
"""
import importlib
import time
import traceback

ENRICHER_MODULES = (
    "spherexlens.triage.asteroids",     # Phase 1.1  (order 10)
    "spherexlens.ingest.links",         # Phase 2    (order 20) WiseView etc.
    "spherexlens.ingest.coverage",      # Phase 2.2  (order 30)
    "spherexlens.ml.serve",             # Phase 5.8  (order 50)
    "spherexlens.triage.evidence",      # Phase 6    (order 60)
)

_REGISTRY = []      # (order, name, fn)
_LOADED = False


def register(name, order=100):
    """Decorator: ``@register("asteroids", order=10)``."""
    def deco(fn):
        _REGISTRY[:] = [r for r in _REGISTRY if r[1] != name]
        _REGISTRY.append((order, name, fn))
        _REGISTRY.sort(key=lambda r: (r[0], r[1]))
        return fn
    return deco


IMPORT_ERRORS = {}   # module -> "ExcType: message" for enrichers that failed to load


def load_enrichers():
    """Import every enricher module once.  A module that fails to import (bug,
    missing third-party dependency) is recorded and skipped -- it must never
    turn every dashboard into a 500."""
    global _LOADED
    if _LOADED:
        return
    for mod in ENRICHER_MODULES:
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError as e:
            if e.name and mod.startswith(e.name):
                continue            # subsystem not present in this build
            IMPORT_ERRORS[mod] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        except Exception as e:
            IMPORT_ERRORS[mod] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
    _LOADED = True


def registered():
    load_enrichers()
    return [name for _, name, _ in _REGISTRY]


def run_enrichers(payload, ctx, only=None):
    """Run every registered enricher on a dashboard payload, in order."""
    load_enrichers()
    report = payload.setdefault("enrichment", {})
    for mod, err in IMPORT_ERRORS.items():
        report[mod.rsplit(".", 1)[-1]] = {"status": "error",
                                          "error": f"failed to load: {err}"}
    for _, name, fn in list(_REGISTRY):
        if only is not None and name not in only:
            continue
        t0 = time.time()
        try:
            fn(payload, ctx)
            report[name] = {"status": "ok",
                            "seconds": round(time.time() - t0, 3)}
        except Exception as e:          # never break the dashboard
            report[name] = {"status": "error", "error": f"{type(e).__name__}: {e}",
                            "seconds": round(time.time() - t0, 3)}
            traceback.print_exc()
    return payload
