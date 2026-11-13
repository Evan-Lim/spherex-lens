"""
export_static.py

OPTIONAL deployment helper -- NOT one of the ten blueprint files.

The app is a FastAPI service plus a vanilla-JS dashboard.  Render/Railway/Fly
can run the whole thing as a container (see Dockerfile), but Netlify,
Cloudflare Pages and GitHub Pages can only serve static files.  This script
pre-renders every API response the dashboard needs, so the same UI runs with no
backend at all:

    python export_static.py                 # writes site/
    python export_static.py --outdir public --targets WISE_0855 RW_Aurigae

Output:

    site/
    ├── index.html            dashboard (data-mode="static")
    ├── style.css
    ├── app.js
    ├── api/
    │   ├── targets.json
    │   ├── dashboard-<Target>.json
    │   └── recovery-<Target>.json
    └── figures/              the pipeline charts, for the "science" section

Deploy with any static host:  netlify deploy --dir site
                              (or point GitHub Pages at site/)
"""

import os
import sys
import html as html_lib
import json
import shutil
import argparse

try:
    import app as portal
except ModuleNotFoundError as exc:      # e.g. running with the wrong Python
    missing = exc.name or "a dependency"
    sys.exit(
        f"\nexport_static.py needs the project environment -- '{missing}' is not\n"
        f"installed in {sys.executable}.\n\n"
        "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt\n"
        "  .venv/bin/python export_static.py\n\n"
        "or, with conda:\n\n"
        "  conda create -n spherex python=3.11 -y && conda activate spherex\n"
        "  pip install -r requirements.txt && python export_static.py\n\n"
        "If you only want to DEPLOY, you do not need this script: publish the\n"
        "site/ folder that was last generated for this project.\n"
    )


# Shown by the Credits dialog when no /api/credits is available.  Keep in step
# with CREDITS_FALLBACK in static/app.js.
CREDITS_FALLBACK = {"sources": [
    {"name": "NASA/IPAC Infrared Science Archive (IRSA)",
     "provides": "SPHEREx Level-2 images; WISE, AllWISE and NEOWISE photometry",
     "licence": "NASA data, no usage restrictions; acknowledgement requested",
     "url": "https://irsa.ipac.caltech.edu/",
     "text": "This research has made use of the NASA/IPAC Infrared Science Archive, which is "
             "funded by the National Aeronautics and Space Administration and operated by the "
             "California Institute of Technology."},
    {"name": "ESA Gaia (DPAC)", "provides": "Gaia DR3 positions, proper motions and variability flags",
     "licence": "CC BY-SA 3.0 IGO", "url": "https://www.cosmos.esa.int/gaia",
     "text": "This work has made use of data from the European Space Agency (ESA) mission Gaia, "
             "processed by the Gaia Data Processing and Analysis Consortium (DPAC)."},
    {"name": "STScI MAST", "provides": "Gaia catalogue links (MAST Portal)",
     "licence": "public archive; acknowledgement requested", "url": "https://archive.stsci.edu/"},
    {"name": "JPL Solar System Dynamics",
     "provides": "Small-Body Database (SBDB) and SB Identification asteroid check",
     "licence": "NASA data, no usage restrictions", "url": "https://ssd.jpl.nasa.gov/"},
    {"name": "Canadian Astronomy Data Centre (CADC)", "provides": "SSOIS asteroid check; NEOSSat archive",
     "licence": "see CADC terms of use", "url": "https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/",
     "text": "This research used the facilities of the Canadian Astronomy Data Centre operated by "
             "the National Research Council of Canada with the support of the Canadian Space Agency."},
    {"name": "Canadian Space Agency - NEOSSat", "provides": "NEOSSat images",
     "licence": "Open Government Licence - Canada",
     "url": "https://open.canada.ca/en/open-government-licence-canada"},
    {"name": "AAVSO VSX via CDS VizieR", "provides": "variable-star catalogue",
     "licence": "see AAVSO and CDS terms", "url": "https://www.aavso.org/vsx/",
     "text": "This research has made use of the VizieR catalogue access tool, CDS, Strasbourg, "
             "France (DOI: 10.26093/cds/vizier)."},
    {"name": "WiseView (byw.tools / Backyard Worlds)", "provides": "the 'Open in WiseView' image blinker",
     "licence": "external tool, linked only", "url": "http://byw.tools/wiseview"},
]}


