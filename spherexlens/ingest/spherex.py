"""
spherexlens.ingest.spherex
SPHEREx Level-2 images: the IRSA SIA2 listing (SpherexSIA) and the public AWS
bucket holding the full files (SpherexS3).

SpherexSIA wraps step1_ingest.query_sia2 for fetch() -- step1's behaviour is
unchanged -- and makes its own single, cached SIA2 request for coverage(), so
a coverage check never sits in query_sia2's 15 s/30 s retry sleeps.

SpherexS3 talks plain S3 REST over HTTPS (no boto3, no credentials):

    https://nasa-irsa-spherex.s3.amazonaws.com/?list-type=2&prefix=...&delimiter=/

Bucket layout (verified 2026-11-13):

    dr1/ qr2/ qr3/                         one prefix per release
    dr1/level2/<period>/                   e.g. 2025W19_1B (planning period)
    dr1/level2/<period>/<l2b version>/     e.g. l2b-v27-2026-197
    .../<detector 1-6>/level2_<period>_<obs seq>_<exp>D<det>_spx_<ver>.fits
    dr1/{dark,epsf,sip,spectral_wcs,...}/  calibration products
    dr1/hips5_cube/                        HiPS cube

A full Level-2 file is ~65 MB.  IRSA's IBE URLs map one-to-one onto keys:
``https://irsa.ipac.caltech.edu/ibe/data/spherex/<key>``.  The deep-field
collection (spherex_dr1_deep) serves files from the same dr1/level2 tree.
"""
import os
import time
import xml.etree.ElementTree as ET

import requests

import config
from spherexlens.ingest.base import (Adapter, ArchiveUnavailable, Coverage,
                                     Result, provenance_record)

IBE_PREFIX = "/ibe/data/spherex/"
S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def _step1():
    import step1_ingest            # lazy: step1 imports the whole pipeline
    return step1_ingest


def _filter_time(rows, t_start, t_end):
    out = []
    for r in rows:
        try:
            t = float(r.get("t_min"))
        except (TypeError, ValueError):
            continue
        if (t_start is None or t >= t_start) and (t_end is None or t <= t_end):
            out.append(r)
    return out


def summarise_rows(rows):
    """(n_images, n_epochs, sorted detector bands) for SIA2 rows."""
    usable = [r for r in rows if r.get("dataproduct_type") == "image"
              and str(r.get("access_url", "")).endswith(".fits")]
    n_epochs = len(_step1().group_epochs(usable)) if usable else 0
    bands = sorted({r.get("energy_bandpassname", "").replace("SPHEREx-", "")
                    for r in usable if r.get("energy_bandpassname")})
    return len(usable), n_epochs, bands


class SpherexSIA(Adapter):
    name = "spherex"
    cache_ttl_s = 86400              # listings grow as IRSA adds data

    def __init__(self, collection=None, **kw):
        super().__init__(**kw)
        self.collection = collection or config.SPHEREX_COLLECTION

    def _params(self, ra, dec, radius_deg):
        return {"POS": f"CIRCLE {ra} {dec} {radius_deg}",
                "COLLECTION": self.collection}

    def query(self, ra, dec, radius_deg):
        """One SIA2 request through the shared HTTP layer -> (rows, prov)."""
        url = _step1().IRSA_SIA2
        body, prov = self.http(url, params=self._params(ra, dec, radius_deg))
        try:
            rows = _step1().parse_votable(body)
        except Exception as e:          # QUERY_STATUS error or bad XML
            raise ArchiveUnavailable(f"{self.name}: {e}")
        prov["n_rows"] = len(rows)
        return rows, prov

    def coverage(self, ra, dec, radius_deg, t_start=None, t_end=None):
        rows, _ = self.query(ra, dec, radius_deg)
        rows = _filter_time(rows, t_start, t_end)
        n, n_epochs, bands = summarise_rows(rows)
        detail = f"{n_epochs} epoch{'s' if n_epochs != 1 else ''}"
        if bands:
            detail += f", detectors {','.join(bands)}"
        return Coverage(self.name, True, n, f"{detail} ({self.collection})")

    def fetch(self, ra, dec, radius_deg, t_start=None, t_end=None,
              use_cache=True):
        """step1_ingest.query_sia2 rows (unchanged behaviour), time-filtered."""
        s1 = _step1()
        try:
            rows = s1.query_sia2(ra, dec, radius_deg=radius_deg,
                                 collection=self.collection, use_cache=use_cache)
        except Exception as e:
            raise ArchiveUnavailable(f"{self.name}: {e}")
        rows = _filter_time(rows, t_start, t_end)
        prov = provenance_record(self.name, s1.IRSA_SIA2,
                                 self._params(ra, dec, radius_deg), len(rows))
        return Result(self.name, rows, [prov], {"collection": self.collection})


