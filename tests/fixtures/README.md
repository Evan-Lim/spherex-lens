# Offline test fixtures

* `level2_*_cut.fits.gz` -- two real SPHEREx DR1 Level-2 cutouts of the WISE 0855
  field, detector D1, one year apart (2025 W19 and 2026 W19). Full layer set:
  IMAGE, FLAGS, VARIANCE, ZODI, EPSF (21x21 zones of 33x33, oversampled x5),
  WCS-WAVE. Source: NASA/IPAC IRSA, collection `spherex_dr1`.
* `archive/` -- recorded responses from JPL, SSOIS, Gaia, CADC, NEOWISE so the
  adapters are tested without network access.
  Recorded 2026-11-13 (trimmed): `spherex_sia2_dr1_wise0855.xml` (6 SIA2 rows),
  `spherex_s3_list_*.xml` (bucket `nasa-irsa-spherex` ListObjectsV2),
  `gaia_irsa_tap_wise0855.tsv`, `gaia_mast_dr3_wise0855.json` (4 rows, used
  columns), `gaia_vizier_harvard_sentinel.xml` (the Harvard mirror's broken
  I/355/gaiadr3 reply), `gaia_esa_vari_classes.csv`, `cadc_neossat_*`
  (CADC argus ObsCore + DataLink).
* `neossat_NEOS_SCI_2024251021442.fits.gz` -- one raw NEOSSat science frame of
  HD 80606 (primary HDU only), CADC collection NEOSSAT. NEOSSat data (c)
  Canadian Space Agency, Open Government Licence - Canada.