def export_v2_endpoints(api_dir):
    """Write api/metrics.json, model.json, credits.json and the deep-field
    results when the backend serves them; credits falls back to a static list.
    Voting, the vote queue, cone search and the candidate tables need the live
    backend and are not exported (the page labels them so)."""
    written = []
    try:
        from fastapi.testclient import TestClient
        client = TestClient(portal.app)
    except Exception as e:                      # httpx missing, app import quirk...
        print(f"   v2 endpoints skipped: {type(e).__name__}: {e}")
        client = None

    def get(path):
        if client is None:
            return None
        try:
            r = client.get(path)
        except Exception as e:
            print(f"   {path}: {type(e).__name__}: {e}")
            return None
        return r.json() if r.status_code == 200 else None

    def dump(name, body):
        path = os.path.join(api_dir, f"{name}.json")
        with open(path, "w") as f:
            json.dump(body, f)
        written.append(path)

    for name in ("metrics", "model"):
        body = get(f"/api/{name}")
        if body is None and name == "metrics" and os.path.exists(os.path.join("eval", "metrics.json")):
            with open(os.path.join("eval", "metrics.json")) as f:
                body = json.load(f)
        if body is not None:
            dump(name, body)
        else:
            print(f"   /api/{name} not available: the page shows its empty state")
    dump("credits", get("/api/credits") or CREDITS_FALLBACK)
    deep = get("/api/deep")
    if deep and deep.get("fields"):
        dump("deep", deep)
        for field in deep["fields"]:
            name = field.get("name")
            body = get(f"/api/deep/{name}") if name else None
            if body is not None:
                dump(f"deep-{name}", body)
    return written


