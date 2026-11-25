"""
spherexlens.ml.imagefeat -- image features and NumPy tree evaluation shared by the ML model (v18).

The feature code was written for the Nov 2026 ML study (first served by the v16 model, now retired) and
is used unchanged by v18, so training and serving compute exactly the same numbers:
  stamps            4 x 21 x 21 cut-outs (epoch A, epoch B, difference, significance), asinh-scaled
  derived/physics   brightness in each epoch, fractional change, residual shapes of the difference
  physics2          brightness-vs-shift decomposition of the difference stamp
  pair_stats/ctx_row  field context: the same decomposition on ~100 ordinary stars of the same image
  colour_model/col_row  Gaia/WISE colour of the star underneath and its colour-corrected change
  forest_proba      evaluation of LightGBM forests exported as plain arrays (no LightGBM needed)
"""
import numpy as np
from scipy.ndimage import maximum_filter
from scipy.spatial import cKDTree

from spherexlens.ml.features import FEATURE_NAMES, extract_features

R = 10                                   # stamp half size -> 21 x 21
DERIVED = ("flux_a", "flux_b", "frac_change", "frac_change_b", "log_flux_a", "log_flux_b", "d_centre")
PHYS = ("d_net_ratio", "d_core_ratio", "d_ring_ratio", "d_psf_corr", "d_peak_offset", "d_neg_frac")
PHYS2 = ("dc_alpha", "dc_shift_px", "dc_flux_share", "dc_r2_full", "dc_r2_flux", "dc_r2_shift", "dc_resid_frac")
CTX = ("ens_n", "ens_alpha_med", "ens_alpha_sd", "alpha_z", "ens_shift", "shift_proj", "shift_cos",
       "ens_r2flux_med", "r2flux_rel", "ens_alpha_absq90")
COL = ("bprp", "g_w1", "star_sep", "dominance", "col_slope", "col_expected", "frac_raw", "frac_colcorr", "z_col")
USED = ("snr", "snr_minus", "fwhm_ratio", "ellipticity", "psf_chi2", "dipole_symmetry", "dipole_sep_px",
        "kind_dipole", "kind_monopole", "kind_monopole_negative", "bright_star_dist_arcsec", "bright_star_gmag",
        "masked_frac", "noise_ratio", "exposure_quality", "abs_ecl_lat_deg")


# ---- tree evaluation (exported LightGBM forests) -------------------------------------------
def _tree_raw(t, X):
    """Raw score of one exported tree for every row of X (LightGBM numerical-split rules)."""
    feat, thr, dleft, miss, left, right, val, leaf = (t[:, k] for k in range(8))
    node = np.zeros(len(X), int)
    active = leaf[node] == 0
    while active.any():
        i = np.where(active)[0]; n = node[i]
        f = X[i, feat[n].astype(int)]
        m = miss[n]
        nan = np.isnan(f)
        f = np.where(nan & (m != 2), 0.0, f)
        use_default = ((m == 1) & (np.abs(f) <= 1e-35)) | ((m == 2) & nan)
        go_left = np.where(use_default, dleft[n] == 1, f <= thr[n])
        node[i] = np.where(go_left, left[n], right[n]).astype(int)
        active = leaf[node] == 0
    return val[node]


def forest_proba(trees, X):
    X = np.asarray(X, float)
    raw = np.zeros(len(X))
    for t in trees:
        raw += _tree_raw(t, X)
    return 1.0 / (1.0 + np.exp(-raw))


def _logit(p):
    p = np.clip(p, 1e-5, 1 - 1e-5)
    return np.log(p / (1 - p))


# ---- stamps ------------------------------------------------------------------------------
def _stamp2(arr, y, x):
    out = np.zeros((2 * R + 1, 2 * R + 1), np.float32)
    ny, nx = arr.shape
    y0, x0 = y - R, x - R
    ys, xs = max(0, y0), max(0, x0)
    ye, xe = min(ny, y + R + 1), min(nx, x + R + 1)
    if ye > ys and xe > xs:
        out[ys - y0:ye - y0, xs - x0:xe - x0] = np.nan_to_num(arr[ys:ye, xs:xe])
    return out


def make_stamp(a, b, D, S, na, nb, sd, y, x):
    """4 x 21 x 21, each layer in noise units, asinh-compressed (float16, as in training)."""
    layers = [_stamp2(a, y, x) / na, _stamp2(b, y, x) / nb, _stamp2(D, y, x) / sd, _stamp2(S, y, x)]
    return np.arcsinh(np.stack(layers) / 3.0).astype(np.float16)


