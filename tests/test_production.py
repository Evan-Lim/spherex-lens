"""Phase 9 production hardening: votes, queue, exports, cone search, health,
credits, job queue and "Open any field" input validation.  All offline."""
import io
import itertools
import time

import pytest
from fastapi.testclient import TestClient

import config
from spherexlens import jobs, votes
from spherexlens.routes import _detections

_IPS = (f"203.0.113.{i % 250}" for i in itertools.count(1))


def _ip():
    """A fresh visitor address per request, so rate limits never interfere."""
    return {"x-forwarded-for": next(_IPS)}


@pytest.fixture
def portal():
    import app as portal
    return portal


@pytest.fixture
def client(portal):
    return TestClient(portal.app)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("SPHEREX_VOTES_SALT", "test-salt")
    monkeypatch.delenv("SPHEREX_VOTES_BUCKET", raising=False)
    votes.set_store(votes.LocalVoteStore(str(tmp_path / "votes")))
    _detections.reset_cache()
    from spherexlens.routes import catalog_export, health, jobs as jobs_route
    from spherexlens.routes import votes as votes_route
    for limiter in (votes_route.VOTE_LIMIT, votes_route.QUEUE_LIMIT,
                    catalog_export.EXPORT_LIMIT, catalog_export.CONE_LIMIT,
                    health.HEALTH_LIMIT, jobs_route.SUBMIT_LIMIT, jobs_route.STATUS_LIMIT):
        limiter.reset()
    health._PROBE_CACHE.update(t=0.0, value=None)
    yield
    votes.set_store(None)
    _detections.reset_cache()


ARROW = "\u2192"


def _det(target, y, x, snr, category="CANDIDATE", ra=10.0, dec=20.0, ml=None, **extra):
    det = {"detection_id": f"{target}:E1{ARROW}E2:{y},{x}", "pair": f"E1{ARROW}E2",
           "position": [y, x], "snr": snr, "category": category, "kind": "monopole",
           "verdict": "check", "priority": 2, "ra": ra, "dec": dec,
           "mjd_a": 60700.5, "mjd_b": 60880.25, "reasons": ["test"]}
    if ml is not None:
        det["ml"] = {"real_bogus": ml, "model_version": "rb-test"}
    det.update(extra)
    return det


DASHBOARDS = [
    ("WISE_0855", {
        "pipeline": {"pipeline_version": "2.0.0-dev", "config_hash": "abc123abc123",
                     "model_version": None},
        "detections": [
            _det("WISE_0855", 10, 11, 5.1, ra=133.78, dec=-7.24,
                 evidence={"score": 0.7}, asteroid={"status": "no_match"}),
            _det("WISE_0855", 20, 21, 9.0, ra=133.79, dec=-7.25),
            _det("WISE_0855", 30, 31, 5.0, category="KNOWN_STAR", ra=133.80, dec=-7.23),
        ]}),
    ("Field_B", {
        "detections": [_det("Field_B", 5, 6, 12.0, ra=200.0, dec=45.0)]}),
]


@pytest.fixture
def fake_dashboards(monkeypatch):
    monkeypatch.setattr(_detections, "collect_dashboards",
                        lambda: (DASHBOARDS, "test-manifest"))
    _detections.reset_cache()


# --------------------------------------------------------------------------
# votes: validation, dedup, backends
# --------------------------------------------------------------------------
GOOD_ID = f"WISE_0855:E1{ARROW}E2:10,11"


@pytest.mark.parametrize("target,detection_id,vote", [
    ("WISE_0855", GOOD_ID, "maybe"),                       # bad vote
    ("WISE_0855", "WISE_0855:10,11", "real"),              # bad id format
    ("Other", GOOD_ID, "real"),                            # id of another target
    ("bad target!", GOOD_ID, "real"),                      # bad target name
    ("WISE_0855", GOOD_ID + "9" * 300, "real"),            # too long
    ("WISE_0855", "", "real"),
])
def test_vote_validation_rejects(target, detection_id, vote):
    with pytest.raises(votes.VoteError):
        votes.validate(target, detection_id, vote)


