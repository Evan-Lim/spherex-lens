# The report (`paper/`)

Two LaTeX documents live here, both self-contained and both compiled with `pdflatex`:

| File | What it is |
|---|---|
| `spherexlens_paper.tex` | the **33-page technical report**: problem statement, related work, data, methodology, revised code, results, validation, discussion, references, appendices, plus a 12-slide deck |
| `slides_and_script.tex` | the **25-page presentation specification**: 23 slides for a 21:40 talk (15:45 short route), one page per slide in the reference document's layout -- `Slide N — Title … time`, `On the slide.` bullets, `Figure / diagram.` brief, the figure itself scaled to the text width, then `Spoken script.` -- plus a live-demonstration page and a backup/Q&A page |

## Build

```bash
cd paper
pdflatex spherexlens_paper.tex   && pdflatex spherexlens_paper.tex
pdflatex slides_and_script.tex   && pdflatex slides_and_script.tex
```

Outputs: `spherexlens_paper.pdf` (~1.9 MB) and `slides_and_script.pdf` (~0.74 MB).
Requires TeX Live with `tikz`, `pgfplots`, `tcolorbox`, `listings`, `booktabs`, `longtable`,
`fancyhdr`, `hyperref`.

Four photographic figures are read from `../figures_real/` (produced by
`python make_figures.py --real`); every other chart in both documents is generated from numbers
taken from `site/api/*.json`, so nothing is a placeholder.

## Presentation structure (`slides_and_script.pdf`)

| Pages | Content |
|---|---|
| 1 | title, deck map (page / slide / visual / time / 15-min route) and production rules |
| 2–12 | why it matters: difference imaging, problem statement, focus + RQs, prior art I–II, SPHEREx data, archive mechanics, architecture, theory I–II (masking/co-add, ZOGY) |
| 13–17 | theory III–VI (detection statistics, dipole vs monopole, triage, spectra) and validation |
| 18–20 | technical implementation: code layout, three deployment shapes, front end |
| 21–22 | results I–II (18 fields, the 17 candidates) |
| 23 | **live demonstration** -- six numbered steps mapped onto the actual screen |
| 24 | conclusion, contributions, references |
| 25 | backup: five anticipated questions + pre-flight checklist |

Timings printed on each page are speaking time. The full route is 21:40; the 16 slides marked ✓
in the deck map form a 15:45 route.


| Pages | Content |
|---|---|
| i, 1–3 | title, abstract, contents |
| 4–8 | §1 introduction + problem statement, §2 related work + gap table |
| 9–12 | §3 data, field sample, volumes |
| 12–17 | §4 methodology (6 stages, ZOGY mathematics, triage, serving, statistics) |
| 17–21 | §5 implementation + revised code (R1–R5), testing |
| 21–24 | §6 results (per-field table, charts, negative results) |
| 24–27 | §7 validation, §8 discussion, §9 limitations/future, §10 conclusion |
| 27–30 | references, appendices (parameter table, API reference) |
| 31–33 | Part II: slides 1–12 with charts and script, anticipated questions |
