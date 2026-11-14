// SPHERExLens -- dashboard frontend
//
// Two data modes:
//   server  (default)  -> FastAPI, /api/*
//   static  (<body data-mode="static">) -> pre-rendered api/*.json
//
// With data-api-base="/live-api", the backend is authoritative for registered
// snapshots and the static site remains the UI shell.
//
// The map is a small interactive canvas engine: pan, zoom, three data layers,
// and overlays for the survey grid, catalog stars and detections.

const STATIC_MODE = document.body.dataset.mode === 'static';
const API_BASE = window.location.origin;
let LIVE_API = (document.body.dataset.apiBase || '').replace(/\/+$/, '');
const LIVE_PIPELINE_API = (document.body.dataset.liveApi || '').replace(/\/+$/, '');

function apiUrl(kind, target, extra = {}) {
  const params = [];
  if (target) params.push(`target=${encodeURIComponent(target)}`);
  if (state.live && (kind === 'dashboard' || kind === 'recovery')) params.push('source=live');
  Object.entries(extra).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') {
      params.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`);
    }
  });
  const query = params.length ? `?${params.join('&')}` : '';
  if (STATIC_MODE && !Object.keys(extra).length) {
    const dir = document.body.dataset.apiDir || 'api';
    const suffix = target ? `-${encodeURIComponent(target)}` : '';
    return `${dir}/${kind}${suffix}.json`;
  }
  return `${API_BASE}/api/${kind}${query}`;
}

// A static build with no declared base may still be sitting behind a proxy
// (the /live-api redirect in netlify.toml). Probe once, quietly.
if (STATIC_MODE && !LIVE_API) {
  fetch('/live-api/targets').then((r) => {
    if (!r.ok) return;
    LIVE_API = '/live-api';
    syncBackendUi();
  }).catch(() => {});
}

// Live mode and "open any field" both need a real Python backend. A static
// deployment usually has none, so say so up front instead of failing silently.
function backendAvailable() {
  return !STATIC_MODE || !!(LIVE_API || LIVE_PIPELINE_API);
}

function syncBackendUi() {
  const isOffline = offline.mode === 'offline';
  const ok = backendAvailable() && !isOffline;
  const toggle = document.getElementById('tog-live');
  const wrap = document.getElementById('toggle-live-wrap');
  const label = document.getElementById('tog-live-label');
  const hint = document.getElementById('live-hint');
  const fieldBtn = document.getElementById('btn-field');
  const fieldHint = document.getElementById('field-hint');

  if (toggle) {
    toggle.disabled = !ok;
    if (!ok) toggle.checked = false;
  }
  if (wrap) wrap.classList.toggle('is-disabled', !ok);
  if (label) {
    label.textContent = ok || isOffline
      ? 'Fresh IRSA'
      : 'Fresh IRSA (no backend)';
  }
  if (hint && isOffline) {
    hint.textContent = 'Unavailable offline: fetching from IRSA needs a connection. '
      + 'It switches back on automatically when the connection returns.';
    hint.classList.add('hint-warn');
  } else if (hint) {
    hint.textContent = ok
      ? (STATIC_MODE
        ? 'Unchecked: latest published Cloud snapshot. Checked: fetches fresh SPHEREx listings and cutouts for this field only; it does not publish a snapshot or refresh catalogs/WISE history.'
        : 'Unchecked: the configured snapshot or local cache. Checked: fetches fresh SPHEREx listings and cutouts for this field only; it does not publish a snapshot or refresh catalogs/WISE history.')
      : 'unavailable: this deployment is pre-rendered and has no Python server behind it. '
        + 'Every field in the dropdown is static JSON. Run app.py locally (or proxy '
        + 'one) to switch these on.';
    hint.classList.toggle('hint-warn', !ok);
  }
  // Offline, coordinates saved in History still open, so the inputs stay on.
  const fieldOk = ok || isOffline;
  if (fieldBtn) {
    fieldBtn.disabled = !fieldOk;
    fieldBtn.classList.toggle('is-disabled', !fieldOk);
  }
  ['field-ra', 'field-dec', 'field-name'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = !fieldOk;
  });
  if (fieldHint && isOffline) {
    fieldHint.textContent = `${offlineWhy()} \u2014 only fields saved in History can be opened`;
    fieldHint.classList.add('hint-warn');
    fieldHint.dataset.offline = 'true';
  } else if (fieldHint && !ok) {
    fieldHint.textContent = 'needs a backend \u2014 not available on this static deployment';
    fieldHint.classList.add('hint-warn');
  } else if (fieldHint && fieldHint.dataset.offline) {
    delete fieldHint.dataset.offline;
    fieldHint.textContent = '';
    fieldHint.classList.remove('hint-warn');
  }
  renderSnapshotControls();
}

// URL of the real pipeline. In a static build this is the proxy prefix.
function remoteUrl(kind, target, extra = {}) {
  const base = (state.live && LIVE_PIPELINE_API)
    || LIVE_API
    || `${API_BASE}/api`;
  const params = [];
  if (target) params.push(`target=${encodeURIComponent(target)}`);
  if (state.live && (kind === 'dashboard' || kind === 'recovery')) params.push('source=live');
  Object.entries(extra).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') {
      params.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`);
    }
  });
  const query = params.length ? `?${params.join('&')}` : '';
  return `${base}/${kind}${query}`;
}

// Dev switch: ?mock=v2 renders static/dev/mock_v2.json (every CONTRACT.md v2
// field filled) and the dev/mock_*.json files for the new endpoints, so the v2
// interface can be built and checked before the backend produces those fields.
const MOCK_MODE = new URLSearchParams(window.location.search).get('mock') || '';
const MOCK_V2 = MOCK_MODE === 'v2';

// When configured, the backend is authoritative for registered-field snapshots.
async function fetchApi(kind, target, extra = {}) {
  if (MOCK_V2 && kind === 'dashboard') return fetch('dev/mock_v2.json', { cache: 'no-store' });
  if (MOCK_V2 && kind === 'status') {
    return new Response('{}', { status: 404, headers: { 'Content-Type': 'application/json' } });
  }
  const options = state.live ? { cache: 'no-store' } : {};
  if (STATIC_MODE && (LIVE_API || (state.live && LIVE_PIPELINE_API))) {
    return fetch(remoteUrl(kind, target, extra), options);
  }
  return fetch(apiUrl(kind, target, extra), options);
}

function startActionProgress(message) {
  const started = performance.now();
  let current = message;
  const renderTime = () => {
    setText('action-time', `${((performance.now() - started) / 1000).toFixed(1)} s`);
  };
  const timer = window.setInterval(renderTime, 250);
  setText('action-message', current);
  renderTime();
  return {
    update(next) {
      current = next;
      setText('action-message', current);
      renderTime();
    },
    stop() {
      window.clearInterval(timer);
      setText('action-time', '');
    },
  };
}

function formatTimestamp(value) {
  if (!value) return 'unknown time';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
}

let cloudRefreshPollTimer = null;
let cloudRefreshRequestedAt = null;
let cloudRefreshPollStartedAt = null;   // B8: when we began polling for this run
let cloudRefreshSubmitting = false;
let latestCloudRefreshStatus = {};

// B8: stop polling after this many milliseconds even if the job never starts.
const CLOUD_REFRESH_WAIT_TIMEOUT_MS = 5 * 60 * 1000;  // 5 minutes
// Poll quickly while a refresh is running, slowly otherwise so a scheduled
// 12-hour run that starts while the page is open still shows up.
const CLOUD_REFRESH_ACTIVE_POLL_MS = 5000;
const CLOUD_REFRESH_IDLE_POLL_MS = 60 * 1000;
// The scheduler fires every 12 h and a run publishes at its end, so a
// snapshot older than 12 h + 2 h of slack means a scheduled run was missed.
const CLOUD_SNAPSHOT_OVERDUE_MS = 14 * 60 * 60 * 1000;
let cloudSnapshotSeenAt = null;   // published_at first seen by this page

function cloudRefreshUrl(kind) {
  // Status reads and the (fast) trigger go to the snapshot service. The live
  // pipeline service runs one request at a time, so polling it would queue
  // behind any IRSA fetch in progress.
  const base = STATIC_MODE
    ? (LIVE_API || LIVE_PIPELINE_API)
    : `${API_BASE}/api`;
  return base ? `${base}/${kind}` : null;
}

function scheduleCloudRefreshPoll(delay = CLOUD_REFRESH_ACTIVE_POLL_MS) {
  if (cloudRefreshPollTimer) window.clearTimeout(cloudRefreshPollTimer);
  cloudRefreshPollTimer = window.setTimeout(updateCloudRefreshStatus, delay);
}

function formatAge(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '';
  const minutes = Math.round(ms / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest && hours < 10 ? `${hours} h ${rest} min ago` : `${hours} h ago`;
}

function irsaCheckText(check) {
  const when = formatTimestamp(check.checked_at);
  const images = `${check.irsa_rows} image${check.irsa_rows === 1 ? '' : 's'}`;
  if (check.reused_snapshot) {
    return `IRSA checked ${when}: same ${images} the snapshot processed, ` +
      `so its full result (all epochs and bands, processed ${formatTimestamp(
        check.snapshot_processed_at)}) is shown`;
  }
  if (check.new_since_snapshot) {
    return `IRSA checked ${when}: ${images}, including new ones since the snapshot; processed fresh now`;
  }
  return `IRSA checked ${when}: ${images}; processed fresh now`;
}

function fieldLabel(name) {
  const option = [...($('target-select')?.options || [])]
    .find((o) => o.value === name);
  return (option && option.textContent.trim()) || name.replace(/_/g, ' ');
}

const FIELD_STATE_TEXT = {
  queued: 'queued',
  checking: 'checking IRSA now',
  updated: 'new SPHEREx data, reprocessed',
  unchanged: 'no new SPHEREx data',
  retained: 'new data unusable, previous snapshot kept',
  failed: 'failed, previous snapshot kept',
};

function renderCloudFieldBoard(status, running) {
  const board = $('cloud-field-board');
  const grid = $('cloud-field-grid');
  if (!board || !grid) return;
  const states = status.field_states && typeof status.field_states === 'object'
    ? status.field_states : {};
  const names = Object.keys(states);
  board.hidden = names.length === 0;
  if (!names.length) return;
  setText('cloud-field-caption', running
    ? 'Per-field progress (this run)'
    : 'Per-field result of the latest run');
  grid.replaceChildren(...names.map((name) => {
    const fieldState = states[name];
    const item = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'field-cell';
    button.dataset.state = fieldState;
    button.dataset.target = name;
    const label = fieldLabel(name);
    button.textContent = label;
    const description = `${label}: ${FIELD_STATE_TEXT[fieldState] || fieldState}`;
    button.title = description;
    button.setAttribute('aria-label', `${description}. Open this field.`);
    if (name === $('target-select')?.value) button.setAttribute('aria-current', 'true');
    item.append(button);
    return item;
  }));
}

function renderCloudFreshness(status, running) {
  const badge = $('cloud-freshness');
  if (!badge) return;
  const total = Number(status.total_targets) || 18;
  const completed = Number(status.completed_targets) || 0;
  const publishedMs = Date.parse(status.snapshot_published_at || '');
  const age = Date.now() - publishedMs;
  let state = 'unknown';
  let text = 'no snapshot';
  if (status.status === 'offline') {
    state = 'offline';
    text = deviceOffline() ? 'offline' : 'server down';
  } else if (running || status.awaiting_requested_run) {
    state = 'refreshing';
    text = running ? `refreshing ${completed}/${total}` : 'starting refresh';
  } else if (Number.isFinite(publishedMs)) {
    state = age > CLOUD_SNAPSHOT_OVERDUE_MS ? 'overdue' : 'fresh';
    text = state === 'overdue' ? 'overdue' : 'fresh';
  }
  badge.dataset.state = state;
  badge.textContent = text;
  badge.title = state === 'overdue'
    ? 'The published snapshot is older than one 12-hour cycle plus 2 h; a scheduled run was missed or failed.'
    : (state === 'offline'
      ? (deviceOffline()
        ? 'This device is offline: showing the copy saved on this device.'
        : 'The SPHEREx-Lens server is not answering (your internet is fine): showing the copy saved on this device.')
      : '');

  const progress = $('cloud-refresh-progress');
  if (progress) {
    progress.hidden = !running;
    progress.setAttribute('aria-valuemax', String(total));
    progress.setAttribute('aria-valuenow', String(completed));
    const bar = progress.querySelector('.refresh-progress-bar');
    if (bar) bar.style.width = `${Math.min(100, (completed / total) * 100)}%`;
  }

  const next = Date.parse(status.next_scheduled_at || '');
  setText('cloud-next-refresh', Number.isFinite(next)
    ? `Automatic IRSA refresh every 12 h · next ${formatTimestamp(status.next_scheduled_at)}`
    : 'Automatic IRSA refresh every 12 h.');
}

function showCloudRefreshState(status) {
  latestCloudRefreshStatus = status;
  const panel = document.querySelector('.cloud-refresh-panel');
  const form = $('cloud-refresh-form');
  const button = $('btn-cloud-refresh');
  const available = !!status.manual_refresh_available;
  const running = status.status === 'running';
  const connected = status.status !== 'unavailable'
    || available || !!status.snapshot_published_at;
  if (panel) panel.hidden = !connected;
  if (form) form.hidden = !available;
  if (button) button.disabled = !available || running || cloudRefreshSubmitting;
  const statusElement = $('cloud-refresh-status');
  if (statusElement) {
    statusElement.dataset.state = status.awaiting_requested_run
      ? 'waiting'
      : (running ? 'running'
        : (status.status === 'succeeded' || status.status === 'failed'
          ? status.status : 'ready'));
  }

  const publishedMs = Date.parse(status.snapshot_published_at || '');
  setText('cloud-snapshot-time', Number.isFinite(publishedMs)
    ? `Published ${formatTimestamp(status.snapshot_published_at)} · ${formatAge(Date.now() - publishedMs)}`
    : 'No Cloud snapshot has been published yet.');
  renderCloudFreshness(status, running);
  renderCloudFieldBoard(status, running);

  const completed = Number(status.completed_targets) || 0;
  const total = Number(status.total_targets) || 18;
  const updated = Number(status.updated_count) || 0;
  const unchanged = Number(status.unchanged_count) || 0;
  const failed = Number(status.failed_count) || 0;
  if (status.status === 'offline') {
    setText('cloud-refresh-status',
      'Offline \u00b7 showing the snapshot saved on this device. ' +
      'Refresh status and IRSA fetches resume automatically when the connection returns.');
  } else if (running) {
    const field = status.current_target
      ? ` · checking ${fieldLabel(status.current_target)} (${status.current_target_index}/${total})`
      : '';
    setText('cloud-refresh-status',
      `Refresh running: ${completed}/${total} complete${field} · ` +
      `${updated} updated · ${unchanged} unchanged · ${failed} failed.`);
  } else if (status.awaiting_requested_run) {
    setText('cloud-refresh-status',
      'Refresh accepted · waiting for the Cloud Run worker to report progress…');
  } else if (status.status === 'succeeded') {
    setText('cloud-refresh-status',
      `Latest refresh published ${formatTimestamp(
        status.published_at || status.completed_at)} · ` +
      `${updated} updated · ${unchanged} unchanged` +
      (failed ? ` · ${failed} failed (previous data kept).` : '.'));
  } else if (status.status === 'failed') {
    const field = status.failed_target ? ` at ${status.failed_target}` : '';
    const reason = status.error_code ? ` (${status.error_code})` : '';
    setText('cloud-refresh-status',
      `Latest refresh failed${field}${reason}; the published snapshot remains available.`);
  } else if (!available) {
    setText('cloud-refresh-status',
      status.status === 'unavailable'
        ? 'Cloud refresh status is unavailable on this API.'
        : 'Operator refresh is not configured on this API.');
  } else {
    setText('cloud-refresh-status',
      'Ready · refresh all 18 registered fields and publish only after validation.');
  }
}

async function updateCloudRefreshStatus() {
  cloudRefreshPollTimer = null;
  const url = cloudRefreshUrl('refresh-status');
  if (!url) {
    showCloudRefreshState({ status: 'unavailable' });
    return;
  }
  try {
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const status = await response.json();
    rememberRefreshStatus(status);
    if (offline.mode === 'offline') syncOfflineCopy();   // the connection is back
    const isNewAttempt = cloudRefreshRequestedAt
      && status.started_at
      && Date.parse(status.started_at) >= Date.parse(cloudRefreshRequestedAt);
    showCloudRefreshState({
      ...status,
      awaiting_requested_run: !!cloudRefreshRequestedAt && !isNewAttempt,
    });
    // A newer snapshot than the one this page loaded -- from the operator's
    // request or from the 12-hour schedule -- reloads the published views.
    const published = status.snapshot_published_at || null;
    const newSnapshot = !!published && !!cloudSnapshotSeenAt
      && published !== cloudSnapshotSeenAt;
    if (published) cloudSnapshotSeenAt = published;
    if ((isNewAttempt && status.status === 'succeeded') || newSnapshot) {
      cloudRefreshRequestedAt = null;
      cloudRefreshPollStartedAt = null;
      if (offline.supported) {
        // Views read the saved copy: update it first (only changed files are
        // downloaded); it then refreshes the views that changed.
        await syncOfflineCopy();
      } else {
        await loadMosaic(true);
        draw();
        if (!state.live) await refreshPublishedView();
      }
      scheduleCloudRefreshPoll(CLOUD_REFRESH_IDLE_POLL_MS);
      return;
    }
    if (isNewAttempt && status.status === 'failed') {
      cloudRefreshRequestedAt = null;
      cloudRefreshPollStartedAt = null;
    }
    if (status.status === 'running' || cloudRefreshRequestedAt) {
      // B8: stop polling if we have been waiting too long without a new run.
      if (cloudRefreshRequestedAt && cloudRefreshPollStartedAt) {
        const waited = Date.now() - Date.parse(cloudRefreshPollStartedAt);
        if (waited > CLOUD_REFRESH_WAIT_TIMEOUT_MS) {
          cloudRefreshRequestedAt = null;
          cloudRefreshPollStartedAt = null;
          setText('cloud-refresh-status',
            'The refresh job did not start within 5 minutes. ' +
            'The lock may still be held by a previous run. Try again later.');
          const statusElement = $('cloud-refresh-status');
          if (statusElement) statusElement.dataset.state = 'failed';
          scheduleCloudRefreshPoll(CLOUD_REFRESH_IDLE_POLL_MS);
          return;
        }
      }
      scheduleCloudRefreshPoll();
    } else if (status.status !== 'unavailable') {
      scheduleCloudRefreshPoll(CLOUD_REFRESH_IDLE_POLL_MS);
    }
  } catch (error) {
    if (offline.supported && offline.mode === 'offline') {
      showCloudRefreshState(offlineStatusForPanel());
      scheduleCloudRefreshPoll(CLOUD_REFRESH_IDLE_POLL_MS);
      return;
    }
    // A failure while online may mean the connection just dropped: check.
    if (offline.supported && offline.mode === 'online') syncOfflineCopy();
    setText('cloud-refresh-status',
      `Refresh status unavailable: ${error.message}`);
    const statusElement = $('cloud-refresh-status');
    if (statusElement) statusElement.dataset.state = 'failed';
    scheduleCloudRefreshPoll(cloudRefreshRequestedAt
      ? CLOUD_REFRESH_ACTIVE_POLL_MS : CLOUD_REFRESH_IDLE_POLL_MS);
  }
}

async function requestCloudRefresh(event) {
  event.preventDefault();
  const input = $('cloud-refresh-token');
  const button = $('btn-cloud-refresh');
  const token = input.value;
  if (!token) {
    setText('cloud-refresh-status', 'Enter the operator token to continue.');
    input.focus();
    return;
  }
  const url = cloudRefreshUrl('refresh');
  if (!url) {
    setText('cloud-refresh-status',
      'Refresh requires a connected Cloud API.');
    return;
  }

  cloudRefreshSubmitting = true;
  if (button) button.disabled = true;
  setText('cloud-refresh-status', 'Sending the authorized refresh request…');
  try {
    const response = await fetch(url, {
      method: 'POST',
      cache: 'no-store',
      credentials: 'omit',
      redirect: 'error',
      headers: { 'X-Refresh-Token': token },
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(result.detail || result.error || `HTTP ${response.status}`);
    }
    cloudRefreshRequestedAt = result.accepted_at || new Date().toISOString();
    cloudRefreshPollStartedAt = new Date().toISOString();  // B8: start the timeout clock
    await updateCloudRefreshStatus();
  } catch (error) {
    setText('cloud-refresh-status', `Refresh request failed: ${error.message}`);
    const statusElement = $('cloud-refresh-status');
    if (statusElement) statusElement.dataset.state = 'failed';
  } finally {
    input.value = '';
    cloudRefreshSubmitting = false;
    if (button) {
      button.disabled = !latestCloudRefreshStatus.manual_refresh_available
        || latestCloudRefreshStatus.status === 'running';
    }
  }
}

// Two different situations used to be both called "offline": the visitor's
// own internet being down, and the SPHEREx-Lens server not answering (as in
// the 2026-11-14 outage).  The browser knows which one it is.
function deviceOffline() {
  return typeof navigator !== 'undefined' && navigator.onLine === false;
}

function offlineWhy() {
  return deviceOffline() ? 'This device is offline' : 'The server is not answering';
}

// ------------------------------------------------------------ offline copy
//
// The browser keeps a complete copy of the latest Cloud snapshot so the site
// still works without internet (sw.js serves it). Kept in sync like the cloud
// publishes: /api/snapshot-index names every immutable object; only objects
// this device has not saved are downloaded, each one is checked against the
// X-Snapshot-Object header, and the new copy is switched on in one step only
// after every file has arrived. The previous copy stays in use until then.

const OFFLINE_DATA_CACHE = 'spx-data-v1';            // same names as sw.js
const OFFLINE_META_URL = '/__spx/meta';
const OFFLINE_LAST_STATUS_URL = '/__spx/last-refresh-status';
// Historical dashboard frames are verified immutable objects kept by sw.js;
// a latest-snapshot sync must not evict them.
const OFFLINE_HISTORY_PREFIX = '/__spx/obj/history-dashboard/';
const OFFLINE_INDEX_TIMEOUT_MS = 30000;   // long enough for a Cloud Run cold start
const OFFLINE_SLOW_MS = 4000;             // after this, say "still connecting"
const OFFLINE_RETRY_MS = 30000;           // reconnect probe while offline
const OFFLINE_PARALLEL = 3;
// The snapshot API allows 30 dashboard and 30 recovery reads per minute per
// visitor; background saving stays well under that so clicks never hit it.
const OFFLINE_PACED_MS = { dashboard: 3500, recovery: 3500 };

const offline = {
  supported: false,
  mode: 'checking',        // checking | online | offline
  meta: null,              // the switched-on saved copy
  lastStatus: null,        // last refresh-status seen online
  syncing: null,
  saved: 0,
  total: 0,
  note: '',
  retryTimer: null,
  slowTimer: null,
};

function offlineBase() {
  const base = STATIC_MODE ? LIVE_API : `${API_BASE}/api`;
  if (!base) return null;
  try {
    // Only same-origin APIs: the service worker and header checks need it.
    return new URL(base, window.location.href).origin === window.location.origin
      ? base : null;
  } catch {
    return null;
  }
}

function offlineObjectKey(kind, object) {
  return `/__spx/obj/${kind}/${encodeURIComponent(object)}`;
}

function offlineStatusKey(field, manifest) {
  return `/__spx/obj/status/${encodeURIComponent(field)}/${encodeURIComponent(manifest)}`;
}

async function offlineCache() {
  return caches.open(OFFLINE_DATA_CACHE);
}

async function offlineReadJson(url) {
  try {
    const response = await (await offlineCache()).match(url);
    return response ? await response.json() : null;
  } catch {
    return null;
  }
}

async function offlineWriteJson(url, value) {
  await (await offlineCache()).put(url, new Response(JSON.stringify(value), {
    headers: { 'Content-Type': 'application/json' },
  }));
}

function offlineSleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function offlineSavedText() {
  const meta = offline.meta;
  if (!meta) return '';
  return `${meta.fields} fields, saved ${formatTimestamp(meta.saved_at)}`;
}

function renderOfflineCopy() {
  const line = $('offline-copy-status');
  if (!line) return;
  line.hidden = !offline.supported;
  if (!offline.supported) return;
  let text;
  if (offline.mode === 'offline') {
    text = offline.meta
      ? `${offlineWhy()} · showing the copy saved on this device (${offlineSavedText()}). ` +
        'Retrying automatically.'
      : `${offlineWhy()}, and no copy is saved on this device yet. ` +
        'Open the site once while it works to save one.';
  } else if (offline.syncing && offline.total) {
    text = `Saving offline copy on this device: ${offline.saved}/${offline.total} files…`;
  } else if (offline.note) {
    text = offline.note;
  } else if (offline.mode === 'checking') {
    text = offline.meta
      ? `Showing the copy saved on this device (${offlineSavedText()}) · checking for a newer snapshot…`
      : 'Checking the Cloud snapshot…';
  } else {
    text = offline.meta
      ? `Offline copy on this device: up to date (${offlineSavedText()}).`
      : 'Offline copy not saved yet.';
  }
  line.textContent = text;
  line.dataset.state = offline.mode;
}

function offlineStatusForPanel() {
  const last = offline.lastStatus || {};
  return {
    ...last,
    status: 'offline',
    manual_refresh_available: false,
    snapshot_published_at: offline.meta?.published_at || last.snapshot_published_at || null,
  };
}

function scheduleOfflineRetry() {
  if (offline.retryTimer) window.clearTimeout(offline.retryTimer);
  offline.retryTimer = window.setTimeout(() => {
    offline.retryTimer = null;
    syncOfflineCopy();
  }, OFFLINE_RETRY_MS);
}

function setOfflineMode(mode) {
  const was = offline.mode;
  offline.mode = mode;
  document.body.classList.toggle('is-offline', mode === 'offline');
  if (mode === 'offline') {
    stopSnapshotPlayback();
    if (state.live) {
      // Fresh IRSA fetches cannot work offline: fall back to the saved copy.
      state.live = false;
      const toggle = $('tog-live');
      if (toggle) toggle.checked = false;
      stopLiveSky();
      if (state.viewMode === 'sky') loadMosaic(false).then(draw);
      else if (!state.pipelineRunning) runPipeline();
    }
    showCloudRefreshState(offlineStatusForPanel());
    scheduleOfflineRetry();
  }
  syncBackendUi();
  renderOfflineCopy();
  if (was === 'offline' && mode === 'online') {
    // Back online: refresh the panel now instead of at the next idle poll.
    scheduleCloudRefreshPoll(0);
    if (!state.live) loadSnapshotHistory($('target-select').value,
      { keepSelection: true });
  }
}

async function offlineFetchIndex() {
  const controller = new AbortController();
  const abort = window.setTimeout(() => controller.abort(), OFFLINE_INDEX_TIMEOUT_MS);
  offline.slowTimer = window.setTimeout(() => {
    if (offline.mode !== 'online') {
      offline.note = 'Still connecting to the Cloud snapshot (it may be waking up)…';
      renderOfflineCopy();
    }
  }, OFFLINE_SLOW_MS);
  try {
    return await fetch(`${offlineBase()}/snapshot-index`, {
      cache: 'no-store', signal: controller.signal,
    });
  } finally {
    window.clearTimeout(abort);
    window.clearTimeout(offline.slowTimer);
  }
}

function offlinePlan(index) {
  const base = offlineBase();
  const current = $('target-select')?.value;
  const items = [];
  const query = (target) => (target ? `?target=${encodeURIComponent(target)}` : '');
  const add = (kind, target, object) => {
    if (!object) return;               // the cloud has no such object either
    items.push({
      slot: target ? `${kind}:${target}` : kind, kind, target, object,
      key: offlineObjectKey(kind, object),
      url: `${base}/${kind}${query(target)}`,
    });
  };
  add('targets', null, index.targets_object);
  add('mosaic', null, index.mosaic);
  Object.entries(index.fields || {}).forEach(([field, objects]) => {
    items.push({
      slot: `status:${field}`, kind: 'status', target: field, manifest: index.manifest,
      key: offlineStatusKey(field, index.manifest),
      url: `${base}/status${query(field)}`,
    });
    add('dashboard', field, objects.dashboard);
    add('recovery', field, objects.recovery);
  });
  // The field on screen first, then the small shared files, then the rest.
  const rank = (item) => (item.target === current ? 0 : (item.target ? 2 : 1));
  return items.map((item, i) => [item, i])
    .sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1])
    .map(([item]) => item);
}

class OfflineSnapshotChanged extends Error {}
class OfflineUnsupported extends Error {}

async function offlineDownload(cache, item, pacer) {
  for (let attempt = 0; ; attempt += 1) {
    await pacer(item.kind);
    let response;
    try {
      response = await fetch(item.url, { cache: 'no-store', headers: { 'X-SPX-Sync': '1' } });
    } catch (error) {
      if (attempt >= 2) throw error;               // connection lost
      await offlineSleep(1500 * (attempt + 1));
      continue;
    }
    if (response.status === 429 && attempt < 6) {  // shared lab address: wait it out
      const wait = Number(response.headers.get('Retry-After')) || 20;
      await offlineSleep(Math.min(60, wait) * 1000);
      continue;
    }
    if (response.status >= 500 && attempt < 2) {
      await offlineSleep(1500 * (attempt + 1));
      continue;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status} for ${item.slot}`);
    const object = response.headers.get('X-Snapshot-Object');
    const manifest = response.headers.get('X-Snapshot-Manifest');
    if (item.object ? !object : !manifest) {
      throw new OfflineUnsupported('this API does not label snapshot objects');
    }
    if (item.object ? object !== item.object : manifest !== item.manifest) {
      throw new OfflineSnapshotChanged('a new snapshot was published during the download');
    }
    const text = await response.text();
    JSON.parse(text);                              // complete JSON only
    await cache.put(item.key, new Response(text, {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'X-Snapshot-Object': object || '',
        'X-Snapshot-Manifest': manifest || '',
        'X-SPX-Saved-At': new Date().toISOString(),
      },
    }));
    return;
  }
}

function offlinePacer() {
  const nextStart = {};
  return async (kind) => {
    const spacing = OFFLINE_PACED_MS[kind];
    if (!spacing) return;
    const now = Date.now();
    const start = Math.max(now, nextStart[kind] || 0);
    nextStart[kind] = start + spacing;
    if (start > now) await offlineSleep(start - now);
  };
}

async function offlineSaveAll(index) {
  const cache = await offlineCache();
  const plan = offlinePlan(index);
  const missing = [];
  for (const item of plan) {
    if (!(await cache.match(item.key))) missing.push(item);
  }
  offline.total = plan.length;
  offline.saved = plan.length - missing.length;
  renderOfflineCopy();
  const pacer = offlinePacer();
  let next = 0;
  let failure = null;
  const worker = async () => {
    while (!failure && next < missing.length) {
      const item = missing[next];
      next += 1;
      try {
        await offlineDownload(cache, item, pacer);
        offline.saved += 1;
        renderOfflineCopy();
      } catch (error) {
        failure = failure || error;
      }
    }
  };
  await Promise.all(Array.from({ length: OFFLINE_PARALLEL }, worker));
  if (failure) throw failure;

  // Switch on only a complete copy.
  for (const item of plan) {
    if (!(await cache.match(item.key))) throw new Error(`${item.slot} is missing`);
  }
  const meta = {
    manifest: index.manifest,
    published_at: index.published_at,
    saved_at: new Date().toISOString(),
    fields: Object.keys(index.fields || {}).length,
    keys: Object.fromEntries(plan.map((item) => [item.slot, item.key])),
  };
  await offlineWriteJson(OFFLINE_META_URL, meta);

  // Only now remove objects the new copy no longer uses.
  const keep = new Set(plan.map((item) => new URL(item.key, window.location.origin).href));
  const stored = await cache.keys();
  await Promise.all(stored
    .filter((request) => new URL(request.url).pathname.startsWith('/__spx/obj/')
      && !new URL(request.url).pathname.startsWith(OFFLINE_HISTORY_PREFIX)
      && !keep.has(request.url))
    .map((request) => cache.delete(request)));
  return meta;
}

async function offlineSyncOnce() {
  const previous = offline.meta;
  for (let round = 0; round < 3; round += 1) {
    let response;
    try {
      response = await offlineFetchIndex();
    } catch (error) {
      offline.note = '';
      setOfflineMode('offline');
      return { changed: false };
    }
    if (response.status === 404) {
      // This API predates the offline copy: never serve a copy it cannot update.
      await (await offlineCache()).delete(OFFLINE_META_URL);
      offline.meta = null;
      offline.supported = false;
      setOfflineMode('online');
      return { changed: false };
    }
    if (!response.ok) {
      offline.note = `Cloud snapshot unavailable right now (HTTP ${response.status})` +
        (offline.meta ? '; the copy saved on this device is shown.' : '.');
      setOfflineMode('online');
      scheduleOfflineRetry();
      return { changed: false };
    }
    const index = await response.json();
    offline.note = '';
    setOfflineMode('online');
    if (offline.meta && offline.meta.manifest === index.manifest) {
      renderOfflineCopy();
      return { changed: false };
    }
    try {
      const meta = await offlineSaveAll(index);
      offline.meta = meta;
      offline.total = 0;
      if (navigator.storage && navigator.storage.persist) {
        navigator.storage.persist().catch(() => {});
      }
      const changedSlots = new Set(Object.keys(meta.keys)
        .filter((slot) => !previous || previous.keys[slot] !== meta.keys[slot]));
      offline.note = previous
        ? `Offline copy updated to the snapshot published ${formatTimestamp(meta.published_at)}.`
        : '';
      renderOfflineCopy();
      return { changed: !!previous, changedSlots };
    } catch (error) {
      offline.total = 0;
      if (error instanceof OfflineSnapshotChanged) continue;   // start again
      if (error instanceof OfflineUnsupported) {
        offline.supported = false;
        renderOfflineCopy();
        return { changed: false };
      }
      const quota = error && error.name === 'QuotaExceededError';
      offline.note = quota
        ? 'Not enough browser storage for an offline copy (about 45 MB is needed).'
        : `Saving the offline copy stopped (${error.message}); ` +
          (previous ? 'the previous copy is kept. ' : '') +
          'Files already saved are reused next time.';
      renderOfflineCopy();
      if (error instanceof TypeError) setOfflineMode('offline');   // network gone
      else scheduleOfflineRetry();
      return { changed: false };
    }
  }
  offline.note = 'The Cloud snapshot kept changing during the download; retrying shortly.';
  renderOfflineCopy();
  scheduleOfflineRetry();
  return { changed: false };
}

async function waitForPipeline() {
  while (state.pipelineRunning) await offlineSleep(200);
}

// Show a newly saved snapshot without resetting views that did not change.
function snapshotViewIsHistorical() {
  return !!state.snapshotHistory.selected;
}

async function refreshPublishedView() {
  const target = $('target-select').value;
  if (snapshotViewIsHistorical() && !state.live) {
    return loadSnapshotHistory(target, { keepSelection: true });
  }
  return runPipeline();
}

async function applyOfflineUpdate(result) {
  if (!result || !result.changed || state.live) return;
  const slots = result.changedSlots || new Set();
  const current = $('target-select')?.value;
  if (slots.has('targets')) await loadTargets();
  if (slots.has('mosaic') && state.mosaic) {
    await loadMosaic(true);
    draw();
  }
  if (slots.has(`dashboard:${current}`) || slots.has(`status:${current}`)) {
    await waitForPipeline();
    await refreshPublishedView();
  }
}

function syncOfflineCopy() {
  if (!offline.supported) return Promise.resolve({ changed: false });
  if (offline.syncing) return offline.syncing;
  const run = () => offlineSyncOnce().then(async (result) => {
    await applyOfflineUpdate(result);
    return result;
  });
  offline.syncing = (navigator.locks
    ? navigator.locks.request('spx-offline-sync', run)   // one tab at a time
    : run())
    .catch((error) => {
      console.warn('offline copy sync failed', error);
      return { changed: false };
    })
    .finally(() => {
      offline.syncing = null;
      renderOfflineCopy();
    });
  return offline.syncing;
}

async function startOfflineCopy() {
  offline.supported = !!(window.isSecureContext && 'caches' in window
    && 'serviceWorker' in navigator && offlineBase());
  if (!offline.supported) return;
  navigator.serviceWorker.register('sw.js').catch((error) => {
    console.warn('offline copy unavailable: service worker not registered', error);
  });
  [offline.meta, offline.lastStatus] = await Promise.all([
    offlineReadJson(OFFLINE_META_URL), offlineReadJson(OFFLINE_LAST_STATUS_URL)]);
  window.addEventListener('online', () => syncOfflineCopy());
  window.addEventListener('offline', () => setOfflineMode('offline'));
  if (navigator.onLine === false) setOfflineMode('offline');
  renderOfflineCopy();
}

function rememberRefreshStatus(status) {
  if (!offline.supported) return;
  offline.lastStatus = status;
  offlineWriteJson(OFFLINE_LAST_STATUS_URL, status).catch(() => {});
}

// --------------------------------------------------------- research history
//
// Every result a researcher fetches -- custom fields, fresh IRSA runs of the
// registered fields, live recovery grids -- is saved on this device exactly as
// received (IndexedDB), so it can be found again by name or coordinates and
// reopened without internet. Entries (small) and payloads (MBs) are stored
// separately so listing never loads the payloads.

const HISTORY_DB = 'spx-history';
const HISTORY_LIMIT_BYTES = 200 * 1024 * 1024;
const HISTORY_MATCH_ARCSEC = 1;
const HISTORY_FORMAT = 'spherexlens-history';
const HISTORY_KINDS = { field: 'custom field', fresh: 'fresh IRSA', recovery: 'recovery' };

const researchHistory = {
  db: null,
  entries: [],
  filter: '',
  note: '',
};

function historyOpen() {
  if (researchHistory.db) return Promise.resolve(researchHistory.db);
  if (!('indexedDB' in window)) return Promise.reject(new Error('IndexedDB unavailable'));
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(HISTORY_DB, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      const entries = db.createObjectStore('entries', { keyPath: 'id' });
      entries.createIndex('key', 'key', { unique: false });
      db.createObjectStore('payloads', { keyPath: 'id' });
    };
    request.onsuccess = () => { researchHistory.db = request.result; resolve(researchHistory.db); };
    request.onerror = () => reject(request.error);
  });
}

function historyTx(stores, mode, work) {
  return historyOpen().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(stores, mode);
    let result;
    Promise.resolve(work(tx)).then((value) => { result = value; }, reject);
    tx.oncomplete = () => resolve(result);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error('history transaction aborted'));
  }));
}

function idbRequest(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function historyKey(kind, target, ra, dec, params) {
  if (kind === 'field') {
    return ['field', Number(ra).toFixed(5), Number(dec).toFixed(5),
      params.slots ?? '', params.size ?? '', params.epochs ?? ''].join('|');
  }
  return `${kind}|${target}`;
}

function historySeparationArcsec(ra1, dec1, ra2, dec2) {
  const rad = Math.PI / 180;
  const dRa = ((ra1 - ra2 + 540) % 360 - 180) * Math.cos(((dec1 + dec2) / 2) * rad);
  return Math.hypot(dRa, dec1 - dec2) * 3600;
}

async function historyRefresh() {
  try {
    researchHistory.entries = await historyTx(['entries'], 'readonly',
      (tx) => idbRequest(tx.objectStore('entries').getAll()));
    researchHistory.entries.sort((a, b) => String(b.saved_at).localeCompare(String(a.saved_at)));
  } catch (error) {
    researchHistory.entries = [];
    researchHistory.note = `History unavailable in this browser (${error.message}).`;
  }
  researchHistory.loaded = true;
  renderResearchHistory();
}

// Save one result. Replaces the previous unpinned result with the same key;
// pinned results are never replaced or removed automatically.
async function historySave({ kind, target, name, ra, dec, params = {}, data,
  irsaCheck = null }) {
  if (!data || typeof data !== 'object') return;
  try {
    const id = `${kind}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const key = historyKey(kind, target, ra, dec, params);
    const stats = data.stats || {};
    const entry = {
      id, kind, key, target, name: name || data.common || target,
      ra: Number.isFinite(ra) ? ra : null,
      dec: Number.isFinite(dec) ? dec : null,
      params, saved_at: new Date().toISOString(), pinned: false,
      bytes: JSON.stringify(data).length,
      fingerprint: irsaCheck?.source_fingerprint || null,
      irsa_check: irsaCheck,
      summary: kind === 'recovery'
        ? { cells: (data.grid || []).length }
        : { detections: (data.detections || []).length,
          max_snr: stats.max_snr ?? null, exposures: stats.n_exposures ?? null },
    };
    await historyTx(['entries', 'payloads'], 'readwrite', async (tx) => {
      const entries = tx.objectStore('entries');
      const payloads = tx.objectStore('payloads');
      const same = await idbRequest(entries.index('key').getAll(key));
      same.filter((old) => !old.pinned).forEach((old) => {
        entries.delete(old.id);
        payloads.delete(old.id);
      });
      entries.put(entry);
      payloads.put({ id, data });
    });
    const removed = await historyEnforceLimit(id);
    researchHistory.note = removed
      ? `History is full (${fmt.bytes(HISTORY_LIMIT_BYTES)}): removed ${removed} oldest unpinned result(s).`
      : '';
    if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
    await historyRefresh();
  } catch (error) {
    researchHistory.note = error && error.name === 'QuotaExceededError'
      ? 'Not enough browser storage to save this result in History.'
      : `Could not save to History: ${error.message}`;
    renderResearchHistory();
  }
}

