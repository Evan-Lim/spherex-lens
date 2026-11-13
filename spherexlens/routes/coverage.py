"""
GET /api/coverage?ra=&dec=&radius=   (CONTRACT.md, owner: ingest)

Which archives hold data around a position:
    {"ra", "dec", "radius_deg", "coverage": [Coverage...], "pipeline": {...}}
See spherexlens.ingest.coverage for the time box and the cache.
"""
from fastapi import APIRouter, HTTPException, Query

import config

router = APIRouter()


@router.get("/api/coverage")
def api_coverage(ra: float = Query(..., description="RA, degrees (0-360)"),
                 dec: float = Query(..., description="Dec, degrees (-90..90)"),
                 radius: float = Query(None, description="cone radius, degrees")):
    # imported here, never at module import time (app imports this module)
    from spherexlens.ingest.coverage import coverage_report
    from version import pipeline_stamp

    radius = config.COVERAGE_RADIUS_DEG if radius is None else radius
    if not 0.0 <= ra <= 360.0:
        raise HTTPException(status_code=422, detail="ra must be in [0, 360] degrees")
    if not -90.0 <= dec <= 90.0:
        raise HTTPException(status_code=422, detail="dec must be in [-90, 90] degrees")
    if not 0.0 < radius <= config.COVERAGE_MAX_RADIUS_DEG:
        raise HTTPException(status_code=422, detail=(
            f"radius must be in (0, {config.COVERAGE_MAX_RADIUS_DEG:g}] degrees"))
    return {"ra": ra, "dec": dec, "radius_deg": radius,
            "coverage": coverage_report(ra % 360.0, dec, radius),
            "pipeline": pipeline_stamp()}
