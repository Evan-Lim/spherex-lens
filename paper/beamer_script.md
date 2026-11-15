# SPHERExLens — complete spoken script for `spherexlens_beamer.pdf`

Companion to the 30-slide deck: one section per slide, with the duration in the heading. Slides
marked **[CORE]** form the 22-minute route.

| Part | Purpose |
|---|---|
| **Purpose** | What the slide must achieve before you advance. |
| **On screen** | Where to point; what to let the audience read. |
| **Say** | The words, at roughly 130 words per minute. |
| **Bridge** | The sentence that hands off to the next slide. |

Routes: **short** = the 13 **[CORE]** slides (21:25) · **standard** = CORE + 4, 9, 13, 14, 19, 20,
21 (31:30) · **full** = all 30 (44:10). Paragraphs marked **[optional]** can be dropped without
breaking the argument — that is how the full text collapses into either shorter route.

Five rules matter more than the words: one idea per slide; say round numbers out loud and keep the
precise ones for questions; point once, then talk to the room; never apologise for the work; pause
two beats after every headline number.

---

## Slide 1 — Title: *Finding What Changed* (1:10) [CORE]

**Purpose.** Establish the question, the scale of the result, and that this is a working system.
**On screen.** Hold. Let them read the five numbers along the bottom before you speak.

**Say.** Good morning. My name is Evan Lim, and for the next twenty minutes — or forty, if you let
me — I want to answer one deceptively simple question: **if you photograph the same patch of sky
twice, six months apart, can you prove what changed?**

It sounds like an astronomy question, and it is, but it is really a question about evidence. Anyone
can look at two pictures and point at a difference. The hard part is defending the claim: saying
*this* difference is astrophysics, *that* one is my telescope, and putting a number on how often I
am wrong.

NASA's SPHEREx satellite supplies the raw material. It sweeps the whole sky in a hundred and two
infrared colours and repeats the sweep every six months. Two sweeps are two photographs of the same
sky, and a difference between them is a measurement of motion, of brightness, or of something
appearing and disappearing. [Optional] The satellite is not the contribution; the software that
turns two sweeps into a checkable list is.

The headline runs along the bottom: eighteen fields, two hundred and forty-six archive cut-outs, one
hundred and nine detections. Ninety-nine are things we already know. Ten are genuinely unexplained.

[Pause.] Hold on to that ratio. Nine in ten detections are not discoveries — and saying so out loud
is what makes the tenth one worth looking at.


---

## Slide 2 — Two visits reveal what one image cannot (1:45) [CORE]

**Purpose.** Give the physical intuition (dipole vs monopole), then break it so the talk has a
problem to solve.
**On screen.** Point at the arrow between the green dots, then at the red dot below.

**Say.** A single image tells you where things are. A second image tells you what changed, and
change is where the science lives. A star that sits still and shines steadily is useful as a
reference; a star that moves, flares or vanishes is a physical event.

So subtract one visit from the other. Everything stationary cancels — the stars, the galaxies, the
diffuse zodiacal glow that suffuses the infrared sky — and what remains is one of two things.

Either a source that **moved**: you get a **dipole**, a negative hole where it was and a positive
peak where it now is. Divide the separation by the elapsed time and you have a proper motion, in
arcseconds per year.

Or a source whose **brightness changed**: a single **monopole**. Positive if it brightened — a flare,
an outburst. Negative if it faded — a star dimming, an eclipse, something occulted.

That is the whole vocabulary: one shape for motion, one for light. [Optional] There is a third,
subtler case — a source that moved *and* changed brightness — which gives an asymmetric dipole whose
lobes differ in flux. We come back to that when we talk about filtering.

Now the bad news, in red at the bottom. Two visits never share exactly the same point-spread
function. The optics focus differently at different temperatures, the detector has a different
sub-pixel phase on the source, and the spacecraft rolls, so the same star lands on different pixels.
The difference image is therefore not empty where nothing changed: it is full of a fine grey haze of
instrumental residuals — and those residuals outnumber the real events by roughly a factor of a
**thousand**.

Which means the job is not subtraction. Subtraction is a two-line operation. The job is **deciding
which residuals are astrophysics**, with a false-alarm rate you can state out loud to a sceptic.

**Bridge.** So let us state precisely what the software has to hand back.

---

## Slide 3 — What the pipeline has to output (1:30) [CORE]

**Purpose.** Define the contract: input, threshold, and that a detection without an identity is not
a result.
**On screen.** Follow the flow; name the three verdicts when you reach the bottom row.

**Say.** Here is the task the way an engineer would state it. The **input** is a pair of 180-by-180
pixel cut-outs, one per epoch, with per-pixel variance planes and a 32-bit quality mask recording
everything the instrument knows about each pixel. We build a significance map — a picture of "how
surprising is this pixel given the noise" — and threshold it at six sigma.

That yields one hundred and nine detections across eighteen fields. A tidy number, and on its own
completely meaningless.

Because a blob in a difference image has no identity. It could be a catalogued star whose proper
motion we failed to account for, an asteroid passing through, an optical ghost, a diffraction spike,
a hole in the mask — or something nobody has recorded. So every detection is given an **identity**,
and that is the bottom row: ninety-nine become KNOWN, because a catalogue, an ephemeris or an
artefact already explains them. Ten survive. Those ten are candidates, and I want to be pedantic
about the word: *candidate* means "unexplained by the surveys we checked". It does not mean
discovery. A discovery needs follow-up spectroscopy and a paper; we produce the list that makes that
follow-up possible.

Three constraints were self-imposed, and they cost real performance. **Public data only** — no
proprietary catalogues, no private observing time. **Every number reproducible** — same inputs, same
code, same output, which is why snapshots are content-addressed and the evaluation harness ships
with the paper. And **"no change" is not "no data"**: if two epochs share no detector band we refuse
the subtraction rather than report an empty result — a distinction that sounds pedantic until you
are the person deciding whether a null detection is a null result.

**Bridge.** Those constraints turn into four concrete questions.

---

## Slide 4 — What the project set out to measure (1:20)

**Purpose.** Show that the work was question-led, and preview the four numbers that answer them.
**On screen.** Four cards; touch each as you name it.

**Say.** Four questions framed everything, and each has a measurable answer rather than an opinion.

**First: calibration.** Is the significance map actually calibrated? Does six sigma mean what it
says on real, co-added, resampled SPHEREx images? The answer is yes — once the scale is set with a
robust median-absolute-deviation estimator instead of a global standard deviation. That single
change is most of why the detector works at all.

