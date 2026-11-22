/**
 * server.js
 *
 * Node.js (Express) backend for the SPHERExLens Discovery Triage Portal.
 * Serves the vanilla JS/HTML/CSS frontend from ./static and exposes all /api/*
 * routes by forwarding snapshot reads to the published Cloud Run snapshot API,
 * streaming live IRSA pipeline endpoints (SSE) to the live pipeline service,
 * and providing local fallbacks for evaluation metrics, model card, credits,
 * health checks, and public voting.
 */

import fs from 'node:fs';
import path from 'node:path';
import { Readable } from 'node:stream';
import { fileURLToPath } from 'node:url';
import compression from 'compression';
import express from 'express';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = Number(process.env.PORT || 3000);
const HOST = '0.0.0.0';
const STARTED_AT_MS = Date.now();

const SNAPSHOT_API = (
  process.env.SPHEREX_SNAPSHOT_API ||
  'https://spherex-lens-api-m6cqoypnxa-uc.a.run.app/api'
).replace(/\/+$/, '');

const LIVE_PIPELINE_API = (
  process.env.SPHEREX_LIVE_API ||
  'https://spherex-lens-live-m6cqoypnxa-uc.a.run.app/api'
).replace(/\/+$/, '');

const EVAL_DIR = path.resolve(__dirname, process.env.SPHEREX_EVAL_DIR || 'eval');
const MODEL_DIR = path.resolve(__dirname, process.env.SPHEREX_ML_MODEL_DIR || 'ml/models');
const STATIC_DIR = path.resolve(__dirname, 'static');
const FIGURES_DIR = path.resolve(__dirname, 'figures');

const PIPELINE_STAMP = {
  pipeline_version: '2.0.0-dev',
  config_hash: 'd8d398effb40',
};

const VALID_VOTES = new Set(['real', 'bogus', 'unsure']);
const TARGET_NAME_RE = /^[A-Za-z0-9_]{1,80}$/;
const DETECTION_ID_RE = /^[A-Za-z0-9_.:,\-\u2192]{1,120}$/;

// In-memory vote fallback when upstream vote storage is unavailable or read-only.
// Maps detection_id -> { real: number, bogus: number, unsure: number }
const memoryVotes = new Map();

const FORWARDED_RESPONSE_HEADERS = [
  'content-type',
  'content-disposition',
  'x-snapshot-object',
  'x-snapshot-manifest',
  'x-pipeline-version',
  'x-config-hash',
  'retry-after',
];

