# spherex-lens

A public-facing **SPHEREx discovery portal**: ingest NASA/IRSA SPHEREx Level-2
cutouts, mask and co-add the six detector arrays, run ZOGY optimal image
subtraction, triage every detection against Gaia DR3 / AllWISE / AAVSO VSX /
JPL SBDB, classify sources with a 102-band spectral index classifier, and
validate the detector with an injection-recovery loop — all served by a FastAPI
backend with a RefractID-style light-theme dashboard.

This repository implements the *SPHEREx Discovery Portal: Complete Technical
Blueprint* file-for-file, and adds a real-data ingest path, provenance tracking
and a deployable dashboard.

> **New here?** Read **[EXPLAINER.md](EXPLAINER.md)** -- a long, plain-language
> guide to the sky, the telescope, the six pipeline steps, every number on the
> dashboard, a worked example and an honesty section. No astronomy or coding
> knowledge needed.

---

## How much data does it use?

`step1_ingest.py` now ingests **every detector and every epoch** available for a
target, not just a 2-band subset:

```bash
python step1_ingest.py                       # QR2, all six bands, all epochs
python step1_ingest.py --epochs firstlast    # quick mode: first + last epoch only
python step1_ingest.py --bands SPHEREx-D1 SPHEREx-D4
```

| Target | SIA2 rows | Epochs in the archive | Full ingest (6 bands x epochs) |
|---|---|---|---|
| Barnard's Star | 273 | 2 | 12 cutouts / ~64 MB |
| Epsilon Indi B | 362 | 3 | 18 cutouts / ~95 MB |
| RW Aurigae | 250 | 2 | 12 cutouts / ~64 MB |
| R Draconis | 591 | 3 | 18 cutouts / ~95 MB |
| WISE 0855 | 278 | 3 | 18 cutouts / ~95 MB |

That is the *whole dataset for a 9-arcminute field*: ~78 cutouts, ~410 MB, one
download per (epoch, detector). Each takes ~1 min at IRSA's ~100 KB/s, so a full
five-target ingest is a ~1 hour job that resumes from cache if it is interrupted
(`Ctrl-C` is safe; re-running skips finished files).

The thirteen bonus fields described further down add more cutouts. Cache size
depends on the ingest options; this workspace currently has 264 FITS cutouts
(about 1.39 GB). The FITS files are gitignored -- a clean clone has the catalogue
metadata but not the image pixels.


**Every epoch is used.** `app.py` co-adds each epoch separately and runs ZOGY on
each *consecutive* pair (E1->E2, E2->E3, ...) plus the deep pair E1->En, then
merges duplicate detections (same position within 2 pixels, keeping the highest
SNR). The map shows the deepest baseline; the *Epoch pairs* panel lists what each
pair produced.

**The whole catalogue is used too.** The object browser lists **every** catalogue
entry in the target's 15' cone (Gaia DR3, VSX, AllWISE where available), not a
sample: 306 objects for WISE 0855, 340 for Barnard's Star. Each row is one
physical object with a verdict:

* **KNOWN** -- it is in at least one catalogue. The `Detected` column says whether
  SPHEREx also saw it change, and `SNR` gives that significance.
* **CANDIDATE** -- it is in *no* catalogue but SPHEREx detected a change there:
  the follow-up list.
* Entries appearing in several catalogues (a Gaia star that is also a VSX
  variable) are merged into one row listing all of them; entries outside the 9'
  map are flagged and drawn dimmed.

What "all of SPHEREx" would mean beyond one field: the mission is ~72 MB per
exposure and millions of exposures. The same code scales by looping targets
(SIA2 cone per field), or by querying in bulk through the public S3 bucket
(`nasa-irsa-spherex`) / TAP instead of one cutout at a time — the pipeline never
holds more than one epoch stack (180x180) in memory.

### Are there only five pictures?

No. Five is how many fields are **registered in `data_cache/`** (the five
mandatory brief targets); the archive holds the whole sky. Concretely:

* SPHEREx covers ~41,253 deg2; a 9' x 9' field is 0.0225 deg2, so the sky is
  roughly **1.8 million fields** of this size.
* A single 0.1-degree cone search already returns **250-591 Level-2 images** per
  target (see the table above) -- the archive is dense, not sparse.
* Any position can be pulled in on demand. Either tick *Live from IRSA*, or use
  the **Open any field** box in the target panel (RA, Dec, optional name), which
  calls:

```bash
curl 'http://localhost:8000/api/field?ra=269.85&dec=4.95&name=My_Field&slots=4'
# -> {"target":"My_Field","n_detections":2,"max_snr":46.58,"n_exposures":4,...}
```

  The cutouts are streamed from IRSA into RAM, the pipeline runs, and the field
  is registered for that server process (the dropdown, `/api/targets`,
  `/api/dashboard` and the all-sky mosaic all know it). Nothing is written to
disk, and a restart forgets it. Measured: **46 s** for a new field at
  RA 269.85, Dec 4.95 -- 2 detections, max 46.6 sigma.

To make a field **permanent**, add it to `TARGETS` in `step1_ingest.py` and
re-run (it then lives in `data_cache/` and ships in the static build), or drop
cutouts plus a `<Name>_meta.json` into `data_cache/` by hand.

### Live mode -- fresh on-demand archive requests

The dashboard can also skip `data_cache/` entirely: it asks IRSA for the cutout,
parses the FITS **from the HTTP response in memory**, and never writes a byte to
disk. In the UI it is the checkbox **"Fetch fresh SPHEREx data from IRSA (selected field)"**;
over HTTP it is a query parameter:

```bash
curl 'http://localhost:8000/api/dashboard?target=WISE_0855&source=live'
curl 'http://localhost:8000/api/dashboard?target=WISE_0855&source=live&live_slots=2'   # limit to two slots
```

```
GET /api/dashboard
  source=cache            read data_cache/ and run the pipeline (default)
  source=live             fresh SIA2 query -> IRSA cutouts -> RAM -> pipeline
  live_slots=N            0 = every slot; otherwise prioritizes matched epochs/bands
  live_size=0.2           cutout width in degrees
  live_epochs=firstlast   or "all"
```

How it is implemented (`live_ingest.py`): the SIA2 rows are selected exactly as
in the ingest step (footprint polygon, epoch split), the cutouts are fetched
**concurrently** (4 worker threads), validated in memory (target inside the
array, FLAGS mask acceptable), and handed to `coadd_detector()` as raw bytes —
`load_exposure()` and `extract_spectrum_from_cutout()` both accept a path *or*
bytes.

| | cached files | live streaming |
|---|---|---|
| response time | seconds, workload-dependent | tens of seconds or longer on a cold fetch |
| repeat live response | n/a | SIA2 and cutouts are fetched again for every run |
| disk used | depends on local cache | **0 bytes for fetched cutouts** |
| freshness | as of the last ingest | no local metadata or cutout cache is used |
| works offline | yes | no |

