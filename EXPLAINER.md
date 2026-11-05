# SPHERExLens, explained for humans

**A plain-language guide to what this project is, what it does, and how to read
every number on the screen. No astronomy and no programming knowledge assumed.**

---

## 1. The one-paragraph version

NASA's SPHEREx space telescope photographs the entire sky every six months, in
102 different colours of infrared light. Because it keeps re-photographing the
same places, you can compare pictures of the same patch of sky taken months
apart: things that **move** (nearby stars, asteroids) or **change brightness**
(variable stars) show up as differences. This project is a website that does
that comparison automatically for five famous objects. It downloads the real
NASA files, cleans them, stacks them, subtracts one epoch from the other, finds
the significant differences, asks *"is this thing already in a catalogue?"*, and
prints a verdict: **KNOWN** (we know what it is) or **CANDIDATE** (nobody has
catalogued it -- worth a look).

Everything you see is traceable: for every picture the site shows, there is a
row in the *Image sources & provenance* table telling you exactly which NASA
archive file it came from, with a link you can click.

---

## 2. What you are looking at, in physical terms

### 2.1 The sky has an address system

Astronomers locate things with two angles, like latitude and longitude on Earth:

* **Right Ascension (RA)** -- the "longitude", measured in hours (0-24h) or
  degrees (0-360°). One hour = 15°.
* **Declination (Dec)** -- the "latitude", from -90° (south pole) to +90° (north
  pole).

