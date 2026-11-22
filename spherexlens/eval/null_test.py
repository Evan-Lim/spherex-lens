"""
spherexlens.eval.null_test  (Phase 4.1)
Measure the false-alarm rate directly.

Within ONE epoch and ONE detector band, split the exposures at random into two
halves, co-add each half (matched masks), and ZOGY-subtract them.  Nothing on
the sky changed between the halves (they are days apart at most), so every
detection is a false alarm *by construction*.  Several random splits per
epoch/band per field.

Note: SPHEREx exposures of one detector sample different wavelengths of the
linear-variable filter, so the two halves are not identical in colour; that
systematic is real -- epoch-vs-epoch comparisons have it too -- and it is
deliberately left in the null.

Reported: false alarms per field (per 180x180 subtraction, scaled to the full
field from the usable area) and per deg^2, plus the distribution of null |S|
extrema, which threshold.py turns into the detection cut.
Epoch/band groups with < 2 exposures are skipped and listed.
"""
import numpy as np
from scipy.ndimage import maximum_filter, minimum_filter

import config
from spherexlens.eval import harness


def null_extrema(S, valid, floor=None):
    """|S| of every 5x5 local maximum of S and of -S above `floor` on valid pixels."""
    floor = config.EVAL_NULL_PEAK_FLOOR if floor is None else floor
    mx = (S == maximum_filter(S, size=5)) & (S > floor) & valid
    mn = (S == minimum_filter(S, size=5)) & (S < -floor) & valid
    return np.concatenate([S[mx], -S[mn]]).astype(float)