**Second: morphology.** Can a dipole be told from a monopole at the three-arcsecond scale of a
SPHEREx pixel — a scale at which a moving source barely separates from its own previous position?
Yes, provided the pairing is constrained by physical proper motion rather than by distance alone.

**Third: identity.** How much of the candidate list do Gaia, WISE, VSX and NEOWISE explain away?
Ninety-one per cent — ninety-nine of a hundred and nine detections. The remaining ten are what the
rest of the talk is about.

**Fourth: completeness.** What did we miss, and can it be measured? Yes: inject twelve thousand two
hundred and forty sources and count. Sixty-nine point three per cent come back under a strict
criterion that was zero point two per cent before the recalibration.

**Bridge.** To see why those four numbers took a year, we have to look at how subtraction is
normally done.

---

## Slide 5 — Forty years of subtracting one image from another (1:30)

**Purpose.** Position the method in the literature, and justify the one property everything depends
on: an analytic null.
**On screen.** Let the timeline sit; rest on 2016.

**Say.** Image subtraction has a forty-year lineage, and it is worth ninety seconds because the
choice of method decides whether you can defend a threshold.

In 1998, Alard and Lupton introduced optimal image subtraction: fit a convolution kernel, expressed
as a sum of basis functions, using the bright stars in the frame. It transformed microlensing
surveys. It also fails exactly where the interesting science is — crowded fields, nebulae, and
anywhere you cannot find an isolated star to fit.

Bramich, in 2008, replaced the basis functions with a discrete, per-pixel kernel. Better behaved in
sparse fields, at the cost of a much noisier solution.

Then in 2016, Zackay, Ofek and Gal-Yam published ZOGY: a closed-form, maximum-likelihood derivation
in Fourier space. Nothing is fitted. Each image is convolved with the *other* image's point-spread
function, the difference is divided by the combined noise, and the output is a significance map that
is Gaussian with unit variance wherever the sky did not change.

That last clause is the reason we use it, and the reason this talk can quote a false-alarm rate at
all. When the null distribution is analytic, choosing a threshold is a statement about probability —
not a knob turned until the picture looks clean.

[Optional] SFFT in 2022 refines the same mathematics for deliberately undersampled space-based
optics; the formulation differs, the philosophy does not.

**Bridge.** That philosophy explains why a pipeline exists at all — and why none exists for SPHEREx.

---

## Slide 6 — Every survey does the same four things (1:20)

**Purpose.** Make the gap concrete: brokers exist, but they start from alerts, not pixels.
**On screen.** Trace the loop, then point at the caption.

**Say.** Every operational time-domain survey runs the same four-stage loop. **Detect** against a
template image. **Veto** with catalogues and machine learning. **Triage** what survives into
priorities. And **hand it to a human**, who decides what deserves a telescope.

That loop is industrialised for optical surveys. ANTARES, ALeRCE, Lasair, Fink — they process
hundreds of thousands of alerts a night, and they are extremely good at the veto.

But look at where their input comes from. A broker ingests *alerts*: structured packets produced by
somebody else's subtraction pipeline, with somebody else's calibration, on somebody else's pixels.
The subtraction itself stays inside the survey.

For SPHEREx there is no equivalent open artefact. No published, inspectable, end-to-end pipeline
that starts from Level-2 FITS files — the actual calibrated frames — and ends at a candidate list a
student could audit. That is the gap.

And notice what kind of gap it is. It is not a missing algorithm: ZOGY is published, catalogues are
public, injection testing is standard practice. The missing thing is an *instance* — a reproducible,
documented, tested implementation that anyone can run, break and check. That is what this project
supplies.

**Bridge.** The instance starts with the instrument, so let me introduce it briefly.

---

## Slide 7 — Six arrays, 102 bands, one sweep every six months (1:30) [CORE]

**Purpose.** Give the audience the three facts that constrain every later design choice: linear
variable filter, six extensions, six-month cadence.
**On screen.** Trace the wavelength axis, then the six extension chips along the bottom.

**Say.** Three properties of the instrument shape everything downstream.

**First, the wavelength coverage.** SPHEREx is a twenty-centimetre all-sky telescope that does not
use filters. It uses a linear variable filter: a different wavelength lands on each detector row, so
a hundred and two bands run continuously from three-quarters of a micron to five microns. Every
pixel therefore has its own colour — which is why a cut-out can later be turned into a spectrum
without any follow-up observation.

**Second, the data product.** Each cut-out arrives with six extensions: the science image, a 32-bit
flag plane that records every quality condition the pipeline detected, the variance per pixel, the
wavelength solution for that pixel, an empirical point-spread function for that detector zone, and a
zodiacal-light model. We use all six. The PSF is what makes a properly matched subtraction possible;
the flag plane is what stops us subtracting artefacts; the wavelength solution is what makes the
spectroscopy slide possible at all.

**Third, the cadence.** Six months. That is short enough to catch genuine variability, and long
enough that anything in the solar system has moved out of the field between visits — which turns out
to be both a blessing and a nuisance.

**Bridge.** Knowing the data product, we can talk about how you actually get it.

---

## Slide 8 — Eighteen fields, fetched as 5 MB stamps (1:20)

**Purpose.** Show that the pipeline is engineered around the archive rather than around a
supercomputer.
**On screen.** The three-step fetch on the right; then the acceptance test.

**Say.** A SPHEREx parent frame is about seventy megabytes. We evaluate eighteen fields with several
visits and six detector arrays each — download the parent frames and this stops being a laptop
pipeline.

So we fetch **stamps**: about five megabytes each, cut out around the target. There are two paths to
the same pixels — an SIA2 cone search through IRSA, or a direct read from the public
`nasa-irsa-spherex` bucket — and the pipeline uses whichever is healthy.

Every stamp must then pass an acceptance test: at least twelve pixels of valid margin around the
target, and fewer than fifty per cent of pixels flagged. Fail either, and the cut-out is refused.
Refusal is recorded in the provenance log rather than quietly dropped, because "we could not use
this field" is a scientific result and has to be reportable.

The sample itself is deliberately mixed: five mandatory targets — Barnard's Star, Epsilon Indi B,
RW Aurigae, R Draconis, WISE 0855 — plus thirteen famous benchmarks spanning galaxies, nebulae, a
quasar and a globular cluster. Eighteen fields, two hundred and forty-six cut-outs: enough diversity
that a pipeline tuned to one kind of sky gets caught.

**Bridge.** With the data in hand, here is how the code is organised.

