import pytest

from snapshot_store import SnapshotStore


class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket = bucket
        self.name = name
        self.generation = None

    def exists(self):
        return self.name in self.bucket.objects

    def upload_from_string(self, payload, content_type=None,
                           if_generation_match=None):
        current = self.bucket.objects.get(self.name)
        if if_generation_match == 0 and current is not None:
            from snapshot_store import _FakePreconditionFailed
            raise _FakePreconditionFailed("generation mismatch")
        if (if_generation_match not in (None, 0)
                and (current is None or current[1] != if_generation_match)):
            from snapshot_store import _FakePreconditionFailed
            raise _FakePreconditionFailed("generation mismatch")
        generation = 1 if current is None else current[1] + 1
        self.bucket.objects[self.name] = (bytes(payload), generation)
        self.generation = generation

    def reload(self):
        _, self.generation = self.bucket.objects[self.name]

    def download_as_bytes(self):
        return self.bucket.objects[self.name][0]

    def delete(self, if_generation_match=None):
        current = self.bucket.objects.get(self.name)
        if current is None or current[1] != if_generation_match:
            from snapshot_store import _FakePreconditionFailed
            raise _FakePreconditionFailed("generation mismatch")
        del self.bucket.objects[self.name]


class FakeBucket:
    def __init__(self):
        self.objects = {}

    def blob(self, name):
        return FakeBlob(self, name)

    def list_blobs(self, prefix):
        blobs = []
        for name in sorted(self.objects):
            if not name.startswith(prefix):
                continue
            blob = FakeBlob(self, name)
            blob.generation = self.objects[name][1]
            blobs.append(blob)
        return blobs


class FakeClient:
    def __init__(self):
        self.fake_bucket = FakeBucket()

    def bucket(self, name):
        assert name == "test-bucket"
        return self.fake_bucket


@pytest.fixture
def store():
    return SnapshotStore("test-bucket", client=FakeClient())


def test_snapshot_publication_is_readable(store):
    dashboard = {"target": "Field_A", "detections": []}
    dashboard_object, _ = store.write_json_immutable(
        "snapshots/dashboards/Field_A", dashboard)
    recovery_object, _ = store.write_json_immutable(
        "snapshots/recovery/Field_A", {"target": "Field_A", "grid": []})
    manifest = {
        "targets": {
            "Field_A": {
                "dashboard": dashboard_object,
                "recovery": recovery_object,
            }
        }
    }

    pointer = store.publish(manifest, expected_generation=0)

    current, generation = store.read_current()
    published = store.read_manifest(current)
    assert generation == 1
    assert published == manifest
    assert store.read_target_json(published, "Field_A", "dashboard") == dashboard


def test_mutable_status_json_is_versioned_on_each_write(store):
    store.write_json("refresh-status/latest.json", {"status": "running"})
    store.write_json("refresh-status/latest.json", {"status": "succeeded"})

    assert store.read_json("refresh-status/latest.json") == {
        "status": "succeeded",
    }


def test_pointer_generation_prevents_stale_publication(store):
    first = store.publish({"revision": 1}, expected_generation=0)
    with pytest.raises(RuntimeError, match="generation mismatch"):
        store.publish({"revision": 2}, expected_generation=0)

    current, generation = store.read_current()
    assert current == first
    assert generation == 1
    assert store.read_manifest(current) == {"revision": 1}


def test_garbage_collection_keeps_only_referenced_objects(store):
    kept, _ = store.write_json_immutable("snapshots/keep", {"ok": True})
    removed, _ = store.write_json_immutable("snapshots/remove", {"old": True})

    store.collect_garbage({kept})

    assert store.read_json(kept) == {"ok": True}
    assert store.read_json(removed) is None


def test_snapshot_api_reads_published_registered_field(store, monkeypatch):
    import app as portal

    dashboard = {"target": "Field_A", "source": "scheduled"}
    recovery = {"target": "Field_A", "grid": []}
    mosaic = {"tiles": [{"target": "Field_A"}], "n_fields": 1}
    targets = {"targets": [{"name": "Field_A", "common": "Field A"}]}
    target_meta = {"meta": {"ra": 10.0, "dec": 20.0}, "catalogs": {}}
    dashboard_object, _ = store.write_json_immutable(
        "snapshots/dashboards/Field_A", dashboard)
    recovery_object, _ = store.write_json_immutable(
        "snapshots/recovery/Field_A", recovery)
    mosaic_object, _ = store.write_json_immutable("snapshots/mosaic", mosaic)
    targets_object, _ = store.write_json_immutable("snapshots/targets", targets)
    meta_object, _ = store.write_json_immutable(
        "cache-metadata/Field_A", target_meta)
    store.publish({
        "targets": {
            "Field_A": {
                "dashboard": dashboard_object,
                "recovery": recovery_object,
                "meta_object": meta_object,
            }
        },
        "mosaic": mosaic_object,
        "targets_object": targets_object,
    }, expected_generation=0)

    monkeypatch.setattr(portal, "SNAPSHOT_BUCKET", "test-bucket")
    monkeypatch.setattr(portal, "_SNAPSHOT_STORE", store)
    monkeypatch.setenv("SPHEREX_SNAPSHOT_READS", "true")

    assert portal.list_targets() == targets
    assert portal.get_dashboard(target="Field_A", source="cache") == dashboard
    assert portal.get_recovery(target="Field_A", source="cache") == recovery
    assert portal.get_mosaic(thumb=48, refresh=0, source="cache") == mosaic

    def failed_live_fetch(*args, **kwargs):
        raise RuntimeError("IRSA is unavailable")

    monkeypatch.setattr(portal, "live_exposures", failed_live_fetch)
    live_response = portal.get_dashboard(target="Field_A", source="live")
    assert live_response.status_code == 502
    assert b"IRSA is unavailable" in live_response.body


def _asgi_get(asgi_app, path, query=""):
    """One GET through the full ASGI stack (middleware, routing, threadpool)."""
    import asyncio
    import json

    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": query.encode(), "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 5000), "server": ("testserver", 80),
    }
    asyncio.run(asgi_app(scope, receive, send))
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages
                    if m["type"] == "http.response.body")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return start["status"], headers, json.loads(body)