async function historyEnforceLimit(keepId) {
  const entries = await historyTx(['entries'], 'readonly',
    (tx) => idbRequest(tx.objectStore('entries').getAll()));
  let total = entries.reduce((sum, e) => sum + (e.bytes || 0), 0);
  const victims = [];
  entries.filter((e) => !e.pinned && e.id !== keepId)
    .sort((a, b) => String(a.saved_at).localeCompare(String(b.saved_at)))
    .forEach((e) => {
      if (total > HISTORY_LIMIT_BYTES) {
        victims.push(e.id);
        total -= e.bytes || 0;
      }
    });
  if (victims.length) {
    await historyTx(['entries', 'payloads'], 'readwrite', (tx) => {
      victims.forEach((id) => {
        tx.objectStore('entries').delete(id);
        tx.objectStore('payloads').delete(id);
      });
    });
  }
  return victims.length;
}

async function historyPayload(id) {
  const row = await historyTx(['payloads'], 'readonly',
    (tx) => idbRequest(tx.objectStore('payloads').get(id)));
  return row ? row.data : null;
}

async function historyUpdate(id, changes) {
  await historyTx(['entries'], 'readwrite', async (tx) => {
    const store = tx.objectStore('entries');
    const entry = await idbRequest(store.get(id));
    if (entry) store.put({ ...entry, ...changes });
  });
  await historyRefresh();
}

async function historyDelete(id) {
  await historyTx(['entries', 'payloads'], 'readwrite', (tx) => {
    tx.objectStore('entries').delete(id);
    tx.objectStore('payloads').delete(id);
  });
  await historyRefresh();
}

function historyFindField(ra, dec, params) {
  return researchHistory.entries.find((e) => e.kind === 'field'
    && Number.isFinite(e.ra) && Number.isFinite(e.dec)
    && String(e.params?.slots ?? '') === String(params.slots ?? '')
    && historySeparationArcsec(e.ra, e.dec, ra, dec) <= HISTORY_MATCH_ARCSEC) || null;
}

function historyMatches(entry, filter) {
  if (!filter) return true;
  const coords = filter.match(/^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (coords && Number.isFinite(entry.ra)) {
    // "RA, Dec": anything within 0.1 degree (the cutout radius).
    return historySeparationArcsec(entry.ra, entry.dec,
      Number(coords[1]), Number(coords[2])) <= 360;
  }
  const text = `${entry.name} ${entry.target} ${HISTORY_KINDS[entry.kind]}`.toLowerCase();
  return text.includes(filter.toLowerCase());
}

function renderResearchHistory() {
  const list = $('history-list');
  if (!list) return;
  const badge = $('history-badge');
  if (badge) {
    badge.textContent = researchHistory.entries.length
      ? String(researchHistory.entries.length)
      : '';
  }
  const total = researchHistory.entries.reduce((sum, e) => sum + (e.bytes || 0), 0);
  setText('history-usage', researchHistory.entries.length
    ? `${researchHistory.entries.length} saved · ${fmt.bytes(total)} of ${fmt.bytes(HISTORY_LIMIT_BYTES)}`
    : '');
  const shown = researchHistory.entries.filter((e) => historyMatches(e, researchHistory.filter));
  list.replaceChildren(...shown.map((entry) => {
    const item = document.createElement('li');
    item.className = 'history-item';
    item.dataset.id = entry.id;
    const coords = Number.isFinite(entry.ra)
      ? `${entry.ra.toFixed(4)}, ${entry.dec.toFixed(4)}` : '';
    const summary = entry.kind === 'recovery'
      ? `${entry.summary?.cells ?? '?'} cells`
      : `${entry.summary?.detections ?? '?'} detections`;
    const title = document.createElement('div');
    title.className = 'history-title';
    title.textContent = `${entry.pinned ? '★ ' : ''}${entry.name}`;
    const meta = document.createElement('div');
    meta.className = 'history-meta';
    meta.textContent = [HISTORY_KINDS[entry.kind], coords, summary,
      formatTimestamp(entry.saved_at)].filter(Boolean).join(' · ');
    const actions = document.createElement('div');
    actions.className = 'history-actions-row';
    const button = (action, text, label) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'history-btn';
      b.dataset.action = action;
      b.textContent = text;
      b.setAttribute('aria-label', `${label}: ${entry.name}`);
      return b;
    };
    actions.append(
      button('open', 'Open', 'Open saved result'),
      button('pin', entry.pinned ? 'Unpin' : 'Pin', entry.pinned ? 'Unpin' : 'Pin'),
      button('delete', 'Delete', 'Delete saved result'));
    item.append(title, meta, actions);
    return item;
  }));
  const empty = $('history-empty');
  if (empty) {
    empty.hidden = shown.length > 0;
    empty.textContent = researchHistory.entries.length
      ? 'No saved result matches this search.'
      : 'Results you fetch from IRSA are saved here automatically, and open without internet.';
  }
  setText('history-note', researchHistory.note);
}

function renderRecoveryHistoryLabel(entry) {
  return `Saved recovery result from ${formatTimestamp(entry.saved_at)}`;
}

async function openHistoryEntry(entry) {
  const data = await historyPayload(entry.id);
  if (!data) {
    researchHistory.note = 'This saved result is missing its data; it was removed.';
    await historyDelete(entry.id);
    return false;
  }
  const sel = $('target-select');
  if (entry.kind === 'field' && ![...sel.options].some((o) => o.value === entry.target)) {
    const option = document.createElement('option');
    option.value = entry.target;
    option.textContent = `${entry.name} (${entry.ra.toFixed(2)}, ${entry.dec.toFixed(2)})`;
    sel.appendChild(option);
  }
  if ([...sel.options].some((o) => o.value === entry.target)) sel.value = entry.target;
  if (entry.kind === 'field') {
    state.fieldCoordinates = { ra: entry.ra, dec: entry.dec, name: entry.name };
    state.customFields[entry.target] = state.fieldCoordinates;
  }
  if (entry.kind === 'recovery') {
    const message = renderRecoveryResult(data, renderRecoveryHistoryLabel(entry));
    setText('action-message', message);
    return true;
  }
  if (state.viewMode === 'sky') switchView('field');
  await waitForPipeline();
  await runPipeline(data, null, entry);
  return true;
}