---

## Slide 9 — Seven subsystems, one science core (1:20)

**Purpose.** Show that this is an engineered system with a stable interface, not a script.
**On screen.** Two rows: science stages above, serving layers below.

**Say.** The code is modular on purpose, and the modules follow the science rather than the other
way round. **Ingest** talks to the archives. **Imaging** handles masks and co-addition. **ZOGY** does
the subtraction. **Triage** assigns identity. **Machine learning** scores real versus bogus.
**Deep** builds light curves at the ecliptic poles. **Eval** runs the null tests and the injection
harness.

Underneath sit two serving layers: a snapshot store holding content-addressed results, and a FastAPI
service that answers questions about them.

The design decision that matters is a single JSON contract between the science and the serving
layer. A detection computed in a batch job and a detection served live are byte-identical, because
they are produced by the same code path — twenty-one thousand four hundred and twenty-nine lines of
runtime Python, seven subsystems, one science core.

That is what makes the claim "you can reproduce this" credible: there is only one implementation of
each number, and it is the same one that produced the slides.

**Bridge.** One thing a modular system must survive is that its data sources fail.

---

## Slide 10 — When an archive goes down, the pipeline does not stop (1:15)

**Purpose.** Establish engineering credibility: fallbacks, and honesty when a fallback also fails.
**On screen.** The cascade; touch each timeout label.

**Say.** Production science has to survive the network, because the archives are somebody else's
service. So every external call is wrapped in a fallback.

Gaia is queried at IPAC first, then at STScI's MAST, then at the CDS mirrors — three independent
routes, two timeouts. In practice this means a Gaia cross-match survives an outage at any single
mirror without human intervention.

Asteroids go to JPL Horizons, are enriched from the Small-Body Database, and are cross-checked
geometrically against CADC's SSOIS service. Optical confirmation comes from CSA's NEOSSat, with darks
and flats removed before any photometry is believed.

And — this is the part I care most about — when a service is genuinely unreachable, the interface
says so. It does not substitute a guess, and it does not silently downgrade a verdict. A card that
says "asteroid check unavailable" is a truthful card; a card that says "candidate" because a server
timed out is a lie with a confidence score attached.

**Bridge.** Before any of that mattered, though, the pixels had to be cleaned.

---

## Slide 11 — Fourteen defect bits go, two stay (1:30) [CORE]

**Purpose.** Show that preprocessing is where most of the sensitivity is won, with one concrete,
surprising example.
**On screen.** The grid: grey cells rejected, green cells kept.

**Say.** The quality mask decides what the difference image can contain, so this slide is quietly one
of the most important in the talk.

We reject fourteen defect bits: cosmic-ray hits, saturation, hot and cold pixels, non-functional
channels, persistence from previous slews, and statistical outliers. Each corresponds to a physical
failure mode of the detector.

But we deliberately **keep two**. `FULLSAMPLE`, which tells us the pixel was read out at full
resolution, and — critically — bit twenty-one, `SOURCE`, which marks genuine astronomical flux. Early
versions rejected `SOURCE` along with everything else, on the principle that masks are good; the
result was artificial hollows around bright stars that looked exactly like transients. Retaining it
was worth more than any threshold tuning we did.

The subtle one is **cross-epoch matching**. If a pixel is masked in the second visit, we drop it from
the first visit as well. Otherwise the mask hole itself becomes a difference: a negative where the
hole is, a positive where the flux used to be — a fake dipole, indistinguishable from a real mover.
That single change cut the median background scatter by more than a factor of two.

Then, before co-addition, each exposure gets sigma-clipped background subtraction, zodiacal-model
removal, and inverse-variance weighting, so a noisy pixel contributes exactly as much as it deserves
and no more.

**Bridge.** With clean, weighted, co-added images, the subtraction itself is almost elegant.

---

## Slide 12 — Subtract in the Fourier domain, with nothing fitted (2:00) [CORE]

**Purpose.** The technical heart: explain matched filtering and the analytic null without algebra.
**On screen.** Two inputs into the kernel; two outputs, D and S.

**Say.** Here is the whole method on one slide, and it is worth two minutes because everything else
in the talk depends on it.

Two co-added images arrive, each with its empirical point-spread function — measured, not assumed,
from a 21-by-21 grid of detector zones. Both are transformed into Fourier space, where convolution
becomes multiplication and the whole problem becomes algebra.

The ZOGY kernel is a matched filter. It convolves image one with the PSF of image *two*, convolves
image two with the PSF of image *one*, subtracts, and divides by the combined noise: the variance of
each image weighted by the other's PSF. That division is what makes it "proper" subtraction — each
image is weighted exactly as much as its own noise and resolution deserve, and a blurry visit cannot
dominate a sharp one.

Two outputs come out. **D**, the difference image in flux units — that is what you look at, the thing
with a dipole or a monopole in it. And **S**, the significance: the difference normalised by its own
expected scatter. Where the sky did not change, S follows a standard normal distribution — mean zero,
unit variance. Not approximately, not empirically: by construction.

[Optional] That is the entire reason this project can make claims about false alarms. If the null
were empirical, the threshold would be a matter of taste, and every group would defend a different
one. Because the null is analytic, six sigma is a probability statement that survives review.

The cost is two fast Fourier transforms per epoch pair — under six-tenths of a second for a
180-squared frame. That is why this runs on a laptop, and why we can afford to run it twelve thousand
times in the injection test.

**Bridge.** A calibrated statistic still needs a threshold, and that is where theory meets reality.

---

## Slide 13 — Why 6σ is a decision, not a habit (1:40)

**Purpose.** Show the gap between the analytic prediction and the real data, and how the operating
point was chosen.
**On screen.** The log plot; touch five sigma, then the dashed six-sigma line.

**Say.** The theory is clean. For thirty-two thousand four hundred pixels in a frame, the two-tailed
Gaussian prediction at five sigma is about two hundredths of a false pixel per frame. In other words:
never.

The data disagreed, and that disagreement is the interesting part. On real co-adds — resampled onto
a common grid, which correlates neighbouring pixels — the effective number of independent resolution
elements is far smaller than the number of pixels, and the tails are heavier than Gaussian. At five
sigma we measured zero point eight six false candidates per field.

So we did the boring, honest thing: we measured the curve and moved the operating point. At six
sigma the empirical false-candidate rate halves to zero point four eight per field, and strict
injection recovery does not change measurably. After the full triage chain — catalogues, mask
verification, halo vetoes — the residual is one point six six false candidates per field.