def build(outdir="site", targets=None, with_recovery=True, with_figures=True,
          api_base="", pipeline_api_base=""):
    """Render the API responses for every cached target into outdir/."""
    target_index = portal.list_targets()["targets"]
    targets = targets or [t["name"] for t in target_index]
    if not targets:
        raise SystemExit("No cached targets: run step1_ingest.py or "
                         "make_demo_data.py first.")
    target_entries = {t["name"]: t for t in target_index}
    unknown = sorted(set(targets) - set(target_entries))
    if unknown:
        raise SystemExit(f"Unknown target(s): {', '.join(unknown)}")

    api_dir = os.path.join(outdir, "api")
    os.makedirs(api_dir, exist_ok=True)

    # Copy the frontend and flip it into static mode.  sw.js and assets/ go
    # too, so a bumped service-worker cache version reaches the deployed site.
    # static/dev/ (the ?mock=v2 files) is development-only and stays out.
    for fname in ("index.html", "style.css", "app.js", "sw.js"):
        shutil.copy(os.path.join("static", fname), os.path.join(outdir, fname))
    if os.path.isdir(os.path.join("static", "assets")):
        shutil.copytree(os.path.join("static", "assets"), os.path.join(outdir, "assets"),
                        dirs_exist_ok=True)
    index_path = os.path.join(outdir, "index.html")
    with open(index_path) as f:
        page = f.read()
    body_attrs = 'data-mode="static" data-api-dir="api"'
    if api_base:
        body_attrs += f' data-api-base="{html_lib.escape(api_base, quote=True)}"'
    if pipeline_api_base:
        body_attrs += (
            f' data-live-api="{html_lib.escape(pipeline_api_base.rstrip("/"), quote=True)}"')
    page = page.replace("<body>", f"<body {body_attrs}>", 1)
    with open(index_path, "w") as f:
        f.write(page)


    written = []
    mosaic_tiles = []
    for name in targets:
        print(f" {name}")
        dashboard = portal.get_dashboard(target=name)
        path = os.path.join(api_dir, f"dashboard-{name}.json")
        if isinstance(dashboard, portal.JSONResponse):
            try:
                body = json.loads(dashboard.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = {"error": f"Dashboard returned HTTP {dashboard.status_code}"}
            with open(path, "w") as f:
                json.dump(body, f)
            mosaic_tiles.append(portal.unavailable_mosaic_tile(
                target_entries[name], body.get("error", "dashboard unavailable")))
            print(f"   saved unavailable status: {body.get('error', 'dashboard unavailable')}")
        elif isinstance(dashboard, dict):
            with open(path, "w") as f:
                json.dump(dashboard, f)
            mosaic_tiles.append(portal.mosaic_tile(dashboard))
        else:
            body = {"error": "Dashboard returned an unexpected response"}
            with open(path, "w") as f:
                json.dump(body, f)
            mosaic_tiles.append(portal.unavailable_mosaic_tile(
                target_entries[name], body["error"]))
            print(f"   saved unavailable status: {body['error']}")
        written.append(path)

        if with_recovery:
            try:
                recovery = portal.get_recovery(target=name)
                path = os.path.join(api_dir, f"recovery-{name}.json")
                if isinstance(recovery, portal.JSONResponse):
                    try:
                        recovery = json.loads(recovery.body.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        recovery = {"error": f"Recovery returned HTTP {recovery.status_code}"}
                if not isinstance(recovery, dict):
                    recovery = {"error": "Recovery returned an unexpected response."}
                with open(path, "w") as f:
                    json.dump(recovery, f)
                written.append(path)
            except Exception as e:
                path = os.path.join(api_dir, f"recovery-{name}.json")
                with open(path, "w") as f:
                    json.dump({"error": f"Recovery export failed ({type(e).__name__}). "
                                         "See the export log."}, f)
                written.append(path)
                print(f"   recovery export failed: {type(e).__name__}: {e}")
        else:
            path = os.path.join(api_dir, f"recovery-{name}.json")
            with open(path, "w") as f:
                json.dump({"error": "Recovery grids were omitted from this export."}, f)
            written.append(path)

    # Use the dashboards rendered above instead of recomputing every field.
    try:
        mosaic = portal.mosaic_payload(mosaic_tiles)
        path = os.path.join(api_dir, "mosaic.json")
        with open(path, "w") as f:
            json.dump(mosaic, f)
        written.append(path)
    except Exception as e:
        print(f"   mosaic skipped: {e}")

    # Keep unavailable targets selectable so their saved error explains the gap.
    available = [target_entries[name] for name in targets]
    with open(os.path.join(api_dir, "targets.json"), "w") as f:
        json.dump({"targets": available}, f, indent=2)
    written.append(os.path.join(api_dir, "targets.json"))

    written.extend(export_v2_endpoints(api_dir))

    if with_figures:
        src = "figures_real"
        if not os.path.isdir(src):
            src = "figures"
        if os.path.isdir(src):
            dest = os.path.join(outdir, "figures")
            shutil.copytree(src, dest, dirs_exist_ok=True)
            written.append(dest)

    return written, available


def main():
    parser = argparse.ArgumentParser(
        description="Pre-render the dashboard into a static site.")
    parser.add_argument("--outdir", default="site")
    parser.add_argument("--targets", nargs="*", default=None)
    parser.add_argument("--no-recovery", action="store_true",
                        help="skip the (slow) injection-recovery grids")
    parser.add_argument("--no-figures", action="store_true")
    parser.add_argument("--api-base", default="",
                        help="proxy prefix for a live backend, e.g. /live-api "
                             "(see netlify.toml); enables live mode and "
                             "'open any field' on the deployed site")
    parser.add_argument("--pipeline-api-base", default="",
                        help="direct Cloud Run API base for long live requests, "
                             "e.g. https://service.run.app/api")
    args = parser.parse_args()

    print(f"Rendering static site into {args.outdir}/ ...")
    written, available = build(args.outdir, args.targets,
                               with_recovery=not args.no_recovery,
                               with_figures=not args.no_figures,
                               api_base=args.api_base,
                               pipeline_api_base=args.pipeline_api_base)

    total = sum(os.path.getsize(p) for p in written if os.path.isfile(p))
    print(f"\n{len(available)} target(s): "
          + ", ".join(t["common"] for t in available))
    print(f"{len(written)} artefacts, {total / 1e6:.1f} MB of JSON "
          f"-> {args.outdir}/")
    if args.api_base:
        print(f"Live backend proxied at {args.api_base}/ "
              "(set the redirect target in netlify.toml)")
    if args.pipeline_api_base:
        print(f"Long-running live requests go directly to "
              f"{args.pipeline_api_base.rstrip('/')}")
    print("\nDeploy:")
    print(f"  netlify deploy --prod --dir {args.outdir}")
    print(f"  (or set the GitHub Pages source to /{args.outdir})")


if __name__ == "__main__":
    main()
