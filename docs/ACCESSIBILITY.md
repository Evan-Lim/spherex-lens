# Accessibility (Section 508 / WCAG 2.1 AA)

What was checked on the SPHERExLens dashboard (`static/`), what was changed,
and what is still open. Checked on 2026-11-13 with headless Chrome at 1440 px
and 390 px, against `?mock=v2` (every v2 field filled) and a pre-rendered
static copy (no v2 fields, no backend).

## Done

| WCAG | Item | How |
|---|---|---|
| 1.1.1 | Text alternatives | Every chart `<canvas>` has `role="img"` and an `aria-label` written from the data (`chartLabel()` in `app.js`), e.g. "Significance histogram: 32400 pixels ... 41 beyond 5 sigma". The sky map's label names the field and counts detections by verdict (`describeMap()`); `#map-summary` points keyboard and screen-reader users to the Detections table, which lists every marker. Vetting-card cutouts, the Accuracy charts and deep-field light curves have labels plus a visible one-line text summary. The logo `<img>` is decorative (`alt=""`). |
| 1.3.1 | Structure | One `<h1>` (brand), `<h2>` per panel/dialog, `<h3>` inside. Table headers carry `scope="col"` / `scope="row"`; new tables have `<caption>`s. The Guide uses the WAI-ARIA tabs pattern (`tablist` / `tab` / `tabpanel`, `aria-selected`, arrow keys, Home/End). |
| 1.4.1 | Not colour alone | Coverage chips show ✓ / ✗ / ? and screen-reader text; new map markers differ by shape (diamond = asteroid verdict, square with a minus = got fainter); ML reasons use ▲/▼ plus hidden text; heat-map cells print the percentage. |
| 1.4.3 | Text contrast ≥ 4.5:1 | Palette measured (script in the report): `--ink-dim` #768390 was 4.18:1 on panels and 3.9:1 on raised surfaces; changed to #8b949e (5.27:1 / 4.89:1). All other text tokens are ≥ 4.8:1 on every surface. New badge colours are ≥ 6.9:1 on their backgrounds. Heat-map text switches black/white by cell luminance. Disabled controls are exempt. |
| 1.4.10 | Reflow at 320 CSS px | No sideways page scroll at 390 px (the coordinate input group was 417 px wide and now wraps; the header wraps). Wide data tables scroll inside their own box. |
| 1.4.11 | Non-text contrast | Focus ring is 2 px #79c0ff (≥ 3:1 on every surface). |
| 2.1.1 | Keyboard | Every new control is a real `<button>` / link. Detection rows have a **Vet** button; catalogue rows are focusable and respond to Enter/Space. Map markers have a keyboard path through the Detections table. |
| 2.1.2 / 2.4.3 | No trap, logical order | Modal sheets (vetting card, Accuracy, Credits, Deep fields) and the Guide keep Tab inside while open, close with Esc / close button / backdrop, and return focus to the control that opened them (a map-opened card returns to that row's Vet button). DOM order follows the visual order. |
| 2.2.2 | Pause, stop, hide | Auto Demo never starts on its own, is a toggle (`aria-pressed`) that stops it, and with `prefers-reduced-motion` it advances every 30 s instead of 12 s. Snapshot playback is user-started and has a stop control. |
| 2.3.3 / reduced motion | Animations | `prefers-reduced-motion: reduce` removes CSS transitions/animations (existing rule), stops the status-dot pulse and spinner, and makes scroll-into-view instant. |
| 2.4.1 | Bypass blocks | "Skip to main content" link, first in tab order. |
| 2.4.7 | Focus visible | `:focus-visible` outline on everything, including table rows. |
| 3.1.1 | Language | `<html lang="en">`. |
| 3.3.2 / 4.1.2 | Labels, name/role/value | Every input/select has a label or `aria-label` (checked automatically: 0 unlabelled, 0 nameless buttons, 0 images without `alt`). Icon buttons (×, −, +, ↻) have `aria-label`s. Toggles expose `aria-pressed`; meters use `role="meter"` with values. |
| 4.1.3 | Status messages | The status pill is a button, so its text is mirrored into a polite live region (`#sr-status`). Progress text (`#action-message`), vote results, the Help-classify note and the map loader are `role="status"`. |

## Remaining / not verified

* **No screen-reader run.** Checked by code review and automated DOM checks,
  not with NVDA, JAWS or VoiceOver. Do one pass with NVDA + Chrome before
  calling the page conformant.
* **The sky map itself** is a canvas: pan/zoom are mouse/wheel or the
  zoom buttons and `+`/`-`/`0` keys, but there is no keyboard cursor over the
  pixels or the catalogue crosses. The Detections and Field Catalogue tables
  are the accessible equivalent.
* **Chart internals** (axis tick text inside canvases) use `--chart-text`
  (#8b949e, ≥ 4.5:1) but are drawn at 11–13 px; they are not resizable text.
  The text summaries carry the information.
* **Floating tool windows** (History, Statistics, Recovery, Help classify) are
  non-modal `role="dialog"`s; they can be dragged with the mouse only.
* **Single-character shortcuts** (`r`, `g`, `d`, `+`, `-`, `0`) exist and
  cannot be turned off (WCAG 2.1.4). They are ignored while typing in inputs.
* The existing cloud-snapshot `<dialog>` uses the browser's native modal
  behaviour and was not changed.
* Colour contrast was checked for the token palette, not for every hard-coded
  colour inside canvas drawings (map overlays, marker labels).