The lesson is not that five sigma is wrong. The lesson is that a threshold is an empirical
calibration, and the number you quote should be the one your data supports, not the one your textbook
remembers.

**Bridge.** With a calibrated statistic in hand, the shape of the residual tells you what happened.

---

## Slide 14 — Three shapes, three physical stories (1:15)

**Purpose.** Turn the statistic into physics: motion versus brightening versus fading.
**On screen.** Three panels; name each shape as you touch it.

**Say.** Once you have a significance map, morphology does most of the interpretation, and it does it
in a way an audience can follow without any mathematics.

A **dipole** — a negative lobe and a positive lobe separated along a straight line — is a source that
moved. The separation is the displacement; divide by the elapsed time and you have a proper motion.
If the two lobes have equal magnitude, the source simply relocated. If they differ, it moved *and*
changed brightness, which is a much stronger physical statement.

A single **positive** peak is brightening: a flare, an accretion outburst, an eclipse egress.

A single **negative** peak is fading — and that case was missing from our first version entirely. We
had built a detector that only believed in things that appeared. Adding the negative monopole, and
the kinematic rules that keep it from being confused with a mover, is one of the reasons the
completeness numbers move so far later in the talk.

**Bridge.** Those three shapes are also the three failure modes, which brings me to the turning point.

---

## Slide 15 — Three defects, three fixes, one recalibration (2:05) [CORE]

**Purpose.** The most important slide in the talk: name the failure honestly, then show that each fix
is physical rather than cosmetic.
**On screen.** Red column, arrow, green column. Land on 0.2 % → 69.3 % and leave it there.

**Say.** This is the turning point of the project, and I want to be blunt about it.

Our first version recovered zero point two per cent of planted sources. Two in a thousand. If we had
only ever looked at pretty pictures, we would have shipped it — the images looked convincing. Strict
testing is what exposed it.

It was not a tuning problem. It was three architectural mistakes.

**First: the significance map was divided by one global standard deviation.** That is a defensible
choice only if the noise is uniform across the frame, and it never is. One bright star with
imperfect subtraction inflates the variance of the entire image, so the divisor becomes too large,
and every real detection elsewhere in the frame is scaled down into invisibility. The fix is a
robust scale: median absolute deviation over the frame, converted to an equivalent sigma. One outlier
no longer moves it. That single change recovered the bulk of the sensitivity.

**Second: the dipole pairing was unconstrained.** Any negative lobe was paired with any positive lobe
within seventy-five arcseconds, which is roughly twenty-five pixels. In a crowded field that is a
matchmaking service: fading stars were being called movers, and unrelated noise lobes were being
called moving stars. The fix is physical. The separation must lie inside the maximum plausible proper
motion over the actual baseline; the two lobes must agree in flux within a factor of two; and the
morphology must be consistent with a displacement. Suddenly every accepted pair is kinematically
possible, and the false movers vanish.

**Third: the bright-star mask was a fixed three arcminutes.** A bright star's halo is not a fixed
size: it depends on the star's brightness and on the detector's scattering. Epsilon Indi's halo
extended well beyond the mask, and its residual ring spawned forty-four false candidates in one
field. The fix is an exponential mask whose radius grows with brightness — up to ten times the flux
radius for the brightest sources.

The result is the number on the slide: zero point two per cent becomes **sixty-nine point three**.
Brightening ninety-six point seven, fading ninety-five point three, movers fifty-six.

That is the difference between a demonstration and an instrument, and it came from three physical
arguments, not from a hyperparameter search.

**Bridge.** Now, how do you verify claims like that without knowing the truth?

---

## Slide 16 — Split one visit in half and subtract (1:30) [CORE]

**Purpose.** The null test: manufacture a sky where the true answer is zero.
**On screen.** The schematic; then the three bullets.

**Say.** How do you measure a detector's false-alarm rate when you do not know what is really out
there?

You build a sky where you *do* know: a sky where the truth is zero. Take a single epoch — one visit —
and split its exposures into two interleaved halves. Subtract one half from the other. Both were
taken minutes apart, at the same pointing; no genuine astrophysical change can exist between them.
Every detection that emerges is, by construction, an artefact.

That is the whole trick, and it is powerful because it needs no truth table, no simulation, and no
assumptions about what the sky contains. It only needs enough exposures in one visit.

Across seventeen fields, two hundred and twenty realisations and three and a half square degrees,
raw subtraction produced eight thousand eight hundred and fifty-four peaks. After catalogue matching,
mask-hole verification and bright-star halo vetoes, that compressed to one point six six false
candidates per field — and exactly zero in six star fields.

That number is the honesty test of the entire talk. When I say ten candidates, this is the background
it has to be read against.

**Bridge.** The mirror image of false alarms is what we failed to detect.

---

## Slide 17 — Plant 12 240 sources and count what comes back (1:30) [CORE]

**Purpose.** Completeness, measured with a rule strict enough that it cannot flatter the pipeline.
**On screen.** The recovery heatmap; dark green is high recovery, pale is low.

**Say.** The complement of the null test is completeness. A null test tells you how often the
pipeline believes something false; injection-recovery tells you how often it misses something true.

We injected twelve thousand two hundred and forty synthetic sources into real co-added images — not
into a simulation, into the actual data — each convolved with the empirical PSF for its detector
zone, across six signal-to-noise levels from three to twenty, and four proper motions from two to
twenty arcseconds per year, with random trajectory angles.

The scoring rule is deliberately harsh. A source counts as recovered only if it is detected within
one and a half pixels of where we put it **and** classified as the same kind of thing we planted. A
mover recovered as a monopole is a failure. That matters, because the naive version of this test —
"did anything appear near where I put something" — inflates completeness by tens of per cent.

Brightening: ninety-six point seven per cent. Fading: ninety-five point three. Movers: fifty-six.

Why are movers lower? Because of the geometry. Below about two arcseconds a year, the displacement
over a six-month baseline is roughly one arcsecond — a third of a pixel — which is inside the
point-spread function. The positive and negative lobes overlap and partially cancel, so the
significance of the pair drops even though the motion is real. Above ten arcseconds a year, recovery
is essentially complete. The fifty-six per cent is an average dominated by the slowest injections,
and we report it rather than quietly removing slow movers from the grid.

Overall: sixty-nine point three per cent — from zero point two.

**Bridge.** That is the detector characterised. Now, what else in the sky pretends to be a transient?

---

## Slide 18 — Asteroids move faster than anything else in the frame (1:20)