def test_vote_validation_accepts_ascii_arrow_and_case():
    assert votes.validate("WISE_0855", "WISE_0855:E1->E3:91.5,88", "REAL") == (
        "WISE_0855", f"WISE_0855:E1{ARROW}E3:91.5,88", "real")


def test_local_votes_dedup_latest_wins_and_hash_client():
    votes.record_vote("WISE_0855", GOOD_ID, "real", "198.51.100.7")
    time.sleep(0.002)
    votes.record_vote("WISE_0855", GOOD_ID, "bogus", "198.51.100.7")
    votes.record_vote("WISE_0855", GOOD_ID, "unsure", "198.51.100.8")
    loaded = votes.load_votes()
    assert len(loaded) == 2
    mine = [v for v in loaded if v["vote"] != "unsure"]
    assert mine[0]["vote"] == "bogus"
    assert set(loaded[0]) == {"detection_id", "target", "vote", "ts", "client"}
    assert all("198.51" not in v["client"] for v in loaded)
    assert votes.tally(loaded)[GOOD_ID] == {"real": 0, "bogus": 1, "unsure": 1}


def test_gcs_votes_are_create_only_objects_and_shared_salt(monkeypatch):
    from test_cloud_snapshots import FakeClient
    monkeypatch.delenv("SPHEREX_VOTES_SALT")
    fake = FakeClient()
    store = votes.GcsVoteStore("test-bucket", client=fake)
    votes.set_store(store)
    votes.record_vote("WISE_0855", GOOD_ID, "real", "198.51.100.7")
    time.sleep(0.002)
    votes.record_vote("WISE_0855", GOOD_ID, "bogus", "198.51.100.7")
    names = sorted(fake.fake_bucket.objects)
    assert "salt/v1.txt" in names
    vote_objects = [n for n in names if n.startswith("votes/v1/")]
    assert len(vote_objects) == 2                 # both kept, nothing overwritten
    # A second instance (fresh process) sees the same salt and the same votes.
    other = votes.GcsVoteStore("test-bucket", client=fake)
    assert other.salt() == store.salt()
    votes.set_store(other)
    loaded = votes.load_votes()
    assert [v["vote"] for v in loaded] == ["bogus"]
    time.sleep(0.002)
    votes.record_vote("WISE_0855", GOOD_ID, "real", "198.51.100.7")
    assert [v["vote"] for v in votes.load_votes()] == ["real"]


def test_post_vote_route(client):
    ok = client.post("/api/votes", headers=_ip(),
                     json={"target": "WISE_0855", "detection_id": GOOD_ID, "vote": "real"})
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    bad = client.post("/api/votes", headers=_ip(),
                      json={"target": "WISE_0855", "detection_id": GOOD_ID, "vote": "x"})
    assert bad.status_code == 400
    unknown = client.post("/api/votes", headers=_ip(), json={
        "target": "Nope_123", "detection_id": f"Nope_123:E1{ARROW}E2:1,1", "vote": "real"})
    assert unknown.status_code == 400
    assert len(votes.load_votes()) == 1


def test_post_vote_rate_limited(client):
    headers = {"x-forwarded-for": "192.0.2.55"}
    body = {"target": "WISE_0855", "detection_id": GOOD_ID, "vote": "real"}
    codes = [client.post("/api/votes", headers=headers, json=body).status_code
             for _ in range(config.Production.VOTE_RATE_PER_MIN + 1)]
    assert codes[-1] == 429 and codes[0] == 200


def test_post_vote_503_in_production_without_bucket(client, monkeypatch):
    monkeypatch.setattr(config.Production, "ENABLED", True)
    votes.set_store(None)
    response = client.post("/api/votes", headers=_ip(), json={
        "target": "WISE_0855", "detection_id": GOOD_ID, "vote": "real"})
    assert response.status_code == 503
    assert "SPHEREX_VOTES_BUCKET" in response.json()["detail"]