`SPHEREX_LIVE_BANDS` selects the initial detector bands (default
`SPHEREx-D1,SPHEREx-D4`); if they cannot cover two epochs, live mode tries all
six detectors. Browser and API responses are marked no-store, and live requests
bypass the local SIA2 XML cache as well as any process-memory cutout cache.
Provenance rows are labelled `live:<obs_id>` and include the IRSA link.

Honest limits: IRSA's cutout service can take tens of seconds or longer and may
throttle bursts. Every live run waits on a fresh SIA2 query, the selected
cutouts, and the scientific pipeline. This is neither a continuous stream nor
capable of a reliable 200 ms end-to-end response. 200 ms is below network
round-trip time to a remote archive for many clients, before transferring
multi-megabyte FITS cutouts or processing them. A cached/precomputed display can
be fast, but it is not fresh archive synchronization.

The all-sky mosaic is a separate, cache-derived coverage view. In live mode the
UI hides those cached tiles rather than presenting them as fresh; opening a
specific field still fetches that field from IRSA. `GET /api/mosaic?source=live`
returns HTTP 501 because a fresh all-sky mosaic is not implemented. Do not
interpret a successful plain `/api/mosaic` response as live archive data.

The numeric `live_slots` option is a work cap for demonstrations, not a way to
request a scientifically valid tiny sample: the selector now prioritizes
matching detector bands across epochs and spreads remaining slots across epochs.
At least one band must still be valid in two epochs; otherwise the API reports
a coverage error.

The pipeline only compares like-for-like observations: at least two epochs must
share a detector band. It reports an explicit coverage error instead of
subtracting different bands or inventing a second epoch. Some target positions
may therefore be listed in the all-sky map but have no comparable thumbnail.

---

## The dashboard

`static/` is a two-column RefractID-style console that works in both deployment
shapes (live FastAPI or pre-rendered static files):

* **Top bar** -- brand, data release (`NEXUS // SPHEREX-QR2`), status pill
  (`NORMAL OPS · FIELD LOADED` / amber while running / red on error),
  **Guide**, **Auto Demo** (cycles the fields every 12 s) and **Reset**.
* **Guide** -- a three-tab explainer written for a non-specialist: *How it works*
  (the camera, subtraction, the five steps, what the map colours mean), *How to
  use it* (a click-by-click walkthrough of the page) and *The words* (a ten-term
  glossary). It is plain HTML inside `index.html`, so it works with no backend and
  ships in the static build; `Esc`, the backdrop or *Got it* closes it.
* **/01 Target Control** -- target dropdown, RA/Dec, Run Pipeline, Recovery Grid,
  Copy Share Link, the triage verdict and eleven live statistics (exposures,
  epochs, baseline, noise, 5-sigma depth, field/pixel, catalogue counts,
  dipoles/monopoles, ...).

  **Live from IRSA and Open any field need a backend.** Both of them ask a Python
  process to go and fetch data at query time, so on the pre-rendered static build
  they are *disabled and labelled as such* (`Live from IRSA -- needs a backend`,
  with an explanatory line underneath) rather than failing silently. The other 19
  fields, the maps, the tables, the charts and the generated recovery grids are
  plain JSON in `site/api/` and work with no server at all. Static values are a
  snapshot from the last export, not a live calculation.
  **Run Pipeline** requests the selected target again (cache mode reads local
  cutouts; live mode fetches/reuses IRSA cutouts). **Recovery Grid** computes the
  injection test on the server, or reads its pre-rendered result on a static
  site. **Copy Share Link** includes the selected target and live-mode choice;
  if browser clipboard access is blocked it shows a manual-copy prompt.
* **/02 Command Dashboard** -- four metric cards (detections, unaccounted,
  max SNR, baseline days), the interactive map, the detections table and the
  **image sources & provenance** table.
* **Hover popups** -- hovering any detection or catalogue object opens a card
  with its verdict, SNR, RA/Dec, magnitude and which catalogues know it; hovering
  an all-sky tile shows that field's statistics.
