"""
export_static.py

OPTIONAL deployment helper -- NOT one of the ten blueprint files.

The app is a FastAPI service plus a vanilla-JS dashboard.  Render/Railway/Fly
can run the whole thing as a container (see Dockerfile), but Netlify, Vercel,
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
                              vercel --cwd site
                              (or point GitHub Pages at site/)
"""

import os
import sys
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
        "If you only want to DEPLOY, you do not need this script: site/ already\n"
        "contains the pre-rendered site (5 targets). Publish that folder.\n"
    )


def build(outdir="site", targets=None, with_recovery=True, with_figures=True,
          api_base=""):
    """Render the API responses for every cached target into outdir/."""
    targets = targets or [t["name"] for t in portal.list_targets()["targets"]]
    if not targets:
        raise SystemExit("No cached targets: run step1_ingest.py or "
                         "make_demo_data.py first.")

    api_dir = os.path.join(outdir, "api")
    os.makedirs(api_dir, exist_ok=True)

    # Copy the frontend and flip it into static mode.
    for fname in ("index.html", "style.css", "app.js"):
        shutil.copy(os.path.join("static", fname), os.path.join(outdir, fname))
    index_path = os.path.join(outdir, "index.html")
    with open(index_path) as f:
        html = f.read()
    body_attrs = 'data-mode="static" data-api-dir="api"'
    if api_base:
        body_attrs += f' data-api-base="{api_base}"'
    html = html.replace("<body>", f"<body {body_attrs}>", 1)
    with open(index_path, "w") as f:
        f.write(html)


    written = []
    for name in targets:
        print(f" {name}")
        dashboard = portal.get_dashboard(target=name)
        if not isinstance(dashboard, dict):
            print(f"   skipped: {getattr(dashboard, 'body', dashboard)}")
            continue
        path = os.path.join(api_dir, f"dashboard-{name}.json")
        with open(path, "w") as f:
            json.dump(dashboard, f)
        written.append(path)

        if with_recovery:
            try:
                recovery = portal.get_recovery(target=name)
                if isinstance(recovery, dict):
                    path = os.path.join(api_dir, f"recovery-{name}.json")
                    with open(path, "w") as f:
                        json.dump(recovery, f)
                    written.append(path)
            except Exception as e:
                print(f"   recovery skipped: {e}")

    # All-sky tiles (one small thumbnail per field) for the sky view.
    try:
        mosaic = portal.get_mosaic(thumb=48, refresh=0)
        path = os.path.join(api_dir, "mosaic.json")
        with open(path, "w") as f:
            json.dump(mosaic, f)
        written.append(path)
    except Exception as e:
        print(f"   mosaic skipped: {e}")

    # The target index lists only what was actually rendered.
    available = [t for t in portal.list_targets()["targets"] if t["name"] in targets]
    with open(os.path.join(api_dir, "targets.json"), "w") as f:
        json.dump({"targets": available}, f, indent=2)
    written.append(os.path.join(api_dir, "targets.json"))

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
    args = parser.parse_args()

    if os.path.isdir(args.outdir):
        shutil.rmtree(args.outdir)

    print(f"Rendering static site into {args.outdir}/ ...")
    written, available = build(args.outdir, args.targets,
                               with_recovery=not args.no_recovery,
                               with_figures=not args.no_figures,
                               api_base=args.api_base)

    total = sum(os.path.getsize(p) for p in written if os.path.isfile(p))
    print(f"\n{len(available)} target(s): "
          + ", ".join(t["common"] for t in available))
    print(f"{len(written)} artefacts, {total / 1e6:.1f} MB of JSON "
          f"-> {args.outdir}/")
    if args.api_base:
        print(f"Live backend proxied at {args.api_base}/ "
              "(set the redirect target in netlify.toml)")
    print("\nDeploy:")
    print(f"  netlify deploy --prod --dir {args.outdir}")
    print(f"  vercel --cwd {args.outdir}")
    print(f"  (or set the GitHub Pages source to /{args.outdir})")


if __name__ == "__main__":
    main()
