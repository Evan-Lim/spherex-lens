"""
Sync the deployed static shell (site/) with the development shell (static/).

`export_static.py` does this as part of a full re-render, but that also rewrites
every api/*.json file (minutes of pipeline work).  When only the HTML/CSS/JS or
the service worker changed, run this instead:

    python scripts/sync_site_shell.py

It copies index.html, style.css, app.js and sw.js, flips <body> into static
mode, and keeps the deployment's existing data-api-base/data-live-api values
unless --api-base / --pipeline-api-base are given.  Re-deploy with
`netlify deploy --prod --dir site` afterwards.
"""
import argparse
import html as html_lib
import os
import re
import shutil


SHELL_FILES = ("index.html", "style.css", "app.js", "sw.js")
ATTR = r'{name}="([^"]*)"'


def existing_attribute(page, name):
    match = re.search(ATTR.format(name=re.escape(name)), page)
    return match.group(1) if match else ""


def sync(outdir="site", api_base=None, pipeline_api_base=None, source="static"):
    source_index = os.path.join(source, "index.html")
    if not os.path.isfile(source_index):
        raise SystemExit(f"missing {source_index}")

    deployed_index = os.path.join(outdir, "index.html")
    current = ""
    if os.path.isfile(deployed_index):
        with open(deployed_index, encoding="utf-8") as file:
            current = file.read()

    if api_base is None:
        api_base = existing_attribute(current, "data-api-base") or "/live-api"
    if pipeline_api_base is None:
        pipeline_api_base = existing_attribute(current, "data-live-api")

    os.makedirs(outdir, exist_ok=True)
    for name in SHELL_FILES:
        shutil.copy(os.path.join(source, name), os.path.join(outdir, name))
    assets = os.path.join(source, "assets")
    if os.path.isdir(assets):
        shutil.copytree(assets, os.path.join(outdir, "assets"), dirs_exist_ok=True)

    with open(source_index, encoding="utf-8") as file:
        page = file.read()
    attrs = 'data-mode="static" data-api-dir="api"'
    if api_base:
        attrs += f' data-api-base="{html_lib.escape(api_base, quote=True)}"'
    if pipeline_api_base:
        attrs += (' data-live-api="' +
                  html_lib.escape(pipeline_api_base.rstrip("/"), quote=True) + '"')
    if "<body>" not in page:
        raise SystemExit("static/index.html has no plain <body> tag to annotate")
    page = page.replace("<body>", f"<body {attrs}>", 1)
    with open(deployed_index, "w", encoding="utf-8") as file:
        file.write(page)
    return os.path.join(outdir, "index.html")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", default="site")
    parser.add_argument("--source", default="static")
    parser.add_argument("--api-base", default=None,
                        help="proxy prefix (default: keep the deployed value, "
                             "or /live-api)")
    parser.add_argument("--pipeline-api-base", default=None,
                        help="direct API base for long live requests "
                             "(default: keep the deployed value)")
    args = parser.parse_args()
    path = sync(args.outdir, args.api_base, args.pipeline_api_base, args.source)
    print(f"synced static shell -> {path}")


if __name__ == "__main__":
    main()
