# SPHERExLens — 7-minute narrated slide video

**Target:** all 30 slides, **7:00 exactly**, one voice, no cuts to camera. **Spoken length:** 835 words
— about **120 words per minute**, slower than conversation and right for technical slides because the
audience is reading the diagram while you talk.

Assets produced alongside this script:

| File | What it is |
|---|---|
| `video_frames/slide-01.png … slide-30.png` | every slide rendered at 1920×1080 |
| `slides_7min.srt` | the narration as subtitles, timed to the durations below |
| `slides_7min_concat.txt` | ffmpeg concat list with the exact per-slide durations |

---

## Timing sheet

| # | Slide | In | Out | Sec |
|---|---|---|---|---|
| 1 | Title — *Finding What Changed* | 0:00 | 0:09 | 9 |
| 2 | Two visits reveal what one image cannot | 0:09 | 0:27 | 18 |
| 3 | What the pipeline has to output | 0:27 | 0:44 | 17 |
| 4 | What the project set out to measure | 0:44 | 0:56 | 12 |
| 5 | Forty years of subtracting one image from another | 0:56 | 1:09 | 13 |
| 6 | Every survey does the same four things | 1:09 | 1:20 | 11 |
| 7 | Six arrays, 102 bands, one sweep every six months | 1:20 | 1:33 | 13 |
| 8 | Eighteen fields, fetched as 5 MB stamps | 1:33 | 1:45 | 12 |
| 9 | Seven subsystems, one science core | 1:45 | 1:56 | 11 |
| 10 | When an archive goes down… | 1:56 | 2:07 | 11 |
| 11 | Fourteen defect bits go, two stay | 2:07 | 2:21 | 14 |
| 12 | Subtract in the Fourier domain | 2:21 | 2:41 | 20 |
| 13 | Why 6σ is a decision, not a habit | 2:41 | 2:59 | 18 |
| 14 | Three shapes, three physical stories | 2:59 | 3:10 | 11 |
| 15 | Three defects, three fixes, one recalibration | 3:10 | 3:33 | 23 |
| 16 | Split one visit in half and subtract | 3:33 | 3:49 | 16 |
| 17 | Plant 12 240 sources and count what comes back | 3:49 | 4:05 | 16 |
| 18 | Asteroids move faster than anything else | 4:05 | 4:16 | 11 |
| 19 | Six verdicts, and one means “unknown” | 4:16 | 4:29 | 13 |
| 20 | A ranking aid that never overrides catalogues | 4:29 | 4:46 | 17 |
| 21 | Five independent streams, one score | 4:46 | 4:56 | 10 |
| 22 | At the ecliptic pole, seen 20 800 times | 4:56 | 5:07 | 11 |
| 23 | 102 bands, read as a spectrum | 5:07 | 5:17 | 10 |
| 24 | One FastAPI service, seven routers | 5:17 | 5:27 | 10 |
| 25 | Reads and live work run in separate services | 5:27 | 5:37 | 10 |
| 26 | A mission console in the browser | 5:37 | 5:47 | 10 |
| 27 | Eighteen fields, 109 detections, 99 explained | 5:47 | 6:05 | 18 |
| 28 | Ten candidates, five fields, one caveat | 6:05 | 6:21 | 16 |
| 29 | Six moves, one browser tab | 6:21 | 6:45 | 24 |
| 30 | Works, honest, and open | 6:45 | 7:00 | 15 |

Nine seconds is the shortest cue (the title) and twenty-four the longest (the demonstration), so no
slide flashes past unread and none outstays its point.

---

## Narration (record straight through)

**0:00 — Slide 1.** SPHERExLens — finding what changed in NASA's SPHEREx all-sky infrared survey.

**0:09 — Slide 2.** One image shows where things are. Two visits show what changed. Subtract them and
the sky cancels, leaving a dipole if a source moved, or a monopole if it brightened or faded — but
instrumental residuals outnumber the real events a thousand to one.

**0:27 — Slide 3.** The task: two 180-pixel cut-outs, one significance map, a six-sigma threshold —
109 detections. Each one must be identified as catalogue star, asteroid, artefact, or candidate.
Public data only, every number reproducible.

**0:44 — Slide 4.** Four questions: is the map calibrated? Can we separate dipoles from monopoles at
three arcseconds? How much do catalogues explain? And what did we miss?

**0:56 — Slide 5.** Forty years of subtraction — kernel fitting, then discrete kernels, then ZOGY:
closed-form Fourier subtraction with an analytic null. That null is why we can quote a false-alarm
rate.

**1:09 — Slide 6.** Every survey detects, vetoes, triages, then hands to a human. Brokers do this for
optical alerts — nothing end-to-end was published for SPHEREx pixels.

**1:20 — Slide 7.** SPHEREx: a twenty-centimetre telescope, 102 bands from 0.75 to 5 microns, six
detector arrays, six FITS extensions per cut-out, six-month cadence. Every pixel has its own colour.

**1:33 — Slide 8.** We fetch five-megabyte stamps rather than seventy-megabyte frames: eighteen
fields, 246 cut-outs, each accepted only with margin and few flagged pixels.

**1:45 — Slide 9.** Seven subsystems — ingest, imaging, ZOGY, triage, machine learning, deep fields,
evaluation — sharing one JSON contract and 21,429 lines of Python.

**1:56 — Slide 10.** Every archive call has a fallback: Gaia through three mirrors, asteroids through
JPL and CADC, optical checks through NEOSSat. Failures are reported, never hidden.

**2:07 — Slide 11.** Fourteen defect bits are rejected, but SOURCE and FULLSAMPLE are kept — rejecting
them erased real flux. Masked pixels are dropped in both epochs, so a hole cannot fake a dipole.