function historyExport() {
  return Promise.all(researchHistory.entries.map(async (entry) => ({
    ...entry, data: await historyPayload(entry.id),
  }))).then((entries) => {
    const file = {
      format: HISTORY_FORMAT, version: 1,
      exported_at: new Date().toISOString(),
      entries: entries.filter((e) => e.data),
    };
    const blob = new Blob([JSON.stringify(file)], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `spherexlens-history-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    researchHistory.note = `Exported ${file.entries.length} saved result(s).`;
    renderResearchHistory();
  });
}

function historyValidEntry(e) {
  if (!e || typeof e !== 'object' || !HISTORY_KINDS[e.kind]) return false;
  if (typeof e.id !== 'string' || typeof e.target !== 'string') return false;
  if (!e.data || typeof e.data !== 'object') return false;
  if (e.kind === 'recovery') return Array.isArray(e.data.grid);
  return Array.isArray(e.data.detections) && !!e.data.map
    && Number.isFinite(e.ra ?? e.data.ra ?? 0);
}

async function historyImport(file) {
  let parsed;
  try {
    parsed = JSON.parse(await file.text());
  } catch {
    researchHistory.note = 'Import failed: the file is not valid JSON.';
    renderResearchHistory();
    return;
  }
  if (!parsed || parsed.format !== HISTORY_FORMAT || !Array.isArray(parsed.entries)) {
    researchHistory.note = 'Import failed: this is not a SPHERExLens history file.';
    renderResearchHistory();
    return;
  }
  const valid = parsed.entries.filter(historyValidEntry);
  const known = new Set(researchHistory.entries.map((e) => e.id));
  const fresh = valid.filter((e) => !known.has(e.id));
  try {
    await historyTx(['entries', 'payloads'], 'readwrite', (tx) => {
      fresh.forEach(({ data, ...entry }) => {
        tx.objectStore('entries').put({
          ...entry,
          key: historyKey(entry.kind, entry.target, entry.ra, entry.dec, entry.params || {}),
          bytes: JSON.stringify(data).length,
        });
        tx.objectStore('payloads').put({ id: entry.id, data });
      });
    });
    const removed = await historyEnforceLimit(null);
    researchHistory.note = `Imported ${fresh.length} result(s)` +
      (valid.length - fresh.length ? `; ${valid.length - fresh.length} already saved` : '') +
      (parsed.entries.length - valid.length
        ? `; ${parsed.entries.length - valid.length} skipped (incomplete)` : '') +
      (removed ? `; removed ${removed} oldest unpinned to stay within the limit` : '') + '.';
  } catch (error) {
    researchHistory.note = `Import failed: ${error.message}`;
  }
  await historyRefresh();
}

function attachHistoryEvents() {
  const list = $('history-list');
  if (list) {
    list.addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-action]');
      if (!button) return;
      const id = button.closest('.history-item')?.dataset.id;
      const entry = researchHistory.entries.find((e) => e.id === id);
      if (!entry) return;
      if (button.dataset.action === 'open') await openHistoryEntry(entry);
      if (button.dataset.action === 'pin') await historyUpdate(id, { pinned: !entry.pinned });
      if (button.dataset.action === 'delete') {
        if (entry.pinned && !window.confirm(`Delete the pinned result "${entry.name}"?`)) return;
        await historyDelete(id);
      }
    });
  }
  $('history-search')?.addEventListener('input', (event) => {
    researchHistory.filter = event.target.value.trim();
    renderResearchHistory();
  });
  $('history-export')?.addEventListener('click', () => historyExport());
  $('history-import')?.addEventListener('change', async (event) => {
    const file = event.target.files && event.target.files[0];
    if (file) await historyImport(file);
    event.target.value = '';
  });
}

// ------------------------------------------------- live service wake-up
// The live pipeline service scales to zero. A cheap request when someone
// starts typing coordinates (or reaches for the IRSA toggle) wakes it while
// they type, so the real request does not also wait for a cold start.
let liveWarmedAt = 0;
function warmLivePipeline() {
  if (!LIVE_PIPELINE_API || offline.mode === 'offline') return;
  if (Date.now() - liveWarmedAt < 4 * 60 * 1000) return;
  liveWarmedAt = Date.now();
  fetch(`${LIVE_PIPELINE_API}/targets`, { cache: 'no-store' }).catch(() => {});
}

function catalogSummary(dashboard) {
  const status = dashboard?.catalog_status || {};
  const parts = catalogCountEntries(dashboard).map(([key, count]) => (status[key] === 'unavailable'
    ? `${catalogLabel(key)} unavailable` : `${catalogLabel(key)} ${count ?? 0}`));
  return parts.length ? `catalogues: ${parts.join(' · ')}` : '';
}

function unavailableCatalogs(dashboard) {
  const status = dashboard?.catalog_status || {};
  return catalogCountEntries(dashboard)
    .filter(([key]) => status[key] === 'unavailable')
    .map(([key]) => catalogLabel(key));
}

async function unpackStreamResult(result) {
  if (result.encoding !== 'gzip-base64') return result.data;
  const binary = atob(result.data_packed);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  return JSON.parse(await new Response(stream).text());
}

async function fetchProgressApi(kind, target, onProgress, extraParams = {}) {
  const base = STATIC_MODE
    ? (LIVE_PIPELINE_API || LIVE_API)
    : `${API_BASE}/api`;
  const params = new URLSearchParams(extraParams);
  if (target) params.set('target', target);
  // The final result arrives gzip-packed (about 5x smaller) when this
  // browser can unpack it; it unpacks to exactly the same JSON.
  if ('DecompressionStream' in window) params.set('result', 'gzip');
  const response = await fetch(`${base}/${kind}/stream?${params}`, {
    cache: 'no-store',
    headers: { Accept: 'text/event-stream' },
  });
  const contentType = response.headers.get('content-type') || '';
  if (response.status === 404 || !contentType.includes('text/event-stream')) {
    return { unsupported: true };
  }
  if (!response.ok || !response.body) {
    let detail = `HTTP ${response.status}`;
    try {
      const problem = await response.json();
      detail = [problem.error, problem.detail].filter(Boolean).join(' ') || detail;
    } catch (_) { /* non-JSON response */ }
    throw new Error(detail);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let result = null;
  try {
    while (!result) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() || '';
      for (const frame of frames) {
        const eventName = frame.match(/^event:\s*(.+)$/m)?.[1];
        const dataLine = frame.split(/\r?\n/)
          .find((line) => line.startsWith('data:'));
        if (!dataLine) continue;
        const payload = JSON.parse(dataLine.slice(5).trim());
        if (eventName === 'progress') onProgress(payload);
        if (eventName === 'result') result = payload;
      }
    }
  } finally {
    reader.releaseLock();
  }
  if (!result) throw new Error('The live progress stream ended before returning a result.');
  return {
    unsupported: false,
    ok: result.status >= 200 && result.status < 300,
    status: result.status,
    data: await unpackStreamResult(result),
    elapsedSeconds: result.elapsed_seconds,
  };
}

// ---------------------------------------------------------------- utilities

const $ = (id) => document.getElementById(id);
const show = (id) => $(id).classList.remove('hidden');
const hide = (id) => $(id).classList.add('hidden');
const setText = (id, text) => {
  const el = $(id);
  if (!el) return;
  if (id === 'map-hint') { renderMapHint(text); return; }
  el.textContent = text;
};

// The map info strip: same text as before, laid out as chips. " · " splits
// parts; a part with "; " becomes a stack of "label  date" lines. A lime tag
// in front names the active view (and layer) from the buttons themselves.
const DATE_RE = /\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}/;
function renderMapHint(text) {
  const el = $('map-hint');
  if (!el) return;
  const raw = String(text ?? '');
  el.dataset.text = raw;
  const clean = (b) => (b ? b.textContent.replace(/\s+/g, ' ').trim() : '');
  const viewBtn = document.querySelector('#view-switch .seg.active');
  const isField = !viewBtn || viewBtn.dataset.view === 'field';
  const tagText = isField
    ? [clean(viewBtn), clean(document.querySelector('#layer-switch .seg.active'))].filter(Boolean).join(' · ')
    : clean(viewBtn);
  // P11 s11: chip roles and the current view, as attributes only (text unchanged)
  const layerBtn = document.querySelector('#layer-switch .seg.active');
  const viewKey = !isField ? 'allsky'
    : ({ image: 'coadd', zogy_image: 'difference', zogy_snr: 'significance' }[layerBtn && layerBtn.dataset.layer] || '');
  const viewAttr = viewKey ? ` data-view="${viewKey}"` : '';
  const roleOf = (part, i) => (/^scale\s/.test(part) ? 'scale'
    : /^(click|drag|press|hover|tap)\b/i.test(part) ? 'hint'
      : (i === 0 && isField && part.trim() ? 'target' : 'fact'));
  const tag = tagText ? `<span class="map-info-tag" data-role="view"${viewAttr}>${fmt.escape(tagText)}</span>` : '';
  const parts = raw.split(' · ');
  if (parts.length < 2) {
    const role = /^(click|drag|press|hover|tap)\b/i.test(raw) ? 'hint' : 'fact';
    el.innerHTML = `${tag}<span class="map-chip map-chip-plain" data-role="${role}">${fmt.escape(raw)}</span>`;
    return;
  }
  const chips = parts.map((part, i) => {
    if (part.includes('; ')) {
      const lines = part.split('; ').map((piece) => {
        const m = piece.match(DATE_RE);
        if (!m || m.index === 0) return `<span class="mi-line">${fmt.escape(piece)}</span>`;
        return `<span class="mi-k">${fmt.escape(piece.slice(0, m.index).trim())}</span>` +
          `<span class="mi-v">${fmt.escape(piece.slice(m.index))}</span>`;
      }).join('');
      return `<span class="map-chip map-chip-stack" data-role="fact">${lines}</span>`;
    }
    const strong = i === 0 && isField ? ' map-chip-strong' : '';
    const role = roleOf(part, i);
    return `<span class="map-chip${strong}" data-role="${role}"${role === 'scale' ? viewAttr : ''}>${fmt.escape(part)}</span>`;
  }).join('');
  el.innerHTML = tag + chips;
}

const fmt = {
  num(v, d = 2) { return (v === null || v === undefined || Number.isNaN(v)) ? '--' : Number(v).toFixed(d); },
  int(v) { return (v === null || v === undefined) ? '--' : String(v); },
  bytes(b) {
    if (!b) return '--';
    return b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.round(b / 1024)} KB`;
  },
  ra(deg) {
    if (deg === null || deg === undefined) return '--';
    const hours = deg / 15;
    const h = Math.floor(hours);
    const m = Math.floor((hours - h) * 60);
    const s = ((hours - h) * 60 - m) * 60;
    return `${String(h).padStart(2, '0')}h${String(m).padStart(2, '0')}m${s.toFixed(1)}s`;
  },
  dec(deg) {
    if (deg === null || deg === undefined) return '--';
    const sign = deg < 0 ? '-' : '+';
    const a = Math.abs(deg);
    const d = Math.floor(a);
    const m = Math.floor((a - d) * 60);
    const s = ((a - d) * 60 - m) * 60;
    return `${sign}${String(d).padStart(2, '0')}d${String(m).padStart(2, '0')}m${s.toFixed(0)}s`;
  },
  sexa(deg) { return `${this.ra(deg)} ${this.dec(deg)}`; },
  escape(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  },
};

function ramp(stops) {
  // stops: [[t, [r,g,b]], ...] with t in 0..1
  const lut = new Uint8Array(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    let a = stops[0], b = stops[stops.length - 1];
    for (let k = 0; k < stops.length - 1; k++) {
      if (t >= stops[k][0] && t <= stops[k + 1][0]) { a = stops[k]; b = stops[k + 1]; break; }
    }
    const span = (b[0] - a[0]) || 1;
    const f = Math.min(1, Math.max(0, (t - a[0]) / span));
    lut[i * 3] = Math.round(a[1][0] + (b[1][0] - a[1][0]) * f);
    lut[i * 3 + 1] = Math.round(a[1][1] + (b[1][1] - a[1][1]) * f);
    lut[i * 3 + 2] = Math.round(a[1][2] + (b[1][2] - a[1][2]) * f);
  }
  return lut;
}

const RAMPS = {
  coadd: ramp([[0, [8, 12, 30]], [0.35, [40, 60, 130]], [0.65, [90, 170, 190]],
               [0.85, [232, 200, 100]], [1, [255, 250, 235]]]),
  diff: ramp([[0, [24, 62, 140]], [0.5, [248, 248, 246]], [1, [170, 28, 24]]]),
  snr: ramp([[0, [12, 8, 26]], [0.35, [86, 24, 104]], [0.6, [190, 62, 72]],
             [0.8, [244, 140, 60]], [1, [252, 244, 180]]]),
  viridis: ramp([[0, [18, 12, 46]], [0.3, [46, 86, 138]], [0.6, [34, 152, 136]],
                 [0.85, [145, 212, 72]], [1, [252, 232, 46]]]),
  gray: ramp([[0, [10, 12, 16]], [1, [248, 250, 252]]]),
  invgray: ramp([[0, [248, 250, 252]], [1, [10, 12, 16]]]),
};

const state = {
  data: null,
  layer: 'zogy_snr',
  stretch: 'linear',
  lut: 'auto',
  target: null,
  scaling: {},           // per-layer [min, max]
  view: { scale: 1, tx: 0, ty: 0 },
  offscreen: {},
  overlays: { grid: true, catalog: true, detections: true },
  selected: -1,
  demo: null,
  crosshair: null,
  live: false,
  fieldCoordinates: null,
  customFields: {},
  pipelineRunning: false,
  recoveryRunning: false,
  // Snapshot history: the retained published dashboards for the current field.
  // `selected` is a manifest object ('' = latest); frames are kept per target
  // in this session so a replay never downloads the same version twice.
  snapshotHistory: { target: null, snapshots: [], selected: '', playing: false,
                     loading: false, error: '', timer: null, runId: 0 },
  snapshotFrames: new Map(),
  snapshotScaleLock: null,   // frozen layer scales while a playback runs
  snapshotTrail: [],         // focus positions across snapshot frames
  focus: null,               // {ra, dec, label} carried across snapshot frames
};

// ------------------------------------------------------- layer rasterisation

function percentile(arr, p) {
  const flat = [];
  for (let y = 0; y < arr.length; y++) {
    for (let x = 0; x < arr[y].length; x++) {
      const v = arr[y][x];
      if (Number.isFinite(v)) flat.push(v);
    }
  }
  if (!flat.length) return 0;
  flat.sort((a, b) => a - b);
  const i = Math.min(flat.length - 1, Math.max(0, Math.round((p / 100) * (flat.length - 1))));
  return flat[i];
}

function stretchNorm(u, mode, symmetric) {
  const c = Math.min(1, Math.max(0, u));
  if (mode === 'linear') return c;
  if (symmetric) {
    const s = 2 * c - 1;
    const mag = mode === 'log'
      ? Math.log10(1 + 49 * Math.abs(s)) / Math.log10(50)
      : Math.asinh(6 * Math.abs(s)) / Math.asinh(6);
    return 0.5 + 0.5 * Math.sign(s) * mag;
  }
  if (mode === 'log') return Math.log10(1 + 99 * c) / 2;
  return Math.asinh(10 * c) / Math.asinh(10);
}

function buildLayer(name, arr, lutName, symmetric, fixedScale = null) {
  const npix = arr.length;
  const canvas = document.createElement('canvas');
  canvas.width = npix;
  canvas.height = npix;
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(npix, npix);
  const effectiveLut = state.lut && state.lut !== 'auto' ? state.lut : lutName;
  const lut = RAMPS[effectiveLut] || RAMPS[lutName];

  let lo, hi;
  const fixed = fixedScale && fixedScale[name];
  if (Array.isArray(fixed) && fixed.length === 2 && fixed[1] > fixed[0]) {
    [lo, hi] = fixed;                    // playback: keep frames comparable
  } else if (symmetric) {
    const amp = Math.max(Math.abs(percentile(arr, 0.5)), Math.abs(percentile(arr, 99.5)), 1e-6);
    lo = -amp;
    hi = amp;
  } else {
    lo = percentile(arr, 0.5);
    hi = percentile(arr, 99.5);
    if (!(hi > lo)) hi = lo + 1e-6;
  }
  state.scaling[name] = [lo, hi];

  for (let y = 0; y < npix; y++) {
    for (let x = 0; x < npix; x++) {
      const v = arr[y][x];
      const t = stretchNorm((v - lo) / (hi - lo), state.stretch, symmetric);
      const i = Math.round(t * 255) * 3;
      const row = npix - 1 - y;                 // image row 0 at the bottom
      const o = 4 * (row * npix + x);
      img.data[o] = lut[i];
      img.data[o + 1] = lut[i + 1];
      img.data[o + 2] = lut[i + 2];
      img.data[o + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  state.offscreen[name] = canvas;
}

function rebuildLayers(useScaleLock = false) {
  if (!state.data) return;
  const lock = useScaleLock ? state.snapshotScaleLock : null;
  buildLayer('image', state.data.image, 'coadd', false, lock);
  buildLayer('zogy_image', state.data.zogy_image, 'diff', true, lock);
  buildLayer('zogy_snr', state.data.zogy_snr, 'snr', false, lock);
}

// ------------------------------------------------------------- canvas maths

function canvasSize() {
  const c = $('canvas-map');
  return { w: c.clientWidth || c.width, h: c.clientHeight || c.height };
}

function currentNpix() {
  return state.data ? state.data.map.npix : 180;
}

function fitView() {
  const npix = currentNpix();
  const { w, h } = canvasSize();
  const pad = 56;
  const s = Math.max(0.4, Math.min((w - pad) / npix, (h - pad) / npix));
  state.view.scale = s;
  state.view.tx = (w - s * npix) / 2;
  state.view.ty = (h - s * npix) / 2;
}

function toImage(mx, my) {
  const npix = currentNpix();
  const scale = state.view.scale || 1;
  const { tx, ty } = state.view;
  return { x: (mx - tx) / scale, y: npix - (my - ty) / scale };
}

function toCanvas(x, y) {
  const npix = currentNpix();
  const { scale, tx, ty } = state.view;
  return { cx: tx + x * scale, cy: ty + (npix - y) * scale };
}

function pixelToSky(x, y) {
  const m = state.data.map;
  const cosd = Math.cos((m.dec * Math.PI) / 180);
  const ra = m.ra - ((x - m.npix / 2) * m.pixscale_arcsec) / 3600 / cosd;
  const dec = m.dec + ((y - m.npix / 2) * m.pixscale_arcsec) / 3600;
  return { ra, dec };
}

// Inverse of pixelToSky for the small linear field approximation used by the
// map.  Used to carry a selected object across snapshot frames.
function skyToPixel(ra, dec, map = state.data && state.data.map) {
  if (!map) return null;
  const cosd = Math.cos((map.dec * Math.PI) / 180) || 1e-6;
  const dra = ((ra - map.ra + 540) % 360) - 180;
  // RA grows to the left (east), as in pixelToSky above.
  const x = map.npix / 2 - ((dra * 3600 * cosd) / map.pixscale_arcsec);
  const y = map.npix / 2 + (((dec - map.dec) * 3600) / map.pixscale_arcsec);
  return { x, y };
}

function valueAt(x, y) {
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  const arr = state.data && state.data[state.layer];
  if (!arr || !arr.length) return null;
  const ix = Math.round(x), iy = Math.round(y);
  if (iy < 0 || iy >= arr.length) return null;
  const row = arr[iy];
  if (!row || ix < 0 || ix >= row.length) return null;
  const v = row[ix];
  return Number.isFinite(v) ? v : null;
}


// ------------------------------------------------------------------ drawing

const DET_COLORS = { dipole: '#22c55e', monopole: '#f59e0b', monopole_negative: '#38bdf8' };
const CAT_COLORS = {
  gaia: '#4d94ff', wise: '#3fb950', allwise: '#3fb950', vsx: '#d29922',
  neowise: '#f0883e', unwise: '#a371f7', twomass: '#ff7b72', '2mass': '#ff7b72',
  panstarrs: '#79c0ff', ps1: '#79c0ff', skymapper: '#56d364',
  simbad: '#e3b341', ssois: '#8b949e', jpl: '#ffa657', neossat: '#ffa657',
};
const CAT_FALLBACK_COLORS = ['#4d94ff', '#3fb950', '#d29922', '#a371f7',
  '#f0883e', '#ff7b72', '#56d364', '#79c0ff', '#e3b341', '#ffa657'];
const CATALOG_LABELS = {
  gaia: 'Gaia DR3', wise: 'AllWISE', allwise: 'AllWISE', vsx: 'VSX',
  neowise: 'NEOWISE', unwise: 'unWISE', twomass: '2MASS', '2mass': '2MASS',
  panstarrs: 'Pan-STARRS', ps1: 'Pan-STARRS', skymapper: 'SkyMapper',
  simbad: 'SIMBAD', jpl: 'JPL', ssois: 'SSOIS', neossat: 'NEOSSat',
};
// The catalogue swatches that already exist in index.html; any other catalogue
// present in the data gets one appended at render time.
const LEGEND_CATALOGS = new Set(['gaia', 'wise', 'allwise', 'vsx']);

function catalogLabel(key) {
  return CATALOG_LABELS[String(key).toLowerCase()] || String(key);
}

function catalogColor(key) {
  const name = String(key || '');
  if (CAT_COLORS[name.toLowerCase()]) return CAT_COLORS[name.toLowerCase()];
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return CAT_FALLBACK_COLORS[hash % CAT_FALLBACK_COLORS.length];
}

// Catalogues the payload actually carries, without the non-catalogue status
// bookkeeping entries (e.g. catalog_status.gaia_mirror).
function catalogCountEntries(dashboard) {
  const counts = dashboard?.catalog_counts || {};
  const status = dashboard?.catalog_status || {};
  const keys = new Set(Object.keys(counts));
  Object.keys(status).forEach((key) => {
    if (['ok', 'none', 'unavailable'].includes(status[key])) keys.add(key);
  });
  return [...keys].filter((key) => key && key !== 'gaia_mirror').sort()
    .map((key) => [key, counts[key]]);
}

// Asteroid verdicts are drawn as diamonds in their own colour, whatever the kind.
const ASTEROID_COLORS = { KNOWN_ASTEROID: '#2dd4bf', POSSIBLE_UNKNOWN_ASTEROID: '#f472b6' };
const KIND_LABEL = { dipole: 'dipole', monopole: 'monopole', monopole_negative: 'got fainter' };

// Triage categories (step4_triage.py). Priority 1-2 = worth a human look.
const CATEGORY_INFO = {
  CANDIDATE:            { label: 'CANDIDATE',      cls: 'badge-candidate', hint: 'in no catalogue -- follow-up' },
  KNOWN_STATIC_CHANGED: { label: 'STATIC CHANGED', cls: 'badge-static',    hint: 'catalogued as constant, yet it changed' },
  KNOWN_VARIABLE:       { label: 'KNOWN VARIABLE', cls: 'badge-variable',  hint: 'change expected: catalogued variable' },
  KNOWN_MOVER:          { label: 'KNOWN MOVER',    cls: 'badge-mover',     hint: 'change expected: catalogued motion' },
  KNOWN_ASTEROID:       { label: 'KNOWN ASTEROID', cls: 'badge-asteroid',  hint: 'a catalogued asteroid was here at that time' },
  POSSIBLE_UNKNOWN_ASTEROID: { label: 'NEW ASTEROID?', cls: 'badge-newast', hint: 'moves like an asteroid, but no known asteroid is here' },
  BRIGHT_STAR_ARTEFACT: { label: 'BRIGHT-STAR ART.', cls: 'badge-artefact', hint: 'inside a saturating star’s mask' },
  MASK_ARTEFACT:        { label: 'MASK ARTEFACT',  cls: 'badge-artefact',  hint: 'no band is unmasked in every epoch here' },
};

// Public mode: one plain sentence per verdict.
const PLAIN_VERDICT = {
  CANDIDATE: 'Something changed here and no catalogue knows it. Worth a closer look.',
  KNOWN_STATIC_CHANGED: 'A star that catalogues call steady changed. Unusual, worth a look.',
  KNOWN_VARIABLE: 'A star already known to change brightness. Expected.',
  KNOWN_MOVER: 'A known star that moves across the sky. Expected.',
  KNOWN_ASTEROID: 'A known asteroid passing through. Expected, not a discovery.',
  POSSIBLE_UNKNOWN_ASTEROID: 'Moves like an asteroid, but no known asteroid was here. Could be new.',
  BRIGHT_STAR_ARTEFACT: 'Probably glare from a very bright star, not a real change.',
  MASK_ARTEFACT: 'Probably a camera artefact: the pixels here were masked.',
};

function plainVerdict(d) {
  const cat = categoryOf(d);
  if (cat && PLAIN_VERDICT[cat]) return PLAIN_VERDICT[cat];
  return d && d.verdict === 'CANDIDATE'
    ? PLAIN_VERDICT.CANDIDATE : 'Matches a catalogued object.';
}

function kindLabel(kind) {
  return KIND_LABEL[kind] || String(kind || 'detection');
}

function categoryOf(d) {
  if (d && d.category && CATEGORY_INFO[d.category]) return d.category;
  // pre-rendered JSON from before categories existed
  return d && d.verdict === 'CANDIDATE' ? 'CANDIDATE' : null;
}

function categoryBadge(d) {
  const cat = categoryOf(d);
  if (!cat) {
    const v = (d && d.verdict) || 'KNOWN';
    return `<span class="badge ${v === 'KNOWN' ? 'badge-known' : 'badge-candidate'}">${fmt.escape(v)}</span>`;
  }
  const info = CATEGORY_INFO[cat];
  const star = d.priority && d.priority <= 2 ? ' ★' : '';
  return `<span class="badge ${info.cls}" title="${fmt.escape(info.hint)}">${info.label}${star}</span>`;
}

function drawTargetAndMovers(ctx) {
  const tp = state.data.target_position;
  ctx.save();
  (state.data.expected_movers || []).forEach((m) => {
    if (m.is_target) return;
    const a = toCanvas(m.x_a, m.y_a), b = toCanvas(m.x, m.y);
    ctx.strokeStyle = m.detected ? 'rgba(124, 58, 237, .9)' : 'rgba(124, 58, 237, .45)';
    ctx.lineWidth = 1.3;
    ctx.setLineDash(m.detected ? [] : [3, 2]);
    ctx.beginPath(); ctx.moveTo(a.cx, a.cy); ctx.lineTo(b.cx, b.cy); ctx.stroke();
    ctx.beginPath(); ctx.arc(b.cx, b.cy, 3, 0, Math.PI * 2); ctx.stroke();
  });
  ctx.setLineDash([]);
  if (tp && tp.x != null && tp.y != null) {
    const { cx, cy } = toCanvas(tp.x, tp.y);
    ctx.strokeStyle = '#7c3aed';
    ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.arc(cx, cy, 10, 0, Math.PI * 2); ctx.stroke();
    [[-16, -11], [11, 16]].forEach(([a, b]) => {
      ctx.beginPath(); ctx.moveTo(cx + a, cy); ctx.lineTo(cx + b, cy); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(cx, cy + a); ctx.lineTo(cx, cy + b); ctx.stroke();
    });
    if (tp.moving) {
      ctx.fillStyle = '#c4a7ff';
      ctx.font = '9px ui-monospace, Menlo, monospace';
      ctx.fillText('target (now)', cx + 13, cy - 10);
    }
  }
  ctx.restore();
}

function draw() {
  const canvas = $('canvas-map');
  const { w, h } = canvasSize();
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, w, h);

  if (state.viewMode === 'sky') {
    drawSky(ctx, w, h);
    return;
  }

  if (!state.data) return;

  const npix = state.data.map.npix;
  const { scale, tx, ty } = state.view;
  const side = scale * npix;

  const layer = state.offscreen[state.layer];
  if (layer) {
    ctx.imageSmoothingEnabled = scale < 3;
    ctx.drawImage(layer, tx, ty, side, side);
  }

  ctx.strokeStyle = '#94a3b8';
  ctx.lineWidth = 1;
  ctx.strokeRect(tx + 0.5, ty + 0.5, side, side);

  if (state.overlays.grid) drawGrid(ctx, tx, ty, side, npix);
  if (state.overlays.catalog) drawCatalogs(ctx);
  if (state.overlays.catalog) drawTargetAndMovers(ctx);
  if (state.overlays.detections) drawDetections(ctx);
  drawSnapshotTrail(ctx);
  if (state.highlight) {
    const { cx, cy } = toCanvas(state.highlight.x, state.highlight.y);
    ctx.save();
    ctx.strokeStyle = '#4d94ff';
    ctx.lineWidth = 1.6;
    ctx.beginPath(); ctx.arc(cx, cy, 13, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(cx, cy, 12, 0, Math.PI * 2); ctx.stroke();
    ctx.restore();
  }
  if (state.crosshair) drawCrosshair(ctx);
}

function drawGrid(ctx, tx, ty, side, npix) {
  const arcsecPerPix = state.data.map.pixscale_arcsec;
  const stepPix = 60 / arcsecPerPix;            // one cell = 1 arcminute
  ctx.save();
  ctx.strokeStyle = 'rgba(148, 163, 184, .38)';
  ctx.lineWidth = 1;
  ctx.setLineDash([2, 3]);
  for (let i = stepPix; i < npix; i += stepPix) {
    const cx = toCanvas(i, 0).cx;
    ctx.beginPath(); ctx.moveTo(cx, ty); ctx.lineTo(cx, ty + side); ctx.stroke();
    const cy = toCanvas(0, i).cy;
    ctx.beginPath(); ctx.moveTo(tx, cy); ctx.lineTo(tx + side, cy); ctx.stroke();
  }
  ctx.restore();

  ctx.save();
  ctx.fillStyle = '#94a3b8';
  ctx.font = '9px ui-monospace, Menlo, monospace';
  const half = ((npix / 2) * arcsecPerPix) / 60;
  for (let a = -Math.floor(half); a <= Math.floor(half); a += 2) {
    if (a === 0) continue;
    const pix = npix / 2 + (a * 60) / arcsecPerPix;
    if (pix < 0 || pix > npix) continue;
    ctx.textAlign = 'center';
    ctx.fillText(`${a > 0 ? '+' : ''}${a}'`, toCanvas(pix, 0).cx, ty + side + 12);
    ctx.textAlign = 'right';
    ctx.fillText(`${a > 0 ? '+' : ''}${a}'`, tx - 5, toCanvas(0, pix).cy + 3);
  }
  ctx.restore();
}

function drawCatalogs(ctx) {
  const pts = state.data.catalog_overlay || [];
  ctx.save();
  ctx.lineWidth = 1;
  for (const p of pts) {
    const { cx, cy } = toCanvas(p.x, p.y);
    ctx.strokeStyle = catalogColor(p.cat);
    ctx.globalAlpha = String(p.cat).toLowerCase() === 'vsx' ? 0.95 : 0.55;
    ctx.beginPath();
    ctx.moveTo(cx - 3.2, cy); ctx.lineTo(cx + 3.2, cy);
    ctx.moveTo(cx, cy - 3.2); ctx.lineTo(cx, cy + 3.2);
    ctx.stroke();
  }
  ctx.restore();
}

function drawDetections(ctx) {
  const dets = state.data.detections || [];
  ctx.save();
  dets.forEach((d, i) => {
    const pos = d.position || [0, 0];
    const { cx, cy } = toCanvas(pos[1], pos[0]);
    const color = DET_COLORS[d.kind] || '#e6edf3';
    const big = i === state.selected;
    const r = big ? 8 : 6;

    const minus = d.pos_minus || d.position_minus;
    if (d.kind === 'dipole' && minus) {
      const m2 = toCanvas(minus[1], minus[0]);
      ctx.strokeStyle = 'rgba(15, 157, 88, .8)';
      ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.moveTo(m2.cx, m2.cy); ctx.lineTo(cx, cy); ctx.stroke();
      ctx.beginPath(); ctx.arc(m2.cx, m2.cy, 2.4, 0, Math.PI * 2); ctx.stroke();
    }

    const astColor = ASTEROID_COLORS[d.category];
    ctx.strokeStyle = astColor || color;
    ctx.lineWidth = big ? 2.2 : 1.4;
    if (astColor) {
      const q = r + 2;                       // diamond: asteroid verdicts
      ctx.beginPath();
      ctx.moveTo(cx, cy - q); ctx.lineTo(cx + q, cy);
      ctx.lineTo(cx, cy + q); ctx.lineTo(cx - q, cy);
      ctx.closePath(); ctx.stroke();
    } else if (d.kind === 'dipole') {
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
    } else {
      ctx.strokeRect(cx - r, cy - r, 2 * r, 2 * r);
      if (d.kind === 'monopole_negative') {  // minus sign: got fainter
        ctx.beginPath(); ctx.moveTo(cx - r + 2.5, cy); ctx.lineTo(cx + r - 2.5, cy); ctx.stroke();
      }
    }
    if (big) {
      ctx.fillStyle = astColor || color;
      ctx.font = '10px ui-monospace, Menlo, monospace';
      ctx.fillText(`${d.snr.toFixed(1)}σ`, cx + r + 4, cy - r + 2);
    }
  });
  ctx.restore();
}

function drawSnapshotTrail(ctx) {
  const trail = state.snapshotTrail || [];
  if (trail.length < 2) return;
  ctx.save();
  ctx.strokeStyle = 'rgba(163, 113, 247, .9)';   // --purple
  ctx.lineWidth = 1.4;
  ctx.setLineDash([4, 3]);
  ctx.beginPath();
  trail.forEach((point, index) => {
    const { cx, cy } = toCanvas(point.x, point.y);
    if (index === 0) ctx.moveTo(cx, cy);
    else ctx.lineTo(cx, cy);
  });
  ctx.stroke();
  ctx.setLineDash([]);
  trail.forEach((point, index) => {
    const { cx, cy } = toCanvas(point.x, point.y);
    const last = index === trail.length - 1;
    ctx.fillStyle = last ? '#a371f7' : 'rgba(163, 113, 247, .55)';
    ctx.beginPath();
    ctx.arc(cx, cy, last ? 3.2 : 2, 0, Math.PI * 2);
    ctx.fill();
  });
  const last = trail[trail.length - 1];
  const { cx, cy } = toCanvas(last.x, last.y);
  ctx.fillStyle = '#c4a7ff';
  ctx.font = '10px ui-monospace, Menlo, monospace';
  ctx.fillText(`${trail.length} snapshot positions`, cx + 7, cy + 12);
  ctx.restore();
}

function drawCrosshair(ctx) {
  const { cx, cy } = state.crosshair;
  ctx.save();
  ctx.strokeStyle = 'rgba(226, 232, 240, 0.42)';
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  ctx.moveTo(0, cy); ctx.lineTo(cx - 6, cy);
  ctx.moveTo(cx + 6, cy); ctx.lineTo(ctx.canvas.width, cy);
  ctx.moveTo(cx, 0); ctx.lineTo(cx, cy - 6);
  ctx.moveTo(cx, cy + 6); ctx.lineTo(cx, ctx.canvas.height);
  ctx.stroke();
  ctx.restore();
}


// ------------------------------------------------------------- interaction

let drag = null;

function updateReadout(mx, my) {
  if (state.viewMode === 'sky') {
    const unavailableTile = state.tileRects.find(
      (rect) => rect.tile.target === state.hoverTile && rect.tile.available === false);
    if (unavailableTile) {
      const tile = unavailableTile.tile;
      const checked = tile.irsa_check ? ` (${irsaCheckText(tile.irsa_check)})` : '';
      setText('map-readout',
        `${tile.common || tile.target}: ${tile.reason || 'No comparable detector coverage.'}${checked}`);
      return;
    }
    const freshTile = state.tileRects.find(
      (rect) => rect.tile.target === state.hoverTile && rect.tile.irsa_check);
    if (freshTile) {
      const tile = freshTile.tile;
      setText('map-readout', `${tile.common || tile.target}: ${irsaCheckText(tile.irsa_check)}` +
        (tile.elapsed_seconds != null ? ` \u00b7 ${tile.elapsed_seconds} s` : ''));
      return;
    }
    const { w, h } = canvasSize();
    const pad = 42, plotW = w - 2 * pad, plotH = h - 2 * pad;
    const px = mx - pad, py = my - pad;
    if (plotW <= 0 || plotH <= 0 || px < 0 || py < 0 || px > plotW || py > plotH) {
      setText('map-readout', 'all-sky view · move over a tile');
      return;
    }
    const ra = (360 - (px / plotW) * 360 + 360) % 360;
    const dec = 90 - (py / plotH) * 180;
    setText('map-readout', `${fmt.ra(ra)} ${fmt.dec(dec)}   all-sky`);
    return;
  }
  if (!state.data) return;
  const { x, y } = toImage(mx, my);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return;
  if (x < 0 || y < 0 || x > currentNpix() || y > currentNpix()) {
    setText('map-readout', 'RA --\u00b7---   Dec --\u00b7---');
    return;
  }
  const { ra, dec } = pixelToSky(x, y);
  const v = valueAt(x, y);
  const unit = state.layer === 'zogy_snr' ? '\u03c3' : 'MJy/sr';
  const value = v === null ? '--' : `${v.toFixed(state.layer === 'image' ? 3 : 2)} ${unit}`;
  setText('map-readout',
    `${fmt.ra(ra)} ${fmt.dec(dec)}   px ${x.toFixed(0)},${y.toFixed(0)}   ${value}`);
}

function attachMapEvents() {
  const canvas = $('canvas-map');
  const wrap = canvas.parentElement;

  canvas.addEventListener('mousedown', (e) => {
    drag = { x: e.clientX, y: e.clientY, tx: state.view.tx, ty: state.view.ty, moved: 0 };
    wrap.classList.add('dragging');
  });

  window.addEventListener('mousemove', (e) => {
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    if (drag) {
      const dx = e.clientX - drag.x;
      const dy = e.clientY - drag.y;
      drag.moved += Math.abs(dx) + Math.abs(dy);
      state.view.tx = drag.tx + dx;
      state.view.ty = drag.ty + dy;
      draw();
      return;
    }
    if (mx < 0 || my < 0 || mx > rect.width || my > rect.height) return;
    state.crosshair = { cx: mx, cy: my };
    updateHover(mx, my);
    updateReadout(mx, my);
    draw();
  });

  window.addEventListener('mouseup', (e) => {
    if (drag && drag.moved < 4) {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      if (state.viewMode === 'sky') {
        const hit = state.tileRects.find((r) => mx >= r.x && mx <= r.x + r.w
          && my >= r.y && my <= r.y + r.h);
        if (hit) {
          const sel = $('target-select');
          if ([...sel.options].some((o) => o.value === hit.tile.target)) {
            sel.value = hit.tile.target;
            switchView('field');
            runPipeline();
          }
        }
      } else {
        pickDetection(mx, my);
      }
    }
    drag = null;
    wrap.classList.remove('dragging');
  });

  canvas.addEventListener('mouseleave', () => {
    state.crosshair = null;
    state.hoverTile = null;
    hideTooltip();
    draw();
  });

  canvas.addEventListener('wheel', (e) => {
    if (!state.data) return;
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    zoomAt(e.clientX - rect.left, e.clientY - rect.top, e.deltaY < 0 ? 1.15 : 1 / 1.15);
  }, { passive: false });

  canvas.addEventListener('dblclick', () => { fitView(); updateZoomLabel(); draw(); });
}

function zoomAt(mx, my, factor) {
  const v = state.view;
  const next = Math.min(12, Math.max(0.35, v.scale * factor));
  const k = next / v.scale;
  v.tx = mx - (mx - v.tx) * k;
  v.ty = my - (my - v.ty) * k;
  v.scale = next;
  updateZoomLabel();
  draw();
}

function updateZoomLabel() {
  setText('zoom-label', `${state.view.scale.toFixed(1)}\u00d7`);
  const m = state.data && state.data.map;
  if (!m) return;
  const px = (60 / m.pixscale_arcsec) * state.view.scale;   // one arcminute in px
  $('scalebar-line').style.width = `${Math.min(120, Math.max(18, px))}px`;
  setText('scalebar-text', "1'");
}

function pickDetection(mx, my) {
  const dets = (state.data && state.data.detections) || [];
  let best = -1, bestD = 12;
  dets.forEach((d, i) => {
    const pos = d.position || [0, 0];
    const { cx, cy } = toCanvas(pos[1], pos[0]);
    const dist = Math.hypot(cx - mx, cy - my);
    if (dist < bestD) { bestD = dist; best = i; }
  });
  if (best < 0) return;
  // A second click on the selected marker opens its vetting card.
  if (best === state.selected) openVetCard(best, document.querySelector(`[data-vet="${best}"]`) || $('canvas-map'));
  else selectDetection(best);
}

function selectDetection(index) {
  state.selected = index;
  const det = state.data && state.data.detections[index];
  if (det && Number.isFinite(det.ra) && Number.isFinite(det.dec)) {
    state.focus = { ra: det.ra, dec: det.dec, label: det.kind || 'detection' };
  }
  renderVerdict(state.data.detections[index]);
  document.querySelectorAll('#detections-body tr')
    .forEach((tr, i) => tr.classList.toggle('selected', i === index));
  draw();
}


// ----------------------------------------------------------- data + panels

async function loadTargets() {
  if (MOCK_V2) {
    const sel = $('target-select');
    sel.innerHTML = '<option value="Barnards_Star">Barnard’s Star (v2 mock)</option>';
    sel.disabled = false;
    return;
  }
  try {
    const url = STATIC_MODE && LIVE_API
      ? remoteUrl('targets') : apiUrl('targets');
    const resp = await fetch(url, STATIC_MODE && LIVE_API
      ? { cache: 'no-store' } : {});
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const targets = (data.targets || []).filter((t) => t && t.name);
    if (!targets.length) return;
    const sel = $('target-select');
    const previous = sel.value;
    sel.innerHTML = targets
      .map((t) => `<option value="${fmt.escape(t.name)}">${fmt.escape(t.common || t.name)}</option>`)
      .join('');
    if (targets.some((t) => t.name === previous)) sel.value = previous;
    // Clear any previous error state.
    const wrap = sel && sel.parentElement;
    if (wrap) wrap.classList.remove('is-error');
  } catch (e) {
    console.error('failed to load target list', e);
    // B10 FIX: show an explicit error state instead of silently showing
    // whatever options were already in the dropdown.
    setText('footer-status', `Target list unavailable: ${e.message}`);
    const sel = $('target-select');
    if (sel) {
      sel.innerHTML = `<option disabled value="">— target list unavailable —</option>`;
      sel.value = '';
      sel.disabled = true;
    }
    const wrap = sel && sel.parentElement;
    if (wrap) wrap.classList.add('is-error');
  }
}

function showGuideTab(name) {
  document.querySelectorAll('.guide-tab').forEach((t) => {
    const on = t.dataset.tab === name;
    t.classList.toggle('active', on);
    t.setAttribute('aria-selected', String(on));
    t.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll('.guide-body').forEach((b) => {
    const on = b.dataset.panel === name;
    b.classList.toggle('active', on);
    b.hidden = !on;
  });
}

function setStatus(text, kind) {
  const pill = document.querySelector('.status-pill');
  pill.classList.remove('busy', 'error');
  if (kind) pill.classList.add(kind);
  setText('status-text', text);
  // The pill is a button; announce the status through a separate live region.
  setText('sr-status', `Status: ${String(text).replace(/·/g, ',').toLowerCase()}`);
}

function renderTargetMeta(data) {
  setText('kv-ra', `${fmt.ra(data.ra)}  (${fmt.num(data.ra, 4)}\u00b0)`);
  setText('kv-dec', `${fmt.dec(data.dec)}  (${fmt.num(data.dec, 4)}\u00b0)`);
  setText('kv-type', `${data.n_epoch1}+${data.n_epoch2} exposures`);
}

function renderCards(data) {
  const s = data.stats || {};
  setText('card-total', fmt.int(s.n_detections));
  setText('card-unknown', fmt.int(s.n_candidate));
  setText('card-snr', s.max_snr ? s.max_snr.toFixed(1) : '--');
  setText('card-baseline', s.baseline_days ? String(Math.round(s.baseline_days)) : '--');
}

function renderStats(data) {
  const s = data.stats || {};
  setText('stat-exp', fmt.int(s.n_exposures));
  setText('stat-epochs', fmt.int(s.n_epochs));
  setText('stat-baseline', s.baseline_days
    ? `${Math.round(s.baseline_days)} d \u00b7 ${s.baseline_months} mo` : '--');
  setText('stat-sigma', s.sigma1 ? `${s.sigma1.toFixed(4)} MJy/sr` : '--');
  setText('stat-depth', s.depth_5sigma ? `${s.depth_5sigma.toFixed(4)} MJy/sr` : '--');
  setText('stat-fov', `${fmt.num(s.fov_arcmin, 1)}' \u00b7 ${fmt.num(s.pixscale_arcsec, 1)}\u2033`);
  setText('stat-cats', `${fmt.int(s.n_gaia)} \u00b7 ${fmt.int(s.n_wise)} \u00b7 ${fmt.int(s.n_vsx)}`);
  setText('stat-priority', s.n_priority != null ? fmt.int(s.n_priority) : '--');
  setText('stat-movers', s.n_expected_movers
    ? `${fmt.int(s.n_movers_detected)} / ${fmt.int(s.n_expected_movers)} seen` : '0');
  const a = s.astrometry;
  setText('stat-astrometry', a && a.scatter_arcsec != null
    ? `\u0394 ${a.median_dra_arcsec}\u2033, ${a.median_ddec_arcsec}\u2033 \u00b7 \u00b1${a.scatter_arcsec}\u2033 (${a.n_stars})`
    : (a && a.note ? 'n/a' : '--'));
  setText('stat-kinds', `${fmt.int(s.n_dipoles)} \u00b7 ${fmt.int(s.n_monopoles)}`);
  setText('stat-detect', fmt.int(s.n_detections));
  setText('stat-unknown', fmt.int(s.n_candidate));
  setText('stat-snr', s.max_snr ? s.max_snr.toFixed(1) : '--');
}

const REASON_TAGS = [
  [/^catalogued as non-variable/i, 'STATIC ANOMALY', 'vf-warn'],
  [/^no catalogue entry/i, 'UNMATCHED', 'vf-alert'],
  [/^VSX\b/i, 'VSX VARIABLE', 'vf-info'],
  [/^Gaia DR3 flags/i, 'GAIA VARIABLE', 'vf-info'],
  [/^catalogued motion/i, 'PROPER MOTION', 'vf-ok'],
  [/^already varied in NEOWISE/i, 'NEOWISE VAR', 'vf-info'],
  [/^stable in NEOWISE/i, 'NEOWISE STABLE', 'vf-warn'],
  [/^saturated in SPHEREx/i, 'SATURATED', 'vf-warn'],
  [/mover saturates SPHEREx/i, 'MOVER SAT.', 'vf-warn'],
  [/^nearby AllWISE entry ignored/i, 'WISE IGNORED', 'vf-mute'],
  [/exceeds|dipole separation/i, 'ASTROMETRY', 'vf-warn'],
  [/chance alignment/i, 'CHANCE ALIGN', 'vf-warn'],
  [/saturating/i, 'BRIGHT STAR', 'vf-mute'],
  [/unmasked|bands dropped/i, 'MASK', 'vf-mute'],
];

function formatVerdictFlag(reason) {
  const rule = REASON_TAGS.find(([re]) => re.test(reason));
  const [, tag, cls] = rule || [null, 'NOTE', 'vf-mute'];
  const cleaned = String(reason)
    .replace(/\s*--\s*/g, ' \u2014 ')
    .replace(/^catalogued as non-variable, non-moving/i, 'non-variable, non-moving entry');
  return `<div class="verdict-flag"><span class="vf-tag ${cls}">${tag}</span>` +
    `<span class="vf-text">${fmt.escape(cleaned)}</span></div>`;
}

function renderVerdict(det) {
  const box = $('verdict-box');
  if (!det) {
    box.innerHTML = '<div class="verdict-label">No detection in this field</div>';
    return;
  }
  const cat = categoryOf(det);
  const info = cat ? CATEGORY_INFO[cat] : null;
  const hasReasons = Boolean((det.reasons || []).length);
  let html = `<div class="verdict-head">${categoryBadge(det)}` +
    (!hasReasons && info ? `<span class="verdict-hint">${fmt.escape(info.hint)}</span>` : '') +
    '<button type="button" class="btn btn-secondary vet-open" data-vet-open>Open vetting card</button></div>';
  html += `<p class="plain-verdict public-only">${fmt.escape(plainVerdict(det))}</p>`;
  html += verdictScoreStrip(det);
  let rows = '';
  const line = (label, detail, num = '') => {
    rows += `<tr><th><span class="v-pill">${label}</span></th>` +
      `<td class="v-detail">${detail}</td>` +
      `<td class="v-num mono">${num}</td></tr>`;
  };

  if ((det.reasons || []).length) {
    html += '<div class="verdict-flags">' +
      det.reasons.map(formatVerdictFlag).join('') + '</div>';
  }
  const keys = Object.keys(det.matches || {});
  if (keys.length) {
    const details = [];
    const seps = [];
    keys.forEach((k) => {
      const m = det.matches[k];
      const name = m.source && (m.source.name || m.source.designation);
      const p = m.p_chance != null ? ` p<sub>chance</sub>=${m.p_chance.toFixed(2)}` : '';
      details.push(`${fmt.escape(k)}${name ? ` (${fmt.escape(name)})` : ''}${p}`);
      seps.push(m.sep_arcsec != null ? `${m.sep_arcsec.toFixed(1)}\u2033` : '--');
    });
    line('Matched', details.join('<br>'), seps.join('<br>'));
  } else if (!cat) {
    line('Match', 'No catalog match \u00b7 follow-up recommended', '\u2014');
  }
  const v = det.vsx;
  if (v) {
    const parts = [fmt.escape(v.label || v.type || 'variable')];
    if (v.period_d) parts.push(`P ${fmt.num(v.period_d, 2)} d`);
    if (v.cycles_in_baseline != null) parts.push(`${v.cycles_in_baseline} cyc`);
    if (v.passband) parts.push(fmt.escape(v.passband));
    if (v.url) parts.push(`<a href="${fmt.escape(v.url)}" target="_blank" rel="noopener">AAVSO \u2197</a>`);
    const vNum = v.amplitude_mag != null ? `${v.amplitude_mag} mag`
      : (v.period_d ? `${fmt.num(v.period_d, 2)} d` : '\u2014');
    line('VSX', parts.join(' \u00b7 '), vNum);
  }
  if (det.delta_mag != null) {
    line('SPHEREx \u0394mag', 'Epoch flux change', `${det.delta_mag > 0 ? '+' : ''}${det.delta_mag} mag`);
  }
  const g = det.gaia;
  if (g && (g.distance_pc || g.var_flag === 'VARIABLE' || g.abs_g != null)) {
    const parts = [];
    if (g.nearby) parts.push('nearby');
    if (g.abs_g != null) parts.push(`M<sub>G</sub> ${g.abs_g}`);
    if (g.var_flag === 'VARIABLE') parts.push('Gaia VARIABLE');
    const gNum = g.distance_pc ? `${fmt.num(g.distance_pc, 1)} pc` : (g.var_flag === 'VARIABLE' ? 'VAR' : '--');
    line('Gaia', parts.join(' \u00b7 ') || 'DR3', gNum);
  }
  const w = det.wise;
  if (w && w.w1_w2 != null) {
    line('AllWISE W1\u2212W2', fmt.escape(w.colour_class || 'IR colour'), `${w.w1_w2} mag`);
  }
  const mc = det.motion_check;
  if (mc) {
    line('Motion',
      `${fmt.escape(mc.catalogue)} ${fmt.num(mc.pm_masyr / 1000, 2)}\u2033/yr \u00b7 ` +
      `pred ${fmt.num(mc.predicted_arcsec, 1)}\u2033 \u00b7 ${fmt.escape(mc.status || '')}`,
      `${mc.measured_arcsec != null ? fmt.num(mc.measured_arcsec, 1) : '--'}\u2033`);
  }
  const n = det.neowise;
  if (n && n.n_visits) {
    const w1 = n.w1 || {};
    const pm = n.pm_significant ? ` \u00b7 pm ${fmt.num(n.pm_total_masyr / 1000, 2)}\u2033/yr` : '';
    line('NEOWISE',
      `W1 amp ${w1.amplitude ?? '--'} mag \u00b7 \u03c7\u00b2\u1d63 ${w1.chi2_red ?? '--'} \u00b7 ` +
      `${n.variable ? 'VARIABLE' : 'stable'}${pm}`,
      `${n.n_visits} visits`);
  }
  if (det.mask && det.mask.bands_excluded) {
    line('Mask', 'Unmasked epoch check', `${det.mask.bands_excluded}/${det.mask.n_bands} excl`);
  }
  line('Position', 'ICRS (RA, Dec)', `${fmt.num(det.ra, 5)}, ${fmt.num(det.dec, 5)}`);
  line('Detection', `${fmt.escape(kindLabel(det.kind))} \u00b7 conf ${fmt.escape(det.confidence || '--')}`, `${fmt.num(det.snr, 1)}\u03c3`);
  html += `<table class="kv-table verdict-table"><tbody>${rows}</tbody></table>`;
  box.innerHTML = html;
  const openBtn = box.querySelector('[data-vet-open]');
  if (openBtn) {
    openBtn.addEventListener('click', () => {
      const i = state.data ? state.data.detections.indexOf(det) : -1;
      if (i >= 0) openVetCard(i, openBtn);
    });
  }
}

function renderTable(dets) {
  const tbody = $('detections-body');
  if (!dets || !dets.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty">No detections</td></tr>';
    return;
  }
  tbody.innerHTML = dets.map((d, i) => {
    const vBadge = categoryBadge(d);
    const kBadge = `<span class="badge badge-${fmt.escape(d.kind)}">${fmt.escape(kindLabel(d.kind))}</span>`;
    const match = d.asteroid && d.asteroid.status === 'match' && d.asteroid.match
      ? d.asteroid.match.designation
      : (Object.keys(d.matches || {}).length ? Object.keys(d.matches).join(', ') : '\u2014');
    const mu = d.proper_motion_arcsec_yr ? d.proper_motion_arcsec_yr.toFixed(1) : '\u2014';
    const ml = d.ml && Number.isFinite(d.ml.real_bogus)
      ? `${Math.round(d.ml.real_bogus * 100)}%${d.ml.uncertain ? ' <span class="obj-note" title="the model is unsure">?</span>' : ''}`
      : '\u2014';
    const ev = d.evidence && Number.isFinite(d.evidence.score)
      ? String(Math.round(d.evidence.score * 100)) : '\u2014';
    return `<tr data-index="${i}">
      <td>${fmt.num(d.ra, 4)}</td>
      <td>${fmt.num(d.dec, 4)}</td>
      <td>${fmt.num(d.snr, 1)}</td>
      <td>${kBadge}</td>
      <td>${vBadge}</td>
      <td>${fmt.escape(match)}</td>
      <td>${mu}</td>
      <td class="expert-only">${ml}</td>
      <td class="expert-only">${ev}</td>
      <td><button type="button" class="row-btn" data-vet="${i}"
        aria-label="Vet detection ${i + 1}: ${fmt.escape(kindLabel(d.kind))} at ${fmt.num(d.snr, 1)} sigma">Vet</button></td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('tr').forEach((tr) => {
    tr.addEventListener('click', (e) => {
      if (e.target.closest('[data-vet]')) return;
      selectDetection(parseInt(tr.dataset.index, 10));
    });
  });
  tbody.querySelectorAll('[data-vet]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const i = parseInt(btn.dataset.vet, 10);
      selectDetection(i);
      openVetCard(i, btn);
    });
  });
}


// ------------------------------------------------------- catalogue browser

state.objects = [];
state.objectFilter = 'all';
state.objectQuery = '';
state.highlight = null;      // {x, y} of the selected catalogue object

function renderPairs(data) {
  const pairs = data.epoch_pairs || [];
  const out = $('pairs-out');
  if (!pairs.length) {
    out.textContent = 'no epoch pairs available';
    return;
  }
  out.innerHTML = pairs.map((p) =>
    `${p.pair.padEnd(7)} ${String(p.n_exposures_a).padStart(2)}+${String(p.n_exposures_b).padStart(2)} exp` +
    `   sigma ${p.sigma_a.toFixed(4)} / ${p.sigma_b.toFixed(4)}` +
    `   -> ${p.n_detections} detection${p.n_detections === 1 ? '' : 's'}`
  ).map((line) => `<div class="pair-row">${fmt.escape(line)}</div>`).join('');
}

function renderObjects(data) {
  state.objects = data.field_objects || [];
  const known = state.objects.filter((o) => o.verdict === 'KNOWN').length;
  const cand = state.objects.filter((o) => o.verdict === 'CANDIDATE').length;
  const det = state.objects.filter((o) => o.detected).length;

  const offMap = state.objects.filter((o) => o.in_field === false).length;
  const catalogText = catalogCountEntries(data)
    .filter(([, count]) => count)
    .map(([key, count]) => ` - ${catalogLabel(key)} ${count}`).join('');
  setText('objects-hint',
    `${state.objects.length} objects (${state.objects.length - offMap} on the map` +
    (offMap ? `, ${offMap} outside)` : ')') + catalogText);

  syncCatalogLegend(data);

  const label = { all: 'all', known: 'known', candidate: 'candidate', detected: 'detected' };
  document.querySelectorAll('#object-filter .seg').forEach((b) => {
    b.dataset.count = { all: state.objects.length, known, candidate: cand, detected: det }[b.dataset.filter];
    b.textContent = `${b.textContent.split(' (')[0]} (${b.dataset.count})`;
  });
  drawObjectTable();
}

// Add a legend swatch for every catalogue in the payload that is not one of
// the three permanent entries (Gaia DR3 / VSX / AllWISE).  This is what makes
// extra catalogues visible on the map instead of silently drawing grey crosses.
function syncCatalogLegend(data) {
  const legend = $('map-legend');
  if (!legend) return;
  legend.querySelectorAll('[data-cat-legend]').forEach((el) => el.remove());
  const keys = new Set(Object.keys(data?.catalog_counts || {}));
  (data?.catalog_overlay || []).forEach((p) => { if (p && p.cat) keys.add(p.cat); });
  [...keys]
    .filter((key) => key && !LEGEND_CATALOGS.has(String(key).toLowerCase()))
    .sort()
    .forEach((key) => {
      const item = document.createElement('span');
      item.className = 'legend-item';
      item.dataset.catLegend = key;
      item.innerHTML = `<i class="sw" aria-hidden="true" style="background:${catalogColor(key)}"></i> ` +
        fmt.escape(catalogLabel(key));
      legend.appendChild(item);
    });
}

function drawObjectTable() {
  const tbody = $('objects-body');
  const q = state.objectQuery.trim().toLowerCase();
  let rows = state.objects.slice();
  if (state.objectFilter === 'known') rows = rows.filter((o) => o.verdict === 'KNOWN');
  if (state.objectFilter === 'candidate') rows = rows.filter((o) => o.verdict === 'CANDIDATE');
  if (state.objectFilter === 'detected') rows = rows.filter((o) => o.detected);
  if (q) {
    rows = rows.filter((o) =>
      (o.name || '').toLowerCase().includes(q) ||
      (o.type || '').toLowerCase().includes(q) ||
      (o.vsx_label || '').toLowerCase().includes(q) ||
      (o.category || '').toLowerCase().includes(q) ||
      o.catalogs.join(',').toLowerCase().includes(q) ||
      `${o.ra.toFixed(4)}`.includes(q) ||
      `${o.dec.toFixed(4)}`.includes(q));
  }

  setText('objects-count', `${rows.length} shown`);

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">No objects match this filter</td></tr>';
    return;
  }

  tbody.innerHTML = rows.slice(0, 400).map((o, i) => {
    const badge = o.detected && o.category ? categoryBadge(o)
      : `<span class="badge ${o.verdict === 'KNOWN' ? 'badge-known' : 'badge-candidate'}">${fmt.escape(o.verdict)}</span>`;
    const cats = ((o.catalogs || []).map((c) =>
      `<span class="cat-chip ${fmt.escape(c)}">${fmt.escape(c)}</span>`).join('') || '\u2014') +
      (o.vsx_label ? ` <span class="obj-note">${fmt.escape(o.vsx_label)}</span>` : '') +
      (o.dist_pc ? ` <span class="obj-note">${fmt.num(o.dist_pc, 1)} pc</span>` : '') +
      (o.colour_class && !o.vsx_label ? ` <span class="obj-note">W1\u2212W2 ${o.w1_w2}</span>` : '');
    const mag = (o.mag === null || o.mag === undefined) ? '\u2014' : o.mag.toFixed(2);
    const seen = o.detected ? 'yes' : 'no change';
    const snr = o.snr ? o.snr.toFixed(1) : '\u2014';
    const off = o.in_field === false ? ' off-map' : '';
    return `<tr data-i="${state.objects.indexOf(o)}" class="${off}">
      <td>${badge}</td>
      <td>${fmt.num(o.ra, 4)}</td>
      <td>${fmt.num(o.dec, 4)}</td>
      <td>${mag}</td>
      <td>${cats}</td>
      <td>${seen}</td>
      <td>${snr}</td>
      <td>${fmt.escape(o.pair || '\u2014')}</td>
    </tr>`;
  }).join('');

  tbody.querySelectorAll('tr').forEach((tr) => {
    // Keyboard: rows are focusable and Enter / Space select them like a click.
    tr.tabIndex = 0;
    tr.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); tr.click(); }
    });
    tr.addEventListener('click', () => {
      const obj = state.objects[parseInt(tr.dataset.i, 10)];
      state.highlight = (obj && obj.in_field !== false) ? { x: obj.x, y: obj.y } : null;
      state.focus = (obj && Number.isFinite(obj.ra) && Number.isFinite(obj.dec))
        ? { ra: obj.ra, dec: obj.dec, label: obj.name || obj.verdict } : null;
      draw();
      if (!obj) return;
      setText('footer-status', obj.in_field === false
        ? `${obj.verdict} at ${obj.ra.toFixed(3)}, ${obj.dec.toFixed(3)} -- outside the 9' map`
        : `selected ${obj.verdict} at ${obj.ra.toFixed(4)}, ${obj.dec.toFixed(4)}`);
    });
  });
}


function renderProvenance(data) {
  const rows = data.provenance || [];
  const tbody = $('sources-body');
  const s = data.stats || {};

  const catalogBits = catalogCountEntries(data)
    .map(([key, count]) => `${catalogLabel(key)} ${fmt.int(count)}`);
  const catalogText = catalogBits.length
    ? `${catalogBits.join(' / ')} entries cross-matched`
    : `Gaia DR3 ${fmt.int(s.n_gaia)} / AllWISE ${fmt.int(s.n_wise)} / VSX ${fmt.int(s.n_vsx)} entries cross-matched`;

  const archiveText =
    `IRSA SIA2 \u00b7 collection ${(rows[0] && rows[0].collection) || 'spherex_dr1'} \u00b7 ` +
    `${rows.length} cutout${rows.length === 1 ? '' : 's'} \u00b7 ` +
    `${s.depth_5sigma ? s.depth_5sigma.toFixed(3) : '--'} MJy/sr 5\u03c3 depth \u00b7 ` +
    catalogText;
  // P11 s6: chip colour by what the part is, matched on stable words (class only)
  const chipKind = (part) => (/\bSIA/.test(part) ? 'archive'
    : /^collection\b/.test(part) ? 'collection'
      : /\bcutouts?\b/.test(part) ? 'cutouts'
        : /\bdepth\b/.test(part) ? 'depth'
          : /cross-matched/.test(part) ? 'xmatch' : '');
  $('archive-line').innerHTML = archiveText.split(' \u00b7 ')
    .map((part) => {
      const kind = chipKind(part);
      return `<span class="archive-chip${kind ? ` archive-chip--${kind}` : ''}">${fmt.escape(part)}</span>`;
    }).join('');

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">No provenance recorded</td></tr>';
    return;
  }

  tbody.innerHTML = rows.map((r) => {
    const lam = r.wavelength_um ? r.wavelength_um.toFixed(3) : '--';
    const epoch = r.epoch
      ? `<span class="epoch-chip ${r.epoch > 1 ? 'e2' : ''}" data-epoch="${Number(r.epoch) || ''}">E${r.epoch}</span>` : '--';
    const obs = r.obs_id || (r.file || '').replace(/_cut\.fits$/, '');
    const link = r.url
      ? `<a href="${fmt.escape(r.url)}" target="_blank" rel="noopener"
            title="open the parent Level-2 file at IRSA">${fmt.escape(String(obs).slice(0, 20))} \u2197</a>`
      : fmt.escape(String(obs).slice(0, 20));
    const shape = Array.isArray(r.shape) ? `${r.shape[0]}\u00d7${r.shape[1]}` : '--';
    return `<tr>
      <td>${epoch}</td>
      <td>${fmt.escape(r.detector || '--')}</td>
      <td>${lam}</td>
      <td>${link}</td>
      <td>${fmt.escape(r.date || '--')}</td>
      <td>${shape}</td>
      <td>${fmt.bytes(r.bytes)}</td>
      <td title="${fmt.escape(r.url || '')}">IRSA \u00b7 ${fmt.escape(r.file || '')}</td>
    </tr>`;
  }).join('');
}

// --------------------------------------------------------- snapshot history
//
// Every published 12-hour snapshot is an immutable manifest with its own
// content-addressed dashboard objects.  The newest is always what the normal
// pipeline view shows; this control walks the retained previous_manifest chain
// and can replay those versions oldest -> latest.  It uses the same fetch
// helpers, rate limits, labels and browser cache as the latest-snapshot view.

const SNAPSHOT_PLAY_MS = 2500;       // keeps playback under 30 reads/minute
const SNAPSHOT_FRAME_LIMIT = 24;     // in-session frames kept per field

function formatSnapshotTime(value) {
  if (!value) return 'time unknown';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function snapshotFrameKey(target, manifest) {
  return `${target}|${manifest}`;
}

function snapshotHistoryAvailable() {
  // Historical dashboards come from the snapshot service, not the live
  // pipeline service: a direct-only static deployment has no history.
  return !STATIC_MODE || !!LIVE_API;
}

function renderSnapshotControls() {
  const group = $('snapshot-controls');
  const select = $('snapshot-select');
  const play = $('btn-snapshot-play');
  if (!group || !select || !play) return;
  const hist = state.snapshotHistory;
  const available = snapshotHistoryAvailable() && !state.live
    && offline.mode !== 'offline';
  const frames = hist.snapshots || [];

  let html = '<option value="">latest</option>';
  if (hist.loading && !frames.length) {
    html = '<option value="">loading history\u2026</option>';
  } else if (!available) {
    html = `<option value="">${state.live ? 'snapshot mode only'
      : (offline.mode === 'offline' ? 'unavailable offline' : 'no snapshot backend')}</option>`;
  } else if (hist.error && !frames.length) {
    html = '<option value="">history unavailable</option>';
  } else if (frames.length) {
    html = frames.map((snap) => {
      const label = snap.latest
        ? `Latest \u00b7 ${formatSnapshotTime(snap.published_at)}`
        : formatSnapshotTime(snap.published_at);
      return `<option value="${snap.latest ? '' : fmt.escape(snap.manifest)}"`
        + ` title="${fmt.escape(snap.published_at || '')}">${fmt.escape(label)}</option>`;
    }).join('');
  }
  select.innerHTML = html;
  const wanted = frames.some((snap) => (snap.latest ? '' : snap.manifest) === hist.selected)
    ? hist.selected : '';
  select.value = wanted;
  hist.selected = wanted;

  select.disabled = !available || hist.loading || frames.length < 1
    || (state.pipelineRunning && !hist.playing);
  play.disabled = !available || hist.loading || frames.length < 2
    || (state.pipelineRunning && !hist.playing);
  play.classList.toggle('playing', hist.playing);
  setText('snapshot-play-label', hist.playing ? 'Stop' : 'Play');
  const icon = play.querySelector('.snapshot-play-icon');
  if (icon) icon.innerHTML = hist.playing ? '&#9632;' : '&#9654;';

  const hours = hist.retentionHours;
  const retention = hours
    ? (hours >= 48 && hours % 24 === 0
      ? `, ${hours / 24} days retained`
      : `, ${hours} h retained`)
    : '';
  group.title = hist.error
    ? `Snapshot history: ${hist.error}`
    : (hist.playing
      ? 'Replaying retained snapshots oldest to latest; click Stop to keep the current frame'
      : `Previous published snapshots for this field${retention}; Play steps oldest to latest`);
}

async function loadSnapshotHistory(target, { keepSelection = false } = {}) {
  const hist = state.snapshotHistory;
  if (!target) {
    // Never ask the API for "whatever is default": that fallback is WISE 0855.
    hist.loading = false;
    hist.snapshots = [];
    hist.selected = '';
    hist.error = 'choose a field first';
    renderSnapshotControls();
    return;
  }
  if (hist.target !== target) {
    stopSnapshotPlayback({ resetScale: false });
    hist.target = target;
    hist.snapshots = [];
    hist.selected = '';
    hist.error = '';
    state.snapshotFrames.clear();
  }
  hist.error = '';
  if (!snapshotHistoryAvailable()) {
    hist.loading = false;
    hist.error = 'this deployment has no snapshot backend';
    renderSnapshotControls();
    return;
  }
  if (state.live) {
    hist.loading = false;
    hist.error = 'turn off Fresh IRSA to view published snapshot history';
    renderSnapshotControls();
    return;
  }
  if (offline.mode === 'offline') {
    hist.loading = false;
    hist.error = 'reconnect to list the published snapshots';
    renderSnapshotControls();
    return;
  }
  hist.loading = true;
  renderSnapshotControls();
  try {
    const response = await fetchApi('snapshot-history', target);
    if (!response.ok) {
      const problem = await response.json().catch(() => ({}));
      const code = response.status;
      throw new Error([problem.error, problem.detail].filter(Boolean).join(' ')
        || (code === 504
          ? 'the snapshot API timed out (HTTP 504) — redeploy or restart the Cloud Run API'
          : `HTTP ${code}`));
    }
    const data = await response.json();
    if (hist.target !== target) return;
    hist.snapshots = (data.snapshots || [])
      .filter((snap) => snap && snap.manifest && snap.dashboard);
    hist.retentionHours = data.retention_hours;
    if (!keepSelection
        || !hist.snapshots.some((snap) => (snap.latest ? '' : snap.manifest) === hist.selected)) {
      hist.selected = '';
    }
  } catch (error) {
    hist.snapshots = [];
    hist.selected = '';
    hist.error = error.message;
    setText('footer-status', `snapshot history unavailable: ${error.message}`);
  } finally {
    if (hist.target === target) {
      hist.loading = false;
      renderSnapshotControls();
    }
  }
}


// Carry the selected catalogue object or detection from one snapshot to the
// next, so playback shows that object's motion/dipole/monopole rather than an
// unrelated marker.  Matches within 10 arcsec and draws the existing ring.
function focusForSnapshot(data) {
  const focus = state.focus;
  if (!focus || !data || !data.map) return;
  const npix = data.map.npix;
  const inside = (point) => point && Number.isFinite(point.x)
    && Number.isFinite(point.y) && point.x >= 0 && point.x <= npix
    && point.y >= 0 && point.y <= npix;
  const pos = skyToPixel(focus.ra, focus.dec, data.map);
  state.highlight = inside(pos) ? { x: pos.x, y: pos.y } : null;

  let best = -1;
  let bestSep = Infinity;
  (data.detections || []).forEach((det, index) => {
    if (!Number.isFinite(det.ra) || !Number.isFinite(det.dec)) return;
    const sep = historySeparationArcsec(det.ra, det.dec, focus.ra, focus.dec);
    if (sep < bestSep) { bestSep = sep; best = index; }
  });

  let point = null;
  let pointRa = focus.ra;
  let pointDec = focus.dec;
  let label = focus.label || '';
  if (best >= 0 && bestSep <= 10) {
    const det = data.detections[best];
    const detPos = skyToPixel(det.ra, det.dec, data.map);
    if (inside(detPos)) {
      point = detPos;
      pointRa = det.ra;
      pointDec = det.dec;
      label = `${det.kind || 'detection'} ${Number.isFinite(det.snr)
        ? det.snr.toFixed(1) : ''}`.trim();
    }
    state.selected = best;
    renderVerdict(det);
    document.querySelectorAll('#detections-body tr')
      .forEach((tr, index) => tr.classList.toggle('selected', index === best));
  } else {
    state.selected = -1;
    if (inside(pos)) point = pos;
  }

  // Keep one point per published version, so replay draws the object's path
  // across snapshots without duplicating a frame that was rendered twice.
  if (point) {
    const stamp = data.snapshot_meta?.manifest
      || data.snapshot_meta?.published_at
      || data.generated_at || '';
    const trail = state.snapshotTrail;
    const last = trail[trail.length - 1];
    if (!last || last.stamp !== stamp) {
      trail.push({
        x: point.x, y: point.y, ra: pointRa, dec: pointDec, label, stamp,
        time: data.snapshot_meta?.published_at || data.generated_at || '',
      });
      if (trail.length > 24) trail.shift();
    }
  }
}

async function loadSnapshotFrame(snapshot, { preserveView = true, quiet = false } = {}) {
  const hist = state.snapshotHistory;
  if (!snapshot || snapshot.latest) {
    hist.selected = '';
    renderSnapshotControls();
    return runPipeline(null, null, null,
      { preserveView, quiet, suppressHistory: quiet });
  }
  hist.selected = snapshot.manifest;
  renderSnapshotControls();
  return runPipeline(null, null, null,
    { snapshotMeta: snapshot, preserveView, quiet });
}

function stopSnapshotPlayback({ resetScale = true } = {}) {
  const hist = state.snapshotHistory;
  if (hist.timer) {
    window.clearTimeout(hist.timer);
    hist.timer = null;
  }
  const wasPlaying = hist.playing;
  hist.playing = false;
  hist.runId += 1;
  if (resetScale && state.snapshotScaleLock) {
    state.snapshotScaleLock = null;
    if (state.data) {
      rebuildLayers();
      draw();
    }
  }
  if (wasPlaying) renderSnapshotControls();
}

async function startSnapshotPlayback() {
  const hist = state.snapshotHistory;
  if (hist.playing) {
    stopSnapshotPlayback();
    setText('footer-status', 'snapshot replay stopped');
    return;
  }
  const frames = (hist.snapshots || []).slice().reverse();   // oldest -> latest
  if (frames.length < 2 || !snapshotHistoryAvailable() || state.live
      || offline.mode === 'offline' || state.pipelineRunning) return;
  if (state.viewMode === 'sky') switchView('field');
  stopSnapshotPlayback({ resetScale: false });
  hist.playing = true;
  hist.runId += 1;
  const runId = hist.runId;
  state.snapshotScaleLock = null;      // captured after the first frame
  state.snapshotTrail = [];
  hist.selected = frames[0].latest ? '' : frames[0].manifest;
  renderSnapshotControls();
  setText('footer-status',
    `snapshot replay: ${frames.length} versions, oldest to latest`);

  const finish = (message) => {
    if (hist.runId !== runId) return;
    hist.playing = false;
    hist.timer = null;
    // Keep the frozen scale so the last frame stays directly comparable;
    // Stop, Run Pipeline and a field change release it.
    renderSnapshotControls();
    setText('footer-status', message);
  };

  const step = async (index) => {
    if (!hist.playing || hist.runId !== runId) return;
    if ($('target-select').value !== hist.target) {
      finish('snapshot replay stopped: the field changed');
      return;
    }
    const snapshot = frames[index];
    try {
      const rendered = await loadSnapshotFrame(snapshot,
        { preserveView: index > 0, quiet: true });
      if (rendered === false) {
        finish('snapshot replay stopped: that frame could not be loaded');
        return;
      }
    } catch (error) {
      finish(`snapshot replay stopped: ${error.message}`);
      return;
    }
    if (!hist.playing || hist.runId !== runId) return;
    if (index === 0) {
      // Freeze the first frame's stretch so later dipoles/monopoles are
      // compared against the same intensity scale instead of re-normalising.
      state.snapshotScaleLock = JSON.parse(JSON.stringify(state.scaling));
    }
    setText('footer-status',
      `snapshot replay ${index + 1}/${frames.length} \u00b7 ` +
      `${formatTimestamp(snapshot.published_at || snapshot.created_at)}`);
    if (index + 1 >= frames.length) {
      finish(`snapshot replay complete: ${frames.length} versions, oldest to latest`);
      return;
    }
    hist.timer = window.setTimeout(() => step(index + 1), SNAPSHOT_PLAY_MS);
  };
  step(0);
}

// --------------------------------------------------------------- pipeline IO

async function runPipeline(preloadedData = null, preloadedElapsed = null,
                           savedEntry = null, options = {}) {
  let statusPromise = null;   // fetched alongside the dashboard
  let fromSavedCopy = false;  // served by sw.js from this device's offline copy
  if (state.pipelineRunning) return false;
  const snapshotMeta = options.snapshotMeta || null;
  const preserveView = !!options.preserveView;
  const target = $('target-select').value;
  if (!target) {
    // No registered target and no custom field: never guess a field (the old
    // API default made this silently load WISE 0855).
    setStatus('STANDBY \u00b7 NO FIELD SELECTED', 'error');
    setText('map-hint', 'choose a target or load a field from coordinates');
    return false;
  }
  state.pipelineRunning = true;
  $('btn-run').disabled = true;
  const pipelineStarted = performance.now();
  const progress = startActionProgress(snapshotMeta
    ? `Loading snapshot from ${formatSnapshotTime(snapshotMeta.published_at)}`
    : (state.live
      ? 'Starting a fresh IRSA request'
      : 'Loading the selected field data'));
  let streamed = null;
  if (state.target !== target) {
    state.recovery = null;
    state.focus = null;          // a different field: drop the old highlight
    state.snapshotScaleLock = null;
    state.snapshotTrail = [];
    state.snapshotHistory.target = target;
    state.snapshotHistory.snapshots = [];
    state.snapshotHistory.selected = '';
    state.snapshotHistory.error = '';
    state.snapshotFrames.clear();
  }
  state.target = target;
  show('map-loader');
  setStatus(snapshotMeta
    ? `LOADING SNAPSHOT \u00b7 ${target.replace(/_/g, ' ').toUpperCase()}`
    : `LOADING ${target.replace(/_/g, ' ').toUpperCase()} \u00b7 FIELD DATA`,
  'busy');
  setText('map-hint', snapshotMeta
    ? `loading the published snapshot from ${formatTimestamp(
      snapshotMeta.published_at || snapshotMeta.created_at)}\u2026`
    : (state.live
      ? 'fetching archive cutouts and running the pipeline\u2026'
      : (STATIC_MODE && LIVE_API
        ? 'loading the latest published cloud snapshot\u2026'
        : 'loading field data\u2026')));
  setText('footer-status', `pipeline: ${target}`);
  setText('card-total', '\u2026');

  try {
    let data = preloadedData;
    if (!data) {
      if (snapshotMeta) {
        const key = snapshotFrameKey(target, snapshotMeta.manifest);
        data = state.snapshotFrames.get(key) || null;
        if (data) {
          progress.update('Using this session\u2019s saved copy of that snapshot');
        } else {
          const resp = await fetchApi('dashboard', target,
            { snapshot: snapshotMeta.manifest });
          if (!resp.ok) {
            const problem = await resp.json().catch(() => ({}));
            throw new Error([problem.error, problem.detail]
              .filter(Boolean).join(' ')
              || `Request failed: HTTP ${resp.status}`);
          }
          data = await resp.json();
          state.snapshotFrames.set(key, data);
          while (state.snapshotFrames.size > SNAPSHOT_FRAME_LIMIT) {
            state.snapshotFrames.delete(state.snapshotFrames.keys().next().value);
          }
        }
      } else if (state.live) {
        streamed = await fetchProgressApi('dashboard', target, (event) => {
          progress.update(event.message || 'Live pipeline is running');
          setText('map-hint', event.message || 'Live pipeline is running');
        });
        if (!streamed.unsupported) {
          if (!streamed.ok) {
            const problem = streamed.data || {};
            throw new Error([problem.error, problem.hint, problem.detail]
              .filter(Boolean).join(' ') || `Request failed: HTTP ${streamed.status}`);
          }
          data = streamed.data;
        } else {
          progress.update('This API has no progress stream; requesting the live endpoint directly');
          const resp = await fetchApi('dashboard', target);
          if (!resp.ok) {
            const problem = await resp.json().catch(() => ({}));
            throw new Error([problem.error, problem.hint, problem.detail]
              .filter(Boolean).join(' ') || `Request failed: HTTP ${resp.status}`);
          }
          data = await resp.json();
        }
      } else {
        // The freshness status is independent of the dashboard: request both
        // at once instead of one after the other.
        statusPromise = fetchApi('status', target).catch((e) => e);
        const resp = await fetchApi('dashboard', target);
        fromSavedCopy = resp.headers.get('X-SPX-Source') === 'offline-copy';
        if (!resp.ok) {
          const problem = await resp.json().catch(() => ({}));
          throw new Error([problem.error, problem.hint, problem.detail]
            .filter(Boolean).join(' ') || `Request failed: HTTP ${resp.status}` +
              (resp.status === 404 ? ' — or tick “Live from IRSA”' : ''));
        }
        data = await resp.json();
      }
    } else {
      progress.update('Rendering the field data');
    }
    if (data.error) {
      progress.update(data.error);
      setText('map-hint', data.error);
      setStatus('ERROR \u00b7 NO DATA', 'error');
      state.data = null;
      state.offscreen = {};
      draw();
      hide('map-loader');
      return false;
    }
    if (snapshotMeta) {
      // A historical dashboard is immutable; today's refresh status does not
      // describe it, so do not fetch or attach it.
      data.freshness = { refresh_attempt: { status: 'historical-snapshot' } };
      data.snapshot_meta = snapshotMeta;
    } else if (data.source === 'scheduled') {
      try {
        const statusResponse = await (statusPromise || fetchApi('status', target));
        if (statusResponse instanceof Error) throw statusResponse;
        if (!statusResponse.ok) throw new Error(`HTTP ${statusResponse.status}`);
        data.freshness = await statusResponse.json();
      } catch (error) {
        data.freshness = { refresh_attempt: { status: 'unavailable' } };
        console.warn('Cloud refresh status is unavailable:', error.message);
      }
    }

    const sameField = !!(state.data && data.map && state.data.map
      && state.data.target === data.target
      && state.data.map.npix === data.map.npix);
    if (!snapshotMeta && !preserveView) state.snapshotTrail = [];
    state.data = data;
    state.selected = data.detections.length ? 0 : -1;
    rebuildLayers(preserveView && !!state.snapshotScaleLock);
    if (!(preserveView && sameField)) fitView();
    updateZoomLabel();
    draw();

    // Each panel renders independently: a problem in one must not blank the page.
    const failures = [];
    const safe = (label, fn) => {
      try { fn(); } catch (err) {
        failures.push(label);
        console.error(`render failed: ${label}`, err);
      }
    };
    safe('target-meta', () => renderTargetMeta(data));
    safe('cards', () => renderCards(data));
    safe('stats', () => renderStats(data));
    safe('detections', () => renderTable(data.detections));
    safe('pairs', () => renderPairs(data));
    safe('catalogue', () => renderObjects(data));
    safe('charts', () => renderCharts(data));
    safe('history', () => renderHistory(data));
    safe('provenance', () => renderProvenance(data));
    safe('mosaic', () => updateMosaicTile(data));
    safe('verdict', () => renderVerdict(data.detections[0] || null));
    safe('focus', () => focusForSnapshot(data));
    safe('coverage', () => renderCoverage(data));
    safe('expert-access', () => renderExpertAccess(data));
    safe('map-summary', () => describeMap(data));
    safe('vet-card', () => refreshOpenVetCard());
    if (failures.length) {
      console.error('panels that failed to render:', failures.join(', '));
    }
    if (state.selected >= 0) {
      document.querySelectorAll('#detections-body tr')
        .forEach((tr, i) => tr.classList.toggle('selected', i === state.selected));
    }
    if (state.focus) draw();

    let freshness;
    if (snapshotMeta) {
      const when = formatTimestamp(
        snapshotMeta.published_at || snapshotMeta.created_at);
      freshness = snapshotMeta.latest
        ? `latest published snapshot \u00b7 ${when}`
        : `historical snapshot \u00b7 ${when}`;
      setText('cloud-support-freshness',
        'Snapshot history compares the retained published versions of this field.');
    } else if (data.source === 'scheduled') {
      const freshnessParts = ['published Cloud snapshot'];
      const processedAt = data.freshness?.field_last_processed_at
        || data.generated_at;
      freshnessParts.push(processedAt
        ? `field last processed ${formatTimestamp(processedAt)}`
        : 'field processing time unavailable');
      if (data.freshness?.field_last_checked_at) {
        freshnessParts.push(
          `last checked ${formatTimestamp(data.freshness.field_last_checked_at)}`);
      }
      if (data.freshness?.snapshot_published_at) {
        freshnessParts.push(
          `snapshot published ${formatTimestamp(data.freshness.snapshot_published_at)}`);
      }
      freshness = freshnessParts.join('; ');
      const supportTimes = [];
      if (data.freshness?.catalogs_last_checked_at) {
        supportTimes.push(
          `Catalogs checked ${formatTimestamp(data.freshness.catalogs_last_checked_at)}`);
      }
      if (data.freshness?.neowise_last_updated_at) {
        supportTimes.push(
          `NEOWISE/AllWISE updated ${formatTimestamp(
            data.freshness.neowise_last_updated_at)}`);
      }
      setText('cloud-support-freshness', supportTimes.join(' · '));
    } else if (data.source === 'live') {
      freshness = 'fresh SPHEREx cutouts from IRSA';
      setText('cloud-support-freshness', '');
    } else {
      freshness = data.generated_at
        ? `cache \u00b7 ${formatTimestamp(data.generated_at)}`
        : 'local cache';
      setText('cloud-support-freshness', '');
    }
    if (data.irsa_check) freshness = `${irsaCheckText(data.irsa_check)}; ${freshness}`;
    if (savedEntry) {
      freshness = `history snapshot (${formatTimestamp(savedEntry.saved_at)})`;
    }
    const missingCatalogs = unavailableCatalogs(data);
    if (missingCatalogs.length) {
      freshness += `; ${missingCatalogs.join(', ')} catalogue unavailable for this run, ` +
        'so matches against it are missing';
    }
    const offlineView = fromSavedCopy && offline.mode === 'offline';
    if (offlineView) {
      freshness = `offline copy (${formatTimestamp(offline.meta?.saved_at)}); ${freshness}`;
    }
    setText('map-hint',
      `${data.common} \u00b7 ${data.n_epoch1}+${data.n_epoch2} exposures \u00b7 ` +
      `${data.map.fov_arcmin}' field \u00b7 ${freshness}`);
    const processedAt = data.freshness?.field_last_processed_at
      || (data.source === 'scheduled' ? data.generated_at : null);
    progress.update(snapshotMeta
      ? `Snapshot from ${formatTimestamp(
        snapshotMeta.published_at || snapshotMeta.created_at)} loaded`
      : data.irsa_check?.reused_snapshot
        ? `IRSA checked in ${(streamed?.elapsedSeconds
          ?? (performance.now() - pipelineStarted) / 1000).toFixed(1)} s: snapshot up to date`
        : data.source === 'live'
          ? `IRSA pipeline complete in ${(streamed?.elapsedSeconds
            ?? preloadedElapsed
            ?? (performance.now() - pipelineStarted) / 1000).toFixed(1)} s`
          : (data.source === 'scheduled'
            ? (processedAt
              ? `Snapshot loaded (${formatTimestamp(processedAt)})`
              : 'Snapshot loaded')
            : (data.generated_at
              ? `Loaded from cache (${formatTimestamp(data.generated_at)})`
              : 'Loaded from local cache')));
    setStatus(savedEntry ? 'HISTORY \u00b7 SAVED'
      : offlineView ? (deviceOffline() ? 'OFFLINE \u00b7 SAVED COPY' : 'SERVER DOWN \u00b7 SAVED COPY')
        : snapshotMeta ? `SNAPSHOT \u00b7 ${snapshotMeta.latest ? 'READY' : 'HISTORY'}`
          : data.source === 'scheduled' ? 'SNAPSHOT \u00b7 READY'
            : 'CACHE \u00b7 READY', offlineView || savedEntry ? 'busy' : undefined);
    if (savedEntry) progress.update(`Opened the saved result from ${formatTimestamp(savedEntry.saved_at)}`);
    // A fresh IRSA run of a registered field is research work: keep it.
    if (!preloadedData && state.live && data.source === 'live') {
      historySave({ kind: 'fresh', target, name: data.common, ra: data.ra, dec: data.dec,
        data, irsaCheck: data.irsa_check || null });
    }
    setText('footer-status',
      `${target} \u00b7 ${data.detections.length} detections \u00b7 ` +
      (snapshotMeta
        ? `snapshot ${formatTimestamp(snapshotMeta.published_at || snapshotMeta.created_at)}`
        : `${data.source === 'scheduled' ? 'published' : 'computed'} ` +
          `${data.generated_at || 'time unknown'}`));
    document.title = `${data.common} -- SPHERExLens`;
    // Refresh the history list after a normal latest-snapshot load.  Historical
    // frames and saved research results keep the list they already have.
    if (!snapshotMeta && !savedEntry && !state.live && !MOCK_V2
        && !state.customFields[target] && !options.suppressHistory) {
      loadSnapshotHistory(target);
    }
    return true;
  } catch (e) {
    setText('map-hint', 'Request failed: ' + e.message);
    progress.update('Request failed: ' + e.message);
    setStatus('ERROR \u00b7 REQUEST FAILED', 'error');
    state.data = null;
    state.offscreen = {};
    draw();
    return false;
  } finally {
    progress.stop();
    hide('map-loader');
    state.pipelineRunning = false;
    $('btn-run').disabled = false;
    renderSnapshotControls();
  }
}

async function openFieldAt(ra, dec, name) {
  if (!Number.isFinite(ra) || !Number.isFinite(dec)
      || ra < 0 || ra > 360 || dec < -90 || dec > 90) {
    setText('field-hint', 'enter RA in [0, 360] and Dec in [-90, 90] degrees');
    return false;
  }
  const params = {
    ra: String(ra),
    dec: String(dec),
    name,
    slots: '4',
  };
  // Coordinates opened before: show the saved result at once.
  if (!researchHistory.loaded) await historyRefresh();
  const saved = historyFindField(ra, dec, params);
  if (saved && await openHistoryEntry(saved)) {
    const when = formatTimestamp(saved.saved_at);
    if (offline.mode === 'offline' || !backendAvailable()) {
      setText('field-hint', `saved result from ${when} (${offlineWhy().toLowerCase()}: opened from History)`);
      return true;
    }
    if (saved.fingerprint) params.known = saved.fingerprint;
    setText('field-hint', `saved result from ${when} shown · checking IRSA for new images…`);
  } else if (offline.mode === 'offline') {
    setText('field-hint',
      `${offlineWhy().toLowerCase()}, and these coordinates are not in History \u2014 new fields need IRSA`);
    return false;
  }
  if (!backendAvailable()) {
    setText('field-hint', 'this needs a backend — set data-api-base to your API URL');
    return false;
  }
  if (!saved) setText('field-hint', 'fetching cutouts from IRSA…');
  const started = performance.now();
  const progress = startActionProgress(saved
    ? 'Checking IRSA for new images at these coordinates'
    : 'Querying IRSA for the custom field');
  if (!saved) show('map-loader');
  try {
    const streamed = await fetchProgressApi('field', null, (event) => {
      progress.update(event.message || 'Custom-field pipeline is running');
      setText('field-hint', event.message || 'Custom-field pipeline is running');
    }, params);
    let data;
    let elapsed = streamed.elapsedSeconds;
    if (!streamed.unsupported) {
      if (!streamed.ok) {
        const problem = streamed.data || {};
        throw new Error(problem.error || problem.detail || `HTTP ${streamed.status}`);
      }
      data = streamed.data;
    } else {
      progress.update('This API has no progress stream; requesting the field endpoint');
      const q = new URLSearchParams(params);
      const resp = await fetch(
        `${LIVE_PIPELINE_API || LIVE_API || `${API_BASE}/api`}/field?${q}`,
        { cache: 'no-store' });
      data = await resp.json();
      if (!resp.ok || data.error) {
        throw new Error(data.error || `HTTP ${resp.status}`);
      }
      elapsed = (performance.now() - started) / 1000;
    }
    if (data.unchanged && saved) {
      const check = data.irsa_check || {};
      const message = `IRSA checked: same ${check.irsa_rows} images as your saved result ` +
        `from ${formatTimestamp(saved.saved_at)}, so nothing was reprocessed`;
      setText('field-hint', message);
      progress.update(message);
      await historyUpdate(saved.id, { irsa_check: check, last_checked_at: check.checked_at });
      return true;
    }
    if (!data.dashboard) throw new Error('Backend response omitted the field dashboard');
    const sel = $('target-select');
    if (![...sel.options].some((option) => option.value === data.target)) {
      const option = document.createElement('option');
      option.value = data.target;
      option.textContent = `${data.common} (${data.ra.toFixed(2)}, ${data.dec.toFixed(2)})`;
      sel.appendChild(option);
    }
    sel.value = data.target;
    state.fieldCoordinates = { ra: data.ra, dec: data.dec, name: data.common };
    state.customFields[data.target] = state.fieldCoordinates;
    progress.update(`${data.common}: IRSA field processing complete`);
    const catalogText = catalogSummary(data.dashboard);
    setText('field-hint',
      `${data.common}: ${data.n_detections} detections, max ${data.max_snr}σ, ` +
      `${data.n_exposures} exposures, ${data.n_epochs} epoch(s)` +
      (catalogText ? ` · ${catalogText}` : '') +
      (saved ? ' · IRSA had new images, so this is a fresh result' : ''));
    historySave({ kind: 'field', target: data.target, name: data.common,
      ra: data.ra, dec: data.dec, params: { slots: params.slots },
      data: data.dashboard, irsaCheck: data.irsa_check || null });
    state.mosaic = null;
    progress.stop();
    await runPipeline(data.dashboard, elapsed);
    return true;
  } catch (e) {
    setText('field-hint', 'failed: ' + e.message);
    progress.update('Custom-field request failed: ' + e.message);
    return false;
  } finally {
    progress.stop();
    hide('map-loader');
  }
}

function refreshSelectedTarget() {
  stopSnapshotPlayback({ resetScale: false });
  const custom = state.customFields[$('target-select').value];
  if (custom) {
    openFieldAt(custom.ra, custom.dec, custom.name);
    return;
  }
  state.fieldCoordinates = null;
  runPipeline();
}

function renderRecoveryResult(data, savedLabel = null) {
  const grid = data.grid || [];
  state.recovery = grid;
  const lines = grid.slice(0, 6).map((g) =>
    `SNR ${String(g.snr).padStart(2)} \u00b7 \u03bc ${String(g.mu).padStart(2)}\u2033/yr \u2192 ${(g.recovery * 100).toFixed(0)}%`);
  const best = grid.reduce((a, b) => (b.recovery > (a ? a.recovery : -1) ? b : a), null);
  if (best) lines.push(`peak ${(best.recovery * 100).toFixed(0)}% at SNR ${best.snr}, \u03bc ${best.mu}\u2033/yr`);
  const saved = data.source === 'scheduled';
  const sourceLabel = saved ? 'Saved scheduled result'
    : (data.source === 'live' ? 'Fresh IRSA run' : 'Cached-cutout run');
  lines.unshift(`${sourceLabel}: ${grid.length}/24 cells` +
    (data.generated_at ? ` \u00b7 ${formatTimestamp(data.generated_at)}` : ''));
  setText('recovery-out', lines.join('\n'));
  $('recovery-out').style.whiteSpace = 'pre-line';
  drawRecoveryChart(grid);
  const complete = grid.length === 24 ? 'complete' : 'incomplete';
  const message = saved
    ? `Saved recovery grid loaded: ${grid.length}/24 cells; generated ${formatTimestamp(data.generated_at)}`
    : `Recovery ${complete}: ${grid.length}/24 cells` +
      (data.generated_at ? `; finished ${formatTimestamp(data.generated_at)}` : '');
  setText('footer-status', `recovery: ${complete} \u00b7 ${grid.length}/24 cells`);
  if (savedLabel) setText('recovery-out', `${savedLabel}\n${$('recovery-out').textContent}`);
  return savedLabel ? `${savedLabel}: ${grid.length}/24 cells` : message;
}

async function runRecovery() {
  if (state.recoveryRunning) return;
  state.recoveryRunning = true;
  $('btn-recovery').disabled = true;
  const target = $('target-select').value;
  const progress = startActionProgress(state.live
    ? 'Starting fresh IRSA retrieval for recovery'
    : (STATIC_MODE && LIVE_API
      ? 'Loading the published recovery result'
      : 'Running recovery from local cached cutouts'));
  setText('recovery-out', state.live
    ? 'fetching fresh IRSA cutouts before recovery\u2026'
    : (STATIC_MODE && LIVE_API
      ? 'loading the saved scheduled recovery result\u2026'
      : 'running injection-recovery from cached cutouts\u2026'));
  setText('footer-status', `recovery: ${target}`);
  try {
    let data;
    if (state.live || (!STATIC_MODE && offline.mode !== 'offline')) {
      const source = state.live ? 'live' : 'cache';
      const streamed = await fetchProgressApi('recovery', target, (event) => {
        progress.update(event.message || 'Recovery is running');
        setText('recovery-out', event.message || 'Recovery is running');
      }, { source });
      if (!streamed.unsupported) {
        if (!streamed.ok) {
          const problem = streamed.data || {};
          throw new Error([problem.error, problem.hint]
            .filter(Boolean).join(' ') || `HTTP ${streamed.status}`);
        }
        data = streamed.data;
      } else {
        progress.update('This API has no progress stream; requesting the live endpoint directly');
        const resp = await fetchApi('recovery', target);
        if (!resp.ok) {
          const problem = await resp.json().catch(() => ({}));
          throw new Error([problem.error, problem.hint]
            .filter(Boolean).join(' ') || `HTTP ${resp.status}`);
        }
        data = await resp.json();
      }
    } else {
      const resp = await fetchApi('recovery', target);
      if (!resp.ok) {
        const problem = await resp.json().catch(() => ({}));
        throw new Error([problem.error, problem.hint]
          .filter(Boolean).join(' ') || `HTTP ${resp.status}`);
      }
      data = await resp.json();
    }
    if (data.error) {
      throw new Error(data.error);
    }
    const message = renderRecoveryResult(data);
    progress.update(message);
    if (state.live && data.source === 'live') {
      historySave({ kind: 'recovery', target, name: `${fieldLabel(target)} recovery`,
        ra: state.data?.ra, dec: state.data?.dec, data });
    }
  } catch (e) {
    setText('recovery-out', 'Recovery failed: ' + e.message);
    progress.update('Recovery failed: ' + e.message);
    setText('footer-status', `recovery failed: ${target}`);
  } finally {
    progress.stop();
    state.recoveryRunning = false;
    $('btn-recovery').disabled = false;
  }
}


// ------------------------------------------------------------- hover tips

function hideTooltip() {
  const tip = $('map-tip');
  if (tip) tip.classList.add('hidden');
}

function showTooltip(lines, mx, my) {
  const tip = $('map-tip');
  if (!tip) return;
  tip.innerHTML = lines.join('<br>');
  tip.style.left = `${mx}px`;
  tip.style.top = `${my}px`;
  tip.classList.remove('hidden');
}

function nearestObject(x, y, maxPx) {
  if (!state.data) return null;
  const objs = state.data.field_objects || [];
  let best = null, bestD = maxPx * maxPx;
  objs.forEach((o) => {
    if (o.in_field === false) return;
    const dx = o.x - x, dy = o.y - y;
    const d = dx * dx + dy * dy;
    if (d < bestD) { bestD = d; best = o; }
  });
  return best;
}

function updateHover(mx, my) {
  if (state.viewMode === 'sky') {
    const hit = state.tileRects.find((r) => mx >= r.x && mx <= r.x + r.w
      && my >= r.y && my <= r.y + r.h);
    state.hoverTile = hit ? hit.tile.target : null;
    if (hit) {
      const t = hit.tile;
      if (t.available === false) {
        showTooltip([
          `<b>${fmt.escape(t.common || t.target)}</b>`,
          `RA ${fmt.ra(t.ra)}  Dec ${fmt.dec(t.dec)}`,
          'comparison unavailable',
          fmt.escape(t.reason || 'no comparable epoch data'),
          'click to view the field status',
        ], mx, my);
        return;
      }
      showTooltip([
        `<b>${fmt.escape(t.common || t.target)}</b>`,
        `RA ${fmt.ra(t.ra)}  Dec ${fmt.dec(t.dec)}`,
        `${t.n_detections} detections · max ${t.max_snr ?? '--'}σ`,
        `known ${t.n_known} · candidate ${t.n_candidate}`,
        `${t.n_exposures} exposures · ${t.n_epochs} epoch(s) · ${t.baseline_days ?? '--'} d`,
        'click to open this field',
      ], mx, my);
    } else {
      hideTooltip();
    }
    return;
  }

  const { x, y } = toImage(mx, my);
  if (!state.data || x < 0 || y < 0 || x > currentNpix() || y > currentNpix()) {
    hideTooltip();
    return;
  }

  const det = nearestDetection(x, y, 7);
  if (det) {
    const cat = categoryOf(det);
    showTooltip([
      `<b>${fmt.escape(det.kind)}</b> · ${cat ? CATEGORY_INFO[cat].label : fmt.escape(det.verdict)}`,
      `SNR ${fmt.num(det.snr, 1)}σ${det.pair ? ' · ' + fmt.escape(det.pair) : ''}`,
      `RA ${fmt.num(det.ra, 5)}  Dec ${fmt.num(det.dec, 5)}`,
      (det.reasons && det.reasons[0]) ? fmt.escape(det.reasons[0])
        : (det.verdict === 'KNOWN'
          ? `matched: ${Object.keys(det.matches || {}).join(', ')}`
          : 'no catalogue match — follow-up'),
    ], mx, my);
    return;
  }

  const obj = nearestObject(x, y, 6);
  if (obj) {
    const extra = [];
    if (obj.vsx_label) extra.push(fmt.escape(obj.vsx_label) + (obj.period_d ? ` · P ${fmt.num(obj.period_d, 1)} d` : ''));
    if (obj.dist_pc) extra.push(`${fmt.num(obj.dist_pc, 1)} pc${obj.nearby ? ' (nearby)' : ''}`);
    if (obj.colour_class) extra.push(`W1−W2 ${obj.w1_w2}: ${fmt.escape(obj.colour_class)}`);
    if (obj.moved_arcsec) extra.push(`moved ${fmt.num(obj.moved_arcsec, 1)}″ since catalogue epoch`);
    if (obj.gaia_variable) extra.push('Gaia flags it VARIABLE');
    showTooltip([
      `<b>${fmt.escape(obj.verdict)}</b>${obj.name ? ' · ' + fmt.escape(obj.name) : ''}`,
      `RA ${fmt.num(obj.ra, 5)}  Dec ${fmt.num(obj.dec, 5)}`,
      obj.mag != null ? `mag ${fmt.num(obj.mag, 2)}` : 'mag —',
      (obj.catalogs || []).length ? `catalogues: ${obj.catalogs.join(', ')}` : 'not in any catalogue',
      ...extra,
      obj.detected ? `SPHEREx sees it change (${fmt.num(obj.snr, 1)}σ)` : 'no change detected',
    ], mx, my);
    return;
  }

  hideTooltip();
}

function nearestDetection(x, y, maxPx) {
  const dets = (state.data && state.data.detections) || [];
  let best = null, bestD = maxPx * maxPx;
  dets.forEach((d) => {
    const pos = d.position || [0, 0];
    const dx = pos[1] - x, dy = pos[0] - y;
    const dd = dx * dx + dy * dy;
    if (dd < bestD) { bestD = dd; best = d; }
  });
  return best;
}

function updateMosaicTile(data) {
  if (!state.mosaic || !state.mosaic.tiles) return;
  if (data.source === 'live' || data.snapshot_meta) return;
  const tile = state.mosaic.tiles.find((t) => t.target === data.target);
  if (!tile) return;
  tile.n_detections = (data.stats || {}).n_detections;
  tile.n_known = (data.stats || {}).n_known;
  tile.n_candidate = (data.stats || {}).n_candidate;
  tile.max_snr = (data.stats || {}).max_snr;
  tile.source = data.source || 'cache';
  if (state.viewMode === 'sky') draw();
}

// ------------------------------------------------------------ all-sky view

state.viewMode = 'field';
state.mosaic = null;
state.tileRects = [];
state.hoverTile = null;
// IRSA-fresh all-sky: tiles fetched one field at a time this session.
state.liveSky = { tiles: {}, runId: 0, active: new Set(), total: 0, lastMessage: '' };
// Fields fetched from IRSA at the same time in the fresh all-sky view. Each
// runs on its own Cloud Run instance; 6 also matches the browser's
// connection limit for one plain-HTTP host (local development).
const LIVE_SKY_PARALLEL = 6;
state.recovery = null;

function skyX(ra, pad, plotW) {
  // sky maps run right-to-left in RA: 0h at the left edge, 12h in the middle
  return pad + ((((360 - ra) % 360) / 360) * plotW);
}

function skyY(dec, pad, plotH) {
  return pad + ((90 - dec) / 180) * plotH;
}

function drawSky(ctx, w, h) {
  const pad = 42;
  const plotW = w - 2 * pad;
  const plotH = h - 2 * pad;
  if (plotW <= 10 || plotH <= 10) return;

  ctx.save();
  ctx.fillStyle = '#0b1220';
  ctx.fillRect(pad, pad, plotW, plotH);
  ctx.font = '11px ui-monospace, Menlo, monospace';
  for (let ra = 0; ra <= 360; ra += 30) {
    const x = skyX(ra, pad, plotW);
    ctx.strokeStyle = 'rgba(148,163,184,.28)';
    ctx.beginPath(); ctx.moveTo(x, pad); ctx.lineTo(x, pad + plotH); ctx.stroke();
    ctx.fillStyle = '#94a3b8';
    ctx.textAlign = 'center';
    ctx.fillText(`${Math.round(ra / 15)}h`, x, pad + plotH + 12);
  }
  for (let dec = -60; dec <= 60; dec += 30) {
    const y = skyY(dec, pad, plotH);
    ctx.strokeStyle = 'rgba(148,163,184,.28)';
    ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(pad + plotW, y); ctx.stroke();
    ctx.fillStyle = '#94a3b8';
    ctx.textAlign = 'right';
    ctx.fillText(`${dec > 0 ? '+' : ''}${dec}\u00b0`, pad - 6, y + 3);
  }
  ctx.strokeStyle = 'rgba(148,163,184,.6)';
  const eq = skyY(0, pad, plotH);
  ctx.beginPath(); ctx.moveTo(pad, eq); ctx.lineTo(pad + plotW, eq); ctx.stroke();
  ctx.restore();

  state.tileRects = [];
  const tiles = ((state.mosaic && state.mosaic.tiles) || []).map((tile) =>
    (state.live && state.liveSky.tiles[tile.target]) || tile);
  ctx.save();
  tiles.forEach((tile) => {
    const cx = skyX(tile.ra, pad, plotW);
    const cy = skyY(tile.dec, pad, plotH);
    const side = 74;
    const x0 = cx - side / 2;
    const y0 = cy - side / 2;
    const thumb = tile.thumb || [];
    const n = thumb.length;

    if (n) {
      const off = document.createElement('canvas');
      off.width = n; off.height = n;
      const octx = off.getContext('2d');
      const img = octx.createImageData(n, n);
      let lo = Infinity, hi = -Infinity;
      thumb.forEach((row) => row.forEach((v) => { if (v < lo) lo = v; if (v > hi) hi = v; }));
      if (!(hi > lo)) hi = lo + 1;
      for (let y = 0; y < n; y++) {
        for (let x = 0; x < n; x++) {
          const v = Math.min(1, Math.max(0, (thumb[y][x] - lo) / (hi - lo)));
          const i = Math.round(v * 255) * 3;
          const o = 4 * ((n - 1 - y) * n + x);
          img.data[o] = RAMPS.snr[i];
          img.data[o + 1] = RAMPS.snr[i + 1];
          img.data[o + 2] = RAMPS.snr[i + 2];
          img.data[o + 3] = 255;
        }
      }
      octx.putImageData(img, 0, 0);
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(off, x0, y0, side, side);
    } else {
      ctx.fillStyle = '#172234';
      ctx.fillRect(x0, y0, side, side);
      ctx.save();
      ctx.fillStyle = '#94a3b8';
      ctx.font = '11px ui-monospace, Menlo, monospace';
      ctx.textAlign = 'center';
      ctx.fillText('NO DATA', cx, cy + 3);
      ctx.restore();
    }

    const freshness = !state.live ? null
      : (tile.source === 'live' ? 'fresh'
        : (state.liveSky.active.has(tile.target) ? 'fetching' : 'waiting'));
    if (freshness === 'waiting' || freshness === 'fetching') {
      ctx.fillStyle = 'rgba(11,18,32,.66)';
      ctx.fillRect(x0, y0, side, side);
      ctx.save();
      ctx.fillStyle = freshness === 'fetching' ? '#7dd3fc' : '#94a3b8';
      ctx.font = '10px ui-monospace, Menlo, monospace';
      ctx.textAlign = 'center';
      ctx.fillText(freshness === 'fetching' ? 'FETCHING' : 'WAITING', cx, cy + 3);
      ctx.restore();
    }

    const hovered = state.hoverTile === tile.target;
    ctx.strokeStyle = hovered ? '#ffffff'
      : (freshness === 'fetching' ? '#7dd3fc'
        : (tile.available === false ? '#fbbf24'
          : (freshness === 'fresh' ? '#34d399'
            : (tile.n_detections > 0 ? '#7dd3fc' : '#475569'))));
    ctx.lineWidth = hovered || freshness === 'fetching' ? 2 : 1;
    if (tile.available === false) ctx.setLineDash([3, 3]);
    ctx.strokeRect(x0 + 0.5, y0 + 0.5, side, side);
    ctx.setLineDash([]);

    ctx.textAlign = 'center';
    ctx.fillStyle = hovered ? '#ffffff' : '#cbd5e1';
    ctx.font = '12px ui-monospace, Menlo, monospace';
    ctx.fillText(tile.common || tile.target, cx, y0 + side + 14);
    ctx.fillStyle = '#64748b';
    ctx.font = '11px ui-monospace, Menlo, monospace';
    ctx.fillText(freshness === 'waiting' || freshness === 'fetching'
      ? 'snapshot \u00b7 IRSA pending'
      : (tile.available === false
        ? 'reason on hover'
        : `${tile.n_detections} det \u00b7 max ${tile.max_snr ?? '--'}\u03c3`),
                 cx, y0 + side + 28);

    state.tileRects.push({ x: x0, y: y0, w: side, h: side, tile });
  });
  ctx.restore();

  ctx.save();
  ctx.fillStyle = '#64748b';
  ctx.font = '11px ui-monospace, Menlo, monospace';
  ctx.textAlign = 'left';
  ctx.fillText(
    `${state.live ? liveSkyLabel(tiles) : mosaicCheckedLabel()}` +
    `${tiles.filter((tile) => tile.available !== false).length}/${tiles.length} field(s) comparable \u00b7 ` +
        `${(state.mosaic && state.mosaic.sky_area_deg2) || 0} deg2 mapped \u00b7 ` +
        'tiles magnified (a 9\u2032 field is 0.3 px at true scale)',
    pad, pad - 14);
  ctx.restore();
}

async function loadMosaic(refresh) {
  // The published Cloud snapshot is always loaded first so the sky is never
  // empty. In IRSA-fresh mode each field is then fetched from IRSA one at a
  // time and its tile replaced as it arrives (runLiveSky).
  stopLiveSky();
  if (refresh && state.live) state.liveSky.tiles = {};
  setText('map-hint', STATIC_MODE && LIVE_API
    ? 'loading published all-sky snapshot\u2026'
    : (refresh ? 'refreshing all-sky tiles\u2026' : 'loading all-sky tiles\u2026'));
  show('map-loader');
  try {
    const query = refresh ? '?refresh=1' : '';
    const resp = STATIC_MODE && LIVE_API
      ? await fetch(remoteUrl('mosaic') + query, { cache: 'no-store' })
      : await fetch(apiUrl('mosaic') + query);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    state.mosaic = await resp.json();
    setText('map-hint', allSkyHint());
    if (state.live && state.viewMode === 'sky') runLiveSky();
  } catch (e) {
    setText('map-hint', 'all-sky tiles unavailable: ' + e.message);
  } finally {
    hide('map-loader');
  }
}

function switchView(view) {
  state.viewMode = view;
  document.body.classList.toggle('sky-mode', view === 'sky');
  document.querySelectorAll('#view-switch .seg').forEach((b) =>
    b.classList.toggle('active', b.dataset.view === view));
  if (view === 'sky') {
    stopSnapshotPlayback();
    if (!state.mosaic) loadMosaic(false).then(draw);
    else if (state.live) runLiveSky();
    setText('map-hint', allSkyHint());
  } else {
    // The live service runs one IRSA request at a time; free it for the field.
    stopLiveSky();
  }
  if (view !== 'sky' && state.data) {
    setText('map-hint',
      `${state.data.common} \u00b7 ${state.data.n_epoch1}+${state.data.n_epoch2} exposures \u00b7 ` +
      `${state.data.map.fov_arcmin}' field`);
  }
  draw();
}

function stopLiveSky() {
  // In-flight requests finish and keep their tiles; no new field is started.
  state.liveSky.runId += 1;
  state.liveSky.active = new Set();
  state.liveSky.lastMessage = '';
}

function liveSkyLabel(tiles) {
  const fresh = tiles.filter((tile) => tile.source === 'live').length;
  return `IRSA fresh ${fresh}/${tiles.length}` +
    (state.liveSky.active.size ? ` \u00b7 ${state.liveSky.active.size} fetching` : '') +
    ' \u00b7 ';
}

async function runLiveSky() {
  if (state.liveSky.active.size) return;       // a run is already active
  const runId = ++state.liveSky.runId;
  const snapshotTiles = (state.mosaic && state.mosaic.tiles) || [];
  const names = snapshotTiles.map((tile) => tile.target);
  const queue = names.filter((name) => !state.liveSky.tiles[name]);
  state.liveSky.total = names.length;
  const cancelled = () => runId !== state.liveSky.runId
    || !state.live || state.viewMode !== 'sky';
  let unsupported = false;

  const fetchOne = async (name) => {
    let tile;
    try {
      const result = await fetchProgressApi('mosaic/tile', name, (event) => {
        if (runId === state.liveSky.runId) {
          state.liveSky.lastMessage = `${fieldLabel(name)}: ${event.message || 'running'}`;
          setText('map-hint', liveSkyProgressText());
        }
      });
      if (result.unsupported) {
        unsupported = true;
        return;
      }
      tile = result.ok ? result.data : null;
      if (!tile) {
        const problem = result.data || {};
        throw new Error(problem.error || problem.detail || `HTTP ${result.status}`);
      }
    } catch (error) {
      const base = snapshotTiles.find((t) => t.target === name) || { target: name };
      tile = { ...base, thumb: [], available: false, source: 'live',
               reason: `IRSA fetch failed: ${error.message}` };
    }
    // A finished fetch is fresh even if the run was cancelled meanwhile.
    state.liveSky.tiles[name] = tile;
  };

  // A small pool of workers: each takes the next field from the queue, so up
  // to LIVE_SKY_PARALLEL fields are being fetched from IRSA at any moment.
  const worker = async () => {
    while (queue.length && !cancelled() && !unsupported) {
      const name = queue.shift();
      state.liveSky.active.add(name);
      setText('map-hint', liveSkyProgressText());
      draw();
      try {
        await fetchOne(name);
      } finally {
        state.liveSky.active.delete(name);
      }
      if (runId === state.liveSky.runId) {
        setText('map-hint', liveSkyProgressText());
        draw();
      }
    }
  };
  await Promise.all(Array.from(
    { length: Math.min(LIVE_SKY_PARALLEL, queue.length) }, worker));

  if (runId !== state.liveSky.runId) return;
  setText('map-hint', unsupported
    ? 'This API cannot fetch all-sky tiles from IRSA; showing the 12-hour Cloud snapshot.'
    : allSkyHint());
  draw();
}

function liveSkyProgressText() {
  const total = state.liveSky.total || 18;
  const done = Object.keys(state.liveSky.tiles).length;
  const active = [...state.liveSky.active].map(fieldLabel);
  const fetching = active.length
    ? ` \u00b7 fetching ${active.slice(0, 3).join(', ')}${active.length > 3 ? ` +${active.length - 3}` : ''}`
    : '';
  const last = state.liveSky.lastMessage ? ` \u00b7 ${state.liveSky.lastMessage}` : '';
  return `IRSA fresh ${done}/${total} done${fetching}${last}`;
}

function mosaicCheckedLabel() {
  const checked = state.mosaic && state.mosaic.generated_at;
  if (!checked) return '';
  const age = formatAge(Date.now() - Date.parse(checked));
  return `IRSA checked ${age || formatTimestamp(checked)} · `;
}

function allSkyHint() {
  if (state.live && state.mosaic) {
    const total = (state.mosaic.tiles || []).length;
    const fresh = Object.keys(state.liveSky.tiles).length;
    return fresh >= total
      ? `All ${total} fields fetched fresh from IRSA this session \u00b7 click a tile to open that field \u00b7 Tiles button fetches them again`
      : `Fetching each field fresh from IRSA (${fresh}/${total} done); dimmed tiles still show the 12-hour Cloud snapshot \u00b7 ${LIVE_SKY_PARALLEL} fields at a time`;
  }
  const counts = state.mosaic
    ? `${state.mosaic.n_available ?? state.mosaic.n_fields} of ${state.mosaic.n_fields} fields comparable`
    : 'loading coverage tiles';
  const source = state.mosaic && state.mosaic.tiles
    && state.mosaic.tiles.some((tile) => tile.source === 'scheduled')
    ? 'scheduled cloud snapshot'
    : 'local cache';
  return `${counts} \u00b7 coverage tiles use ${source} \u00b7 click a tile to open that field`;
}

// ===================================================================== v2 UI
//
// Everything below reads the optional fields of spherexlens/CONTRACT.md. Each
// one may be missing (older snapshots, a subsystem that failed, the static
// build), so every renderer checks before it draws and hides what it lacks.

function prefersReducedMotion() {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch { return false; }
}

// localStorage can throw (private windows, blocked storage): never let it break the page.
function lsGet(key, fallback = null) {
  try {
    const v = window.localStorage.getItem(key);
    return v === null ? fallback : v;
  } catch { return fallback; }
}
function lsSet(key, value) {
  try { window.localStorage.setItem(key, value); } catch { /* storage unavailable */ }
}

function chartLabel(id, text) {
  const c = $(id);
  if (!c) return;
  c.setAttribute('role', 'img');
  c.setAttribute('aria-label', text);
}

function sedSummary(sed) {
  const cat = (sed && sed.points) || [];
  const sph = ((sed && sed.spherex) || []).filter((p) => p.mjy > 0);
  const lam = cat.concat(sph).map((p) => p.lam_um).filter((v) => v > 0);
  return `Spectral energy distribution: ${cat.length} catalogue points (Gaia, AllWISE) and ` +
    `${sph.length} SPHEREx points` +
    (lam.length ? ` between ${Math.min(...lam).toFixed(2)} and ${Math.max(...lam).toFixed(2)} microns.` : '.');
}

const pct = (v, d = 0) => (Number.isFinite(v) ? `${(v * 100).toFixed(d)}%` : '—');

// ------------------------------------------------------------- modal sheets
// The vetting card, Accuracy page and Credits are modal: focus moves in, Tab
// stays inside, Esc / the close button / the backdrop close it, and focus
// returns to whatever opened it.

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), '
  + 'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusables(el) {
  return [...el.querySelectorAll(FOCUSABLE)]
    .filter((n) => n.offsetParent !== null || n === document.activeElement);
}

function trapFocus(el, e) {
  if (e.key !== 'Tab') return;
  const items = focusables(el);
  if (!items.length) { e.preventDefault(); return; }
  const first = items[0], last = items[items.length - 1];
  if (e.shiftKey && (document.activeElement === first || !el.contains(document.activeElement))) {
    e.preventDefault(); last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault(); first.focus();
  }
}

function openSheet(id, opener) {
  const el = $(id);
  if (!el) return;
  if (el.classList.contains('hidden')) {
    el._opener = opener || document.activeElement;
    el.classList.remove('hidden');
  }
  document.body.classList.add('sheet-open');
  const heading = el.querySelector('h2');
  if (heading) {
    heading.tabIndex = -1;
    heading.focus({ preventScroll: true });
  }
}

function closeSheet(id) {
  const el = $(id);
  if (!el || el.classList.contains('hidden')) return;
  el.classList.add('hidden');
  if (!document.querySelector('.sheet:not(.hidden)')) document.body.classList.remove('sheet-open');
  if (id === 'vet') state.vetIndex = -1;
  const opener = el._opener;
  el._opener = null;
  if (opener && opener.isConnected && opener.focus) opener.focus();
}

function attachSheets() {
  document.querySelectorAll('.sheet').forEach((el) => {
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        closeSheet(el.id);
        return;
      }
      trapFocus(el, e);
    });
    el.addEventListener('click', (e) => { if (e.target === el) closeSheet(el.id); });
    el.querySelectorAll('[data-sheet-close]').forEach((btn) =>
      btn.addEventListener('click', () => closeSheet(el.id)));
  });
}