# --------------------------------------------------------------------------
# queue
# --------------------------------------------------------------------------
def test_queue_fallback_ranks_by_snr_near_threshold(client, fake_dashboards, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "spherexlens.ml.active", None)   # no ML layer
    monkeypatch.setattr(config, "DETECTION_SIGMA", 5.0)
    data = client.get("/api/votes/queue?n=10", headers=_ip()).json()
    assert data["ranking"] == "snr_near_threshold"
    snrs = [d["snr"] for d in data["detections"]]
    assert snrs == [5.1, 9.0, 12.0]                       # KNOWN_STAR excluded
    assert all(not d["category"].startswith("KNOWN_") for d in data["detections"])
    assert data["detections"][0]["votes"] == {"real": 0, "bogus": 0, "unsure": 0}


def test_queue_prefers_ml_probability_near_half(client, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "spherexlens.ml.active", None)
    dash = [("WISE_0855", {"detections": [
        _det("WISE_0855", 1, 1, 5.0, ml=0.95), _det("WISE_0855", 2, 2, 30.0, ml=0.55)]})]
    monkeypatch.setattr(_detections, "collect_dashboards", lambda: (dash, "x"))
    data = client.get("/api/votes/queue?n=1", headers=_ip()).json()
    assert [d["snr"] for d in data["detections"]] == [30.0]


# --------------------------------------------------------------------------
# catalogue exports and cone search
# --------------------------------------------------------------------------
def test_candidates_csv(client, fake_dashboards):
    from astropy.io import ascii
    response = client.get("/api/candidates.csv", headers=_ip())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["x-pipeline-version"]
    assert response.text.startswith("# SPHEREx-Lens candidate catalogue")
    table = ascii.read(response.text, format="csv", comment="#")
    assert len(table) == 4
    for column in _detections.COLUMNS:
        assert column in table.colnames
    assert set(table["config_hash"].filled("")) >= {"abc123abc123"}


def _parse_votable(content):
    from astropy.io.votable import parse
    return parse(io.BytesIO(content))


def test_candidates_votable(client, fake_dashboards):
    response = client.get("/api/candidates.votable", headers=_ip())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-votable+xml")
    vot = _parse_votable(response.content)
    table = vot.get_first_table()
    assert len(table.array) == 4
    params = {p.name: p.value for p in table.params}
    assert "server_pipeline_version" in params and "server_config_hash" in params
    ucds = {f.name: f.ucd for f in table.fields}
    assert ucds["ra"] == "pos.eq.ra;meta.main"


def test_cone_search_returns_scs_ucds_and_hits(client, fake_dashboards):
    response = client.get("/api/cone?RA=133.78&DEC=-7.24&SR=0.1", headers=_ip())
    assert response.status_code == 200
    table = _parse_votable(response.content).get_first_table()
    ucds = {f.ucd for f in table.fields}
    assert {"ID_MAIN", "POS_EQ_RA_MAIN", "POS_EQ_DEC_MAIN"} <= ucds
    assert len(table.array) == 3                     # Field_B is far away
    assert table.array["dist_deg"][0] == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("query", [
    "RA=10&DEC=20&SR=1.5",          # SR over the 1 degree cap
    "DEC=20&SR=0.1",                # missing RA
    "RA=10&DEC=95&SR=0.1",          # Dec out of range
    "RA=abc&DEC=20&SR=0.1",         # not a number
    "RA=nan&DEC=20&SR=0.1",
    "RA=10&DEC=20&SR=-1",
])
def test_cone_search_bad_input_is_error_votable(client, fake_dashboards, query):
    response = client.get(f"/api/cone?{query}", headers=_ip())
    assert response.status_code == 200                 # SCS: errors travel in the VOTable
    vot = _parse_votable(response.content)
    infos = {i.name: i.value for i in vot.infos}
    assert infos.get("Error")
    assert any(i.name == "QUERY_STATUS" and i.value == "ERROR"
               for r in vot.resources for i in r.infos)


