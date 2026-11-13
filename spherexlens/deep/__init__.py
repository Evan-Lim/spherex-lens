"""
spherexlens.deep -- Phase 7: SPHEREx deep fields (many epochs).

The DR1 deep survey (`spherex_dr1_deep`) revisits the ecliptic poles
constantly: one point at the North Ecliptic Pole (RA 270.0, Dec +66.56) lies
in ~20,800 deep images over ~385 days (measured 2026-11-13 via IRSA TAP),
versus ~440 in the all-sky DR1 survey.

The catch: SPHEREx has a linear variable filter, so every visit sees a star at
a *different wavelength*.  A raw flux-vs-time curve mixes "the star changed"
with "we looked in a different colour".  lightcurve.py therefore fits each
star's own mean spectrum from all visits and measures variability as the
ratio to that spectrum.

    select.py      choose and download deep exposures spread over the year
    photometry.py  forced aperture photometry + wavelength + centroid per visit
    lightcurve.py  spectrum-normalised light curves, variability, periods, motion
    run.py         CLI: python -m spherexlens.deep.run --name NEP --ra 270 --dec 66.56
"""
