"""Versioned Google Cloud Storage snapshots for the scheduled refresh pipeline."""

import hashlib
import json
import os
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone


# Project that owns the snapshot bucket.  Cloud Run knows its own project;
# a developer PC's application-default login usually does not.
DEFAULT_GCP_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT") or "spherex-lens"


# Objects under these prefixes are written once under the SHA-256 of their
# content (write_*_immutable), so a given name can never hold different bytes.
# They are safe to keep in memory indefinitely.  Mutable objects --
# current.json, refresh-status/latest.json, refresh.lock -- are never cached.
_IMMUTABLE_PREFIXES = ("manifests/", "snapshots/", "cache/", "cache-metadata/")


class _ImmutableObjectCache:
    """Thread-safe LRU of immutable object bytes, bounded by total size."""

    def __init__(self, max_bytes):
        self.max_bytes = max_bytes
        self._items = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            payload = self._items.get(key)
            if payload is not None:
                self._items.move_to_end(key)
            return payload

    def put(self, key, payload):
        if len(payload) > self.max_bytes:
            return
        with self._lock:
            if key in self._items:
                return
            self._items[key] = payload
            self._size += len(payload)
            while self._size > self.max_bytes:
                _, evicted = self._items.popitem(last=False)
                self._size -= len(evicted)


_OBJECT_CACHE = _ImmutableObjectCache(
    int(os.environ.get("SPHEREX_SNAPSHOT_CACHE_MB", "256")) * 1024 * 1024)


def _not_found_errors():
    try:
        from google.api_core.exceptions import NotFound
    except ImportError:          # tests without google-cloud installed
        return (KeyError, FileNotFoundError)
    return (NotFound, KeyError, FileNotFoundError)


class _FakePreconditionFailed(RuntimeError):
    """
    Stand-in for google.api_core.exceptions.PreconditionFailed used by tests.
    Real code imports the GCP class; tests use FakeBlob which raises this so
    that acquire_refresh_lock's except clause still fires correctly.
    """


