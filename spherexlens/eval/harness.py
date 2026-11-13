"""
spherexlens.eval.harness
The small, clean pipeline the evaluation (and the ML label builder) runs on:

    field = load_field(cache_dir, "Barnards_Star")
    ctx   = run_pair(field, ea, eb)          # co-add, ZOGY, detect
    ctx["detections"]                        # raw step3 detections

It reuses step2 (make_target_wcs, coadd_matched) and step3 (zogy,
find_detections) exactly as app.get_dashboard does, but without the web,
snapshot and catalogue-network machinery, and it returns a context dict with
the same keys as the dashboard's ``enrich_ctx`` so spherexlens.ml.features
works on both.

PSF: the per-exposure SPHEREx EPSF (oversampled x5 on the 6.2" native grid)
is resampled onto the 3" analysis grid (see ``read_epsf``).  When a file has
no EPSF a Gaussian of config.PSF_SIGMA_PX_FALLBACK is used and recorded.
"""
import json
import os
import re

import numpy as np

import config
from step2_coadd import coadd_matched, make_target_wcs
from step3_zogy import find_detections, zogy

NOISE_CEILING = 1e3          # noise_map values above this are "no data"


# ---------------------------------------------------------------------------
# field loading
# ---------------------------------------------------------------------------
def _resolve_path(path, cache_dir):
    """A meta.json path as stored (often 'data_cache\\x.fits') -> file on disk."""
    if not isinstance(path, str):
        return None
    norm = os.path.normpath(path.replace("\\", os.sep))
    base = os.path.basename(norm)
    for cand in (norm, os.path.join(cache_dir, base), os.path.join(cache_dir, norm),
                 os.path.join(cache_dir, base + ".gz")):
        if os.path.exists(cand):
            return cand
    return None


def list_fields(cache_dir):
    """Targets in a cache dir that have a _meta.json and >= 2 cutouts on disk."""
    out = []
    for name in sorted(os.listdir(cache_dir)):
        if not name.endswith("_meta.json"):
            continue
        target = name[:-len("_meta.json")]
        try:
            with open(os.path.join(cache_dir, name)) as f:
                meta = json.load(f)
        except Exception:
            continue
        n = sum(1 for p in meta.get("paths", []) if _resolve_path(p, cache_dir))
        if n >= 2:
            out.append(target)
    return out


def _band_of(path, fallback):
    m = re.search(r"D(\d)_spx", os.path.basename(str(path)))
    return f"SPHEREx-D{m.group(1)}" if m else fallback


def load_field(cache_dir, target, npix=None, pixscale_arcsec=None):
    """
    Read <target>_meta.json and describe every cutout that is on disk.

    Returns a dict: target, meta, ra, dec, npix, pixscale, wcs, catalogs,
    exposures=[{path, epoch, band, mjd, obs_id, index}], epoch_mjd.
    """
    npix = int(npix or config.NPIX)
    pixscale = float(pixscale_arcsec or config.PIXSCALE_ARCSEC)
    with open(os.path.join(cache_dir, f"{target}_meta.json")) as f:
        meta = json.load(f)
    by_file = {}
    for s in meta.get("sources") or []:
        if isinstance(s, dict) and s.get("path"):
            by_file[os.path.basename(os.path.normpath(
                s["path"].replace("\\", os.sep)))] = s
    epochs = meta.get("epochs")
    paths = meta.get("paths") or []
    exposures = []
    for i, p in enumerate(paths):
        path = _resolve_path(p, cache_dir)
        if path is None:
            continue
        s = by_file.get(os.path.basename(os.path.normpath(p.replace("\\", os.sep)))) or {}
        epoch = s.get("epoch")
        if epoch is None and epochs is not None and len(epochs) == len(paths):
            epoch = epochs[i]
        exposures.append({
            "path": path, "index": i,
            "epoch": epoch if epoch is not None else 1,
            "band": s.get("band") or _band_of(path, f"exp{i}"),
            "mjd": s.get("mjd"), "obs_id": s.get("obs_id"),
        })
    ra, dec = meta["meta"]["ra"], meta["meta"]["dec"]
    epoch_mjd = {}
    for e in sorted({x["epoch"] for x in exposures}):
        vals = [x["mjd"] for x in exposures if x["epoch"] == e and x["mjd"]]
        epoch_mjd[e] = float(np.mean(vals)) if vals else None
    return {
        "target": target, "meta": meta, "ra": ra, "dec": dec,
        "npix": npix, "pixscale": pixscale,
        "wcs": make_target_wcs(ra, dec, npix=npix, pixscale_arcsec=pixscale),
        "catalogs": meta.get("catalogs") or {},
        "exposures": exposures, "epoch_mjd": epoch_mjd,
    }


