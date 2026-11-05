// SPHEREx Discovery Portal frontend

const API_BASE = window.location.origin;

let currentData = null;

// -- Helpers -------------------------------------------------------------

function show(id) { document.getElementById(id).classList.remove('hidden'); }
function hide(id) { document.getElementById(id).classList.add('hidden'); }

function setText(id, text) { document.getElementById(id).textContent = text; }

function verdictClass(verdict) {
  return verdict === 'KNOWN' ? 'verdict-known' : 'verdict-candidate';
}

// -- Map rendering -------------------------------------------------------

function renderMap(zogyData) {
  const canvas = document.getElementById('canvas-map');
  const ctx = canvas.getContext('2d');
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  canvas.width = w;
  canvas.height = h;

  const rows = zogyData.length;
  const cols = zogyData[0].length;

  let vmin = Infinity, vmax = -Infinity;
  for (let y = 0; y < rows; y++) {
    for (let x = 0; x < cols; x++) {
      const v = zogyData[y][x];
      if (v < vmin) vmin = v;
      if (v > vmax) vmax = v;
    }
  }
  const range = Math.max(vmax - vmin, 1e-6);

  const off = document.createElement('canvas');
  off.width = cols;
  off.height = rows;
  const offCtx = off.getContext('2d');
  const imgData = offCtx.createImageData(cols, rows);

  for (let y = 0; y < rows; y++) {
    for (let x = 0; x < cols; x++) {
      const v = (zogyData[y][x] - vmin) / range;
      const r = v > 0.5 ? Math.floor(255 * (v - 0.5) * 2) : 0;
      const g = Math.floor(255 * (1 - Math.abs(v - 0.5) * 2));
      const b = v < 0.5 ? Math.floor(255 * (0.5 - v) * 2) : 0;
      const idx = 4 * (y * cols + x);
      imgData.data[idx] = r;
      imgData.data[idx + 1] = g;
      imgData.data[idx + 2] = b;
      imgData.data[idx + 3] = 255;
    }
  }

  offCtx.putImageData(imgData, 0, 0);
  ctx.drawImage(off, 0, 0, w, h);
}

// -- Verdict panel -------------------------------------------------------

function renderVerdict(det) {
  const box = document.getElementById('verdict-box');
  if (!det) {
    box.innerHTML = '<div class="verdict-label">No detection</div>';
    return;
  }

  const vClass = det.verdict === 'KNOWN' ? 'verdict-known' : 'verdict-candidate';
  let html = `<div class="${vClass}">${det.verdict}</div>`;

  if (det.verdict === 'KNOWN') {
    const matchList = Object.keys(det.matches).map(k => {
      const sep = det.matches[k].sep_arcsec.toFixed(1);
      return `${k} (${sep}'')`;
    });
    html += `<div class="verdict-detail">Matched: ${matchList.join(', ')}</div>`;
  } else {
    html += `<div class="verdict-detail">No catalog match within 30''. Follow-up recommended.</div>`;
  }
  html += `<div class="verdict-detail">RA ${det.ra.toFixed(4)}, Dec ${det.dec.toFixed(4)}</div>`;
  html += `<div class="verdict-detail">SNR ${det.snr.toFixed(1)}</div>`;
  box.innerHTML = html;
}

// -- Detections table ----------------------------------------------------

function renderTable(detections) {
  const tbody = document.getElementById('detections-body');
  if (!detections || detections.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">No detections</td></tr>';
    return;
  }

  tbody.innerHTML = detections.map((d, i) => {
    const vBadge = `<span class="badge ${d.verdict === 'KNOWN' ? 'badge-known' : 'badge-candidate'}">${d.verdict}</span>`;
    const kindBadge = `<span class="badge badge-${d.kind}">${d.kind}</span>`;
    const matchName = d.verdict === 'KNOWN'
      ? Object.keys(d.matches).join(', ')
      : '---';
    return `
      <tr data-index="${i}">
        <td>${d.ra.toFixed(3)}</td>
        <td>${d.dec.toFixed(3)}</td>
        <td>${d.snr.toFixed(1)}</td>
        <td>${kindBadge}</td>
        <td>${vBadge}</td>
        <td>${matchName}</td>
      </tr>
    `;
  }).join('');

  Array.from(tbody.querySelectorAll('tr')).forEach(tr => {
    tr.addEventListener('click', () => {
      const idx = parseInt(tr.dataset.index, 10);
      renderVerdict(detections[idx]);
    });
  });
}

// -- API calls -----------------------------------------------------------

async function runPipeline() {
  const target = document.getElementById('target-select').value;
  show('map-loader');
  setText('map-hint', 'Running pipeline...');
  setText('card-total', '...');

  try {
    const resp = await fetch(`${API_BASE}/api/dashboard?target=${encodeURIComponent(target)}`);
    const data = await resp.json();

    if (data.error) {
      setText('map-hint', data.error);
      hide('map-loader');
      return;
    }

    currentData = data;
    renderMap(data.zogy_snr);
    renderTable(data.detections);

    if (data.detections.length > 0) {
      renderVerdict(data.detections[0]);
      const maxSnr = Math.max(...data.detections.map(d => d.snr));
      setText('card-snr', maxSnr.toFixed(1));
      setText('stat-snr', maxSnr.toFixed(1));
    } else {
      renderVerdict(null);
      setText('card-snr', '--');
      setText('stat-snr', '--');
    }

    const unknownCount = data.detections.filter(d => d.verdict === 'CANDIDATE').length;
    setText('card-total', data.detections.length);
    setText('card-unknown', unknownCount);
    setText('stat-detect', data.detections.length);
    setText('stat-unknown', unknownCount);
    setText('map-hint', `${data.common} -- ${data.n_epoch1}+${data.n_epoch2} exposures`);
  } catch (e) {
    setText('map-hint', 'Request failed: ' + e.message);
  } finally {
    hide('map-loader');
  }
}

async function runRecovery() {
  const target = document.getElementById('target-select').value;
  setText('map-hint', 'Running recovery grid...');
  try {
    const resp = await fetch(`${API_BASE}/api/recovery?target=${encodeURIComponent(target)}`);
    const data = await resp.json();
    if (data.error) {
      setText('map-hint', data.error);
      return;
    }
    const summary = data.grid.slice(0, 4).map(g =>
      `SNR ${g.snr}/mu ${g.mu}: ${(g.recovery*100).toFixed(0)}%`
    ).join(' | ');
    setText('map-hint', 'Recovery: ' + summary);
  } catch (e) {
    setText('map-hint', 'Recovery failed: ' + e.message);
  }
}

function injectPlanetX() {
  setText('map-hint', 'Planet X injection -- not yet implemented in demo');
}

document.getElementById('btn-run').addEventListener('click', runPipeline);
document.getElementById('btn-recovery').addEventListener('click', runRecovery);
document.getElementById('btn-inject').addEventListener('click', injectPlanetX);
document.getElementById('target-select').addEventListener('change', runPipeline);

runPipeline();