def test_snapshot_responses_name_the_object_they_carry(store, monkeypatch):
    import app as portal

    dashboard = {"target": "Field_A", "source": "scheduled"}
    recovery = {"target": "Field_A", "grid": [{"snr": 3, "mu": 1, "recovery": 0.5}]}
    mosaic = {"tiles": [{"target": "Field_A"}], "n_fields": 1}
    targets = {"targets": [{"name": "Field_A", "common": "Field A"}]}
    dashboard_object, _ = store.write_json_immutable(
        "snapshots/dashboards/Field_A", dashboard)
    recovery_object, _ = store.write_json_immutable(
        "snapshots/recovery/Field_A", recovery)
    mosaic_object, _ = store.write_json_immutable("snapshots/mosaic", mosaic)
    targets_object, _ = store.write_json_immutable("snapshots/targets", targets)
    store.publish({
        "targets": {"Field_A": {"dashboard": dashboard_object,
                                "recovery": recovery_object}},
        "mosaic": mosaic_object,
        "targets_object": targets_object,
    }, expected_generation=0)
    monkeypatch.setattr(portal, "SNAPSHOT_BUCKET", "test-bucket")
    monkeypatch.setattr(portal, "_SNAPSHOT_STORE", store)
    monkeypatch.setenv("SPHEREX_SNAPSHOT_READS", "true")
    portal._MANIFEST_CACHE.update({"t": 0.0, "pointer": None, "manifest": None})

    status, headers, index = _asgi_get(portal.app, "/api/snapshot-index")
    assert status == 200
    manifest_name = index["manifest"]
    assert manifest_name and headers["x-snapshot-manifest"] == manifest_name
    assert headers["cache-control"].startswith("no-store")
    assert index["targets_object"] == targets_object
    assert index["mosaic"] == mosaic_object
    assert index["fields"] == {"Field_A": {"dashboard": dashboard_object,
                                           "recovery": recovery_object}}

    for path, query, expected_object, expected_body in (
            ("/api/dashboard", "target=Field_A", dashboard_object, dashboard),
            ("/api/recovery", "target=Field_A", recovery_object, recovery),
            ("/api/mosaic", "", mosaic_object, mosaic),
            ("/api/targets", "", targets_object, targets)):
        status, headers, body = _asgi_get(portal.app, path, query)
        assert status == 200, path
        assert headers["x-snapshot-object"] == expected_object, path
        assert body == expected_body, path

    status, headers, body = _asgi_get(portal.app, "/api/status", "target=Field_A")
    assert status == 200
    assert headers["x-snapshot-manifest"] == manifest_name
    assert "x-snapshot-object" not in headers

    # Live responses are never labelled: they are not snapshot objects.
    monkeypatch.setattr(portal, "live_exposures",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    status, headers, _ = _asgi_get(portal.app, "/api/dashboard",
                                   "target=Field_A&source=live")
    assert "x-snapshot-object" not in headers

    # Internal callers still receive the plain payload.
    assert portal.get_dashboard(target="Field_A", source="cache") == dashboard
    assert portal.get_snapshot_index()["manifest"] == manifest_name


def test_snapshot_history_lists_retained_versions_and_serves_each(store, monkeypatch):
    """The newest dashboard stays default; older manifests stay selectable."""
    from datetime import datetime, timedelta, timezone
    import app as portal

    old_dashboard = {"target": "Field_A", "version": "old", "detections": []}
    new_dashboard = {"target": "Field_A", "version": "new", "detections": []}
    old_object, _ = store.write_json_immutable(
        "snapshots/dashboards/Field_A", old_dashboard)
    new_object, _ = store.write_json_immutable(
        "snapshots/dashboards/Field_A", new_dashboard)
    now = datetime.now(timezone.utc)
    old_pointer = store.publish({
        "schema_version": 1,
        "created_at": (now - timedelta(hours=20)).isoformat(),
        "targets": {"Field_A": {"dashboard": old_object}},
    }, expected_generation=0)
    new_pointer = store.publish({
        "schema_version": 1,
        "created_at": (now - timedelta(hours=8)).isoformat(),
        "targets": {"Field_A": {"dashboard": new_object}},
        "previous_manifest": old_pointer["manifest"],
    }, expected_generation=1)

    monkeypatch.setattr(portal, "SNAPSHOT_BUCKET", "test-bucket")
    monkeypatch.setattr(portal, "_SNAPSHOT_STORE", store)
    monkeypatch.setitem(portal._SNAPSHOT_MODE, "value", "bucket")
    monkeypatch.setenv("SPHEREX_SNAPSHOT_READS", "true")
    portal._MANIFEST_CACHE.update({"t": 0.0, "pointer": None, "manifest": None})

    history = portal.get_snapshot_history(target="Field_A")
    assert [item["dashboard"] for item in history["snapshots"]] == [
        new_object, old_object]
    assert history["latest"] == new_pointer["manifest"]
    assert history["snapshots"][0]["latest"] is True
    assert history["snapshots"][1]["latest"] is False
    assert history["snapshots"][1]["order"] == 0      # oldest first for playback

    status, headers, body = _asgi_get(portal.app, "/api/snapshot-history",
                                      "target=Field_A")
    assert status == 200
    assert body["snapshots"][0]["manifest"] == new_pointer["manifest"]
    assert headers["x-snapshot-manifest"] == new_pointer["manifest"]
    assert headers["cache-control"].startswith("no-store")

    # The default route still returns the newest published dashboard.
    status, headers, body = _asgi_get(portal.app, "/api/dashboard",
                                      "target=Field_A")
    assert status == 200
    assert body == new_dashboard
    assert headers["x-snapshot-object"] == new_object

    # A specific retained manifest returns that version, labelled so the
    # browser can cache it by immutable object name.
    status, headers, body = _asgi_get(
        portal.app, "/api/dashboard",
        f"target=Field_A&snapshot={old_pointer['manifest']}")
    assert status == 200
    assert body == old_dashboard
    assert headers["x-snapshot-object"] == old_object
    assert headers["x-snapshot-manifest"] == old_pointer["manifest"]
    assert headers["cache-control"].startswith("no-store")

    # Unknown/expired manifests are rejected rather than reading any object.
    status, _, body = _asgi_get(
        portal.app, "/api/dashboard",
        "target=Field_A&snapshot=manifests/" + "0" * 64 + ".json")
    assert status == 404
    assert "not retained" in body["detail"]


def test_snapshot_history_rejects_a_live_combination(store, monkeypatch):
    import app as portal

    monkeypatch.setattr(portal, "SNAPSHOT_BUCKET", "test-bucket")
    monkeypatch.setattr(portal, "_SNAPSHOT_STORE", store)
    monkeypatch.setitem(portal._SNAPSHOT_MODE, "value", "bucket")
    monkeypatch.setenv("SPHEREX_SNAPSHOT_READS", "true")

    with pytest.raises(portal.HTTPException) as error:
        portal.get_dashboard(target="Field_A", source="live",
                             snapshot="manifests/" + "a" * 64 + ".json")
    assert error.value.status_code == 400


def test_snapshot_status_distinguishes_check_processing_and_refresh(store, monkeypatch):
    import app as portal

    dashboard_object, _ = store.write_json_immutable(
        "snapshots/dashboards/Field_A", {"target": "Field_A"})
    neowise_object, _ = store.write_json_immutable(
        "snapshots/neowise/Field_A",
        {"target": "Field_A", "generated_utc": "2026-11-05T08:00:00Z"})
    store.publish({
        "targets": {
            "Field_A": {
                "dashboard": dashboard_object,
                "neowise_object": neowise_object,
                "catalogs_checked_at": "2026-11-04T08:00:00+00:00",
                "last_checked_at": "2026-11-07T08:00:00+00:00",
                "last_updated_at": "2026-11-06T08:00:00+00:00",
                "last_check": "unchanged",
            }
        }
    }, expected_generation=0)
    store.write_json("refresh-status/latest.json", {
        "status": "failed",
        "started_at": "2026-11-07T08:00:00+00:00",
        "completed_at": "2026-11-07T08:01:00+00:00",
        "error_code": "RuntimeError",
        "internal_detail": "must not be exposed",
    })
    monkeypatch.setattr(portal, "_snapshot_store", lambda: store)
    # Reset the manifest cache so this test's store is used.
    monkeypatch.setattr(portal, "_MANIFEST_CACHE",
                        {"t": 0.0, "pointer": None, "manifest": None})

    status = portal.get_status("Field_A")

    assert status["snapshot_published_at"]
    assert status["field_last_checked_at"] == "2026-11-07T08:00:00+00:00"
    assert status["field_last_processed_at"] == "2026-11-06T08:00:00+00:00"
    assert status["catalogs_last_checked_at"] == "2026-11-04T08:00:00+00:00"
    assert status["neowise_last_updated_at"] == "2026-11-05T08:00:00Z"
    assert status["refresh_attempt"]["status"] == "failed"
    assert "internal_detail" not in status["refresh_attempt"]


def test_refresh_status_is_sanitized_and_reports_snapshot_time(
        store, monkeypatch):
    import app as portal

    dashboard_object, _ = store.write_json_immutable(
        "snapshots/dashboards/Field_A", {"target": "Field_A"})
    pointer = store.publish({
        "targets": {"Field_A": {"dashboard": dashboard_object}},
    }, expected_generation=0)
    store.write_json("refresh-status/latest.json", {
        "status": "running",
        "started_at": "2026-11-07T08:00:00Z",
        "total_targets": 18,
        "completed_targets": 3,
        "current_target": "Field_D",
        "current_target_index": 4,
        "current_target_state": "checking",
        "updated_count": 1,
        "unchanged_count": 2,
        "failed_count": 0,
        "internal_detail": "must not be exposed",
    })
    monkeypatch.setattr(portal, "_snapshot_store", lambda: store)
    monkeypatch.setenv("SPHEREX_REFRESH_OPERATOR_TOKEN", "operator-token")
    monkeypatch.setenv("SPHEREX_REFRESH_PROJECT", "test-project")
    monkeypatch.setenv("SPHEREX_REFRESH_REGION", "us-central1")
    monkeypatch.setenv("SPHEREX_REFRESH_JOB", "spherex-refresh")

    status = portal.get_refresh_status()

    assert status["status"] == "running"
    assert status["completed_targets"] == 3
    assert status["current_target"] == "Field_D"
    assert status["snapshot_published_at"] == pointer["published_at"]
    assert status["manual_refresh_available"] is True
    assert "internal_detail" not in status


def test_refresh_trigger_requires_operator_token_and_invokes_job(
        store, monkeypatch):
    from types import SimpleNamespace

    import app as portal

    monkeypatch.setattr(portal, "_snapshot_store", lambda: store)
    monkeypatch.setenv("SPHEREX_REFRESH_OPERATOR_TOKEN", "operator-token")
    monkeypatch.setenv("SPHEREX_REFRESH_PROJECT", "test-project")
    monkeypatch.setenv("SPHEREX_REFRESH_REGION", "us-central1")
    monkeypatch.setenv("SPHEREX_REFRESH_JOB", "spherex-refresh")
    calls = []
    monkeypatch.setattr(
        portal, "_invoke_cloud_refresh_job",
        lambda: calls.append(True) or "operations/refresh-1")

    with pytest.raises(portal.HTTPException) as error:
        portal.trigger_cloud_refresh(
            SimpleNamespace(headers={"x-refresh-token": "wrong-token"}))
    assert error.value.status_code == 401
    assert not calls

    accepted = portal.trigger_cloud_refresh(
        SimpleNamespace(headers={"x-refresh-token": "operator-token"}))
    assert accepted["status"] == "accepted"
    assert accepted["operation"] == "operations/refresh-1"
    assert calls == [True]

    lock = store.acquire_refresh_lock()
    try:
        with pytest.raises(portal.HTTPException) as error:
            portal.trigger_cloud_refresh(
                SimpleNamespace(headers={"x-refresh-token": "operator-token"}))
        assert error.value.status_code == 409
        assert calls == [True]
    finally:
        store.release_refresh_lock(lock)


def test_refresh_job_invoker_uses_authorized_cloud_run_api(monkeypatch):
    import google.auth
    import google.auth.transport.requests

    import app as portal

    monkeypatch.setenv("SPHEREX_REFRESH_PROJECT", "test-project")
    monkeypatch.setenv("SPHEREX_REFRESH_REGION", "us-central1")
    monkeypatch.setenv("SPHEREX_REFRESH_JOB", "spherex-refresh")
    credentials = object()
    monkeypatch.setattr(
        google.auth, "default",
        lambda **kwargs: (credentials, "test-project"))
    calls = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"name": "operations/refresh-1"}

    class Session:
        def __init__(self, value):
            assert value is credentials

        def post(self, url, json, timeout):
            calls.append((url, json, timeout))
            return Response()

    monkeypatch.setattr(
        google.auth.transport.requests, "AuthorizedSession", Session)

    assert portal._invoke_cloud_refresh_job() == "operations/refresh-1"
    assert calls == [(
        "https://run.googleapis.com/v2/projects/test-project/locations/"
        "us-central1/jobs/spherex-refresh:run",
        {},
        20,
    )]


