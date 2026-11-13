"""
Deep-field light curves.

    python -m spherexlens.deep.run --name NEP --ra 270.0 --dec 66.56 \
        [--max-exposures 200] [--size 0.1] [--gmax 15.5] [--max-sources 60]

1. SIA2 query of `spherex_dr1_deep` for images containing the position;
2. stratified sample over time x detector (select.py), cutouts via IRSA;
3. Gaia DR3 sources in the cutout (G <= gmax, brightest first);
4. forced photometry on every cutout (photometry.py);
5. spectrum-normalised light curves, variability, periods, motion
   (lightcurve.py);
6. writes deep/<name>.json (summary + per-source light curves).

Heavy: a 200-exposure run downloads ~200 cutouts.  Run it as a background or
Cloud Run job, not inside a web request.
"""
import argparse
import json
import os
import time

from version import pipeline_stamp

OUT_DIR = os.environ.get("SPHEREX_DEEP_DIR", "deep")


def gaia_sources(ra, dec, radius_deg, gmax=17.0, max_sources=60):
    """Gaia stars to measure.  strict=True: an IRSA outage raises instead of
    returning [] -- an empty list would be published as "0 variable stars"."""
    import step1_ingest as ingest
    rows = ingest.query_irsa_tap_gaia(ra, dec, radius_deg, limit=5000, strict=True)
    rows = [r for r in rows if r.get("gmag", 99) <= gmax]
    rows.sort(key=lambda r: r.get("gmag", 99))
    out = [{"id": f"Gaia DR3 {r.get('source_id')}", "ra": r["ra"], "dec": r["dec"],
            "gmag": r.get("gmag"), "var_flag": r.get("var_flag"),
            "gaia_pm_ra": r.get("pm_ra"), "gaia_pm_dec": r.get("pm_de")}
           for r in rows[:max_sources]]
    return out


class DeepRunError(RuntimeError):
    """The run could not produce a trustworthy result; nothing was written."""


def run(name, ra, dec, max_exposures=200, size_deg=0.1, gmax=17.0,
        max_sources=60, workers=3):
    from spherexlens.deep import lightcurve, photometry, select

    t0 = time.time()
    # Catalogue first: if it fails, stop before downloading anything and
    # leave any previous deep/<name>.json untouched.
    sources = gaia_sources(ra, dec, size_deg / 2 * 0.9, gmax, max_sources)
    if not sources:
        raise DeepRunError(f"{name}: no Gaia sources with G <= {gmax} inside the "
                           f"cutout; nothing written")
    rows = select.query_rows(ra, dec)
    chosen = select.select_rows(rows, max_exposures=max_exposures)
    print(f"{name}: {len(rows)} deep images contain the position; "
          f"using {len(chosen)} spread over time and detectors")
    fetched = select.fetch(chosen, ra, dec, size_deg=size_deg, workers=workers)
    print(f"{name}: {len(fetched)} cutouts on disk")
    if len(fetched) < 12:
        raise DeepRunError(f"{name}: only {len(fetched)} usable cutouts; nothing written")
    print(f"{name}: forced photometry of {len(sources)} Gaia sources")
    per_source = {s["id"]: [] for s in sources}
    for path, _row in fetched:
        try:
            for m in photometry.measure(path, sources):
                per_source[m["id"]].append(m)
        except Exception as e:
            print(f"    photometry failed on {os.path.basename(path)}: {e}")
    results = []
    for s in sources:
        summary = lightcurve.analyse_source(per_source[s["id"]])
        results.append({**s, **(summary or {})})
    ensemble = lightcurve.flag_variables_against_ensemble(results)
    ok = [r for r in results if r.get("status") == "ok"]
    payload = {
        "name": name, "ra": ra, "dec": dec, "collection": select.DEEP_COLLECTION,
        "pipeline": pipeline_stamp(),
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_images_available": len(rows), "n_exposures_used": len(fetched),
        "n_sources": len(sources), "n_analysed": len(ok),
        "n_variable": sum(1 for r in ok if r.get("variable")),
        "n_periodic": sum(1 for r in ok if (r.get("period") or {}).get("significant")),
        "n_moving": sum(1 for r in ok if (r.get("motion") or {}).get("significance", 0) > 5),
        "method": ("forced aperture photometry; flux divided by each source's own "
                   "mean SPHEREx spectrum (LVF wavelength differs per visit); "
                   "3% systematic floor; chi-square vs constant and vs the field's median "
                   "star; Lomb-Scargle; centroid motion fit"),
        "ensemble_chi2_median": ensemble,
        "seconds": round(time.time() - t0, 1),
        "sources": results,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"{name}.json")
    tmp = out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, out)              # atomic: a crash never leaves half a file
    print(f"{name}: {payload['n_analysed']} analysed, {payload['n_variable']} variable, "
          f"{payload['n_periodic']} periodic, {payload['n_moving']} moving -> {out}")
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--name", required=True)
    ap.add_argument("--ra", type=float, required=True)
    ap.add_argument("--dec", type=float, required=True)
    ap.add_argument("--max-exposures", type=int, default=200)
    ap.add_argument("--size", type=float, default=0.1)
    ap.add_argument("--gmax", type=float, default=17.0)
    ap.add_argument("--max-sources", type=int, default=60)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args(argv)
    run(a.name, a.ra, a.dec, a.max_exposures, a.size, a.gmax, a.max_sources, a.workers)


if __name__ == "__main__":
    main()
