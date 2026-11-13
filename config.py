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

# ---- eval (Phase 4: spherexlens/eval) -------------------------------------
EVAL_DIR = os.environ.get("SPHEREX_EVAL_DIR", "eval")
EVAL_NULL_SPLITS = int(_env("EVAL_NULL_SPLITS", 3, int))      # random halves per epoch/band
EVAL_NULL_PEAK_FLOOR = 3.0          # null |S| extrema recorded down to this
EVAL_MATCH_TOLERANCE_PX = 1.5       # strict injection: detection within this
EVAL_INJECT_SNR_GRID = (3, 5, 7, 10, 15, 20)   # PSF-matched single-epoch SNR
EVAL_INJECT_MU_GRID = (2, 5, 10, 20)           # arcsec / yr
EVAL_INJECT_TRIALS = int(_env("EVAL_INJECT_TRIALS", 20, int))  # per (snr, mu, category)
EVAL_INJECT_PER_IMAGE = 6           # sources injected into one image pair
EVAL_INJECT_MIN_SEP_PX = 35         # > step3 max_sep_px so injections never pair
EVAL_EDGE_MARGIN_PX = 12            # injections / answer key stay this far from edges
EVAL_BOOTSTRAP = int(_env("EVAL_BOOTSTRAP", 1000, int))
ANSWER_KEY_MIN_DISPLACEMENT_ARCSEC = 5.0   # ~0.8 native SPHEREx pixel (6.2")
ANSWER_KEY_GAIA_GMAG_MAX = 16.0     # bright enough for a SPHEREx dipole
ANSWER_KEY_VSX_AMPLITUDE_MIN = 0.5  # mag
ANSWER_KEY_VSX_MAG_MAX = 14.0       # brightest catalogued magnitude
ANSWER_KEY_MATCH_PX = 2.0           # catalogue astrometry is looser than injections
BH_FDR_Q = 0.05
# How the eval normalises the ZOGY S map: "pipeline" = exactly as step3 does
# (S / S.std(), which bright-star residuals inflate), "robust" = additionally
# rescale so the MAD of S is 1 (what a noise-limited S map would have).
EVAL_S_NORMALISATION = os.environ.get("SPHEREX_EVAL_S_NORMALISATION", "pipeline")

# ---- ml (Phase 5: spherexlens/ml) -----------------------------------------
ML_DATA_DIR = os.environ.get("SPHEREX_ML_DATA_DIR", "ml/data")
ML_HELDOUT_FIELD_FRACTION = 0.2     # fields never used for training/calibration
ML_CV_FOLDS = 5
ML_UNCERTAIN_LOW = 0.35             # 0.35 < P(real) < 0.65 -> "uncertain"
ML_UNCERTAIN_HIGH = 0.65
ML_DECISION_THRESHOLD = 0.5
ML_N_REASONS = 3
ML_FEATURE_RADIUS_PX = 3            # masked fraction / chi2 window
ML_BRIGHT_STAR_GMAG = 16.0          # "nearest bright Gaia star" cut
ML_FEATURE_CACHE = os.environ.get("SPHEREX_ML_FEATURE_CACHE", "1") not in ("0", "false", "")
ML_SPECTRAL_MIN_PER_CLASS = 20      # spectral classifier refuses to train below this


# ---- eval threshold loader (Phase 4.4) -------------------------------------
# When `python -m spherexlens.eval.threshold` (or eval.run) has written
# eval/threshold.json, its null-test-calibrated sigma replaces the 5.0
# default -- unless SPHEREX_DETECTION_SIGMA is set, which always wins.
def _load_eval_threshold(path=None):
    import json
    if os.environ.get("SPHEREX_DETECTION_SIGMA"):
        return None
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                EVAL_DIR, "threshold.json")
    try:
        with open(path) as f:
            sigma = float(json.load(f)["sigma"])
    except Exception:
        return None
    return sigma if 2.0 <= sigma <= 20.0 else None


EVAL_THRESHOLD_SIGMA = _load_eval_threshold()
if EVAL_THRESHOLD_SIGMA is not None:
    DETECTION_SIGMA = EVAL_THRESHOLD_SIGMA
