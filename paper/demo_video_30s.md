# SPHERExLens — 30-second demonstration video

**Format:** 1920×1080, 30 fps (shoot 60 if you can, export 30), hard cuts only — no transitions,
no music bed louder than −18 LUFS. **Voice-over:** 72 words, ~145 wpm. **Captions:** every line is
duplicated on screen so the video works muted.

Every action below names the real control in `site/index.html`, so the recording is repeatable.

---

## Shot list

| # | Time | Action (exact control) | What the viewer sees | Voice-over |
|---|---|---|---|---|
| 1 | 0:00–0:03 | Console at rest; cursor drifts to **Run Pipeline** (`#btn-run`) | Full console, dark UI, telemetry strip | "SPHERExLens — NASA's all-sky infrared survey, in a browser." |
| 2 | 0:03–0:07 | Target (`#target-select`) → **Barnard's Star**; click **Run Pipeline** | Loader → co-add paints → four KPI cards count up → verdict card: **KNOWN_MOVER · 36.2σ** | "Pick a target, run it — the difference image appears in seconds." |
| 3 | 0:07–0:11 | Layer switch (`#layer-switch`): **Co-add** → **Diff D** → **Sig S** | Stars vanish, dipole appears, then the significance map blooms | "Co-add, difference, significance: see what changed." |
| 4 | 0:11–0:14 | **Snapshot ▸ Play** (`#btn-snapshot-play`) | Time-lapse of retained visits, purple centroid trail; cut mid-play | "Play six months of visits as one animation." |
| 5 | 0:14–0:18 | **Next candidate** (`#vet-next`) | Verdict flips to **CANDIDATE**, evidence score **0.94**, top reason listed | "Every detection is scored — mover, or candidate." |
| 6 | 0:18–0:21 | Toggle **Catalogues** (`#tog-cat`) and **Detections** (`#tog-det`); colour ramp (`#lut-select`) → **Inferno** | Overlay markers drop in; the image turns from grey to hot orange | "Overlay catalogues, switch colour ramps." |
| 7 | 0:21–0:24 | View switch **All-sky** (`#view-switch` → `data-view="sky"`) | Mosaic fills the screen; slow 20 % push-in | "Zoom out to the all-sky mosaic." |
| 8 | 0:24–0:27 | **Accuracy** (`#btn-accuracy`) → recovery chart (`#chart-recovery`) | Null-test curve, then **69.3 %** recovery bar | "69 percent recovery, 1.66 false alarms per field." |
| 9 | 0:27–0:29 | **Export** (`#btn-export`) — or **Help classify** (`#btn-win-classify`) if you prefer the vote story | Download list slides in / vote buttons pulse | "Export it, or vote on it." |
| 10 | 0:29–0:30 | End card (brand colour, centred wordmark + URL) | `SPHERExLens` / one-line URL | "SPHERExLens. Find what changed." |

**Why this order:** hook → the one action that matters (Run) → the three layers → time travel →
the honesty story (candidate + evidence) → visual payoff → scale → credibility → give-away → brand.
Every shot changes the pixels on screen, so nothing feels like filler at 2–4 seconds a shot.

---

## Voice-over only (for the booth recording)

> SPHERExLens — NASA's all-sky infrared survey, in a browser.
> Pick a target, run it — the difference image appears in seconds.
> Co-add, difference, significance: see what changed.
> Play six months of visits as one animation.
> Every detection is scored — mover, or candidate.
> Overlay catalogues, switch colour ramps.
> Zoom out to the all-sky mosaic.
> 69 percent recovery, 1.66 false alarms per field.
> Export it, or vote on it.
> SPHERExLens. Find what changed.

72 words. Read it at a brisk, even pace; do not pause between sentences — the cuts are the rhythm.

---

## Caption version (muted autoplay)

| Time | On-screen text |
|---|---|
| 0:00 | **SPHERExLens** · NASA's infrared sky, in a browser |
| 0:03 | Pick a target → **Run Pipeline** |
| 0:07 | Co-add → Difference → Significance |
| 0:11 | Six months of visits, one animation |
| 0:14 | Scored: **KNOWN_MOVER** or **CANDIDATE** |
| 0:18 | Catalogues and detections, overlaid |
| 0:21 | All-sky mosaic |
| 0:24 | **69.3 %** recovery · **1.66** false alarms / field |
| 0:27 | Export · vote · inspect |
| 0:29 | **spherexlens** — find what changed |

---

## Pre-flight (five minutes before recording)

1. **Warm the cache.** Open Barnard's Star once and run it, so shot 2 is a re-run from cache
   (milliseconds, not a cold IRSA fetch).
2. **Confirm Play is enabled.** `#btn-snapshot-play` is disabled until at least two retained
   snapshots exist — check the snapshot selector shows more than "latest".
3. **Open Accuracy once** so the charts are already rendered when you click in shot 8.
4. **Browser hygiene:** 100 % zoom, 1920×1080 window, bookmarks bar hidden, notifications off,
   one tab only, cursor size default.
5. **Cursor discipline:** move in straight lines, park before clicking, and never hunt. If a click
   misses, cut and re-record that shot — do not repair in the edit.

---

## Fallbacks

- **Cold start too slow?** Use `#btn-demo` (Auto Demo) which scripts the same sequence, and cut to
  it after the first second.
- **Network down?** The service worker serves cached snapshots: shots 1–7 still work; skip shot 9's
  export if the download list is empty.
- **Accuracy panel empty?** It is populated from the published snapshot — if it is blank, the
  snapshot did not load; reload once rather than showing an empty modal.

---

## Export

H.264, 1080p, 30 fps, ~10 Mbps, stereo AAC 192 kbps. No fade in — start on a frame, not black.
Hold the end card for a full second so a looping player does not flash it.