**Purpose.** Introduce the dominant confuser, and show that identification beats hand-vetoing.
**On screen.** Four boxes left to right, then the dashed return path.

**Say.** The most common false transient in an infrared survey is a rock.

An asteroid moves across the sky at arcminutes per hour. Over a six-month baseline it has long since
left the field — so it appears in visit one, is absent in visit two, and leaves a single monopole
that looks exactly like a stellar flare. There is no morphological signature that distinguishes it
from real astrophysics. The only way to tell is to ask where the solar-system objects were.

So we identify rather than guess. Every exposure's modified Julian date goes to JPL Horizons'
small-body identification service; a match pulls orbital data from the Small-Body Database —
class, diameter, absolute magnitude, near-Earth flag; and CADC's SSOIS service performs an
independent geometric cross-check against archival frames. If a body matches, the detection becomes
`KNOWN_ASTEROID` and never reaches the candidate list.

The call is time-boxed at twenty-five seconds, and it fails honestly. If JPL is unreachable, the card
says the check was unavailable. It does not quietly default to "candidate" — because that would turn
a network problem into a scientific claim.

**Bridge.** Asteroids are the easy confuser. The hard one is the catalogue itself.

---

## Slide 19 — Six verdicts, and one of them means "unknown" (1:30)

**Purpose.** Identity: show why epoch propagation is the difference between a candidate and a known
star.
**On screen.** The decision tree; point at the bottom box when you say "candidate".

**Say.** Catalogue matching is where a detection becomes an identity, and the subtlety is time.

Gaia positions are quoted at epoch 2016. SPHEREx observed in 2026. In those ten years Barnard's Star
moves ninety-four arcseconds — about a hundred pixels, thirty times its own PSF. A naive cone search
centred on the Gaia position therefore misses the star completely. And that is precisely what our
first version did: it flagged Barnard's Star as an unexplained candidate.

So we propagate. Every catalogue position is advanced along its proper-motion vector to the exact
modified Julian date of the exposure, using Gaia DR3 and AllWISE kinematics. The cone search then
happens in the frame where the object actually was.

Match radii also scale with brightness, because bright stars bleed: ten arcseconds for a faint star,
a hundred and twenty for a bright one, plus a hundred-and-eighty-arcsecond halo veto that catches
diffraction spikes and scattered light.

Out of that comes a six-way verdict: `KNOWN_MOVER`, `VARIABLE`, `STATIC_CHANGED`, `BRIGHT_STAR`,
`MASK_ARTEFACT` — and only what survives all five becomes a `CANDIDATE`.

**Bridge.** The classifier that comes next has to respect those verdicts, not overrule them.

---

## Slide 20 — A ranking aid that never overrides the catalogues (1:35)

**Purpose.** Show machine learning used with discipline — and pre-empt the obvious critique by making
it first.
**On screen.** The bars; point at the two pairs as you quote them.

**Say.** We do use machine learning, and it is worth being precise about what it is for.

Twenty-two engineered features describe each detection: signal-to-noise, chi-squared goodness of fit,
ellipticity, the ratio of FWHM to the PSF, local noise ratio, masked fraction, and so on. They feed a
gradient-boosted tree classifier trained on twelve thousand one hundred and fifty labelled examples —
injections, catalogue sources, and split-epoch null residuals. Isotonic calibration then turns the
raw score into a posterior probability, validated field by field with GroupKFold plus four held-out
benchmark fields, so no field can leak into its own evaluation.

Now the honest part, which is the reason this slide exists.

The headline precision is point nine six. That number is inflated, and we say so in the model card.
Our synthetic movers are dipoles; the null-test artefacts almost never are. So the classifier is
partly learning "is this a dipole", which is easy, instead of "is this real", which is the question.

The honest measurement is on monopoles only: precision point nine zero and recall point eight two for
brightening; point seven two and point six three for fading. That is the number to quote if you are
deciding whether to trust the ranking.

And the ranking is all it does. It never overrides a catalogue verdict, it always shows its top three
reasons in plain English, and public votes feed the next training set — a model that improves by
argument rather than by assertion.

**Bridge.** For a candidate that survives all of that, one number still is not enough.

---

## Slide 21 — Five independent streams, one believability score (1:15)

**Purpose.** Explain evidence combination for a layperson: independent witnesses, weighted, and
explainable.
**On screen.** Five boxes converging on the purple score.

**Say.** For a real candidate, no single number is decisive — and pretending otherwise is how
pipelines become overconfident.

So we combine five independent streams of evidence. The calibrated classifier probability. The
matched-filter significance from the subtraction. Kinematic consistency: does the object's motion
agree with fourteen years of NEOWISE and with Gaia's proper motion? Asteroid clearance from JPL. And
optical confirmation from NEOSSat, if the field was observed.

Each stream has a weight, each contributes a value, and the score is the weighted mean — between zero
and one. In the demonstration case it comes out at zero point nine four.

Two properties matter more than the arithmetic. First, every contribution is visible in the
interface with its weight and a plain-English reason, so a human can disagree with the machine in
public. Second, a stream that is unavailable reports a null weight rather than a zero — an archive
outage must not look like evidence against the candidate.

**Bridge.** That is the transient machinery. One last idea turns the same data into a time machine.

---

## Slide 22 — At the ecliptic pole, the same star is seen 20 800 times (1:20)

**Purpose.** Show a second science product that falls out of the orbit, and the artefact that nearly
ruins it.
**On screen.** Polar geometry on the left; the bullets on the right.

**Say.** SPHEREx's orbit gives us something no other all-sky survey has: repetition.

It is a polar orbit, so the field of view sweeps past the ecliptic poles on every orbit — about
twenty thousand eight hundred passes over the mission. At the pole you therefore have a high-cadence
light curve for free, with visits minutes apart rather than months.

There is a catch, and it is the instrument's own design. The filter is linear and variable, so the
wavelength a star is observed at depends on where it lands on the detector. As the spacecraft rolls,
the star lands on different pixels, which means different wavelengths. A raw light curve therefore
swings by tens of per cent for no physical reason at all — it is the star's spectrum being sampled at
different places.

The fix is spectral normalisation: model each star's own spectral energy distribution across all
available bands, divide it out of every visit, and impose a three per cent systematic floor to absorb
sub-pixel flat-field variation. What is left is the star's actual variability.

On a seventy-eight-visit dataset at the north ecliptic pole, the pipeline flagged exactly one star out
of six as variable — and Gaia DR3 independently lists that same star. One detection, correctly
identified, with no false positives.