So `RA 269.452°, Dec +4.693°` is a precise address on the sky (it is Barnard's
Star). Angles on the sky are small, so astronomers use **arcminutes** (') and
**arcseconds** ("): 1° = 60' = 3600". The full Moon is about 30' across. This
website looks at a square **9 arcminutes on a side** -- roughly a third of the
Moon's width, which is a comfortable "patch of sky" for a telescope.

### 2.2 Why infrared

Visible light gets blocked by dust, and cool objects (brown dwarfs at a few
hundred kelvin) barely glow in visible light at all. In the near-infrared
(0.75-5 micrometres) those objects are bright, dust is transparent, and
molecules like water and methane leave deep fingerprints. That is exactly the
wavelength range SPHEREx was built for.

### 2.3 What SPHEREx actually is

* A 20 cm telescope -- small, deliberately: it must fit in a rocket and be cheap
  enough to *survey* rather than to *stare*.
* **Six detector arrays**, each 2040 x 2040 pixels, each sitting under a
  "linear variable filter" (LVF): a piece of glass whose colour changes from one
  edge to the other. So one exposure is not one colour -- each **row** of the
  detector is a slightly different wavelength.
* 6 arrays x 17 wavelength channels each = **102 bands**, from 0.75 µm to
  5.0 µm.
* Pixels are big: one pixel covers about **6.2 arcseconds** of sky. (Compare:
  Hubble sees 0.05" in one pixel.) SPHEREx is a wide-angle survey machine, not a
  close-up machine.
* It sweeps the whole sky every six months, so the same patch is re-imaged with
  a gap of months -- perfect for finding things that change.

### 2.4 The five targets (and why these five)

| Target | What it is | Why it is interesting |
|---|---|---|
| **Barnard's Star** | A red dwarf (M4V), 6 light-years away | The fastest-moving star in our sky: 10.4"/year. Over months it visibly shifts. |
| **Epsilon Indi B** | A brown dwarf pair (T1+T6) | A "failed star": too light to fuse hydrogen, so it just glows warm. |
| **WISE 0855** | A Y-dwarf at ~250 K | One of the coldest objects known outside our solar system -- barely warmer than the Earth's surface on a winter night. |
| **RW Aurigae** | A T Tauri star | A baby star still surrounded by a disc; it flickers unpredictably. |
| **R Draconis** | A Mira variable | A dying star that pulses, changing brightness by a factor of hundreds over ~340 days. |

Two of these are **movers**, two are **flickerers**, and one is an extreme cool
object -- which is exactly why the pipeline has to be able to tell "movement"
apart from "brightness change".

### 2.5 The bonus fields (famous objects)

The five above are the brief. The repository also ships thirteen familiar fields
ingested by exactly the same code, to prove the pipeline is not tuned to one kind
of object: **M31 Andromeda**, **M42 the Orion Nebula**, **M45 the Pleiades**,
**M1 the Crab Nebula**, **M51 the Whirlpool**, **M104 the Sombrero**, **M57 the
Ring Nebula**, **Sgr A\*** at the Galactic Centre, the quasar **3C 273**,
**Betelgeuse**, **Sirius**, **Proxima Centauri** and **TRAPPIST-1**.

They are deliberately harder in a different way. A red dwarf is a single point
of light; M42 is a wall of glowing gas, M31 is a galaxy of billions of stars
smeared across the field, and Sgr A* sits in the most crowded star field in the
sky.

What happens when you subtract two epochs of a nebula? Almost every "difference"
turns out to be a star that is already in the catalogue, because what changes
between SPHEREx passes in a crowded field is mostly the PSF and the detector,
not the sky. The numbers, straight from the shipped data: the Crab Nebula gives
10 detections and all 10 are `KNOWN`; M45 gives 6, all `KNOWN`; Sgr A* gives 7,
all `KNOWN`. That is the triage doing exactly its job -- a difference image is
not a discovery until you have checked whether something was already there.

What works *better* than expected: the Ring Nebula gives one genuine unknown
(1 `KNOWN`, 1 `CANDIDATE` at 23.8 sigma), and so does the quasar 3C 273 (3 known,
1 unknown, 24.3 sigma), the Sombrero (37.6 sigma) and M31 (one unknown). Nebulae
and galaxies are not point sources, so their "changes" are mostly artefacts of the
PSF -- which is precisely why the catalogue check exists.

Three fields produce nothing usable, and that is also honest. M42 is smooth
glowing gas with no compact source that changes between the two epochs.
TRAPPIST-1 only has 2 usable cutouts (one band, two epochs) because the archive
kept timing out on that position while this was built. Betelgeuse is so bright
that the detector saturates and almost every pixel gets flagged, leaving nothing
valid to weight -- so the page reports *"noise estimate degenerate"* instead of
a nonsense number. And the Large Magellanic Cloud was tried and dropped: at
Dec -69.8 every QR2 image in a 9' box belongs to the same visit, so there is no
second epoch to subtract. With 12-18 cutouts the single-star brief targets are
where `CANDIDATE` verdicts actually mean something.


---

## 3. What is inside a SPHEREx file

A SPHEREx download is a **FITS** file: a box with several labelled compartments
(called *extensions*). Ours contains:

| Compartment | Plain-English meaning |
|---|---|
| `IMAGE` | The photograph. Numbers are **surface brightness** in **MJy/sr** (megajanskys per steradian). Bigger number = brighter patch of sky. |
| `FLAGS` | A "damage map": a whole number per pixel, where each *bit* says something specific ("this pixel was hit by a cosmic ray", "this pixel is dead", "this pixel overflowed"). |
| `VARIANCE` | The uncertainty of each pixel, squared. Tiny values mean "trust this pixel"; large values mean "noisy". |
| `ZODI` | A model of the zodiacal light -- sunlight scattered by interplanetary dust. |
| `PSF` | The "blur profile" of the telescope: what a single point of light looks like after passing through the optics (121 slices of 101 x 101 pixels). |
| `WCS-WAVE` | A look-up table that answers *"what wavelength is this pixel?"* for every position in the image. |

Two extras in the header matter:

* **WCS** ("World Coordinate System") -- the recipe that converts a pixel
  position (column 57, row 40) into a sky position (RA, Dec) and back.
* **OBSID / MJD / DETECTOR** -- which observation this is, when it was taken (in
  Modified Julian Days), and which of the six arrays produced it.

> **Why you should care about `FLAGS`:** roughly 2 % of pixels in a typical
> SPHEREx image are damaged, cosmic-ray-hit, saturated or simply dead. If you
> averaged those in, you would inject fake blobs into your picture. The pipeline
> throws them away *before* averaging.

---

## 4. The six-step pipeline, in words you can picture

The code has six files, `step1` ... `step6`. Here is what each one does, with an
everyday analogy.

### Step 1 -- Ingest: do the shopping (`step1_ingest.py`)

You cannot analyse data you do not have. This step asks the NASA archive "what
SPHEREx pictures cover Barnard's Star?" and receives a catalogue of available
observations with their dates and URLs. Full frames are 70 MB each and there are
hundreds of them, so instead of downloading everything it asks IRSA's **cutout
service** for a small 0.2°-wide square around the target (about 5 MB) -- like
asking the library for a photocopy of one page instead of the whole shelf.

It keeps only exposures where the target sits comfortably inside the picture
(otherwise you would get a thin strip with the star cut off), splits them into
**epochs** (visits separated by more than a month), and keeps the first and the
last epoch -- because a difference between two epochs far apart in time is the
most sensitive way to spot movement.

Finally it looks up the same patch of sky in three catalogues: **Gaia DR3**
(1.8 billion star positions and motions), **AAVSO VSX** (variable stars), and --
when the archive cooperates -- **AllWISE** (infrared sources). This is the
"phone book" that later decides whether a detection is already known.

*Output:* FITS files in `data_cache/` plus one JSON file per target listing the
files, their epochs, their archive URLs, and the catalogue entries.

### Step 2 -- Co-add: make a clean average (`step2_coadd.py`)

Two things happen here.

**Masking.** Using the `FLAGS` bitmask, the code marks every damaged pixel as
"ignore". Same idea as crossing out smudged words before re-typing a page. The
bits the pipeline refuses to trust are: transient events, overflow, onboard
processing errors, phantom pixels, reference pixels at the detector edge,
permanently unusable pixels, dichroic inefficiency, lost data, hot and cold
pixels, uncorrected phantom signal, unreliable non-linearity corrections,
persistent charge, and pixels flagged as outliers. Two flags are *not* masked:
"full sample available" (information only) and "mapped to a known source" (that
is real light, not a defect).

**Stacking.** Real exposures are not perfectly aligned: the telescope wobbles,
the sky rotates. Each exposure is therefore **reprojected** -- every output pixel
is mapped back through both coordinate systems to find which input pixel belongs
there -- onto one common grid. Then the exposures are averaged with
**inverse-variance weighting**: a pixel with small uncertainty counts more than a
noisy one. The result is an image with less noise than any individual exposure,
because noise partly cancels while real stars add up.

*Output:* two stacked images, one per epoch, plus a **noise map** telling you how
uncertain each pixel is (typically sigma ~ 0.01 MJy/sr here).


### Step 3 -- Subtract: the clever part (`step3_zogy.py`)

Naively subtracting picture A from picture B is a trap: the telescope's blur (a
"point spread function", PSF) differs slightly between visits, so even a
perfectly constant star leaves a bright ring and a dark ring behind. You would
drown in false alarms.

**ZOGY** (Zackay, Ofek & Gal-Yam 2016) solves this properly: both epochs are
first convolved to a *common* effective PSF, then subtracted in a way that is
statistically optimal. Two outputs come out:

* **D**, the difference image -- positive where the sky got brighter, negative
  where it got fainter.
* **S**, the *significance* map -- the same thing divided by its own uncertainty,
  so the numbers read directly as "how many sigma".

A **sigma** is a ruler for surprise: 1 sigma happens all the time by chance, 3
sigma is unusual, **5 sigma is the standard astronomy threshold for "this is
probably real"** (roughly a 1-in-3.5-million fluke for pure Gaussian noise).

The code then looks for two shapes in **S**:

* a **dipole** -- a bright spot *and* a dark spot side by side. That is what you
  get when a source is at position X in epoch 1 and position Y in epoch 2: a
  positive blob at the new place, a negative one at the old. **A mover.**
* a **monopole** -- a single bright spot with no dark twin. That is what you get
  when a star simply got brighter or fainter in place. **A variable.**

Telling these apart automatically is the difference between a picture gallery and
an instrument.

### Step 4 -- Triage: is it news? (`step4_triage.py`)

For every detection, the code converts the pixel position to RA/Dec and looks for
the nearest entry in each catalogue. If something is within **30 arcseconds**
(five SPHEREx pixels -- generous enough for a moving star's shift between
epochs), the verdict is **KNOWN** and the match distance is reported. If nothing
is there, the verdict is **CANDIDATE**: not a discovery, but a *permission to
look*. A match closer than 5" raises the verdict to `high` confidence.

### Step 5 -- Spectrum and colour (`step5_spectrum.py`)

Two jobs:

* **Aperture photometry.** Add up the light in a small circle around a position
  and subtract the local background measured in a ring around it. The result is a
  flux in MJy/sr.
* **A three-index classifier.** Using the 102 wavelength channels, it measures
  three numbers: methane absorption (**CH4**), water absorption (**H2O**) and the
  near-infrared **slope** (how much brighter the object is at 4.5 micrometres
  than at 1.1). A small decision tree turns those into a label: *ultracool
  Y-dwarf*, *T-type brown dwarf*, *icy KBO / Planet-X candidate*, *dusty T
  Tauri*, or *M-dwarf / Mira red giant*.

The wavelength of each pixel is not stored as an image: it comes from the
`WCS-WAVE` look-up table, which is why `step5_spectrum.py` interpolates it. If a
cache has a 102-band `SPECTRUM` table next to it (the synthetic demo does), that
table is used instead.

### Step 6 -- Test your own detector (`step6_injection.py`)

This is the part most projects skip and most reviewers love. You cannot claim "I
would have found a moving object" without evidence. So the code plays hide and
seek with itself: it **injects** fake point sources of known brightness and known
speed into the real images, runs the whole detection chain, and counts how many
it gets back. Doing that across a grid of brightness x speed produces a
**recovery curve**: at SNR 10 and 10"/year it recovers 100 %; at 2"/year it
recovers almost nothing. That curve is the honest answer to *"how good is this
telescope plus pipeline, really?"*.


---

## 5. How the website works (the short version)

Think of the page as a **notice board that gets one big envelope of numbers** and
paints everything from it.

1. You open the page. Three files arrive: the layout, the colours, and one script.
2. The script asks the server *"which targets do you have?"* and fills the
dropdown.
3. It then asks *"run the pipeline for this target"*. The server does all the
   heavy work -- stacking the exposures, subtracting the epochs, matching
   catalogues, counting objects -- and replies with **one JSON document** of about
a megabyte: three 180x180 arrays (the stacked image, the difference image and the
significance map), the detection list, the catalogue list, the numbers and the
provenance table.
4. The browser turns those arrays into three pictures (one per layer), draws the
   selected one on a canvas, and overlays the grid, the catalogue crosses and the
   detection markers.
5. From then on everything is instant and local: dragging, zooming, switching
   layers, hovering for popups, redrawing charts. Only four things ask the server
   again -- changing the target, pressing *Recovery Grid*, pressing *Tiles* and
   ticking *Live from IRSA*.

The **all-sky view** works the same way: one request returns a tiny thumbnail and
a few numbers per field, and the browser draws them on an RA/Dec chart. Clicking a
thumbnail is just a shortcut for "open that field".

That is why the same page works with no server at all (Netlify/Vercel): the JSON
is pre-computed once and served as plain files.

**If you want it live on Netlify.** Netlify cannot run Python -- its functions are
JavaScript/TypeScript/Go, and astropy + reproject + numpy would not fit the
bundle anyway. What it can do is *proxy*: deploy the FastAPI app on Render, Fly,
Railway or any VPS, then add one rule to `netlify.toml` forwarding
`/live-api/*` to that server, and rebuild the site with
`python export_static.py --api-base /live-api`. The page then keeps its
pre-rendered JSON for instant loads and quietly falls back to the proxy for
anything that is not pre-rendered -- which is what makes *Live from IRSA* and
*Open any field* work on the deployed site. Same origin, so nothing about CORS
changes. Full steps are in README section "Hybrid: static on Netlify, live
pipeline behind a proxy".


---

## 5a. A guided tour of the website

The screen has two columns and nothing requires a page reload.

### 5.1 Top bar

* **SPHERExLens** -- the project name.
* **NEXUS // SPHEREX-QR2** -- which NASA data release you are looking at
  (`spherex_qr2` is the second SPHEREx quick release).
* **Status pill** -- green `NORMAL OPS - FIELD LOADED` when data is loaded, amber
  while the pipeline runs, red if a request failed.
* **Guide** -- opens a three-tab explainer written for someone who has never
  looked at an astronomical image: *How it works* (the camera, subtraction, the
  five steps, what the colours mean), *How to use it* (a click-by-click tour of
  this exact page) and *The words* (a ten-term glossary). `Esc` or the backdrop
  closes it. It needs no backend, so it works on the static deployment too.
* **Auto Demo** -- cycles the fields every 12 seconds, like a slideshow.
  Press again to stop.
* **Reset** -- restores the default zoom and clears the selection.

### 5.2 Left column (/01) -- Target Control

* **Target** dropdown: the five mandatory objects. Changing it re-runs the
  pipeline for that object.
* **RA / Dec / exposures**: the sky address (hours-minutes-seconds and
  degrees-arcminutes-arcseconds) and how many exposures were stacked per epoch.
* **Actions**:
  * `Run Pipeline` -- recompute everything for the selected target.
  * `Recovery Grid` -- run the injection-recovery test (24 cells) and print the
    table under "Recovery summary".
  * `Copy Share Link` -- copy a URL with `?target=...` so you can send someone
    straight to the same field.
* **Triage Verdict** -- for the *selected* detection: verdict badge, which
  catalogue matched and how far away it was, RA/Dec, significance, kind (dipole
  or monopole) and confidence.
* **Open any field** -- type an RA and Dec (degrees) and press the button. The
  server fetches those cutouts from IRSA, runs the pipeline on them, and adds the
  field to the dropdown and to the all-sky chart. This works in live mode only
  (the pre-rendered static build has no server to ask), and it takes about a
  minute per new field because every cutout has to be cut out of a 70 MB image at
  the archive.
* **Pipeline statistics** -- explained in section 6.

### 5.3 Right column (/02) -- Command Dashboard

* **Field metrics**: four cards -- detections above 5 sigma, how many are
  unaccounted for, the strongest significance, and the time baseline between the
  two epochs in days.
* **Live map**: see 5.4.
* **Epoch pairs**: a line per comparison actually made (E1->E2, E2->E3, E1->E3
  when there are three epochs), with the number of exposures in each side, the
  noise of each stack and how many detections that pair produced.
* **Detections table**: one row per detection (the ten strongest). Clicking a row
  selects it, outlines its marker on the map and fills the verdict panel.
* **Field catalogue**: *every* object the catalogues know about in the target's
  15-arcmin surroundings -- 306 objects for WISE 0855, 340 for Barnard's Star --
  one row each, with the verdict described in section 5.5.
* **Image sources & provenance**: one row per downloaded FITS file -- epoch,
  detector, central wavelength, observation id, UTC date, pixel size, file size,
  and a link that opens the *parent* Level-2 file on the IRSA server.

### 5.4 The Field catalogue panel -- every object, classified

This panel answers a different question from the detections table. The
detections table asks *"what changed?"*. The catalogue panel asks *"what is in
this piece of sky, and does SPHEREx see it change?"*

Each row is one physical object:

| Column | Meaning |
|---|---|
| **Verdict** | `KNOWN` = it appears in at least one catalogue. `CANDIDATE` = it appears in *no* catalogue, but SPHEREx detected a change there (the follow-up list). |
| **RA / Dec** | Sky position, in degrees. |
| **Mag** | Catalogued brightness (Gaia G, or WISE W1 when Gaia has none). |
| **Catalogues** | which catalogues know this object -- a Gaia star that is also a VSX variable shows both chips, because the two entries are merged into one row. |
| **Detected** | `yes` if SPHEREx found a significant change at this position, `no change` if the object is stable within our sensitivity. |
| **SNR** | significance of that change. |
| **Pair** | which epoch pair saw it (E1->E2, E1->E3, ...). |

The filter buttons (**All / Known / Candidate / Detected**) re-count themselves
every time a target loads. Click a row to ring that object on the map; objects
that fall outside the 9-arcmin map are shown dimmed with the note *outside the
9' map*, because the catalogues were queried over a wider 15-arcmin cone than
the map displays.

Why this matters: it turns the tool from "here are some blobs" into a
**completeness statement**. You can ask: of the 340 catalogued objects in
Barnard's Star's field, how many did SPHEREx see change? (A handful -- most stars
are stable at our sensitivity.) And conversely: of the 7 CANDIDATEs, none of them
is in Gaia, VSX or AllWISE.

### 5.5 Live mode -- read the sky straight from NASA, store nothing

> **One requirement.** Live mode (and *Open any field*) can only work if a Python
> server is answering. On the pre-rendered static build there is none, so the
> checkbox is *disabled* and relabelled **"Live from IRSA -- needs a backend"**,
> with a line underneath saying so, and the RA/Dec box is greyed out. That is
> deliberate: a toggle that silently does nothing would be worse. Everything else
> on the page runs from pre-computed JSON and needs no server at all.


There are two ways this app gets its pictures, and the checkbox **"Live from
IRSA (no cache, no download)"** switches between them:

* **Cache mode (default).** The cutouts were downloaded once into
  `data_cache/` and are re-read from your disk. Fast (about one second),
  works offline, but takes ~200 MB of storage.
* **Live mode.** The app asks IRSA for each cutout, reads the FITS file
  *straight out of the network response in memory*, and runs the pipeline on
  it. Nothing is ever written to disk. The first request for a target takes
  roughly half a minute to a minute and a half (it fetches several 5 MB files
  in parallel); after that the answer is held in RAM for half an hour, so
  pressing the button again takes about a second.

Why not always live? Because IRSA is a shared public archive: it takes about a
minute to cut one file out of a 70 MB image, and it throttles clients that ask
for too much too fast. For a demo you want the cache; for "is this still the
latest data?" you want live. Both produce exactly the same numbers, and live
rows in the provenance table are labelled `live:<observation id>` with the same
clickable IRSA link.

### 5.6 Hover popups, the all-sky view and the charts

**Hover popups.** Put the pointer on any marker and a card appears with the
essentials: for a detection, its verdict (KNOWN/CANDIDATE), significance, RA/Dec,
which epoch pair saw it and which catalogues matched; for a catalogue star, its
verdict, magnitude, which catalogues know it and whether SPHEREx saw it change.
The popup follows the pointer and disappears when you move away -- nothing is
stored, it is read straight out of the JSON already in memory.

**All-sky view.** The `Field / All-sky` switch above the map redraws the same
canvas as a map of the whole celestial sphere: right ascension runs right to
left, declination bottom to top, and every field you have data for is placed at
its true position. Each field is drawn as a magnified thumbnail of its
significance map (a 9-arcminute field would be a third of a pixel at true scale,
which is why the caption says the tiles are magnified). Hovering a tile shows that
field's detection count, epochs and baseline; clicking it opens the field. The
caption also reports the total sky area covered -- a few hundredths of a square
degree, which is the honest scale of this demo against SPHEREx's 41,000 square
degrees.

**Charts.** Four small charts sit above the detections table:

| Chart | What it tells you |
|---|---|
| **Significance histogram** | A healthy subtraction is a narrow peak around zero (noise) with a long tail (real sources). Bars beyond 5 sigma are highlighted red -- those are the detections. |
| **Noise per epoch** | How deep each stack is. More exposures or more bands make a shorter bar. |
| **Detections per epoch pair** | Which time baseline found the most change (E1->E3 usually beats E1->E2). |
| **Injection recovery** | The honesty curve: fraction of fake sources recovered vs brightness and speed. Empty until you press *Recovery Grid*. |

### 5.7 The map, and how to play with it

The map is a small interactive canvas, not a static picture.

| Action | What it does |
|---|---|
| **Drag** | Pan around the field (the 9' square is bigger than the panel). |
| **Mouse wheel** | Zoom in and out around the cursor (up to 12x). |
| **Double-click** or `Fit` | Return to the default framing. |
| **`+` / `-` / `0` keys** | Zoom in, zoom out, fit. |
| **Hover** | A crosshair follows the cursor; the bottom-left readout shows RA, Dec, pixel coordinates and the value under the cursor. |
| **Click a marker** | Select that detection (same as clicking its table row). |
| **Co-add / Difference D / Significance S** | Switch which image you are looking at: the stacked epoch-1 picture, the ZOGY difference, or the significance map. |
| **Grid / Catalogs / Detections** | Turn overlays on and off. |
| **`g` / `d` / `r` keys** | Toggle grid, toggle detections, re-run the pipeline. |

Overlay colours: **blue crosses** are Gaia DR3 stars in the field, **amber
crosses** are VSX variable stars, **green circles** are dipoles (with a line
joining the new position to the old), **amber squares** are monopoles. The scale
bar in the corner always shows how long one arcminute is at the current zoom.

---

## 6. Every number on the screen, translated

| Shown as | Meaning |
|---|---|
| **RA / Dec** | Sky address in hours-minutes-seconds and degrees-arcminutes-arcseconds. |
| **Detections** | Number of 5-sigma peaks in the significance map: "how many things changed". |
| **Unaccounted** | Detections with no catalogue match: "how many are worth a human look". |
| **Max SNR** | The strongest peak, in sigma. 30 sigma is very loud; 5.7 is barely over the line. |
| **Baseline** | Days between the two epochs. Longer baseline = more time for movement to accumulate. |
| **Exposures** | How many files were stacked in total. More exposures = less noise. |
| **Epochs** | Distinct observing visits. ZOGY needs at least two. |
| **Noise sigma1** | Median per-pixel uncertainty of epoch 1, in MJy/sr (surface brightness). |
| **5-sigma depth** | 5 x the noise: the faintest believable signal, i.e. "how deep did we look". |
| **Field / pixel** | 9 arcminutes across, 3 arcseconds per output pixel (finer than SPHEREx's native 6.2" because the co-add oversamples). |
| **Gaia / VSX** | How many catalogue entries were available in the patch -- the size of the "phone book". |
| **Dipoles / mono** | Movers versus flickerers among the detections. |
| **Confidence** | `high` if a match is within 5", else `medium`. |
| **mu (arcsec/yr)** | Proper motion implied by the dipole separation. Barnard's Star really moves 10.4"/yr. |
| **MJy/sr** | Megajansky per steradian -- infrared astronomy's "brightness per patch of sky". |


---

## 7. Worked example: Barnard's Star, number by number

The page for Barnard's Star, as built from the live archive:

* **4 exposures, 2 epochs.** Two detectors (D1, near 0.94 micrometres, and D4,
  near 3.1) in each of two visits: MJD 60918 (2025-271) and MJD 61110
  (2026-094). That is a **192-day baseline**, just over six months.
* **Noise sigma1 = 0.0106, sigma2 = 0.0119 MJy/sr.** The second epoch is
  marginally noisier; inverse-variance weighting handles that automatically.
* **12 detections above 5 sigma** (the dashboard table shows the ten strongest).
  **4 KNOWN** (matched to a Gaia DR3 star or a VSX variable), **8 CANDIDATE**.
* **Strongest peak: 29.5 sigma**, mean 10.8 sigma. The bright saturated star at
  the top of the frame is also detected as a dipole: its core is masked, and a
  small residual ring is left over -- an honest artefact, not a discovery.
* **300 Gaia DR3 sources and 47 VSX variables** were available inside the 9'
  field, which is what makes the triage verdicts meaningful.

For comparison, **R Draconis** (a Mira variable in a crowded field) has 6
detections and *all six* are KNOWN: every change it shows is already catalogued,
which is exactly what you expect for a famous pulsating star.

---

## 8. Where the pixels come from (and how to check)

Every row of the *Image sources & provenance* table is a real, resolvable record:

```
Epoch  Det  lambda (um)  Observation             UTC date    Pixels    Size    Archive
E1     D1   0.939        2025W35_2A_0611_2 (TM)  2025-09-27  117x117   5.0 MB  IRSA - level2_2025W35...
```

Clicking the observation id opens the parent file at
`https://irsa.ipac.caltech.edu/ibe/data/spherex/qr2/level2/...` -- the actual
70 MB Level-2 product the cutout was carved from. From there you can inspect the
same data the website used, with the same headers, in any FITS viewer.

The archive line above the table summarises the chain:
`IRSA SIA2 - collection spherex_qr2 - 4 cutouts - 0.050 MJy/sr 5-sigma depth -
Gaia DR3 300 / VSX 47 entries cross-matched`.

---

## 9. What this project does NOT do (honesty section)

* **It is not a discovery machine.** A CANDIDATE means "no catalogue match within
  30 arcseconds", which is most often a faint star too dim for Gaia, a cosmic ray
  that slipped through, or a residual from a bright star's mask. Real follow-up
  means re-observing the position.
* **The real cutouts carry two bands, not 102.** With two file pairs the
  "spectrum" is four aperture measurements, so the 102-band classifier is *not*
  run on real data -- the API returns `classification: null` rather than making
  something up. The synthetic demo cache is what exercises the classifier.
* **AllWISE is empty in this deployment.** IRSA's TAP copy of the AllWISE table
  was not queryable and the VizieR mirrors were unreachable, so KNOWN verdicts
  rest on Gaia DR3 + VSX.
* **Eight months is short.** A star moving 10"/year shifts 6-7". The dipole
  machinery can recover that, but the injection-recovery curves show that
  anything slower than about 5"/year at SNR 5-10 is largely missed.
* **The map is not a photograph.** The colours are a stretch applied to
  surface-brightness values; only the *relative* pattern is meaningful, and the
  readout gives the actual number under the cursor.
* **"All the images" means all images of this patch of sky.** For one target the
  pipeline now ingests every detector (D1-D6) and every epoch the archive holds:
  up to 18 cutouts, ~95 MB. The full mission is millions of 72 MB exposures, so
  covering the whole sky is a data-centre job, not a laptop one. The code scales
  the same way (one SIA2 cone per field, or bulk access through the public
  `nasa-irsa-spherex` S3 bucket), and its memory use does not grow: it only ever
  holds one 180x180 stack at a time.
* **The demo cache is synthetic.** Files named `demo_*.fits` in
  `data_cache_demo/` are generated by `make_demo_data.py` for offline work and
  must never be presented as NASA data.


---

## 10. Mini glossary

| Term | Meaning |
|---|---|
| **Aperture photometry** | Adding up pixels inside a circle to measure how much light an object emits. |
| **Arcminute / arcsecond** | 1/60 and 1/3600 of a degree. The Moon is 30 arcminutes wide. |
| **Background subtraction** | Estimating the sky glow in a ring around an object and subtracting it. |
| **Co-add** | Averaging several exposures of the same patch to reduce noise. |
| **Cutout** | A small square extracted from a big image, delivered by a web service. |
| **Dipole** | A plus/minus pair in a difference image; the signature of something that moved. |
| **Epoch** | One observing visit. ZOGY compares two epochs. |
| **FITS** | The file format used by astronomy: a stack of labelled arrays plus a text header. |
| **FLAGS** | Per-pixel bitmask recording defects. |
| **Injection-recovery** | Hiding fake sources in real data to measure how many you can find again. |
| **IRSA** | NASA/IPAC Infrared Science Archive at Caltech -- where SPHEREx data lives. |
| **Jansky / MJy/sr** | Unit of flux density; MJy/sr is surface brightness. |
| **LVF** | Linear variable filter: makes each detector row a different wavelength. |
| **Monopole** | A single blob in a difference image; the signature of a brightness change. |
| **MJD** | Modified Julian Date -- days since 1858-11-17, how astronomers write times. |
| **Oversampling** | Sampling finer than the native pixel size (our 3" grid vs SPHEREx's 6.2"). |
| **Proper motion** | A star's apparent movement across the sky, in arcseconds per year. |
| **PSF** | Point spread function: the blurry shape a point of light makes. |
| **SIA2** | IVOA "Simple Image Access v2", the web standard used to find the images. |
| **Sigma** | A unit of statistical surprise; 5 sigma is the usual "real" threshold. |
| **TAP / ADQL** | Standard query protocol and SQL dialect for astronomy databases. |
| **Triage** | Deciding whether a detection is already known. |
| **Variance / inverse-variance weighting** | Uncertainty squared; weighting by 1/variance favours trustworthy pixels. |
| **WCS** | World Coordinate System: pixels to sky coordinates and back. |
| **Y-dwarf / T-dwarf** | The coldest classes of brown dwarf (below ~500 K / ~500-1300 K). |
| **ZOGY** | Zackay-Ofek-Gal-Yam optimal image subtraction (2016). |

---

## 11. Frequently asked questions

**Is this real NASA data?**
Yes. Every `.fits` file in `data_cache/` was downloaded from IRSA, and each one is
listed with its observation id and a link to the parent file. The synthetic
`demo_*.fits` files are the exception and live separately in `data_cache_demo/`.

**Why does the map look purple and orange?**
Because it is a *significance* map, not a picture. Purple/black is near zero,
orange-to-white is a strong positive signal, blue-black is a strong negative one.
A mover shows one positive and one negative blob -- that pair is the whole point.

**Why is my favourite star not detected?**
Either it did not change between the two epochs, or it is brighter than the
saturation limit (masked), or it sits outside the 9' field, or its change is below
5 sigma. The injection-recovery curves tell you exactly how faint a change the
pipeline can see.

**Can it find Planet Nine?**
In principle this is the right *technique* (subtract two epochs, look for a moving
dot). In practice a 9' field, two bands and two epochs is a toy version: real
searches use many fields, many epochs and much deeper images.

**Are there really only five pictures?**
No -- five is just how many fields are **cached**. SPHEREx photographs the whole
sky (about 41,253 square degrees); a 9-arcminute field is 0.0225 square degrees,
so the sky is roughly 1.8 million fields of this size. This app ships five of
them, and can pull in any other one: tick *Live from IRSA* or type an RA/Dec in
*Open any field*, and the cutouts for that position are fetched and analysed on
the spot. A field added that way lives in the server's memory only -- it does not
survive a restart unless you also download it with `step1_ingest.py`.

**Can it work without downloading anything?**
Yes -- tick *Live from IRSA*. The cutouts are fetched and parsed in memory and nothing is saved. The cost is time: the first look at a target is dominated by IRSA's cutout service (tens of seconds), after which the result is cached in RAM for 30 minutes.

**How long does a page take to load?**
The FastAPI version runs the whole pipeline per request: a few seconds for four
cutouts. The static version loads pre-computed JSON (about 0.6 MB per target), so
it is instant.

**Can I add my own target?**
Yes -- put cutouts in `data_cache/`, write a `<Target>_meta.json` with `paths`,
`epochs` and (optionally) `catalogs`, and reload. The README documents the exact
schema.

**Where are the charts?**
`figures/` holds the charts made from the synthetic cache, `figures_real/` the
same charts made from the real cutouts, and `site/figures/` ships them with the
static build. `python make_figures.py` regenerates them.

**Is any of this invented?**
No invented data: the numbers in this document were read off the actual output of
the pipeline on the actual files. Where something did not work (AllWISE, the
102-band classifier on real data), it says so.

---

## 12. Credits

* SPHEREx data: NASA/JPL-Caltech, hosted by IRSA/IPAC. Required acknowledgement:
  *"This publication makes use of data products from the Spectro-Photometer for
  the History of the Universe, Epoch of Reionization and Ices Explorer
  (SPHEREx), which is a joint project of the Jet Propulsion Laboratory and the
  California Institute of Technology, and is funded by the National Aeronautics
  and Space Administration."*
* Gaia DR3: ESA/Gaia/DPAC, via VizieR / IRSA TAP.
* AAVSO VSX: the American Association of Variable Star Observers.
* Method: Zackay, Ofek & Gal-Yam 2016, *Proper Image Subtraction*, ApJ 830, 27.
* Everything else (pipeline, dashboard, documentation) is in this repository.

