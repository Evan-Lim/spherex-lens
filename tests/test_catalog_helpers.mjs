// Regression test for the catalogue helpers in static/app.js.  The UI used to
// hard-code Gaia / VSX / AllWISE, so a catalogue added to the data (2MASS,
// Pan-STARRS, ...) never appeared in the summary, legend or object hint.
//
// Run with:  node --test tests/test_catalog_helpers.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SOURCE = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');

function extractFunction(source, name) {
  const marker = `function ${name}(`;
  const start = source.indexOf(marker);
  if (start < 0) throw new Error(`function ${name} not found`);
  const open = source.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    else if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced function ${name}`);
}

function extractConstants(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  if (start < 0 || end < 0) throw new Error('constants not found');
  return source.slice(start, end);
}

function loadHelpers() {
  const block = [
    extractConstants(SOURCE, 'const CAT_COLORS = {', 'function catalogCountEntries'),
    extractFunction(SOURCE, 'catalogCountEntries'),
    extractFunction(SOURCE, 'catalogSummary'),
    extractFunction(SOURCE, 'unavailableCatalogs'),
    extractConstants(SOURCE, 'const COVERAGE_ON_DEMAND =', 'function coverageState'),
    extractFunction(SOURCE, 'coverageState'),
    extractFunction(SOURCE, 'coverageReason'),
  ].join('\n');
  const sandbox = {};
  vm.runInNewContext(`${block}\nthis.result = {
    catalogLabel, catalogColor, catalogCountEntries, catalogSummary,
    unavailableCatalogs, coverageState, coverageReason,
  };`, sandbox);
  return sandbox.result;
}

test('an extra catalogue is counted, labelled and coloured', () => {
  const h = loadHelpers();
  const dashboard = {
    catalog_counts: { gaia: 12, wise: 4, vsx: 2, twomass: 3, panstarrs: 7 },
    catalog_status: { gaia_mirror: 'mast', skymapper: 'unavailable' },
  };
  assert.equal(h.catalogLabel('twomass'), '2MASS');
  assert.equal(h.catalogLabel('panstarrs'), 'Pan-STARRS');
  assert.equal(h.catalogLabel('skymapper'), 'SkyMapper');
  assert.match(h.catalogColor('twomass'), /^#[0-9a-f]{6}$/i);
  assert.notEqual(h.catalogColor('twomass'), h.catalogColor('panstarrs'));

  const summary = h.catalogSummary(dashboard);
  assert.match(summary, /catalogues:/);
  assert.match(summary, /2MASS 3/);
  assert.match(summary, /Pan-STARRS 7/);
  assert.match(summary, /SkyMapper unavailable/);
  assert.doesNotMatch(summary, /gaia_mirror/);

  assert.deepEqual(Array.from(h.unavailableCatalogs(dashboard)), ['SkyMapper']);
  const keys = Array.from(h.catalogCountEntries(dashboard), ([key]) => key);
  assert.deepEqual(keys, ['gaia', 'panstarrs', 'skymapper', 'twomass', 'vsx', 'wise']);
});

test('coverage chips distinguish "answered but empty" from "has data"', () => {
  const h = loadHelpers();
  assert.equal(h.coverageState({ archive: 'spherex', available: true, count: 301 }), 'yes');
  assert.equal(h.coverageState({ archive: 'gaia', available: true, count: 269 }), 'yes');
  assert.equal(h.coverageState({ archive: 'vsx', available: true, count: 0 }), 'no');
  assert.equal(h.coverageState({ archive: 'neossat', available: true, count: 0 }), 'no');
  assert.equal(h.coverageState({ archive: 'gaia', available: false, count: 0 }), 'no');
  assert.equal(h.coverageState({ archive: 'jpl_sb_ident', available: true, count: 0 }), 'unknown');
  assert.equal(h.coverageState({ archive: 'ssois', available: true, count: 0 }), 'unknown');
  assert.equal(h.coverageState({ archive: 'neowise' }), 'unknown');
  // A failed check or a pending one is not "no data"; NEOWISE may show x only
  // when it actually answered with an empty cone.
  assert.equal(h.coverageState({ archive: 'neowise', available: false, count: 0,
    error: 'Timeout: archive did not answer' }), 'unknown');
  assert.match(h.coverageReason({ archive: 'neowise', available: false, count: 0,
    error: 'Timeout' }), /unavailable/);
  assert.equal(h.coverageState({ archive: 'neossat', available: false, count: 0,
    pending: true }), 'unknown');
  assert.match(h.coverageReason({ archive: 'neossat', available: false,
    count: 0, pending: true }), /checking/);
  assert.match(h.coverageReason({ archive: 'neowise', available: true, count: 0 }),
    /no NEOWISE visits/);
  assert.match(h.coverageReason({ archive: 'jpl_sb_ident', available: true, count: 0 }),
    /per detection/);
});