def groups_by_epoch_band(field):
    """{(epoch, band): [exposure, ...]} in mjd order."""
    out = {}
    for x in field["exposures"]:
        out.setdefault((x["epoch"], x["band"]), []).append(x)
    for v in out.values():
        v.sort(key=lambda x: (x["mjd"] or 0, x["index"]))
    return out


def deep_pair(field):
    """(first, last) epoch sharing at least one band, or None."""
    eps = {}
    for x in field["exposures"]:
        eps.setdefault(x["epoch"], set()).add(x["band"])
    order = sorted(eps)
    for i, ea in enumerate(order):
        for eb in reversed(order[i + 1:]):
            if eps[ea] & eps[eb]:
                return ea, eb
    return None


def pair_exposures(field, ea, eb, max_per_band=None, rng=None):
    """Exposures of epochs ea and eb restricted to bands both epochs have."""
    g = groups_by_epoch_band(field)
    shared = ({b for (e, b) in g if e == ea} & {b for (e, b) in g if e == eb})
    out = {ea: [], eb: []}
    for e in (ea, eb):
        for b in sorted(shared):
            items = list(g[(e, b)])
            if max_per_band and len(items) > max_per_band:
                if rng is not None:
                    idx = sorted(rng.choice(len(items), max_per_band, replace=False))
                    items = [items[i] for i in idx]
                else:
                    items = items[:max_per_band]
            out[e].extend(items)
    return out


# ---------------------------------------------------------------------------
# PSF
# ---------------------------------------------------------------------------
EPSF_OVERSAMPLE = 5


def _triangle(half):
    k = 1.0 - np.abs(np.arange(-half, half + 1)) / float(half + 1)
    return k / k.sum()


def read_epsf(path, pixscale_arcsec=None, half_size=7):
    """
    The SPHEREx effective PSF of one cutout resampled to the analysis grid.

    The EPSF table holds 21x21 zones of 33x33 samples oversampled x5 on the
    native 6.2" pixel.  We take the zone nearest the cutout centre, convolve
    it with the bilinear-interpolation kernel that reprojection applies
    (a triangle one native pixel wide), and sample it every `pixscale_arcsec`.
    Returns a (2*half_size+1)^2 stamp normalised to unit sum, or None.
    """
    from astropy.io import fits
    from scipy.ndimage import convolve1d, map_coordinates

    pixscale = float(pixscale_arcsec or config.PIXSCALE_ARCSEC)
    try:
        with fits.open(path) as hdul:
            if "EPSF" not in hdul:
                return None
            tab = hdul["EPSF"].data
            hdr = hdul["IMAGE"].header if "IMAGE" in hdul else hdul[0].header
            ny, nx = hdr.get("NAXIS2", 0), hdr.get("NAXIS1", 0)
            xp = nx / 2.0 - float(hdr.get("CRPIX1A", hdr.get("CRPIX1W", 0.0)))
            yp = ny / 2.0 - float(hdr.get("CRPIX2A", hdr.get("CRPIX2W", 0.0)))
            d2 = (np.asarray(tab["XCENTER"]) - xp) ** 2 + (np.asarray(tab["YCENTER"]) - yp) ** 2
            stamp = np.asarray(tab["EPSF"][int(np.argmin(d2))], dtype=float)
    except Exception:
        return None
    if stamp.ndim != 2 or not np.isfinite(stamp).all() or stamp.sum() <= 0:
        return None
    stamp = np.clip(stamp, 0, None)
    tri = _triangle(EPSF_OVERSAMPLE)
    stamp = convolve1d(convolve1d(stamp, tri, axis=0, mode="constant"), tri, axis=1,
                       mode="constant")
    c = (stamp.shape[0] - 1) / 2.0
    step = pixscale / (config.NATIVE_PIXEL_ARCSEC / EPSF_OVERSAMPLE)
    off = np.arange(-half_size, half_size + 1) * step
    yy, xx = np.meshgrid(c + off, c + off, indexing="ij")
    out = map_coordinates(stamp, [yy, xx], order=1, mode="constant", cval=0.0)
    out = np.clip(out, 0, None)
    if out.sum() <= 0:
        return None
    return out / out.sum()


def psf_moments(stamp):
    """(sigma_px, fwhm_px) of a stamp from its second moments."""
    s = np.clip(np.asarray(stamp, dtype=float), 0, None)
    n = s.shape[0]
    yy, xx = np.mgrid[0:n, 0:n] - (n - 1) / 2.0
    tot = s.sum()
    if tot <= 0:
        return config.PSF_SIGMA_PX_FALLBACK, 2.3548 * config.PSF_SIGMA_PX_FALLBACK
    var = float(((xx ** 2 + yy ** 2) * s).sum() / tot / 2.0)
    sig = float(np.sqrt(max(var, 1e-6)))
    return sig, 2.3548 * sig


