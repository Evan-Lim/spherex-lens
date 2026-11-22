"""
spherexlens -- the v2 subsystems of SPHEREx-Lens.

The original blueprint modules (step1_ingest ... step7_neowise, catalog_tools,
app) stay at the repository root so every documented command keeps working.
New subsystems live here:

    spherexlens.ingest    archive adapters (one shape for every archive)
    spherexlens.triage    extra triage layers (asteroids, multi-source evidence)
    spherexlens.eval      null tests, strict injection, answer key, metrics
    spherexlens.ml        features, labels, real/bogus + spectral models
    spherexlens.routes    extra FastAPI routers, auto-included by app.py
    spherexlens.enrich    the dashboard enricher hook

See spherexlens/CONTRACT.md for the JSON fields each subsystem adds.
"""