// ------------------------------------------------------- public / expert mode

const MODE_KEY = 'spx-ui-mode';

function applyMode(mode) {
  const m = mode === 'expert' ? 'expert' : 'public';
  document.body.dataset.uimode = m;
  document.querySelectorAll('#mode-switch .seg').forEach((b) => {
    const on = b.dataset.mode === m;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', String(on));
  });
  lsSet(MODE_KEY, m);
  if (m === 'expert') {
    const win = $('win-classify');
    if (win) closeToolWindow(win);
  }
  if (state.vetIndex >= 0) renderVetCard(state.vetIndex);
}

// ------------------------------------------------------------ API endpoints

// Live-only endpoints (votes, queue, cone, candidate tables): the server, or a
// static build with a live API behind it. null = "needs the live backend".
function liveBackendBase() {
  if (!STATIC_MODE) return `${API_BASE}/api`;
  return LIVE_API || null;
}

const MOCK_FILES = {
  metrics: 'dev/mock_metrics.json',
  model: 'dev/mock_model.json',
  'votes/queue': 'dev/mock_queue.json',
};

// GET a v2 JSON endpoint. Static builds read the exported api/<name>.json
// (after trying a live API when one is configured). Returns {ok, status, data, url}.
async function fetchV2Json(name, query = '') {
  const attempts = [];
  if (MOCK_V2 && MOCK_FILES[name]) attempts.push(MOCK_FILES[name]);
  else if (!STATIC_MODE) attempts.push(`${API_BASE}/api/${name}${query}`);
  else {
    if (LIVE_API) attempts.push(`${LIVE_API}/${name}${query}`);
    attempts.push(`${document.body.dataset.apiDir || 'api'}/${name.replace(/\//g, '-')}.json`);
  }
  let last = { ok: false, status: 0, data: null, url: attempts[0] };
  for (const url of attempts) {
    try {
      // A stuck server must not leave a panel on "Loading..." forever.
      const ctl = typeof AbortController === 'function' ? new AbortController() : null;
      const timer = ctl ? setTimeout(() => ctl.abort(), 20000) : null;
      let resp;
      try {
        resp = await fetch(url, { cache: 'no-store', signal: ctl ? ctl.signal : undefined });
      } finally {
        if (timer) clearTimeout(timer);
      }
      if (!resp.ok) { last = { ok: false, status: resp.status, data: null, url }; continue; }
      return { ok: true, status: resp.status, data: await resp.json(), url };
    } catch (err) {
      const timedOut = err && err.name === 'AbortError';
      last = { ok: false, status: 0, data: null, url,
               error: timedOut ? 'the server did not answer within 20 s' : err.message };
    }
  }
  return last;
}

