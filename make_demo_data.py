"""
make_demo_data.py

OPTIONAL developer utility -- this file is NOT one of the ten blueprint files.

step1_ingest.py needs the IRSA SIA2 service (and VizieR) to be reachable. When
you are offline, on a throttled network, or rehearsing a demo, this script
writes a small but structurally faithful SPHEREx cache into data_cache/:

  * four Level-2-like cutouts per target, with IMAGE, FLAGS, VARIANCE, CWAVE
    and CBAND extensions and a real TAN WCS;
  * two epochs of two exposures each, so app.py can co-add and subtract;
  * a static star field, one moving source (proper motion) and one variable
    source (monopole), sampled on SPHEREx detector D3 and D4 wavelength ranges;
  * a *_meta.json whose Gaia DR3 / AllWISE / VSX entries match the static stars
    and the variable, so triage yields both KNOWN and CANDIDATE verdicts.

Data written here is synthetic and must never be presented as NASA data.

Run:  python make_demo_data.py [--npix 400] [--overwrite]
"""

import os
import json
import zlib
import argparse

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

CACHE = "data_cache"

PIXSCALE = 1.5          # arcsec per pixel of the synthetic cutouts
PSF_SIGMA_PX = 3.0      # ~4.5 arcsec FWHM, close to the SPHEREx PSF
NOISE_SIGMA = 3.0       # MJy / sr per pixel
N_STARS = 40
EXPOSURES_PER_EPOCH = 2

# SPHEREx detector wavelength coverage: six arrays x 17 channels = 102 bands
# across 0.75-5.00 um (blueprint Section 3.4).
DETECTORS = {
    "D1": (0.75, 1.09),
    "D2": (1.10, 1.62),
    "D3": (1.63, 2.41),
    "D4": (2.42, 3.82),
    "D5": (3.83, 4.41),
    "D6": (4.42, 5.00),
}
CHANNELS_PER_DETECTOR = 17
EXPOSURE_DETECTORS = ["D3", "D4", "D3", "D4"]   # epochs 1 and 2 use D3 + D4

# FLAGS bits, mirroring step2_coadd.py.
BIT_DEAD = 1 << 0
BIT_SATURATED = 1 << 1
BIT_COSMIC_RAY = 1 << 2
BIT_HOT_PIXEL = 1 << 5
BIT_STRAY_LIGHT = 1 << 8

# Per-target SED parameters for the synthetic 102-band spectrum.  The
# absorption troughs are placed exactly where step5_spectrum.py samples them:
# CH4 at 1.72 / 2.32 um, H2O at 1.50 / 2.00 / 3.00 um.
SED_PARAMS = {
    "Barnards_Star": {"temp": 3134.0, "ch4": 0.0, "h2o": 0.15, "dust": 0.30,
                      "hot": 0.0, "expect": "M-dwarf / Mira red giant"},
    "Epsilon_Indi_B": {"temp": 1200.0, "ch4": 0.75, "h2o": 0.40, "dust": 0.0,
                       "hot": 0.2, "expect": "T-type brown dwarf"},
    "WISE_0855": {"temp": 300.0, "ch4": 0.90, "h2o": 0.60, "dust": 0.0,
                  "hot": 0.2, "expect": "Ultracool Y-dwarf"},
    "RW_Aurigae": {"temp": 4000.0, "ch4": 0.05, "h2o": 0.10, "dust": 2.0,
                   "hot": 0.0, "expect": "Dusty T Tauri / variable star"},
    "R_Draconis": {"temp": 2900.0, "ch4": 0.05, "h2o": 0.20, "dust": 0.30,
                   "hot": 0.0, "expect": "M-dwarf / Mira red giant"},
}