def robust(v):
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    if v.size < 10:
        return 1.0
    s = 1.4826 * np.median(np.abs(v - np.median(v)))
    return float(s) if s > 0 else 1.0


# ---- stamp numbers -----------------------------------------------------------------------
def derived(stamps):
    v = 3.0 * np.sinh(stamps.astype(np.float32))
    c = slice(8, 13)
    fa = v[:, 0, c, c].sum((1, 2)); fb = v[:, 1, c, c].sum((1, 2))
    d = v[:, 2, c, c].sum((1, 2))
    return np.column_stack([fa, fb, (fb - fa) / np.maximum(np.abs(fa), 1.0),
                            (fb - fa) / np.maximum(np.abs(fb), 1.0),
                            np.log10(1 + np.abs(fa)), np.log10(1 + np.abs(fb)), d]).astype(np.float32)


_yy, _xx = np.mgrid[-10:11, -10:11]
_r = np.hypot(_yy, _xx)
_psf = np.exp(-_r ** 2 / (2 * 1.6 ** 2))
_psf /= np.sqrt((_psf ** 2).sum())
_inner = _r <= 5.5


def physics(stamps):
    D = 3.0 * np.sinh(stamps[:, 2].astype(np.float32))
    core = _r <= 2.5
    ring = (_r > 2.5) & (_r <= 5.5)
    inner = _r <= 5.5
    net = (D * inner).sum((1, 2))
    tot = (np.abs(D) * inner).sum((1, 2)) + 1e-6
    c = (D * core).sum((1, 2))
    rg = (D * ring).sum((1, 2))
    sgn = np.sign(c + 1e-9)
    corr = (D * _psf).sum((1, 2)) / (np.sqrt((D[:, inner] ** 2).sum(1)) + 1e-6)
    a = np.abs(D) * inner
    idx = a.reshape(len(a), -1).argmax(1)
    py, px = np.unravel_index(idx, (21, 21))
    off = np.hypot(py - 10, px - 10)
    neg = ((np.sign(D) != sgn[:, None, None]) * np.abs(D) * inner).sum((1, 2)) / tot
    return np.column_stack([net / tot, c / (np.abs(D) * core).sum((1, 2)).clip(1e-6),
                            rg * sgn / (np.abs(c) + 1e-6), corr * sgn, off, neg]).astype(np.float32)


def _r2(D, Xs):
    beta, *_ = np.linalg.lstsq(Xs, D, rcond=None)
    res = D - Xs @ beta
    return beta, 1 - (res ** 2).sum() / max(((D - D.mean()) ** 2).sum(), 1e-9), res


def physics2(stamps):
    st = 3.0 * np.sinh(stamps.astype(np.float32))
    out = np.zeros((len(st), len(PHYS2)), np.float32)
    for i in range(len(st)):
        a, b, D = st[i, 0], st[i, 1], st[i, 2]
        M = 0.5 * (a + b)
        gy, gx = np.gradient(M)
        d = D[_inner]; m = M[_inner]; one = np.ones_like(m)
        beta, r2f, res = _r2(d, np.column_stack([m, gx[_inner], gy[_inner], one]))
        _, r2a, _ = _r2(d, np.column_stack([m, one]))
        _, r2s, _ = _r2(d, np.column_stack([gx[_inner], gy[_inner], one]))
        ef = ((beta[0] * m) ** 2).sum()
        es = ((beta[1] * gx[_inner] + beta[2] * gy[_inner]) ** 2).sum()
        out[i] = [beta[0], np.hypot(beta[1], beta[2]), ef / (ef + es + 1e-9), r2f, r2a, r2s,
                  np.sqrt((res ** 2).sum() / max((d ** 2).sum(), 1e-9))]
    return np.nan_to_num(out)


def decomp(stamps):
    st = 3.0 * np.sinh(stamps.astype(np.float32))
    out = np.zeros((len(st), 4), np.float32)
    for i in range(len(st)):
        a, b, D = st[i, 0], st[i, 1], st[i, 2]
        M = 0.5 * (a + b)
        gy, gx = np.gradient(M)
        d = D[_inner]; m = M[_inner]; one = np.ones_like(m)
        beta, _, _ = _r2(d, np.column_stack([m, gx[_inner], gy[_inner], one]))
        _, r2a, _ = _r2(d, np.column_stack([m, one]))
        out[i] = [beta[0], beta[1], beta[2], r2a]
    return np.nan_to_num(out)


