"""
version.py
Single source of truth for "which code produced this result".

Every dashboard, snapshot, metrics file and model card carries
``pipeline_stamp()`` so a number on the page can always be traced back to the
pipeline version and the exact settings (config hash) that produced it.
"""
import hashlib
import json

PIPELINE_VERSION = "2.0.0-dev"


def config_hash():
    """Short, stable hash of every public setting in config.py."""
    import config

    settings = {k: getattr(config, k) for k in dir(config)
                if k.isupper() and not k.startswith("_")}
    blob = json.dumps(settings, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def pipeline_stamp():
    return {"pipeline_version": PIPELINE_VERSION, "config_hash": config_hash()}
