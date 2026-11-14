# Deck diagrams (`paper/deck_figs/`)

`fig01.png` … `fig30.png` are the **original TiKZ diagrams** from
`../slides_and_script.tex`, one per slide, rendered at 240 dpi on a white
background with a 16 px margin (so they sit cleanly on the `#F6F3EF` slide
background without a visible box).

They are *not* screenshots of `slides_and_script.pdf`: each figure was lifted
out of the source as its own `tikzpicture`, wrapped in a `preview`-package
standalone document, and compiled. That keeps the vector quality and lets them
be re-rendered at any size.

## Regenerate

```bash
cd paper
python3 - <<'PY'
import os
src = open('slides_and_script.tex').read()
pre = src.split('\\begin{document}')[0]
os.makedirs('deck_figs', exist_ok=True)
idx = n = 0
while True:
    i = src.find('\\figbox{', idx)
    if i < 0:
        break
    j, depth = i + len('\\figbox{'), 1
    while depth:
        depth += {'{': 1, '}': -1}.get(src[j], 0)
        j += 1
    body = src[i + len('\\figbox{'):j - 1]
    idx, n = j, n + 1
    open(f'deck_figs/fig{n:02d}.tex', 'w').write(
        pre + '\n\\usepackage[active,tightpage]{preview}\n'
              '\\PreviewEnvironment{tikzpicture}\n\\begin{document}\n'
              + body + '\n\\end{document}\n')
print('extracted', n)
PY

cd deck_figs
for f in fig*.tex; do b=${f%.tex}; pdflatex -interaction=nonstopmode -halt-on-error "$f" >/dev/null; done
for f in fig*.pdf; do b=${f%.pdf}
  pdftoppm -png -r 240 -singlefile "$f" "$b"
  magick "$b.png" -bordercolor white -border 12 -trim +repage -bordercolor white -border 16 "$b.png"
done
```

## Slide → file

| File | `slides_and_script.tex` slide | Topic |
|---|---|---|
| fig01 | 1 | Observation sequence + hero metric cards |
| fig02 | 2 | Why difference imaging (four panels) |
| fig03 | 3 | Problem formulation, verdict fan-out |
| fig04 | 4 | Research questions and scope |
| fig05 | 5 | Prior art I: subtraction lineage |
| fig06 | 6 | Prior art II: brokers and the gap |
| fig07 | 7 | SPHEREx focal plane, D1–D6, FITS extensions |
| fig08 | 8 | Sky map of 18 fields + SIA2/S3 ingest |
| fig09 | 9 | Subsystem architecture |
| fig10 | 10 | Archive fallback waterfall + mode table |
| fig11 | 11 | Flag census + cross-epoch mask matching |
| fig12 | 12 | ZOGY signal flow + Gaussian null |
| fig13 | 13 | False-positive budget vs threshold |
| fig14 | 14 | Dipole / positive / negative monopole |
| fig15 | 15 | Decision A: three defects → three fixes |
| fig16 | 16 | Split-epoch null test + per-field bars |
| fig17 | 17 | Injection-recovery heatmap + v1/v2 bars |
| fig18 | 18 | Asteroid ephemeris architecture |
| fig19 | 19 | Epoch propagation + six-way triage |
| fig20 | 20 | ML pipeline + honest precision/recall |
| fig21 | 21 | Multi-factor evidence engine + card |
| fig22 | 22 | NEP deep field geometry + light curves |
| fig23 | 23 | 102-band spectroscopy + templates |
| fig24 | 24 | FastAPI backend architecture |
| fig25 | 25 | Cloud topology + S1–S21 security matrix |
| fig26 | 26 | Mission-console wireframe + canvas pipeline |
| fig27 | 27 | 18-field census (KNOWN vs CANDIDATE) |
| fig28 | 28 | WISE 0855 sky offsets + Barnard's Star |
| fig29 | 29 | Live-demo console map (six moves) |
| fig30 | 30 | Works / Honest / Open Horizons |

The Python figures live in `../figures_hires/` (`fig01_band_coverage.png` …
`fig08_photometry.png`) and are used *unmodified*, as requested.