TARGETS = {
    "Barnards_Star": {"ra": 269.452083, "dec": 4.693333,
                      "type": "Mover_M4V", "common": "Barnard's Star"},
    "Epsilon_Indi_B": {"ra": 331.043750, "dec": -56.782778,
                       "type": "Mover_TDwarf", "common": "Epsilon Indi B"},
    "WISE_0855": {"ra": 133.795129, "dec": -7.245147,
                  "type": "Mover_YDwarf", "common": "WISE 0855"},
    "RW_Aurigae": {"ra": 76.956250, "dec": 30.401389,
                   "type": "Variable_TTauri", "common": "RW Aurigae"},
    "R_Draconis": {"ra": 248.167500, "dec": 66.754722,
                   "type": "Variable_Mira", "common": "R Draconis"},
}


def make_wcs(ra_deg, dec_deg, npix, jitter_arcsec=(0.0, 0.0), rotation_deg=0.0):
    """North-up TAN WCS with an optional pointing jitter and roll."""
    w = WCS(naxis=2)
    w.wcs.crpix = [npix / 2.0, npix / 2.0]
    w.wcs.cdelt = np.array([-PIXSCALE / 3600.0, PIXSCALE / 3600.0])
    w.wcs.crval = [ra_deg + jitter_arcsec[0] / 3600.0,
                   dec_deg + jitter_arcsec[1] / 3600.0]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    theta = np.radians(rotation_deg)
    w.wcs.pc = np.array([[np.cos(theta), -np.sin(theta)],
                         [np.sin(theta), np.cos(theta)]])
    return w


def _add_source(img, x, y, amplitude, sigma=PSF_SIGMA_PX):
    """Add a Gaussian point source to img, stamping only a local patch."""
    ny, nx = img.shape
    half = int(np.ceil(4.0 * sigma))
    x0, x1 = max(0, int(x) - half), min(nx, int(x) + half + 1)
    y0, y1 = max(0, int(y) - half), min(ny, int(y) + half + 1)
    if x0 >= x1 or y0 >= y1:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1]
    img[y0:y1, x0:x1] += amplitude * np.exp(
        -(((xx - x) ** 2 + (yy - y) ** 2) / (2.0 * sigma ** 2))
    )


def sky_offset_to_radec(ra_c, dec_c, dra_arcsec, ddec_arcsec):
    """Tiny-angle conversion from tangent-plane offsets to sky coordinates."""
    dec = dec_c + ddec_arcsec / 3600.0
    ra = ra_c + dra_arcsec / 3600.0 / max(np.cos(np.radians(dec_c)), 1e-6)
    return ra, dec


def build_star_field(rng, npix):
    """Static stars as (dra_arcsec, ddec_arcsec, peak_flux)."""
    half = 0.5 * npix * PIXSCALE - 30.0
    stars = []
    for _ in range(N_STARS):
        dra = rng.uniform(-half, half)
        ddec = rng.uniform(-half, half)
        flux = float(10.0 ** rng.uniform(0.5, 2.2))
        stars.append((dra, ddec, flux))
    # Brightest first, so catalogs can take a prefix.
    stars.sort(key=lambda s: -s[2])
    return stars


def exposure_image(rng, wcs, npix, ra_c, dec_c, stars, sources):
    """
    Render one exposure.

    ra_c, dec_c : nominal target centre (before pointing jitter), so that a
                  pointing jitter moves the field on the detector
    stars       : static (dra, ddec, flux) tuples
    sources     : extra (dra, ddec, flux) tuples (movers, variables) for this epoch
    """
    img = np.zeros((npix, npix), dtype=np.float32)

    for dra, ddec, flux in list(stars) + list(sources):
        ra, dec = sky_offset_to_radec(ra_c, dec_c, dra, ddec)
        x, y = wcs.world_to_pixel_values(ra, dec)
        _add_source(img, float(x), float(y), flux)

    img += rng.normal(0.0, NOISE_SIGMA, img.shape).astype(np.float32)
    return img