// --------------------------------------------------------------- coverage

const ARCHIVE_NAMES = {
  spherex: 'SPHEREx', neowise: 'NEOWISE', allwise: 'AllWISE', wise: 'WISE', gaia: 'Gaia',
  neossat: 'NEOSSat', jpl_sb_ident: 'JPL', jpl: 'JPL', ssois: 'SSOIS', vsx: 'VSX',
  simbad: 'SIMBAD', unwise: 'unWISE', twomass: '2MASS', '2mass': '2MASS',
  panstarrs: 'Pan-STARRS', ps1: 'Pan-STARRS', skymapper: 'SkyMapper', ztf: 'ZTF',
};
// Archives that are queried per detection/designation, so a field-level count
// of 0 means "not checked here yet", not "no data".
const COVERAGE_ON_DEMAND = new Set(['jpl', 'jpl_sb_ident', 'ssois', 'simbad']);

function coverageState(c) {
  if (!c) return 'unknown';
  const available = c.available;
  const count = c.count;
  const numeric = Number.isFinite(count);
  if (c.pending) return 'unknown';          // still being checked
  if (available === false && c.error) return 'unknown';   // archive failed, not "empty"
  if (available !== true && available !== false) return 'unknown';
  if (COVERAGE_ON_DEMAND.has(String(c.archive || '')) && (!numeric || count === 0)) {
    return 'unknown';
  }
  // available=true means the archive answered, not that this field has rows.
  return available && (!numeric || count > 0) ? 'yes' : 'no';
}

function coverageReason(c) {
  if (!c) return 'not checked for this field';
  if (c.pending) return 'still checking this archive';
  if (c.available === false && c.error) return 'check unavailable';
  if (COVERAGE_ON_DEMAND.has(String(c.archive || '')) && !(Number.isFinite(c.count) && c.count > 0)) {
    return 'queried per detection/designation';
  }
  const state = coverageState(c);
  if (state === 'yes') return 'has data for this field';
  if (state === 'no' && c.archive === 'neowise') {
    return 'no NEOWISE visits within 10″ of the field centre';
  }
  return state === 'no' ? 'no data for this field' : 'not checked for this field';
}

function renderCoverage(data) {
  const row = $('coverage-row');
  const list = $('coverage-chips');
  if (!row || !list) return;
  const cov = Array.isArray(data && data.coverage) ? data.coverage : [];
  row.hidden = !cov.length;
  if (!cov.length) { list.innerHTML = ''; return; }
  list.innerHTML = cov.map((c) => {
    const name = ARCHIVE_NAMES[c.archive] || String(c.archive || '?').toUpperCase();
    const state = coverageState(c);
    const ok = state === 'yes';
    const unknown = state === 'unknown';
    const detail = c.detail || (Number.isFinite(c.count) && ok ? String(c.count) : '');
    const tip = `${name}: ${coverageReason(c)}` +
      (c.error ? ` — ${c.error}` : '') +
      (c.detail ? ` — ${c.detail}` : '') + (Number.isFinite(c.count) ? ` (${c.count})` : '');
    return `<li class="cov-chip" data-state="${state}" title="${fmt.escape(tip)}">` +
      `<span class="cov-name">${fmt.escape(name)}</span>` +
      `<span class="cov-mark" aria-hidden="true">${unknown ? '?' : ok ? '✓' : '✗'}</span>` +
      `<span class="sr-only">${unknown ? 'not checked' : ok ? 'available' : 'not available'}</span>` +
      (detail && ok ? `<span class="cov-detail">${fmt.escape(detail)}</span>` : '') +
      (c.detail && !ok ? `<span class="sr-only">${fmt.escape(c.detail)}</span>` : '') +
      '</li>';
  }).join('');
}

// ---------------------------------------------------- expert data access

function renderExpertAccess(data) {
  const dl = $('prov-pipeline');
  const list = $('download-list');
  const note = $('cone-note');
  if (!dl || !list) return;
  const p = data.pipeline || {};
  const rows = [];
  const add = (k, v) => { if (v !== null && v !== undefined && v !== '') rows.push([k, v]); };
  add('Pipeline version', p.pipeline_version);
  add('Config hash', p.config_hash);
  add('Model version', p.model_version || (p.pipeline_version ? 'no ML model in this run' : null));
  add('Generated', data.generated_at ? formatTimestamp(data.generated_at) : null);
  const enr = data.enrichment && typeof data.enrichment === 'object' ? Object.entries(data.enrichment) : [];
  if (enr.length) {
    add('Enrichment', enr.map(([name, e]) => `${name} ${e && e.status ? e.status : '?'}` +
      (e && Number.isFinite(e.seconds) ? ` (${e.seconds.toFixed(1)} s)` : '') +
      (e && e.error ? ` — ${e.error}` : '')).join(' · '));
  }
  const eq = Array.isArray(data.exposure_quality) ? data.exposure_quality : [];
  if (eq.length) {
    const dropped = eq.filter((x) => x.kept === false);
    add('Exposure quality', `${eq.length - dropped.length} of ${eq.length} exposures kept` +
      (dropped.length ? `; dropped: ${dropped.map((x) => `${x.obs_id} (${(x.reasons || []).join(', ')})`).join('; ')}` : ''));
  }
  const psfs = (data.epochs || []).filter((e) => e.psf && Number.isFinite(e.psf.fwhm_arcsec));
  if (psfs.length) {
    add('PSF per epoch', psfs.map((e) => `E${e.epoch} ${fmt.num(e.psf.fwhm_arcsec, 2)}″ (${e.psf.source || '?'})`).join(' · '));
  }
  dl.innerHTML = rows.length
    ? rows.map(([k, v]) => `<div><dt>${fmt.escape(k)}</dt><dd class="mono">${fmt.escape(v)}</dd></div>`).join('')
    : '<div><dt>Provenance</dt><dd>This snapshot predates pipeline version stamps.</dd></div>';
  if (eq.length && eq.some((x) => x.epoch != null || x.psf_fwhm_arcsec != null || x.band)) {
    const yes = (v) => (v === true ? 'yes' : v === false ? 'no' : '—');
    dl.insertAdjacentHTML('beforeend', '<div class="eq-wrap"><dt>Exposures</dt><dd><div class="table-wrap">' +
      '<table class="sources-table eq-table"><caption class="sr-only">Exposure quality</caption><thead><tr>' +
      ['Observation', 'Epoch', 'Band', 'Score', 'Kept', 'PSF', 'Zodi sub.', 'Reasons']
        .map((h) => `<th scope="col">${h}</th>`).join('') + '</tr></thead><tbody>' +
      eq.map((x) => `<tr><td>${fmt.escape(x.obs_id || '--')}</td><td>${fmt.escape(x.epoch ?? '--')}</td>` +
        `<td>${fmt.escape(x.band || '--')}</td><td>${Number.isFinite(x.score) ? fmt.num(x.score, 2) : '--'}</td>` +
        `<td>${yes(x.kept)}</td><td>${Number.isFinite(x.psf_fwhm_arcsec) ? `${fmt.num(x.psf_fwhm_arcsec, 2)}″${x.psf_source ? ` (${fmt.escape(x.psf_source)})` : ''}` : '--'}</td>` +
        `<td>${yes(x.zodi_subtracted)}</td><td>${fmt.escape((x.reasons || []).join(', '))}</td></tr>`).join('') +
      '</tbody></table></div></dd></div>');
  }

  const base = liveBackendBase();
  const target = data.target || state.target || '';
  const needs = '<span class="needs-backend">needs the live backend</span>';
  const item = (label, href, desc, live = true) => {
    if (live && !base) return `<li><span class="dl-off">${label}</span> ${needs}<span class="dl-desc">${desc}</span></li>`;
    return `<li><a href="${fmt.escape(href)}" download>${label}</a><span class="dl-desc">${desc}</span></li>`;
  };
  const fieldJson = MOCK_V2 ? 'dev/mock_v2.json' : apiUrl('dashboard', target);
  list.innerHTML = [
    item('All candidates (CSV)', `${base}/candidates.csv`, 'every triaged detection, one row each', true),
    item('All candidates (VOTable)', `${base}/candidates.votable`, 'for TOPCAT / Aladin / astropy', true),
    item(`This field (JSON)`, fieldJson, `the full dashboard payload for ${fmt.escape(data.common || target)}`, false),
  ].join('');
  if (note) {
    const sr = 0.1;
    const cone = base ? `${base}/cone?RA=${fmt.num(data.ra, 4)}&DEC=${fmt.num(data.dec, 4)}&SR=${sr}` : null;
    note.innerHTML = 'IVOA Simple Cone Search: ' + (cone
      ? `<a href="${fmt.escape(cone)}">${fmt.escape(cone)}</a> — give TOPCAT (VO → Cone Search) or Aladin the base URL <code>${fmt.escape(base)}/cone?</code>; RA, DEC and SR are in degrees.`
      : `<code>/api/cone?RA=&amp;DEC=&amp;SR=</code> ${needs}.`);
  }
}

