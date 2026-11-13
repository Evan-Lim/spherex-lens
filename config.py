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