const CREDITS_FALLBACK = {
  checked: '2026-11-13',
  note: 'wording_verified=false: check wording_source before quoting.',
  sources: [
    {
      id: 'irsa_spherex',
      name: 'SPHEREx (NASA/IPAC Infrared Science Archive)',
      used_for: 'SPHEREx spectral images (SIA2 cutouts), the core data of every dashboard',
      acknowledgement:
        'This publication makes use of data products from the Spectro-Photometer for the History of the Universe, Epoch of Reionization and Ices Explorer (SPHEREx), which is a joint project of the Jet Propulsion Laboratory and the California Institute of Technology, and is funded by the National Aeronautics and Space Administration.',
      citation: 'Cite the IRSA DOI of the SPHEREx data release used (listed in the IRSA SPHEREx documentation).',
      licence: 'NASA data: no copyright restrictions; acknowledgement requested',
      url: 'https://irsa.ipac.caltech.edu/Missions/spherex.html',
      wording_verified: true,
    },
    {
      id: 'irsa',
      name: 'NASA/IPAC Infrared Science Archive (IRSA)',
      used_for: 'SIA2 image search, TAP catalogue queries (AllWISE, NEOWISE), finder charts',
      acknowledgement:
        'This research has made use of the NASA/IPAC Infrared Science Archive, which is funded by the National Aeronautics and Space Administration and operated by the California Institute of Technology.',
      licence: 'NASA data: no copyright restrictions; acknowledgement requested',
      url: 'https://irsa.ipac.caltech.edu/',
      wording_verified: true,
    },
    {
      id: 'irsa_wise',
      name: 'WISE / AllWISE (NASA/IPAC IRSA)',
      used_for: 'AllWISE catalogue cross-matching and colours',
      acknowledgement:
        'This publication makes use of data products from the Wide-field Infrared Survey Explorer, which is a joint project of the University of California, Los Angeles, and the Jet Propulsion Laboratory/California Institute of Technology, funded by the National Aeronautics and Space Administration.',
      citation: 'Wright et al. 2010, AJ, 140, 1868',
      licence: 'NASA data: no copyright restrictions; acknowledgement requested',
      url: 'https://irsa.ipac.caltech.edu/Missions/wise.html',
      wording_verified: true,
    },
    {
      id: 'irsa_neowise',
      name: 'NEOWISE (NASA/IPAC IRSA)',
      used_for: 'NEOWISE single-exposure time series (motion consistency)',
      acknowledgement:
        'This publication also makes use of data products from NEOWISE, which is a project of the Jet Propulsion Laboratory/California Institute of Technology, funded by the Planetary Science Division of the National Aeronautics and Space Administration.',
      citation: 'Mainzer et al. 2011, ApJ, 731, 53; Mainzer et al. 2014, ApJ, 792, 30',
      licence: 'NASA data: no copyright restrictions; acknowledgement requested',
      url: 'https://irsa.ipac.caltech.edu/Missions/wise.html',
      wording_verified: true,
    },
    {
      id: 'gaia',
      name: 'ESA Gaia (DR3)',
      used_for: 'Astrometry and proper motions of known stars',
      acknowledgement:
        'This work has made use of data from the European Space Agency (ESA) mission Gaia (https://www.cosmos.esa.int/gaia), processed by the Gaia Data Processing and Analysis Consortium (DPAC).',
      citation: 'Gaia Collaboration et al. 2016; Gaia Collaboration et al. 2023',
      licence: 'CC BY-NC 3.0 IGO (Gaia data licence)',
      url: 'https://www.cosmos.esa.int/web/gaia-users/credits',
      wording_verified: true,
    },
    {
      id: 'mast',
      name: 'STScI Mikulski Archive for Space Telescopes (MAST)',
      used_for: 'Gaia/MAST portal links for each detection',
      acknowledgement:
        'This research has made use of data obtained from the Mikulski Archive for Space Telescopes (MAST) at the Space Telescope Science Institute.',
      licence: 'Public archive; see MAST data attribution guidance',
      url: 'https://archive.stsci.edu/publishing/mission-acknowledgements',
      wording_verified: false,
    },
    {
      id: 'jpl_ssd',
      name: 'NASA/JPL Solar System Dynamics (SBDB, SB Identification)',
      used_for: 'Known-asteroid check (sb_ident) and small-body lookups (SBDB)',
      acknowledgement: 'NASA/JPL Solar System Dynamics. https://ssd.jpl.nasa.gov',
      licence: 'U.S. Government work; citation requested',
      url: 'https://ssd.jpl.nasa.gov/',
      wording_verified: true,
    },
    {
      id: 'cadc_ssois',
      name: 'CADC Solar System Object Image Search (SSOIS)',
      used_for: 'Independent asteroid ephemeris check and image search links',
      acknowledgement:
        'Asteroid image search by the Solar System Object Image Search (SSOIS) of the Canadian Astronomy Data Centre (Gwyn, Hill & Kavelaars 2012).',
      citation: 'Gwyn, Hill & Kavelaars 2012, PASP, 124, 579',
      licence: 'CADC terms of use',
      url: 'https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/en/ssois/',
      wording_verified: true,
    },
    {
      id: 'neossat',
      name: 'Canadian Space Agency NEOSSat (via CADC)',
      used_for: 'Follow-up frame search for candidates',
      acknowledgement:
        'Contains information licensed under the Open Government Licence - Canada. NEOSSat data courtesy of the Canadian Space Agency, obtained through the Canadian Astronomy Data Centre.',
      licence: 'Open Government Licence - Canada',
      url: 'https://data.asc-csa.gc.ca/dataset/neossat',
      wording_verified: true,
    },
    {
      id: 'vsx',
      name: 'AAVSO International Variable Star Index (VSX), via CDS VizieR (B/vsx)',
      used_for: 'Known variable stars',
      acknowledgement:
        'This research made use of the International Variable Star Index (VSX) database, operated at AAVSO, Cambridge, Massachusetts, USA.',
      citation: 'Watson, Henden & Price 2006, SASS, 25, 47',
      licence: 'AAVSO VSX terms; CDS/VizieR conditions of use',
      url: 'https://www.aavso.org/vsx/',
      wording_verified: false,
    },
    {
      id: 'vizier',
      name: 'CDS VizieR',
      used_for: 'Access to the VSX catalogue',
      acknowledgement:
        'This research has made use of the VizieR catalogue access tool, CDS, Strasbourg, France (DOI: 10.26093/cds/vizier).',
      licence: 'CDS conditions of use',
      url: 'https://vizier.cds.unistra.fr/',
      wording_verified: false,
    },
    {
      id: 'wiseview',
      name: 'WiseView / Backyard Worlds: Planet 9',
      used_for: 'Per-detection WiseView flip-book links (byw.tools)',
      acknowledgement:
        'This research has made use of WiseView (Caselden et al. 2018), a tool developed for the Backyard Worlds: Planet 9 citizen-science project (Kuchner et al. 2017).',
      citation: 'Caselden et al. 2018, ascl:1806.004; Kuchner et al. 2017, ApJL, 841, L19',
      licence: 'Linked tool; no data redistributed',
      url: 'http://byw.tools/wiseview',
      wording_verified: false,
    },
  ],
};