# --------------------------------------------------------------------------
# health and credits
# --------------------------------------------------------------------------
def test_health_live_is_offline(client, monkeypatch):
    import requests

    def boom(*a, **k):
        raise AssertionError("health/live must not use the network")
    monkeypatch.setattr(requests, "get", boom)
    data = client.get("/api/health/live").json()
    assert data["ok"] is True and data["pipeline"]["config_hash"]


class _FakeResponse:
    def __init__(self, status):
        self.status_code = status

    def close(self):
        pass


def test_health_reports_archive_down_but_stays_ok(client, monkeypatch):
    from spherexlens.routes import health
    import requests

    def fake_get(url, **kwargs):
        if "cadc" in url:
            raise requests.ConnectTimeout("slow")
        return _FakeResponse(503 if "vizier" in url else 200)
    monkeypatch.setattr(health.requests, "get", fake_get)
    response = client.get("/api/health", headers=_ip())
    data = response.json()
    assert response.status_code == 200 and data["ok"] is True
    assert data["archives_down"] == ["cadc", "vizier"]
    assert data["archives"]["irsa_sia"]["status"] == "up"
    assert data["snapshot"]["status"] == "disabled"     # conftest turns reads off


def test_health_not_ok_when_snapshot_unreadable(client, monkeypatch, portal):
    from spherexlens.routes import health
    monkeypatch.setattr(health, "probe_archives", lambda force=False: {})
    monkeypatch.setattr(portal, "_snapshot_mode", lambda: "bucket")

    def broken():
        raise RuntimeError("bucket gone")
    monkeypatch.setattr(portal, "_snapshot_store", broken)
    response = client.get("/api/health", headers=_ip())
    assert response.status_code == 503 and response.json()["ok"] is False


def test_credits_have_acknowledgements_and_urls(client):
    data = client.get("/api/credits").json()
    ids = {s["id"] for s in data["sources"]}
    assert {"irsa_spherex", "gaia", "mast", "jpl_ssd", "cadc_ssois", "neossat",
            "vsx", "wiseview", "irsa_wise"} <= ids
    for source in data["sources"]:
        assert source["acknowledgement"] and source["licence"]
        assert source["url"].startswith(("https://", "http://"))
        assert isinstance(source["wording_verified"], bool)
    neossat = next(s for s in data["sources"] if s["id"] == "neossat")
    assert "Open Government Licence" in neossat["licence"]
    assert neossat["url"] == "https://data.asc-csa.gc.ca/dataset/neossat"


