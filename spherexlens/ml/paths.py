"""Repository-relative locations of eval / ml artefacts (cwd-independent)."""
import os

import config

ROOT = os.path.dirname(os.path.abspath(config.__file__))


def resolve(path):
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def model_dir():
    return resolve(config.ML_MODEL_DIR)


def data_dir():
    return resolve(config.ML_DATA_DIR)


def eval_dir():
    return resolve(config.EVAL_DIR)


def feature_cache_path():
    return os.path.join(data_dir(), "feature_cache.jsonl")