def test_local_refresh_status_proxies_sanitized_cloud_status(monkeypatch):
    import app as portal

    class Response:
        status_code = 200
        ok = True

        @staticmethod
        def json():
            return {
                "status": "running",
                "total_targets": 18,
                "completed_targets": 5,
                "manual_refresh_available": True,
                "snapshot_published_at": "2026-11-07T08:00:00Z",
                "internal_detail": "must not pass through",
            }

    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return Response()

    monkeypatch.setenv(
        "SPHEREX_REFRESH_API_URL", "https://cloud.example/api")
    monkeypatch.setattr(portal.requests, "request", request)

    response = portal.get_refresh_status()

    assert response.status_code == 200
    assert response.body
    import json
    payload = json.loads(response.body)
    assert payload["manual_refresh_available"] is True
    assert payload["completed_targets"] == 5
    assert "internal_detail" not in payload
    assert calls == [(
        "GET",
        "https://cloud.example/api/refresh-status",
        {"headers": {}, "timeout": (5, 25), "allow_redirects": False},
    )]


def test_local_refresh_trigger_proxies_operator_token(monkeypatch):
    from types import SimpleNamespace

    import app as portal

    class Response:
        status_code = 202
        ok = True

        @staticmethod
        def json():
            return {"status": "accepted", "accepted_at": "2026-11-07T08:00:00Z"}

    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return Response()

    monkeypatch.setenv(
        "SPHEREX_REFRESH_API_URL", "https://cloud.example/api")
    monkeypatch.setattr(portal.requests, "request", request)

    response = portal.trigger_cloud_refresh(
        SimpleNamespace(headers={"x-refresh-token": "operator-secret"}))

    assert response.status_code == 202
    assert calls == [(
        "POST",
        "https://cloud.example/api/refresh",
        {
            "headers": {"X-Refresh-Token": "operator-secret"},
            "timeout": (5, 25),
            "allow_redirects": False,
        },
    )]


def test_refresh_proxy_rejects_non_https_url(monkeypatch):
    import app as portal

    monkeypatch.setenv("SPHEREX_REFRESH_API_URL", "http://cloud.example/api")

    with pytest.raises(portal.HTTPException) as error:
        portal.get_refresh_status()

    assert error.value.status_code == 503


def test_ingestion_uses_supplied_fresh_listing(monkeypatch):
    import step1_ingest as ingest

    def unexpected_query(*args, **kwargs):
        pytest.fail("the supplied SIA2 listing should prevent a second query")

    monkeypatch.setattr(ingest, "query_sia2", unexpected_query)
    monkeypatch.setattr(ingest, "select_exposures",
                        lambda *args, **kwargs: ([], 0))

    result = ingest.download_spherex_cutouts(
        12.0, -4.0, rows=[])

    assert result == ([], [], 0, 0)


def test_strict_catalog_query_reports_transport_failure(monkeypatch):
    import requests
    import step1_ingest as ingest

    def fail_request(*args, **kwargs):
        raise requests.ConnectionError("IRSA unavailable")

    monkeypatch.setattr(ingest.requests, "post", fail_request)
    monkeypatch.setattr(ingest.time, "sleep", lambda *_: None)

    with pytest.raises(RuntimeError, match="IRSA TAP gaia query failed"):
        ingest.query_irsa_tap("SELECT 1", "gaia", retries=1, strict=True)


