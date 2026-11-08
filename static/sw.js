// SPHERExLens offline copy -- service worker.
//
// 1. Website shell (HTML, JS, CSS, fonts): network first, saved copy when the
//    network is down or slower than SHELL_TIMEOUT_MS.
// 2. Cloud snapshot reads (targets, dashboard, status, recovery, mosaic):
//    served from the saved copy that app.js keeps in sync with
//    /api/snapshot-index. Every saved object is keyed by its content-addressed
//    snapshot object name, so a saved copy can never be mislabelled.
// 3. Everything else -- fresh IRSA requests (source=live), progress streams,
//    the refresh trigger and status, the snapshot index -- goes to the network
//    untouched and is never stored.

const SHELL_CACHE = 'spx-shell-v8';
const DATA_CACHE = 'spx-data-v1';
const META_URL = '/__spx/meta';
const SHELL_TIMEOUT_MS = 4000;
const SHELL_FILES = ['./', 'index.html', 'app.js', 'style.css',
  'assets/icons/logo-spherex-lens.svg'];
const DATA_PATH = /\/(?:api|live-api)\/(targets|dashboard|status|recovery|mosaic)$/;
const FONT_HOSTS = new Set(['fonts.googleapis.com', 'fonts.gstatic.com']);

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    // One missing file must not stop the worker from installing.
    await Promise.allSettled(SHELL_FILES.map((file) =>
      cache.add(new Request(file, { cache: 'reload' }))));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keep = new Set([SHELL_CACHE, DATA_CACHE]);
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n.startsWith('spx-') && !keep.has(n))
      .map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

// Same key scheme as app.js (offlineKey there).
function dataKey(kind, target, meta) {
  const keys = meta && meta.keys;
  if (!keys) return null;
  return keys[target ? `${kind}:${target}` : kind] || null;
}

async function readMeta() {
  const cache = await caches.open(DATA_CACHE);
  const response = await cache.match(META_URL);
  if (!response) return null;
  try { return await response.json(); } catch { return null; }
}

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', 'X-SPX-Source': 'offline-copy' },
  });
}

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout')), ms);
    promise.then((value) => { clearTimeout(timer); resolve(value); },
      (error) => { clearTimeout(timer); reject(error); });
  });
}

async function fromSavedCopy(kind, target) {
  const meta = await readMeta();
  const key = dataKey(kind, target, meta);
  if (!key) return null;
  const cache = await caches.open(DATA_CACHE);
  const saved = await cache.match(key);
  if (!saved) return null;
  const headers = new Headers(saved.headers);
  headers.set('X-SPX-Source', 'offline-copy');
  headers.set('X-SPX-Snapshot-Published', meta.published_at || '');
  return new Response(saved.body, { status: saved.status, headers });
}

// A response labelled by the server with its snapshot object can be saved
// under that name straight away: the name identifies the content exactly.
async function saveLabelled(kind, target, response) {
  const object = response.headers.get('X-Snapshot-Object');
  const manifest = response.headers.get('X-Snapshot-Manifest');
  let key = null;
  if (object && kind !== 'status') key = `/__spx/obj/${kind}/${encodeURIComponent(object)}`;
  else if (manifest && kind === 'status' && target) {
    key = `/__spx/obj/status/${encodeURIComponent(target)}/${encodeURIComponent(manifest)}`;
  }
  if (!key || response.status !== 200) return;
  const text = await response.text();
  JSON.parse(text);                       // only complete JSON is ever saved
  const cache = await caches.open(DATA_CACHE);
  await cache.put(key, new Response(text, {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
      'X-Snapshot-Object': object || '',
      'X-Snapshot-Manifest': manifest || '',
      'X-SPX-Saved-At': new Date().toISOString(),
    },
  }));
}

async function handleData(event, kind, target) {
  const saved = await fromSavedCopy(kind, target);
  if (saved) return saved;              // instant; app.js re-syncs right after
  try {
    const response = await fetch(event.request);
    event.waitUntil(saveLabelled(kind, target, response.clone()).catch(() => {}));
    return response;
  } catch (error) {
    return jsonResponse(503, {
      error: 'Offline, and this data is not saved on this device yet.',
      hint: 'Open the site once while connected; the offline copy is saved automatically.',
      offline: true,
    });
  }
}

async function handleShell(request) {
  const cache = await caches.open(SHELL_CACHE);
  try {
    const response = await withTimeout(fetch(request), SHELL_TIMEOUT_MS);
    if (response.ok) await cache.put(request, response.clone());
    return response;
  } catch (error) {
    const saved = await cache.match(request, { ignoreSearch: request.mode === 'navigate' })
      || (request.mode === 'navigate' ? await cache.match('index.html') : null);
    if (saved) return saved;
    throw error;
  }
}

async function handleFont(request) {
  const cache = await caches.open(SHELL_CACHE);
  const saved = await cache.match(request);
  const network = fetch(request).then((response) => {
    if (response.ok || response.type === 'opaque') cache.put(request, response.clone());
    return response;
  });
  if (saved) {
    network.catch(() => {});
    return saved;
  }
  return network;
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);

  if (FONT_HOSTS.has(url.hostname)) {
    event.respondWith(handleFont(request));
    return;
  }
  if (url.origin !== self.location.origin) return;

  const match = url.pathname.match(DATA_PATH);
  if (match) {
    const live = (url.searchParams.get('source') || '').toLowerCase() === 'live';
    if (live || request.headers.get('X-SPX-Sync')) return;   // straight to network
    const kind = match[1];
    const target = kind === 'targets' || kind === 'mosaic'
      ? null : (url.searchParams.get('target') || 'WISE_0855');
    event.respondWith(handleData(event, kind, target));
    return;
  }
  if (url.pathname.startsWith('/__spx/')) return;
  if (url.pathname.includes('/api/') || url.pathname.startsWith('/live-api/')
      || url.pathname.startsWith('/data-live-api/')) return;

  if (request.mode === 'navigate' || /\.(?:js|css|html|png|svg|ico|woff2?)$/.test(url.pathname)
      || url.pathname === '/') {
    event.respondWith(handleShell(request));
  }
});