* **All-sky view** -- one extra toggle plots every field on an equirectangular
  RA/Dec chart (`/api/mosaic`), each tile a down-sampled significance thumbnail
  with its detection count; click a tile to open that field. Fields are drawn
  magnified (a 9' field is 0.3 px at true scale) and the caption says so.
* **Target history** -- the NEOWISE panel displays archival AllWISE/NEOWISE
  photometry when available; these historical measurements are not live SPHEREx
  detections or an on-map NEOWISE symbol.
* **Diagnostics charts** -- four charts: the significance histogram (noise
  peak vs real sources), noise per epoch, detections per epoch pair, and the
  injection-recovery curve.
* **Interactive map** (`static/app.js`, ~1.1k lines of vanilla canvas code)
  * drag to pan, wheel to zoom (0.35x-12x), double-click / `Fit` / `0` to reset,
    `+`/`-` keys, click a marker to select a detection;
  * three layers: **Co-add**, **Difference D**, **Significance S**;
  * overlays: survey grid with arcminute labels, **Gaia DR3** and **VSX**
    catalogue crosses, dipole/monopole markers, a crosshair that reads out
    RA/Dec/pixel/value under the cursor, and a scale bar.
* **Provenance** -- every cutout is listed with epoch, detector, central
  wavelength (from the real `WCS-WAVE` table), observation id, UTC date, pixel
  size, file size and a **clickable link to the parent Level-2 file at IRSA**, so
  any pixel on screen can be traced back to its archive record.

The type is **IBM Plex Sans** for interface text and **IBM Plex Mono** for the
uppercase micro-labels, coordinates and tables -- loaded from Google Fonts with
system-stack fallbacks, so an offline copy still looks deliberate.

---

## The six pipeline stages

| # | Stage | Module | What it does |
|---|-------|--------|--------------|
| 1 | Ingest | `step1_ingest.py` | IRSA SIA2 query for the five mandatory targets + VizieR catalogs (Gaia DR3, AllWISE, VSX), cached to `data_cache/` |
| 2 | Co-add | `step2_coadd.py` | FLAGS bitmask rejection, WCS reprojection to a common TAN plane, inverse-variance co-addition |
| 3 | Detect | `step3_zogy.py` | Zackay–Ofek–Gal-Yam proper image subtraction, dipole (mover) vs monopole (variable) detection |
| 4 | Triage | `step4_triage.py` | Nearest-neighbour cross-match, `KNOWN` / `CANDIDATE` verdict + confidence |
| 5 | Classify | `step5_spectrum.py` | Aperture photometry, three-index classifier (CH4, H2O, NIR slope) |
| 6 | Validate | `step6_injection.py` | Injection-recovery grid over SNR x proper motion |

Serving layer: `app.py` (FastAPI) + `static/index.html`, `static/style.css`,
`static/app.js` (vanilla JS canvas dashboard).

## The five mandatory targets

| Target | Type | RA (deg) | Dec (deg) |
|---|---|---|---|
| Barnard's Star | M4V red dwarf | 269.452083 | +4.693333 |
| Epsilon Indi B | T1+T6 brown dwarf | 331.043750 | -56.782778 |
| WISE 0855 | Y-dwarf (250 K) | 133.795129 | -7.245147 |
| RW Aurigae | T Tauri variable | 76.956250 | +30.401389 |
| R Draconis | Mira red giant | 248.167500 | +66.754722 |

`astropy.coordinates.SkyCoord` also accepts sexagesimal input, e.g.
`SkyCoord("08h55m10.831s", "-07d14m42.53s")` gives the WISE 0855 coordinates
above.

## Extra fields: famous objects

Beyond the five, the repo also pre-renders thirteen well-known fields. They are
ingested with the same code path (2-3 bands x 2 epochs, so 2-6 cutouts apiece) and
are there to show that the pipeline is not tuned to one kind of target -- nebulae,
spirals, the Galactic Centre, a quasar, the brightest star in the sky and two
famous planet hosts all go through it unchanged:

| Field key | Object | Type | RA (deg) | Dec (deg) |
|---|---|---|---|---|
| `Andromeda_M31` | Andromeda Galaxy (M31) | spiral galaxy | 10.684700 | +41.269167 |
| `Orion_M42` | Orion Nebula (M42) | HII region | 83.822083 | -5.391111 |
| `Pleiades_M45` | Pleiades (M45) | open cluster | 56.750000 | +24.116667 |
| `Crab_M1` | Crab Nebula (M1) | supernova remnant | 83.633083 | +22.014500 |
| `Whirlpool_M51` | Whirlpool Galaxy (M51) | spiral galaxy | 202.469575 | +47.195258 |
| `SgrA_star` | Sagittarius A* | Galactic Centre | 266.416817 | -29.007825 |
| `Proxima_Cen` | Proxima Centauri | M5.5V flare star | 217.428951 | -62.679483 |
| `TRAPPIST_1` | TRAPPIST-1 | M8V planet host | 346.622400 | -5.041300 |
| `Betelgeuse` | Betelgeuse | red supergiant | 88.792938 | +7.407064 |
| `Sirius` | Sirius | brightest star | 101.287155 | -16.716116 |
| `Sombrero_M104` | Sombrero Galaxy (M104) | spiral galaxy | 189.997633 | -11.623051 |
| `Ring_M57` | Ring Nebula (M57) | planetary nebula | 283.395833 | +33.029167 |
| `Quasar_3C273` | Quasar 3C 273 | quasar (z=0.158) | 187.277912 | +2.052021 |

Add them with:

```bash
python step1_ingest.py --targets Andromeda_M31 Orion_M42 Pleiades_M45 Crab_M1 \
                              Whirlpool_M51 SgrA_star Proxima_Cen TRAPPIST_1 \
                              Betelgeuse Sirius Sombrero_M104 Ring_M57 Quasar_3C273 \
                       --epochs firstlast --bands SPHEREX-D1 SPHEREX-D4
```

These are demonstration fields, not brief deliverables. The command above is a
compact two-band example; the following values are from the current local cache
export, which can include more detectors and epochs. Coverage varies by target:

| Field | exposures | epochs | detections | KNOWN | CANDIDATE | max SNR / status |
|---|---|---|---|---|---|---|
| Andromeda (M31) | 18 | 3 | 10 | 10 | 0 | 15.0 |
| Orion Nebula (M42) | 12 | 2 | 0 | 0 | 0 | -- |
| Pleiades (M45) | 12 | 2 | 6 | 4 | 2 | 41.2 |
| Crab Nebula (M1) | 12 | 2 | 7 | 7 | 0 | 8.6 |
| Whirlpool (M51) | 18 | 3 | 7 | 7 | 0 | 32.3 |
| Sagittarius A* | -- | -- | -- | -- | -- | unavailable: no shared detector band |
| Proxima Centauri | 12 | 2 | 3 | 3 | 0 | 17.2 |
| TRAPPIST-1 | 18 | 3 | 10 | 9 | 1 | 41.7 |
| Betelgeuse | -- | -- | -- | -- | -- | unavailable: one recorded epoch |
| Sirius | 12 | 2 | 7 | 7 | 0 | 8.5 |
| Sombrero (M104) | 19 | 4 | 10 | 10 | 0 | 27.9 |
| Ring Nebula (M57) | 14 | 3 | 10 | 10 | 0 | 30.7 |
| Quasar 3C 273 | 18 | 3 | 5 | 4 | 1 | 45.8 |

This export contains 18 field entries; 16 have comparable difference maps. Two
remain visible but honestly unavailable: Betelgeuse's current local metadata
records only one epoch, while Sgr A* has different detector bands in its two
epochs. An isolated IRSA retry found no usable first-epoch Sgr A* D2 cutout; the
candidate was outside the delivered image. The cached SIA2 listing shows more
Betelgeuse coverage, and a separate temporary ingest validated eight cutouts
across two epochs, but those were not merged into the existing user cache.
Regenerate this snapshot after deliberately refreshing that cache. The numbers
above describe this local export, not a live or continuous survey.

The **Large Magellanic Cloud** was also tried and dropped: every QR2 row inside a
9' box at Dec -69.8 falls in a single visit, so there is no second epoch to
subtract (it is one commented-out line in `TARGETS`).


---

## Quick start

```bash
# 1. Environment
conda create -n spherex python=3.11 -y
conda activate spherex
pip install -r requirements.txt

# 2. Fetch real data (needs internet + optional IRSA account, see below)
python step1_ingest.py                   # writes data_cache/*.fits and *_meta.json
python step7_neowise.py                  # optional: WISE 2010-2024 history per
                                         # target + detection -> *_neowise.json

# 3. Run the pipeline end to end
python step2_coadd.py
python step3_zogy.py
python step4_triage.py
python step5_spectrum.py
python step6_injection.py

# 4. Serve the dashboard
uvicorn app:app --reload --port 8000     # then open http://localhost:8000
```

That command uses the local cache by default. To view the published private
Cloud Storage snapshot on localhost, set `SPHEREX_SNAPSHOT_BUCKET` and
authenticate with Google Application Default Credentials before starting
Uvicorn; see [Use the Cloud snapshot from localhost](cloud/README.md#use-the-cloud-snapshot-from-localhost).

Offline / rehearsal mode (synthetic cache + all charts):

```bash
python make_demo_data.py     # writes a SPHEREx-shaped cache into data_cache_demo/
python make_figures.py       # writes figures/*.png + figures/summary.{csv,json}
SPHEREX_CACHE=data_cache_demo uvicorn app:app --port 8000
```

Deploy:

```bash
python export_static.py                     # -> site/  (Netlify / Pages)
docker build -t spherex-lens . && docker run -p 8000:8000 spherex-lens   # Render/Railway/Fly
```

See [Deployment](#deployment) for the full matrix of hosts.

---

## How the page works (one request, end to end)

1. **The browser loads three static files** -- `index.html`, `style.css`,
   `app.js`. There is no framework and no build step.
2. **`app.js` asks for `/api/targets`** and rebuilds the dropdown from whatever
   data the deployment actually ships (so a static build with only some fields
   still works).
3. **`runPipeline()` fetches `/api/dashboard?target=...`** (or the pre-rendered
   `api/dashboard-<target>.json` in static mode). On the server that handler
   co-adds every epoch, runs ZOGY on each consecutive pair, triages the
   detections against the whole catalogue, computes the statistics, the
   significance histogram and the catalogue object list, and returns **one JSON
   document** (~1 MB).
4. **The renderer paints from that one object**: `image` / `zogy_image` /
   `zogy_snr` are turned into three offscreen canvases (one per layer), then the
   visible one is drawn with the current pan/zoom transform, followed by the
   grid, the catalogue crosses and the detection markers.
5. **Every interaction is local** -- pan, zoom, layer switch, overlay toggles,
   hover popups, chart redraws -- except three actions that fetch: changing the
   target, pressing *Recovery Grid*, and *Live from IRSA*.
6. **The all-sky view** fetches `/api/mosaic` once: a 48x48 thumbnail plus
   statistics per field, drawn on an equirectangular RA/Dec grid. Clicking a tile
   is just a shortcut for "select that target and run the pipeline".

Nothing is computed in the browser except pixels-to-colours and hit testing, so
the static build (Netlify) and the live server behave identically.

---

## Getting the data

### 1. Where the data lives

| Product | Archive | Endpoint |
|---|---|---|
| SPHEREx Level-2 images/cutouts (QR2, QR3) | NASA/IPAC IRSA | SIA2: `https://irsa.ipac.caltech.edu/SIA` · TAP: `https://irsa.ipac.caltech.edu/TAP` |
| Gaia DR3 | IRSA TAP (fallback: VizieR) | table `gaia.gaia_dr3_source` · VizieR `I/355/gaiadr3` |
| AllWISE | IRSA TAP (fallback: VizieR) | table `allwise_p3as_psd` · VizieR `II/328/allwise` (not on the Harvard mirror) |
| AAVSO VSX | VizieR (Harvard → CDS → NAOJ) | catalog `B/vsx/vsx` (type, period, amplitude, OID) |
| NEOWISE-R single exposures | IRSA TAP (`step7_neowise.py`) | table `neowiser_p1bs_psd`, one upload-join per target |
| AllWISE per-exposure photometry | IRSA TAP (`step7_neowise.py`) | table `allwise_p3as_mep` |
| JPL Small-Body Database | NASA/JPL SSD | REST: `https://ssd-api.jpl.nasa.gov/sbdb.api` |

Everything is public. Registering at
<https://irsa.ipac.caltech.edu/registration/> is optional but stops IRSA from
throttling bulk requests; use the same link to browse
<https://irsa.ipac.caltech.edu/Missions/spherex.html> and confirm sky coverage
before the event.

### 2. How to download it

**a) The script in this repo (recommended, does everything):**

```bash
python step1_ingest.py                      # QR2, 4 cutouts per target
python step1_ingest.py --collection spherex_qr3 --size 0.15
```

`step1_ingest.py` loops over the five mandatory targets and, for each one:

1. issues an **SIA2 cone search** --
   `https://irsa.ipac.caltech.edu/SIA?POS=CIRCLE ra dec 0.1&COLLECTION=spherex_qr2`
   -- and parses the returned VOTable into rows (`obs_id`,
   `energy_bandpassname`, `t_min`, `s_region`, `access_url`, ...);
2. splits the rows into **observing epochs** (gaps > 30 days) and keeps the
   first and the last epoch;
3. for each (epoch, band) slot ranks the candidate images by how deep inside
   their footprint the target lies (point-in-polygon test on the SIA2
   `s_region`), then downloads a **cutout** of the best one;
4. **validates** each cutout -- target at least 12 px inside the array and
   fewer than 50 % of pixels flagged -- and moves to the next candidate if not;
5. queries Gaia DR3 and AllWISE on IRSA TAP (complete cones, brightest
   first) and VSX on VizieR (trying the Harvard, CDS and NAOJ mirrors in
   that order; Gaia/AllWISE fall back to VizieR if IRSA fails);
6. writes `data_cache/<Target>_meta.json`.

> **Two gotchas this script exists to handle.** The IRSA SIA2 endpoint only
> accepts the `POS=CIRCLE ra dec r` form: adding the legacy `POS=ra,dec&SIZE=`
> pair routes the request to the old SIA **v1** handler, which answers
> `UsageFault: BAD_REQUEST: Unknown parameter: collection`. And a full-frame
> Level-2 image is ~72 MB (2040x2040, six extensions) while IRSA's IBE/S3
> downloads run at ~100 KB/s, so a whole frame takes >10 min -- the cutout
> service returns the same data products for the target region in ~5 MB.

**b) Manual cutout request (what the script does under the hood):**

```bash
# path = the part of access_url after "/ibe/data/"
curl -G 'https://irsa.ipac.caltech.edu/ibe/cutout' \
     --data-urlencode 'path=spherex/qr2/level2/2025W44_2B/l2b-v20-2025-307/1/level2_2025W44_2B_0322_4D1_spx_l2b-v20-2025-307.fits' \
     --data-urlencode 'center=133.795129,-7.245147' \
     --data-urlencode 'size=0.2' \
     -o cutout.fits
```

`422 Arrays do not overlap.` means the requested centre is outside that
exposure's footprint (pick another row); `404 Requested file was not found`
means the `path` was wrong.

**c) Any SIA2 client (pyvo) -- same query, no downloads:**