# --------------------------------------------------------------------------
# job queue
# --------------------------------------------------------------------------
def _wait(queue, job_id, states=("succeeded", "failed", "timed_out"), timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = queue.status(job_id)
        if status["state"] in states:
            return status
        time.sleep(0.01)
    raise AssertionError(f"job stuck: {queue.status(job_id)}")


def test_job_queue_runs_reports_and_hides_errors():
    queue = jobs.JobQueue(max_concurrent=1, max_queued=4, per_client=3)
    try:
        ok = queue.submit("t", lambda report: (report({"stage": "x", "message": "hi"}),
                                               {"value": 1})[1], "c1")
        bad = queue.submit("t", lambda report: 1 / 0, "c1")
        assert _wait(queue, ok.id)["result"] == {"value": 1}
        assert queue.status(ok.id)["progress"] == {"stage": "x", "message": "hi"}
        failed = _wait(queue, bad.id)
        assert failed["state"] == "failed"
        assert failed["error"] == {"error": "The job failed (ZeroDivisionError)."}
    finally:
        queue.shutdown()


def test_job_queue_limits_and_expiry():
    import threading
    gate = threading.Event()
    queue = jobs.JobQueue(max_concurrent=1, max_queued=1, per_client=1,
                          ttl_s=0.05, timeout_s=60)
    try:
        first = queue.submit("t", lambda r: gate.wait(5) and {"ok": 1}, "a")
        with pytest.raises(jobs.QueueFull):
            queue.submit("t", lambda r: {}, "a")           # per-client limit
        second = queue.submit("t", lambda r: {"ok": 2}, "b")
        assert queue.status(second.id)["queue_position"] == 1
        with pytest.raises(jobs.QueueFull):
            queue.submit("t", lambda r: {}, "c")           # queue full
        gate.set()
        _wait(queue, second.id)
        time.sleep(0.1)
        assert queue.status(first.id) is None              # expired after TTL
    finally:
        gate.set()
        queue.shutdown()


def test_job_queue_times_out_long_jobs():
    import threading
    gate = threading.Event()
    queue = jobs.JobQueue(max_concurrent=1, max_queued=1, per_client=2,
                          ttl_s=60, timeout_s=0.05)
    try:
        job = queue.submit("t", lambda r: gate.wait(5) and {"late": True}, "a")
        status = _wait(queue, job.id, states=("timed_out",))
        assert "abandoned" in status["error"]["error"]
        gate.set()
        time.sleep(0.05)
        assert queue.status(job.id)["state"] == "timed_out"   # late result dropped
    finally:
        gate.set()
        queue.shutdown()


def test_field_job_routes(client, portal, monkeypatch):
    queue = jobs.JobQueue(max_concurrent=1, max_queued=2, per_client=2)
    jobs.set_queue(queue)
    calls = []

    def fake_register(ra, dec, name, size, slots, epochs, catalogs, known_fingerprint=""):
        portal._emit_progress("triage", "classifying")
        calls.append((ra, dec, name))
        return {"target": "My_Field", "n_detections": 0}
    monkeypatch.setattr(portal, "_register_field", fake_register)
    try:
        bad = client.post("/api/jobs/field", headers=_ip(), json={"ra": 400, "dec": 0})
        assert bad.status_code == 422
        bad_size = client.post("/api/jobs/field", headers=_ip(),
                               json={"ra": 10, "dec": 0, "size": 3})
        assert bad_size.status_code == 422
        accepted = client.post("/api/jobs/field", headers=_ip(),
                               json={"ra": 10.5, "dec": -3, "name": "My Field"})
        assert accepted.status_code == 202
        job_id = accepted.json()["id"]
        _wait(queue, job_id)
        status = client.get(f"/api/jobs/{job_id}", headers=_ip()).json()
        assert status["state"] == "succeeded"
        assert status["result"]["target"] == "My_Field"
        assert status["progress"]["stage"] == "triage"
        assert calls == [(10.5, -3.0, "My Field")]
        assert client.get("/api/jobs/" + "0" * 32, headers=_ip()).status_code == 404
        assert client.get("/api/jobs/not-an-id", headers=_ip()).status_code == 404
    finally:
        jobs.set_queue(None)


def test_cloudrun_jobs_backend_is_a_clear_stub(client, monkeypatch):
    jobs.set_queue(jobs.CloudRunJobsBackend())
    try:
        response = client.post("/api/jobs/field", headers=_ip(), json={"ra": 10, "dec": 0})
        assert response.status_code == 501
    finally:
        jobs.set_queue(None)


# --------------------------------------------------------------------------
# "Open any field" input validation (security review S1-S3)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("query", [
    "ra=361&dec=0", "ra=-1&dec=0", "ra=10&dec=91", "ra=nan&dec=0", "ra=inf&dec=0",
    "ra=10&dec=0&size=0.6", "ra=10&dec=0&slots=13",
    "ra=10&dec=0&name=" + "a" * 81, "ra=10&dec=0&known=not-hex!",
    "ra=10&dec=0&epochs=everything",
])
@pytest.mark.parametrize("path", ["/api/field", "/api/field/stream"])
def test_field_rejects_bad_input(client, path, query):
    response = client.get(f"{path}?{query}", headers=_ip())
    assert response.status_code == 422, response.text


