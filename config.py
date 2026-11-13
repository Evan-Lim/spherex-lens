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