function describeMap(data) {
  const canvas = $('canvas-map');
  if (!canvas || !data) return;
  const dets = data.detections || [];
  const counts = {};
  dets.forEach((d) => {
    const cat = categoryOf(d);
    const label = cat ? CATEGORY_INFO[cat].label.toLowerCase() : String(d.verdict || 'detection').toLowerCase();
    counts[label] = (counts[label] || 0) + 1;
  });
  const parts = Object.entries(counts).map(([k, n]) => `${n} ${k}`);
  canvas.setAttribute('aria-label',
    `Field map of ${data.common || data.target}, ${data.map ? data.map.fov_arcmin : 9} arcminutes across, ` +
    `${dets.length} detection${dets.length === 1 ? '' : 's'}${parts.length ? `: ${parts.join(', ')}` : ''}.`);
}

// Two small score chips under the verdict badge (expert) -- ML + evidence.
function verdictScoreStrip(det) {
  const bits = [];
  if (det.ml && Number.isFinite(det.ml.real_bogus)) {
    bits.push(`<span class="score-chip" title="ML real/bogus score (${fmt.escape(det.ml.model_version || 'model')})">ML ${pct(det.ml.real_bogus)}${det.ml.uncertain ? ' · unsure' : ''}</span>`);
  }
  if (det.evidence && Number.isFinite(det.evidence.score)) {
    bits.push(`<span class="score-chip" title="Evidence score: all checks combined">Evidence ${Math.round(det.evidence.score * 100)}</span>`);
  }
  const a = det.asteroid;
  if (a && a.status === 'unavailable') bits.push('<span class="score-chip score-warn">asteroid check unavailable</span>');
  if (a && a.status === 'match' && a.match) bits.push(`<span class="score-chip score-ast">${fmt.escape(a.match.designation)}</span>`);
  return bits.length ? `<div class="score-strip expert-only">${bits.join('')}</div>` : '';
}

// ------------------------------------------------------------ vetting card

state.vetIndex = -1;

function openVetCard(index, opener) {
  if (!state.data || !state.data.detections[index]) return;
  state.vetIndex = index;
  renderVetCard(index);
  openSheet('vet', opener);
}

function refreshOpenVetCard() {
  if (state.vetIndex < 0) return;
  const dets = (state.data && state.data.detections) || [];
  if (!dets.length) { closeSheet('vet'); return; }
  state.vetIndex = Math.min(state.vetIndex, dets.length - 1);
  renderVetCard(state.vetIndex);
}

function cutoutRange(values, symmetric) {
  if (!values.length) return [0, 1];
  if (symmetric) {
    const amp = Math.max(Math.abs(percentile([values], 1)), Math.abs(percentile([values], 99.5)), 1e-9);
    return [-amp, amp];
  }
  const lo = percentile([values], 1), hi = percentile([values], 99.5);
  return [lo, hi > lo ? hi : lo + 1e-9];
}

// Draw a (2*half+1)^2 crop of `arr` centred on image pixel (row, col).
// arrows: [{dx, dy, colour}] in image pixels with y pointing north (up).
function drawCutout(canvas, arr, row, col, half, lutName, symmetric, arrows = []) {
  const n = 2 * half + 1;
  const cell = 6;
  canvas.width = n * cell;
  canvas.height = n * cell;
  const ctx = canvas.getContext('2d');
  const at = (y, x) => {
    const r = arr && arr[y];
    const v = r ? r[x] : null;
    return Number.isFinite(v) ? v : null;
  };
  const values = [];
  for (let y = row - half; y <= row + half; y++) {
    for (let x = col - half; x <= col + half; x++) {
      const v = at(y, x);
      if (v !== null) values.push(v);
    }
  }
  const [lo, hi] = cutoutRange(values, symmetric);
  const lut = RAMPS[lutName] || RAMPS.gray;
  for (let j = 0; j < n; j++) {
    const y = row + half - j;                    // north up, like the map
    for (let i = 0; i < n; i++) {
      const v = at(y, col - half + i);
      if (v === null) {
        ctx.fillStyle = '#2a2f37';
      } else {
        const t = Math.min(1, Math.max(0, (v - lo) / (hi - lo)));
        const k = Math.round(t * 255) * 3;
        ctx.fillStyle = `rgb(${lut[k]},${lut[k + 1]},${lut[k + 2]})`;
      }
      ctx.fillRect(i * cell, j * cell, cell, cell);
    }
  }
  const c = (half + 0.5) * cell;
  ctx.lineWidth = 2;
  [['rgba(0,0,0,.75)', 3], ['#ffffff', 1.4]].forEach(([colour, w]) => {
    ctx.strokeStyle = colour; ctx.lineWidth = w;
    [[0, -1], [0, 1], [-1, 0], [1, 0]].forEach(([ax, ay]) => {
      ctx.beginPath();
      ctx.moveTo(c + ax * 9, c + ay * 9);
      ctx.lineTo(c + ax * 18, c + ay * 18);
      ctx.stroke();
    });
  });
  arrows.forEach((a) => {
    const x0 = c + (a.fromX || 0) * cell, y0 = c - (a.fromY || 0) * cell;
    const x1 = x0 + a.dx * cell, y1 = y0 - a.dy * cell;
    const ang = Math.atan2(y1 - y0, x1 - x0);
    [['rgba(0,0,0,.8)', 4], [a.colour, 2]].forEach(([colour, w]) => {
      ctx.strokeStyle = colour; ctx.fillStyle = colour; ctx.lineWidth = w;
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x1 - 9 * Math.cos(ang - 0.45), y1 - 9 * Math.sin(ang - 0.45));
      ctx.lineTo(x1 - 9 * Math.cos(ang + 0.45), y1 - 9 * Math.sin(ang + 0.45));
      ctx.closePath(); ctx.fill();
    });
  });
  ctx.fillStyle = 'rgba(0,0,0,.6)';
  ctx.fillRect(0, canvas.height - 15, 36, 15);
  ctx.fillStyle = '#ffffff';
  ctx.font = '10px ui-monospace, Menlo, monospace';
  ctx.fillText('N↑ E←', 3, canvas.height - 4);
}

function compass(paDeg) {
  const names = ['north', 'north-east', 'east', 'south-east', 'south', 'south-west', 'west', 'north-west'];
  return names[Math.round((((paDeg % 360) + 360) % 360) / 45) % 8];
}

// Gaia proper motion of the matched star, if any: {mu (″/yr), dirX, dirY, pa}.
function gaiaMotion(det) {
  const g = det.matches && det.matches.gaia && det.matches.gaia.source;
  if (!g || !Number.isFinite(g.pm_ra) || !Number.isFinite(g.pm_de)) return null;
  const mu = Math.hypot(g.pm_ra, g.pm_de) / 1000;
  if (!(mu > 0)) return null;
  // pm_ra is toward the east (+RA), which is -x on the map; pm_de is +y.
  const norm = Math.hypot(g.pm_ra, g.pm_de);
  return {
    mu, dirX: -g.pm_ra / norm, dirY: g.pm_de / norm,
    pa: (Math.atan2(g.pm_ra, g.pm_de) * 180) / Math.PI,
  };
}

function nearTarget(det) {
  const tp = state.data && state.data.target_position;
  const pos = det.position;
  if (!tp || !Array.isArray(pos) || !Number.isFinite(tp.x)) return false;
  return Math.hypot(tp.x - pos[1], tp.y - pos[0]) <= 4;
}

const NEOSSAT_TEXT = {
  none: ['No NEOSSat pictures of this spot near these dates.', 'muted'],
  frames: ['NEOSSat has pictures of this spot; they have not been checked yet.', 'info'],
  confirmed: ['Seen in NEOSSat too ✓ — an independent telescope confirms it.', 'ok'],
  not_seen: ['NEOSSat looked but did not see it (it may be too faint for NEOSSat).', 'warn'],
  unavailable: ['The NEOSSat archive could not be reached for this run.', 'warn'],
};

const EVIDENCE_NAMES = {
  ml: 'ML score', motion_consistency: 'Motion agrees', asteroid_check: 'Asteroid check',
  neossat: 'NEOSSat', neowise: 'NEOWISE history', catalogue: 'Catalogue match',
};

function vetSection(title, body, extraClass = '') {
  return `<section class="vet-sec ${extraClass}"><h3>${title}</h3>${body}</section>`;
}

function meter(value, label) {
  const v = Math.max(0, Math.min(1, value));
  return `<div class="meter" role="meter" aria-valuemin="0" aria-valuemax="100" ` +
    `aria-valuenow="${Math.round(v * 100)}" aria-label="${fmt.escape(label)}">` +
    `<span class="meter-fill" style="width:${(v * 100).toFixed(1)}%"></span></div>`;
}

function extLink(href, label, cls = 'ext-link') {
  return `<a class="${cls}" href="${fmt.escape(href)}" target="_blank" rel="noopener noreferrer">` +
    `${label}<span class="sr-only"> (opens in a new tab)</span> <span aria-hidden="true">↗</span></a>`;
}

function asteroidBlock(det) {
  const a = det.asteroid;
  if (!a) {
    return '<p class="vet-muted">The asteroid check was not run for this snapshot.</p>';
  }
  if (a.status === 'unavailable') {
    return '<div class="vet-caution" role="note"><b>⚠ Asteroid check unavailable.</b> ' +
      'We could not ask the asteroid services whether a known asteroid was here, so this could ' +
      'still be a known asteroid. Treat it with caution.' +
      (a.error ? `<span class="vet-muted"> (${fmt.escape(a.error)})</span>` : '') + '</div>';
  }
  const checked = (a.checked || []).map((c) => ({ jpl_sb_ident: 'JPL SB Identification', ssois: 'CADC SSOIS' }[c] || c));
  const via = checked.length ? `<p class="vet-muted">Checked with ${fmt.escape(checked.join(' and '))}` +
    (Number.isFinite(a.n_in_field) ? `; ${a.n_in_field} known asteroid${a.n_in_field === 1 ? '' : 's'} in the field at the time.` : '.') + '</p>' : '';
  if (a.status === 'no_match') {
    return '<p><span class="vf-tag vf-ok">NO MATCH</span> No known asteroid was at this spot at that time.</p>' + via;
  }
  const m = a.match || {};
  const neo = m.neo ? ' <span class="badge badge-neo" title="Near-Earth object">NEO</span>' : '';
  const ssois = m.ssois_agrees === true ? '<span class="vf-tag vf-ok">SSOIS agrees</span>'
    : m.ssois_agrees === false ? '<span class="vf-tag vf-warn">SSOIS disagrees</span>'
      : '<span class="vf-tag vf-mute">SSOIS not checked</span>';
  const facts = [];
  if (m.orbit_class) facts.push(`orbit class ${fmt.escape(m.orbit_class)}`);
  if (Number.isFinite(m.sep_arcsec)) facts.push(`${fmt.num(m.sep_arcsec, 1)}″ away`);
  if (m.epoch) facts.push(`in epoch ${fmt.escape(m.epoch)}`);
  if (Number.isFinite(m.v_mag)) facts.push(`V ${fmt.num(m.v_mag, 1)}`);
  if (Number.isFinite(m.h_mag)) facts.push(`H ${fmt.num(m.h_mag, 1)}`);
  if (Number.isFinite(m.pos_unc_arcsec)) facts.push(`position ±${fmt.num(m.pos_unc_arcsec, 1)}″`);
  const links = [];
  if (m.sbdb_url) links.push(extLink(m.sbdb_url, 'JPL Small-Body Database'));
  if (m.ssois_url) links.push(extLink(m.ssois_url, 'CADC SSOIS'));
  return `<p class="ast-match"><span class="vf-tag vf-info">MATCH</span> <b>${fmt.escape(m.designation || 'known asteroid')}</b>${neo} ${ssois}</p>` +
    (facts.length ? `<p class="vet-muted">${facts.join(' · ')}</p>` : '') +
    (links.length ? `<p class="vet-links">${links.join(' ')}</p>` : '') + via;
}

function neossatBlock(det) {
  const n = det.neossat;
  if (!n) return '<p class="vet-muted">No NEOSSat check in this snapshot.</p>';
  const [text, tone] = NEOSSAT_TEXT[n.status] || [`NEOSSat: ${fmt.escape(n.status)}`, 'muted'];
  const frames = (n.frames || []).slice(0, 6).map((f) =>
    `<li>${f.url ? extLink(f.url, fmt.escape(f.obs_id || 'frame')) : fmt.escape(f.obs_id || 'frame')}` +
    `${Number.isFinite(f.mjd) ? ` <span class="vet-muted">MJD ${fmt.num(f.mjd, 2)}</span>` : ''}</li>`).join('');
  return `<p class="tone-${tone}">${text}</p>` +
    (Number.isFinite(n.n_frames) && n.n_frames ? `<p class="vet-muted">${n.n_frames} frame${n.n_frames === 1 ? '' : 's'}</p>` : '') +
    (frames ? `<ul class="vet-frames">${frames}</ul>` : '');
}

function mlBlock(det) {
  const ml = det.ml;
  if (!ml || !Number.isFinite(ml.real_bogus)) {
    return '<p class="vet-muted">No ML score for this snapshot.</p>';
  }
  const reasons = (ml.reasons || []).slice(0, 5).map((r) => {
    const up = (r.effect || 0) >= 0;
    return `<li class="${up ? 'r-up' : 'r-down'}"><span aria-hidden="true">${up ? '▲' : '▼'}</span>` +
      `<span class="sr-only">${up ? 'makes it more likely real:' : 'makes it less likely real:'}</span> ` +
      `${fmt.escape(r.label || r.feature)}` +
      `<span class="expert-only vet-muted"> (${fmt.escape(r.feature)} ${up ? '+' : ''}${fmt.num(r.effect, 2)})</span></li>`;
  }).join('');
  const unsure = ml.uncertain
    ? '<div class="vet-callout"><b>The computer is unsure about this one — help us classify.</b> ' +
      '<a href="#vet-vote" data-jump-vote>Vote below</a>.</div>' : '';
  return `<p class="big-score">${pct(ml.real_bogus)} <span>likely real</span></p>` +
    meter(ml.real_bogus, 'ML score, percent likely real') +
    `<p class="vet-muted">The ML score ranks detections so the best are looked at first; it does not decide.` +
    `${ml.model_version ? ` <span class="expert-only">Model ${fmt.escape(ml.model_version)}.</span>` : ''}</p>` +
    (reasons ? `<ul class="ml-reasons" aria-label="Main reasons for the ML score">${reasons}</ul>` : '') + unsure;
}

function evidenceBlock(det) {
  const ev = det.evidence;
  if (!ev || !Number.isFinite(ev.score)) {
    return '<p class="vet-muted">No evidence score for this snapshot.</p>';
  }
  const rows = (ev.parts || []).map((p) => {
    const v = Number.isFinite(p.value) ? Math.round(p.value * 100) : null;
    return `<tr><th scope="row">${fmt.escape(EVIDENCE_NAMES[p.name] || p.name)}</th>` +
      `<td class="mono">${v === null ? '<span class="vet-muted">n/a</span>' : v}</td>` +
      `<td class="mono expert-only">${Number.isFinite(p.weight) ? `×${fmt.num(p.weight, 2)}` : ''}</td>` +
      `<td>${fmt.escape(p.detail || '')}</td></tr>`;
  }).join('');
  return `<p class="big-score">${Math.round(ev.score * 100)} <span>/ 100</span></p>` +
    meter(ev.score, 'Evidence score out of 100') +
    '<p class="vet-muted">Every check combined into one number. n/a = that check could not be made.</p>' +
    (rows ? `<table class="kv-table ev-table"><caption class="sr-only">Evidence score parts</caption>` +
      `<thead class="sr-only"><tr><th scope="col">Part</th><th scope="col">Score</th><th scope="col" class="expert-only">Weight</th><th scope="col">Detail</th></tr></thead>` +
      `<tbody>${rows}</tbody></table>` : '');
}

function linksBlock(det) {
  const L = Object.assign({}, det.links || {});
  let note = '';
  if (!det.links && Number.isFinite(det.ra) && Number.isFinite(det.dec)) {
    // Older snapshots: build the two links that need no archive-specific query.
    L.wiseview = `http://byw.tools/wiseview#ra=${det.ra.toFixed(5)}&dec=${det.dec.toFixed(5)}&size=176&band=3&speed=500&minbright=-50&maxbright=500&window=0.5&diff_by_window=0&linear=1&color=&zoom=9&border=1&gaia=1&invert=1&maxdyr=0&scandir=0&neowise=0&diff=0&outer_epochs=0&unique=1&smooth_scan=0&shift=0&pmra=0&pmdec=0`;
    L.simbad = `https://simbad.cds.unistra.fr/simbad/sim-coo?Coord=${det.ra.toFixed(5)}+${det.dec.toFixed(5)}&Radius=10&Radius.unit=arcsec`;
    note = '<p class="vet-muted">Links built in the browser (this snapshot has none).</p>';
  }
  const out = [];
  if (L.wiseview) out.push(extLink(L.wiseview, 'Open in WiseView', 'btn btn-primary ext-btn'));
  const named = [['irsa_finder', 'IRSA Finder Chart'], ['simbad', 'SIMBAD'], ['gaia_mast', 'Gaia (MAST)']];
  named.forEach(([k, label]) => { if (L[k]) out.push(extLink(L[k], label, 'btn btn-secondary ext-btn')); });
  if (!out.length) return '<p class="vet-muted">No archive links for this detection.</p>';
  return `<div class="vet-linkrow">${out.join('')}</div>${note}` +
    (L.wiseview ? '<p class="vet-muted">WiseView flips through a decade of WISE images of this spot (Backyard Worlds).</p>' : '');
}

function neowiseBlock(det) {
  const n = det.neowise;
  if (!n || !n.n_visits) return '';
  const w1 = n.w1 || {};
  const parts = [`${n.n_visits} visits`, n.variable ? 'brightness VARIED' : 'brightness steady'];
  if (w1.amplitude != null) parts.push(`W1 amplitude ${w1.amplitude} mag`);
  if (n.pm_significant && Number.isFinite(n.pm_total_masyr)) parts.push(`moves ${fmt.num(n.pm_total_masyr / 1000, 2)}″/yr`);
  return vetSection('NEOWISE history (2010–2024)', `<p>${parts.map(fmt.escape).join(' · ')}</p>`);
}

const VOTE_KEY = 'spx-votes';
function myVotes() {
  try { return JSON.parse(lsGet(VOTE_KEY, '{}')) || {}; } catch { return {}; }
}

state.votesUnavailable = false;

function votingAvailable() {
  return MOCK_V2 || (!!liveBackendBase() && !state.votesUnavailable && offline.mode !== 'offline');
}

function voteBlock(det) {
  const id = det.detection_id || '';
  const mine = myVotes()[id];
  const ok = votingAvailable() && !!id;
  const label = { real: 'Real', bogus: 'Bogus', unsure: 'Not sure' };
  const buttons = ['real', 'bogus', 'unsure'].map((v) =>
    `<button type="button" class="btn ${v === 'real' ? 'btn-primary' : 'btn-secondary'} vote-btn" data-vote="${v}"` +
    ` aria-pressed="${mine === v}"${ok ? '' : ' disabled'}>${label[v]}</button>`).join('');
  const why = !id ? 'This detection has no stable id in this snapshot, so it cannot be voted on.'
    : !ok ? 'Voting needs the live backend; this copy of the site is pre-rendered.' : '';
  return `<section class="vet-sec vet-vote" id="vet-vote" aria-labelledby="vet-vote-h">` +
    `<h3 id="vet-vote-h">Is this real?</h3>` +
    '<p class="vet-muted">Look at the pictures and the checks, then tell us what you think.</p>' +
    `<div class="vote-row" role="group" aria-labelledby="vet-vote-h">${buttons}</div>` +
    `<p class="vote-status" role="status" aria-live="polite">${mine ? `Thanks — you voted “${label[mine]}”. You can change it.` : ''}` +
    `${why ? `<span class="needs-backend">${why}</span>` : ''}</p></section>`;
}

