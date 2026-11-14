// Regression test for static/sw.js: a historical dashboard is cached per
// (field, snapshot manifest).  Before the fix the key was the manifest alone,
// so once WISE 0855's first frame was saved, every other field's Play/dropdown
// selection for that same manifest was served WISE 0855's dashboard.
//
// Run with:  node --test tests/test_service_worker.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const ORIGIN = 'https://spherex.test';
const SW_SOURCE = readFileSync(new URL('../static/sw.js', import.meta.url), 'utf8');

class FakeCache {
  constructor() { this.items = new Map(); }
  key(request) {
    return typeof request === 'string' ? new URL(request, ORIGIN).href : request.url;
  }
  async match(request) {
    const saved = this.items.get(this.key(request));
    return saved ? saved.clone() : undefined;
  }
  async put(request, response) {
    this.items.set(this.key(request), response.clone());
  }
  async delete(request) { return this.items.delete(this.key(request)); }
  async keys() { return [...this.items.keys()].map((url) => new Request(url)); }
  async add(request) { await this.put(request, await network(request)); }
}

class FakeCacheStorage {
  constructor() { this.caches = new Map(); }
  async open(name) {
    if (!this.caches.has(name)) this.caches.set(name, new FakeCache());
    return this.caches.get(name);
  }
  async keys() { return [...this.caches.keys()]; }
  async delete(name) { return this.caches.delete(name); }
  async match(request) {
    for (const cache of this.caches.values()) {
      const hit = await cache.match(request);
      if (hit) return hit;
    }
    return undefined;
  }
}

// The fake network returns a dashboard whose target equals the URL's target,
// so a cache mix-up is directly visible in the body.
const networkCalls = [];
async function network(request) {
  const url = typeof request === 'string' ? new URL(request, ORIGIN) : new URL(request.url);
  networkCalls.push(url.href);
  const target = url.searchParams.get('target') || 'unknown';
  const manifest = url.searchParams.get('snapshot') || '';
  return new Response(JSON.stringify({ target, manifest }), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
      'X-Snapshot-Object': `snapshots/dashboards/${target}/object.json`,
      'X-Snapshot-Manifest': manifest,
    },
  });
}

function loadWorker() {
  const listeners = {};
  const self = {
    location: new URL(`${ORIGIN}/sw.js`),
    addEventListener(type, fn) { listeners[type] = fn; },
    skipWaiting: async () => {},
    clients: { claim: async () => {} },
  };
  const sandbox = {
    self,
    caches: new FakeCacheStorage(),
    fetch: network,
    Request,
    Response,
    Headers,
    URL,
    console,
    setTimeout,
    clearTimeout,
  };
  vm.runInNewContext(SW_SOURCE, sandbox, { filename: 'sw.js' });
  return listeners.fetch;
}

async function requestFrame(onFetch, target, manifest) {
  const url = `${ORIGIN}/live-api/dashboard?target=${encodeURIComponent(target)}` +
    `&snapshot=${encodeURIComponent(manifest)}`;
  const event = {
    request: new Request(url),
    pending: [],
    respondWith(promise) { this.response = promise; },
    waitUntil(promise) { this.pending.push(promise); },
  };
  onFetch(event);
  if (!event.response) return { response: null, body: null };
  const response = await event.response;
  await Promise.allSettled(event.pending);
  const body = await response.json();
  return { response, body };
}

test('a historical frame is cached and replayed per field, not per manifest', async () => {
  const onFetch = loadWorker();
  const manifest = 'manifests/0123456789abcdef.json';

  const first = await requestFrame(onFetch, 'WISE_0855', manifest);
  assert.equal(first.body.target, 'WISE_0855');

  // Same published manifest, different field: must NOT reuse WISE 0855.
  const second = await requestFrame(onFetch, 'Barnards_Star', manifest);
  assert.equal(second.body.target, 'Barnards_Star');

  // The first field's frame is still a correct offline hit (no extra hop).
  const callsBefore = networkCalls.length;
  const third = await requestFrame(onFetch, 'WISE_0855', manifest);
  assert.equal(third.body.target, 'WISE_0855');
  assert.equal(third.response.headers.get('X-SPX-Source'), 'offline-copy');
  assert.equal(networkCalls.length, callsBefore, 'saved frame should not hit the network');

  // A different manifest for the same field is a different frame.
  const fourth = await requestFrame(onFetch, 'WISE_0855', 'manifests/fedcba9876543210.json');
  assert.equal(fourth.body.manifest, 'manifests/fedcba9876543210.json');
});