def s3_key_for_url(access_url):
    """IBE access_url -> bucket key ('dr1/level2/...'), or None."""
    if not access_url or IBE_PREFIX not in access_url:
        return None
    return access_url.split(IBE_PREFIX, 1)[1].split("?", 1)[0]


def parse_s3_listing(body):
    """ListObjectsV2 XML -> (objects, prefixes, next_token)."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise ArchiveUnavailable(f"spherex_s3: bad listing XML: {e}")
    if root.tag.endswith("Error"):
        code = root.findtext("Code") or "?"
        raise ArchiveUnavailable(f"spherex_s3: {code}: {root.findtext('Message')}")
    objects = []
    for c in root.iter(S3_NS + "Contents"):
        objects.append({"key": c.findtext(S3_NS + "Key"),
                        "size": int(c.findtext(S3_NS + "Size") or 0),
                        "last_modified": c.findtext(S3_NS + "LastModified"),
                        "etag": (c.findtext(S3_NS + "ETag") or "").strip('"')})
    prefixes = [p.findtext(S3_NS + "Prefix")
                for p in root.iter(S3_NS + "CommonPrefixes")]
    token = None
    if (root.findtext(S3_NS + "IsTruncated") or "").lower() == "true":
        token = root.findtext(S3_NS + "NextContinuationToken")
    return objects, prefixes, token


class SpherexS3(Adapter):
    """List and download full Level-2 files for bulk / deep-field jobs."""
    name = "spherex_s3"
    cache_ttl_s = 3600

    def __init__(self, endpoint=None, **kw):
        super().__init__(**kw)
        self.endpoint = (endpoint or config.SPHEREX_S3_ENDPOINT).rstrip("/")

    def list(self, prefix, delimiter=None, max_keys=1000, max_pages=None):
        """Every object (and sub-prefix, with a delimiter) under ``prefix``.

        Returns {"objects": [...], "prefixes": [...], "truncated": bool,
        "provenance": [...]}; ``max_pages`` caps the walk (truncated=True).
        """
        objects, prefixes, prov = [], [], []
        token, pages = None, 0
        while True:
            params = {"list-type": "2", "prefix": prefix,
                      "max-keys": str(int(max_keys))}
            if delimiter:
                params["delimiter"] = delimiter
            if token:
                params["continuation-token"] = token
            body, rec = self.http(self.endpoint + "/", params=params)
            objs, prefs, token = parse_s3_listing(body)
            rec["n_rows"] = len(objs) + len(prefs)
            prov.append(rec)
            objects += objs
            prefixes += prefs
            pages += 1
            if not token or (max_pages and pages >= max_pages):
                break
        return {"objects": objects, "prefixes": prefixes,
                "truncated": bool(token), "provenance": prov}

    def coverage(self, ra=None, dec=None, radius_deg=None, t_start=None,
                 t_end=None, prefix="dr1/level2/", max_pages=5):
        """Object count (and volume) under a bucket prefix.

        The bucket has no spatial index, so a cone is answered by SpherexSIA;
        this is for sizing bulk jobs ("how much is under 2025W19_1B/?").
        """
        listing = self.list(prefix, max_pages=max_pages)
        n = len(listing["objects"])
        gb = sum(o["size"] for o in listing["objects"]) / 1e9
        more = "+" if listing["truncated"] else ""
        return Coverage(self.name, True, n,
                        f"{n}{more} objects, {gb:.1f}{more} GB under {prefix}")

    def url_for_key(self, key):
        return f"{self.endpoint}/{key}"

    def download(self, key, dest_dir, expected_size=None, overwrite=False,
                 chunk=1 << 20):
        """Stream one object to ``dest_dir``; returns (path, provenance).

        Written to <name>.part and renamed only when complete, so an
        interrupted bulk job never leaves a truncated FITS behind.
        """
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, os.path.basename(key))
        url = self.url_for_key(key)
        if os.path.exists(path) and not overwrite and (
                expected_size is None or os.path.getsize(path) == expected_size):
            rec = provenance_record(self.name, url)
            rec["cached"] = True
            return path, rec
        last = None
        for attempt in range(config.HTTP_RETRIES):
            try:
                with self.session.get(url, stream=True,
                                      timeout=config.HTTP_TIMEOUT_S) as r:
                    if r.status_code >= 400:
                        last = f"HTTP {r.status_code}"
                        if r.status_code < 500:
                            break
                    else:
                        tmp = path + ".part"
                        n = 0
                        with open(tmp, "wb") as f:
                            for block in r.iter_content(chunk):
                                f.write(block)
                                n += len(block)
                        if expected_size is not None and n != expected_size:
                            last = f"short read {n} of {expected_size} bytes"
                            os.remove(tmp)
                        else:
                            os.replace(tmp, path)
                            rec = provenance_record(self.name, url)
                            rec["bytes"] = n
                            return path, rec
            except requests.RequestException as e:
                last = f"{type(e).__name__}: {e}"
            time.sleep(config.HTTP_BACKOFF_S * (2 ** attempt))
        raise ArchiveUnavailable(f"{self.name}: {key}: {last}")

    def fetch_prefix(self, prefix, dest_dir, max_files=None, match=None):
        """Bulk download every .fits under a prefix (optionally filtered)."""
        listing = self.list(prefix)
        objs = [o for o in listing["objects"] if o["key"].endswith(".fits")
                and (match is None or match in o["key"])]
        if max_files:
            objs = objs[:max_files]
        rows, prov = [], list(listing["provenance"])
        for o in objs:
            path, rec = self.download(o["key"], dest_dir, o["size"])
            rows.append({**o, "path": path, "url": self.url_for_key(o["key"])})
            prov.append(rec)
        return Result(self.name, rows, prov, {"prefix": prefix})

    def fetch(self, ra, dec, radius_deg, t_start=None, t_end=None,
              dest_dir=None, max_files=None, bands=None, sia=None):
        """Full Level-2 files for every SIA2 image in a cone.

        Without ``dest_dir`` nothing is downloaded: the rows list the keys
        and HTTPS URLs (useful to plan a bulk job).
        """
        sia = sia or SpherexSIA(session=self.session, use_cache=self.use_cache)
        listing = sia.fetch(ra, dec, radius_deg, t_start, t_end)
        rows, prov = [], list(listing.provenance)
        for r in listing.rows:
            key = s3_key_for_url(r.get("access_url"))
            if not key or (bands and r.get("energy_bandpassname") not in bands):
                continue
            rows.append({"key": key, "url": self.url_for_key(key),
                         "obs_id": r.get("obs_id"), "t_min": r.get("t_min"),
                         "band": r.get("energy_bandpassname")})
        if max_files:
            rows = rows[:max_files]
        if dest_dir:
            for row in rows:
                row["path"], rec = self.download(row["key"], dest_dir)
                prov.append(rec)
        return Result(self.name, rows, prov, {"collection": sia.collection})