async function sendVote(det, vote, root) {
  const status = root.querySelector('.vote-status');
  const id = det.detection_id;
  const target = (state.data && state.data.target) || state.target;
  const label = { real: 'Real', bogus: 'Bogus', unsure: 'Not sure' }[vote];
  root.querySelectorAll('[data-vote]').forEach((b) => { b.disabled = true; });
  if (status) status.textContent = 'Sending your vote…';
  let ok = false, unavailable = false, problem = '';
  if (MOCK_V2) {
    ok = true;                                       // dev mock: nothing is sent
  } else {
    try {
      const resp = await fetch(`${liveBackendBase()}/votes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target, detection_id: id, vote }),
      });
      if ([404, 405, 501].includes(resp.status)) unavailable = true;
      else if (!resp.ok) problem = `HTTP ${resp.status}`;
      else ok = true;
    } catch (err) {
      problem = err.message;
    }
  }
  if (ok) {
    const all = myVotes();
    all[id] = vote;
    lsSet(VOTE_KEY, JSON.stringify(all));
  }
  if (unavailable) state.votesUnavailable = true;
  root.querySelectorAll('[data-vote]').forEach((b) => {
    b.disabled = !votingAvailable();
    b.setAttribute('aria-pressed', String(ok ? b.dataset.vote === vote : b.getAttribute('aria-pressed') === 'true'));
  });
  if (status) {
    status.textContent = ok
      ? `Thanks — you voted “${label}”.${MOCK_V2 ? ' (mock mode: not sent)' : ''}`
      : unavailable ? 'Voting needs the live backend; this server has no vote endpoint.'
        : `Your vote was not saved (${problem}). Please try again.`;
  }
}

function cutoutsBlock(det) {
  const d = state.data;
  const pos = det.position;
  if (!d || !Array.isArray(pos) || !Array.isArray(d.image)) {
    return '<p class="vet-muted">No image data in this snapshot.</p>';
  }
  const half = 12;
  const size = (2 * half + 1) * ((d.map && d.map.pixscale_arcsec) || 3);
  const fig = (id, title, desc) => `<figure class="cutout"><canvas id="${id}" role="img" aria-label="${fmt.escape(desc)}"></canvas>` +
    `<figcaption>${title}</figcaption></figure>`;
  const gm = gaiaMotion(det);
  const minus = det.pos_minus || det.position_minus;
  const notes = [];
  if (gm) {
    notes.push(`<span class="arrow-key gaia" aria-hidden="true"></span> Gaia says this star moves ${fmt.num(gm.mu, 2)}″/yr toward the ${compass(gm.pa)}.`);
  }
  if (det.kind === 'dipole' && Array.isArray(minus)) {
    notes.push('<span class="arrow-key measured" aria-hidden="true"></span> The difference shows a dark spot (where it was) and a bright spot (where it is now): the purple arrow joins them.');
  } else if (det.kind === 'monopole_negative') {
    notes.push('One dark spot in the difference: the source got fainter.');
  } else if (det.kind === 'monopole') {
    notes.push('One bright spot in the difference: the source got brighter, or appeared.');
  }
  const mc = det.motion_check;
  if (mc && Number.isFinite(mc.predicted_arcsec)) {
    notes.push(`Expected shift over the baseline: ${fmt.num(mc.predicted_arcsec, 1)}″; measured ${mc.measured_arcsec != null ? `${fmt.num(mc.measured_arcsec, 1)}″` : 'n/a'}.`);
  }
  return '<div class="cutouts">' +
    fig('vet-cut-sky', 'Sky (all visits stacked)', `Co-add picture ${size} arcseconds across, centred on the detection` + (gm ? `, with an arrow showing the Gaia motion toward the ${compass(gm.pa)}` : '')) +
    fig('vet-cut-diff', 'Difference (later − earlier)', `Difference picture ${size} arcseconds across: ${det.kind === 'dipole' ? 'a dark and a bright spot side by side, the sign of motion' : det.kind === 'monopole_negative' ? 'one dark spot, the source got fainter' : 'one bright spot, the source got brighter'}`) +
    fig('vet-cut-snr', 'Significance (σ)', `Significance picture: the change is ${fmt.num(det.snr, 1)} sigma`) +
    '</div>' +
    `<p class="vet-muted">Each picture is ${size}″ across, north up, east left. This snapshot keeps the stacked picture and the difference, not a separate before and after picture for each visit.</p>` +
    (notes.length ? `<ul class="cutout-notes">${notes.map((n) => `<li>${n}</li>`).join('')}</ul>` : '');
}

function paintCutouts(det) {
  const d = state.data;
  const pos = det.position;
  if (!d || !Array.isArray(pos) || !Array.isArray(d.image)) return;
  const [row, col] = pos;
  const half = 12;
  const T = chartTheme();
  const sky = [];
  const gm = gaiaMotion(det);
  if (gm) sky.push({ dx: gm.dirX * 8, dy: gm.dirY * 8, colour: T.blue });
  const diff = [];
  const minus = det.pos_minus || det.position_minus;
  if (det.kind === 'dipole' && Array.isArray(minus)) {
    const dx = col - minus[1], dy = row - minus[0];
    if (Math.hypot(dx, dy) > 0.5) diff.push({ fromX: -dx, fromY: -dy, dx, dy, colour: '#c4a7ff' });
  }
  const sk = $('vet-cut-sky'), df = $('vet-cut-diff'), sn = $('vet-cut-snr');
  if (sk) drawCutout(sk, d.image, row, col, half, 'coadd', false, sky);
  if (df) drawCutout(df, d.zogy_image, row, col, half, 'diff', true, diff);
  if (sn) drawCutout(sn, d.zogy_snr, row, col, half, 'snr', false);
}

function renderVetCard(index) {
  const body = $('vet-body');
  const d = state.data;
  const det = d && d.detections[index];
  if (!body || !det) return;
  const n = d.detections.length;
  const cat = categoryOf(det);
  const info = cat ? CATEGORY_INFO[cat] : null;
  setText('vet-kicker', `${d.common || d.target} · detection ${index + 1} of ${n}`);
  setText('vet-title', `${info ? info.label : (det.verdict || 'Detection')} · ${kindLabel(det.kind)} · ${fmt.num(det.snr, 1)}σ`);
  $('vet-prev').disabled = index <= 0;
  $('vet-next').disabled = index >= n - 1;

  const reasons = (det.reasons || []).length
    ? `<div class="verdict-flags">${det.reasons.map(formatVerdictFlag).join('')}</div>`
    : `<p class="vet-muted">${fmt.escape(info ? info.hint : 'No rule notes.')}</p>`;
  const facts = `<dl class="vet-facts">` +
    `<div><dt>Position</dt><dd class="mono">${fmt.num(det.ra, 5)}, ${fmt.num(det.dec, 5)}</dd></div>` +
    `<div><dt>Epochs</dt><dd class="mono">${fmt.escape(det.pair || '--')}${Number.isFinite(det.mjd_a) ? ` (MJD ${fmt.num(det.mjd_a, 1)} → ${fmt.num(det.mjd_b, 1)})` : ''}</dd></div>` +
    (det.delta_mag != null ? `<div><dt>Brightness</dt><dd class="mono">${det.delta_mag > 0 ? '+' : ''}${det.delta_mag} mag</dd></div>` : '') +
    (Number.isFinite(det.sign) && det.kind !== 'dipole' ? `<div><dt>Change</dt><dd>${det.sign < 0 ? 'got fainter' : 'got brighter'}</dd></div>` : '') +
    (Number.isFinite(det.n_pairs_seen) ? `<div><dt>Seen in</dt><dd>${det.n_pairs_seen} epoch pair${det.n_pairs_seen === 1 ? '' : 's'}</dd></div>` : '') +
    `<div class="expert-only"><dt>ID</dt><dd class="mono">${fmt.escape(det.detection_id || '--')}</dd></div>` +
    `<div class="expert-only"><dt>Confidence</dt><dd>${fmt.escape(det.confidence || '--')} · priority ${fmt.escape(det.priority ?? '--')}</dd></div>` +
    '</dl>';

  const showSed = nearTarget(det) && d.sed &&
    ((d.sed.points || []).concat(d.sed.spherex || [])).some((p) => p.mjy > 0 && p.lam_um > 0);

  body.innerHTML =
    `<div class="vet-summary">${categoryBadge(det)}<p class="plain-verdict">${fmt.escape(plainVerdict(det))}</p></div>` +
    '<div class="vet-grid">' +
      '<div class="vet-col">' +
        vetSection('Pictures', cutoutsBlock(det)) +
        vetSection('Why the pipeline says so', reasons + facts) +
        neowiseBlock(det) +
        (showSed ? vetSection('Target spectrum / SED',
          `<figure class="chart vet-sed"><canvas id="vet-sed" role="img" aria-label="${fmt.escape(sedSummary(d.sed))}"></canvas></figure>`) : '') +
      '</div>' +
      '<div class="vet-col">' +
        vetSection('Asteroid check', asteroidBlock(det), det.asteroid && det.asteroid.status === 'unavailable' ? 'is-caution' : '') +
        vetSection('ML score', mlBlock(det)) +
        vetSection('Evidence score', evidenceBlock(det)) +
        vetSection('NEOSSat', neossatBlock(det)) +
        vetSection('Look it up', linksBlock(det)) +
        voteBlock(det) +
      '</div>' +
    '</div>';

  paintCutouts(det);
  if (showSed) drawSed(d.sed, 'vet-sed');

  body.querySelectorAll('[data-vote]').forEach((btn) => {
    btn.addEventListener('click', () => sendVote(det, btn.dataset.vote, body.querySelector('.vet-vote')));
  });
  const jump = body.querySelector('[data-jump-vote]');
  if (jump) {
    jump.addEventListener('click', (e) => {
      e.preventDefault();
      const first = body.querySelector('[data-vote]:not([disabled])') || body.querySelector('#vet-vote-h');
      if (first) {
        if (first.tagName === 'H3') first.tabIndex = -1;
        first.scrollIntoView({ block: 'center', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
        first.focus({ preventScroll: true });
      }
    });
  }
}

function stepVetCard(delta) {
  const dets = (state.data && state.data.detections) || [];
  const next = state.vetIndex + delta;
  if (next < 0 || next >= dets.length) return;
  state.vetIndex = next;
  selectDetection(next);
  renderVetCard(next);
  const btn = $(delta < 0 ? 'vet-prev' : 'vet-next');
  if (btn && btn.disabled) $('vet-title').focus();
}

// --------------------------------------------------------- help classify

async function loadClassifyQueue() {
  const list = $('classify-list');
  const note = $('classify-note');
  if (!list) return;
  if (!MOCK_V2 && !liveBackendBase()) {
    list.innerHTML = '';
    if (note) note.innerHTML = '<span class="needs-backend">Help classify needs the live backend; this copy of the site is pre-rendered.</span>';
    return;
  }
  if (note) note.textContent = 'Loading…';
  const res = await fetchV2Json('votes/queue', '?n=10');
  if (!res.ok) {
    list.innerHTML = '';
    if (note) {
      note.innerHTML = res.status === 404
        ? '<span class="needs-backend">Help classify needs the live backend (this server has no vote queue yet).</span>'
        : `Could not load the list (${fmt.escape(res.error || `HTTP ${res.status}`)}).`;
    }
    return;
  }
  const raw = res.data;
  const items = Array.isArray(raw) ? raw : (raw && (raw.items || raw.queue || raw.detections)) || [];
  if (!items.length) {
    list.innerHTML = '';
    if (note) note.textContent = 'Nothing to classify right now — thank you!';
    return;
  }
  const votes = myVotes();
  list.innerHTML = items.slice(0, 10).map((it, i) => {
    const cat = it.category && CATEGORY_INFO[it.category] ? CATEGORY_INFO[it.category].label : (it.verdict || '');
    const score = it.ml && Number.isFinite(it.ml.real_bogus) ? it.ml.real_bogus : it.real_bogus;
    const done = votes[it.detection_id];
    return `<li class="classify-item">` +
      `<div><b>${fmt.escape(it.common || String(it.target || '').replace(/_/g, ' '))}</b> · ${fmt.escape(kindLabel(it.kind))}` +
      `${Number.isFinite(it.snr) ? ` · ${fmt.num(it.snr, 1)}σ` : ''}` +
      `<div class="vet-muted">${fmt.escape(cat)}${Number.isFinite(score) ? ` · ML ${pct(score)}` : ''}` +
      `${Number.isFinite(it.ra) ? ` · ${fmt.num(it.ra, 4)}, ${fmt.num(it.dec, 4)}` : ''}` +
      `${done ? ` · you voted ${fmt.escape(done)}` : ''}</div></div>` +
      `<button type="button" class="btn btn-secondary" data-classify="${i}">Look at it</button></li>`;
  }).join('');
  if (note) note.textContent = `${items.length} detection${items.length === 1 ? '' : 's'} need a human look.`;
  list.querySelectorAll('[data-classify]').forEach((btn) => {
    btn.addEventListener('click', () => openQueuedDetection(items[parseInt(btn.dataset.classify, 10)], btn));
  });
}

async function openQueuedDetection(item, opener) {
  if (!item) return;
  const sel = $('target-select');
  if (item.target && sel && sel.value !== item.target) {
    if (![...sel.options].some((o) => o.value === item.target)) {
      setText('classify-note', `${item.target} is not in this deployment's field list.`);
      return;
    }
    sel.value = item.target;
    await waitForPipeline();
    await runPipeline();
  }
  const dets = (state.data && state.data.detections) || [];
  let index = dets.findIndex((d) => d.detection_id && d.detection_id === item.detection_id);
  if (index < 0 && Number.isFinite(item.ra)) {
    let best = Infinity;
    dets.forEach((d, i) => {
      const sep = historySeparationArcsec(d.ra, d.dec, item.ra, item.dec);
      if (sep < best && sep < 6) { best = sep; index = i; }
    });
  }
  if (index < 0) {
    setText('classify-note', 'That detection is not in the current snapshot of its field.');
    return;
  }
  selectDetection(index);
  openVetCard(index, opener);
}

// ------------------------------------------------------------ accuracy page

function v2Frame(id) {
  const canvas = $(id);
  if (!canvas) return null;
  const T = chartTheme();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(canvas.clientWidth) || 360, h = Math.round(canvas.clientHeight) || 220;
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = T.bg; ctx.fillRect(0, 0, w, h);
  const f = { ctx, w, h, left: 44, right: w - 12, top: 14, bottom: h - 34 };
  f.X = scaler(0, 1, f.left, f.right);
  f.Y = scaler(0, 1, f.bottom, f.top);
  ctx.strokeStyle = T.grid; ctx.lineWidth = 1;
  ctx.font = '11px "DM Mono", ui-monospace, Menlo, monospace';
  ctx.fillStyle = T.muted;
  [0, 0.25, 0.5, 0.75, 1].forEach((t) => {
    ctx.beginPath(); ctx.moveTo(f.left, f.Y(t)); ctx.lineTo(f.right, f.Y(t)); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(f.X(t), f.top); ctx.lineTo(f.X(t), f.bottom); ctx.stroke();
    ctx.textAlign = 'right'; ctx.fillText(t.toFixed(t % 1 ? 2 : 0), f.left - 5, f.Y(t) + 4);
    ctx.textAlign = 'center'; ctx.fillText(t.toFixed(t % 1 ? 2 : 0), f.X(t), f.bottom + 14);
  });
  return f;
}

function axisTitles(f, x, y) {
  const { ctx } = f;
  ctx.fillStyle = chartTheme().text;
  ctx.font = '11px "DM Mono", ui-monospace, Menlo, monospace';
  ctx.textAlign = 'center';
  ctx.fillText(x, (f.left + f.right) / 2, f.h - 4);
  ctx.save();
  ctx.translate(11, (f.top + f.bottom) / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText(y, 0, 0);
  ctx.restore();
}

function drawPrCurve(id, curve) {
  const f = v2Frame(id);
  if (!f) return;
  const pts = (curve || []).filter((p) => Array.isArray(p) && p.length >= 2).sort((a, b) => a[0] - b[0]);
  const { ctx } = f;
  ctx.strokeStyle = chartTheme().blue; ctx.lineWidth = 2;
  ctx.beginPath();
  pts.forEach(([r, p], i) => { if (i) ctx.lineTo(f.X(r), f.Y(p)); else ctx.moveTo(f.X(r), f.Y(p)); });
  ctx.stroke();
  axisTitles(f, 'recall (share of real ones found)', 'precision');
}

function drawCalibration(id, cal) {
  const f = v2Frame(id);
  if (!f) return;
  const { ctx } = f;
  const T = chartTheme();
  ctx.strokeStyle = T.muted; ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(f.X(0), f.Y(0)); ctx.lineTo(f.X(1), f.Y(1)); ctx.stroke();
  ctx.setLineDash([]);
  const pts = (cal || []).filter((p) => Array.isArray(p) && p.length >= 2);
  const maxN = Math.max(1, ...pts.map((p) => p[2] || 1));
  ctx.strokeStyle = T.amber; ctx.lineWidth = 1.5;
  ctx.beginPath();
  pts.forEach(([pr, ob], i) => { if (i) ctx.lineTo(f.X(pr), f.Y(ob)); else ctx.moveTo(f.X(pr), f.Y(ob)); });
  ctx.stroke();
  ctx.fillStyle = T.amber;
  pts.forEach(([pr, ob, n]) => {
    ctx.beginPath(); ctx.arc(f.X(pr), f.Y(ob), 2.5 + 4 * Math.sqrt((n || 1) / maxN), 0, Math.PI * 2); ctx.fill();
  });
  axisTitles(f, 'ML score said (chance real)', 'turned out real');
}

function heatColour(v) {
  const lut = RAMPS.viridis;
  const k = Math.round(Math.max(0, Math.min(1, v)) * 255) * 3;
  const rgb = [lut[k], lut[k + 1], lut[k + 2]];
  const lum = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255;
  return { bg: `rgb(${rgb.join(',')})`, fg: lum > 0.5 ? '#0b0e12' : '#ffffff' };
}

function recallHeatmaps(grid) {
  const cats = [...new Set(grid.map((g) => g.category || 'all'))];
  return cats.map((cat) => {
    const cells = grid.filter((g) => (g.category || 'all') === cat);
    const snrs = [...new Set(cells.map((g) => g.snr))].sort((a, b) => a - b);
    const mus = [...new Set(cells.map((g) => g.mu))].sort((a, b) => a - b);
    const head = snrs.map((s) => `<th scope="col">${fmt.escape(s)}</th>`).join('');
    const body = mus.map((mu) => `<tr><th scope="row">${fmt.escape(mu)}</th>` + snrs.map((s) => {
      const c = cells.find((g) => g.snr === s && g.mu === mu);
      if (!c || !Number.isFinite(c.recall)) return '<td class="heat-na">—</td>';
      const col = heatColour(c.recall);
      return `<td style="background:${col.bg};color:${col.fg}" title="${c.n ? `${c.n} planted` : ''}">${Math.round(c.recall * 100)}%</td>`;
    }).join('') + '</tr>').join('');
    return `<div class="heat-wrap"><table class="heat-table">` +
      `<caption>${fmt.escape(cat)}: recall by signal strength (columns, SNR) and speed (rows, ″/yr)</caption>` +
      `<thead><tr><th scope="col"><span class="sr-only">speed \\ SNR</span>μ \\ SNR</th>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }).join('');
}

function accuracyTile(label, value, ci, line) {
  const ciText = Array.isArray(ci) && ci.length === 2
    ? `<span class="tile-ci">95% CI ${pct(ci[0])}–${pct(ci[1])}</span>` : '';
  return `<div class="acc-tile"><div class="tile-label">${label}</div>` +
    `<div class="tile-value">${value}</div>${ciText}<p class="tile-line">${line}</p></div>`;
}

function modelCard(model, url) {
  if (!model) return '<p class="vet-muted">No model card published yet.</p>';
  const rows = Object.entries(model).filter(([k]) => !k.startsWith('_')).map(([k, v]) => {
    let val;
    if (Array.isArray(v)) val = v.map((x) => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ');
    else if (v && typeof v === 'object') val = Object.entries(v).map(([a, b]) => `${a}: ${typeof b === 'object' ? JSON.stringify(b) : b}`).join(' · ');
    else val = String(v);
    return `<div><dt>${fmt.escape(k.replace(/_/g, ' '))}</dt><dd>${fmt.escape(val)}</dd></div>`;
  }).join('');
  return `<dl class="prov-list model-card">${rows}</dl>` +
    (url && !MOCK_V2 ? `<p>${extLink(url, 'Model card (JSON)')}</p>` : '');
}

async function openAccuracy(opener) {
  const body = $('accuracy-body');
  openSheet('accuracy', opener);
  if (!body) return;
  body.setAttribute('aria-busy', 'true');
  body.innerHTML = '<p class="vet-muted">Loading the accuracy measurements…</p>';
  const [metricsRes, modelRes] = await Promise.all([fetchV2Json('metrics'), fetchV2Json('model')]);
  body.setAttribute('aria-busy', 'false');
  const m = metricsRes.ok ? metricsRes.data : null;
  const model = modelRes.ok && modelRes.data && !modelRes.data.error ? modelRes.data : null;
  if (!m || m.error || (!m.classifier && !m.null_test && !m.injection)) {
    body.innerHTML = '<div class="empty-state"><h3>No accuracy measurements yet</h3>' +
      '<p>They appear here once the evaluation has run: it plants fake sources in real SPHEREx images, ' +
      'runs the pipeline on empty sky to count false alarms, and scores the ML model on fields it never saw.</p>' +
      `<p class="vet-muted">Source: <code>/api/metrics</code> (${metricsRes.status ? `HTTP ${metricsRes.status}` : 'not reachable'}).</p>` +
      (model ? `<h3>Model card</h3>${modelCard(model, modelRes.url)}` : '') + '</div>';
    return;
  }
  const c = m.classifier || {};
  const ci = c.ci95 || {};
  const nt = m.null_test || {};
  const inj = m.injection || {};
  const th = m.threshold || {};
  const ak = m.answer_key || {};
  const p = m.pipeline || {};
  const num2 = (v) => (Number.isFinite(v) ? v.toFixed(2) : '—');

  const tiles = [
    accuracyTile('Precision', pct(c.precision), ci.precision,
      Number.isFinite(c.precision) ? `Of everything we flag as real, ${pct(c.precision)} is real.` : 'Not measured yet.'),
    accuracyTile('Recall', pct(c.recall), ci.recall,
      Number.isFinite(c.recall) ? `Of all the real ones, we catch ${pct(c.recall)}.` : 'Not measured yet.'),
    accuracyTile('F1', num2(c.f1), ci.f1, 'One number that balances the two (1.00 is perfect).'),
    accuracyTile('PR-AUC', num2(c.pr_auc), ci.pr_auc, 'How well the ML score puts real ones ahead of bogus ones (1.00 is perfect).'),
  ].join('');
  const facts = [];
  if (Number.isFinite(nt.false_alarms_per_field)) {
    facts.push(accuracyTile('False alarms', num2(nt.false_alarms_per_field), null,
      `On ${nt.n_fields ?? 'several'} patches of empty sky we raised ${num2(nt.false_alarms_per_field)} false alarms per field` +
      (Number.isFinite(nt.false_alarms_per_deg2) ? ` (${num2(nt.false_alarms_per_deg2)} per square degree).` : '.')));
  }
  if (Number.isFinite(inj.recall_overall)) {
    facts.push(accuracyTile('Planted sources found', pct(inj.recall_overall), null,
      `Of the fake sources we planted in real images, we found ${pct(inj.recall_overall)}.`));
  }
  if (Number.isFinite(ak.recall)) {
    facts.push(accuracyTile('Known objects found', pct(ak.recall), null,
      `Of ${ak.n ?? 'the'} known real objects in our answer key, we found ${pct(ak.recall)}.`));
  }
  if (Number.isFinite(th.sigma)) {
    facts.push(accuracyTile('Threshold', `${fmt.num(th.sigma, 1)}σ`, null,
      `We only flag changes stronger than ${fmt.num(th.sigma, 1)}σ` +
      (th.method ? ` (chosen by ${fmt.escape(th.method)}).` : '.')));
  }

  const pr = c.pr_curve || [];
  const cal = c.calibration || [];
  const calGap = cal.length
    ? cal.reduce((best, x) => (Math.abs(x[1] - x[0]) > Math.abs(best[1] - best[0]) ? x : best), cal[0]) : null;
  const prMid = pr.length ? pr.reduce((best, x) => (Math.abs(x[0] - 0.9) < Math.abs(best[0] - 0.9) ? x : best), pr[0]) : null;
  const prText = prMid ? `Precision-recall curve: at ${pct(prMid[0])} recall the precision is ${pct(prMid[1])}. PR-AUC ${num2(c.pr_auc)}.` : '';
  const calText = calGap ? `Calibration: points close to the dashed diagonal mean the ML percentage can be taken at face value. ` +
    `Largest gap: the model said ${pct(calGap[0])} and ${pct(calGap[1])} turned out real.` : '';

  const conf = c.confusion;
  let confHtml = '';
  if (conf && Array.isArray(conf.matrix) && Array.isArray(conf.labels)) {
    const L = conf.labels;
    confHtml = '<table class="conf-table"><caption>Confusion matrix (rows: true label, columns: what the model said)</caption>' +
      `<thead><tr><th scope="col"><span class="sr-only">true \\ predicted</span></th>${L.map((l) => `<th scope="col">said ${fmt.escape(l)}</th>`).join('')}</tr></thead><tbody>` +
      conf.matrix.map((row, i) => `<tr><th scope="row">really ${fmt.escape(L[i])}</th>${row.map((v, j) =>
        `<td class="${i === j ? 'conf-ok' : 'conf-bad'}">${fmt.escape(v)}</td>`).join('')}</tr>`).join('') +
      '</tbody></table>';
  }
  const byClass = ak.by_class && Object.keys(ak.by_class).length
    ? '<table class="kv-table"><caption class="vet-muted">Answer-key recall by kind of object</caption><tbody>' +
      Object.entries(ak.by_class).map(([k, v]) => `<tr><th scope="row">${fmt.escape(k.replace(/_/g, ' '))}</th><td class="mono">${pct(v)}</td></tr>`).join('') +
      '</tbody></table>' : '';

  body.innerHTML =
    `<p class="acc-lead">How often SPHERExLens is right, measured on fake sources planted in real images, on empty sky, and on fields the model never trained on.</p>` +
    `<p class="vet-muted mono">pipeline ${fmt.escape(p.pipeline_version || '?')} · config ${fmt.escape(p.config_hash || '?')} · model ${fmt.escape(p.model_version || 'none')}` +
    `${m.generated_utc ? ` · measured ${fmt.escape(formatTimestamp(m.generated_utc))}` : ''}${c.split ? ` · split: ${fmt.escape(c.split)}` : ''}</p>` +
    (MOCK_V2 ? '<p class="needs-backend">Mock numbers (?mock=v2) — not a real measurement.</p>' : '') +
    `<h3>The ML model</h3><div class="acc-tiles">${tiles}</div>` +
    (facts.length ? `<h3>The whole pipeline</h3><div class="acc-tiles">${facts.join('')}</div>` : '') +
    '<div class="acc-charts">' +
      (pr.length ? `<figure class="chart acc-chart"><figcaption>Precision vs recall</figcaption><canvas id="acc-pr" role="img" aria-label="${fmt.escape(prText)}"></canvas><p class="chart-summary">${fmt.escape(prText)}</p></figure>` : '') +
      (cal.length ? `<figure class="chart acc-chart"><figcaption>Calibration: is “90%” really 90%?</figcaption><canvas id="acc-cal" role="img" aria-label="${fmt.escape(calText)}"></canvas><p class="chart-summary">${fmt.escape(calText)}</p></figure>` : '') +
    '</div>' +
    (confHtml || byClass ? `<div class="acc-tables">${confHtml}${byClass}</div>` : '') +
    ((inj.grid || []).length ? `<h3>Where we find planted sources</h3><p class="vet-muted">Recall in each box: faint (left) to bright (right), slow (top) to fast (bottom). Darker boxes = fewer found.</p><div class="heat-grid">${recallHeatmaps(inj.grid)}</div>` : '') +
    `<h3>Model card</h3>${modelCard(model, modelRes.url)}`;

  state.accCurves = { pr, cal };
  if (pr.length) drawPrCurve('acc-pr', pr);
  if (cal.length) drawCalibration('acc-cal', cal);
}

// ---------------------------------------------------------------- credits

const CREDITS_FALLBACK = [
  { name: 'NASA/IPAC Infrared Science Archive (IRSA)', provides: 'SPHEREx Level-2 images; WISE, AllWISE and NEOWISE photometry',
    licence: 'NASA data, no usage restrictions; acknowledgement requested', url: 'https://irsa.ipac.caltech.edu/',
    text: 'This research has made use of the NASA/IPAC Infrared Science Archive, which is funded by the National Aeronautics and Space Administration and operated by the California Institute of Technology. SPHEREx is a joint project of the Jet Propulsion Laboratory and the California Institute of Technology, funded by NASA. WISE is a joint project of UCLA and JPL/Caltech, funded by NASA; NEOWISE is a project of JPL/Caltech funded by NASA’s Planetary Science Division.' },
  { name: 'ESA Gaia (DPAC)', provides: 'Gaia DR3 positions, proper motions and variability flags',
    licence: 'CC BY-SA 3.0 IGO', url: 'https://www.cosmos.esa.int/gaia',
    text: 'This work has made use of data from the European Space Agency (ESA) mission Gaia, processed by the Gaia Data Processing and Analysis Consortium (DPAC). Funding for the DPAC has been provided by national institutions, in particular the institutions participating in the Gaia Multilateral Agreement.' },
  { name: 'STScI MAST', provides: 'Gaia catalogue links (MAST Portal)',
    licence: 'public archive; acknowledgement requested', url: 'https://archive.stsci.edu/',
    text: 'Links to the Mikulski Archive for Space Telescopes (MAST) at the Space Telescope Science Institute.' },
  { name: 'JPL Solar System Dynamics', provides: 'Small-Body Database (SBDB) and SB Identification asteroid check',
    licence: 'NASA data, no usage restrictions', url: 'https://ssd.jpl.nasa.gov/',
    text: 'Asteroid identifications use the JPL Small-Body Identification service and Small-Body Database (NASA/JPL Solar System Dynamics).' },
  { name: 'Canadian Astronomy Data Centre (CADC)', provides: 'SSOIS asteroid check; NEOSSat archive',
    licence: 'see CADC terms of use', url: 'https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/',
    text: 'This research used the facilities of the Canadian Astronomy Data Centre operated by the National Research Council of Canada with the support of the Canadian Space Agency. SSOIS: Gwyn, Hill & Kavelaars (2012).' },
  { name: 'Canadian Space Agency — NEOSSat', provides: 'NEOSSat images',
    licence: 'Open Government Licence – Canada', url: 'https://open.canada.ca/en/open-government-licence-canada',
    text: 'Contains information licensed under the Open Government Licence – Canada (NEOSSat data, Canadian Space Agency).' },
  { name: 'AAVSO VSX via CDS VizieR', provides: 'variable-star catalogue',
    licence: 'see AAVSO and CDS terms', url: 'https://www.aavso.org/vsx/',
    text: 'This research has made use of the International Variable Star Index (VSX) database, operated at AAVSO, Cambridge, Massachusetts, USA, and of the VizieR catalogue access tool, CDS, Strasbourg, France (DOI: 10.26093/cds/vizier). SIMBAD links: CDS, Strasbourg.' },
  { name: 'WiseView (byw.tools / Backyard Worlds)', provides: 'the “Open in WiseView” image blinker',
    licence: 'external tool, linked only', url: 'http://byw.tools/wiseview',
    text: 'WiseView: Caselden et al. (2018), built for the Backyard Worlds: Planet 9 citizen-science project.' },
];

function normaliseCredits(data) {
  const list = Array.isArray(data) ? data
    : (data && (data.sources || data.credits || data.items)) || null;
  if (!Array.isArray(list) || !list.length) return null;
  return list.map((c) => (typeof c === 'string' ? { name: c } : {
    name: c.name || c.title || c.source || 'Data source',
    provides: c.provides || c.data || c.what || '',
    licence: c.licence || c.license || '',
    url: c.url || c.link || '',
    text: c.text || c.acknowledgement || c.acknowledgment || c.citation || '',
  }));
}

async function openCredits(opener) {
  const body = $('credits-body');
  openSheet('credits', opener);
  if (!body) return;
  const res = await fetchV2Json('credits');
  const list = (res.ok && normaliseCredits(res.data)) || CREDITS_FALLBACK;
  const from = res.ok && normaliseCredits(res.data) ? '' : '<p class="vet-muted">Built-in list (the server did not provide <code>/api/credits</code>).</p>';
  body.innerHTML = '<p>SPHERExLens is an independent project. It is not endorsed by NASA, ESA, the Canadian Space Agency or any data provider, and it uses no agency insignia.</p>' +
    from + '<ul class="credits-list">' + list.map((c) =>
    `<li><h3>${fmt.escape(c.name)}</h3>` +
      (c.provides ? `<p><b>Used for:</b> ${fmt.escape(c.provides)}</p>` : '') +
      (c.licence ? `<p><b>Licence:</b> ${fmt.escape(c.licence)}</p>` : '') +
      (c.text ? `<p class="vet-muted">${fmt.escape(c.text)}</p>` : '') +
      (c.url ? `<p>${extLink(c.url, fmt.escape(c.url.replace(/^https?:\/\//, '').replace(/\/$/, '')))}</p>` : '') +
    '</li>').join('') + '</ul>';
}

// ------------------------------------------------------------- deep fields
// GET /api/deep (list) and /api/deep/{name}: forced-photometry light curves of
// every catalogued star in a SPHEREx deep field (Phase 7).

MOCK_FILES.deep = 'dev/mock_deep.json';
state.deep = { field: null, source: -1 };

function deepFlags(s) {
  const out = [];
  if (s.variable) out.push('<span class="vf-tag vf-warn">VARIABLE</span>');
  else if (s.variable_self) out.push('<span class="vf-tag vf-mute" title="varies against its own median, but not against the field ensemble">SELF-VAR</span>');
  if (s.period && s.period.significant) out.push('<span class="vf-tag vf-info">PERIODIC</span>');
  if (s.motion && s.motion.significance > 5) out.push('<span class="vf-tag vf-ok">MOVING</span>');
  return out.join(' ') || '<span class="vet-muted">steady</span>';
}

function drawDeepCurve(id, lc) {
  const canvas = $(id);
  if (!canvas || !lc || !(lc.mjd || []).length) return;
  const T = chartTheme();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(canvas.clientWidth) || 600, h = Math.round(canvas.clientHeight) || 200;
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = T.bg; ctx.fillRect(0, 0, w, h);
  const pts = lc.mjd.map((m, i) => [m, (lc.ratio || [])[i], (lc.ratio_err || [])[i] || 0])
    .filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]));
  if (!pts.length) return;
  const xs = pts.map((p) => p[0]);
  const lo = Math.min(...pts.map((p) => p[1] - p[2]));
  const hi = Math.max(...pts.map((p) => p[1] + p[2]));
  const pad = (hi - lo) * 0.08 || 0.05;
  const X = scaler(Math.min(...xs), Math.max(...xs), 52, w - 12);
  const Y = scaler(lo - pad, hi + pad, h - 30, 12);
  ctx.strokeStyle = T.grid; ctx.fillStyle = T.muted;
  ctx.font = '11px "DM Mono", ui-monospace, Menlo, monospace';
  [lo - pad, (lo + hi) / 2, hi + pad].forEach((v) => {
    ctx.beginPath(); ctx.moveTo(52, Y(v)); ctx.lineTo(w - 12, Y(v)); ctx.stroke();
    ctx.textAlign = 'right'; ctx.fillText(v.toFixed(2), 47, Y(v) + 4);
  });
  if (1 > lo - pad && 1 < hi + pad) {
    ctx.strokeStyle = T.muted; ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(52, Y(1)); ctx.lineTo(w - 12, Y(1)); ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.textAlign = 'left'; ctx.fillText(`MJD ${Math.min(...xs).toFixed(0)}`, 52, h - 10);
  ctx.textAlign = 'right'; ctx.fillText(`${Math.max(...xs).toFixed(0)}`, w - 12, h - 10);
  ctx.textAlign = 'center'; ctx.fillText('flux / own median', w / 2, h - 10);
  ctx.strokeStyle = T.blue; ctx.fillStyle = T.blue; ctx.lineWidth = 1;
  pts.forEach(([m, r, e]) => {
    const x = X(m);
    if (e) { ctx.beginPath(); ctx.moveTo(x, Y(r - e)); ctx.lineTo(x, Y(r + e)); ctx.stroke(); }
    ctx.beginPath(); ctx.arc(x, Y(r), 2.2, 0, Math.PI * 2); ctx.fill();
  });
}

function renderDeepSource(i) {
  const f = state.deep.field;
  const s = f && f.sources[i];
  const out = $('deep-source');
  if (!s || !out) return;
  state.deep.source = i;
  const lc = s.lightcurve || {};
  const ratios = (lc.ratio || []).filter(Number.isFinite);
  const rms = ratios.length ? Math.sqrt(ratios.reduce((a, r) => a + (r - 1) ** 2, 0) / ratios.length) : null;
  const per = s.period || {};
  const mo = s.motion || {};
  const summary = `${s.id}: ${ratios.length} measurements; scatter ${rms !== null ? `${(rms * 100).toFixed(1)}%` : 'n/a'} around its median` +
    (per.significant ? `; repeats every ${fmt.num(per.period_days, 2)} days` : '') +
    (mo.significance > 5 ? `; moves (${fmt.num(Math.hypot(mo.pm_ra, mo.pm_dec), 0)} mas/yr)` : '') + '.';
  out.innerHTML = `<h3>${fmt.escape(s.id)}</h3><p>${deepFlags(s)}</p>` +
    `<figure class="chart deep-chart"><canvas id="deep-lc" role="img" aria-label="${fmt.escape(`Light curve. ${summary}`)}"></canvas>` +
    `<p class="chart-summary">${fmt.escape(summary)}</p></figure>` +
    '<dl class="prov-list">' +
    `<div><dt>Position</dt><dd class="mono">${fmt.num(s.ra, 5)}, ${fmt.num(s.dec, 5)}${Number.isFinite(s.gmag) ? ` · G ${fmt.num(s.gmag, 2)}` : ''}</dd></div>` +
    `<div><dt>Period</dt><dd class="mono">${per.period_days ? `${fmt.num(per.period_days, 3)} d · FAP ${Number(per.fap).toExponential(1)}${per.significant ? '' : ' (not significant)'}` : 'none found'}</dd></div>` +
    `<div><dt>Motion</dt><dd class="mono">${Number.isFinite(mo.pm_ra) ? `${fmt.num(mo.pm_ra, 1)}, ${fmt.num(mo.pm_dec, 1)} mas/yr · ${fmt.num(mo.significance, 1)}σ` : 'n/a'}</dd></div>` +
    (Number.isFinite(s.ensemble_chi2_median) ? `<div><dt>Field χ² median</dt><dd class="mono">${fmt.num(s.ensemble_chi2_median, 2)}</dd></div>` : '') +
    '</dl>';
  drawDeepCurve('deep-lc', lc);
}

async function loadDeepField(name) {
  const body = $('deep-fields-out');
  if (!body) return;
  body.innerHTML = '<p class="vet-muted">Loading…</p>';
  let res;
  if (MOCK_V2) {
    const r = await fetch('dev/mock_deep_field.json', { cache: 'no-store' });
    res = { ok: r.ok, status: r.status, data: r.ok ? await r.json() : null };
  } else {
    res = await fetchV2Json(`deep/${encodeURIComponent(name)}`);
  }
  if (!res.ok || !res.data || !Array.isArray(res.data.sources)) {
    body.innerHTML = `<p class="vet-muted">Could not load ${fmt.escape(name)} (${res.status ? `HTTP ${res.status}` : 'not reachable'}).</p>`;
    return;
  }
  const f = res.data;
  const rank = (s) => (s.variable ? 0 : 4) + ((s.period || {}).significant ? 0 : 2) + ((s.motion || {}).significance > 5 ? 0 : 1);
  f.sources.sort((a, b) => rank(a) - rank(b));
  state.deep.field = f;
  const rows = f.sources.slice(0, 200).map((s, i) => `<tr><td>${fmt.escape(s.id)}</td>` +
    `<td>${Number.isFinite(s.gmag) ? fmt.num(s.gmag, 1) : '—'}</td><td>${deepFlags(s)}</td>` +
    `<td>${(s.period || {}).significant ? `${fmt.num(s.period.period_days, 2)} d` : '—'}</td>` +
    `<td><button type="button" class="row-btn" data-deep-src="${i}" aria-label="Show the light curve of ${fmt.escape(s.id)}">Light curve</button></td></tr>`).join('');
  body.innerHTML =
    `<p>${fmt.escape(f.name)}: ${f.n_exposures_used ?? '?'} exposures, ${f.n_analysed ?? f.sources.length} stars measured — ` +
    `<b>${f.n_variable ?? 0}</b> variable, <b>${f.n_periodic ?? 0}</b> periodic, <b>${f.n_moving ?? 0}</b> moving.</p>` +
    (f.method ? `<p class="vet-muted">${fmt.escape(f.method)}</p>` : '') +
    (MOCK_V2 ? '<p class="needs-backend">Mock data (?mock=v2).</p>' : '') +
    '<div class="deep-grid"><div class="table-wrap deep-table-wrap"><table class="objects-table deep-table">' +
    '<caption class="sr-only">Stars in this deep field, the interesting ones first</caption>' +
    '<thead><tr><th scope="col">Star</th><th scope="col">G</th><th scope="col">Flags</th><th scope="col">Period</th><th scope="col"><span class="sr-only">Light curve</span></th></tr></thead>' +
    `<tbody>${rows}</tbody></table></div><div id="deep-source" class="deep-source"></div></div>`;
  body.querySelectorAll('[data-deep-src]').forEach((btn) => {
    btn.addEventListener('click', () => renderDeepSource(parseInt(btn.dataset.deepSrc, 10)));
  });
  if (f.sources.length) renderDeepSource(0);
}

async function openDeep(opener) {
  openSheet('deep', opener);
  const body = $('deep-body');
  if (!body) return;
  body.innerHTML = '<p class="vet-muted">Loading the deep-field list…</p>';
  const res = await fetchV2Json('deep');
  const fields = res.ok && res.data && Array.isArray(res.data.fields) ? res.data.fields : [];
  if (!fields.length) {
    body.innerHTML = '<div class="empty-state"><h3>No deep fields yet</h3><p>SPHEREx looks at its two deep fields near the ecliptic poles hundreds of times. ' +
      'When the deep-field run has been done, every star there gets a light curve, and this page lists the ones that vary, repeat or move.</p>' +
      `<p class="vet-muted">Source: <code>/api/deep</code> (${res.status ? `HTTP ${res.status}` : 'not reachable'}).</p></div>`;
    return;
  }
  body.innerHTML = '<div class="deep-pick"><label for="deep-select">Deep field</label> ' +
    `<select id="deep-select" class="control-select">${fields.map((f) =>
      `<option value="${fmt.escape(f.name)}">${fmt.escape(f.name)} (${f.n_variable ?? 0} variable)</option>`).join('')}</select></div>` +
    '<div id="deep-fields-out"></div>';
  $('deep-select').addEventListener('change', (e) => loadDeepField(e.target.value));
  loadDeepField(fields[0].name);
}

// ------------------------------------------------------------------ wiring

function attachV2Ui() {
  applyMode(lsGet(MODE_KEY, 'public'));
  document.querySelectorAll('#mode-switch .seg').forEach((btn) => {
    btn.addEventListener('click', () => applyMode(btn.dataset.mode));
  });
  attachSheets();
  $('btn-accuracy')?.addEventListener('click', (e) => openAccuracy(e.currentTarget));
  $('btn-credits')?.addEventListener('click', (e) => openCredits(e.currentTarget));
  $('btn-deep')?.addEventListener('click', (e) => openDeep(e.currentTarget));
  $('vet-prev')?.addEventListener('click', () => stepVetCard(-1));
  $('vet-next')?.addEventListener('click', () => stepVetCard(1));
  $('btn-win-classify')?.addEventListener('click', () => {
    // runs before the generic window toggle: load once it is visible
    setTimeout(() => {
      if (!$('win-classify').classList.contains('hidden')) loadClassifyQueue();
    }, 0);
  });
  document.querySelectorAll('.chart canvas').forEach((c) => {
    if (!c.getAttribute('role')) c.setAttribute('role', 'img');
    if (!c.getAttribute('aria-label')) c.setAttribute('aria-label', 'Chart: no data loaded yet.');
  });
  if (MOCK_V2) {
    document.body.classList.add('is-mock');
    setText('field-hint', 'mock data (?mock=v2): every v2 field is synthetic');
  }
  // Repaint the accuracy charts at their new size.
  let t = 0;
  window.addEventListener('resize', () => {
    clearTimeout(t);
    t = setTimeout(() => {
      const curves = state.accCurves;
      if (!curves || !$('accuracy') || $('accuracy').classList.contains('hidden')) return;
      if ($('acc-pr')) drawPrCurve('acc-pr', curves.pr);
      if ($('acc-cal')) drawCalibration('acc-cal', curves.cal);
    }, 200);
  });
}

// ------------------------------------------------------- demo / reset / init

function toggleDemo() {
  const btn = $('btn-demo');
  if (state.demo) {
    clearInterval(state.demo);
    state.demo = null;
    btn.classList.remove('active');
    btn.textContent = 'Auto Demo';
    btn.setAttribute('aria-pressed', 'false');
    setText('footer-status', 'auto demo stopped');
    return;
  }
  btn.classList.add('active');
  btn.textContent = 'Stop Demo';
  btn.setAttribute('aria-pressed', 'true');
  const advance = () => {
    const sel = $('target-select');
    const n = sel.options.length;
    if (n > 1) sel.selectedIndex = (sel.selectedIndex + 1) % n;
    refreshSelectedTarget();
  };
  advance();
  // Reduced motion: the page changes less often (WCAG 2.2.2 -- the same
  // button stops it at any time; it never starts on its own).
  const every = prefersReducedMotion() ? 30000 : 12000;
  state.demo = setInterval(advance, every);
  setText('footer-status', `auto demo: cycling targets every ${every / 1000} s — press Stop Demo to stop`);
}

function resetAll() {
  if (state.demo) toggleDemo();
  const wasHistory = !!state.snapshotHistory.selected;
  stopSnapshotPlayback();
  state.snapshotHistory.selected = '';
  state.snapshotScaleLock = null;
  state.snapshotTrail = [];
  state.focus = null;
  state.highlight = null;
  state.objectFilter = 'all';
  state.objectQuery = '';
  const search = $('object-search');
  if (search) search.value = '';
  document.querySelectorAll('#object-filter .seg').forEach((b) =>
    b.classList.toggle('active', b.dataset.filter === 'all'));
  drawObjectTable();
  state.selected = -1;
  state.crosshair = null;
  state.recovery = null;
  state.stretch = 'linear';
  state.lut = 'auto';
  const stretchEl = $('stretch-select');
  if (stretchEl) stretchEl.value = 'linear';
  const lutEl = $('lut-select');
  if (lutEl) lutEl.value = 'auto';
  if (state.data) {
    rebuildLayers();
    fitView();
    updateZoomLabel();
    draw();
    renderVerdict(state.data.detections[0] || null);
    state.selected = 0;
    document.querySelectorAll('#detections-body tr')
      .forEach((tr, i) => tr.classList.toggle('selected', i === 0));
    drawRecoveryChart(null);
  }
  setText('recovery-out', 'injection-recovery not run yet');
  $('recovery-out').style.whiteSpace = 'normal';
  setText('map-hint', 'view reset');
  renderSnapshotControls();
  if (wasHistory && !state.live) runPipeline();
}

async function copyShareLink() {
  const target = $('target-select').value;
  const params = new URLSearchParams({ target });
  if (state.fieldCoordinates) {
    params.set('ra', String(state.fieldCoordinates.ra));
    params.set('dec', String(state.fieldCoordinates.dec));
    if (state.fieldCoordinates.name) params.set('name', state.fieldCoordinates.name);
  } else if (state.live) {
    params.set('source', 'live');
  }
  const url = `${window.location.origin}${window.location.pathname}?${params.toString()}`;
  try {
    await navigator.clipboard.writeText(url);
    setText('action-message', 'Share link copied to the clipboard.');
    setText('footer-status', 'share link copied');
  } catch {
    const manualCopy = window.prompt('Copy this share link:', url);
    if (manualCopy === null) {
      setText('action-message', 'Copy cancelled; no link was copied.');
      setText('footer-status', 'share link copy cancelled');
      return;
    }
    setText('action-message', 'Link shown in the dialog; copy it there.');
    setText('footer-status', 'share link shown for manual copying');
  }
  setText('action-time', '');
  history.replaceState(null, '', `?${params.toString()}`);
}

// ------------------------------------------------ presentation-only motion
// Sliding pill behind the active button of a group. The existing code still
// just toggles `.active`; a MutationObserver moves the pill after it does.
function attachSlider(group, itemSelector, pillClass) {
  if (!group || group.querySelector(`.${pillClass}`)) return;
  const pill = document.createElement('span');
  pill.className = pillClass;
  pill.setAttribute('aria-hidden', 'true');
  group.prepend(pill);
  group.classList.add('has-pill');
  const sync = (instant) => {
    const active = group.querySelector(`${itemSelector}.active`);
    if (!active || !active.offsetWidth) { pill.style.opacity = '0'; return; }
    if (instant) pill.classList.remove('pill-ready');
    pill.style.opacity = '1';
    pill.style.width = `${active.offsetWidth}px`;
    pill.style.transform = `translateX(${active.offsetLeft}px)`;
    if (instant) requestAnimationFrame(() => pill.classList.add('pill-ready'));
  };
  group.syncPill = sync;
  const mo = new MutationObserver(() => sync(false));
  group.querySelectorAll(itemSelector).forEach((b) =>
    mo.observe(b, { attributes: true, attributeFilter: ['class'], childList: true, characterData: true, subtree: true }));
  sync(true);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => sync(true));
}

// P10a: one-line summaries on the closed Target Control cards. They only read
// what renderStats() and the recovery code already wrote into the page.
function syncTcSummaries() {
  const t = (id) => (($(id) && $(id).textContent) || '').trim();
  const stats = $('tc-stats-sum');
  if (stats) {
    const exp = t('stat-exp');
    const snr = t('stat-snr');
    stats.textContent = exp && exp !== '--'
      ? `${exp}\u00b7${snr}`
      : '';
  }
  const rec = $('tc-recovery-sum');
  if (rec) {
    const rawRec = t('recovery-out');
    rec.textContent = !rawRec || /not run yet/i.test(rawRec)
      ? ''
      : rawRec.split('\n')[0].slice(0, 22);
  }
}

function attachTcSummaries() {
  const mo = new MutationObserver(syncTcSummaries);
  ['stat-exp', 'stat-epochs', 'stat-snr', 'recovery-out'].forEach((id) => {
    const el = $(id);
    if (el) mo.observe(el, { childList: true, characterData: true, subtree: true });
  });
  syncTcSummaries();
}

// P10a: the map box is now sized by its column (square, capped at 80vh), so
// re-fit the field whenever that box changes size, not only on window resize.
function attachMapFit() {
  const wrap = document.querySelector('.map-wrap');
  if (!wrap || !('ResizeObserver' in window)) return;
  let last = '';
  let raf = 0;
  new ResizeObserver(() => {
    const size = `${wrap.clientWidth}x${wrap.clientHeight}`;
    if (size === last) return;
    last = size;
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      if (state.data && state.viewMode !== 'sky') { fitView(); updateZoomLabel(); }
      draw();
    });
  }).observe(wrap);
}

function attachMotion() {
  attachTcSummaries();
  attachMapFit();
  ['view-switch', 'layer-switch', 'object-filter'].forEach((id) =>
    attachSlider($(id), '.seg', 'seg-pill'));
  attachSlider($('guide-tabs'), '.guide-tab', 'tab-pill');

  let resizeTimer = 0;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      ['view-switch', 'layer-switch', 'object-filter', 'guide-tabs'].forEach((id) => {
        const g = $(id);
        if (g && g.syncPill) g.syncPill(true);
      });
      // charts are drawn at their on-screen size, so repaint them
      if (state.data) {
        try {
          renderCharts(state.data);
          drawSed(state.data.sed);
          drawLightCurve(state.data.neowise || {}, state.data.epochs);
        } catch (err) { console.error('chart redraw failed', err); }
      }
    }, 150);
  });
}