# ---- field context (ordinary stars of the same difference image) --------------------------
def pair_stats(a, b, D, S, na, nb, sd):
    M = np.nan_to_num(a) / na + np.nan_to_num(b) / nb
    ok = np.isfinite(a) & np.isfinite(b) & np.isfinite(D)
    pk = (M == maximum_filter(M, size=7)) & (M > 15) & (M < 3000) & ok
    pk[:12] = pk[-12:] = False; pk[:, :12] = pk[:, -12:] = False
    ys, xs = np.nonzero(pk)
    if len(ys) > 100:
        sel = np.random.default_rng(len(ys)).choice(len(ys), 100, replace=False); ys, xs = ys[sel], xs[sel]
    if len(ys) >= 5:
        st = np.stack([make_stamp(a, b, D, S, na, nb, sd, y, x) for y, x in zip(ys, xs)])
        dc = decomp(st)
        al = dc[:, 0]; med = float(np.median(al))
        return dict(n=len(ys), amed=med, asd=max(1.4826 * float(np.median(np.abs(al - med))), 1e-3),
                    bvec=np.median(dc[:, 1:3], 0), r2=float(np.median(dc[:, 3])),
                    aq90=float(np.quantile(np.abs(al - med), 0.9)))
    return dict(n=len(ys), amed=0.0, asd=np.nan, bvec=np.array([np.nan, np.nan]), r2=np.nan, aq90=np.nan)


def ctx_row(stamp, s):
    a, bx, by, r2 = decomp(stamp[None])[0]
    bv = s["bvec"]; bn = float(np.hypot(*bv)) if np.all(np.isfinite(bv)) else np.nan
    dv = np.array([bx, by]); dn = float(np.hypot(bx, by))
    proj = float(dv @ bv / (bn ** 2 + 1e-6)) if np.isfinite(bn) else np.nan
    cos = float(dv @ bv / (dn * bn + 1e-9)) if np.isfinite(bn) else np.nan
    return [s["n"], s["amed"], s["asd"], (a - s["amed"]) / s["asd"] if np.isfinite(s["asd"]) else np.nan,
            bn, proj, cos, s["r2"], r2 - s["r2"] if np.isfinite(s["r2"]) else np.nan,
            abs(a - s["amed"]) / (s["aq90"] + 1e-6) if np.isfinite(s["aq90"]) else np.nan]


# ---- colour-aware photometry ---------------------------------------------------------------
def _box(img, y, x):
    return float(np.nansum(img[max(0, y - 2):y + 3, max(0, x - 2):x + 3]))


def colour_model(ctx, a, b, ea, eb):
    from catalog_tools import propagate_catalogs
    from spherexlens.eval import harness
    cats = propagate_catalogs(ctx.get("raw_catalogs") or {}, (ctx.get("epoch_mjd") or {}).get(eb))
    gaia = cats.get("gaia") or []; wise = cats.get("wise") or []
    if not gaia:
        return None
    G = np.array([[q["ra"], q["dec"], q.get("gmag") or 99, q.get("bpmag") or np.nan, q.get("rpmag") or np.nan,
                   str(q.get("var_flag") or "").upper() == "VARIABLE"] for q in gaia], float)
    wcs = ctx["wcs"]; H, W = a.shape
    px, py = wcs.world_to_pixel_values(G[:, 0], G[:, 1])
    yi, xi = np.round(py).astype(int), np.round(px).astype(int)
    ok = (yi >= 0) & (yi < H) & (xi >= 0) & (xi < W)
    gflux = 10 ** (-0.4 * (G[:, 2] - 15))
    grid = np.zeros((H, W)); np.add.at(grid, (yi[ok], xi[ok]), gflux[ok])
    gw1 = np.full(len(G), np.nan)
    if wise:
        Wc = np.array([[w["ra"], w["dec"], w.get("w1") if w.get("w1") is not None else np.nan] for w in wise], float)
        c = np.cos(np.radians(ctx["dec"]))
        dd, jj = cKDTree(np.c_[Wc[:, 0] * c, Wc[:, 1]]).query(np.c_[G[:, 0] * c, G[:, 1]], distance_upper_bound=3 / 3600)
        m = np.isfinite(dd); gw1[m] = G[m, 2] - Wc[jj[m], 2]
    bprp = G[:, 3] - G[:, 4]
    dom = np.zeros(len(G))
    for j in np.where(ok)[0]:
        t = grid[max(0, yi[j] - 2):yi[j] + 3, max(0, xi[j] - 2):xi[j] + 3].sum()
        dom[j] = gflux[j] / t if t > 0 else 0
    na = harness.scalar_noise(ctx["noise_maps"][ea])
    E = []
    for j in np.where(ok & (G[:, 5] == 0) & (dom >= 0.5) & np.isfinite(bprp))[0]:
        if yi[j] < 2 or xi[j] < 2 or yi[j] >= H - 2 or xi[j] >= W - 2:
            continue
        fa, fb = _box(a, yi[j], xi[j]), _box(b, yi[j], xi[j])
        if not (np.isfinite(fa) and np.isfinite(fb)) or fa < 50 * na:
            continue
        E.append((fa, (fb - fa) / fa, bprp[j], gw1[j]))
    E = np.array(E) if E else np.zeros((0, 4))
    slope, icpt = (np.polyfit(E[:, 2], E[:, 1], 1) if len(E) >= 30 else (np.nan, np.nan))
    c = np.cos(np.radians(ctx["dec"]))
    return dict(G=G, bprp=bprp, gw1=gw1, dom=dom, E=E, slope=slope, icpt=icpt,
                tree=cKDTree(np.c_[G[:, 0] * c, G[:, 1]]), c=c, wcs=wcs)


