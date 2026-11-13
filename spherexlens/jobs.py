"""
spherexlens.jobs
A bounded in-process job queue for long "Open any field" runs.

    queue = get_queue()
    job = queue.submit(kind, func, client)     # func(report) -> JSON-able result
    queue.status(job_id) -> dict | None

* At most ``config.Production.MAX_CONCURRENT_JOBS`` run at once (thread pool);
  at most ``MAX_QUEUED_JOBS`` are waiting; each client (salted hash of its
  address) may hold ``MAX_JOBS_PER_CLIENT`` unfinished jobs.  Over a limit,
  ``submit`` raises ``QueueFull`` (HTTP 429).
* Finished jobs are kept for ``JOB_RESULT_TTL_S`` and then forgotten.
* A running job past ``JOB_TIMEOUT_S`` is reported as ``timed_out``; Python
  threads cannot be killed, so its thread finishes in the background and its
  late result is discarded.

The queue lives in one process: on Cloud Run a job id is only known to the
instance that accepted it (set session affinity on the live service, or use
``SPHEREX_JOBS_BACKEND=cloudrun`` -- see CloudRunJobsBackend below).
"""
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import config

STATES = ("queued", "running", "succeeded", "failed", "timed_out")
ACTIVE = ("queued", "running")


class QueueFull(RuntimeError):
    """Too many jobs (overall or for this client)."""


class BackendUnavailable(RuntimeError):
    """The configured job backend cannot accept jobs."""


class Job:
    __slots__ = ("id", "kind", "client", "state", "created", "started", "finished",
                 "result", "error", "progress", "params")

    def __init__(self, kind, client, params=None):
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.client = client
        self.state = "queued"
        self.created = time.time()
        self.started = None
        self.finished = None
        self.result = None
        self.error = None
        self.progress = None
        self.params = params or {}

    def public(self, position=None):
        def iso(t):
            return (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
                    if t else None)
        out = {"id": self.id, "kind": self.kind, "state": self.state,
               "created_at": iso(self.created), "started_at": iso(self.started),
               "finished_at": iso(self.finished), "params": self.params,
               "progress": self.progress}
        if position is not None:
            out["queue_position"] = position
        if self.state == "succeeded":
            out["result"] = self.result
        if self.error is not None:
            out["error"] = self.error
        return out