**Bridge.** And once you have a spectrum per visit, you can do classification as well.

---

## Slide 23 — 102 bands, read as a spectrum (1:15)

**Purpose.** Show the second science product and its guard-rail: no label without evidence.
**On screen.** The wavelength axis with three markers, then the template curves.

**Say.** Because every pixel carries its own wavelength, a cut-out can be turned into a low-resolution
infrared spectrum without any telescope time at all. That is the quiet superpower of a linear variable
filter.

Three indices do most of the work, and each corresponds to a physical fact about the object. Methane
absorption around one point six microns: brown dwarfs have it, most stars do not. Water absorption at
one point five and two microns: icy bodies and cool atmospheres have it in abundance. And the overall
near-infrared slope, which separates warm dust — a young star with a disc — from a bare photosphere.

Put those three together and you can separate an ultracool Y-dwarf from an icy Kuiper Belt object from
a dusty T Tauri star, at least at the level of "this is worth a spectrum".

And then the guard-rail, which matters as much as the classification. A standard cut-out yields
twelve to eighteen usable bands after masking. That is not enough to name a class. So the classifier
refuses: below thirty usable bands it reports the raw fluxes and the indices, and declines to attach
a label. A classification you cannot support is worse than no classification at all, because somebody
will believe it.

**Bridge.** That is the science. Now the engineering that makes it run for other people.

---

## Slide 24 — One FastAPI service, seven routers, one job pool (1:10)

**Purpose.** Establish that the science is served by a real, bounded system.
**On screen.** The application bar, four routers, the job engine underneath.

**Say.** On the engineering side, the backend is one FastAPI application — deliberately thin.

Four public routers cover the surface area: targets and voting, catalogue cone search, deep-field
light curves, and the evaluation metrics. Each router does three things — validate the input,
enqueue the work, stream the result — and nothing else. The science lives below them, in the same
modules the batch pipeline uses.

Heavy work goes into a bounded job pool: one running, eight waiting, each job carrying a
hundred-and-twenty-eight-bit identifier. Bounded matters. An unbounded queue on a scientific service
is a denial-of-service attack that you wrote yourself.

In front of everything sits rate limiting and structured logging, with a correlation identifier
attached to every request. When something goes wrong at three in the morning, that identifier is what
lets you follow one request from the browser through the router to the archive call that failed.

**Bridge.** That service has to be deployed somewhere with two very different workloads.

---

## Slide 25 — Reads and live work run in different services (1:10)

**Purpose.** Justify the split deployment with a concrete failure mode.
**On screen.** Edge, two services, storage, scheduled job.

**Say.** Deployment splits reads from live work, and the reason is a single failure mode: head-of-line
blocking.

A cached read should take milliseconds. An archive fetch can take forty seconds. Put them in the same
service and a handful of slow requests will stall every fast one behind them — the classic way a
scientific web tool becomes unusable exactly when a conference audience is watching.

So there are two Cloud Run services from one image. The API service runs at concurrency eighty and
answers from pre-computed snapshots. The live service runs at concurrency one and streams directly
from IRSA, absorbing the slow work without touching the fast path.

A scheduled job refreshes the snapshots every twelve hours, publishes them as content-addressed
manifests in private storage, and keeps a fourteen-day history — which is what makes "replay the
number you saw last Tuesday" possible.

And a twenty-one-point security review closed before launch: signed proxy tokens so headers cannot be
forged, non-root containers, hashed lock files, bounded queues.

**Bridge.** The last engineering slide is the part the audience will actually touch.

---

## Slide 26 — A mission console in the browser, no build step (1:10)

**Purpose.** Show the interface is a deliberate design decision, not a JavaScript exercise.
**On screen.** The wireframe; the viewer and the verdict card dominate.

**Say.** The front end is deliberately unfashionable: plain HTML, CSS and JavaScript — seven thousand
six hundred and eleven lines — and no build step at all. No framework, no bundler, no
`node_modules`. You open the folder and it runs, which is why the demonstration works on any laptop in
the room and why a reviewer can read the code that produces a number.

It renders a NASA-style mission console: a toolbar, a telemetry strip, four key numbers, and a split
view between the sky viewer and the verdict card. The design goal was that a first-time visitor can
tell, within ten seconds, what the telescope saw and what the software concluded.

Underneath, a two-stage canvas engine does the image work: clip at the half to ninety-nine and a half
percentile, map through a 256-level colour ramp offscreen, then composite five vector overlays — grid,
catalogues, dipoles, reticle and labels — at sixty frames per second.

And a service worker keeps recent snapshots cached in the browser, with two hundred megabytes of
history. That is not a nice-to-have: it is what makes a live demonstration survive conference wifi.

**Bridge.** So, after all of that machinery, what did we actually find?

---

## Slide 27 — Eighteen fields, 109 detections, 99 explained (1:40) [CORE]

**Purpose.** The census. Make the audience comfortable with the fact that most detections are not
discoveries.
**On screen.** The stacked bars for the five brief targets.

**Say.** The census: eighteen fields, one hundred and nine detections.

Ninety-nine are known — ninety-one per cent — and ten are candidates. I want to dwell on that ratio
rather than rush past it, because it is the honest shape of a time-domain survey. Most of what
survives subtraction is not new; it is something you already had in a catalogue, or an artefact of the
instrument.

The bars show the five brief targets, which contribute thirty-five detections between them, seven of
them unexplained. The thirteen benchmarks add the remaining seventy-four.

Two fields produced nothing at all, and both are worth explaining.

Orion M42 is a null sky: the nebular gas is essentially stationary across our baseline, so a correct
subtraction yields nothing. We report zero, rather than lowering the threshold until something
appears.

Sagittarius A-star is refused outright. The two epochs do not share a detector band across that
field, which means there is no wavelength at which both images exist. There is no honest subtraction
to make, so we make none — and the pipeline says why.

**Bridge.** So what are the ten that survived?

---

## Slide 28 — Ten candidates, five fields, one caveat (1:40) [CORE]

**Purpose.** Present the residuals honestly, and close the Barnard's Star loop.
**On screen.** Five amber cards, then the two green cards underneath.

**Say.** The ten candidates cluster in five fields: Epsilon Indi B with three, WISE 0855 with two, the
Pleiades M45 with two, TRAPPIST-1 with two, and the quasar 3C 273 with one.

