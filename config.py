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

# ---- asteroids (Phase 1.1: spherexlens.triage.asteroids) --------------------
# ASTEROID_MATCH_FLOOR_ARCSEC, ASTEROID_MATCH_MAX_ARCSEC,
# ECLIPTIC_LATITUDE_WARN_DEG and SSOIS_AGREEMENT_ARCSEC are defined above.
ASTEROID_MAX_QUERIES_PER_FIELD = int(_env("ASTEROID_MAX_QUERIES_PER_FIELD", 4, int))
ASTEROID_TIME_DEDUPE_DAYS = 0.01    # exposures closer than this share one query
ASTEROID_MATCH_UNC_K = 3.0          # match radius = k * position uncertainty
ASTEROID_KNOWN_PRIORITY = 3         # same tier as KNOWN_MOVER / KNOWN_VARIABLE
ASTEROID_POSSIBLE_PRIORITY = 2      # below CANDIDATE (1): likely an asteroid
ASTEROID_ONE_EPOCH_SIGMA = 3.0      # PSF-weighted excess that counts as "seen"
JPL_SB_IDENT_TIMEOUT_S = _env("JPL_SB_IDENT_TIMEOUT_S", 400.0)  # it is slow
SSOIS_MAX_BRACKET_DAYS = 2.0        # SSOIS images used for interpolation

# ---- PSF / ZODI (phase 1.3, 1.4) -------------------------------------------
# The PSF comes from each cutout's EPSF table (zone nearest the target,
# resampled to the 3" grid); PSF_SIGMA_PX_FALLBACK above is used only when a
# file has none.  PSF_SIZE_PX is the side of the resampled kernel (odd): the
# 33 x 33 ePSF at 1.24"/sample spans 41", i.e. ~14 grid pixels.
PSF_SIZE_PX = 15
SUBTRACT_ZODI = os.environ.get("SPHEREX_SUBTRACT_ZODI", "1") not in ("0", "false", "False")

# ---- exposure quality (phase 3.2, spherexlens/imaging.score_exposures) -----
# score = s_flag * s_noise * s_psf * s_astro, each a linear ramp from 1 (at or
# better than *_OK) to 0 (at or worse than *_BAD):
#   s_flag  -- fraction of pixels unusable (FLAGS & BAD_BITS, non-finite).
#              Real DR1 cutouts: 1-7 %.
#   s_noise -- pixel-to-pixel noise / sqrt(VARIANCE), relative to the median of
#              the same epoch, or to EQ_NOISE_ABS_REF (whichever is worse).
#              Real DR1 cutouts: 1.05-2.4 x the VARIANCE prediction.
#   s_psf   -- ePSF FWHM relative to the median of the same detector band.
#   s_astro -- multiplied by EQ_ASTRO_UNSOLVED when ASTRONETSOLVED is False and
#              by EQ_DQA_FAIL when L1DQAFLG/L2DQAFLG is not 'Pass'.
# Exposures scoring below EXPOSURE_QUALITY_MIN are left out of the co-add; an
# epoch is never emptied (its best exposure is kept, with a reason).
EXPOSURE_QUALITY_MIN = _env("EXPOSURE_QUALITY_MIN", 0.3)
EQ_FLAG_OK, EQ_FLAG_BAD = 0.15, 0.60
EQ_NOISE_OK, EQ_NOISE_BAD = 1.5, 3.0
EQ_NOISE_ABS_REF = 2.5
EQ_PSF_OK, EQ_PSF_BAD = 1.3, 2.0
EQ_ASTRO_UNSOLVED = 0.2
EQ_DQA_FAIL = 0.5

# ---- artefact masks (phase 3.3) --------------------------------------------
# Detector-edge mask: detections within EDGE_MASK_PX target pixels of any
# exposure footprint edge (where the co-add weight jumps) are MASK_ARTEFACT.
EDGE_MASK_PX = int(_env("EDGE_MASK_PX", 4, int))
# Bright-star cross: arms along each exposure's detector rows/columns (SPHEREx
# is unobscured, so no spider spikes; bleed/crosstalk run along the readout
# axes), SPIKE_LENGTH_FACTOR x the bright-star mask radius long.
SPIKE_LENGTH_FACTOR = 2.5
SPIKE_HALF_WIDTH_ARCSEC = 6.2       # one native pixel
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

# ---- asteroid check time box (integration) ---------------------------------
# Web requests wait at most this long for JPL sb_ident (1-5 min per query);
# 0 = wait for every answer.  cloud_refresh.py sets 0 for published snapshots.
ASTEROID_TIME_BOX_S = _env("ASTEROID_TIME_BOX_S", 25.0)

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
ML_MIN_PER_CLASS = 10               # real/bogus training refuses below this per class
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

# ---- ingest (Phase 2.1-2.3, 3.1, 3.4, 3.5) -- spherexlens/ingest/ ---------
# SPHEREx Level-2 files in the public AWS bucket (anonymous HTTPS, S3 REST).
# Layout: <release>/level2/<planning period>/<l2b version>/<detector 1-6>/
#         level2_<period>_<obs seq>_<exp>D<det>_spx_<l2b version>.fits
SPHEREX_S3_ENDPOINT = "https://nasa-irsa-spherex.s3.amazonaws.com"
# Gaia mirrors tried in order by spherexlens.ingest.gaia.fetch_gaia_with_fallback
GAIA_MIRRORS = ("irsa", "mast", "vizier")
MAST_INVOKE_URL = "https://mast.stsci.edu/api/v0/invoke"
# CADC ObsCore TAP (registry ivo://cadc.nrc.ca/argus); NEOSSat as CADC lists it
CADC_TAP_SYNC = "https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus/sync"
NEOSSAT_COLLECTION = "NEOSSAT"
NEOSSAT_STAMP_SNR = 5.0             # aperture SNR for "source seen in a stamp"
# /api/coverage and the "coverage" enricher
COVERAGE_TIMEOUT_S = _env("COVERAGE_TIMEOUT_S", 20.0)        # per archive, API
COVERAGE_ENRICH_BUDGET_S = _env("COVERAGE_ENRICH_BUDGET_S", 3.0)   # dashboard
COVERAGE_CACHE_TTL_S = 86400
COVERAGE_RADIUS_DEG = 0.1
COVERAGE_MAX_RADIUS_DEG = 1.0
WISEVIEW_SIZE_ARCSEC = 176
