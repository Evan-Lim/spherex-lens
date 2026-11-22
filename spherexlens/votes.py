"""
spherexlens.votes
Public real/bogus votes on detections (Phase 9 / Phase 5 active learning).

    record_vote(target, detection_id, vote, client_key) -> dict
    load_votes() -> list[dict]     # {"detection_id","target","vote","ts","client"}

``load_votes`` returns one vote per (client, detection): the latest wins.
``client`` is a salted SHA-256 of the visitor address, never the address.

Storage backends
----------------
local (default)  JSON lines appended to ``$SPHEREX_CACHE/votes/votes-YYYYMMDD.jsonl``
                 under a process lock.  Fine for a laptop or a single VM.
gcs              ``SPHEREX_VOTES_BUCKET`` set.  Every vote is its own object,
                 ``votes/v1/YYYY-MM-DD/<ts_us>-<uuid>.json``, written with
                 ``if_generation_match=0`` (create-only), so concurrent writers
                 on any number of Cloud Run instances can never overwrite each
                 other and the API service account needs no delete/update
                 permission.  Objects are immutable, so ``load_votes`` downloads
                 each one once per process and afterwards only lists new names.

Salt: ``SPHEREX_VOTES_SALT`` when set; otherwise a random salt is created once
and kept next to the votes (``votes/.salt`` locally, ``salt/v1.txt`` in the
bucket, created with a generation precondition so all instances agree).
"""
import json
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone

VALID_VOTES = ("real", "bogus", "unsure")
MAX_TARGET_LEN = 80
MAX_DETECTION_ID_LEN = 200
TARGET_RE = re.compile(r"[A-Za-z0-9_]{1,80}\Z")
# "<target>:<pair>:<y>,<x>" as app.get_dashboard builds it, e.g.
# "WISE_0855:E1→E3:91,88".  The arrow may also be sent as "->".
_NUM = r"-?\d{1,5}(?:\.\d{1,8})?"
DETECTION_ID_RE = re.compile(
    r"(?P<target>[A-Za-z0-9_]{1,80}):(?P<pair>E\d{1,3}(?:→|->)E\d{1,3}):"
    rf"(?P<y>{_NUM}),(?P<x>{_NUM})\Z")


class VoteError(ValueError):
    """Invalid vote input (the route turns it into HTTP 400)."""


class VotesNotConfigured(RuntimeError):
    """No durable votes backend in production (HTTP 503)."""


def normalise_detection_id(detection_id):
    return detection_id.replace("->", "→")


def validate(target, detection_id, vote):
    """Return the cleaned (target, detection_id, vote) or raise VoteError."""
    if not isinstance(target, str) or not TARGET_RE.fullmatch(target):
        raise VoteError("target must be 1-80 letters, digits or underscores.")
    if (not isinstance(detection_id, str) or not detection_id
            or len(detection_id) > MAX_DETECTION_ID_LEN):
        raise VoteError("detection_id is missing or too long.")
    match = DETECTION_ID_RE.fullmatch(detection_id)
    if not match:
        raise VoteError('detection_id must look like "<target>:E1→E2:<y>,<x>".')
    if match.group("target") != target:
        raise VoteError("detection_id does not belong to target.")
    if not isinstance(vote, str) or vote.lower() not in VALID_VOTES:
        raise VoteError("vote must be one of: real, bogus, unsure.")
    return target, normalise_detection_id(detection_id), vote.lower()


