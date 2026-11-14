# `SPHEREXFINALV2` — audit and integration plan

Source of truth: `paper/slides_and_script.pdf` (30 slides, rebuilt from
`slides_and_script.tex`). Target: the Canva presentation **SPHEREXFINALV2**
(`DAHa6NJ2RcM`, 33 pages, 1920×1080, OpenSauceOne, warm-white `#F6F3EF`).

The Canva connector went offline part-way through this pass, so this file
records exactly what was verified, what still has to be changed, and which
already-rendered diagram goes on which page. Everything below is derived from
the exported deck (`pdftotext`/`pdffonts`) and from the rebuilt PDF, not from
guesswork.

---

## 1. What is already right in the deck

* Font is **OpenSauceOne** (Regular / SemiBold / Bold / Italic) on every page —
  the requested "open sauce" face is already the deck default, so replaced text
  keeps it automatically.
* Page background is the same warm white as the title slide on 26 of 33 pages
  (`#F6F3EF`); six pages drift to `#F0F5FA` / `#FBF6F1` / `#F4F8FD`, which is
  visually indistinguishable but can be unified if desired.
* Headline numbers 18 fields / 246 cutouts / 109 detections / 99 KNOWN /
  10 CANDIDATE agree with the PDF.

## 2. Corrections (verified against the rebuilt PDF)

| Page | Find | Replace with | Why |
|---|---|---|---|
| 2 | `Challenge statementS` | `Challenge Statements` | typo |
| 6 | `Two shapes of changes - Dipole (moved) / Monopole (brightened)` | `Three morphologies — dipole (moved), positive monopole (brightened), negative monopole (faded)` | PDF slides 2/14 |
| 9 | `Threshold \|S\| > 5σ` | `Threshold \|S\| > 6.0σ` | v2 operating point |
| 10 | `Can a dipole been told from a monopole at 3" ?` | `Can a dipole be told from a monopole at 3″?` | grammar |
| 15 (Step 2) | `16 FLAGS classes are rejected: SUR transients, overflow, phantom, edge, hot/cold, persistence` | `14 defect bits rejected — cosmic rays (SUR), saturation, hot/cold pixels, persistence, statistical outliers. Bits 12 (FULLSAMPLE) and 21 (SOURCE) are deliberately preserved.` | PDF slide 11 |
| 18 | `The 5 sigma Threshold` | `The 6.0σ Threshold` | PDF slide 13 |
| 18 | `Five sigma means roughly one false pixel every fifty frames.` | `At 5σ the analytic budget is 0.0186 false pixels per 180² frame and the empirical null gave 0.86 false candidates per field; at 6.0σ that falls to 0.48 with no loss of strict recovery.` | PDF slide 13 |
| 19 | `Barnard's Star example: two pixels in 216 days measured about 25 arcseconds per year, versus about 10 expected.` | `Barnard's Star: 94″ over 9.8 yr ≈ 10.4″/yr — the propagated Gaia vector lands on the SPHEREx centroid, resolving the 36.2σ dipole as KNOWN_MOVER.` | PDF slides 19, 28 |
| 20 (Step 4) | `within a 5 to 30 arcsecond search radius` | `within 10″ for faint stars (G ≥ 16), scaling to 120″ for G ≤ 9, with a 180″ bright-star halo veto` | PDF slide 19 |
| 24 | `Seven stage scripts contain 2,397 lines; five support modules contain 1,826; ap.p.py contains 2,859, for 7,082 runtime Python lines.` | `The v1 scripts (seven stages, 2,397 lines; five support modules, 1,826; app.py, 2,859) are now a modular backend of 21,429 runtime Python lines across ingest, imaging, triage, ml, deep, eval and routes.` | PDF slides 1, 9, 24, 30 |
| 26 | `6,982 lines of zero-build vanilla web code` | `7,611 lines of zero-build vanilla web code` | PDF slide 26 |


## 3. Missing material to add (PDF slides with no deck page)

These are the v2 results the current deck never shows. Each already has a
finished diagram in `paper/deck_figs/`:

| PDF slide | New page content | Diagram |
|---|---|---|
| 15 | **Decision A** — three fatal defects (global σ, unconstrained 75″ dipole pairing, fixed 3′ bright-star mask) → robust MAD, physical pairing (μ_max, 2.0 flux ratio), exponential masking. 0.2 % → 69.3 % strict recovery | `fig15.png` |
| 16 | **Null test** — 220 split-epoch realisations, 8,854 raw peaks, 1.66 false CANDIDATE/field, 0 in six star fields | `fig16.png` |
| 17 | **Injection–recovery** — 12,240 injections, SNR 3–20 × μ 2–20″/yr; 96.7 / 95.3 / 56.0 / 69.3 % | `fig17.png` |
| 18 | **Asteroid engine** — JPL Horizons + SBDB + CADC SSOIS, 25 s fail-don't-fake time-box | `fig18.png` |
| 20 | **Machine learning** — HGB on 12,150 labels, 22 features, isotonic calibration, honest 0.90/0.82 and 0.72/0.63 | `fig20.png` |
| 21 | **Evidence engine** — five weighted streams → 0.88 believability, per-factor explanation | `fig21.png` |
| 22 | **Deep fields** — NEP geometry, spectral normalisation, 1 of 6 variables matching Gaia DR3 | `fig22.png` |
| 29 | **Live demonstration** — six moves mapped to real DOM controls | `fig29.png` |
| 30 | **Conclusion** — Works / Honest / Open Horizons + artefact card | `fig30.png` |

Python figures (use the originals from `figures_hires/`, unmodified):

| Figure | Page |
|---|---|
| `fig01_band_coverage.png` | Background → SPHEREx band coverage |
| `fig05_recovery.png` | Injection–recovery page |
| `fig03_detections.png` | Results I |
| `fig07_targets_summary.png` | Results II |

## 4. Diagram placement

Every page has a 96-column ink map; these are the largest free rectangles found
(1920×1080 coordinates, `x, y, w, h`):

| Page | Free rectangle | Suggested diagram |
|---|---|---|
| 24 Impl. I | `0, 700, 1920, 380` | `fig24.png` (backend) |
| 26 Impl. III | `0, 680, 1920, 400` | `fig26.png` (console + canvas) |
| 25 Impl. II | `40, 0, 1000, 280` | `fig25.png` (cloud topology) |
| 5 Archive | `420, 200, 1000, 140` | `fig08.png` (sky map) |
| 4 Background | `0, 0, 760, 340` | `fig07.png` (focal plane) |
| 28 Results I | `320, 880, 1600, 200` | `fig03_detections.png` |

Pages whose free space is thinner than ~220 px cannot take a diagram without
moving existing blocks; those are the ones that need a real re-layout, listed
in §3 as new pages instead.

## 5. How the deck should be edited

1. `start-editing-transaction` on `DAHa6NJ2RcM`.
2. `replace_text` with the strings in §2. Element IDs are
   `<page_id>-<element_suffix>`, and the suffix survives copying, so a
   single-page `copy-design` + transaction reveals the IDs for any page.
3. `insert_fill` the PNGs from `paper/deck_figs/` (240 dpi, white ground, 16 px
   margin) into the rectangles in §4, keeping the aspect ratio.
4. `commit-editing-transaction`.

All diagram files are ready; only the Canva write operations remain.


## 6. Housekeeping

Two scratch designs were created in the account while reading element IDs and
can safely be deleted:

* `DAHa6LWIXqE` — single-page copy of page 1
* `DAHa6NN5tLg` — copy of pages 12–14

The original (`DAHa6NJ2RcM`) was never modified by those copies.