def test_strict_vsx_query_retries_mirror_without_a_table(monkeypatch):
    import step1_ingest as ingest

    class FakeVizier:
        calls = 0

        def __init__(self, **kwargs):
            pass

        def query_region(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return []
            return [[{
                "RAJ2000": 88.79,
                "DEJ2000": 7.41,
                "Name": "Test Variable",
                "Type": "EA",
                "OID": 123,
                "max": 10.1,
                "min": 11.2,
                "f_min": "Y",
                "n_max": "V",
                "n_min": "V",
                "Period": 1.5,
            }]]

    fake_vizier = FakeVizier()
    monkeypatch.setattr(ingest, "Vizier", lambda **kwargs: fake_vizier)
    monkeypatch.setattr(ingest, "VIZIER_MIRRORS", ("vizier.example.test",))
    monkeypatch.setattr(ingest.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        ingest, "query_irsa_tap_gaia",
        lambda *args, **kwargs: [{"ra": 1.0, "dec": 2.0}])
    monkeypatch.setattr(
        ingest, "query_irsa_tap_allwise",
        lambda *args, **kwargs: [{"ra": 1.0, "dec": 2.0}])

    catalogs = ingest.download_catalogs(88.79, 7.41, strict=True)

    assert fake_vizier.calls == 2
    assert catalogs["vsx"][0]["name"] == "Test Variable"
    assert catalogs["vsx"][0]["oid"] == 123


def test_strict_vsx_query_still_fails_after_retry(monkeypatch):
    import step1_ingest as ingest

    class EmptyVizier:
        calls = 0

        def __init__(self, **kwargs):
            pass

        def query_region(self, *args, **kwargs):
            self.calls += 1
            return []

    empty_vizier = EmptyVizier()
    monkeypatch.setattr(ingest, "Vizier", lambda **kwargs: empty_vizier)
    monkeypatch.setattr(ingest, "VIZIER_MIRRORS", ("vizier.example.test",))
    monkeypatch.setattr(ingest.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        ingest, "query_irsa_tap_gaia",
        lambda *args, **kwargs: [{"ra": 1.0, "dec": 2.0}])
    monkeypatch.setattr(
        ingest, "query_irsa_tap_allwise",
        lambda *args, **kwargs: [{"ra": 1.0, "dec": 2.0}])

    with pytest.raises(RuntimeError, match="vsx catalog query failed"):
        ingest.download_catalogs(88.79, 7.41, strict=True)

    assert empty_vizier.calls == 2


def test_refresh_checks_unchanged_sources_without_reprocessing(monkeypatch):
    import cloud_refresh as refresh

    calls = []
    monkeypatch.setattr(refresh.ingest, "TARGETS", {
        "Field_A": {"common": "Field A", "ra": 10.0, "dec": 20.0}
    })
    monkeypatch.setattr(
        refresh.ingest, "query_sia2",
        lambda *args, **kwargs: calls.append(kwargs) or [])
    store = SnapshotStore("test-bucket", client=FakeClient())

    refresh._run_refresh_locked(store)
    refresh._run_refresh_locked(store)

    pointer, _ = store.read_current()
    manifest = store.read_manifest(pointer)
    entry = manifest["targets"]["Field_A"]
    assert len(calls) == 2
    assert all(call["use_cache"] is False for call in calls)
    assert entry["last_check"] == "unchanged"
    assert store.read_json(entry["dashboard"])["error"].startswith(
        "No SPHEREx products")
    status = store.read_json("refresh-status/latest.json")
    assert status["completed_targets"] == 1
    assert status["unchanged_count"] == 1
    assert status["failed_count"] == 0


def test_refresh_failure_keeps_previous_published_snapshot(monkeypatch):
    import cloud_refresh as refresh

    monkeypatch.setattr(refresh.ingest, "TARGETS", {
        "Field_A": {"common": "Field A", "ra": 10.0, "dec": 20.0}
    })
    store = SnapshotStore("test-bucket", client=FakeClient())
    dashboard_object, _ = store.write_json_immutable(
        "snapshots/dashboards/Field_A", {"target": "Field_A"})
    previous_pointer = store.publish({
        "targets": {"Field_A": {"dashboard": dashboard_object}},
    }, expected_generation=0)

    def fail_query(*args, **kwargs):
        raise RuntimeError("IRSA unavailable")

    monkeypatch.setattr(refresh.ingest, "query_sia2", fail_query)
    with pytest.raises(RuntimeError):
        refresh.run_refresh(store)

    pointer, _ = store.read_current()
    status = store.read_json("refresh-status/latest.json")
    assert pointer == previous_pointer
    assert status["status"] == "failed"
    assert status["failed_target"] == "Field_A"
    assert status["failed_count"] == 1


def test_refresh_lock_is_removed_after_completion(store):
    lock = store.acquire_refresh_lock()
    assert store.bucket.blob("refresh.lock").exists()

    store.release_refresh_lock(lock)

    assert not store.bucket.blob("refresh.lock").exists()


# ---------------------------------------------------------------------------
# Regression tests for B1 – B8
# ---------------------------------------------------------------------------

# ── B1: NEOWISE data must be present after a catalog-only refresh ──────────

def test_neowise_file_restored_before_initial_dashboard(monkeypatch, tmp_path):
    """
    B1: _refresh_target must write the prior NEOWISE file to disk BEFORE
    calling get_dashboard the first time.  Simulate a catalog-only reprocess:
    detections are unchanged so refresh_neowise stays False and only the
    initial_dashboard is published.  Verify that dashboard's neowise panel
    is populated (not None / not available=False).
    """
    import cloud_refresh as refresh

    store = SnapshotStore("test-bucket", client=FakeClient())
    monkeypatch.setenv("SPHEREX_CACHE", str(tmp_path))
    monkeypatch.setattr(refresh, "CACHE", str(tmp_path))

    prior_neowise = {
        "target": "T1", "generated_utc": "2026-01-01T00:00:00Z",
        "positions": [{"kind": "target", "ra": 1.0, "dec": 2.0, "summary": {}}],
    }
    neowise_object, _ = store.write_json_immutable("snapshots/neowise/T1", prior_neowise)

    # prior entry has a neowise_object; same detections_hash → refresh_neowise=False
    detections_hash = refresh._canonical_hash([])
    prior_entry = {
        "dashboard": None,
        "neowise_object": neowise_object,
        "neowise_checked_at": "2026-01-01T00:00:00Z",
        "detections_hash": detections_hash,
        "last_check": "pipeline_unavailable",
    }

    fake_meta = {"paths": [], "catalogs": {}, "meta": {"ra": 1.0, "dec": 2.0}}
    # Make _restore_target_cache return a truthy value so the NEOWISE path runs.
    monkeypatch.setattr(refresh, "_restore_target_cache",
                        lambda *a, **kw: fake_meta)

    dashboard_calls = []
    fake_dashboard = {
        "target": "T1", "image": [[0]], "detections": [],
        "neowise": {"available": False, "generated_utc": None, "target": None},
    }

    def fake_get_dashboard(**kwargs):
        import json
        neowise_path = tmp_path / "T1_neowise.json"
        if neowise_path.exists():
            with open(neowise_path) as f:
                neowise = json.load(f)
            fake_dashboard["neowise"] = {
                "available": True,
                "generated_utc": neowise.get("generated_utc"),
                "target": neowise.get("positions", [None])[0],
            }
        dashboard_calls.append(dict(fake_dashboard))
        return dict(fake_dashboard)

    monkeypatch.setattr(refresh.ingest, "ingest_target",
                        lambda *a, **kw: fake_meta)
    monkeypatch.setattr(refresh.portal, "get_dashboard", fake_get_dashboard)
    monkeypatch.setattr(refresh.portal, "get_recovery",
                        lambda **kw: {"grid": [], "target": "T1"})

    entry = refresh._refresh_target(
        store, "T1", prior_entry, "fp-new", rows=["row"], ra=1.0, dec=2.0,
        checked_at="2026-11-07T00:00:00Z", refresh_catalogs=True,
    )
    assert dashboard_calls, "get_dashboard was never called"
    assert dashboard_calls[0]["neowise"]["available"] is True, (
        "B1: NEOWISE was NOT present in the initial dashboard"
    )


def test_neowise_present_when_detections_unchanged(monkeypatch, tmp_path):
    """
    B1 (variant): when detections_hash matches the prior, refresh_neowise=False
    and only one get_dashboard call runs.  The file must already be on disk.
    """
    import cloud_refresh as refresh

    store = SnapshotStore("test-bucket", client=FakeClient())
    monkeypatch.setenv("SPHEREX_CACHE", str(tmp_path))
    monkeypatch.setattr(refresh, "CACHE", str(tmp_path))

    prior_neowise = {"target": "T2", "generated_utc": "2026-01-01T00:00:00Z",
                     "positions": []}
    neowise_object, _ = store.write_json_immutable("snapshots/neowise/T2", prior_neowise)

    # Same detections hash → refresh_neowise=False
    empty_hash = refresh._canonical_hash([])
    prior_entry = {
        "dashboard": None,
        "neowise_object": neowise_object,
        "detections_hash": empty_hash,
        "last_check": "updated",
    }

    fake_meta = {"paths": [], "catalogs": {}, "meta": {"ra": 1.0, "dec": 2.0}}
    monkeypatch.setattr(refresh, "_restore_target_cache",
                        lambda *a, **kw: fake_meta)

    dashboard_calls = []

    def fake_get_dashboard(**kwargs):
        neowise_path = tmp_path / "T2_neowise.json"
        found = neowise_path.exists()
        dashboard_calls.append({"neowise_on_disk": found})
        return {"target": "T2", "image": [[0]], "detections": [],
                "neowise": {"available": found, "generated_utc": None, "target": None}}

    monkeypatch.setattr(refresh.ingest, "ingest_target",
                        lambda *a, **kw: fake_meta)
    monkeypatch.setattr(refresh.portal, "get_dashboard", fake_get_dashboard)
    monkeypatch.setattr(refresh.portal, "get_recovery",
                        lambda **kw: {"grid": [], "target": "T2"})

    refresh._refresh_target(
        store, "T2", prior_entry, "fp-2", rows=["row"], ra=1.0, dec=2.0,
        checked_at="2026-11-07T00:00:00Z", refresh_catalogs=False,
    )
    assert dashboard_calls, "get_dashboard was never called"
    assert dashboard_calls[0]["neowise_on_disk"] is True, (
        "B1: NEOWISE file was not on disk when initial_dashboard ran"
    )


# ── B2: stale lock threshold is 30 minutes, not 36 hours ──────────────────

def test_lock_is_stale_after_31_minutes(store):
    """B2: a lock with heartbeat_at 31 minutes ago should be overwriteable."""
    from datetime import datetime, timedelta, timezone

    old_time = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    store.bucket.objects["refresh.lock"] = (
        store._json_bytes({"token": "old", "started_at": old_time,
                           "heartbeat_at": old_time}), 1)

    # acquire_refresh_lock must succeed (overwrite the stale lock)
    lock = store.acquire_refresh_lock()
    assert lock is not None
    blob, gen = lock
    assert gen >= 2
    store.release_refresh_lock(lock)


def test_lock_blocks_within_30_minutes(store):
    """B2: a lock with heartbeat_at 20 minutes ago must still block."""
    from datetime import datetime, timedelta, timezone

    recent = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    store.bucket.objects["refresh.lock"] = (
        store._json_bytes({"token": "live", "started_at": recent,
                           "heartbeat_at": recent}), 1)

    with pytest.raises(RuntimeError, match="already running"):
        store.acquire_refresh_lock()


def test_stale_lock_marks_prior_run_failed(store):
    """B2: overwriting a stale lock must write status=failed first."""
    from datetime import datetime, timedelta, timezone

    old_time = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    store.bucket.objects["refresh.lock"] = (
        store._json_bytes({"token": "old", "started_at": old_time,
                           "heartbeat_at": old_time}), 1)
    store.write_json("refresh-status/latest.json",
                     {"status": "running", "started_at": old_time})

    lock = store.acquire_refresh_lock()
    store.release_refresh_lock(lock)

    status = store.read_json("refresh-status/latest.json")
    assert status["status"] == "failed"
    assert status["error_code"] == "stale_lock_preempted"


def test_heartbeat_updates_heartbeat_at(store):
    """B2: heartbeat_refresh_lock must update the heartbeat_at field."""
    import json
    from datetime import datetime, timedelta, timezone

    old_time = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    lock = store.acquire_refresh_lock()
    # Manually backdate the heartbeat to simulate time passing.
    blob, gen = lock
    data = json.loads(blob.download_as_bytes())
    data["heartbeat_at"] = old_time
    blob.upload_from_string(store._json_bytes(data),
                            content_type="application/json",
                            if_generation_match=gen)
    blob.reload()
    lock = (blob, blob.generation)

    new_lock = store.heartbeat_refresh_lock(lock)
    new_data = json.loads(store.bucket.blob("refresh.lock").download_as_bytes())
    assert new_data["heartbeat_at"] != old_time, "heartbeat_at was not updated"
    store.release_refresh_lock(new_lock)


# ── B3: per-field isolation ────────────────────────────────────────────────

def test_single_field_failure_does_not_abort_run(monkeypatch):
    """
    B3: when the first of three targets raises, the other two should still
    be processed and a manifest should be published.
    """
    import cloud_refresh as refresh

    store = SnapshotStore("test-bucket", client=FakeClient())
    monkeypatch.setattr(refresh.ingest, "TARGETS", {
        "Bad": {"common": "Bad", "ra": 1.0, "dec": 0.0},
        "Good1": {"common": "Good 1", "ra": 2.0, "dec": 0.0},
        "Good2": {"common": "Good 2", "ra": 3.0, "dec": 0.0},
    })

    call_log = []

    def fake_query(ra, dec, **kwargs):
        call_log.append(ra)
        if ra == 1.0:
            raise RuntimeError("IRSA exploded for Bad")
        return []  # no products → stub entry, no reprocessing

    monkeypatch.setattr(refresh.ingest, "query_sia2", fake_query)

    refresh._run_refresh_locked(store)

    pointer, _ = store.read_current()
    assert pointer is not None, "No manifest was published"
    manifest = store.read_manifest(pointer)
    assert manifest is not None

    # All three fields must appear in the manifest
    assert "Bad" in manifest["targets"]
    assert "Good1" in manifest["targets"]
    assert "Good2" in manifest["targets"]

    # Good fields got their stub no-products entries
    assert manifest["targets"]["Good1"]["last_check"] in (
        "no_products", "unchanged")
    assert manifest["targets"]["Good2"]["last_check"] in (
        "no_products", "unchanged")

    status = store.read_json("refresh-status/latest.json")
    assert status["failed_count"] == 1
    assert status["completed_targets"] == 3


def test_majority_failure_aborts_publish(monkeypatch):
    """B3: when more than half of fields fail, no manifest is published."""
    import cloud_refresh as refresh

    store = SnapshotStore("test-bucket", client=FakeClient())
    monkeypatch.setattr(refresh.ingest, "TARGETS", {
        "Bad1": {"common": "Bad1", "ra": 1.0, "dec": 0.0},
        "Bad2": {"common": "Bad2", "ra": 2.0, "dec": 0.0},
        "Good": {"common": "Good", "ra": 3.0, "dec": 0.0},
    })

    def fail_all_but_good(ra, dec, **kwargs):
        if ra in (1.0, 2.0):
            raise RuntimeError("exploded")
        return []

    monkeypatch.setattr(refresh.ingest, "query_sia2", fail_all_but_good)

    with pytest.raises(RuntimeError, match="fields failed"):
        refresh._run_refresh_locked(store)

    pointer, _ = store.read_current()
    assert pointer is None, "Manifest should not have been published"


def test_failed_field_retains_prior_entry(monkeypatch):
    """B3: a failed field keeps whatever was in the manifest before this run."""
    import cloud_refresh as refresh

    store = SnapshotStore("test-bucket", client=FakeClient())

    dashboard_object, _ = store.write_json_immutable(
        "snapshots/dashboards/Field_A",
        {"target": "Field_A", "image": [[0]], "ra": 10.0, "dec": 20.0})
    prior_pointer = store.publish({
        "targets": {
            "Field_A": {
                "dashboard": dashboard_object,
                "last_check": "updated",
            }
        },
    }, expected_generation=0)

    monkeypatch.setattr(refresh.ingest, "TARGETS", {
        "Field_A": {"common": "Field A", "ra": 10.0, "dec": 20.0},
    })

    def fail_query(*args, **kwargs):
        raise RuntimeError("query bombed")

    monkeypatch.setattr(refresh.ingest, "query_sia2", fail_query)

    # With 1 target failing out of 1 (100% > 50%) the publish is aborted.
    with pytest.raises(RuntimeError):
        refresh._run_refresh_locked(store)

    # The prior snapshot must still be the live one.
    pointer, _ = store.read_current()
    assert pointer == prior_pointer, "Prior snapshot was overwritten after failure"


# ── B5: custom field name collision against GCS manifest ──────────────────

def test_custom_field_name_collision_with_manifest(monkeypatch):
    """
    B5: registering a custom field whose name matches a manifest entry at
    different coordinates must produce a renamed target, not collide silently.
    """
    import app as portal

    store = SnapshotStore("test-bucket", client=FakeClient())
    meta_obj, _ = store.write_json_immutable(
        "cache-metadata/Sirius",
        {"meta": {"ra": 101.2887, "dec": -16.7161, "common": "Sirius"}})
    store.publish({
        "targets": {
            "Sirius": {
                "meta_object": meta_obj,
                "dashboard": None,
            }
        }
    }, expected_generation=0)

    monkeypatch.setattr(portal, "SNAPSHOT_BUCKET", "test-bucket")
    monkeypatch.setattr(portal, "_SNAPSHOT_STORE", store)
    monkeypatch.setenv("SPHEREX_SNAPSHOT_READS", "true")
    monkeypatch.setattr(portal, "_MANIFEST_CACHE",
                        {"t": 0.0, "pointer": None, "manifest": None})

    # Ask for a custom "Sirius" at completely different coordinates
    safe = portal.re.sub(r"[^A-Za-z0-9_]+", "_", "Sirius").strip("_")
    existing = portal._manifest_target_entry(safe)
    assert existing is not None, "_manifest_target_entry should find Sirius"
    assert float(existing["meta"]["ra"]) != 10.0


def test_custom_field_name_collision_with_targets_registry(monkeypatch):
    """B5: name matching a key in TARGETS must also trigger renaming."""
    import app as portal
    import step1_ingest as ingest

    target_name = next(iter(ingest.TARGETS))  # grab any real target name
    result = portal._manifest_target_entry(target_name)
    assert result is not None, (
        f"_manifest_target_entry should return something for registered target {target_name!r}")


# ── B6: custom field recovery must route to the live pipeline ──────────────

def test_custom_field_recovery_uses_live_pipeline(monkeypatch):
    """
    B6: get_recovery for a target in FIELD_REGISTRY must call live_exposures
    even when source=cache, bypassing the manifest entirely.
    """
    import app as portal

    store = SnapshotStore("test-bucket", client=FakeClient())
    monkeypatch.setattr(portal, "_SNAPSHOT_STORE", store)
    monkeypatch.setattr(portal, "SNAPSHOT_BUCKET", "test-bucket")
    monkeypatch.setenv("SPHEREX_SNAPSHOT_READS", "true")

    meta = {"meta": {"ra": 45.0, "dec": 10.0},
            "paths": [], "epochs": [], "catalogs": {}, "custom": True}
    portal.FIELD_REGISTRY["CustomX"] = meta

    live_calls = []

    def fake_live_exposures(target, meta, **kwargs):
        live_calls.append(target)
        raise portal.CoverageError("no coverage for test")

    monkeypatch.setattr(portal, "live_exposures", fake_live_exposures)
    try:
        response = portal.get_recovery(target="CustomX", source="cache")
        assert response.status_code == 422
    finally:
        portal.FIELD_REGISTRY.pop("CustomX", None)

    assert "CustomX" in live_calls, (
        "B6: live_exposures was not called for custom field recovery")


# ── B7: live request with no meta_object falls back to TARGETS ────────────

def test_live_request_without_meta_object_uses_targets_fallback(monkeypatch):
    """
    B7: get_dashboard(source=live) for a manifest entry that has no meta_object
    (e.g. a no-products entry) must not return 503; it must call live_exposures.
    """
    import app as portal
    import step1_ingest as ingest

    # Pick any registered target.
    target = next(iter(ingest.TARGETS))

    store = SnapshotStore("test-bucket", client=FakeClient())
    # Publish an entry with a dashboard but NO meta_object.
    dashboard_object, _ = store.write_json_immutable(
        f"snapshots/dashboards/{target}",
        {"target": target, "error": "No SPHEREx products are currently available."})
    store.publish({
        "targets": {
            target: {
                "dashboard": dashboard_object,
                # no meta_object
                "last_check": "no_products",
            }
        }
    }, expected_generation=0)

    monkeypatch.setattr(portal, "SNAPSHOT_BUCKET", "test-bucket")
    monkeypatch.setattr(portal, "_SNAPSHOT_STORE", store)
    monkeypatch.setattr(portal, "_MANIFEST_CACHE",
                        {"t": 0.0, "pointer": None, "manifest": None})
    monkeypatch.setenv("SPHEREX_SNAPSHOT_READS", "true")

    live_calls = []

    def fake_live_exposures(t, meta, **kwargs):
        live_calls.append(t)
        raise portal.CoverageError("no test coverage")

    monkeypatch.setattr(portal, "live_exposures", fake_live_exposures)
    # Offline: the fresh IRSA listing check finds nothing to compare.
    monkeypatch.setattr(portal, "query_sia2", lambda *args, **kwargs: [])

    response = portal.get_dashboard(target=target, source="live")
    assert response.status_code == 422, (
        f"Expected 422 from CoverageError; got {response.status_code}")
    assert target in live_calls, (
        "B7: live_exposures was not called; the request probably returned 503")


# ── B8: UI poll timeout (Python-side guard, not JS) ───────────────────────
# The actual JS behaviour cannot be tested in pytest; we verify the API
# response contract that drives the timeout decision.

def test_refresh_status_returns_started_at_for_timing(store, monkeypatch):
    """
    B8 (API contract): refresh-status must include started_at so the JS can
    compare it against cloudRefreshRequestedAt to detect a new run.
    """
    import app as portal

    store.publish({"targets": {}}, expected_generation=0)
    store.write_json("refresh-status/latest.json", {
        "status": "running",
        "started_at": "2026-11-07T08:00:00Z",
        "total_targets": 18,
    })
    monkeypatch.setattr(portal, "_snapshot_store", lambda: store)
    monkeypatch.setattr(portal, "_MANIFEST_CACHE",
                        {"t": 0.0, "pointer": None, "manifest": None})

    status = portal.get_refresh_status()

    if isinstance(status, dict):
        assert "started_at" in status, "started_at missing from refresh-status response"
    else:
        import json
        payload = json.loads(status.body)
        assert "started_at" in payload, "started_at missing from refresh-status response"


# ── Rate limiter must not break internal callers ──────────────────────────

def test_rate_limited_endpoints_stay_callable_internally():
    """Streams, the refresh worker and export call the pipeline functions
    directly; slowapi's wrapper raises without a starlette Request, so the
    module-level names must be the unwrapped functions while the HTTP routes
    keep the limiter."""
    import app as portal

    limited = {
        "/api/targets": portal.list_targets,
        "/api/dashboard": portal.get_dashboard,
        "/api/recovery": portal.get_recovery,
        "/api/field": portal.add_field,
        "/api/mosaic": portal.get_mosaic,
        "/api/refresh": portal.trigger_cloud_refresh,
    }
    routes = {route.path: route.endpoint for route in portal.app.routes
              if getattr(route, "path", None) in limited}
    assert set(routes) == set(limited)
    for path, func in limited.items():
        assert not hasattr(func, "__wrapped__"), path
        assert routes[path] is not func, f"{path} route lost its rate limit"
        assert routes[path].__wrapped__ is func, path


def test_recovery_stream_reaches_pipeline_without_request(monkeypatch):
    """Regression: stream_recovery used to raise inside slowapi and report
    'The live pipeline failed unexpectedly' for every request."""
    import asyncio

    import app as portal

    monkeypatch.setattr(portal, "_snapshot_store", lambda: None)

    async def collect():
        response = await portal.stream_recovery(target="Nope", source="cache")
        return "".join([
            chunk.decode() if isinstance(chunk, bytes) else chunk
            async for chunk in response.body_iterator])

    text = asyncio.run(collect())
    assert "failed unexpectedly" not in text
    assert '"status":404' in text


def test_rate_limit_key_identifies_visitor_behind_proxies():
    from types import SimpleNamespace

    import app as portal

    def request(headers, host="169.254.1.1"):
        return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))

    assert portal._client_key(request({
        "x-nf-client-connection-ip": "203.0.113.7",
        "x-forwarded-for": "198.51.100.1, 192.0.2.9"})) == "203.0.113.7"
    # Cloud Run appends the real peer as the last hop; earlier hops are
    # client-controlled.
    assert portal._client_key(request({
        "x-forwarded-for": "10.0.0.1, 198.51.100.2"})) == "198.51.100.2"
    assert portal._client_key(request({}, host="192.0.2.50")) == "192.0.2.50"


