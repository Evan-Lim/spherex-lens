"""
spherexlens.ml.spectral  (Phase 5.9)
Spectral-type classifier (brown_dwarf / star / galaxy / other) on SPHEREx SEDs.

    python -m spherexlens.ml.spectral train   --cache data_cache [--fields ...]
    python -m spherexlens.ml.spectral predict --cache data_cache --field NAME

Objects: catalogued Gaia (G <= 15) and AllWISE (W1 <= 13) sources inside each
field's cutouts (at most SPECTRAL_MAX_OBJECTS per field).
SED: aperture photometry of every exposure (step5's algorithm -- r = 3 native
px, annulus r+2..r+6 -- applied in one pass per file; each exposure's
wavelength from its WCS-WAVE table), proper motion propagated to the
exposure's date.  Features: the flux in each of the six SPHEREx detector bands
divided by the summed |flux| (a colour vector independent of brightness), the
three step5 spectral indices (ch4, h2o, nir_slope) and the number of samples.

Catalogue labels (heuristic, documented in the card):
  brown_dwarf  W1-W2 >= 1.0, point-like, and Gaia parallax >= 20 mas or no Gaia
               counterpart; or the field target itself when its type names a dwarf
  galaxy       AllWISE ext_flg 2-5 (2MASS XSC association) and no significant
               Gaia parallax
  star         Gaia parallax / error >= 5 and W1-W2 < 0.5
  other        everything else
Minimum-sample guard: classes with fewer than config.ML_SPECTRAL_MIN_PER_CLASS
objects are dropped; with fewer than two classes left the command refuses to
train, says so and writes an honest card.  Brown dwarfs are rare -- expect
that class to be missing until many fields are processed.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

import config
import version as pipeline_version
from spherexlens.ml import paths

CLASSES = ("brown_dwarf", "star", "galaxy", "other")
BANDS = tuple(f"SPHEREx-D{i}" for i in range(1, 7))
SPEC_FEATURES = tuple(f"frac_{b[-2:]}" for b in BANDS) + ("ch4", "h2o", "nir_slope", "n_samples")
SPECTRAL_MAX_OBJECTS = 300


def label_object(gaia, wise, target_type=None):
    w12 = None
    if wise and wise.get("w1") is not None and wise.get("w2") is not None:
        w12 = float(wise["w1"]) - float(wise["w2"])
    plx = (gaia or {}).get("parallax")
    plx_err = (gaia or {}).get("parallax_error")
    plx_snr = plx / plx_err if plx and plx_err else 0.0
    # ext_flg 1 only means a poor PSF-profile fit (blends, saturation); 2-5 tie the
    # source to a 2MASS extended-source-catalogue galaxy
    ext = str((wise or {}).get("ext_flg") or "0") in ("2", "3", "4", "5")
    if target_type and any(k in target_type for k in ("Dwarf", "YDwarf", "TDwarf", "LDwarf")):
        return "brown_dwarf"
    if w12 is not None and w12 >= 1.0 and not ext and (gaia is None or (plx or 0) >= 20):
        return "brown_dwarf"
    if ext and plx_snr < 3:
        return "galaxy"
    if gaia is not None and plx_snr >= 5 and (w12 is None or w12 < 0.5):
        return "star"
    return "other"


def field_objects(field, max_objects=SPECTRAL_MAX_OBJECTS):
    """Catalogue objects inside the field with their labels."""
    from catalog_tools import sep_arcsec
    cats = field["catalogs"] or {}
    wcs, npix = field["wcs"], field["npix"]
    m = 8

    def inside(ra, dec):
        try:
            x, y = wcs.world_to_pixel_values(ra, dec)
        except Exception:
            return False
        return m <= float(x) < npix - m and m <= float(y) < npix - m

    wise = [w for w in cats.get("wise") or [] if (w.get("w1") or 99) <= 13 and inside(w["ra"], w["dec"])]
    gaia = [g for g in cats.get("gaia") or [] if (g.get("gmag") or 99) <= 15 and inside(g["ra"], g["dec"])]
    objs, used = [], set()
    for g in sorted(gaia, key=lambda s: s["gmag"]):
        best = None
        for i, w in enumerate(wise):
            if i not in used and sep_arcsec(g["ra"], g["dec"], w["ra"], w["dec"]) < 3.0:
                best = i
                break
        if best is not None:
            used.add(best)
        objs.append({"ra": g["ra"], "dec": g["dec"], "pm": (g.get("pm_ra"), g.get("pm_de")),
                     "gaia": g, "wise": wise[best] if best is not None else None})
    for i, w in enumerate(wise):
        if i not in used:
            objs.append({"ra": w["ra"], "dec": w["dec"], "pm": (None, None),
                         "gaia": None, "wise": w})
    objs = objs[:max_objects]
    meta = field["meta"]["meta"]
    from catalog_tools import sep_arcsec as sep
    for o in objs:
        ttype = meta.get("type") if sep(o["ra"], o["dec"], meta.get("ra_ref", meta["ra"]),
                                        meta.get("dec_ref", meta["dec"])) < 5 else None
        o["label"] = label_object(o["gaia"], o["wise"], ttype)
    return objs


def photometry(field, objs, r=3):
    """{object index: [(wavelength_um, band, flux), ...]} from every exposure."""
    from astropy.io import fits
    from astropy.wcs import WCS
    from catalog_tools import GAIA_DR3_EPOCH_MJD, propagate
    from step5_spectrum import _wavelength_from_wave_tab

    out = {i: [] for i in range(len(objs))}
    for ex in field["exposures"]:
        try:
            with fits.open(ex["path"]) as hdul:
                hdu = hdul["IMAGE"] if "IMAGE" in hdul else hdul[0]
                img = hdu.data.astype(float)
                wcs = WCS(hdu.header)
                for i, o in enumerate(objs):
                    ra, dec = o["ra"], o["dec"]
                    if o["pm"][0] and ex["mjd"]:
                        ra, dec = propagate(ra, dec, o["pm"][0], o["pm"][1] or 0.0,
                                            (ex["mjd"] - GAIA_DR3_EPOCH_MJD) / 365.25)
                    cx, cy = (float(v) for v in wcs.world_to_pixel_values(ra, dec))
                    if not (r + 7 <= cx < img.shape[1] - r - 7 and r + 7 <= cy < img.shape[0] - r - 7):
                        continue
                    y0, x0 = int(cy) - r - 7, int(cx) - r - 7
                    cut = img[y0:int(cy) + r + 8, x0:int(cx) + r + 8]
                    yy, xx = np.mgrid[y0:y0 + cut.shape[0], x0:x0 + cut.shape[1]]
                    d = np.hypot(xx - cx, yy - cy)
                    ap, ann = d < r, (d > r + 2) & (d < r + 6)
                    bg = np.nanmean(cut[ann]) if ann.any() else 0.0
                    flux = float(np.nansum(cut[ap]) - (bg if np.isfinite(bg) else 0) * ap.sum())
                    lam, _ = _wavelength_from_wave_tab(hdul, cx, cy)
                    out[i].append((lam, ex["band"], flux))
        except Exception as e:
            print(f"spectral: skipped {os.path.basename(ex['path'])}: {e}")
    return out


def sed_features(samples):
    from step5_spectrum import three_index_classifier
    f = dict.fromkeys(SPEC_FEATURES, float("nan"))
    if not samples:
        f["n_samples"] = 0.0
        return f
    by = {b: [] for b in BANDS}
    for lam, band, flux in samples:
        if band in by and np.isfinite(flux):
            by[band].append(flux)
    means = {b: (float(np.mean(v)) if v else float("nan")) for b, v in by.items()}
    tot = sum(abs(v) for v in means.values() if np.isfinite(v))
    for b in BANDS:
        if tot > 0 and np.isfinite(means[b]):
            f[f"frac_{b[-2:]}"] = means[b] / tot
    pts = [(lam, fl) for lam, _, fl in samples if lam and np.isfinite(fl)]
    if len(pts) >= 6:
        pts.sort()
        idx = three_index_classifier(np.array([p[0] for p in pts]), np.array([p[1] for p in pts]))
        f["ch4"], f["h2o"], f["nir_slope"] = idx["ch4"], idx["h2o"], idx["nir_slope"]
    f["n_samples"] = float(len(samples))
    return f


def build_table(cache, fields):
    from spherexlens.eval import harness
    rows = []
    for t in fields:
        field = harness.load_field(cache, t)
        objs = field_objects(field)
        phot = photometry(field, objs)
        for i, o in enumerate(objs):
            f = sed_features(phot[i])
            if f["n_samples"] < 2:
                continue
            rows.append({"field": t, "label": o["label"], "ra": o["ra"], "dec": o["dec"], **f})
        print(f"spectral: {t}: {len(objs)} objects", flush=True)
    return rows


def train_rows(rows, seed=None, min_per_class=None):
    """Train with the minimum-sample guard.  Returns (bundle or None, card)."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import GroupKFold, StratifiedKFold
    seed = config.ML_RANDOM_SEED if seed is None else seed
    min_n = config.ML_SPECTRAL_MIN_PER_CLASS if min_per_class is None else min_per_class
    counts = {c: sum(1 for r in rows if r["label"] == c) for c in CLASSES}
    kept = [c for c in CLASSES if counts[c] >= min_n]
    card = {"kind": "spectral", "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pipeline": pipeline_version.pipeline_stamp(), "features": list(SPEC_FEATURES),
            "class_counts": counts, "classes_trained": kept, "min_per_class": min_n,
            "fields": sorted({r["field"] for r in rows})}
    if len(kept) < 2:
        card["status"] = "insufficient_data"
        card["note"] = (f"only {len(kept)} class(es) reach {min_n} examples; refusing to train. "
                        "Process more fields (brown dwarfs are rare).")
        return None, card
    rows = [r for r in rows if r["label"] in kept]
    X = np.array([[r[f] for f in SPEC_FEATURES] for r in rows], dtype=float)
    y = np.array([kept.index(r["label"]) for r in rows])
    groups = np.array([r["field"] for r in rows])
    n_groups = len(set(groups))
    if n_groups >= 2:
        splitter = GroupKFold(n_splits=min(5, n_groups)).split(X, y, groups)
        card["split"] = f"GroupKFold by field ({min(5, n_groups)} folds)"
    else:
        splitter = StratifiedKFold(n_splits=3, shuffle=True, random_state=seed).split(X, y)
        card["split"] = "StratifiedKFold within ONE field (no field-level validation!)"
    pred = np.full(len(y), -1)
    for tr, te in splitter:
        if len(set(y[tr])) < 2:
            continue
        m = HistGradientBoostingClassifier(max_iter=150, class_weight="balanced",
                                           random_state=seed).fit(X[tr], y[tr])
        pred[te] = m.predict(X[te])
    ok = pred >= 0
    from sklearn.metrics import balanced_accuracy_score, confusion_matrix
    card["cv_balanced_accuracy"] = (round(float(balanced_accuracy_score(y[ok], pred[ok])), 4)
                                    if ok.any() else None)
    card["cv_confusion"] = {"labels": kept,
                            "matrix": confusion_matrix(y[ok], pred[ok],
                                                       labels=list(range(len(kept)))).tolist()
                            if ok.any() else []}
    model = HistGradientBoostingClassifier(max_iter=150, class_weight="balanced",
                                           random_state=seed).fit(X, y)
    card["status"] = "ok"
    card["n"] = int(len(y))
    return {"model": model, "classes": kept, "features": list(SPEC_FEATURES)}, card