function closeAllTooltips(except = null) {
  document.querySelectorAll('.tip-wrap.open').forEach((wrap) => {
    if (wrap === except) return;
    wrap.classList.remove('open');
    const btn = wrap.querySelector('[data-tip-toggle]');
    if (btn) btn.setAttribute('aria-expanded', 'false');
  });
}

let winZCounter = 410;

function focusToolWindow(win) {
  document.querySelectorAll('.win-dialog').forEach((d) => d.classList.remove('win-active'));
  if (!win) return;
  winZCounter += 1;
  win.style.zIndex = String(winZCounter);
  win.classList.add('win-active');
}

function closeToolWindow(win) {
  if (!win || win.classList.contains('hidden')) return;
  win.classList.add('hidden');
  win.classList.remove('win-active');
  const btn = document.querySelector(`[data-win="${win.id}"]`);
  if (btn) btn.setAttribute('aria-expanded', 'false');
}

function positionToolWindow(win, trigger) {
  const pad = 12;
  const baseWidth = win.id === 'win-history' ? 430 : 380;
  const width = Math.min(baseWidth, window.innerWidth - pad * 2);
  win.style.width = `${width}px`;
  if (win.dataset.positioned === '1') {
    const curLeft = parseFloat(win.style.left) || pad;
    const curTop = parseFloat(win.style.top) || 88;
    win.style.left = `${Math.max(pad, Math.min(curLeft, window.innerWidth - width - pad))}px`;
    win.style.top = `${Math.max(46, Math.min(curTop, window.innerHeight - 80))}px`;
    return;
  }
  const openCount = [...document.querySelectorAll('.win-dialog:not(.hidden)')]
    .filter((d) => d !== win).length;
  const cascade = openCount * 22;
  const rect = trigger ? trigger.getBoundingClientRect() : null;
  const desiredLeft = rect ? rect.right - width + cascade : window.innerWidth - width - 24 - cascade;
  const desiredTop = rect ? rect.bottom + 8 + cascade : 88 + cascade;
  win.style.left = `${Math.max(pad, Math.min(desiredLeft, window.innerWidth - width - pad))}px`;
  win.style.top = `${Math.max(46, Math.min(desiredTop, window.innerHeight - 180))}px`;
}

function toggleToolWindow(winId, trigger) {
  const win = $(winId);
  if (!win) return;
  if (!win.classList.contains('hidden')) {
    closeToolWindow(win);
    return;
  }
  win.classList.remove('hidden');
  if (trigger) trigger.setAttribute('aria-expanded', 'true');
  positionToolWindow(win, trigger);
  focusToolWindow(win);
}

function attachToolWindowsAndTips() {
  document.querySelectorAll('[data-win]').forEach((btn) => {
    btn.addEventListener('click', () => toggleToolWindow(btn.dataset.win, btn));
  });

  document.querySelectorAll('[data-win-close]').forEach((btn) => {
    btn.addEventListener('click', () => closeToolWindow($(btn.dataset.winClose)));
  });

  document.querySelectorAll('.win-dialog').forEach((win) => {
    win.addEventListener('pointerdown', () => focusToolWindow(win));
    const handle = win.querySelector('[data-drag-handle]');
    if (!handle) return;
    handle.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || e.target.closest('button')) return;
      e.preventDefault();
      focusToolWindow(win);
      const rect = win.getBoundingClientRect();
      const startX = e.clientX;
      const startY = e.clientY;
      const originLeft = rect.left;
      const originTop = rect.top;
      win.dataset.positioned = '1';
      const onMove = (ev) => {
        const maxLeft = Math.max(8, window.innerWidth - Math.min(rect.width, window.innerWidth - 16) - 8);
        const maxTop = Math.max(44, window.innerHeight - 44);
        win.style.left = `${Math.max(8, Math.min(originLeft + (ev.clientX - startX), maxLeft))}px`;
        win.style.top = `${Math.max(44, Math.min(originTop + (ev.clientY - startY), maxTop))}px`;
      };
      const onUp = () => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        window.removeEventListener('pointercancel', onUp);
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      window.addEventListener('pointercancel', onUp);
    });
  });

  document.querySelectorAll('[data-tip-toggle]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const wrap = btn.closest('.tip-wrap');
      if (!wrap) return;
      const opening = !wrap.classList.contains('open');
      closeAllTooltips(opening ? wrap : null);
      wrap.classList.toggle('open', opening);
      btn.setAttribute('aria-expanded', String(opening));
      if (opening) {
        const pop = wrap.querySelector('.tip-popover');
        if (pop) {
          pop.style.left = '0';
          pop.style.right = 'auto';
          const r = pop.getBoundingClientRect();
          if (r.right > window.innerWidth - 12) {
            pop.style.left = 'auto';
            pop.style.right = '0';
          }
        }
      }
    });
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.tip-wrap')) closeAllTooltips();
  });
}

function attachUiEvents() {
  $('btn-run').addEventListener('click', refreshSelectedTarget);
  $('btn-recovery').addEventListener('click', runRecovery);
  $('btn-demo').addEventListener('click', toggleDemo);
  $('btn-reset').addEventListener('click', resetAll);
  $('btn-export').addEventListener('click', copyShareLink);
  $('target-select').addEventListener('change', () => {
    if (state.demo) toggleDemo();
    refreshSelectedTarget();
  });

  document.querySelectorAll('#layer-switch .seg').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#layer-switch .seg').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      state.layer = btn.dataset.layer;
      const [lo, hi] = state.scaling[state.layer] || [0, 1];
      setText('map-hint',
        `${state.data ? state.data.common : ''} \u00b7 ${btn.textContent.replace(/\s+/g, ' ')} \u00b7 ` +
        `scale ${lo.toPrecision(3)} \u2026 ${hi.toPrecision(3)}`);
      draw();
    });
  });

  const stretchSel = $('stretch-select');
  if (stretchSel) {
    stretchSel.addEventListener('change', (e) => {
      state.stretch = e.target.value;
      if (state.data) { rebuildLayers(); draw(); }
    });
  }
  const lutSel = $('lut-select');
  if (lutSel) {
    lutSel.addEventListener('change', (e) => {
      state.lut = e.target.value;
      if (state.data) { rebuildLayers(); draw(); }
    });
  }

  document.querySelectorAll('#view-switch .seg').forEach((btn) => {
    btn.addEventListener('click', () => switchView(btn.dataset.view));
  });
  const mosaicBtn = $('btn-mosaic');
  if (mosaicBtn) {
    mosaicBtn.addEventListener('click', () => loadMosaic(true).then(draw));
  }
  const cloudRefreshForm = $('cloud-refresh-form');
  if (cloudRefreshForm) {
    cloudRefreshForm.addEventListener('submit', requestCloudRefresh);
  }
  const cloudFieldGrid = $('cloud-field-grid');
  if (cloudFieldGrid) {
    cloudFieldGrid.addEventListener('click', (event) => {
      const cell = event.target.closest('.field-cell');
      const sel = $('target-select');
      if (!cell || !sel) return;
      if (![...sel.options].some((o) => o.value === cell.dataset.target)) return;
      sel.value = cell.dataset.target;
      switchView('field');
      runPipeline();
      renderCloudFieldBoard(latestCloudRefreshStatus,
        latestCloudRefreshStatus.status === 'running');
    });
  }

  document.querySelectorAll('#object-filter .seg').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#object-filter .seg').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      state.objectFilter = btn.dataset.filter;
      drawObjectTable();
    });
  });
  $('object-search').addEventListener('input', (e) => {
    state.objectQuery = e.target.value;
    drawObjectTable();
  });

  // ------------------------------------------------ snapshot history controls
  const snapshotSelect = $('snapshot-select');
  if (snapshotSelect) {
    snapshotSelect.addEventListener('change', async (event) => {
      // Read the chosen manifest before stopping playback: stopSnapshotPlayback
      // re-renders the control (and therefore rewrites select.value) when a
      // replay is running.
      const manifest = event.target.value;
      stopSnapshotPlayback();
      await waitForPipeline();
      if (!manifest) {
        await runPipeline();
        return;
      }
      const snapshot = state.snapshotHistory.snapshots
        .find((snap) => snap.manifest === manifest);
      if (!snapshot) {
        renderSnapshotControls();
        return;
      }
      try {
        await loadSnapshotFrame(snapshot, { preserveView: !!state.data });
      } catch (error) {
        setText('footer-status', `snapshot unavailable: ${error.message}`);
        renderSnapshotControls();
      }
    });
  }
  const snapshotPlay = $('btn-snapshot-play');
  if (snapshotPlay) snapshotPlay.addEventListener('click', startSnapshotPlayback);

  // ------------------------------------------------------------- the guide
  const guide = $('guide');
  let guideOpener = null;
  const openGuide = (tab) => {
    if (!guide) return;
    guideOpener = document.activeElement;
    guide.classList.remove('hidden');
    if (tab) showGuideTab(tab);
    const tabs = $('guide-tabs');
    if (tabs && tabs.syncPill) tabs.syncPill(true);
    const first = guide.querySelector('.guide-tab');
    if (first) first.focus();
  };
  const closeGuide = () => {
    if (!guide || guide.classList.contains('hidden')) return;
    guide.classList.add('hidden');
    if (guideOpener && guideOpener.focus) guideOpener.focus();
    guideOpener = null;
  };

  // ------------------------------------------- cloud snapshot dialog (4.3)
  const cloudDialog = $('cloud-dialog');
  const cloudOpener = $('btn-cloud-status');
  if (cloudDialog && cloudOpener && cloudDialog.showModal) {
    cloudOpener.addEventListener('click', () => {
      if (!cloudDialog.open) cloudDialog.showModal();
    });
    const cloudClose = $('cloud-dialog-close');
    if (cloudClose) cloudClose.addEventListener('click', () => cloudDialog.close());
    // a click on the backdrop lands on the <dialog> itself, outside its box
    cloudDialog.addEventListener('click', (e) => {
      if (e.target !== cloudDialog) return;
      const r = cloudDialog.getBoundingClientRect();
      const inside = e.clientX >= r.left && e.clientX <= r.right
        && e.clientY >= r.top && e.clientY <= r.bottom;
      if (!inside) cloudDialog.close();
    });
    cloudDialog.addEventListener('close', () => cloudOpener.focus());
  }
  const btnGuide = $('btn-guide');
  if (btnGuide) btnGuide.addEventListener('click', () => openGuide('how'));
  const gc1 = $('guide-close');
  if (gc1) gc1.addEventListener('click', closeGuide);
  const gc2 = $('guide-close-2');
  if (gc2) gc2.addEventListener('click', closeGuide);
  if (guide) {
    guide.addEventListener('click', (e) => { if (e.target === guide) closeGuide(); });
    guide.addEventListener('keydown', (e) => trapFocus(guide, e));
    const tabs = [...document.querySelectorAll('.guide-tab')];
    tabs.forEach((tab, i) => {
      tab.addEventListener('click', () => showGuideTab(tab.dataset.tab));
      // WAI-ARIA tabs: arrow keys move between tabs, Home / End jump.
      tab.addEventListener('keydown', (e) => {
        const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
        let next = null;
        if (step) next = tabs[(i + step + tabs.length) % tabs.length];
        if (e.key === 'Home') next = tabs[0];
        if (e.key === 'End') next = tabs[tabs.length - 1];
        if (!next) return;
        e.preventDefault();
        showGuideTab(next.dataset.tab);
        next.focus();
      });
    });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (document.querySelector('.tip-wrap.open')) {
      closeAllTooltips();
      return;
    }
    const openWins = [...document.querySelectorAll('.win-dialog:not(.hidden)')];
    if (openWins.length) {
      openWins.sort((a, b) => (parseInt(a.style.zIndex || '400', 10) - parseInt(b.style.zIndex || '400', 10)));
      closeToolWindow(openWins[openWins.length - 1]);
      return;
    }
    closeGuide();
  });

  attachMotion();
  attachToolWindowsAndTips();

  const liveToggle = $('tog-live');
  if (liveToggle) {
    liveToggle.addEventListener('change', (e) => {
      if (!backendAvailable()) {
        e.target.checked = false;
        return;
      }
      state.live = e.target.checked;
      state.mosaic = null;
      if (state.viewMode === 'sky') loadMosaic(false).then(draw);
      setText('footer-status', state.live
        ? 'IRSA fresh mode: archive listing and cutouts fetched for each run'
        : 'snapshot mode: loading the configured field data source');
      refreshSelectedTarget();
    });
  }

  const fieldBtn = $('btn-field');
  if (fieldBtn) {
    fieldBtn.addEventListener('click', async () => {
      const ra = parseFloat($('field-ra').value);
      const dec = parseFloat($('field-dec').value);
      const name = $('field-name').value.trim();
      await openFieldAt(ra, dec, name);
    });
  }

  $('tog-grid').addEventListener('change', (e) => { state.overlays.grid = e.target.checked; draw(); });
  $('tog-cat').addEventListener('change', (e) => { state.overlays.catalog = e.target.checked; draw(); });
  $('tog-det').addEventListener('change', (e) => { state.overlays.detections = e.target.checked; draw(); });

  $('zoom-in').addEventListener('click', () => {
    const { w, h } = canvasSize();
    zoomAt(w / 2, h / 2, 1.25);
  });
  $('zoom-out').addEventListener('click', () => {
    const { w, h } = canvasSize();
    zoomAt(w / 2, h / 2, 1 / 1.25);
  });
  $('zoom-fit').addEventListener('click', () => { fitView(); updateZoomLabel(); draw(); });

  window.addEventListener('resize', () => {
    if (!state.data) return;
    draw();
  });

  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'SELECT' || e.target.tagName === 'INPUT') return;
    if (e.key === '+' || e.key === '=') $('zoom-in').click();
    if (e.key === '-') $('zoom-out').click();
    if (e.key === '0') $('zoom-fit').click();
    if (e.key === 'r') refreshSelectedTarget();
    if (e.key === 'g') { $('tog-grid').click(); }
    if (e.key === 'd') { $('tog-det').click(); }
  });
}

async function init() {
  attachV2Ui();
  attachUiEvents();
  attachMapEvents();
  attachHistoryEvents();
  historyRefresh();
  ['field-ra', 'field-dec', 'field-name', 'tog-live'].forEach((id) => {
    $(id)?.addEventListener('focus', warmLivePipeline);
    $(id)?.addEventListener('pointerenter', warmLivePipeline);
  });
  await startOfflineCopy();          // reads the saved copy's index (a few ms)
  syncBackendUi();
  // The refresh panel does not need the target list: load both at once.
  const refreshStatusLoaded = updateCloudRefreshStatus();
  await loadTargets();
  syncBackendUi();
  // Relabel the per-field grid with display names now that targets exist.
  renderCloudFieldBoard(latestCloudRefreshStatus,
    latestCloudRefreshStatus.status === 'running');
  const params = new URLSearchParams(window.location.search);
  const wanted = params.get('target');
  if (wanted) {
    const sel = $('target-select');
    if ([...sel.options].some((o) => o.value === wanted)) sel.value = wanted;
  }
  state.live = params.get('source') === 'live' && backendAvailable()
    && offline.mode !== 'offline';
  const liveToggle = $('tog-live');
  if (liveToggle) liveToggle.checked = state.live;
  // Check for a newer snapshot while the saved copy is already on screen.
  syncOfflineCopy();
  setStatus('STANDBY \u00b7 LOADING FIELD', 'busy');
  if (params.has('ra') && params.has('dec')) {
    await openFieldAt(Number(params.get('ra')), Number(params.get('dec')),
                      params.get('name') || '');
    await refreshStatusLoaded;
    return;
  }
  await runPipeline();
  await refreshStatusLoaded;
}

init();


// ------------------------------------------------------------------ charts

// Chart colours come from the CSS tokens, read once on first draw (the
// stylesheet has loaded by then). Only colours change, not what is plotted.
let THEME = null;
function chartTheme() {
  if (THEME) return THEME;
  const css = getComputedStyle(document.documentElement);
  const v = (name, fallback) => (css.getPropertyValue(name).trim() || fallback);
  // P8 s2: chart-specific tokens, falling back to the general ones
  THEME = {
    bg: v('--chart-bg', v('--panel-solid', '#0e1424')),
    grid: v('--chart-grid', v('--line', 'rgba(148,163,184,.16)')),
    text: v('--chart-text', v('--text-soft', '#b4c0d3')),
    muted: v('--chart-text', v('--muted', '#8592a8')),
    blue: v('--chart-blue', v('--blue', '#4c8dff')),
    green: v('--chart-green', v('--green', '#2fd47a')),
    red: v('--chart-red', '#ff5a3c'),
    amber: v('--chart-amber', v('--amber', '#c98a4b')),
    purple: v('--purple', '#a78bfa'),
    font: '13px "DM Mono", ui-monospace, Menlo, monospace',  /* P9 s3/s9: DM Mono 13px */
  };
  return THEME;
}

function chartFrame(id, title) {
  const canvas = $(id);
  if (!canvas) return null;
  const T = chartTheme();
  // Draw at the canvas's on-screen size so 12px text stays 12px when the
  // chart box is wider or narrower than the 380x150 default.
  const cssW = Math.round(canvas.clientWidth), cssH = Math.round(canvas.clientHeight);
  const dpr = window.devicePixelRatio || 1;
  const ctx = canvas.getContext('2d');
  let w = canvas.width, h = canvas.height;
  if (cssW > 0 && cssH > 0) {
    if (canvas.width !== Math.round(cssW * dpr)) canvas.width = Math.round(cssW * dpr);
    if (canvas.height !== Math.round(cssH * dpr)) canvas.height = Math.round(cssH * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    w = cssW; h = cssH;
  } else {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = T.bg;
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = T.grid;
  ctx.lineWidth = 1;
  for (let i = 1; i < 4; i++) {
    const y = (h - 26) * (i / 4) + 10;
    ctx.beginPath(); ctx.moveTo(30, y); ctx.lineTo(w - 8, y); ctx.stroke();
  }
  ctx.fillStyle = chartTheme().muted;
  ctx.font = chartTheme().font;
  ctx.textAlign = 'left';
  ctx.fillText(title, 30, 15);
  // plotTop leaves room for a value label above the tallest bar (12px text)
  return { ctx, w, h, plotBottom: h - 26, plotTop: 38, plotLeft: 30, plotRight: w - 8 };
}

function drawHistogram(hist) {
  const f = chartFrame('chart-hist', 'pixels per \u03c3 bin');
  if (!f || !hist || !hist.counts || !hist.counts.length) return;
  const { ctx, plotBottom, plotTop, plotLeft, plotRight } = f;
  const counts = hist.counts;
  const max = Math.max(...counts) || 1;
  const bw = (plotRight - plotLeft) / counts.length;
  counts.forEach((c, i) => {
    const x = plotLeft + i * bw;
    const hgt = (plotBottom - plotTop) * (c / max);
    const centre = hist.edges[i] + (hist.edges[i + 1] - hist.edges[i]) / 2;
    ctx.fillStyle = Math.abs(centre) > (hist.threshold || 6) ? chartTheme().red : chartTheme().blue;
    ctx.globalAlpha = Math.abs(centre) > (hist.threshold || 6) ? 0.9 : 0.6;
    ctx.fillRect(x + 0.5, plotBottom - hgt, Math.max(1, bw - 1), hgt);
  });
  ctx.globalAlpha = 1;
  ctx.fillStyle = chartTheme().muted;
  ctx.font = chartTheme().font;
  ctx.textAlign = 'center';
  const last = counts.length - 1;
  ctx.fillText(`${hist.edges[0]}\u03c3`, plotLeft + bw / 2, plotBottom + 13);
  ctx.fillText('0', plotLeft + (last / 2) * bw + bw / 2, plotBottom + 13);
  ctx.textAlign = 'right';  // keep the end label inside the canvas
  ctx.fillText(`${hist.edges[last]}\u03c3`, plotRight, plotBottom + 13);
}

function drawBars(id, labels, values, colour, unit) {
  const f = chartFrame(id, unit || '');
  if (!f) return;
  if (!values || !values.length) {
    f.ctx.fillStyle = chartTheme().muted;
    f.ctx.font = chartTheme().font;
    f.ctx.textAlign = 'center';
    f.ctx.fillText('no data yet', f.w / 2, f.h / 2);
    return;
  }
  const { ctx, plotBottom, plotTop, plotLeft, plotRight } = f;
  const max = Math.max(...values, 1e-9);
  const slotW = (plotRight - plotLeft) / values.length;
  const barW = Math.min(48, Math.max(8, slotW - 10));
  values.forEach((v, i) => {
    const hgt = (plotBottom - plotTop) * (v / max);
    const cx = plotLeft + i * slotW + slotW / 2;
    const bx = cx - barW / 2;
    ctx.fillStyle = colour;
    ctx.globalAlpha = 0.85;
    ctx.fillRect(bx, plotBottom - hgt, barW, hgt);
    ctx.globalAlpha = 1;
    ctx.fillStyle = chartTheme().text;
    ctx.font = chartTheme().font;
    ctx.textAlign = 'center';
    ctx.fillText(String(labels[i]), cx, plotBottom + 13);
    ctx.fillText(Number(v).toFixed(v < 10 ? 2 : 0), cx, plotBottom - hgt - 4);
  });
}

function drawRecoveryChart(grid) {
  const wrap = $('chart-recovery-wrap');
  const diag = $('diag-charts');
  const hasGrid = Boolean(grid && grid.length);
  if (wrap) wrap.classList.toggle('hidden', !hasGrid);
  if (diag) diag.classList.toggle('has-3', !hasGrid);
  if (!hasGrid) return;
  const mean = grid.reduce((a, g) => a + (g.recovery || 0), 0) / grid.length;
  chartLabel('chart-recovery', `Injection recovery: ${grid.length} cells, mean ${Math.round(mean * 100)}% ` +
    'of planted sources recovered; recovery rises with SNR. Open the Recovery window for the table.');
  const f = chartFrame('chart-recovery', 'recovered % vs SNR');
  if (!f) return;
  const { ctx, plotBottom, plotTop, plotLeft, plotRight } = f;
  const snrs = [...new Set(grid.map((g) => g.snr))].sort((a, b) => a - b);
  const mus = [...new Set(grid.map((g) => g.mu))].sort((a, b) => a - b);
  const colours = [chartTheme().blue, chartTheme().green, chartTheme().amber, chartTheme().red];
  mus.forEach((mu, k) => {
    ctx.strokeStyle = colours[k % colours.length];
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    snrs.forEach((snr, i) => {
      const cell = grid.find((g) => g.snr === snr && g.mu === mu);
      const v = cell ? cell.recovery : 0;
      const x = plotLeft + (plotRight - plotLeft) * (i / Math.max(1, snrs.length - 1));
      const y = plotBottom - (plotBottom - plotTop) * v;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = colours[k % colours.length];
    ctx.font = chartTheme().font;
    ctx.textAlign = 'left';
    ctx.fillText(`\u03bc${mu}\u2033`, plotLeft + 2 + k * 34, plotTop + 8);
  });
  ctx.fillStyle = chartTheme().muted;
  ctx.font = chartTheme().font;
  ctx.textAlign = 'center';
  snrs.forEach((snr, i) => {
    const x = plotLeft + (plotRight - plotLeft) * (i / Math.max(1, snrs.length - 1));
    ctx.fillText(String(snr), x, plotBottom + 13);
  });
}

function chartMessage(f, text) {
  f.ctx.fillStyle = chartTheme().muted;
  f.ctx.font = chartTheme().font;
  f.ctx.textAlign = 'center';
  f.ctx.fillText(text, f.w / 2, f.h / 2);
}

function scaler(lo, hi, a, b) {
  const span = hi - lo || 1;
  return (v) => a + (b - a) * ((v - lo) / span);
}

// W6: Gaia + AllWISE photometry and the SPHEREx aperture spectrum, log-log.
function drawSed(sed, canvasId = 'chart-sed') {
  const f = chartFrame(canvasId, 'flux density (mJy) vs λ (µm), log-log');
  if (!f) return;
  const cat = (sed && sed.points) || [];
  const sph = ((sed && sed.spherex) || []).filter((p) => p.mjy > 0);
  const all = cat.concat(sph).filter((p) => p.mjy > 0 && p.lam_um > 0);
  if (!all.length) { chartMessage(f, sed && sed.extended ? 'extended target: no single counterpart' : 'no photometry'); return; }
  const lx = all.map((p) => Math.log10(p.lam_um)), ly = all.map((p) => Math.log10(p.mjy));
  const X = scaler(Math.min(...lx) - 0.05, Math.max(...lx) + 0.05, f.plotLeft + 4, f.plotRight - 4);
  const Y = scaler(Math.min(...ly) - 0.2, Math.max(...ly) + 0.2, f.plotBottom, f.plotTop);
  const { ctx } = f;
  sph.forEach((p) => {
    ctx.fillStyle = chartTheme().purple;
    ctx.beginPath(); ctx.arc(X(Math.log10(p.lam_um)), Y(Math.log10(p.mjy)), 2.2, 0, Math.PI * 2); ctx.fill();
  });
  cat.forEach((p) => {
    if (!(p.mjy > 0)) return;
    const x = X(Math.log10(p.lam_um)), y = Y(Math.log10(p.mjy));
    ctx.fillStyle = p.band.startsWith('Gaia') ? chartTheme().blue : chartTheme().green;
    ctx.fillRect(x - 3, y - 3, 6, 6);
    ctx.font = chartTheme().font;
    ctx.textAlign = 'center';
    ctx.fillText(p.band.replace('Gaia ', ''), x, y - 6);
  });
  ctx.fillStyle = chartTheme().muted;
  ctx.font = chartTheme().font;
  ctx.textAlign = 'left';
  ctx.fillText('■ Gaia  ■ AllWISE  ● SPHEREx', f.plotLeft, f.plotBottom + 13);
}

// N1/N4: the target's WISE light curve, 2010-2024, with the SPHEREx epochs.
function drawLightCurve(history, epochs) {
  const f = chartFrame('chart-neowise', 'W1 / W2 visit means (mag), 2010–2024');
  if (!f) return;
  const nw = (history && history.target) || {};
  const visits = (nw && nw.visits) || [];
  if (!visits.length) {
    chartMessage(f, history && history.available
      ? 'NEOWISE query completed; no matching visits'
      : 'NEOWISE history unavailable or not refreshed');
    return;
  }
  const mags = [];
  visits.forEach((v) => { ['w1', 'w2'].forEach((b) => { if (v[b] != null) mags.push(v[b]); }); });
  const years = visits.map((v) => v.year).concat((epochs || []).map((e) => e.year).filter(Boolean));
  const X = scaler(Math.min(...years) - 0.3, Math.max(...years) + 0.3, f.plotLeft + 4, f.plotRight - 4);
  const Y = scaler(Math.max(...mags) + 0.1, Math.min(...mags) - 0.1, f.plotBottom, f.plotTop);  // bright up
  const { ctx } = f;
  (epochs || []).forEach((e) => {
    if (!e.year) return;
    ctx.strokeStyle = chartTheme().purple;
    ctx.globalAlpha = 0.5;
    ctx.setLineDash([2, 2]);
    ctx.beginPath(); ctx.moveTo(X(e.year), f.plotTop); ctx.lineTo(X(e.year), f.plotBottom); ctx.stroke();
  });
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
  [['w1', chartTheme().blue], ['w2', chartTheme().red]].forEach(([b, colour]) => {
    ctx.strokeStyle = colour; ctx.fillStyle = colour; ctx.lineWidth = 1;
    visits.forEach((v) => {
      if (v[b] == null) return;
      const x = X(v.year), y = Y(v[b]), e = v[b + '_err'] || 0;
      ctx.beginPath(); ctx.moveTo(x, Y(v[b] - e)); ctx.lineTo(x, Y(v[b] + e)); ctx.stroke();
      ctx.beginPath(); ctx.arc(x, y, 2.2, 0, Math.PI * 2); ctx.fill();
    });
  });
  ctx.fillStyle = chartTheme().muted;
  ctx.font = chartTheme().font;
  ctx.textAlign = 'left';
  const x0 = Math.min(...visits.map((v) => v.year)), x1 = Math.max(...visits.map((v) => v.year));
  ctx.fillText(`${x0.toFixed(0)}`, f.plotLeft, f.plotBottom + 13);
  ctx.textAlign = 'right';
  ctx.fillText(`${x1.toFixed(0)} · ● W1 ● W2 · dashed = SPHEREx`, f.plotRight, f.plotBottom + 13);
}

// NEOWISE/SED notes as label | value rows; the text of every note is unchanged.
// A ": " only splits when the part before it is a short label (W1, NEOWISE,
// Stored NEOWISE history), so a sentence that happens to contain ": " stays whole.
function notesTable(notes) {
  const row = (note) => {
    const pre = note.match(/^(W1 trend|WISE motion) (.*)$/s);
    if (pre) return `<tr><th>${fmt.escape(pre[1])}</th><td>${fmt.escape(pre[2])}</td></tr>`;
    const at = note.indexOf(': ');
    if (at > 0 && at <= 32) {
      return `<tr><th>${fmt.escape(note.slice(0, at))}</th><td>${fmt.escape(note.slice(at + 2))}</td></tr>`;
    }
    return `<tr><td colspan="2" class="kv-full">${fmt.escape(note)}</td></tr>`;
  };
  return `<table class="kv-table notes-table"><tbody>${notes.map(row).join('')}</tbody></table>`;
}

function renderHistory(data) {
  const nw = data.neowise || {};
  const sed = data.sed || {};
  const visits = (nw.target && nw.target.visits) || [];
  const hasSed = (sed.points || []).concat(sed.spherex || [])
    .some((p) => p.mjy > 0 && p.lam_um > 0);
  const hasNw = visits.length > 0;
  const hasWiseComp = (sed.wise_comparison || []).length > 0;

  const secHistory = $('sec-history');
  const historyCharts = $('history-charts');
  const sedWrap = $('chart-sed-wrap');
  const nwWrap = $('chart-neowise-wrap');
  if (sedWrap) sedWrap.classList.toggle('hidden', !hasSed);
  if (nwWrap) nwWrap.classList.toggle('hidden', !hasNw);
  if (historyCharts) historyCharts.classList.toggle('hidden', !hasSed && !hasNw);
  if (secHistory) secHistory.classList.toggle('hidden', !hasSed && !hasNw && !hasWiseComp);

  if (hasSed) drawSed(sed);
  if (hasNw) drawLightCurve(nw, data.epochs);
  if (hasSed) chartLabel('chart-sed', sedSummary(sed));
  if (hasNw) {
    const yrs = visits.map((v) => v.year).filter(Number.isFinite);
    chartLabel('chart-neowise', `Infrared light curve: ${visits.length} AllWISE/NEOWISE visits` +
      (yrs.length ? ` from ${Math.min(...yrs).toFixed(0)} to ${Math.max(...yrs).toFixed(0)}` : '') +
      '. Dashed lines mark the SPHEREx visits. The notes below give the numbers.');
  }

  const notes = [];
  const queriedAt = nw.generated_utc
    ? `; last queried ${formatTimestamp(nw.generated_utc)}`
    : '';
  if (hasNw || (nw.available && visits.length === 0)) {
    notes.push(hasNw
      ? `Stored NEOWISE history: ${visits.length} visits${queriedAt}` +
        (data.source === 'live' ? '; this live SPHEREx fetch did not refresh it.' : '.')
      : `NEOWISE query completed; no matching visits were returned${queriedAt}.`);
  }
  (sed.wise_comparison || []).forEach((c) => {
    notes.push(`${c.band}: SPHEREx ${c.spherex_mjy} mJy vs AllWISE ${c.wise_mjy} mJy ` +
      `(ratio ${c.ratio}, Δ ${c.delta_mag > 0 ? '+' : ''}${c.delta_mag} mag over ${c.years_between ?? '?'} yr)`);
  });
  const s = nw.target && nw.target.summary;
  if (s && s.n_visits) {
    const w1 = s.w1 || {}, w2 = s.w2 || {};
    notes.push(`NEOWISE: ${s.n_visits} visits · W1 amp ${w1.amplitude ?? '--'} (χ²ᵣ ${w1.chi2_red ?? '--'}) ` +
      `· W2 amp ${w2.amplitude ?? '--'} (χ²ᵣ ${w2.chi2_red ?? '--'}) · ${s.variable ? 'VARIABLE' : 'stable'}`);
    if (w1.trend_mag_per_yr != null) notes.push(`W1 trend ${w1.trend_mag_per_yr} ± ${w1.trend_err} mag/yr`);
    if (s.pm_total_masyr != null) {
      notes.push(`WISE motion ${fmt.num(s.pm_total_masyr / 1000, 3)} ± ${fmt.num(s.pm_err_masyr / 1000, 3)}″/yr` +
        (s.pm_significant ? ' (significant)' : ''));
    }
  }
  if (sed.note && hasWiseComp) notes.push(sed.note);
  $('history-out').innerHTML = notes.length ? notesTable(notes) : '';

  const movers = data.expected_movers || [];
  const tbody = $('movers-body');
  if (!movers.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">No catalogued star moves ≥ 1 map pixel between the first and last epoch</td></tr>';
    return;
  }
  tbody.innerHTML = movers.map((m) => `<tr>
      <td>${m.is_target ? '<b>target</b>' : fmt.escape(m.cat)}${m.name ? ' · ' + fmt.escape(m.name) : ''}</td>
      <td>${fmt.num(m.pm_masyr / 1000, 2)}</td>
      <td>${fmt.num(m.displacement_arcsec, 1)}${m.resolvable ? '' : ' <span class="obj-note">&lt; PSF</span>'}</td>
      <td>${m.mag != null ? fmt.num(m.mag, 1) : '—'}</td>
      <td>${m.detected ? `yes · ${fmt.escape(m.det_kind)} ${m.det_snr}σ` : 'not seen'}</td>
      <td>${fmt.num(m.ra, 4)}, ${fmt.num(m.dec, 4)}</td>
    </tr>`).join('');
}

function renderCharts(data) {
  drawHistogram(data.histogram);

  const labels = [];
  const sigmas = [];
  (data.epoch_pairs || []).forEach((p) => {
    labels.push(p.pair);
    sigmas.push(p.sigma_a * 1000);
  });
  drawBars('chart-noise', labels, sigmas, chartTheme().blue, '\u03c3 \u00d7 10\u207b\u00b3 MJy/sr');
  drawBars('chart-pairs',
           (data.epoch_pairs || []).map((p) => p.pair),
           (data.epoch_pairs || []).map((p) => p.n_detections),
           chartTheme().green, 'detections per pair');
  drawRecoveryChart(state.recovery);

  // Text alternatives for the canvas charts (WCAG 1.1.1).
  const h = data.histogram;
  if (h && h.counts && h.counts.length) {
    const total = h.counts.reduce((a, b) => a + b, 0);
    const beyond = h.counts.reduce((a, c, i) => {
      const centre = h.edges[i] + (h.edges[i + 1] - h.edges[i]) / 2;
      return a + (Math.abs(centre) > (h.threshold || 6) ? c : 0);
    }, 0);
    chartLabel('chart-hist', `Significance histogram: ${total} pixels from ${h.edges[0]} to ` +
      `${h.edges[h.edges.length - 1]} sigma; ${beyond} pixels lie beyond the ` +
      `${h.threshold || 6} sigma detection threshold (shown in red).`);
  } else {
    chartLabel('chart-hist', 'Significance histogram: no data.');
  }
  const pairs = data.epoch_pairs || [];
  chartLabel('chart-noise', pairs.length
    ? `Noise per epoch pair: ${pairs.map((p) => `${p.pair} ${(p.sigma_a * 1000).toFixed(1)}`).join(', ')} (thousandths of MJy/sr).`
    : 'Noise per epoch: no data.');
  chartLabel('chart-pairs', pairs.length
    ? `Detections per epoch pair: ${pairs.map((p) => `${p.pair} ${p.n_detections}`).join(', ')}.`
    : 'Detections per epoch pair: no data.');
}