def make_flags(rng, img, npix):
    """
    Contamination bitmask: cosmic rays, hot pixels, dead pixels, a stray-light
    edge and saturated cores -- one of each of the five bits step2_coadd masks.
    """
    flags = np.zeros((npix, npix), dtype=np.int32)
    flags[rng.random((npix, npix)) < 0.004] |= BIT_COSMIC_RAY
    flags[rng.random((npix, npix)) < 0.002] |= BIT_HOT_PIXEL

    # A few dead pixels, as short vertical defects.
    for _ in range(4):
        x = int(rng.integers(10, npix - 10))
        y = int(rng.integers(10, npix - 10))
        flags[y:y + 3, x] |= BIT_DEAD

    # Stray light creeping in along the detector edge.
    flags[:, :5] |= BIT_STRAY_LIGHT

    # Saturated cores: only the brightest pixels, as on real data.
    flags[img > np.median(img) + 40.0 * NOISE_SIGMA] |= BIT_SATURATED
    return flags


def band_wavelengths():
    """Central wavelengths (um) of the 102 SPHEREx spectral channels."""
    bands = []
    for det in ("D1", "D2", "D3", "D4", "D5", "D6"):
        lam0, lam1 = DETECTORS[det]
        bands.append(np.linspace(lam0, lam1, CHANNELS_PER_DETECTOR))
    return np.concatenate(bands)


def planck_shape(lam_um, temp):
    """
    Planck function per unit wavelength, normalized to its peak.

    Evaluated in log space so that cool objects (Y-dwarfs at a few hundred K)
    keep real dynamic range instead of underflowing to zero at short
    wavelengths.
    """
    lam_m = np.asarray(lam_um, dtype=float) * 1e-6
    x = 6.62607015e-34 * 2.99792458e8 / (lam_m * 1.380649e-23 * temp)
    log_b = -5.0 * np.log(lam_m) - x - np.log1p(-np.exp(-np.minimum(x, 700.0)))
    return np.exp(log_b - log_b.max())


def _trough(lam, centre, width):
    return np.exp(-0.5 * ((lam - centre) / width) ** 2)


def synthetic_sed(wavelengths, temp, ch4_depth, h2o_depth, dust_amp=0.0,
                  hot_frac=0.0, hot_temp=1400.0, dust_temp=900.0):
    """
    Two-component continuum + molecular absorption + optional hot dust.

    A single blackbody cannot reproduce the near-infrared shape of a cool
    atmosphere (real T/Y dwarfs are far bluer in H band than a Planck curve
    because the flux emerges from deeper, hotter layers inside the absorption
    bands), so the continuum is a cool + hot blackbody mixture.  The CH4 and
    H2O troughs sit at the wavelengths step5_spectrum.py samples, so the
    three-index classifier acts on a physically-shaped spectrum.
    """
    lam = np.asarray(wavelengths, dtype=float)
    continuum = (1.0 - hot_frac) * planck_shape(lam, temp)
    if hot_frac > 0.0:
        continuum = continuum + hot_frac * planck_shape(lam, hot_temp)
    continuum /= continuum.max()

    ch4 = np.clip(_trough(lam, 1.72, 0.09) + 0.9 * _trough(lam, 2.32, 0.10), 0, 1)
    h2o = np.clip(_trough(lam, 1.50, 0.05) + 0.95 * _trough(lam, 2.00, 0.09)
                  + 0.6 * _trough(lam, 3.00, 0.22), 0, 1)

    flux = continuum * (1.0 - ch4_depth * ch4) * (1.0 - h2o_depth * h2o)
    if dust_amp > 0.0:
        dust = planck_shape(lam, dust_temp)
        flux = flux + dust_amp * dust * continuum.max()
    return flux


def build_spectrum(name, rng, peak_flux=8.0):
    """102-band (wavelength, flux, error) arrays for one target."""
    params = SED_PARAMS[name]
    lam = band_wavelengths()
    flux = synthetic_sed(lam, params["temp"], params["ch4"], params["h2o"],
                         params["dust"], params["hot"])
    flux = peak_flux * flux / flux.max()
    noisy = flux * (1.0 + rng.normal(0.0, 0.01, flux.shape))
    error = 0.01 * flux
    return lam, noisy, error


