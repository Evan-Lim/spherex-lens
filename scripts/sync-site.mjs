/**
 * scripts/sync-site.mjs
 *
 * Rebuilds the Netlify drag-and-drop static deployment folder (`site/`) from
 * `static/` while preserving `_redirects`, `_headers`, `figures/`, and any
 * pre-rendered `api/` payloads, and stamps the `<body>` static-mode attributes
 * so live Cloud Run snapshot reads (`/live-api`) and live IRSA SSE streams
 * (`data-live-api`) work identically when dragged directly into Netlify.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, '..');

const SOURCE_DIR = path.join(ROOT, 'static');
const OUT_DIR = path.join(ROOT, 'site');
const SHELL_FILES = ['index.html', 'style.css', 'app.js', 'sw.js'];

const DEFAULT_API_BASE = '/live-api';
const DEFAULT_LIVE_API = 'https://spherex-lens-live-m6cqoypnxa-uc.a.run.app/api';

const DEFAULT_REDIRECTS = `# Netlify proxy rules. They live inside site/ so they apply however the site
# is deployed (drag-and-drop of this folder, \`netlify deploy --dir site\`, or
# Git with publish directory "site").

/live-api/*       https://spherex-lens-api-m6cqoypnxa-uc.a.run.app/api/:splat   200!
/data-live-api/*  https://spherex-lens-live-m6cqoypnxa-uc.a.run.app/api/:splat  200!
`;

const DEFAULT_HEADERS = `# Netlify response headers for drag-and-drop deploys.
/sw.js
  Cache-Control: no-cache
  Service-Worker-Allowed: /

/figures/*
  Cache-Control: public, max-age=86400
`;

function extractAttr(html, name) {
  const re = new RegExp(`${name}="([^"]*)"`);
  const match = html.match(re);
  return match ? match[1] : '';
}

function escapeAttr(val) {
  return String(val)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function copyDirRecursive(src, dest) {
  if (!fs.existsSync(src)) return;
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyDirRecursive(srcPath, destPath);
    } else {
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

function syncSite() {
  const sourceIndexPath = path.join(SOURCE_DIR, 'index.html');
  if (!fs.existsSync(sourceIndexPath)) {
    throw new Error(`Missing ${sourceIndexPath}`);
  }

  fs.mkdirSync(OUT_DIR, { recursive: true });

  const deployedIndexPath = path.join(OUT_DIR, 'index.html');
  const existingHtml = fs.existsSync(deployedIndexPath)
    ? fs.readFileSync(deployedIndexPath, 'utf-8')
    : '';

  const apiBase = extractAttr(existingHtml, 'data-api-base') || DEFAULT_API_BASE;
  const liveApi = (extractAttr(existingHtml, 'data-live-api') || DEFAULT_LIVE_API).replace(/\/+$/, '');

  for (const name of SHELL_FILES) {
    const src = path.join(SOURCE_DIR, name);
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, path.join(OUT_DIR, name));
    }
  }

  copyDirRecursive(path.join(SOURCE_DIR, 'assets'), path.join(OUT_DIR, 'assets'));

  // Keep Netlify drag-and-drop _redirects and _headers present
  const redirectsPath = path.join(OUT_DIR, '_redirects');
  if (!fs.existsSync(redirectsPath)) {
    fs.writeFileSync(redirectsPath, DEFAULT_REDIRECTS, 'utf-8');
  }
  const headersPath = path.join(OUT_DIR, '_headers');
  if (!fs.existsSync(headersPath)) {
    fs.writeFileSync(headersPath, DEFAULT_HEADERS, 'utf-8');
  }

  const page = fs.readFileSync(sourceIndexPath, 'utf-8');
  if (!page.includes('<body>')) {
    throw new Error('static/index.html must contain a plain <body> tag for static sync');
  }

  const attrs = [
    'data-mode="static"',
    'data-api-dir="api"',
    `data-api-base="${escapeAttr(apiBase)}"`,
    `data-live-api="${escapeAttr(liveApi)}"`,
  ].join(' ');

  const stamped = page.replace('<body>', `<body ${attrs}>`);
  fs.writeFileSync(deployedIndexPath, stamped, 'utf-8');

  console.log(`Synced Netlify static folder -> ${OUT_DIR}`);
  console.log(`  data-api-base=${apiBase}`);
  console.log(`  data-live-api=${liveApi}`);
}

syncSite();