function readJsonFile(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
  } catch {
    return null;
  }
}

function loadLocalModelCard() {
  const latest = readJsonFile(path.join(MODEL_DIR, 'LATEST.json'));
  if (!latest || !latest.json) return null;
  const card = readJsonFile(path.join(MODEL_DIR, latest.json));
  if (!card) return null;
  const mdPath = path.resolve(MODEL_DIR, '..', 'MODEL_CARD.md');
  try {
    card.model_card_markdown = fs.readFileSync(mdPath, 'utf-8');
  } catch {
    // Optional markdown card
  }
  delete card.medians;
  return card;
}

function buildUpstreamUrl(baseUrl, subPath, originalUrl) {
  const queryIndex = originalUrl.indexOf('?');
  const query = queryIndex >= 0 ? originalUrl.slice(queryIndex) : '';
  const cleanSub = subPath.startsWith('/') ? subPath : `/${subPath}`;
  return `${baseUrl}${cleanSub}${query}`;
}

function recordMemoryVote(detectionId, vote) {
  const tally = memoryVotes.get(detectionId) || { real: 0, bogus: 0, unsure: 0 };
  tally[vote] = (tally[vote] || 0) + 1;
  memoryVotes.set(detectionId, tally);
  return tally;
}

async function proxyRequest(req, res, baseUrl, subPath, options = {}) {
  const {
    stream = false,
    timeoutMs = stream ? 300_000 : 60_000,
    noStore = true,
  } = options;

  const targetUrl = buildUpstreamUrl(baseUrl, subPath, req.originalUrl);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  // Abort upstream if the browser disconnects mid-stream
  const onClose = () => controller.abort();
  res.on('close', onClose);

  try {
    const headers = {
      'user-agent': 'spherex-lens-node/2.0',
    };
    if (req.headers.accept) headers.accept = req.headers.accept;
    if (req.headers['x-refresh-token']) {
      headers['x-refresh-token'] = req.headers['x-refresh-token'];
    } else if (subPath === '/refresh' && process.env.SPHEREX_REFRESH_OPERATOR_TOKEN) {
      headers['x-refresh-token'] = process.env.SPHEREX_REFRESH_OPERATOR_TOKEN;
    }

    const fetchInit = {
      method: req.method,
      headers,
      redirect: 'manual',
      signal: controller.signal,
    };

    if (req.method !== 'GET' && req.method !== 'HEAD' && req.body !== undefined) {
      headers['content-type'] = 'application/json';
      fetchInit.body = typeof req.body === 'string' ? req.body : JSON.stringify(req.body);
    }

    const upstream = await fetch(targetUrl, fetchInit);

    for (const headerName of FORWARDED_RESPONSE_HEADERS) {
      const value = upstream.headers.get(headerName);
      if (value) res.setHeader(headerName, value);
    }

    const contentType = upstream.headers.get('content-type') || '';
    const isSse = stream || contentType.includes('text/event-stream');

    if (isSse) {
      res.setHeader('Cache-Control', 'no-cache, no-transform');
      res.setHeader('X-Accel-Buffering', 'no');
      res.setHeader('Connection', 'keep-alive');
    } else if (noStore) {
      res.setHeader('Cache-Control', 'no-store, no-cache, max-age=0');
      res.setHeader('Pragma', 'no-cache');
    }

    res.status(upstream.status);

    if (upstream.body && isSse) {
      if (typeof res.flushHeaders === 'function') res.flushHeaders();
      const nodeStream = Readable.fromWeb(upstream.body);
      nodeStream.on('error', () => {
        if (!res.writableEnded) res.end();
      });
      nodeStream.pipe(res);
      await new Promise((resolve) => {
        res.once('finish', resolve);
        res.once('close', resolve);
      });
      return;
    }

    const buffer = Buffer.from(await upstream.arrayBuffer());
    res.send(buffer);
  } catch (error) {
    if (res.headersSent) {
      if (!res.writableEnded) res.end();
      return;
    }
    const isAbort = error && error.name === 'AbortError';
    res.status(503).json({
      error: isAbort
        ? 'The upstream SPHERExLens service timed out.'
        : 'Cannot reach the SPHERExLens Cloud API.',
      detail: error instanceof Error ? error.message : String(error),
      hint: 'Check the network connection or retry in a moment.',
    });
  } finally {
    clearTimeout(timer);
    res.off('close', onClose);
  }
}