# ── Per-field refresh progress and schedule ───────────────────────────────

def test_refresh_status_field_states_are_sanitized(store, monkeypatch):
    import app as portal

    store.write_json("refresh-status/latest.json", {
        "status": "running",
        "field_states": {
            "Field_A": "updated",
            "Field_B": "checking",
            "../etc": "updated",
            "Field_C": "<script>",
            "Field_D": "retained",
        },
    })
    monkeypatch.setattr(portal, "_snapshot_store", lambda: store)

    status = portal.get_refresh_status()

    assert status["field_states"] == {
        "Field_A": "updated", "Field_B": "checking", "Field_D": "retained"}
    assert status["next_scheduled_at"]


def test_next_scheduled_refresh_matches_kuala_lumpur_cron(monkeypatch):
    from datetime import datetime, timezone

    import app as portal

    monkeypatch.delenv("SPHEREX_REFRESH_SCHEDULE_UTC_HOURS", raising=False)
    at = lambda *args: datetime(2026, 11, 7, *args, tzinfo=timezone.utc)
    # 00:00 and 12:00 Asia/Kuala_Lumpur == 16:00 and 04:00 UTC.
    assert portal._next_scheduled_refresh(at(3, 59)) == at(4).isoformat()
    assert portal._next_scheduled_refresh(at(4, 0)) == at(16).isoformat()
    assert portal._next_scheduled_refresh(at(16, 30)) == datetime(
        2026, 11, 8, 4, tzinfo=timezone.utc).isoformat()
    monkeypatch.setenv("SPHEREX_REFRESH_SCHEDULE_UTC_HOURS", "bogus")
    assert portal._next_scheduled_refresh(at(1)) is None