```python
from pyvo.dal import sia2

results = sia2.search("https://irsa.ipac.caltech.edu/SIA",
                      pos=(133.795129, -7.245147, 0.1),   # ra, dec, radius deg
                      collection="spherex_qr2")
table = results.to_table()
print(table["access_url"])          # one Level-2 FITS file per row
```

`astroquery.ipac.irsa.Irsa.query_sia(...)` accepts the same SIA2 keywords
(`pos=`, `collection=`, `res_format=`). TAP works too for catalogue-style
queries across the whole mission (`https://irsa.ipac.caltech.edu/TAP`), and
`Irsa.list_collections()` shows the live list -- currently
`spherex_qr2`, `spherex_qr2_cal`, `spherex_qr2_deep`, `spherex_qr3`,
`spherex_qr3_cal`, `spherex_qr3_deep`.

**d) The IRSA web UI** -- search by position or object name, use the
"Cutout Tool", and you get the same files by hand.

### 3. What format you get

* **SIA2/TAP responses** are **VOTable XML** (or CSV). They are *metadata only*:
  the pixels are at the URLs in `access_url`. Each row carries `obs_id`,
  `energy_bandpassname` (`SPHEREx-D1` ... `SPHEREx-D6`), `t_min`/`t_max` (MJD),
  `s_ra`/`s_dec`, `s_region` (footprint polygon), `access_url` and
  `access_estsize`.