What do they have in common? They sit at faint infrared excesses beyond the Gaia and WISE detection
limits, in crowded or high-background regions. That is exactly the regime SPHEREx was built to
explore — and exactly the regime where a candidate is a hypothesis rather than a discovery.

Which brings me to the caveat, and to a confession.

Barnard's Star was our very first "candidate". It was never a candidate. It was a coordinate-frame
error: we compared a 2016 position to a 2026 image and were surprised that the star had moved. Once we
propagate the Gaia position to the exposure epoch, the thirty-six point two sigma dipole becomes a
`KNOWN_MOVER`, and that field has zero candidates.

I tell that story because it is the most useful thing in the talk for anyone building a similar
pipeline: the spectacular result you find on day one is frequently the bug you will find on day
thirty.

The green card on the right is the other side of the same coin. In fields containing catalogued
variables, we recovered seven of a hundred and one VSX variables. That sounds poor, and it is a
*lower bound*, not a failure rate: a catalogued variable need not flare during our particular
six-month window, so the test is not "can you find all hundred and one", it is "do the ones that
varied, get found".

**Bridge.** Rather than describe the interface, let me show it to you.

---

## Slide 29 — Six moves, one browser tab (2:15) [CORE]

**Purpose.** Prove the numbers are real, in public, with a rehearsed fallback for every failure.
**On screen.** Leave the slide up as a map for ten seconds, then switch to the console. Return to the
slide for the last sentence if the demo overruns.

**Say.** Six moves, and then I will stop talking about software and let you watch it work.

**Move one.** This is the deployed console, already pointed at Barnard's Star. Everything on screen is
live: the target list, the archive metadata, the timestamps.