def eligible_groups(field, min_exposures=2):
    """({(epoch, band): [exp...]} with >= min_exposures, [skipped descriptions])."""
    ok, skipped = {}, []
    for key, exps in sorted(harness.groups_by_epoch_band(field).items(),
                            key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        if len(exps) >= min_exposures:
            ok[key] = exps
        else:
            skipped.append({"epoch": key[0], "band": key[1], "n_exposures": len(exps),
                            "reason": f"< {min_exposures} exposures"})
    return ok, skipped


def null_realisation(field, half_a, half_b, sigma=None, psf=None, keep_ctx=False):
    """Co-add two halves of one epoch/band, subtract, detect.  Returns a dict."""
    stacks, noise_maps, excluded, n_bands, extras = harness.coadd(field, {0: half_a, 1: half_b})
    s1, s2 = harness.scalar_noise(noise_maps[0]), harness.scalar_noise(noise_maps[1])
    if not (np.isfinite(s1) and np.isfinite(s2)):
        return None
    if psf is None or (extras or {}).get("psfs"):
        psf = harness.psf_from_extras(extras, [0, 1], [x["path"] for x in half_a + half_b],
                                      field["pixscale"])
    D, S = harness.subtract(stacks[0], stacks[1], s1, s2, psf)
    valid = harness.valid_mask([noise_maps[0], noise_maps[1]], excluded,
                               margin=config.EVAL_EDGE_MARGIN_PX // 2)
    # a detection off the usable area is a mask artefact, which the triage
    # already labels; the null measures the sky area only
    mjds = [x["mjd"] for x in half_a + half_b if x["mjd"]]
    span = (max(mjds) - min(mjds)) / 365.25 if len(mjds) > 1 else 0.0
    dets = harness.detect(S, max(span, 1.0 / 365.25), sigma, field["pixscale"])
    dets = [d for d in dets if _on(valid, harness.detection_position(d))]
    for d in dets:
        d["epochs"] = (0, 1)
        d["pair"] = "null"
    n_cand = _count_candidates(field, dets, half_a + half_b)
    out = {"n_false": len(dets), "n_false_candidates": n_cand,
           "valid_npix": int(valid.sum()),
           "peaks": null_extrema(S, valid).tolist(),
           "n_a": len(half_a), "n_b": len(half_b), "detections": dets}
    if keep_ctx:
        ctx = harness.make_ctx(field, stacks, noise_maps, excluded, n_bands, 0, 1, D, S,
                               dets, psf, epoch_mjd={0: None, 1: None}, extras=extras)
        out["ctx"] = ctx
    return out


def _count_candidates(field, dets, exposures):
    """How many null detections the live triage would show as CANDIDATE.

    Blobs on catalogued stars come out KNOWN_* or as bright-star / mask
    artefacts on the website; only CANDIDATE (and POSSIBLE_UNKNOWN_ASTEROID)
    reach a visitor as "something new".  Same triage call as app.get_dashboard,
    catalogues propagated to the exposures' mean epoch.
    """
    try:
        from catalog_tools import propagate_catalogs
        from step4_triage import triage
    except Exception:
        return None
    mjds = [x["mjd"] for x in exposures if x.get("mjd")]
    cats = propagate_catalogs(field.get("catalogs") or {},
                              float(np.mean(mjds)) if mjds else None)
    n = 0
    for d in dets:
        try:
            t = triage(d, field["ra"], field["dec"], field["npix"], field["pixscale"],
                       cats, wcs=field["wcs"], context={"baseline_years": None})
        except Exception:
            continue
        if t.get("category") in ("CANDIDATE", "POSSIBLE_UNKNOWN_ASTEROID"):
            n += 1
    return n


def _on(valid, pos):
    if pos is None:
        return False
    y, x = int(round(pos[0])), int(round(pos[1]))
    return 0 <= y < valid.shape[0] and 0 <= x < valid.shape[1] and bool(valid[y, x])


def run_null_test(field, n_splits=None, sigma=None, seed=None, max_groups=None,
                  max_per_half=None, on_realisation=None):
    """
    Random half-splits of every eligible epoch/band group of one field.

    on_realisation(result_with_ctx, key) -- optional hook (ML labels: every
    null detection is a "bogus" example); when given, ctx is kept for it.
    """
    n_splits = int(n_splits or config.EVAL_NULL_SPLITS)
    rng = np.random.default_rng(config.ML_RANDOM_SEED if seed is None else seed)
    groups, skipped = eligible_groups(field)
    keys = list(groups)
    if max_groups and len(keys) > max_groups:
        idx = sorted(rng.choice(len(keys), max_groups, replace=False))
        keys = [keys[i] for i in idx]
    reals = []
    for key in keys:
        exps = groups[key]
        psf = None                  # each realisation uses step2's merged ePSFs
        for k in range(n_splits):
            perm = rng.permutation(len(exps))
            h = len(exps) // 2
            a = [exps[i] for i in perm[:h]]
            b = [exps[i] for i in perm[h:]]
            if max_per_half:
                a, b = a[:max_per_half], b[:max_per_half]
            r = null_realisation(field, a, b, sigma, psf, keep_ctx=on_realisation is not None)
            if r is None:
                continue
            r.update({"epoch": key[0], "band": key[1], "split": k,
                      "n_exposures": len(exps)})
            if on_realisation is not None:
                on_realisation(r, key)
            r.pop("ctx", None)
            reals.append(r)
            if len(exps) == 2:
                break            # a 2-exposure group has only one distinct split
    return {"target": field["target"], "realisations": reals, "skipped": skipped,
            "npix": field["npix"], "pixscale": field["pixscale"],
            "sigma": sigma or config.DETECTION_SIGMA}


def per_field_rate(realisations, npix):
    """False alarms per full field for each realisation (area-scaled)."""
    full = float(npix * npix)
    return np.array([r["n_false"] * full / max(r["valid_npix"], 1) for r in realisations])


def summarise(results, seed=None, n_boot=None):
    """
    Pool null results of many fields.  Returns the CONTRACT "null_test" block
    plus per-field rates, bootstrap CI and the skipped groups.
    """
    n_boot = int(n_boot or config.EVAL_BOOTSTRAP)
    rng = np.random.default_rng(config.ML_RANDOM_SEED if seed is None else seed)
    rates, per_field, skipped, cand_rates = [], {}, [], []
    n_false = 0
    area_deg2 = 0.0
    for res in results:
        skipped += [{"field": res["target"], **s} for s in res["skipped"]]
        reals = res["realisations"]
        if not reals:
            continue
        r = per_field_rate(reals, res["npix"])
        rates.extend(r.tolist())
        pix_deg2 = (res["pixscale"] / 3600.0) ** 2
        a = sum(x["valid_npix"] for x in reals) * pix_deg2
        nf = sum(x["n_false"] for x in reals)
        nc = sum((x.get("n_false_candidates") or 0) for x in reals)
        cand_rates.extend([(x.get("n_false_candidates") or 0) * res["npix"] ** 2
                           / max(x["valid_npix"], 1) for x in reals])
        n_false += nf
        area_deg2 += a
        per_field[res["target"]] = {
            "n_realisations": len(reals), "n_false": nf,
            "false_alarms_per_field": round(float(r.mean()), 4),
            "false_alarms_per_deg2": round(nf / a, 3) if a else None,
            "n_false_candidates": nc,
        }
    rates = np.array(rates)
    out = {
        "n_fields": len(per_field), "n_realisations": int(rates.size),
        "n_false": int(n_false), "area_deg2": round(area_deg2, 5),
        "false_alarms_per_field": round(float(rates.mean()), 4) if rates.size else None,
        "false_alarms_per_deg2": round(n_false / area_deg2, 3) if area_deg2 else None,
        "candidate_false_alarms_per_field": (round(float(np.mean(cand_rates)), 4)
                                             if cand_rates else None),
        "per_field": per_field, "skipped": skipped,
        "definition": "detections in a ZOGY subtraction of two random halves of one "
                      "epoch+band; field = one 180x180 3\" subtraction",
    }
    if rates.size >= 2:
        boots = [rng.choice(rates, rates.size).mean() for _ in range(n_boot)]
        out["ci95"] = [round(float(np.percentile(boots, 2.5)), 4),
                       round(float(np.percentile(boots, 97.5)), 4)]
    return out


def pooled_peaks(results):
    """(|S| extrema, weight per extremum = full-field scaling, n realisations)."""
    peaks, weights, n = [], [], 0
    for res in results:
        full = float(res["npix"] ** 2)
        for r in res["realisations"]:
            w = full / max(r["valid_npix"], 1)
            peaks.extend(r["peaks"])
            weights.extend([w] * len(r["peaks"]))
            n += 1
    return np.array(peaks, dtype=float), np.array(weights, dtype=float), n