def card_md(card):
    lines = ["# Model card: SPHEREx-Lens spectral-type classifier", "",
             f"Status: **{card['status']}** -- generated {card['generated_utc']}, pipeline "
             f"{card['pipeline']['pipeline_version']} ({card['pipeline']['config_hash']}).", "",
             "Intended use: a coarse brown dwarf / star / galaxy / other guess from the "
             "SPHEREx SED of catalogued objects; a hint for triage, never a classification "
             "of record.", "",
             "Labels are catalogue heuristics (WISE W1-W2 colour, AllWISE extent, Gaia "
             "parallax; see spherexlens/ml/spectral.py) -- noisy, and brown dwarfs are rare.", "",
             f"Class counts: {card['class_counts']} (minimum {card['min_per_class']} per class).", "",
             f"Fields: {len(card['fields'])}."]
    if card["status"] == "ok":
        lines += ["", f"Trained classes: {card['classes_trained']}; validation: {card['split']}; "
                  f"balanced accuracy {card.get('cv_balanced_accuracy')}; confusion "
                  f"{card.get('cv_confusion')}."]
    else:
        lines += ["", f"Not trained: {card['note']}"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Spectral-type classifier")
    ap.add_argument("cmd", choices=("train", "predict"))
    ap.add_argument("--cache", default=os.environ.get("SPHEREX_CACHE", "data_cache"))
    ap.add_argument("--fields", nargs="*")
    ap.add_argument("--field")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    from spherexlens.eval import harness
    import joblib
    mdir = paths.model_dir()
    if args.cmd == "train":
        fields = args.fields or harness.list_fields(args.cache)
        rows = build_table(args.cache, fields)
        bundle, card = train_rows(rows)
        os.makedirs(mdir, exist_ok=True)
        if bundle is not None:
            joblib.dump(bundle, os.path.join(mdir, "spectral-LATEST.joblib"), compress=3)
        with open(os.path.join(mdir, "spectral-LATEST.json"), "w") as f:
            json.dump(card, f, indent=1)
        with open(os.path.join(os.path.dirname(mdir.rstrip("/\\")), "SPECTRAL_MODEL_CARD.md"), "w") as f:
            f.write(card_md(card))
        print(f"spectral: {card['status']}; class counts {card['class_counts']}"
              + (f"; balanced accuracy {card.get('cv_balanced_accuracy')}" if bundle else
                 f"; {card['note']}"))
        return 0 if bundle is not None else 2
    if not args.field:
        print("predict needs --field")
        return 1
    path = os.path.join(mdir, "spectral-LATEST.joblib")
    if not os.path.exists(path):
        print("spectral: no trained model (run `train` first)")
        return 1
    bundle = joblib.load(path)
    rows = build_table(args.cache, [args.field])
    X = np.array([[r[f] for f in SPEC_FEATURES] for r in rows], dtype=float)
    probs = bundle["model"].predict_proba(X) if len(rows) else []
    out = [{"ra": r["ra"], "dec": r["dec"], "catalogue_label": r["label"],
            "probabilities": {c: round(float(p), 3) for c, p in zip(bundle["classes"], pr)}}
           for r, pr in zip(rows, probs)]
    text = json.dumps(out, indent=1)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