class JobQueue:
    def __init__(self, max_concurrent=None, max_queued=None, per_client=None,
                 ttl_s=None, timeout_s=None):
        prod = config.Production
        self.max_concurrent = max(1, int(max_concurrent or prod.MAX_CONCURRENT_JOBS))
        self.max_queued = int(max_queued if max_queued is not None else prod.MAX_QUEUED_JOBS)
        self.per_client = int(per_client or prod.MAX_JOBS_PER_CLIENT)
        self.ttl_s = float(ttl_s or prod.JOB_RESULT_TTL_S)
        self.timeout_s = float(timeout_s or prod.JOB_TIMEOUT_S)
        self._pool = ThreadPoolExecutor(max_workers=self.max_concurrent,
                                        thread_name_prefix="spherex-job")
        self._jobs = {}
        self._lock = threading.Lock()

    # -- bookkeeping ------------------------------------------------------
    def _expire(self, now):
        for job in list(self._jobs.values()):
            if (job.state == "running" and job.started
                    and now - job.started > self.timeout_s):
                job.state, job.finished = "timed_out", now
                job.error = {"error": "The job ran longer than "
                                      f"{int(self.timeout_s)} s and was abandoned."}
            if job.finished and now - job.finished > self.ttl_s:
                del self._jobs[job.id]

    def _active(self):
        return [j for j in self._jobs.values() if j.state in ACTIVE]

    # -- API --------------------------------------------------------------
    def submit(self, kind, func, client, params=None):
        with self._lock:
            now = time.time()
            self._expire(now)
            active = self._active()
            if sum(1 for j in active if j.client == client) >= self.per_client:
                raise QueueFull(f"You already have {self.per_client} unfinished "
                                "job(s); wait for one to finish.")
            if len(active) >= self.max_concurrent + self.max_queued:
                raise QueueFull("The job queue is full; try again in a few minutes.")
            job = Job(kind, client, params)
            self._jobs[job.id] = job
        self._pool.submit(self._run, job, func)
        return job

    def _run(self, job, func):
        with self._lock:
            if job.state != "queued":
                return
            job.state, job.started = "running", time.time()

        def report(payload):
            if isinstance(payload, dict):
                job.progress = {k: payload.get(k) for k in
                                ("stage", "message", "completed", "total")
                                if payload.get(k) is not None}

        try:
            result = func(report)
            error = None
        except Exception as exc:          # never leak the message to clients
            result, error = None, {"error": f"The job failed ({type(exc).__name__})."}
        with self._lock:
            if job.state != "running":    # timed out meanwhile: drop the result
                return
            job.finished = time.time()
            if error is None and isinstance(result, dict) and result.get("_job_error"):
                error = {k: v for k, v in result.items() if k != "_job_error"}
            if error is None:
                job.state, job.result = "succeeded", result
            else:
                job.state, job.error = "failed", error

    def status(self, job_id):
        with self._lock:
            self._expire(time.time())
            job = self._jobs.get(job_id)
            if job is None:
                return None
            position = None
            if job.state == "queued":
                queued = sorted((j for j in self._jobs.values() if j.state == "queued"),
                                key=lambda j: j.created)
                position = next(i for i, j in enumerate(queued) if j.id == job.id) + 1
            return job.public(position)

    def stats(self):
        with self._lock:
            self._expire(time.time())
            counts = dict.fromkeys(STATES, 0)
            for job in self._jobs.values():
                counts[job.state] += 1
            return {"max_concurrent": self.max_concurrent, "max_queued": self.max_queued,
                    "per_client": self.per_client, **counts}

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)


class CloudRunJobsBackend:
    """Run each field job as a Cloud Run Job execution (SPHEREX_JOBS_BACKEND=cloudrun).

    Not implemented yet -- documented stub.  The intended design, reusing the
    trigger pattern of app._invoke_cloud_refresh_job:

    1. POST https://run.googleapis.com/v2/projects/P/locations/R/jobs/J:run
       with ``overrides.containerOverrides[0].args`` =
       ["python", "-m", "spherexlens.field_worker", "--ra", .., "--dec", .., "--job-id", id]
       using the API service account (it already holds roles/run.invoker on the
       refresh job; a separate ``spherex-field`` job would need the same).
    2. The worker writes ``jobs/<id>.json`` (status + dashboard) to a private
       bucket; GET /api/jobs/{id} reads that object, so any API instance can
       answer.
    The worker module, the job resource and the bucket do not exist yet.
    """

    def submit(self, *args, **kwargs):
        raise BackendUnavailable(
            "SPHEREX_JOBS_BACKEND=cloudrun is not implemented yet; unset it to use "
            "the in-process queue.")

    def status(self, job_id):
        return None

    def stats(self):
        return {"backend": "cloudrun", "implemented": False}


_QUEUE = {"value": None}
_QUEUE_LOCK = threading.Lock()


def get_queue():
    with _QUEUE_LOCK:
        if _QUEUE["value"] is None:
            backend = os.environ.get("SPHEREX_JOBS_BACKEND", "local").strip().lower()
            _QUEUE["value"] = CloudRunJobsBackend() if backend == "cloudrun" else JobQueue()
        return _QUEUE["value"]


def set_queue(queue):
    """Replace the process queue (tests)."""
    with _QUEUE_LOCK:
        old, _QUEUE["value"] = _QUEUE["value"], queue
    if isinstance(old, JobQueue) and old is not queue:
        old.shutdown()