* **SPHEREx Level-2 cutouts** are **FITS** files. A real cutout of WISE 0855
  contains exactly these extensions:

  | HDU | Contents | Type / units |
  |---|---|---|
  | `IMAGE` | calibrated surface brightness + WCS | float32, MJy/sr |
  | `FLAGS` | contamination bitmask (bits defined in the header, see below) | int32 |
  | `VARIANCE` | per-pixel variance | float32, (MJy/sr)^2 (~1e-3) |
  | `ZODI` | zodiacal-light model | float32, MJy/sr |
  | `PSF` | effective PSF cube, 121 planes of 101x101 | float32 |
  | `WCS-WAVE` | wavelength calibration table (FITS `WAVE-TAB`) | BINTABLE |

  Two details matter for the code:

  * There is **no `CWAVE`/`CBAND` image**. The wavelength of a pixel comes from
    the `WCS-WAVE` table: `X`/`Y` key columns (a 9x9 or 13x20 grid over the
    parent 2040-pixel frame) and `VALUES(x, y, 2)` = (wavelength, bandwidth) in
    um. `CRPIX1W`/`CRPIX2W` in the `IMAGE` header shift a cutout pixel back to
    parent coordinates. `step5_spectrum.py` does this interpolation.
  * The WCS is **TAN-SIP** and, in a cutout, `CRPIX` can be strongly negative
    (the distortion polynomial stays anchored on the parent frame). astropy's
    default SIP inverter then diverges and returns NaN for every pixel;
    `step2_coadd.py` retries with `all_world2pix(..., tolerance=1e-8,
    maxiter=100)` and does its own bilinear resampling when needed.

* **FLAGS bits** (from the `MP_*` keywords of the `FLAGS` header):

  | Bit | Plane | Bit | Plane |
  |---|---|---|---|
  | 0 | transient during SUR | 11 | cold pixel |
  | 1 | overflow during SUR | 12 | full sample history (info) |
  | 2 | onboard processing error | 14 | phantom correction missing |
  | 4 | phantom pixel | 15 | nonlinearity unreliable |
  | 5 | reference pixel (edge) | 17 | persistent charge |
  | 6 | permanently unusable | 19 | flagged by Detect Outliers |
  | 7 | dichroic inefficiency | 21 | mapped to a known source |
  | 9 | onboard data lost | | |
  | 10 | hot pixel | | |

  `step2_coadd.BAD_BITS` masks everything except the two informational planes
  (12 full-sample and 21 known-source).

* **VizieR catalogs** come back as `astropy.table.Table` objects (VOTable under
  the hood). `step1_ingest.py` keeps only the columns triage needs:
  `RA_ICRS`/`DE_ICRS`/`pmRA`/`pmDE`/`Gmag` (Gaia DR3), `RAJ2000`/`DEJ2000`/
  `W1mag`/`W2mag` (AllWISE), `RAJ2000`/`DEJ2000`/`Name`/`Type` (VSX).
* **JPL SBDB** returns **JSON** (`{"object": {...}, "orbit": {...}}`); it is an
  optional overlay and is not needed for the five target verdicts.

### 4. How to insert it into the project

The backend never touches the network -- it only reads `data_cache/`. So
"installing" data is two things: the FITS files, and one small JSON per target.

```
data_cache/                       # real data (default)
├── level2_2025W35_2A_0611_2D1_spx_l2b-v20-2025-271_cut.fits
├── level2_2026W11_1A_0430_1D1_spx_l2b-v24-2026-094_cut.fits
├── ...
├── WISE_0855_meta.json           # the index the pipeline reads
└── ...

data_cache_demo/                  # synthetic cache from make_demo_data.py
```

`SPHEREX_CACHE` selects which one the app, the steps and the figure script read
(default `data_cache`):

```bash
SPHEREX_CACHE=data_cache_demo uvicorn app:app --port 8000   # synthetic demo
uvicorn app:app --port 8000                                 # real cutouts
```

`<Target>_meta.json` is the only contract:

```json
{
  "target": "WISE_0855",
  "meta": {"ra": 133.795129, "dec": -7.245147,
           "type": "Mover_YDwarf", "common": "WISE 0855"},
  "paths": ["data_cache/level2_...D1_spx_..._cut.fits",
            "data_cache/level2_...D4_spx_..._cut.fits",
            "data_cache/level2_...D1_spx_..._cut.fits",
            "data_cache/level2_...D4_spx_..._cut.fits"],
  "epochs": [1, 1, 2, 2],
  "catalogs": {
    "gaia": [{"ra": 133.79, "dec": -7.25, "pm_ra": 0.0, "pm_de": 0.0, "gmag": 18.2}],
    "wise": [{"ra": 133.79, "dec": -7.25, "w1": 18.6, "w2": 18.7}],
    "vsx":  [{"ra": 133.79, "dec": -7.25, "name": "WISE 0855", "type": "VAR"}]
  },
  "collection": "spherex_qr2",
  "cutout_size_deg": 0.2,
  "sia2_rows": 298,
  "n_epochs": 3
}
```

* `epochs` (one integer per entry of `paths`) is what makes the ZOGY split
  exact. When it is absent the API falls back to splitting `paths` in half, so
  keep the list time-sorted. Fewer than two files returns a clear HTTP 500
  ("Need at least 2 for subtraction").
* `paths` may be relative (as written by `step1_ingest.py`) or absolute.
* `catalogs`, `spectrum_fits` and extra keys are optional. Without `catalogs`
  every detection is triaged as `CANDIDATE`. `spectrum_fits` is a 102-band
  `SPECTRUM` table (`WAVELENGTH`, `FLUX`, `ERROR`): when present it drives the
  dashboard spectrum panel and `fig04`; when absent the API reports the real
  per-exposure aperture photometry instead (2-4 points is not a spectrum, so no
  classifier verdict is fabricated).
* The filename stem **is** the API target key: `WISE_0855_meta.json` ->
  `?target=WISE_0855`, which is also what `/api/targets` and the dashboard
  dropdown enumerate.

To add a target by hand: copy the FITS cutouts into `data_cache/`, write the
JSON above with your own RA/Dec, `paths` and `epochs`, reload the page. No code
change is needed. FITS arrays are git-ignored (`.gitignore`), while the
`*_meta.json` files are small and worth committing.

### 5. Verified real-data run

`python step1_ingest.py` was executed against the live services. For Barnard's
Star it found **273 overlapping QR2 images in 2 epochs**, kept the 4 most
interior ones (2 bands x 2 epochs) and downloaded them as cutouts:

```
  SIA2 spherex_qr2: POS=CIRCLE 269.4521 4.6933 0.1
  -> 273 overlapping Level-2 images.
  2 epoch(s), 4 (epoch, band) slot(s).
    level2_2025W35_2A_0611_2D1_spx_l2b-v20-2025-271_cut.fits   5.3 MB
    accepted epoch 1 SPHEREx-D1
    level2_2025W35_2A_0611_2D4_spx_l2b-v20-2025-271_cut.fits   5.3 MB
    accepted epoch 1 SPHEREx-D4
    level2_2026W11_1A_0539_1D1_spx_l2b-v24-2026-094_cut.fits   5.3 MB
    accepted epoch 2 SPHEREx-D1
    level2_2026W11_1A_0539_1D4_spx_l2b-v24-2026-094_cut.fits   5.3 MB
    accepted epoch 2 SPHEREx-D4
  Querying catalogs within 15'...
    gaia: 300 sources (IRSA TAP fallback)
    vsx: 47 sources
```