def _utc_now():
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# backends
# --------------------------------------------------------------------------
class LocalVoteStore:
    name = "local"

    def __init__(self, directory=None):
        cache = os.environ.get("SPHEREX_CACHE", "data_cache")
        self.directory = directory or os.path.join(cache, "votes")
        self._lock = threading.Lock()

    def salt(self):
        env = os.environ.get("SPHEREX_VOTES_SALT")
        if env:
            return env
        path = os.path.join(self.directory, ".salt")
        with self._lock:
            os.makedirs(self.directory, exist_ok=True)
            if not os.path.exists(path):
                try:
                    with open(path, "x", encoding="utf-8") as fh:
                        fh.write(secrets.token_hex(16))
                except FileExistsError:
                    pass
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()

    def append(self, record):
        day = record["ts"][:10].replace("-", "")
        path = os.path.join(self.directory, f"votes-{day}.jsonl")
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self._lock:
            os.makedirs(self.directory, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def records(self):
        if not os.path.isdir(self.directory):
            return []
        out = []
        for name in sorted(os.listdir(self.directory)):
            if not (name.startswith("votes-") and name.endswith(".jsonl")):
                continue
            with open(os.path.join(self.directory, name), encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue            # a torn last line is skipped
        return out


class GcsVoteStore:
    name = "gcs"
    PREFIX = "votes/v1/"
    SALT_OBJECT = "salt/v1.txt"

    def __init__(self, bucket_name, client=None):
        if client is None:
            from google.cloud import storage
            from snapshot_store import DEFAULT_GCP_PROJECT
            client = storage.Client(project=DEFAULT_GCP_PROJECT)
        self.bucket = client.bucket(bucket_name)
        self._seen = {}                 # object name -> record (immutable)
        self._salt = None
        self._lock = threading.Lock()

    @staticmethod
    def _precondition_errors():
        from snapshot_store import _FakePreconditionFailed
        try:
            from google.api_core.exceptions import PreconditionFailed
            return (PreconditionFailed, _FakePreconditionFailed)
        except ImportError:
            return (_FakePreconditionFailed,)

    def salt(self):
        env = os.environ.get("SPHEREX_VOTES_SALT")
        if env:
            return env
        if self._salt:
            return self._salt
        blob = self.bucket.blob(self.SALT_OBJECT)
        try:
            blob.upload_from_string(secrets.token_hex(16).encode(),
                                    content_type="text/plain",
                                    if_generation_match=0)
        except self._precondition_errors():
            pass                        # another instance created it first
        self._salt = self.bucket.blob(self.SALT_OBJECT).download_as_bytes().decode().strip()
        return self._salt

    def append(self, record):
        ts_us = int(datetime.fromisoformat(record["ts"]).timestamp() * 1_000_000)
        name = f"{self.PREFIX}{record['ts'][:10]}/{ts_us:016d}-{uuid.uuid4().hex}.json"
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True).encode()
        self.bucket.blob(name).upload_from_string(
            payload, content_type="application/json", if_generation_match=0)
        with self._lock:
            self._seen[name] = record

    def records(self):
        names = [blob.name for blob in self.bucket.list_blobs(prefix=self.PREFIX)]
        for name in names:
            if name in self._seen:
                continue
            try:
                record = json.loads(self.bucket.blob(name).download_as_bytes())
            except Exception:
                continue
            with self._lock:
                self._seen[name] = record
        with self._lock:
            return [self._seen[n] for n in sorted(names) if n in self._seen]


_STORE = {"value": None}
_STORE_LOCK = threading.Lock()


def get_store():
    """The configured backend (GCS when SPHEREX_VOTES_BUCKET is set).

    Raises VotesNotConfigured in production without a bucket: Cloud Run's
    disk is per-instance and wiped on restart, so local votes would be lost.
    """
    with _STORE_LOCK:
        if _STORE["value"] is not None:
            return _STORE["value"]
        bucket = os.environ.get("SPHEREX_VOTES_BUCKET", "").strip()
        if bucket:
            _STORE["value"] = GcsVoteStore(bucket)
        else:
            import config
            if config.Production.ENABLED and os.environ.get(
                    "SPHEREX_VOTES_ALLOW_LOCAL", "").lower() not in {"1", "true", "yes"}:
                raise VotesNotConfigured(
                    "Voting is not configured on this server "
                    "(SPHEREX_VOTES_BUCKET is not set).")
            _STORE["value"] = LocalVoteStore()
        return _STORE["value"]


def set_store(store):
    """Inject a backend (tests) or reset with None."""
    with _STORE_LOCK:
        _STORE["value"] = store
    _CACHE.update(t=0.0, votes=None)


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def record_vote(target, detection_id, vote, client_key):
    """Validate and store one vote; returns the stored record.

    ``client_key`` is the raw visitor key (e.g. IP); only its salted hash is
    stored.  A later vote by the same client on the same detection replaces
    the earlier one when votes are loaded.
    """
    target, detection_id, vote = validate(target, detection_id, vote)
    if not isinstance(client_key, str) or not client_key:
        raise VoteError("client_key is required.")
    store = get_store()
    from spherexlens.http_guard import hashed_client
    record = {
        "detection_id": detection_id,
        "target": target,
        "vote": vote,
        "ts": _utc_now().isoformat(timespec="microseconds"),
        "client": hashed_client(client_key[:256], salt=store.salt()),
    }
    store.append(record)
    _CACHE.update(t=0.0, votes=None)
    return record


_CACHE = {"t": 0.0, "votes": None}
_CACHE_TTL_S = 30.0


def load_votes():
    """All votes, deduplicated: latest vote per (client, detection_id).

    Sorted oldest first.  Each item: {"detection_id","target","vote","ts","client"}.
    """
    now = time.monotonic()
    if _CACHE["votes"] is not None and now - _CACHE["t"] < _CACHE_TTL_S:
        return [dict(v) for v in _CACHE["votes"]]
    try:
        store = get_store()
    except VotesNotConfigured:
        return []
    latest = {}
    for record in store.records():
        if not isinstance(record, dict):
            continue
        try:
            target, detection_id, vote = validate(
                record.get("target"), record.get("detection_id"), record.get("vote"))
        except VoteError:
            continue
        client, ts = record.get("client"), record.get("ts")
        if not isinstance(client, str) or not isinstance(ts, str):
            continue
        key = (client, detection_id)
        if key not in latest or ts >= latest[key]["ts"]:
            latest[key] = {"detection_id": detection_id, "target": target,
                           "vote": vote, "ts": ts, "client": client}
    votes = sorted(latest.values(), key=lambda v: (v["ts"], v["detection_id"]))
    _CACHE.update(t=now, votes=votes)
    return [dict(v) for v in votes]


def tally(votes=None):
    """{detection_id: {"real": n, "bogus": n, "unsure": n}}"""
    out = {}
    for v in votes if votes is not None else load_votes():
        counts = out.setdefault(v["detection_id"], dict.fromkeys(VALID_VOTES, 0))
        counts[v["vote"]] += 1
    return out