class SnapshotStore:
    def __init__(self, bucket_name=None, client=None):
        bucket_name = bucket_name or os.environ.get("SPHEREX_SNAPSHOT_BUCKET")
        if not bucket_name:
            raise RuntimeError("SPHEREX_SNAPSHOT_BUCKET must be configured")
        if client is None:
            from google.cloud import storage
            client = storage.Client(project=DEFAULT_GCP_PROJECT)
        self.bucket = client.bucket(bucket_name)

    @staticmethod
    def _json_bytes(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")

    def _download(self, object_name):
        """Object bytes, or None when it does not exist.

        One GCS round trip (download, treating 404 as missing) instead of an
        exists() check followed by a download.  Immutable, content-addressed
        objects are then served from memory.
        """
        immutable = object_name.startswith(_IMMUTABLE_PREFIXES)
        key = (getattr(self.bucket, "name", None) or id(self.bucket), object_name)
        if immutable:
            cached = _OBJECT_CACHE.get(key)
            if cached is not None:
                return cached
        try:
            payload = self.bucket.blob(object_name).download_as_bytes()
        except _not_found_errors():
            return None
        if immutable:
            _OBJECT_CACHE.put(key, payload)
        return payload

    def read_json(self, object_name):
        payload = self._download(object_name)
        return None if payload is None else json.loads(payload)

    def write_json(self, object_name, value):
        payload = self._json_bytes(value)
        blob = self.bucket.blob(object_name)
        if blob.exists():
            blob.reload()
            generation = blob.generation
        else:
            generation = 0
        blob.upload_from_string(
            payload,
            content_type="application/json",
            if_generation_match=generation,
        )

    def read_bytes(self, object_name):
        return self._download(object_name)

    def read_current(self):
        # Never cached: this pointer is what moves when a snapshot publishes.
        # reload() fetches the generation (404 -> no snapshot yet); two round
        # trips instead of exists() + reload() + download.
        blob = self.bucket.blob("current.json")
        try:
            blob.reload()
        except _not_found_errors():
            return None, 0
        return json.loads(blob.download_as_bytes()), blob.generation

    def write_json_immutable(self, prefix, value):
        payload = self._json_bytes(value)
        digest = hashlib.sha256(payload).hexdigest()
        object_name = f"{prefix}/{digest}.json"
        blob = self.bucket.blob(object_name)
        self._write_immutable(blob, payload, "application/json")
        return object_name, digest

    def write_bytes_immutable(self, prefix, payload, content_type):
        digest = hashlib.sha256(payload).hexdigest()
        object_name = f"{prefix}/{digest}"
        blob = self.bucket.blob(object_name)
        self._write_immutable(blob, payload, content_type)
        return object_name, digest

    @staticmethod
    def _write_immutable(blob, payload, content_type):
        if blob.exists():
            return
        blob.upload_from_string(
            payload, content_type=content_type, if_generation_match=0)

    def publish(self, manifest, expected_generation):
        manifest_object, manifest_hash = self.write_json_immutable(
            "manifests", manifest)
        pointer = {
            "manifest": manifest_object,
            "manifest_sha256": manifest_hash,
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        blob = self.bucket.blob("current.json")
        blob.upload_from_string(
            self._json_bytes(pointer),
            content_type="application/json",
            if_generation_match=expected_generation,
        )
        return pointer

    def read_manifest(self, pointer):
        if not isinstance(pointer, dict) or not pointer.get("manifest"):
            return None
        return self.read_json(pointer["manifest"])

    def read_target_json(self, manifest, target, key):
        target_entry = (manifest or {}).get("targets", {}).get(target) or {}
        object_name = target_entry.get(key)
        return self.read_json(object_name) if object_name else None

    # B2 FIX: stale threshold is 30 minutes, not 36 hours.  The scheduler
    # runs every 12 hours, so a crashed job must not block two subsequent runs.
    # The heartbeat field is written every ~5 min by the worker thread; the
    # staleness check prefers heartbeat_at over started_at so a slow-starting
    # but still-alive job is never preempted.
    _LOCK_STALE_MINUTES = 30

    def _lock_age_seconds(self, lock_data):
        """Seconds since the last heartbeat (or start if none)."""
        ts = lock_data.get("heartbeat_at") or lock_data.get("started_at", "")
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return float("inf")
        return (datetime.now(timezone.utc) - t).total_seconds()

    def refresh_lock_is_active(self):
        blob = self.bucket.blob("refresh.lock")
        if not blob.exists():
            return False
        blob.reload()
        existing = json.loads(blob.download_as_bytes())
        age = self._lock_age_seconds(existing)
        return age < self._LOCK_STALE_MINUTES * 60

    def heartbeat_refresh_lock(self, lock):
        """
        Update the heartbeat_at field in the lock to prove the worker is alive.

        Returns the updated (blob, generation) tuple so the caller can replace
        its handle.  If the lock has already been deleted or overwritten
        (e.g. by an admin), returns the original lock unchanged and logs a
        warning — the worker should continue but the lock is no longer held.
        """
        try:
            from google.api_core.exceptions import PreconditionFailed, NotFound
        except ImportError:
            # Tests use the fake bucket which raises _FakePreconditionFailed.
            PreconditionFailed = NotFound = _FakePreconditionFailed

        blob, generation = lock
        try:
            blob.reload()
            existing = json.loads(blob.download_as_bytes())
        except Exception:
            return lock
        existing["heartbeat_at"] = datetime.now(timezone.utc).isoformat()
        try:
            blob.upload_from_string(
                self._json_bytes(existing),
                content_type="application/json",
                if_generation_match=generation,
            )
            blob.reload()
            return blob, blob.generation
        except (PreconditionFailed, NotFound, _FakePreconditionFailed):
            # Lock was taken over by a new run; our heartbeat is harmless but
            # we can no longer update it.
            return lock

    def acquire_refresh_lock(self):
        try:
            from google.api_core.exceptions import PreconditionFailed
        except ImportError:
            PreconditionFailed = _FakePreconditionFailed

        blob = self.bucket.blob("refresh.lock")
        now_iso = datetime.now(timezone.utc).isoformat()
        lock_data = {
            "token": uuid.uuid4().hex,
            "started_at": now_iso,
            "heartbeat_at": now_iso,
        }
        try:
            blob.upload_from_string(
                self._json_bytes(lock_data),
                content_type="application/json",
                if_generation_match=0,
            )
            blob.reload()
            return blob, blob.generation
        except (PreconditionFailed, _FakePreconditionFailed):
            blob.reload()
            existing = json.loads(blob.download_as_bytes())
            age = self._lock_age_seconds(existing)
            if age < self._LOCK_STALE_MINUTES * 60:
                raise RuntimeError(
                    "Another cloud refresh is already running")
            # The old run is stale (crashed/OOM-killed).  Mark it failed
            # before overwriting so /api/refresh-status reflects reality.
            try:
                self.write_json("refresh-status/latest.json", {
                    **(self.read_json("refresh-status/latest.json") or {}),
                    "status": "failed",
                    "completed_at": now_iso,
                    "error_code": "stale_lock_preempted",
                })
            except Exception:
                pass  # status update failure must not block the new run
            try:
                blob.upload_from_string(
                    self._json_bytes(lock_data),
                    content_type="application/json",
                    if_generation_match=blob.generation,
                )
            except (PreconditionFailed, _FakePreconditionFailed) as exc:
                raise RuntimeError(
                    "A stale refresh lock was changed concurrently") from exc
            blob.reload()
            return blob, blob.generation

    @staticmethod
    def release_refresh_lock(lock):
        try:
            from google.api_core.exceptions import NotFound, PreconditionFailed
        except ImportError:
            NotFound = PreconditionFailed = _FakePreconditionFailed

        blob, generation = lock
        try:
            blob.delete(if_generation_match=generation)
        except (NotFound, PreconditionFailed, _FakePreconditionFailed):
            return

    def collect_garbage(self, referenced_objects):
        referenced = set(referenced_objects)
        for prefix in ("manifests/", "snapshots/", "cache/", "cache-metadata/"):
            for blob in self.bucket.list_blobs(prefix=prefix):
                if blob.name not in referenced:
                    blob.delete(if_generation_match=blob.generation)
