// SPHERExLens -- dashboard frontend
//
// Two data modes:
//   server  (default)  -> FastAPI, /api/*
//   static  (<body data-mode="static">) -> pre-rendered api/*.json
//
// The map is a small interactive canvas engine: pan, zoom, three data layers,
// and overlays for the survey grid, catalog stars and detections.

const STATIC_MODE = document.body.dataset.mode === 'static';
const API_BASE = window.location.origin;

function apiUrl(kind, target) {
  if (STATIC_MODE) {
    const dir = document.body.dataset.apiDir || 'api';
    const suffix = target ? `-${encodeURIComponent(target)}` : '';
    return `${dir}/${kind}${suffix}.json`;
  }
  const params = [];
  if (target) params.push(`target=${encodeURIComponent(target)}`);
  if (state.live && kind === 'dashboard') params.push('source=live');
  const query = params.length ? `?${params.join('&')}` : '';
  return `${API_BASE}/api/${kind}${query}`;
}

// ---------------------------------------------------------------- utilities

const $ = (id) => document.getElementById(id);
const show = (id) => $(id).classList.remove('hidden');
const hide = (id) => $(id).classList.add('hidden');
const setText = (id, text) => { const el = $(id); if (el) el.textContent = text; };

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
};

const state = {
  data: null,
  layer: 'zogy_snr',
  target: null,
  scaling: {},           // per-layer [min, max]
  view: { scale: 1, tx: 0, ty: 0 },
  offscreen: {},
  overlays: { grid: true, catalog: true, detections: true },
  selected: -1,
  demo: null,
  crosshair: null,
  live: false,
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

function buildLayer(name, arr, lutName, symmetric) {
  const npix = arr.length;
  const canvas = document.createElement('canvas');
  canvas.width = npix;
  canvas.height = npix;
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(npix, npix);
  const lut = RAMPS[lutName];

  let lo, hi;
  if (symmetric) {
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
      const t = Math.min(1, Math.max(0, (v - lo) / (hi - lo)));
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

function rebuildLayers() {
  if (!state.data) return;
  buildLayer('image', state.data.image, 'coadd', false);
  buildLayer('zogy_image', state.data.zogy_image, 'diff', true);
  buildLayer('zogy_snr', state.data.zogy_snr, 'snr', false);
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
  const pad = 34;
  const s = Math.max(0.4, Math.min((w - pad) / npix, (h - pad) / npix));
  state.view.scale = s;
  state.view.tx = (w - s * npix) / 2;
  state.view.ty = (h - s * npix) / 2;
}

function toImage(mx, my) {
  const npix = currentNpix();
  const { scale, tx, ty } = state.view;
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

function valueAt(x, y) {
  const arr = state.data[state.layer];
  if (!arr) return null;
  const ix = Math.round(x), iy = Math.round(y);
  if (iy < 0 || iy >= arr.length || ix < 0 || ix >= arr[0].length) return null;
  return arr[iy][ix];
}


// ------------------------------------------------------------------ drawing

const DET_COLORS = { dipole: '#0f9d58', monopole: '#d97706' };
const CAT_COLORS = { gaia: '#2563eb', wise: '#0f9d58', vsx: '#d97706' };

function draw() {
  const canvas = $('canvas-map');
  const { w, h } = canvasSize();
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, w, h);
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
  if (state.overlays.detections) drawDetections(ctx);
  if (state.highlight) {
    const { cx, cy } = toCanvas(state.highlight.x, state.highlight.y);
    ctx.save();
    ctx.strokeStyle = '#0f172a';
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
    ctx.strokeStyle = CAT_COLORS[p.cat] || '#64748b';
    ctx.globalAlpha = p.cat === 'vsx' ? 0.95 : 0.55;
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
    const color = DET_COLORS[d.kind] || '#0f172a';
    const big = i === state.selected;
    const r = big ? 8 : 6;

    if (d.kind === 'dipole' && d.pos_minus) {
      const m2 = toCanvas(d.pos_minus[1], d.pos_minus[0]);
      ctx.strokeStyle = 'rgba(15, 157, 88, .8)';
      ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.moveTo(m2.cx, m2.cy); ctx.lineTo(cx, cy); ctx.stroke();
      ctx.beginPath(); ctx.arc(m2.cx, m2.cy, 2.4, 0, Math.PI * 2); ctx.stroke();
    }

    ctx.strokeStyle = color;
    ctx.lineWidth = big ? 2.2 : 1.4;
    if (d.kind === 'dipole') {
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
    } else {
      ctx.strokeRect(cx - r, cy - r, 2 * r, 2 * r);
    }
    if (big) {
      ctx.fillStyle = color;
      ctx.font = '10px ui-monospace, Menlo, monospace';
      ctx.fillText(`${d.snr.toFixed(1)}σ`, cx + r + 4, cy - r + 2);
    }
  });
  ctx.restore();
}

function drawCrosshair(ctx) {
  const { cx, cy } = state.crosshair;
  ctx.save();
  ctx.strokeStyle = 'rgba(15, 23, 42, .45)';
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
  if (!state.data) return;
  const { x, y } = toImage(mx, my);
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
    updateReadout(mx, my);
    draw();
  });

  window.addEventListener('mouseup', (e) => {
    if (drag && drag.moved < 4) {
      const rect = canvas.getBoundingClientRect();
      pickDetection(e.clientX - rect.left, e.clientY - rect.top);
    }
    drag = null;
    wrap.classList.remove('dragging');
  });

  canvas.addEventListener('mouseleave', () => {
    state.crosshair = null;
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
  if (best >= 0) selectDetection(best);
}

function selectDetection(index) {
  state.selected = index;
  renderVerdict(state.data.detections[index]);
  document.querySelectorAll('#detections-body tr')
    .forEach((tr, i) => tr.classList.toggle('selected', i === index));
  draw();
}


// ----------------------------------------------------------- data + panels

async function loadTargets() {
  try {
    const resp = await fetch(apiUrl('targets'));
    if (!resp.ok) return;
    const data = await resp.json();
    const targets = (data.targets || []).filter((t) => t && t.name);
    if (!targets.length) return;
    const sel = $('target-select');
    const previous = sel.value;
    sel.innerHTML = targets
      .map((t) => `<option value="${fmt.escape(t.name)}">${fmt.escape(t.common || t.name)}</option>`)
      .join('');
    if (targets.some((t) => t.name === previous)) sel.value = previous;
  } catch (e) { /* keep the hard-coded list */ }
}

function setStatus(text, kind) {
  const pill = document.querySelector('.status-pill');
  pill.classList.remove('busy', 'error');
  if (kind) pill.classList.add(kind);
  setText('status-text', text);
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
  setText('stat-cats', `${fmt.int(s.n_gaia)} \u00b7 ${fmt.int(s.n_vsx)}`);
  setText('stat-kinds', `${fmt.int(s.n_dipoles)} \u00b7 ${fmt.int(s.n_monopoles)}`);
  setText('stat-detect', fmt.int(s.n_detections));
  setText('stat-unknown', fmt.int(s.n_candidate));
  setText('stat-snr', s.max_snr ? s.max_snr.toFixed(1) : '--');
}

function renderVerdict(det) {
  const box = $('verdict-box');
  if (!det) {
    box.innerHTML = '<div class="verdict-label">No detection in this field</div>';
    return;
  }
  const cls = det.verdict === 'KNOWN' ? 'verdict-known' : 'verdict-candidate';
  let html = `<div class="${cls}">${fmt.escape(det.verdict)}</div>`;
  if (det.verdict === 'KNOWN') {
    const list = Object.keys(det.matches || {}).map((k) => {
      const m = det.matches[k];
      const name = m.source && m.source.name;
      return `${k}${name ? ` (${fmt.escape(name)})` : ''} ${m.sep_arcsec.toFixed(1)}\u2033`;
    });
    html += `<div class="verdict-detail">Matched: <b>${list.join(', ')}</b></div>`;
  } else {
    html += '<div class="verdict-detail">No catalog match within 30\u2033 \u00b7 follow-up recommended</div>';
  }
  html += `<div class="verdict-detail">RA <b>${fmt.num(det.ra, 5)}</b> \u00b7 Dec <b>${fmt.num(det.dec, 5)}</b></div>`;
  html += `<div class="verdict-detail">SNR <b>${fmt.num(det.snr, 1)}\u03c3</b> \u00b7 ${fmt.escape(det.kind)} \u00b7 confidence <b>${fmt.escape(det.confidence || '--')}</b></div>`;
  box.innerHTML = html;
}

function renderTable(dets) {
  const tbody = $('detections-body');
  if (!dets || !dets.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">No detections</td></tr>';
    return;
  }
  tbody.innerHTML = dets.map((d, i) => {
    const vBadge = `<span class="badge ${d.verdict === 'KNOWN' ? 'badge-known' : 'badge-candidate'}">${fmt.escape(d.verdict)}</span>`;
    const kBadge = `<span class="badge badge-${fmt.escape(d.kind)}">${fmt.escape(d.kind)}</span>`;
    const match = d.verdict === 'KNOWN' ? Object.keys(d.matches || {}).join(', ') : '\u2014';
    const mu = d.proper_motion_arcsec_yr ? d.proper_motion_arcsec_yr.toFixed(1) : '\u2014';
    return `<tr data-index="${i}">
      <td>${fmt.num(d.ra, 4)}</td>
      <td>${fmt.num(d.dec, 4)}</td>
      <td>${fmt.num(d.snr, 1)}</td>
      <td>${kBadge}</td>
      <td>${vBadge}</td>
      <td>${fmt.escape(match)}</td>
      <td>${mu}</td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('tr').forEach((tr) => {
    tr.addEventListener('click', () => selectDetection(parseInt(tr.dataset.index, 10)));
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
  out.style.whiteSpace = 'pre-line';
  out.textContent = pairs.map((p) =>
    `${p.pair.padEnd(7)} ${String(p.n_exposures_a).padStart(2)}+${String(p.n_exposures_b).padStart(2)} exp` +
    `   sigma ${p.sigma_a.toFixed(4)} / ${p.sigma_b.toFixed(4)}` +
    `   -> ${p.n_detections} detection${p.n_detections === 1 ? '' : 's'}`
  ).join('\n');
}

function renderObjects(data) {
  state.objects = data.field_objects || [];
  const counts = data.catalog_counts || {};
  const known = state.objects.filter((o) => o.verdict === 'KNOWN').length;
  const cand = state.objects.filter((o) => o.verdict === 'CANDIDATE').length;
  const det = state.objects.filter((o) => o.detected).length;

  const offMap = state.objects.filter((o) => o.in_field === false).length;
  setText('objects-hint',
    `${state.objects.length} objects (${state.objects.length - offMap} on the map` +
    (offMap ? `, ${offMap} outside)` : ')') +
    (counts.gaia ? ` - Gaia ${counts.gaia}` : '') +
    (counts.vsx ? ` - VSX ${counts.vsx}` : '') +
    (counts.wise ? ` - AllWISE ${counts.wise}` : ''));

  const label = { all: 'all', known: 'known', candidate: 'candidate', detected: 'detected' };
  document.querySelectorAll('#object-filter .seg').forEach((b) => {
    b.dataset.count = { all: state.objects.length, known, candidate: cand, detected: det }[b.dataset.filter];
    b.textContent = `${b.textContent.split(' (')[0]} (${b.dataset.count})`;
  });
  drawObjectTable();
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
    const cls = o.verdict === 'KNOWN' ? 'badge-known' : 'badge-candidate';
    const cats = (o.catalogs || []).map((c) =>
      `<span class="cat-chip ${fmt.escape(c)}">${fmt.escape(c)}</span>`).join('') || '\u2014';
    const mag = (o.mag === null || o.mag === undefined) ? '\u2014' : o.mag.toFixed(2);
    const seen = o.detected ? 'yes' : 'no change';
    const snr = o.snr ? o.snr.toFixed(1) : '\u2014';
    const off = o.in_field === false ? ' off-map' : '';
    return `<tr data-i="${state.objects.indexOf(o)}" class="${off}">
      <td><span class="badge ${cls}">${fmt.escape(o.verdict)}</span></td>
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
    tr.addEventListener('click', () => {
      const obj = state.objects[parseInt(tr.dataset.i, 10)];
      state.highlight = (obj && obj.in_field !== false) ? { x: obj.x, y: obj.y } : null;
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

  setText('archive-line',
    `IRSA SIA2 \u00b7 collection ${(rows[0] && rows[0].collection) || 'spherex_qr2'} \u00b7 ` +
    `${rows.length} cutout${rows.length === 1 ? '' : 's'} \u00b7 ` +
    `${s.depth_5sigma ? s.depth_5sigma.toFixed(3) : '--'} MJy/sr 5\u03c3 depth \u00b7 ` +
    `Gaia DR3 ${fmt.int(s.n_gaia)} / VSX ${fmt.int(s.n_vsx)} entries cross-matched`);

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">No provenance recorded</td></tr>';
    return;
  }

  tbody.innerHTML = rows.map((r) => {
    const lam = r.wavelength_um ? r.wavelength_um.toFixed(3) : '--';
    const epoch = r.epoch
      ? `<span class="epoch-chip ${r.epoch > 1 ? 'e2' : ''}">E${r.epoch}</span>` : '--';
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

// --------------------------------------------------------------- pipeline IO

async function runPipeline() {
  const target = $('target-select').value;
  state.target = target;
  show('map-loader');
  setStatus(`FETCHING ${target.replace(/_/g, ' ').toUpperCase()} \u00b7 PIPELINE RUNNING`, 'busy');
  setText('map-hint', 'running ZOGY on the cached cutouts\u2026');
  setText('footer-status', `pipeline: ${target}`);
  setText('card-total', '\u2026');

  try {
    const resp = await fetch(apiUrl('dashboard', target));
    if (!resp.ok) {
      setText('map-hint', STATIC_MODE
        ? `No pre-rendered dashboard for ${target}.`
        : `Request failed: HTTP ${resp.status}`);
      setStatus('ERROR \u00b7 NO DATA', 'error');
      hide('map-loader');
      return;
    }
    const data = await resp.json();
    if (data.error) {
      setText('map-hint', data.error);
      setStatus('ERROR \u00b7 NO DATA', 'error');
      hide('map-loader');
      return;
    }

    state.data = data;
    state.selected = data.detections.length ? 0 : -1;
    rebuildLayers();
    fitView();
    updateZoomLabel();
    draw();

    renderTargetMeta(data);
    renderCards(data);
    renderStats(data);
    renderTable(data.detections);
    renderPairs(data);
    renderObjects(data);
    renderProvenance(data);
    renderVerdict(data.detections[0] || null);
    if (state.selected >= 0) {
      document.querySelectorAll('#detections-body tr')
        .forEach((tr, i) => tr.classList.toggle('selected', i === 0));
    }

    setText('map-hint',
      `${data.common} \u00b7 ${data.n_epoch1}+${data.n_epoch2} exposures \u00b7 ${data.map.fov_arcmin}' field`);
    setStatus('NORMAL OPS \u00b7 FIELD LOADED');
    setText('footer-status', `${target} \u00b7 ${data.detections.length} detections`);
    document.title = `${data.common} -- SPHERExLens`;
  } catch (e) {
    setText('map-hint', 'Request failed: ' + e.message);
    setStatus('ERROR \u00b7 REQUEST FAILED', 'error');
  } finally {
    hide('map-loader');
  }
}

async function runRecovery() {
  const target = $('target-select').value;
  setText('recovery-out', 'running injection-recovery grid\u2026');
  setText('footer-status', `recovery: ${target}`);
  try {
    const resp = await fetch(apiUrl('recovery', target));
    if (!resp.ok) {
      setText('recovery-out', `no recovery grid available for ${target}`);
      return;
    }
    const data = await resp.json();
    if (data.error) {
      setText('recovery-out', data.error);
      return;
    }
    const grid = data.grid || [];
    const lines = grid.slice(0, 6).map((g) =>
      `SNR ${String(g.snr).padStart(2)} \u00b7 \u03bc ${String(g.mu).padStart(2)}\u2033/yr \u2192 ${(g.recovery * 100).toFixed(0)}%`);
    const best = grid.reduce((a, b) => (b.recovery > (a ? a.recovery : -1) ? b : a), null);
    if (best) lines.push(`peak ${(best.recovery * 100).toFixed(0)}% at SNR ${best.snr}, \u03bc ${best.mu}\u2033/yr`);
    setText('recovery-out', lines.join('\n'));
    $('recovery-out').style.whiteSpace = 'pre-line';
    setText('footer-status', `recovery: ${grid.length} cells`);
  } catch (e) {
    setText('recovery-out', 'recovery failed: ' + e.message);
  }
}


// ------------------------------------------------------- demo / reset / init

function toggleDemo() {
  const btn = $('btn-demo');
  if (state.demo) {
    clearInterval(state.demo);
    state.demo = null;
    btn.classList.remove('active');
    btn.textContent = 'Auto Demo';
    setText('footer-status', 'auto demo stopped');
    return;
  }
  btn.classList.add('active');
  btn.textContent = 'Stop Demo';
  const advance = () => {
    const sel = $('target-select');
    const n = sel.options.length;
    if (n > 1) sel.selectedIndex = (sel.selectedIndex + 1) % n;
    runPipeline();
  };
  advance();
  state.demo = setInterval(advance, 12000);
  setText('footer-status', 'auto demo: cycling targets every 12 s');
}

function resetAll() {
  if (state.demo) toggleDemo();
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
  if (state.data) {
    fitView();
    updateZoomLabel();
    draw();
    renderVerdict(state.data.detections[0] || null);
    state.selected = 0;
    document.querySelectorAll('#detections-body tr')
      .forEach((tr, i) => tr.classList.toggle('selected', i === 0));
  }
  setText('recovery-out', 'injection-recovery not run yet');
  $('recovery-out').style.whiteSpace = 'normal';
  setText('map-hint', 'view reset');
}

async function copyShareLink() {
  const target = $('target-select').value;
  const url = `${window.location.origin}${window.location.pathname}?target=${encodeURIComponent(target)}`;
  try {
    await navigator.clipboard.writeText(url);
    setText('footer-status', 'share link copied');
  } catch (e) {
    setText('footer-status', `share link: ${url}`);
  }
  history.replaceState(null, '', `?target=${encodeURIComponent(target)}`);
}

function attachUiEvents() {
  $('btn-run').addEventListener('click', runPipeline);
  $('btn-recovery').addEventListener('click', runRecovery);
  $('btn-demo').addEventListener('click', toggleDemo);
  $('btn-reset').addEventListener('click', resetAll);
  $('btn-export').addEventListener('click', copyShareLink);
  $('target-select').addEventListener('change', () => {
    if (state.demo) toggleDemo();
    runPipeline();
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

  const liveToggle = $('tog-live');
  if (liveToggle) {
    liveToggle.addEventListener('change', (e) => {
      state.live = e.target.checked;
      setText('footer-status', state.live
        ? 'live mode: cutouts are fetched from IRSA and kept in RAM (nothing written to disk)'
        : 'cache mode: reading data_cache/');
      runPipeline();
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
    if (e.key === 'r') runPipeline();
    if (e.key === 'g') { $('tog-grid').click(); }
    if (e.key === 'd') { $('tog-det').click(); }
  });
}

async function init() {
  attachUiEvents();
  attachMapEvents();
  await loadTargets();
  const wanted = new URLSearchParams(window.location.search).get('target');
  if (wanted) {
    const sel = $('target-select');
    if ([...sel.options].some((o) => o.value === wanted)) sel.value = wanted;
  }
  setStatus('STANDBY \u00b7 LOADING FIELD', 'busy');
  await runPipeline();
}

init();