Running the pipeline on all five real caches (two bands per epoch, D1 + D4):

| Target | SIA2 rows | Epochs kept | Cutouts | Detections | KNOWN | CANDIDATE | Max SNR | sigma1 / sigma2 | Wavelengths (um) |
|---|---|---|---|---|---|---|---|---|---|
| Barnard's Star | 273 | 2 | 4 | 12 | 4 | 8 | 29.5 | 0.0106 / 0.0119 | 0.91, 0.94, 3.05, 3.15 |
| Epsilon Indi B | 362 | 3 | 4 | 5 | 0 | 5 | 22.4 | 0.0118 / 0.0110 | 0.98, 1.06, 3.29, 3.73 |
| RW Aurigae | 250 | 2 | 4 | 1 | 0 | 1 | 5.7 | 0.0133 / 0.0134 | 0.82, 0.86, 2.70, 2.74 |
| R Draconis | 591 | 3 | 4 | 6 | 6 | 0 | 18.9 | 0.0128 / 0.0117 | 0.90, 0.94, 2.99, 3.16 |
| WISE 0855 | 278 | 3 | 4 | 2 | 0 | 2 | 9.0 | 0.0100 / 0.0098 | 0.90, 0.99, 2.99, 3.36 |

20 real cutouts (160 MB) spanning roughly 8 months of baseline per target.
Catalog columns: 300 Gaia DR3 sources per target (IRSA TAP) plus VSX (47 / 8 / 0
/ 10 / 12) where VizieR answered. **AllWISE is empty in this run** -- the
IRSA TAP copy of `allwise_p3as_psr` is not queryable and the VizieR mirrors were
unreachable, so KNOWN verdicts come from Gaia DR3 + VSX only.

![Real co-add and ZOGY](figures_real/fig02_coadd_panels.png)

![Real detections](figures_real/fig03_detections.png)

The bright star at the top of the Barnard's Star frame is a real saturated
source: its core is flagged (`MP_OVERFLOW`) and masked, which is why it appears
as a ring in the co-add and as a black hole in the difference image. Most
`CANDIDATE` verdicts sit on faint stars that Gaia DR3 does not list at these
magnitudes -- exactly the kind of follow-up target the portal is meant to
surface. R Draconis, a Mira in a crowded field, is the opposite case: all six of
its detections sit on catalogued stars.

Two honest caveats, both visible in the figures:

* With only two bands per epoch, the "spectrum" is four aperture measurements,
  not 102 channels, so `three_index_classifier` is **not** run (the API reports
  `classification: null`). The synthetic cache is what exercises the 102-band
  classifier; the real cache exercises everything else.
* IRSA's bulk throughput from this network is ~100 KB/s and VizieR was mostly
  unreachable, so a full five-target ingest takes a while. Everything is
  cached: re-running `python step1_ingest.py` skips finished downloads,
  re-validates cached cutouts instantly, and only fetches what is missing
  (SIA2 VOTables are cached to `data_cache/sia2_*.xml`, cutouts are kept by
  filename).

---

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/targets` | Targets present in `data_cache/` |
| `GET /api/dashboard?target=WISE_0855` | Runs stages 2-5 and returns `image`, `zogy_image`, `zogy_snr`, `sigma1`, `sigma2`, triaged `detections`, and `spectrum` (102 wavelengths, flux, classifier indices + label, plus the aperture flux at the target position) |
| `GET /api/recovery?target=WISE_0855` | Injection-recovery grid (`snr`, `mu`, `recovery`) |
| `GET /api/field?ra=&dec=&name=` | Register any sky position: streams its cutouts from IRSA, runs the pipeline, adds it to the dropdown/mosaic (RAM only) |
| `GET /api/mosaic` | One 48x48 significance thumbnail + statistics per cached field, for the all-sky view (cached in RAM for 15 min) |
| `GET /` | Static dashboard (`static/index.html`) |

The `/api/dashboard` response carries 180 x 180 nested arrays for `image`,
`zogy_image` and `zogy_snr` (~1.2 MB uncompressed, a few hundred KB gzipped).

---

---

## Demo data and generated charts


Both scripts below are **optional add-ons** -- they are not among the ten
blueprint files, and nothing in `step*.py` / `app.py` imports them.

### `make_demo_data.py` -- a synthetic, SPHEREx-shaped cache

```bash
python make_demo_data.py                 # all five targets, 400 px cutouts
python make_demo_data.py --overwrite     # regenerate
python make_demo_data.py --targets WISE_0855 --npix 300
```

For each target it writes into `data_cache/`:

* `demo_<Target>_00.fits` ... `_03.fits` -- 400 x 400 cutouts at 1.5"/pixel,
  with `IMAGE`, `FLAGS`, `VARIANCE`, `CWAVE`, `CBAND` extensions and a real TAN
  WCS (per-exposure pointing jitter + roll, so the reprojection step matters).
  Exposures 0-1 are epoch 1 and 2-3 are epoch 2, alternating detector D3/D4.
* A static field of 40 stars, **one mover** (6" -> 2" over the 0.5 yr gap, i.e.
  8"/yr) and **one variable** (flux x2.1 between epochs).
* A `FLAGS` mask that exercises all five bits: cosmic rays (0.4% of pixels),
  hot pixels (0.2%), four dead columns, a 5-pixel stray-light edge and
  saturated cores on the brightest stars -- about 1.9% of pixels masked.
* `demo_<Target>_spectrum.fits` -- a 102-row `SPECTRUM` binary table
  (`WAVELENGTH`, `FLUX`, `ERROR`) built from a cool+hot blackbody continuum
  with CH4 troughs at 1.72/2.32 um and H2O troughs at 1.50/2.00/3.00 um, i.e.
  exactly the windows `step5_spectrum.py` samples.
* `<Target>_meta.json` -- the index described above, with 20 Gaia DR3 and 20
  AllWISE entries on the field stars plus one VSX entry on the variable, so
  triage produces both `KNOWN` and `CANDIDATE` verdicts.

The generator runs the *unmodified* classifier on every spectrum it writes and
prints the result, so the demo cannot drift out of sync with the pipeline:

```
  102-band spectrum -> OK  ch4=2.74 h2o=0.85 nir_slope=1.01 \