def test_refresh_worker_publishes_per_field_states(monkeypatch):
    import cloud_refresh as refresh

    monkeypatch.setattr(refresh.ingest, "TARGETS", {
        "Field_A": {"common": "Field A", "ra": 10.0, "dec": 20.0},
        "Field_B": {"common": "Field B", "ra": 11.0, "dec": 21.0},
    })

    def query(ra, dec, **kwargs):
        if ra == 11.0:
            raise RuntimeError("IRSA unavailable")
        return []

    monkeypatch.setattr(refresh.ingest, "query_sia2", query)
    store = SnapshotStore("test-bucket", client=FakeClient())

    refresh._run_refresh_locked(store)        # A: first sighting -> updated
    status = store.read_json("refresh-status/latest.json")
    assert status["field_states"] == {"Field_A": "updated", "Field_B": "failed"}

    refresh._run_refresh_locked(store)        # A: same listing -> unchanged
    status = store.read_json("refresh-status/latest.json")
    assert status["field_states"] == {"Field_A": "unchanged", "Field_B": "failed"}


# ── IRSA-fresh all-sky tiles (one field per request) ──────────────────────

def test_live_mosaic_tile_builds_fresh_tile(monkeypatch):
    import numpy as np

    import app as portal

    calls = []

    def fake_dashboard(**kwargs):
        calls.append(kwargs)
        return {"target": "Sirius", "common": "Sirius", "ra": 101.28,
                "dec": -16.72, "map": {"fov_arcmin": 9.0},
                "zogy_snr": np.zeros((96, 96)).tolist(),
                "stats": {"n_detections": 3, "max_snr": 8.9}}

    monkeypatch.setattr(portal, "get_dashboard", fake_dashboard)
    tile = portal.live_mosaic_tile("Sirius")

    assert calls == [{"target": "Sirius", "source": "live", "live_slots": 0,
                      "live_size": 0.2, "live_epochs": "firstlast"}]
    assert tile["available"] is True and tile["source"] == "live"
    assert tile["n_detections"] == 3 and len(tile["thumb"]) == 48
    assert tile["generated_at"].endswith("Z")