const app = express();
app.disable('x-powered-by');

// Gzip JSON & static payloads, skipping SSE event streams
app.use(
  compression({
    threshold: 1024,
    filter: (req, res) => {
      if (req.path.endsWith('/stream')) return false;
      const ct = String(res.getHeader('Content-Type') || '');
      if (ct.includes('text/event-stream')) return false;
      return compression.filter(req, res);
    },
  })
);

app.use(express.json({ limit: '1mb' }));

// Optional CORS allow-list
const corsOrigins = (process.env.SPHEREX_CORS_ORIGINS || '')
  .split(',')
  .map((o) => o.trim().replace(/\/+$/, ''))
  .filter(Boolean);

if (corsOrigins.length > 0) {
  app.use((req, res, next) => {
    const origin = (req.headers.origin || '').replace(/\/+$/, '');
    if (origin && corsOrigins.includes(origin)) {
      res.setHeader('Access-Control-Allow-Origin', origin);
      res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
      res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-Refresh-Token');
      res.setHeader('Access-Control-Expose-Headers', 'X-Snapshot-Object, X-Snapshot-Manifest');
      if (req.method === 'OPTIONS') return res.sendStatus(204);
    }
    next();
  });
}

// ---------------------------------------------------------------------------
// Health & Local-Fallback Metadata Endpoints
// ---------------------------------------------------------------------------

app.get('/api/health/live', (_req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.json({
    ok: true,
    pipeline: PIPELINE_STAMP,
    uptime_s: Number(((Date.now() - STARTED_AT_MS) / 1000).toFixed(1)),
    runtime: 'node',
  });
});

app.get('/api/health', async (req, res) => {
  try {
    const upstream = await fetch(`${SNAPSHOT_API}/health`, {
      signal: AbortSignal.timeout(8000),
    });
    if (upstream.ok) {
      const data = await upstream.json();
      res.setHeader('Cache-Control', 'no-store');
      return res.json(data);
    }
  } catch {
    // Fall through to local status payload
  }
  res.setHeader('Cache-Control', 'no-store');
  res.json({
    ok: true,
    pipeline: PIPELINE_STAMP,
    snapshot: { status: 'forwarded', detail: `Proxying to ${SNAPSHOT_API}` },
    uptime_s: Number(((Date.now() - STARTED_AT_MS) / 1000).toFixed(1)),
  });
});

app.get('/api/metrics', async (req, res) => {
  try {
    const upstream = await fetch(`${SNAPSHOT_API}/metrics`, {
      signal: AbortSignal.timeout(8000),
    });
    if (upstream.ok) {
      const data = await upstream.json();
      return res.json(data);
    }
  } catch {
    // Fall back to bundled eval/metrics.json
  }
  const local = readJsonFile(path.join(EVAL_DIR, 'metrics.json'));
  if (!local) {
    return res.status(404).json({ error: 'No evaluation metrics available.' });
  }
  return res.json(local);
});

