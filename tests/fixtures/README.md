# Offline test fixtures

* `level2_*_cut.fits.gz` -- two real SPHEREx DR1 Level-2 cutouts of the WISE 0855
  field, detector D1, one year apart (2025 W19 and 2026 W19). Full layer set:
  IMAGE, FLAGS, VARIANCE, ZODI, EPSF (21x21 zones of 33x33, oversampled x5),
  WCS-WAVE. Source: NASA/IPAC IRSA, collection `spherex_dr1`.
* `archive/` -- recorded responses from JPL, SSOIS, Gaia, CADC, NEOWISE so the
  adapters are tested without network access.