def test_live_mosaic_tile_keeps_field_locatable_on_irsa_error(monkeypatch):
    import app as portal

    monkeypatch.setattr(portal, "get_dashboard", lambda **kwargs: portal.JSONResponse(
        status_code=422, content={"error": "no detector band is observed in every epoch"}))
    tile = portal.live_mosaic_tile("SgrA_star")

    assert tile["available"] is False and tile["source"] == "live"
    assert "every epoch" in tile["reason"]
    assert tile["target"] == "SgrA_star" and tile["ra"] and tile["dec"]


def test_live_mosaic_tile_rejects_unregistered_target():
    import app as portal

    response = portal.live_mosaic_tile("Not_A_Field")
    assert response.status_code == 404


# ── Faster, still-complete fresh checks and snapshot reads ────────────────

def _published_field(store, monkeypatch, fingerprint):
    import app as portal
    import step1_ingest as ingest

    target = next(iter(ingest.TARGETS))
    dashboard_object, _ = store.write_json_immutable(
        "snapshots/dashboards/x", {"target": target, "image": [[0.0]],
                                    "zogy_snr": [[0.0]], "source": "scheduled"})
    store.publish({"targets": {target: {
        "dashboard": dashboard_object, "source_fingerprint": fingerprint,
        "last_updated_at": "2026-11-07T16:33:33+00:00", "last_check": "updated",
    }}}, expected_generation=0)
    monkeypatch.setattr(portal, "SNAPSHOT_BUCKET", "test-bucket")
    monkeypatch.setattr(portal, "_SNAPSHOT_STORE", store)
    monkeypatch.setattr(portal, "_snapshot_store", lambda: store)
    monkeypatch.setattr(portal, "_MANIFEST_CACHE",
                        {"t": 0.0, "pointer": None, "manifest": None})
    return target


def test_fresh_check_reuses_snapshot_when_irsa_lists_nothing_new(store, monkeypatch):
    import app as portal

    rows = [{"obs_id": "a"}]
    monkeypatch.setattr(portal, "query_sia2", lambda *a, **k: rows)
    monkeypatch.setattr(portal, "source_fingerprint", lambda r, ra, dec: "same")
    target = _published_field(store, monkeypatch, fingerprint="same")
    monkeypatch.setattr(portal, "live_exposures", lambda *a, **k: pytest.fail(
        "cutouts must not be downloaded when IRSA lists nothing new"))

    result = portal.get_dashboard(target=target, source="live", live_slots=0,
                                  live_size=0.2, live_epochs="firstlast")

    assert result["image"] == [[0.0]]          # the full published result
    check = result["irsa_check"]
    assert check["reused_snapshot"] is True and check["new_since_snapshot"] is False
    assert check["irsa_rows"] == 1 and check["checked_at"].endswith("Z")
    assert check["snapshot_processed_at"] == "2026-11-07T16:33:33+00:00"


def test_fresh_check_runs_full_fetch_with_the_same_listing_when_irsa_changed(
        store, monkeypatch):
    import app as portal
    import step1_ingest as ingest

    rows = [{"obs_id": "new"}]
    monkeypatch.setattr(portal, "query_sia2", lambda *a, **k: rows)
    monkeypatch.setattr(portal, "source_fingerprint", lambda r, ra, dec: "changed")
    target = _published_field(store, monkeypatch, fingerprint="old")
    ra, dec = ingest.target_coordinates(target)
    monkeypatch.setattr(portal, "_meta_from_targets_registry",
                        lambda t: {"meta": {"ra": ra, "dec": dec, "common": t}})
    seen = {}

    def fake_live_exposures(t, meta, **kwargs):
        seen.update(kwargs)
        raise portal.CoverageError("stop after the hand-off")

    monkeypatch.setattr(portal, "live_exposures", fake_live_exposures)
    response = portal.get_dashboard(target=target, source="live", live_slots=0,
                                    live_size=0.2, live_epochs="firstlast")

    assert response.status_code == 422
    assert seen["rows"] is rows                # no second IRSA query


def test_immutable_snapshot_objects_are_cached_but_pointers_are_not(store):
    name, _ = store.write_json_immutable("snapshots/dashboards/y", {"v": 1})
    assert store.read_json(name) == {"v": 1}
    del store.bucket.objects[name]             # GCS no longer has it...
    assert store.read_json(name) == {"v": 1}   # ...served from memory

    store.write_json("refresh-status/latest.json", {"status": "running"})
    assert store.read_json("refresh-status/latest.json")["status"] == "running"
    store.write_json("refresh-status/latest.json", {"status": "succeeded"})
    assert store.read_json("refresh-status/latest.json")["status"] == "succeeded"
    assert store.read_json("snapshots/missing.json") is None


