"""
config.py
Central settings for the pipeline.  Every tunable number lives here, so it can
be changed in one place and so version.config_hash() changes whenever a result
could change.

Older modules (catalog_tools, step4_triage, step7_neowise) still keep their own
documented heuristic constants at the top of each file; new code reads from
here.  Values can be overridden with an environment variable of the same name
prefixed SPHEREX_ (e.g. SPHEREX_DETECTION_SIGMA=4.5).
"""
import os


def _env(name, default, cast=float):
    raw = os.environ.get("SPHEREX_" + name)
    if raw is None or raw == "":
        return default
    try:
        return cast(raw)
    except ValueError:
        return default


# ---- grid / detector -------------------------------------------------------
PIXSCALE_ARCSEC = 3.0               # common tangent-plane grid
NATIVE_PIXEL_ARCSEC = 6.2           # SPHEREx native pixel
NPIX = 180                          # 9' field on the 3" grid
PSF_SIGMA_PX_FALLBACK = 1.4         # Gaussian used only when a file has no PSF

# ---- detection -------------------------------------------------------------
# Default significance cut.  Phase 4 (eval) replaces this with a cut chosen
# from the measured null-test false-alarm rate; see eval/threshold.json.
DETECTION_SIGMA = _env("DETECTION_SIGMA", 5.0)
TARGET_FALSE_ALARMS_PER_FIELD = _env("TARGET_FALSE_ALARMS_PER_FIELD", 0.1)
MERGE_RADIUS_PX = 2
MAX_TRIAGED_DETECTIONS = int(_env("MAX_TRIAGED_DETECTIONS", 10, int))

# ---- solar-system objects --------------------------------------------------
ASTEROID_MATCH_FLOOR_ARCSEC = 9.0   # ~1.5 native pixels
ASTEROID_MATCH_MAX_ARCSEC = 60.0
ECLIPTIC_LATITUDE_WARN_DEG = 30.0   # |beta| below this: asteroids are common
SSOIS_AGREEMENT_ARCSEC = 15.0       # JPL vs SSOIS position must agree within

# ---- archives / network ----------------------------------------------------
HTTP_TIMEOUT_S = _env("HTTP_TIMEOUT_S", 60.0)
HTTP_RETRIES = int(_env("HTTP_RETRIES", 3, int))
HTTP_BACKOFF_S = 2.0
GAIA_RELEASE = os.environ.get("SPHEREX_GAIA_RELEASE", "DR3")
GAIA_RUWE_MAX = 1.4

# ---- ML --------------------------------------------------------------------
ML_MODEL_DIR = os.environ.get("SPHEREX_ML_MODEL_DIR", "ml/models")
ML_RANDOM_SEED = 20261113
SPHEREX_COLLECTION = os.environ.get("SPHEREX_COLLECTION", "spherex_dr1")   # IRSA retired QR2

# ---- production (Phase 9) --------------------------------------------------
# Serving limits for the public API.  They never change a science result, so
# they live in a class: version.config_hash() hashes only UPPER-CASE module
# names, and a dashboard's config_hash must not differ between a laptop and
# Cloud Run.  Read them as config.Production.MAX_CONCURRENT_JOBS etc.
class Production:
    ENABLED = (os.environ.get("SPHEREX_ENV", "").lower() == "production"
               or bool(os.environ.get("K_SERVICE")))       # set by Cloud Run
    MAX_LIVE_SLOTS = int(_env("MAX_LIVE_SLOTS", 12, int))     # per live request
    MAX_FIELD_SIZE_DEG = _env("MAX_FIELD_SIZE_DEG", 0.5)      # "Open any field"
    FIELD_NAME_MAX_LEN = int(_env("FIELD_NAME_MAX_LEN", 80, int))
    MAX_CONCURRENT_JOBS = int(_env("MAX_CONCURRENT_JOBS", 1, int))
    MAX_QUEUED_JOBS = int(_env("MAX_QUEUED_JOBS", 8, int))
    MAX_JOBS_PER_CLIENT = int(_env("MAX_JOBS_PER_CLIENT", 2, int))
    JOB_RESULT_TTL_S = _env("JOB_RESULT_TTL_S", 1800.0)
    JOB_TIMEOUT_S = _env("JOB_TIMEOUT_S", 900.0)              # running job budget
    REQUEST_TIMEOUT_S = _env("REQUEST_TIMEOUT_S", 30.0)       # snapshot-API reads
    HEALTH_PROBE_TIMEOUT_S = _env("HEALTH_PROBE_TIMEOUT_S", 4.0)
    HEALTH_PROBE_TTL_S = _env("HEALTH_PROBE_TTL_S", 300.0)
    SNAPSHOT_MAX_AGE_H = _env("SNAPSHOT_MAX_AGE_H", 36.0)     # health: stale after
    VOTE_RATE_PER_MIN = int(_env("VOTE_RATE_PER_MIN", 30, int))
    VOTES_QUEUE_CACHE_S = _env("VOTES_QUEUE_CACHE_S", 180.0)
    CONE_MAX_SR_DEG = 1.0                                     # IVOA SCS cap