app.get('/api/metrics/history', async (_req, res) => {
  try {
    const upstream = await fetch(`${SNAPSHOT_API}/metrics/history`, {
      signal: AbortSignal.timeout(8000),
    });
    if (upstream.ok) {
      const data = await upstream.json();
      return res.json(data);
    }
  } catch {
    // Fall back to bundled eval/metrics_history.json
  }
  const local = readJsonFile(path.join(EVAL_DIR, 'metrics_history.json'));
  return res.json(Array.isArray(local) ? local : []);
});

app.get('/api/model', async (_req, res) => {
  try {
    const upstream = await fetch(`${SNAPSHOT_API}/model`, {
      signal: AbortSignal.timeout(8000),
    });
    if (upstream.ok) {
      const data = await upstream.json();
      return res.json(data);
    }
  } catch {
    // Fall back to bundled ml/models metadata
  }
  const local = loadLocalModelCard();
  if (!local) {
    return res.status(404).json({ error: 'No trained real/bogus model.', ml_status: 'no_model' });
  }
  return res.json(local);
});

app.get('/api/credits', async (_req, res) => {
  try {
    const upstream = await fetch(`${SNAPSHOT_API}/credits`, {
      signal: AbortSignal.timeout(8000),
    });
    if (upstream.ok) {
      const data = await upstream.json();
      return res.json(data);
    }
  } catch {
    // Fall back to built-in credits list
  }
  return res.json(CREDITS_FALLBACK);
});

// ---------------------------------------------------------------------------
// Public Voting Endpoints (Upstream + In-Memory Fallback)
// ---------------------------------------------------------------------------

app.get('/api/votes/queue', async (req, res) => {
  try {
    const url = buildUpstreamUrl(SNAPSHOT_API, '/votes/queue', req.originalUrl);
    const upstream = await fetch(url, { signal: AbortSignal.timeout(15_000) });
    if (upstream.ok) {
      const data = await upstream.json();
      if (Array.isArray(data.detections) && memoryVotes.size > 0) {
        for (const det of data.detections) {
          const extra = memoryVotes.get(det.detection_id);
          if (extra) {
            det.votes = {
              real: (det.votes?.real || 0) + extra.real,
              bogus: (det.votes?.bogus || 0) + extra.bogus,
              unsure: (det.votes?.unsure || 0) + extra.unsure,
            };
          }
        }
      }
      res.setHeader('Cache-Control', 'no-store');
      return res.json(data);
    }
    const text = await upstream.text();
    return res.status(upstream.status).type('application/json').send(text);
  } catch {
    const fallback = readJsonFile(path.join(STATIC_DIR, 'dev', 'mock_queue.json'));
    if (fallback) return res.json(fallback);
    return res.status(503).json({ error: 'Vote queue is temporarily unavailable.' });
  }
});

app.post('/api/votes', async (req, res) => {
  const { target, detection_id: detectionId, vote } = req.body || {};
  if (typeof target !== 'string' || !TARGET_NAME_RE.test(target)) {
    return res.status(400).json({ detail: 'Invalid target name.' });
  }
  if (typeof detectionId !== 'string' || !DETECTION_ID_RE.test(detectionId)) {
    return res.status(400).json({ detail: 'Invalid detection_id.' });
  }
  if (typeof vote !== 'string' || !VALID_VOTES.has(vote)) {
    return res.status(400).json({ detail: 'Vote must be real, bogus, or unsure.' });
  }

  try {
    const upstream = await fetch(`${SNAPSHOT_API}/votes`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ target, detection_id: detectionId, vote }),
      signal: AbortSignal.timeout(10_000),
    });
    if (upstream.ok) {
      const data = await upstream.json().catch(() => ({ ok: true }));
      return res.json(data);
    }
    if (upstream.status === 400 || upstream.status === 422 || upstream.status === 429) {
      const text = await upstream.text();
      return res.status(upstream.status).type('application/json').send(text);
    }
  } catch {
    // Fall back to in-memory session storage
  }

  const votes = recordMemoryVote(detectionId, vote);
  return res.json({ ok: true, storage: 'memory', votes });
});

// ---------------------------------------------------------------------------
// Live Pipeline & SSE Streaming Endpoints -> LIVE_PIPELINE_API
// ---------------------------------------------------------------------------