**2:21 — Slide 12.** ZOGY convolves each image with the other's point-spread function and divides by
the combined noise — nothing is fitted. The output is a flux difference and a significance map that
is unit-variance Gaussian wherever the sky did not change. Two FFTs, under 0.6 seconds.

**2:41 — Slide 13.** At five sigma the theory predicts 0.02 false pixels per frame; the real data gave
0.86 per field. At six sigma that halves, with no loss of recovery. The threshold is calibrated, not
inherited.

**2:59 — Slide 14.** Three shapes tell the story: a dipole means motion, a positive peak means
brightening, a negative peak means fading. Geometry is the physics.

**3:10 — Slide 15.** The turning point. Our first detector recovered 0.2 per cent — three
architectural defects: a global noise scale, unconstrained dipole pairing, and a fixed bright-star
mask. Robust MAD scaling, physical pairing and exponential masking took recovery to 69.3 per cent.

**3:33 — Slide 16.** To measure false alarms we split one visit in two and subtract: no real change
can exist. Two hundred and twenty realisations gave 8,854 raw peaks, cut to 1.66 false candidates per
field — zero in six star fields.

**3:49 — Slide 17.** Then we plant 12,240 sources and count: 96.7 per cent of brightenings, 95.3 of
fadings, 56 of movers — slow movers hide inside the PSF. Overall: 69.3.

**4:05 — Slide 18.** Asteroids move arcminutes per hour and mimic flares, so we identify them: JPL
Horizons, the Small-Body Database, CADC cross-checks — time-boxed at twenty-five seconds.

**4:16 — Slide 19.** Identity needs time: Gaia is epoch 2016, SPHEREx is 2026. Propagating proper
motion — Barnard's Star moves 94 arcseconds — turns a phantom candidate into KNOWN_MOVER.

**4:29 — Slide 20.** A classifier on twenty-two features ranks candidates. The headline 0.96 precision
is inflated by synthetic dipoles; honest monopole skill is 0.90 and 0.82. It ranks — it never
overrules a catalogue.

**4:46 — Slide 21.** Five weighted streams — machine learning, significance, kinematics, asteroid
clearance, optical confirmation — combine into one explainable score.

**4:56 — Slide 22.** At the ecliptic pole the same star is seen twenty thousand eight hundred times.
Spectral normalisation removes the filter artefact: one of six stars variable, matching Gaia.

**5:07 — Slide 23.** 102 bands give a spectrum without follow-up: methane, water and near-infrared
slope separate brown dwarfs, icy bodies and young stars — above thirty usable bands.

**5:17 — Slide 24.** One FastAPI service, thin routers, a bounded job pool — one running, eight
waiting — rate limited and correlated.

**5:27 — Slide 25.** Reads and live work run in separate services: concurrency eighty for snapshots,
one for streaming, refreshed every twelve hours.

**5:37 — Slide 26.** The console is 7,611 lines of vanilla web code: no build step, two-stage canvas,
offline caching.

**5:47 — Slide 27.** The census: 109 detections across eighteen fields — 99 known, 10 candidates.
Detection count is not discovery count. Two fields return nothing: M42 is a null sky, Sgr A-star has
no shared band.

**6:05 — Slide 28.** The ten sit in five fields — Epsilon Indi B, WISE 0855, M45, TRAPPIST-1, 3C
273 — faint infrared excesses beyond Gaia and WISE. Barnard's Star, our first candidate, was a
coordinate bug.

**6:21 — Slide 29.** Live: select Barnard's Star and run. A 36.2-sigma dipole, carded as KNOWN_MOVER.
Toggle co-add, difference, significance. Play six months of snapshots. The Crab Nebula for extended
gas. The accuracy panel — every number here comes from it.

**6:45 — Slide 30.** It works: 69.3 per cent recovery. It is honest: 1.66 false candidates per field.
And it is open: reproducible from NASA's archive. Thank you.

---

## How to produce it

**Option A — record the deck directly (fastest).** Open `spherexlens_beamer.pdf` in a presenter that
advances on a timer (Keynote, PowerPoint, or `pdfpc`), set the per-slide durations above, record the
screen at 1080p while you speak, and stop on slide 30.

**Option B — assemble from the frames (cleanest).** The renders and the concat list already exist. On
any machine with ffmpeg:

```bash
cd paper
ffmpeg -f concat -safe 0 -i slides_7min_concat.txt \
       -vf "fps=30,format=yuv420p" -c:v libx264 -crf 20 -movflags +faststart \
       slides_7min_silent.mp4            # 7:00, no audio
# then lay the voice track over it and attach the subtitles:
ffmpeg -i slides_7min_silent.mp4 -i voice.wav -i slides_7min.srt \
       -map 0:v -map 1:a -map 2:s -c:v copy -c:a aac -b:a 192k \
       -c:s mov_text -metadata:s:s:0 language=eng slides_7min.mp4
```

**Option C — silent with captions only.** Use `slides_7min.srt` as the script: the timings already
match the frame durations, so a muted autoplay version needs no re-timing.

---

## Delivery notes

- **One idea per cue.** Each line is a single claim plus its number. If you want to add a second
  sentence, cut it instead — the slide is already carrying it.
- **Pause on the numbers.** 109, 6σ, 69.3, 1.66, 36.2 — half a beat each. Those are the five things a
  viewer will repeat afterwards.
- **Don't read the slide.** The narration says what the diagram *means*; the audience can read the
  labels themselves.
- **Record in one take where you can.** Thirty hard cuts hide small stumbles far better than a spliced
  edit does.
- **Loudness.** Normalise the voice to −16 LUFS for web, −23 LUFS for broadcast.
- **If you must cut 30 seconds,** drop slides 24–26 (the three implementation slides) to five seconds
  each and give the six saved seconds to slide 15 and slide 29 — the two places viewers rewind.