def write_spectrum(path, name, wavelengths, flux, error):
    """Write a 102-row SPECTRUM binary table next to the cutouts."""
    cols = [
        fits.Column(name="WAVELENGTH", format="E", unit="um", array=wavelengths),
        fits.Column(name="FLUX", format="E", unit="MJy/sr", array=flux),
        fits.Column(name="ERROR", format="E", unit="MJy/sr", array=error),
    ]
    hdu = fits.BinTableHDU.from_columns(cols, name="SPECTRUM")
    hdu.header["TARGET"] = (TARGETS[name]["common"], "Target name")
    hdu.header["NPTS"] = (len(wavelengths), "Number of spectral channels")
    hdu.header["ORIGIN"] = ("make_demo_data.py", "SYNTHETIC demo data")
    fits.HDUList([fits.PrimaryHDU(), hdu]).writeto(path, overwrite=True)


def read_spectrum(path):
    """Read a SPECTRUM table written by write_spectrum()."""
    with fits.open(path) as hdul:
        tab = hdul["SPECTRUM"].data
        return (np.asarray(tab["WAVELENGTH"], dtype=float),
                np.asarray(tab["FLUX"], dtype=float),
                np.asarray(tab["ERROR"], dtype=float))


def write_cutout(path, img, flags, var, wcs, detector, exposure_id):
    """Write a Level-2-like FITS file with IMAGE / FLAGS / VARIANCE / CWAVE / CBAND."""
    lam0, lam1 = DETECTORS[detector]
    npix = img.shape[0]
    ramp = np.linspace(lam0, lam1, npix, dtype=np.float32)
    cwave = np.tile(ramp[:, None], (1, npix))
    cband = np.full((npix, npix), (lam1 - lam0) / 17.0, dtype=np.float32)

    header = wcs.to_header()
    header["DETECTOR"] = (detector, "SPHEREx detector array")
    header["EXPOSURE"] = (exposure_id, "Synthetic exposure index")
    header["BUNIT"] = ("MJy/sr", "Surface brightness")
    header["ORIGIN"] = ("make_demo_data.py", "SYNTHETIC demo data, not NASA data")

    hdus = [fits.PrimaryHDU()]
    for ext_name, data in (("IMAGE", img), ("FLAGS", flags),
                           ("VARIANCE", var), ("CWAVE", cwave), ("CBAND", cband)):
        hdus.append(fits.ImageHDU(data, header=header.copy(), name=ext_name))
    fits.HDUList(hdus).writeto(path, overwrite=True)


def build_catalogs(ra_c, dec_c, stars, variable, target_name):
    """Catalog JSON that matches the static field (KNOWN) plus the variable."""
    gaia, wise = [], []
    for dra, ddec, flux in stars[:20]:
        ra, dec = sky_offset_to_radec(ra_c, dec_c, dra, ddec)
        mag = float(np.clip(20.0 - 2.5 * np.log10(max(flux, 1e-3)), 8.0, 22.0))
        gaia.append({"ra": ra, "dec": dec, "pm_ra": 0.0, "pm_de": 0.0,
                     "gmag": mag})
        wise.append({"ra": ra, "dec": dec, "w1": mag + 0.4, "w2": mag + 0.5})

    v_ra, v_dec = sky_offset_to_radec(ra_c, dec_c, variable[0], variable[1])
    vsx = [{"ra": v_ra, "dec": v_dec,
            "name": f"{target_name} demo variable", "type": "VAR"}]
    return {"gaia": gaia, "wise": wise, "vsx": vsx}


