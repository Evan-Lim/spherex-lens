# The report (`paper/`)

`spherexlens_paper.tex` is a single self-contained LaTeX file containing:

* **Part I — technical report** (problem statement, related work, data, methodology, revised code,
  results, validation, discussion, limitations, conclusion, references, appendices).
* **Part II — presentation**: a 12-slide deck for an 8-minute talk, where every slide is reproduced
  as a card with its *real* chart (Ti*k*Z/pgfplots or a rendered figure from `figures_real/`), plus
  the complete word-for-word script with per-slide timings.

## Build

```bash
cd paper
pdflatex spherexlens_paper.tex
pdflatex spherexlens_paper.tex      # second pass: table of contents + refs
```

Output: `spherexlens_paper.pdf` (33 pages, ~1.9 MB). Requires TeX Live with
`tikz`, `pgfplots`, `tcolorbox`, `listings`, `booktabs`, `longtable`, `fancyhdr`, `hyperref`.

The four photographic figures are read from `../figures_real/` (produced by
`python make_figures.py --real`); every other chart is generated from numbers taken from
`site/api/*.json`, so nothing in the document is a placeholder.

## What is where

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