def _expected(s, j, fa):
    E = s["E"]
    if len(E) < 30:
        return np.nan, np.nan
    if np.isfinite(s["bprp"][j]):
        for dc, dw in ((0.25, 0.4), (0.5, 0.8)):
            m = np.abs(E[:, 2] - s["bprp"][j]) <= dc
            if np.isfinite(s["gw1"][j]):
                m &= ~np.isfinite(E[:, 3]) | (np.abs(E[:, 3] - s["gw1"][j]) <= dw)
            mb = m & (np.abs(np.log(E[:, 0] / max(fa, 1e-9))) < np.log(5)); mm = mb if mb.sum() >= 10 else m
            if mm.sum() >= 10:
                r = E[mm, 1]; med = float(np.median(r))
                return med, max(0.02, 1.4826 * float(np.median(np.abs(r - med))))
        e = float(s["slope"] * s["bprp"][j] + s["icpt"])
        res = E[:, 1] - (s["slope"] * E[:, 2] + s["icpt"])
        return e, max(0.03, 2 * 1.4826 * float(np.median(np.abs(res))))
    r = E[:, 1]; med = float(np.median(r))
    return med, max(0.02, 1.4826 * float(np.median(np.abs(r - med))))


def col_row(pos, s, a, b):
    out = [np.nan] * len(COL)
    y, x = int(pos[0]), int(pos[1])
    fa, fb = _box(a, y, x), _box(b, y, x)
    frac = (fb - fa) / max(abs(fa), 1e-12) if np.isfinite(fa) and np.isfinite(fb) else np.nan
    out[6] = frac
    if s is None:
        return out
    ra, dec = s["wcs"].pixel_to_world_values(float(pos[1]), float(pos[0]))
    idx = s["tree"].query_ball_point([ra * s["c"], dec], 4.5 / 3600)
    out[4] = s["slope"]
    if idx:
        j = idx[int(np.argmin(s["G"][idx, 2]))]
        out[0], out[1], out[3] = s["bprp"][j], s["gw1"][j], s["dom"][j]
        out[2] = float(np.hypot((s["G"][j, 0] - ra) * s["c"], s["G"][j, 1] - dec) * 3600)
        e, sc = _expected(s, j, fa)
    else:
        e, sc = _expected(dict(s, bprp=np.array([np.nan]), gw1=np.array([np.nan])), 0, fa)
    out[5] = e
    if np.isfinite(e) and np.isfinite(frac):
        fc = (1 + frac) / (1 + e) - 1; out[7] = fc; out[8] = fc / sc
    return out


# ---- per-detection vectors -----------------------------------------------------------------
def _pair_of(d, ctx):
    """(ea, eb) of the detection's own epoch pair, as in training (one pair per image set)."""
    ep = d.get("epochs") or ctx.get("pair") or ctx.get("epoch_order")
    if not ep or len(ep) < 2:
        return None
    return ep[0], ep[-1]


def _pair_setup(ctx, d, ea, eb):
    from spherexlens.eval import harness
    from spherexlens.ml.features import pair_maps
    a, b = ctx["stacks"][ea], ctx["stacks"][eb]
    D, S = pair_maps(dict(d, epochs=[ea, eb]), ctx)
    if D is None or S is None:
        raise ValueError("no difference image")
    na = harness.scalar_noise(ctx["noise_maps"][ea]); nb = harness.scalar_noise(ctx["noise_maps"][eb])
    sd = robust(D)
    try:
        cmod = colour_model(ctx, a, b, ea, eb)
    except Exception:
        cmod = None
    return dict(a=a, b=b, D=D, S=S, na=na, nb=nb, sd=sd, stats=pair_stats(a, b, D, S, na, nb, sd), cmod=cmod)