def make_target(name, meta, npix=400, overwrite=False):
    """Write four synthetic exposures plus <name>_meta.json for one target."""
    rng = np.random.default_rng(zlib.crc32(name.encode()) & 0xFFFFFFFF)
    ra_c, dec_c = meta["ra"], meta["dec"]
    stars = build_star_field(rng, npix)

    # One proper-motion source: 4 arcsec of motion over the 0.5 yr gap (8"/yr).
    mover = (6.0, -2.0, 60.0)
    mover_epoch2 = (2.0, -2.0, 60.0)
    # One variable: flux increases by ~2x between epochs (monopole in D).
    variable = (-22.0, 28.0)
    var_flux = {1: 45.0, 2: 95.0}

    paths = []
    for i in range(2 * EXPOSURES_PER_EPOCH):
        epoch = 1 if i < EXPOSURES_PER_EPOCH else 2
        fname = os.path.join(CACHE, f"demo_{name}_{i:02d}.fits")
        paths.append(fname)
        if os.path.exists(fname) and not overwrite:
            print(f"  keeping existing {fname}")
            continue

        jitter = (rng.uniform(-2.0, 2.0), rng.uniform(-2.0, 2.0))
        rotation = rng.uniform(-0.4, 0.4)
        wcs = make_wcs(ra_c, dec_c, npix, jitter_arcsec=jitter,
                       rotation_deg=rotation)

        extra = []
        if epoch == 1:
            extra.append(mover)
        else:
            extra.append(mover_epoch2)
        extra.append((variable[0], variable[1], var_flux[epoch]))

        img = exposure_image(rng, wcs, npix, ra_c, dec_c, stars, extra)
        flags = make_flags(rng, img, npix)
        var = np.full((npix, npix), NOISE_SIGMA ** 2, dtype=np.float32)
        write_cutout(fname, img, flags, var, wcs,
                     detector=EXPOSURE_DETECTORS[i], exposure_id=i)

    record = {
        "target": name,
        "meta": meta,
        "paths": paths,
        "catalogs": build_catalogs(ra_c, dec_c, stars, variable, name),
        "note": "SYNTHETIC demo data written by make_demo_data.py",
    }

    spec_path = os.path.join(CACHE, f"demo_{name}_spectrum.fits")
    if overwrite or not os.path.exists(spec_path):
        lam, flux, err = build_spectrum(name, rng)
        write_spectrum(spec_path, name, lam, flux, err)
    record["spectrum_fits"] = spec_path

    meta_path = os.path.join(CACHE, f"{name}_meta.json")
    with open(meta_path, "w") as f:
        json.dump(record, f, indent=2)

    print(f"  {name}: {len(paths)} cutouts, "
          f"{len(record['catalogs']['gaia'])} Gaia / "
          f"{len(record['catalogs']['wise'])} AllWISE / "
          f"{len(record['catalogs']['vsx'])} VSX sources")

    # Sanity check: the real classifier must recover the intended spectral type.
    from step5_spectrum import three_index_classifier

    lam, flux, _ = read_spectrum(spec_path)
    result = three_index_classifier(lam, flux)
    expect = SED_PARAMS[name]["expect"]
    flag = "OK " if result["label"] == expect else "MISMATCH"
    print(f"  102-band spectrum -> {flag} ch4={result['ch4']:.2f} "
          f"h2o={result['h2o']:.2f} nir_slope={result['nir_slope']:.2f} "
          f"label='{result['label']}' (expected '{expect}')")
    return record


def main():
    parser = argparse.ArgumentParser(
        description="Write a synthetic SPHEREx cache into data_cache/.")
    parser.add_argument("--npix", type=int, default=400,
                        help="cutout size in pixels (default 400 -> 10 arcmin)")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace existing demo FITS files")
    parser.add_argument("--targets", nargs="*", default=None,
                        help="subset of target keys (default: all five)")
    args = parser.parse_args()

    os.makedirs(CACHE, exist_ok=True)
    names = args.targets or list(TARGETS)
    print(f"Writing synthetic SPHEREx cutouts to {CACHE}/ ...")
    for name in names:
        if name not in TARGETS:
            raise SystemExit(f"Unknown target '{name}'. Choose from {list(TARGETS)}")
        print(f" {name}")
        make_target(name, TARGETS[name], npix=args.npix, overwrite=args.overwrite)
    print("\nDone. Next: uvicorn app:app --port 8000")


if __name__ == "__main__":
    main()


