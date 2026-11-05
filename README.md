# spherex-lens

A public-facing **SPHEREx discovery portal**: ingest NASA/IRSA SPHEREx Level-2
cutouts, mask and co-add the six detector arrays, run ZOGY optimal image
subtraction, triage every detection against Gaia DR3 / AllWISE / AAVSO VSX /
JPL SBDB, classify sources with a 102-band spectral index classifier, and
validate the detector with an injection-recovery loop — all served by a FastAPI
backend with a RefractID-style light-theme dashboard.

This repository implements the *SPHEREx Discovery Portal: Complete Technical
Blueprint* file-for-file.

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

---

## Quick start

```bash
# 1. Environment
conda create -n spherex python=3.11 -y
conda activate spherex
pip install -r requirements.txt

# 2. Fetch real data (needs internet + optional IRSA account, see below)
python step1_ingest.py                   # writes data_cache/*.fits and *_meta.json

# 3. Run the pipeline end to end
python step2_coadd.py
python step3_zogy.py
python step4_triage.py
python step5_spectrum.py
python step6_injection.py

# 4. Serve the dashboard
uvicorn app:app --reload --port 8000     # then open http://localhost:8000
```

Offline / rehearsal mode (synthetic cache + all charts):

```bash
python make_demo_data.py     # writes a SPHEREx-shaped cache into data_cache/
python make_figures.py       # writes figures/*.png + figures/summary.{csv,json}
uvicorn app:app --port 8000
```

---

## Getting the data

### 1. Where the data lives

| Product | Archive | Endpoint |
|---|---|---|
| SPHEREx Level-2 images/cutouts (QR2, QR3) | NASA/IPAC IRSA | SIA2: `https://irsa.ipac.caltech.edu/SIA` · TAP: `https://irsa.ipac.caltech.edu/TAP` |
| Gaia DR3 | VizieR (CDS) | catalog `I/355/gaiadr3` |
| AllWISE | VizieR (CDS) | catalog `II/328/allwise` |
| AAVSO VSX | VizieR (CDS) | catalog `B/vsx/vsx` |
| JPL Small-Body Database | NASA/JPL SSD | REST: `https://ssd-api.jpl.nasa.gov/sbdb.api` |

Everything is public. Registering at
<https://irsa.ipac.caltech.edu/registration/> is optional but stops IRSA from
throttling bulk requests; use the same link to browse
<https://irsa.ipac.caltech.edu/Missions/spherex.html> and confirm sky coverage
before the event.

### 2. How to download it

**a) The script in this repo (recommended, does everything):**

```bash
python step1_ingest.py
```

`step1_ingest.py` loops over the five mandatory targets, issues a SIA2 cone
search (`POS`, `SIZE`, `COLLECTION=spherex_qr3`, `RESPONSEFORMAT=votable`),
scrapes the `uri` / `access_url` fields out of the VOTable response, streams
each FITS file into `data_cache/`, then queries the three VizieR catalogs and
writes one JSON per target.

**b) Any SIA2 client (pyvo):**

```python
from pyvo.dal import sia2

results = sia2.search("https://irsa.ipac.caltech.edu/SIA",
                      pos=(133.795129, -7.245147, 0.1),   # ra, dec, radius deg
                      collection="spherex_qr3")
table = results.to_table()
print(table["access_url"])          # one FITS cutout per row
url = table["access_url"][0]
```

`astroquery.ipac.irsa.Irsa.query_sia(...)` accepts the same SIA2 keywords
(`pos=`, `collection=`, `res_format=`) if you prefer astroquery. TAP works too
for catalogue-style queries across the whole mission:

```python
from pyvo.dal import TAPService
tap = TAPService("https://irsa.ipac.caltech.edu/TAP")
tap.search("SELECT TOP 10 * FROM spherex.artifact").to_table()
```

**c) The IRSA web UI** -- search by position or object name, tick the QR3
cutouts you want, "Download" gives you the same FITS files by hand.

### 3. What format you get

* **SIA2/TAP responses** are **VOTable XML** (`RESPONSEFORMAT=votable`) or CSV.
  They are *metadata only*: the pixels are at the URLs in the
  `access_url` / `uri` columns.
* **SPHEREx Level-2 cutouts** are **FITS** files, one per exposure and detector.
  The HDUs the pipeline reads:

  | HDU | Contents | Units |
  |---|---|---|
  | `IMAGE` | calibrated surface brightness + WCS | MJy/sr |
  | `FLAGS` | 32-bit contamination bitmask | - |
  | `VARIANCE` | per-pixel variance sigma^2 | (MJy/sr)^2 |
  | `CWAVE` | central wavelength per pixel | um |
  | `CBAND` | bandwidth per pixel | um |
  | `PSF` / `ePSF` | effective PSF cube | - |

  The WCS is TAN; `step2_coadd.py` reprojects everything onto one north-up
  tangent plane before co-adding. Missing `FLAGS`/`VARIANCE` are tolerated
  (zeros / ones), so the pipeline also runs on bare `IMAGE` files.