def gaussian_stamp(sigma_px, half_size=7):
    n = 2 * half_size + 1
    yy, xx = np.mgrid[0:n, 0:n] - half_size
    g = np.exp(-(xx ** 2 + yy ** 2) / (2.0 * sigma_px ** 2))
    return g / g.sum()


def field_psf(paths, pixscale_arcsec=None, max_files=8):
    """
    Mean EPSF of a set of cutouts -> {"stamp", "sigma_px", "fwhm_px", "model"}.
    model is "epsf" (n files used) or "gaussian" (fallback, recorded).
    """
    stamps = []
    for p in list(paths)[:max_files]:
        s = read_epsf(p, pixscale_arcsec)
        if s is not None:
            stamps.append(s)
    if stamps:
        stamp = np.mean(stamps, axis=0)
        stamp /= stamp.sum()
        sig, fwhm = psf_moments(stamp)
        return {"stamp": stamp, "sigma_px": sig, "fwhm_px": fwhm,
                "model": "epsf", "n_files": len(stamps)}
    sig = config.PSF_SIGMA_PX_FALLBACK
    return {"stamp": gaussian_stamp(sig), "sigma_px": sig, "fwhm_px": 2.3548 * sig,
            "model": "gaussian", "n_files": 0}


# ---------------------------------------------------------------------------
# co-add / subtract / detect
# ---------------------------------------------------------------------------
def coadd(field, groups):
    """
    groups {key: [exposure, ...]} -> stacks, noise_maps, excluded, n_bands, extras.

    extras is step2's return_psf dict (psfs, psf_info, exposure_quality,
    edge_map) when available, else {} (older step2).
    """
    g = {k: [(x["path"], x["band"]) for x in v] for k, v in groups.items()}
    try:
        out = coadd_matched(g, field["wcs"], npix=field["npix"], return_psf=True)
        if len(out) == 5:
            return out
        return (*out, {})
    except TypeError:
        return (*coadd_matched(g, field["wcs"], npix=field["npix"]), {})


def psf_from_extras(extras, keys, paths=None, pixscale=None):
    """
    PSF dict for a pair of co-adds: per-epoch stamps from step2 (the merged
    exposure ePSFs), their mean as "stamp", widths from second moments.
    Falls back to field_psf(paths) when step2 supplied none.
    """
    psfs = (extras or {}).get("psfs") or {}
    info = (extras or {}).get("psf_info") or {}
    stamps = [np.asarray(psfs[k], dtype=float) for k in keys if psfs.get(k) is not None]
    if len(stamps) == len(keys) and stamps:
        n = max(s.shape[0] for s in stamps)
        padded = []
        for st in stamps:
            p = (n - st.shape[0]) // 2
            padded.append(np.pad(st, p) / st.sum())
        mean = np.mean(padded, axis=0)
        mean /= mean.sum()
        sig, fwhm = psf_moments(mean)
        srcs = {(info.get(k) or {}).get("source") for k in keys}
        model = "epsf" if srcs == {"epsf"} else ("gaussian" if srcs <= {"gaussian_fallback", None}
                                               else "mixed")
        return {"stamp": mean, "stamps": {k: pp for k, pp in zip(keys, padded)},
                "sigma_px": sig, "fwhm_px": fwhm, "model": model,
                "psf_info": {str(k): info.get(k) for k in keys}}
    return field_psf(paths or [], pixscale)


def scalar_noise(noise_map):
    nm = np.where(np.isfinite(noise_map) & (noise_map < NOISE_CEILING), noise_map, np.nan)
    v = float(np.nanmedian(nm)) if np.isfinite(nm).any() else float("nan")
    return v


def valid_mask(noise_maps, excluded=None, margin=0):
    """Pixels with data in every map, no mask-matching losses, off the edges."""
    ok = None
    for nm in noise_maps:
        m = np.isfinite(nm) & (nm < NOISE_CEILING)
        ok = m if ok is None else (ok & m)
    if excluded is not None:
        ok = ok & (np.asarray(excluded) == 0)
    if margin:
        ok = ok.copy()
        ok[:margin] = ok[-margin:] = False
        ok[:, :margin] = ok[:, -margin:] = False
    return ok


def robust_renormalise(S):
    """S / (1.4826 MAD(S)): unit noise even when bright residuals inflate std."""
    v = S[np.isfinite(S)]
    if v.size < 10:
        return S
    mad = 1.4826 * float(np.median(np.abs(v - np.median(v))))
    return S / mad if mad > 0 else S


