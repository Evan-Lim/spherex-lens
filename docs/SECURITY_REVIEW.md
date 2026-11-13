# SPHEREx-Lens security review (Phase 9, v2)

Reviewed: 2026-11-13, branch `wt/prod`. Scope: the public FastAPI app (`app.py`),
the new Phase 9 routes (`spherexlens/routes/{votes,catalog_export,health,credits,jobs}.py`),
`spherexlens/votes.py`, `spherexlens/jobs.py`, Terraform in `cloud/terraform/`,
the Netlify proxy (`netlify.toml`, `site/_redirects`), and `static/` and `site/`
(checked for secrets only), and `spherexlens/routes/deep.py` from `exec-plan-v1`.

Severity: **High** means it can cost money or take the service down for
everyone. **Medium** means abuse is possible but limited, or it leaks internal
details. **Low** means hardening or hygiene.
Status: **Fixed** (in this branch), **For user** (outside this agent's files,
or a deployment decision), **Accepted** (documented, no change).

Line numbers refer to `app.py` on this branch.

## Summary

| # | Sev | Area | Finding | Status |
|---|---|---|---|---|
| S1 | Medium | `/api/field`, `/api/field/stream` (`app.py:2680-2759`) | RA/Dec were checked only inside `add_field`. NaN/inf passed the Query parsing. `known` took any 128 characters. Size/slot limits were only hard-coded Query bounds. | **Fixed** |
| S2 | High | `/api/field/stream` (`app.py:2735`) | No rate limit on the SSE variant of "Open any field" (the one the site uses). Each call starts a full IRSA fetch and pipeline run. | **Fixed** (5/min per visitor, same as `/api/field`) |
| S3 | High | `_register_field` name-collision loop (`app.py:2815-2828`) | `while taken: safe = safe[:70] + "_x"` produces the same 72-character name every time after the first round. A name colliding at that length looped forever while holding `FIELD_REQUEST_LIMIT`. One request could block the field loader on that instance, and every iteration also read GCS. | **Fixed** (20 numbered attempts, then HTTP 409) |
| S4 | Medium | `_register_field` errors (`app.py:2856, 2889`) | `f"IRSA listing query failed: {e}"` returned the raw exception text (URLs, internal hostnames, paths) to the client. | **Fixed** (only the type name goes to the client; the details are logged) |
| S5 | Low | `_register_field` label | The raw `name` was echoed back as `common` and rendered by the UI. The UI escapes it today (`fmt.escape`/`textContent`), but the server did not. | **Fixed** (`_display_name` strips control and HTML characters) |
| S6 | High | `/api/dashboard/stream` (`app.py:2446`), `/api/recovery/stream` (`app.py:2646`), `/api/mosaic/tile/stream` (`app.py:3030`) | Same issue as S2: these live-pipeline SSE routes have no per-visitor rate limit. The live service caps them (concurrency 1, max 6 instances), which limits concurrency but not total spend. | **Fixed** (integration, 2026-11-13): `/api/dashboard/stream` and `/api/recovery/stream` 10/min, `/api/mosaic/tile/stream` 60/min per visitor; `target` max 80 characters. |
| S7 | Medium | Error text in `get_dashboard`/`get_recovery` (`app.py:2064, 2082, 2172, 2187, 2582, 2630`) | `str(e)` from IRSA, co-add and ZOGY failures is returned to the client. It can include archive URLs and internal file paths. | **Fixed** (integration): `_public_error()` sends the step and exception type only; the exception text is logged server-side. |
| S8 | Medium | `_client_key` (`app.py:69-89`) | The rate-limit key trusts `x-nf-client-connection-ip`, which any caller reaching Cloud Run directly can forge, so limits can be bypassed by rotating the header. The docstring already says this. The new routes reuse the same key, so the same applies to them, including vote stuffing. | **Fixed in code; switch-on pending**: Netlify signs proxied requests (`signed = "SPHEREX_PROXY_SECRET"` in netlify.toml, `X-Nf-Sign` HS256 JWT); with `SPHEREX_PROXY_SECRET` on Cloud Run (Terraform `proxy_signing = true`, Secret Manager `spherex-proxy-secret`) the visitor header is trusted only on a valid signature, else Cloud Run's own X-Forwarded-For hop. `/api/health/live` reports `proxy_signature`. Steps: cloud/README.md "S8 signed Netlify proxy" (needs the Netlify owner). |
| S9 | Low | Refresh token compare (`app.py:1923`) | Uses `hmac.compare_digest`, which is constant-time. The length check (>1024) runs first and reveals only that bound. The token comes from Secret Manager and is header-only, never in URLs or logs. | **Accepted** (no issue) |
| S10 | Low | CORS (`app.py:55-67`) | Exact-origin allowlist from `SPHEREX_CORS_ORIGINS`, no credentials, `allow_headers=["*"]`. With no origins configured, no CORS headers are sent. Fine for a public, credential-less API. | **Accepted**. Optional: narrow `allow_headers` to `Content-Type, X-Refresh-Token`. |
| S11 | Low | SSRF | No user-controlled URL is ever fetched. The refresh proxy and snapshot API URLs come from environment variables (HTTPS checked). `snapshot=` is matched only against the manifest chain. Targets are regex-checked or looked up in the registry. The health probes use a fixed URL list. | **Accepted** (no issue found) |
| S12 | Low | Secrets in repo/static | `git grep` over `static/`, `site/`, `*.py`, `*.tf`, `*.toml`, `*.yaml`, `*.json`, `*.ps1` and `Dockerfile*`, plus a check of every file ever added to git history, found no keys, tokens, `.env` files, tfvars or tfstate. `.gitignore` covers `terraform.tfvars` and `terraform.tfstate*`. The operator token is typed into a password field and sent only as a header. | **Accepted** (clean) |
| S13 | Low | Dependency pinning (`requirements.txt`) | Only lower bounds (`>=`), apart from google-cloud-storage and slowapi. Builds are not reproducible, and a compromised or breaking upstream release would be picked up silently. | **Fixed** (integration): `requirements.lock` with sha256 hashes, installed with `--require-hashes` in both images; `pip-audit` in CI. The audit found CVE-2026-126190 in soupsieve 2.10 -> pinned soupsieve 4.1; no known vulnerabilities remain. |
| S14 | Low | `Dockerfile.cloud` | The container runs as root. | **Fixed** (integration): both images create and switch to an unprivileged user (uid 10001). |
| S15 | Medium | New: `POST /api/votes` | Votes are validated (vote value, `detection_id` grammar and length ≤200, the target must be a served field and must match the ID). Limited to 30/min per visitor, body limited by the Pydantic `max_length`s. Votes are stored create-only (the API service account cannot overwrite or delete them), and the client is stored only as a salted hash. Remaining risk: S8 (forged visitor key, so ballot stuffing). | **Fixed** (in design). The rest is covered by S8. |
| S16 | Low | New: `/api/cone`, `/api/candidates.*` | Inputs are parsed as finite floats ≤32 characters, range-checked, with SR ≤1°. Errors are SCS error VOTables carrying fixed messages. The detection list is cached for 3 min, so a request never triggers a pipeline run when a snapshot store is configured. Limits: 60/min (cone), 20/min (exports). | **Fixed** (in design) |
| S17 | Low | New: `/api/health` | Archive probes use fixed URLs, a 4 s timeout and a 5 min cache, and run behind a lock so concurrent callers cannot multiply the outbound requests. Limit 30/min. `/api/health/live` touches nothing external. | **Fixed** (in design) |
| S18 | Medium | New: `/api/jobs/*` | Bounded queue (1 running, 8 waiting by default), at most 2 unfinished jobs per visitor, 5 submissions/min, results kept 30 min. Job IDs are 128-bit random, so another visitor's ID cannot be guessed. Failures report only the exception type. A job past `JOB_TIMEOUT_S` is marked `timed_out`, but its thread keeps running (Python threads cannot be killed), so the hard ceiling is the pool size. | **Fixed** (in design). Limitation documented. |
| S19 | Low | Logging | The new JSON request logs record the request ID, route, method, status and duration. They never include the client IP, token or body. Exceptions are logged by type only. | **Accepted** |
| S20 | Low | `/api/status`, `/api/refresh-status`, `/api/recovery/stream` | The `target` query parameter has no `max_length` or regex (it is only used as a dict key, and 404 messages echo it). | **Fixed** (integration): `target` is `max_length=80` with pattern `^[A-Za-z0-9_]+$` on `/api/status`, `/api/snapshot-history`, `/api/dashboard`, `/api/recovery`, and length-capped on the streams. |
| S21 | Low | `/api/deep`, `/api/deep/{name}` (`spherexlens/routes/deep.py`, on `exec-plan-v1`; Phase 7 agent) | Both are read-only and only open files under `$SPHEREX_DEEP_DIR`. The name regex `^[A-Za-z0-9_.-]{1,64}$` excludes `/` and `\`, so path traversal is not possible. Remaining issues: (a) no rate limit, and `/api/deep` opens and parses every JSON file on each request without caching, so its cost grows with the directory; (b) `re.match` with `$` also accepts a trailing newline (`%0A`), which is harmless but sloppy; (c) the 404 message returns the operator CLI command; (d) `open()` has no `encoding=`. | **Fixed** (integration): `fullmatch`, 60/min rate limit on both routes, mtime-keyed cache (bucket reads cached 5 min on the cloud), plain 404 without the CLI command. |

## What was changed in this branch

* `app.py`, limited to the "Open any field" input path:
  * `field_input_error()` is a single validator used by `/api/field`,
    `/api/field/stream` and `POST /api/jobs/field`. It requires finite RA in
    [0, 360] and Dec in [-90, 90], size within
    `config.Production.MAX_FIELD_SIZE_DEG`, slots within `MAX_LIVE_SLOTS`, and a
    name of at most `FIELD_NAME_MAX_LEN` characters.
  * `known` must be a hex fingerprint (`^[0-9a-f]{0,64}$`).
  * `/api/field/stream` is rate-limited (5/min) and validates its input before
    the stream starts, so a bad request gets a 422 instead of an SSE error event.
  * The name-collision loop is bounded (S3), exception text no longer reaches
    clients (S4), and the display name is sanitised (S5).
* New routes use `spherexlens/http_guard.py` (per-visitor in-memory limiter,
  the same visitor key as the app) and `spherexlens/logging_setup.py`
  (structured logs).

## Tests

`tests/test_production.py` covers S1 (20 bad-input cases across both field
routes), S3 (the collision loop finishes with 409), S4/S5 (no exception text,
sanitised label), and votes, cone and job input validation and limits.

## Rate-limit inventory

| Route | Limit (per visitor, per instance) | Where |
|---|---|---|
| `/api/dashboard` | 30/min | app.py (slowapi) |
| `/api/recovery` | 30/min | app.py |
| `/api/field`, `/api/field/stream` | 5/min each + `FIELD_REQUEST_LIMIT` concurrency | app.py (`/stream` added here) |
| `/api/refresh` | 3/min + operator token | app.py |
| `/api/targets`, `/api/snapshot-*`, `/api/mosaic` | 60/min | app.py |
| `/api/dashboard/stream`, `/api/recovery/stream`, `/api/mosaic/tile/stream` | 10/min (60/min tile) | app.py |
| `/api/status`, `/api/refresh-status` | none (cheap cached reads) | app.py |
| `POST /api/votes` | 30/min | routes/votes.py |
| `/api/votes/queue` | 60/min | routes/votes.py |
| `/api/candidates.csv`, `.votable` | 20/min | routes/catalog_export.py |
| `/api/cone` | 60/min | routes/catalog_export.py |
| `/api/health` | 30/min (probes cached 5 min) | routes/health.py |
| `/api/health/live`, `/api/credits` | none (static, no I/O) | routes/health.py, credits.py |
| `POST /api/jobs/field` | 5/min + 2 unfinished jobs per visitor + bounded queue | routes/jobs.py |
| `/api/jobs/{id}` | 120/min | routes/jobs.py |
| `/api/deep`, `/api/deep/{name}` | 60/min | routes/deep.py |
| other Phase routes (`/api/coverage`, `/api/metrics*`, `/api/model`) | not reviewed here (not on this branch) | |