* **VizieR catalogs** come back as `astropy.table.Table` objects (VOTable/FITS
  under the hood). `step1_ingest.py` keeps only the columns triage needs:
  `RA_ICRS`/`DE_ICRS`/`pmRA`/`pmDE`/`Gmag` (Gaia), `RAJ2000`/`DEJ2000`/`W1mag`/
  `W2mag` (AllWISE), `RAJ2000`/`DEJ2000`/`Name`/`Type` (VSX).
* **JPL SBDB** returns **JSON** (`{"object": {...}, "orbit": {...}}`); it is an
  optional overlay and is not needed for the five target verdicts.

### 4. How to insert it into the project

The backend never touches the network -- it only reads `data_cache/`. So
"installing" data is two things: the FITS files, and one small JSON per target.

```
data_cache/
├── spherex_qr3_000.fits          # cutouts (any filename you like)
├── spherex_qr3_001.fits
├── ...
├── WISE_0855_meta.json           # the index the pipeline reads
└── ...
```

`data_cache/<Target>_meta.json` is the only contract:

```json
{
  "target": "WISE_0855",
  "meta": {"ra": 133.795129, "dec": -7.245147,
           "type": "Mover_YDwarf", "common": "WISE 0855"},
  "paths": ["data_cache/spherex_qr3_000.fits",
            "data_cache/spherex_qr3_001.fits",
            "data_cache/spherex_qr3_002.fits",
            "data_cache/spherex_qr3_003.fits"],
  "catalogs": {
    "gaia": [{"ra": 133.79, "dec": -7.25, "pm_ra": 0.0, "pm_de": 0.0, "gmag": 18.2}],
    "wise": [{"ra": 133.79, "dec": -7.25, "w1": 18.6, "w2": 18.7}],
    "vsx":  [{"ra": 133.79, "dec": -7.25, "name": "WISE 0855", "type": "VAR"}]
  },
  "spectrum_fits": "data_cache/demo_WISE_0855_spectrum.fits"
}
```

* `paths` order matters: `app.py` splits the list in half, co-adds each half as
  one epoch and subtracts the two stacks -- keep exposures time-sorted, and give
  the second epoch its own files. Fewer than two entries returns a clear HTTP
  500 ("Need at least 2 for subtraction").
* `paths` may be relative (as written by `step1_ingest.py`) or absolute.
* `catalogs`, `spectrum_fits` and extra keys are optional. Without `catalogs`
  every detection is triaged as `CANDIDATE`. `spectrum_fits` is the 102-band
  `SPECTRUM` table (`WAVELENGTH`, `FLUX`, `ERROR` columns): when present it
  drives the spectrum panel of `/api/dashboard` and of `fig04`; when absent the
  API falls back to the blueprint's synthetic ramp and the figures skip that
  panel.
* The filename stem **is** the API target key: `WISE_0855_meta.json` ->
  `?target=WISE_0855`, which is also what `/api/targets` and the dashboard
  dropdown enumerate.

To add a target by hand: copy the FITS cutouts into `data_cache/`, write the
JSON above with your own RA/Dec and file list, reload the page. No code change
is needed. FITS arrays are git-ignored (`.gitignore`), while the `*_meta.json`
files are small and worth committing so a fresh clone demos immediately.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/targets` | Targets present in `data_cache/` |
| `GET /api/dashboard?target=WISE_0855` | Runs stages 2-5 and returns `image`, `zogy_image`, `zogy_snr`, `sigma1`, `sigma2`, triaged `detections`, and `spectrum` (102 wavelengths, flux, classifier indices + label, plus the aperture flux at the target position) |
| `GET /api/recovery?target=WISE_0855` | Injection-recovery grid (`snr`, `mu`, `recovery`) |
| `GET /` | Static dashboard (`static/index.html`) |

The `/api/dashboard` response carries 180 x 180 nested arrays for `image`,
`zogy_image` and `zogy_snr` (~1.2 MB uncompressed, a few hundred KB gzipped).

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
| Barnard's Star | 2 | 0 | 2 | 35.2 | M-dwarf / Mira red giant |
| Epsilon Indi B | 4 | 2 | 2 | 34.0 | T-type brown dwarf |
| WISE 0855 | 3 | 2 | 1 | 33.8 | Ultracool Y-dwarf |
| RW Aurigae | 2 | 2 | 0 | 34.9 | Dusty T Tauri / variable star |
| R Draconis | 3 | 0 | 3 | 33.0 | M-dwarf / Mira red giant |

*(numbers from the cache currently in `data_cache/`; they shift slightly if you
regenerate with `--overwrite` because the star field is re-drawn)*

**The demo data is synthetic and must never be presented as NASA data.** It is
there to make the pipeline, the dashboard and the figures reproducible offline;
point `data_cache/` at real IRSA cutouts to get real science.

### `make_figures.py` -- every chart the submission needs

```bash
python make_figures.py                          # all targets, detail = WISE 0855
python make_figures.py --detail-target RW_Aurigae --trials 40
```

It re-runs stages 2-6 on whatever is cached (real or synthetic) and writes
PNGs plus machine-readable numbers into `figures/`:

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

## Repository layout

```
spherex-lens/
├── data_cache/              # FITS cutouts + *_meta.json (arrays are git-ignored)
├── figures/                 # generated charts + summary.csv/json
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