def subtract(img1, img2, sigma1, sigma2, psf=None, zogy_fn=None, normalisation=None):
    """
    ZOGY D and S.  Uses the real PSF stamp when step3.zogy accepts psf1/psf2
    (newer step3), else a Gaussian with the stamp's second-moment width.
    normalisation -- "pipeline" (step3 as is) or "robust" (see config).
    """
    norm = normalisation or config.EVAL_S_NORMALISATION
    D, S = _subtract(img1, img2, sigma1, sigma2, psf, zogy_fn)
    if norm == "robust":
        S = robust_renormalise(S)
    return D, S


def _subtract(img1, img2, sigma1, sigma2, psf=None, zogy_fn=None):
    fn = zogy_fn or zogy
    psf = psf or {}
    sig = psf.get("sigma_px") or config.PSF_SIGMA_PX_FALLBACK
    if zogy_fn is None and psf.get("stamp") is not None:
        stamps = list((psf.get("stamps") or {}).values())
        p1, p2 = (stamps[0], stamps[-1]) if len(stamps) >= 2 else (psf["stamp"], psf["stamp"])
        try:
            return fn(img1, img2, sigma1, sigma2, psf_sigma1=sig, psf_sigma2=sig,
                      psf1=p1, psf2=p2)
        except TypeError:
            pass                     # older step3 without psf1/psf2
    return fn(img1, img2, sigma1, sigma2, sig, sig)


def detect(S, dt_years, sigma_thresh=None, pixscale_arcsec=None):
    return find_detections(S, pix_scale_arcsec=pixscale_arcsec or config.PIXSCALE_ARCSEC,
                           dt_years=dt_years or 0.5,
                           sigma_thresh=sigma_thresh or config.DETECTION_SIGMA)


def detection_position(d):
    """(y, x) of a detection's main (positive or only) lobe, any dict flavour."""
    p = d.get("position") or d.get("pos_plus") or d.get("pos")
    if p is None and d.get("pos_minus") is not None:
        p = d.get("pos_minus")
    return (float(p[0]), float(p[1])) if p is not None else None


def make_ctx(field, stacks, noise_maps, excluded, n_bands, ea, eb, D, S, detections,
             psf=None, epoch_mjd=None, extras=None):
    """A dict with the keys of app.get_dashboard's enrich_ctx (plus a few)."""
    epoch_mjd = epoch_mjd if epoch_mjd is not None else field.get("epoch_mjd", {})
    noises = {e: scalar_noise(noise_maps[e]) for e in stacks}
    return {
        "target": field["target"], "meta": field["meta"], "wcs": field["wcs"],
        "npix": field["npix"], "pixscale_arcsec": field["pixscale"],
        "ra": field["ra"], "dec": field["dec"],
        "stacks": stacks, "noises": noises, "noise_maps": noise_maps,
        "excluded": excluded, "n_bands": n_bands,
        "epoch_order": [ea, eb], "epoch_mjd": epoch_mjd,
        "raw_catalogs": field.get("catalogs") or {},
        "S": S, "D": D, "raw_detections": detections, "detections": detections,
        "psf": psf, "pair": (ea, eb),
        "psfs": (extras or {}).get("psfs"), "psf_info": (extras or {}).get("psf_info"),
        "exposure_quality": (extras or {}).get("exposure_quality"),
        "edge_map": (extras or {}).get("edge_map"),
    }


def dt_years(field, ea, eb):
    a, b = field["epoch_mjd"].get(ea), field["epoch_mjd"].get(eb)
    if a and b and b > a:
        return (b - a) / 365.25
    return 0.5


def run_pair(field, ea, eb, sigma=None, max_per_band=None, rng=None, psf=None):
    """Co-add epochs ea/eb (shared bands, matched masks), ZOGY, detect."""
    groups = pair_exposures(field, ea, eb, max_per_band=max_per_band, rng=rng)
    if not groups[ea] or not groups[eb]:
        return None
    stacks, noise_maps, excluded, n_bands, extras = coadd(field, groups)
    if psf is None:
        psf = psf_from_extras(extras, [ea, eb], [x["path"] for x in groups[ea] + groups[eb]],
                              field["pixscale"])
    s1, s2 = scalar_noise(noise_maps[ea]), scalar_noise(noise_maps[eb])
    if not (np.isfinite(s1) and np.isfinite(s2)):
        return None
    D, S = subtract(stacks[ea], stacks[eb], s1, s2, psf)
    dt = dt_years(field, ea, eb)
    dets = detect(S, dt, sigma, field["pixscale"])
    for d in dets:
        d["epochs"] = (ea, eb)
        d["pair"] = f"E{ea}→E{eb}"
    ctx = make_ctx(field, stacks, noise_maps, excluded, n_bands, ea, eb, D, S, dets, psf,
                   extras=extras)
    ctx["dt_years"] = dt
    ctx["n_exposures"] = {ea: len(groups[ea]), eb: len(groups[eb])}
    return ctx