app.get('/api/dashboard/stream', (req, res) =>
  proxyRequest(req, res, LIVE_PIPELINE_API, '/dashboard/stream', { stream: true })
);
app.get('/api/recovery/stream', (req, res) =>
  proxyRequest(req, res, LIVE_PIPELINE_API, '/recovery/stream', { stream: true })
);
app.get('/api/field/stream', (req, res) =>
  proxyRequest(req, res, LIVE_PIPELINE_API, '/field/stream', { stream: true })
);
app.get('/api/mosaic/tile/stream', (req, res) =>
  proxyRequest(req, res, LIVE_PIPELINE_API, '/mosaic/tile/stream', { stream: true })
);
app.get('/api/field', (req, res) =>
  proxyRequest(req, res, LIVE_PIPELINE_API, '/field', { timeoutMs: 180_000 })
);
app.post('/api/jobs/field', (req, res) =>
  proxyRequest(req, res, LIVE_PIPELINE_API, '/jobs/field', { timeoutMs: 30_000 })
);
app.get('/api/jobs/:jobId', (req, res) =>
  proxyRequest(req, res, LIVE_PIPELINE_API, `/jobs/${encodeURIComponent(req.params.jobId)}`, {
    timeoutMs: 30_000,
  })
);
app.post('/api/refresh', (req, res) =>
  proxyRequest(req, res, SNAPSHOT_API, '/refresh', { timeoutMs: 30_000 })
);

// ---------------------------------------------------------------------------
// Snapshot & Read-Heavy API Endpoints -> SNAPSHOT_API (or LIVE_PIPELINE_API when source=live)
// ---------------------------------------------------------------------------

app.get('/api/dashboard', (req, res) => {
  const isLive = String(req.query.source || '').toLowerCase() === 'live';
  return proxyRequest(req, res, isLive ? LIVE_PIPELINE_API : SNAPSHOT_API, '/dashboard', {
    timeoutMs: isLive ? 180_000 : 60_000,
  });
});

app.get('/api/recovery', (req, res) => {
  const isLive = String(req.query.source || '').toLowerCase() === 'live';
  return proxyRequest(req, res, isLive ? LIVE_PIPELINE_API : SNAPSHOT_API, '/recovery', {
    timeoutMs: isLive ? 180_000 : 60_000,
  });
});

app.get('/api/mosaic', (req, res) => {
  const isLive = String(req.query.source || '').toLowerCase() === 'live';
  return proxyRequest(req, res, isLive ? LIVE_PIPELINE_API : SNAPSHOT_API, '/mosaic', {
    timeoutMs: isLive ? 180_000 : 60_000,
  });
});

// Catch-all for remaining /api/* endpoints (targets, status, refresh-status,
// snapshot-index, snapshot-history, deep, deep/:name, candidates.csv,
// candidates.votable, cone, coverage)
app.all('/api/*', (req, res) => {
  const subPath = req.path.slice('/api'.length) || '/';
  return proxyRequest(req, res, SNAPSHOT_API, subPath);
});

// Compatibility proxy prefixes matching netlify.toml
app.all('/live-api/*', (req, res) => {
  const subPath = req.path.slice('/live-api'.length) || '/';
  return proxyRequest(req, res, SNAPSHOT_API, subPath);
});

app.all('/data-live-api/*', (req, res) => {
  const subPath = req.path.slice('/data-live-api'.length) || '/';
  return proxyRequest(req, res, LIVE_PIPELINE_API, subPath, {
    stream: subPath.endsWith('/stream'),
  });
});

// ---------------------------------------------------------------------------
// Static Assets & Frontend Shell
// ---------------------------------------------------------------------------

// Never cache the service worker so clients always get updates
app.get('/sw.js', (_req, res) => {
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Service-Worker-Allowed', '/');
  res.sendFile(path.join(STATIC_DIR, 'sw.js'));
});

if (fs.existsSync(FIGURES_DIR)) {
  app.use('/figures', express.static(FIGURES_DIR, { maxAge: '1d' }));
}

app.use(
  express.static(STATIC_DIR, {
    index: 'index.html',
    extensions: ['html'],
  })
);

app.listen(PORT, HOST, () => {
  console.log(`SPHERExLens portal listening on http://${HOST}:${PORT}`);
  console.log(`  Snapshot API: ${SNAPSHOT_API}`);
  console.log(`  Live API:     ${LIVE_PIPELINE_API}`);
});