def test_field_input_error_helper(portal):
    assert portal.field_input_error(10, 0, "ok", 0.2, 4) is None
    assert portal.field_input_error(float("nan"), 0)
    assert portal.field_input_error(10, 0, size=0.01)
    assert portal.field_input_error(10, 0, slots=True)


def test_register_field_name_collision_is_bounded(portal, monkeypatch):
    monkeypatch.setattr(portal, "load_target",
                        lambda name: {"meta": {"ra": 1.0, "dec": 1.0}})
    started = time.monotonic()
    response = portal._register_field(10.0, 20.0, "x" * 80, 0.2, 0, "firstlast", 0)
    assert response.status_code == 409
    assert time.monotonic() - started < 5


def test_register_field_sanitises_label_and_hides_exception_text(portal, monkeypatch):
    def failing_listing(*args, **kwargs):
        raise RuntimeError("secret internal path C:/srv/x")
    monkeypatch.setattr(portal, "query_sia2", failing_listing)
    response = portal._register_field(10.0, 20.0, "<b>Hi</b>", 0.2, 0, "firstlast", 0)
    assert response.status_code == 502
    assert b"secret internal path" not in response.body

    seen = {}

    def fake_dashboard(target, **kwargs):
        seen["meta"] = dict(portal.FIELD_REGISTRY[target]["meta"])
        return {"target": target, "stats": {}}
    monkeypatch.setattr(portal, "query_sia2", lambda *a, **k: [])
    monkeypatch.setattr(portal, "get_dashboard", fake_dashboard)
    result = portal._register_field(10.0, 20.0, "<b>Hi</b>", 0.2, 0, "firstlast", 0)
    assert result["target"] == "b_Hi_b"
    assert seen["meta"]["common"] == "bHi/b"
    portal.FIELD_REGISTRY.pop(result["target"], None)
    portal.FIELD_DASH_CACHE.pop(result["target"], None)


def test_s8_netlify_signature_gates_the_visitor_header(monkeypatch):
    import base64, hashlib, hmac, json, time
    from spherexlens import http_guard as g

    def tok(secret, claims, alg="HS256"):
        enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
        head = enc({"alg": alg, "typ": "JWT"}) + "." + enc(claims)
        sig = hmac.new(secret.encode(), head.encode(), hashlib.sha256).digest()
        return head + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()

    base = {"x-nf-client-connection-ip": "203.0.113.9", "x-forwarded-for": "198.51.100.7"}
    monkeypatch.delenv("SPHEREX_PROXY_SECRET", raising=False)
    assert g.trusted_client_ip(base, "x") == "203.0.113.9"          # no secret: old behaviour
    monkeypatch.setenv("SPHEREX_PROXY_SECRET", "s3cret")
    assert g.trusted_client_ip(base, "x") == "198.51.100.7"         # unsigned: forged header ignored
    good = dict(base, **{"x-nf-sign": tok("s3cret", {"iss": "netlify", "exp": time.time() + 60})})
    assert g.trusted_client_ip(good, "x") == "203.0.113.9"
    bad = dict(base, **{"x-nf-sign": tok("wrong", {"iss": "netlify"})})
    assert g.trusted_client_ip(bad, "x") == "198.51.100.7"
    old = dict(base, **{"x-nf-sign": tok("s3cret", {"iss": "netlify", "exp": time.time() - 5})})
    assert g.trusted_client_ip(old, "x") == "198.51.100.7"
    none_alg = dict(base, **{"x-nf-sign": tok("s3cret", {"iss": "netlify"}, alg="none")})
    assert g.trusted_client_ip(none_alg, "x") == "198.51.100.7"