label='Ultracool Y-dwarf' (expected 'Ultracool Y-dwarf')
```

| Target | Detections | KNOWN | CANDIDATE | Max SNR | Classifier verdict |
|---|---|---|---|---|---|
| Barnard's Star | 2 | 0 | 2 | 33.3 | M-dwarf / Mira red giant |
| Epsilon Indi B | 5 | 2 | 3 | 32.5 | T-type brown dwarf |
| WISE 0855 | 4 | 1 | 3 | 32.7 | Ultracool Y-dwarf |
| RW Aurigae | 2 | 2 | 0 | 34.6 | Dusty T Tauri / variable star |
| R Draconis | 3 | 0 | 3 | 34.1 | M-dwarf / Mira red giant |

*(numbers from the cache currently in `data_cache/`; they shift slightly if you
regenerate with `--overwrite` because the star field is re-drawn)*

**The demo data is synthetic and must never be presented as NASA data.** It is
there to make the pipeline, the dashboard and the figures reproducible offline;
point `data_cache/` at real IRSA cutouts to get real science.

### `make_figures.py` -- every chart the submission needs

```bash
python make_figures.py                          # all targets, detail = WISE 0855
python make_figures.py --detail-target RW_Aurigae --trials 40
SPHEREX_CACHE=data_cache_demo python make_figures.py --outdir figures
python make_figures.py --outdir figures_real --detail-target Barnards_Star
```

It re-runs stages 2-6 on whatever is cached (real or synthetic) and writes
PNGs plus machine-readable numbers into the output directory (`figures/` holds
the synthetic-cache charts, `figures_real/` the real-cutout ones):

| Figure | What it shows |
|---|---|
| `fig01_band_coverage.png` | the six detector arrays and their 102 channels, with all five demo spectra and the CH4/H2O bands marked |
| `fig02_coadd_panels.png` | epoch 1 co-add, epoch 2 co-add, ZOGY difference `D`, significance `S` |
| `fig03_detections.png` | dipoles (mover: plus lobe + negative lobe, joined) and monopoles (variable) marked on `S` |
| `fig04_spectrum_classification.png` | the 102-band spectrum with the exact CH4 / H2O / NIR windows the classifier slices, plus the three indices and the verdict |
| `fig05_recovery.png` | injection-recovery curves (recovery vs SNR for mu = 1, 2, 5, 10 "/yr) and the same grid as a heat map |
| `fig06_flags_masking.png` | the FLAGS bit map of one exposure and the masked fraction per bit |
| `fig07_targets_summary.png` | KNOWN vs CANDIDATE detections and max SNR for each of the five targets |
| `fig08_photometry.png` | aperture photometry of the mover and the variable in every exposure (flux step = motion, flux ratio = variability) |
| `summary.csv` / `summary.json` | per-target RA/Dec, epochs, sigmas, detections, verdicts, max SNR, classifier indices and the recovery grid |

![Band coverage](figures/fig01_band_coverage.png)

![Co-add and ZOGY panels](figures/fig02_coadd_panels.png)

![Detections](figures/fig03_detections.png)

![Spectrum and classification](figures/fig04_spectrum_classification.png)

![Injection-recovery](figures/fig05_recovery.png)

![FLAGS masking](figures/fig06_flags_masking.png)

![Targets summary](figures/fig07_targets_summary.png)

![Photometry](figures/fig08_photometry.png)

The PNGs are committed so the README renders on GitHub; delete `figures/` and
re-run the script to refresh them.

---

## Deployment

The project ships in two shapes, because the pipeline needs Python scientific
stack (numpy/scipy/astropy/reproject) that static hosts cannot run:

| Shape | What runs | Hosts |
|---|---|---|
| **Full app** (FastAPI + live pipeline) | `uvicorn app:app` in a container | Render, Railway, Fly.io, Google Cloud Run, any Docker host / VPS |
| **Static site** (pre-rendered JSON) | `site/` with `api/*.json` | Netlify, Cloudflare Pages, GitHub Pages, S3 |

Both are generated from the same code and look identical in the browser.

### A. Full app in a container (recommended for a live demo)

Everything needed is in the repo already: `Dockerfile`, `.dockerignore`,
`Procfile`, `render.yaml`, `fly.toml`. The FITS cutouts are gitignored, so a
clean clone contains metadata but not the image pixels. FITS files are excluded
from Docker build contexts; mount a populated `data_cache/` if you want to use
cached mode inside a local container. Otherwise use live mode with IRSA access.
Metadata paths are normalized so Windows-generated cache manifests work inside
Linux containers.

```bash
docker build -t spherex-lens .
docker run -p 8000:8000 spherex-lens
# -> http://localhost:8000
```

**Render** -- push the repo, then *New + -> Blueprint*: `render.yaml` already
declares a free Docker web service with `healthCheckPath: /api/targets`.
The free tier and upstream IRSA availability are not guaranteed; cold live
requests can take tens of seconds, and this service does not promise 200 ms
fresh-data synchronization. The API has no built-in authentication. CORS is
disabled by default and can be explicitly allowlisted with
`SPHEREX_CORS_ORIGINS=https://your-frontend.example`; CORS is not an access
control, so protect `/api/field` with a trusted proxy/authentication or rate
limits before exposing it to untrusted clients.

**Railway** -- *New Project -> Deploy from repo*; it detects the `Procfile`
(`uvicorn app:app --host 0.0.0.0 --port $PORT`) and builds with Nixpacks. Add
`SPHEREX_CACHE=data_cache` if you switch caches.

**Fly.io** --

```bash
fly launch --no-deploy --copy-config   # uses fly.toml (1 GB shared VM)
fly deploy                              # -> https://spherex-lens.fly.dev
```

**Google Cloud Run** --

```bash
gcloud run deploy spherex-lens --source . --port 8000 --allow-unauthenticated
```

That command deploys only the interactive app; it does not create the
scheduled refresh pipeline or durable snapshots. For the agreed 12-hour,
registered-field architecture, use the [Google Cloud deployment guide](cloud/README.md)
and Terraform configuration in `cloud/terraform/`.

**Any VPS** --

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000        # put nginx/caddy in front
```

All of these expose the same API (`/api/targets`, `/api/dashboard`,
`/api/recovery`) and serve the dashboard at `/`.

### B. Static site (Netlify / Pages)

`export_static.py` runs the whole pipeline once per target and writes every API
response as a file, then flips the dashboard into static mode
(`<body data-mode="static">`), so no Python is needed at runtime:

```bash
python export_static.py                       # -> site/  (18 fields; current snapshot ~58 MB JSON)
python export_static.py --no-recovery         # faster: skip the recovery grids
python export_static.py --targets WISE_0855 R_Draconis
```

```
site/
├── index.html      style.css      app.js
├── api/targets.json
├── api/dashboard-<Target>.json     # what /api/dashboard returns
├── api/recovery-<Target>.json      # what /api/recovery returns
└── figures/                        # the pipeline charts
```

Deploy it:

```bash
# Netlify  (netlify.toml already sets publish = "site")
netlify deploy --prod --dir site

# Cloudflare Pages
wrangler pages deploy site --project-name spherex-lens

# GitHub Pages: branch-based publishing only serves / or /docs, so either
#   - Actions: actions/upload-pages-artifact with path: site, or
#   - rename the folder: --outdir docs  and publish from /docs
```

Because the JSON is plain files, the dashboard works identically offline --
`python -m http.server -d site 8300` is enough to demo it on a laptop.

**Why not run the pipeline on Netlify functions?** A single
`/api/dashboard` request loads four ~5 MB FITS files, masks and reprojects them,
runs an FFT subtraction and a 12-cell injection-recovery grid. That is seconds
of CPU and a few hundred MB of native dependencies -- outside the limits of
serverless static platforms, which is why the static build pre-renders instead.
(Netlify Functions also only run JavaScript/TypeScript/Go, and 50 MB zipped /
250 MB unzipped would not fit numpy + scipy + astropy + reproject even if they
did.)

### C. Hybrid: static Netlify site with a cloud snapshot API

Netlify serves the interface. A `[[redirects]]` rewrite forwards short
snapshot reads from `/live-api/*` to Cloud Run:

```toml
# netlify.toml
[[redirects]]
  from = "/live-api/*"
  to = "https://YOUR_CLOUD_RUN_URL/api/:splat"
  status = 200
  force = true
```

```powershell
python export_static.py --api-base /live-api `
  --pipeline-api-base https://YOUR_CLOUD_RUN_URL/api
netlify deploy --prod --dir site
```

With the cloud backend configured, the deployed site uses the published
snapshot API for registered fields, targets, recovery grids and the mosaic.
`Open any field` and the explicit live toggle fetch on demand from IRSA:

| Interaction | Path taken |
|---|---|
| Registered-field dashboard | `/live-api/dashboard?target=...` -- published snapshot |
| Registered-field target list, recovery, mosaic | `/live-api/<route>` -- published snapshot |
| "Live from IRSA" toggle | direct Cloud Run `/api/dashboard/stream` -- fresh IRSA data |
| "Open any field" (RA/Dec) | direct Cloud Run `/api/field/stream` -- fresh IRSA data |

Netlify proxy rewrites time out after 26 seconds, so long IRSA requests use the
direct Cloud Run URL. Set the exact Netlify site origin in
`cloud/terraform/terraform.tfvars` as `netlify_origin`; it must not end in `/`.
CORS only permits the browser origin and is not authentication. Without
`--api-base` the registered-field site stays static; without
`--pipeline-api-base`, fresh requests fall back to the proxy and remain subject
to its timeout. This cloud deployment is not guaranteed free; see
[cloud/README.md](cloud/README.md) for costs, setup and security limitations.

The static page can detect an available `/live-api` rewrite, but a deploy with
the direct pipeline base configured is required for long-running live progress.


---

## Repository layout

```
spherex-lens/
├── data_cache/              # FITS cutouts + *_meta.json (arrays are git-ignored)
├── figures/                 # generated charts + summary.csv/json (demo cache)
├── figures_real/            # the same charts for the real cutouts
├── notebooks/               # exploration notebooks (optional)
├── static/
│   ├── index.html           # dashboard markup
│   ├── style.css            # RefractID light theme
│   └── app.js               # canvas ZOGY map, verdict panel, detections table
├── step1_ingest.py          # IRSA SIA2 + VizieR ingest
├── step2_coadd.py           # FLAGS masking + inverse-variance co-add
├── step3_zogy.py            # ZOGY subtraction + dipole/monopole detection
├── step4_triage.py          # catalog cross-match and verdict
├── step5_spectrum.py        # 102-band extraction + three-index classifier
├── step6_injection.py       # injection-recovery validation
├── app.py                   # FastAPI backend
├── make_demo_data.py        # optional: synthetic offline demo cache
├── make_figures.py          # optional: renders figures/ from the cache
├── EXPLAINER.md             # plain-language guide to the whole project
├── live_ingest.py          # optional: stream cutouts from IRSA into RAM (no disk)
├── export_static.py         # optional: pre-renders site/ for Netlify
├── site/                    # static build output (index.html + api/*.json)
├── Dockerfile               # full app container (Render/Railway/Fly/Cloud Run)
├── docker-compose.yml       # local container run
├── Procfile / render.yaml / fly.toml / netlify.toml
├── requirements.txt
└── README.md
```

## Data products used

* **IMAGE** — calibrated surface brightness (MJy/sr) + WCS.
* **FLAGS** — 32-bit bitmask; bits used here: `1` dead, `2` saturated,
  `4` cosmic ray, `32` hot pixel, `256` stray light.
* **VARIANCE** — per-pixel variance sigma^2.
* **CWAVE / CBAND** — central wavelength and bandwidth lookup tables.
* **ePSF** — 101 x 101 x 121 cube (101 x 101 x 151 for detector 3). QR2/QR3 PSF
  layer headers before pipeline 6.5.6 (and QR2 files without the `+psfix1`
  suffix) have a known x/y indexing bug.

## Implementation notes

* ZOGY needs **two epochs**: `app.py` splits the cached files in half, co-adds
  each half and subtracts the two stacks. Fewer than two files returns a clear
  500 (see Section 8.3 of the blueprint).
* Detection threshold defaults to 5 sigma (`find_detections(..., sigma_thresh=5.0)`);
  the significance map is normalized by its own standard deviation.
* Triage radius is 30 arcsec (`MATCH_RADIUS_ARCSEC`), with a 5 arcsec "tight"
  radius that promotes the verdict confidence to `high`.
* The `FLAGS` bit values in `step2_coadd.py` are illustrative -- verify them
  against the current SPHEREx Explanatory Supplement before publication.
* Pre-cache before any demo: run `step1_ingest.py` (or `make_demo_data.py`)
  ahead of time, because the judging environment may be offline or throttled.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: reproject` | `pip install reproject` inside the `spherex` env |
| `conda: command not found` | reopen the shell or `source ~/miniconda3/bin/activate` |
| `SIA2 query failed: 404` | endpoint moved -- check <https://irsa.ipac.caltech.edu/SIA> |
| `Vizier query failed: timeout` | lower `row_limit` in `step1_ingest.py` or shrink the radius |
| `No cached data for target` | run `step1_ingest.py` (or `make_demo_data.py`) first |
| `Only N cutouts cached` | need at least 2 exposures; widen the SIA2 radius or change `collection` |
| ZOGY map all zeros | `S.std() == 0` means epoch 1 and epoch 2 are identical inputs |
| Canvas blank | check the browser console; `/api/dashboard` must return `zogy_snr` |

## References

1. Alard & Lupton 1998, *A Method for Optimal Image Subtraction*, ApJ 503, 325
2. Crill et al. 2020, *SPHEREx: NASA's Near-Infrared Spectrophotometric All-Sky
   Survey*, Proc. SPIE 11443
3. IPAC/IRSA SPHEREx Archive Team, *Data Products & Quick Release 2/3
   Explanatory Supplement*
4. Kirkpatrick et al. 2021, ApJS 253, 7
5. Kuchner et al. 2017, *Backyard Worlds: Planet 9*, ApJL 841, L19
6. Luhman 2014, *Discovery of a ~250 K Brown Dwarf at 2 pc*, ApJL 786, L18
7. Molliere et al. 2019, *petitRADTRANS*, A&A 627, A67
8. Zackay, Ofek & Gal-Yam 2016, *Proper Image Subtraction (ZOGY)*, ApJ 830, 27
9. Zackay & Ofek 2017, *How to Coadd Images. I.*, ApJ 836, 187