def test_fresh_fetch_works_without_a_snapshot(monkeypatch):
    import app as portal
    import step1_ingest as ingest

    target = next(iter(ingest.TARGETS))
    monkeypatch.setattr(portal, "_snapshot_store", lambda: None)
    monkeypatch.setattr(portal, "query_sia2", lambda *a, **k: [])
    monkeypatch.setattr(portal, "live_exposures", lambda *a, **k: (_ for _ in ()).throw(
        portal.CoverageError("reached the fresh fetch")))

    response = portal.get_dashboard(target=target, source="live", live_slots=0,
                                    live_size=0.2, live_epochs="firstlast")
    assert response.status_code == 422        # not 404 "no cached data"


def test_irsa_timeouts_fit_inside_cloud_run_request_limit():
    import live_ingest
    import step1_ingest as ingest

    connect, read = ingest.SIA2_TIMEOUT
    sia2_worst = 3 * (connect + read) + 15 + 30          # 3 attempts + back-off
    connect, read = live_ingest.FETCH_TIMEOUT
    # Past the budget no new download or retry starts, so at most one
    # attempt that began just before the budget ran out is still in flight.
    worst = sia2_worst + live_ingest.LIVE_DEADLINE_SECONDS + (connect + read)
    assert worst < 900, f"worst case {worst} s exceeds Cloud Run's 900 s limit"


def test_catalogs_run_in_parallel_and_report_status(monkeypatch):
    import threading
    import step1_ingest as ingest

    barrier = threading.Barrier(3, timeout=5)   # all three must be in flight together

    def gaia(*args, **kwargs):
        barrier.wait()
        return [{"ra": 1.0, "dec": 2.0}]

    def allwise(*args, **kwargs):
        barrier.wait()
        raise RuntimeError("TAP down")           # falls back to VizieR

    class Mirrors:
        calls = []

        def __init__(self, **kwargs):
            pass

        def query_region(self, *args, catalog=None, **kwargs):
            Mirrors.calls.append((self.VIZIER_SERVER, catalog))
            if catalog == "B/vsx/vsx":
                if len([c for c in Mirrors.calls if c[1] == catalog]) == 1:
                    barrier.wait()
                return []                         # the mirror answers: no sources
            raise ConnectionError("unreachable")

    monkeypatch.setattr(ingest, "Vizier", Mirrors)
    monkeypatch.setattr(ingest, "VIZIER_MIRRORS", ("first.test", "second.test"))
    monkeypatch.setattr(ingest.time, "sleep", lambda *_: None)
    monkeypatch.setattr(ingest, "query_irsa_tap_gaia", gaia)
    monkeypatch.setattr(ingest, "query_irsa_tap_allwise", allwise)

    seen = []
    cats, status = ingest.download_catalogs_with_status(
        10.0, 20.0, progress=lambda key, n, state: seen.append((key, state)))

    assert cats["gaia"] == [{"ra": 1.0, "dec": 2.0}] and status["gaia"] == "ok"
    assert cats["wise"] == [] and status["wise"] == "unavailable"
    assert cats["vsx"] == [] and status["vsx"] == "none"
    # VSX: the answering first mirror is retried once and then believed;
    # the unreachable fallback mirror is not waited on.
    vsx_calls = [c for c in Mirrors.calls if c[1] == "B/vsx/vsx"]
    assert vsx_calls == [("first.test", "B/vsx/vsx")] * 2
    assert sorted(seen) == [("gaia", "ok"), ("vsx", "none"), ("wise", "unavailable")]


def test_custom_field_with_unchanged_listing_skips_reprocessing(monkeypatch):
    import app as portal
    from step1_ingest import source_fingerprint

    rows = [{"obs_id": "a", "access_url": "https://x/1.fits", "t_min": 60000.0,
             "energy_bandpassname": "SPHEREx-D1", "s_ra": 2.0, "s_dec": 2.0}]
    monkeypatch.setattr(portal, "query_sia2", lambda *a, **k: rows)
    monkeypatch.setattr(portal, "_start_catalog_lookup",
                        lambda *a, **k: pytest.fail("catalogues must not be queried"))
    monkeypatch.setattr(portal, "live_exposures",
                        lambda *a, **k: pytest.fail("cutouts must not be fetched"))
    monkeypatch.setattr(portal, "_manifest_target_entry", lambda name: None)

    known = source_fingerprint(rows, 2.0, 2.0)
    result = portal._register_field(2.0, 2.0, "", 0.2, 4, "firstlast", 1,
                                    known_fingerprint=known)
    assert result["unchanged"] is True
    assert result["irsa_check"]["source_fingerprint"] == known
    assert result["irsa_check"]["irsa_rows"] == 1


def test_compressed_stream_result_unpacks_to_identical_json():
    import base64
    import gzip
    import json
    import app as portal

    payload = {"target": "Field", "values": list(range(500)), "name": "Ωmega ✓"}
    packed = portal._pack_result(payload)
    assert packed["encoding"] == "gzip-base64"
    raw = gzip.decompress(base64.b64decode(packed["data_packed"]))
    assert json.loads(raw) == payload
    assert packed["raw_bytes"] == len(raw)


def _no_login_store(*args, **kwargs):
    from google.auth.exceptions import DefaultCredentialsError
    raise DefaultCredentialsError("Your default credentials were not found.")


def test_without_google_login_snapshot_reads_go_through_the_api(monkeypatch):
    import app as portal

    monkeypatch.setenv("SPHEREX_SNAPSHOT_READS", "true")
    monkeypatch.setattr(portal, "SNAPSHOT_SOURCE", "auto")
    monkeypatch.setattr(portal, "_SNAPSHOT_STORE", None)
    monkeypatch.setitem(portal._SNAPSHOT_MODE, "value", None)
    monkeypatch.setattr(portal, "SnapshotStore", _no_login_store)
    forwarded = []

    def fake_forward(method, path, query, token=None):
        forwarded.append((method, path, query))
        body = b'{"targets":[{"name":"Field_A"}]}' if path == "/api/targets" else b'{"ok":true}'
        return 200, {"Content-Type": "application/json",
                     "X-Snapshot-Object": "snapshots/x/abc.json"}, body

    monkeypatch.setattr(portal, "_forward_snapshot_request", fake_forward)

    status, headers, body = _asgi_get(portal.app, "/api/targets")
    assert status == 200 and body == {"targets": [{"name": "Field_A"}]}
    assert headers["x-snapshot-object"] == "snapshots/x/abc.json"
    assert headers["cache-control"].startswith("no-store")
    assert portal._snapshot_mode() == "api"
    assert portal._snapshot_store() is None

    _asgi_get(portal.app, "/api/dashboard", "target=Betelgeuse")
    assert forwarded[-1] == ("GET", "/api/dashboard", "target=Betelgeuse")

    # Fresh IRSA requests and custom fields stay on this computer.
    count = len(forwarded)
    monkeypatch.setattr(portal, "live_exposures",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no IRSA in tests")))
    monkeypatch.setattr(portal, "query_sia2", lambda *a, **k: [])
    _asgi_get(portal.app, "/api/dashboard", "target=Betelgeuse&source=live")
    monkeypatch.setitem(portal.FIELD_REGISTRY, "My_Field", None)
    _asgi_get(portal.app, "/api/recovery", "target=My_Field")
    assert len(forwarded) == count


def test_forced_bucket_mode_without_login_gives_a_clear_error_not_a_crash(monkeypatch):
    import app as portal

    monkeypatch.setenv("SPHEREX_SNAPSHOT_READS", "true")
    monkeypatch.setattr(portal, "SNAPSHOT_SOURCE", "bucket")
    monkeypatch.setattr(portal, "_SNAPSHOT_STORE", None)
    monkeypatch.setitem(portal._SNAPSHOT_MODE, "value", None)
    monkeypatch.setattr(portal, "SnapshotStore", _no_login_store)

    status, _, body = _asgi_get(portal.app, "/api/targets")
    assert status == 503
    assert "Google login" in body["detail"]