**Move two.** Run the pipeline. [Pause while it runs — narrate the wait: "this is fetching two visits
from IRSA and co-adding them".] Now look at the viewer: a red lobe where the star was, a green lobe
where it now is — the thirty-six point two sigma dipole — and the verdict card on the right saying
`KNOWN_MOVER`, with the Gaia designation and the position propagated to this exposure.

**Move three.** Toggle the layers. Co-add, difference, significance. Watch the stationary stars
disappear as the difference layer comes on: that is the calibration working in front of you. The
things that remain are the things that changed.

**Move four.** Press play. The engine steps through the retained snapshots at two and a half seconds a
frame, with the intensity stretch locked to the first frame so the flux stays comparable, and a purple
dashed trail tracing the centroid from visit to visit. That is the motion, in one animation.

**Move five.** All-sky, and over to the Crab Nebula. This is the hard case: extended, filamentary gas
rather than a point source. The point is that it does not crash and does not invent a source; the
residuals stay structured and the pipeline declines to call them candidates.

**Move six.** The accuracy panel: the split-epoch null-test curve, the calibration plot, the injection
matrix. Every number I have quoted this morning is on that screen, produced by this pipeline, not
transcribed by me.

[If the network is down, say:] The cached snapshots are already in this browser — the service worker
serves them — so the demonstration continues; only the live fetch is missing.

**Bridge.** Three points, and then questions.

---

## Slide 30 — Works, honest, and open (1:20) [CORE]

**Purpose.** Land three claims, each with a number, then open the floor.
**On screen.** Three pillars; then the open-horizons strip; then stop advancing.

**Say.** Three things, and each has a number behind it.

**It works.** Sixty-nine point three per cent strict injection recovery on twelve thousand two hundred
and forty planted sources — ninety-seven per cent for flaring sources, ninety-five for fading ones —
after a recalibration that took the same number from zero point two. Ninety-one per cent of detections
are explained by public catalogues. Asteroids are identified from ephemerides rather than assumed.

**It is honest.** One point six six false candidates per field in a null test where the truth is
exactly zero, with six star fields returning zero. Fields that produced nothing are reported as
having produced nothing. The machine-learning bias is printed in the model card rather than buried.
The instrument is calibrated, and the calibration is measured.

**It is open.** Twenty-one thousand four hundred and twenty-nine lines of runtime Python, seven
thousand six hundred and eleven lines of interface, all public, all testable, and reproducible from
NASA's archive — eighteen fields, two hundred and forty-six cut-outs, every number replayable from a
content-addressed snapshot.

The next steps are three: full-mission light curves at the ecliptic poles, GPU deconvolution to push
below the space-based point-spread function, and active learning that retrains on public votes rather
than on my judgement.

Thank you. I am happy to take questions.

---

# Cutting the talk down

The script is written for the full 44-minute route. If you have less time, cut whole sections
*between* bridges rather than trimming sentences — the bridges are written so the argument survives
the gap.

**For the 31-minute route**, add these seven slides to the [CORE] set: 4 (questions), 9
(architecture), 13 (threshold), 14 (morphology), 19 (triage), 20 (machine learning) and 21
(evidence). No bridges change.

**For the 22-minute route**, keep the 13 [CORE] slides and use these replacement bridges:

* **After 3 → 7** (skipping prior art and the gap): "There is a forty-year literature behind this —
  kernels, then closed-form Fourier subtraction — and the only thing you need for the rest of the
  talk is that our method has an analytic null, which is why I can quote a false-alarm rate. And the
  gap it fills is an artefact nobody had published for SPHEREx. Let me show you the data instead."
* **After 7 → 11** (skipping archive and architecture): "We fetch eighteen fields as five-megabyte
  stamps from the public archive, and the code is seven modules with a single JSON contract. What
  matters more is what happens to the pixels before subtraction."
* **After 17 → 27** (skipping asteroids, triage, ML, evidence, deep fields, spectroscopy and all
  implementation): "Everything in that block is about attribution — asteroids, catalogue
  propagation, a classifier, an evidence score, and the time-domain and spectroscopic products that
  fall out of the same data. The result of all of it is the number on the next slide."

**If you are running long mid-talk**, drop in this order: 23 (spectroscopy), 24 (backend), 26
(frontend), 21 (evidence), 22 (deep fields). Each can go without a replacement bridge if you are
already past the previous slide's bridge.

**If you are running short**, expand three places rather than adding slides: the mechanism of the
mask (slide 11), the reason movers are hard (slide 17), and the Barnard's Star story (slide 28).

---

# Glossary — if the room is not astronomical

Use these one-line definitions the first time a term appears, then stop explaining and let the
audience keep up.

| Term | One-line definition |
|---|---|
| **Epoch / visit** | One observation of a field at one moment in time. Two epochs = two photographs. |
| **Cut-out** | A small square of the sky, cut from a much larger image, centred on a target. |
| **PSF (point-spread function)** | The blurry shape a single point of light makes on the detector — the telescope's own signature. |
| **Significance (σ)** | How many standard deviations a measurement sits above the noise. 6σ ≈ a one-in-a-billion fluke for pure noise. |
| **Dipole / monopole** | A pair of opposite blobs (motion) versus a single blob (brightness change). |
| **Proper motion** | An object's real movement across the sky, in arcseconds per year. |
| **Arcsecond (″)** | One 3600th of a degree — roughly the width of a coin seen two kilometres away. |
| **Catalogue** | A published list of known objects: Gaia (positions, motion), WISE/NEOWISE (infrared), VSX (variables). |
| **Astrometry** | Measuring positions and their changes, rather than brightness. |
| **Photometry** | Measuring brightness in a defined band or aperture. |
| **FITS** | The standard file format of astronomy: images plus a header of metadata. |
| **Co-addition** | Averaging several exposures of the same field, weighted by noise, to go deeper. |
| **Injection–recovery** | Planting known fake sources and counting how many the pipeline finds — the standard completeness test. |
| **Null test** | A test where the true answer is known to be zero, used to measure false alarms. |
| **Ecliptic pole** | The point on the sky perpendicular to the Solar System's plane — where a polar-orbiting telescope keeps returning. |

If somebody asks "what is a brown dwarf": "an object heavier than a planet but too light to fuse
hydrogen; it glows in the infrared, and it is one of the things SPHEREx was built to find."

---

# Ten questions you should be able to answer

**Q1. Why 6σ rather than the usual 5σ?**
Because the analytic budget does not survive contact with real data. At 5σ the Gaussian prediction is
0.0186 false pixels per frame; the empirical split-epoch null gave 0.86 false candidates per field —
a factor of forty-six worse — because resampling correlates neighbouring pixels and the tails are
heavier. At 6σ that falls to 0.48 per field with no measurable loss in strict recovery.

**Q2. Why is mover recovery only 56 %?**
Movers are the hardest class physically, not algorithmically. At 2″/yr over a six-month baseline the
displacement is about one arcsecond — a fraction of a pixel, inside the PSF — so the positive and
negative lobes overlap and partially cancel. Above 10″/yr recovery is essentially complete. The 56 %
is an average dominated by the slowest injections; we report it rather than removing them.

**Q3. Aren't the ten candidates just noise?**
They are what survives a 6σ cut, a match against four catalogues, mask-hole verification and a
bright-star halo veto, at a measured false-candidate rate of 1.66 per field. That is a statement about
the ensemble, not about any individual object. Each candidate carries its evidence breakdown so a
follow-up observer can decide.

**Q4. Why not an end-to-end neural network?**
Because the point is auditability. Every step is inspectable: a mask you can look at, a subtraction
you can recompute, a catalogue match you can re-query. A network mapping pixels to candidates would be
shorter to write and impossible to defend line by line — and at this data volume there is nothing for
it to learn that the physics does not already encode.

**Q5. What happens when IRSA retires DR1, as it retired QR2?**
The pipeline already survived that once. The ingestion layer is versioned, so moving releases meant
changing the archive adapter and re-running the evaluation harness, not rewriting the science.
Snapshots are content-addressed with SHA-256, so every number here can be replayed from the stored
manifests even if the upstream archive changes.

**Q6. How do you know the pipeline is not just finding what you injected?**
Because the injection grid is independent of the selection: twelve thousand two hundred and forty
sources over six signal-to-noise levels, four proper motions and random trajectory angles, scored
under a rule that requires the recovered object to be within 1.5 pixels *and* of the right kind. We
also run the same code on real fields with no injections at all, and on split-epoch nulls where
nothing real can exist.

**Q7. What is the dominant systematic you have not solved?**
Slow movers. Between roughly 1″/yr and 3″/yr the dipole is real but barely resolved, so completeness
falls exactly where interesting nearby stars live. The honest fix is a joint fit of position and flux
across all epochs rather than a pairwise difference; that is future work, not a claim.

**Q8. Why is the candidate list so small if SPHEREx sees the whole sky?**
Because this is eighteen fields, not the whole sky: one 0.2° cut-out per field, two epochs, and a 6σ
threshold on a deliberately conservative detector. Scaling to the mission is a throughput problem —
the per-field compute is under a second of FFT work — not a methodological one.

**Q9. How long does one field take?**
From fetched cut-outs to candidate list: seconds, dominated by two FFTs per epoch pair at under 0.6 s
per 180² frame. The wall-clock cost is the archive: a cold fetch is tens of seconds, which is exactly
why the deployment separates cached reads from live streaming.

**Q10. What would falsify the whole approach?**
If split-epoch nulls produced candidates at the same rate as real epoch pairs, the detector would be
measuring systematics rather than sky. That is the first experiment I would run on any new release,
and it is why the null test leads the validation section rather than trailing it.

---

# Rehearsal checklist

- [ ] Read slides 1, 2 and 3 aloud once without notes — they set the contract for everything after.
- [ ] Time slides 15, 16 and 17 together; they are the technical core and they always run long.
- [ ] Run the demonstration twice: once warm (snapshots cached) and once cold (fresh browser, no
      cache), so you know what the audience sees if it lands badly.
- [ ] Check the projector is 16:9. The deck is 160 × 90 mm; a 4:3 projector letterboxes it but crops
      nothing.
- [ ] Have the PDF on a USB stick *and* in cloud storage; the demo is the only part that needs a
      network.
- [ ] Say the four headline numbers aloud — 18 fields, 109 detections, 69.3 % recovery, 1.66 false
      candidates per field — until they are automatic.
- [ ] Decide in advance which slides you will drop if you are ten minutes behind at slide 17.

---

# One-page summary (for the back of your hand)

1. **Question** — two visits, six months apart: can you prove what changed?
2. **Method** — ZOGY Fourier subtraction; an analytic Gaussian null; 6σ calibrated empirically.
3. **Recalibration** — three architectural defects; 0.2 % → 69.3 % strict recovery.
4. **Honesty** — split-epoch nulls: 1.66 false candidates per field, zero in six star fields.
5. **Completeness** — 12 240 injections: 96.7 % / 95.3 % / 56.0 % / 69.3 %.
6. **Result** — 109 detections, 99 known (91 %), 10 candidates in five fields.
7. **Caveat** — "candidate" means unexplained by four catalogues, not discovered.
8. **Open** — 21 429 Python lines, 7 611 UI lines, every number replayable.














**Bridge.** Let us start with why this is harder than subtracting two pictures.
