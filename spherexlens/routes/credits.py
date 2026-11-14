"""
GET /api/credits -- the data sources SPHEREx-Lens uses, with the
acknowledgement each asks for, its licence/terms and where the wording came
from.

``wording_verified`` is true only where the text was copied from the
provider's own page (``wording_source``) on 2026-11-13.  Entries marked false
use the commonly published wording and should be checked against
``wording_source`` before a publication quotes them.
"""
from fastapi import APIRouter

router = APIRouter()

CREDITS_CHECKED = "2026-11-13"

SOURCES = [
    {
        "id": "irsa_spherex",
        "name": "SPHEREx (NASA/IPAC Infrared Science Archive)",
        "used_for": "SPHEREx spectral images (SIA2 cutouts), the core data of every dashboard",
        "acknowledgement": (
            "This publication makes use of data products from the Spectro-Photometer "
            "for the History of the Universe, Epoch of Reionization and Ices Explorer "
            "(SPHEREx), which is a joint project of the Jet Propulsion Laboratory and "
            "the California Institute of Technology, and is funded by the National "
            "Aeronautics and Space Administration."),
        "citation": "Cite the IRSA DOI of the SPHEREx data release used "
                    "(listed in the IRSA SPHEREx documentation).",
        "licence": "NASA data: no copyright restrictions; acknowledgement requested",
        "url": "https://irsa.ipac.caltech.edu/Missions/spherex.html",
        "wording_source": "https://irsa.ipac.caltech.edu/Missions/spherex.html",
        "wording_verified": True,
    },
    {
        "id": "irsa",
        "name": "NASA/IPAC Infrared Science Archive (IRSA)",
        "used_for": "SIA2 image search, TAP catalogue queries (AllWISE, NEOWISE), finder charts",
        "acknowledgement": (
            "This research has made use of the NASA/IPAC Infrared Science Archive, "
            "which is funded by the National Aeronautics and Space Administration and "
            "operated by the California Institute of Technology."),
        "licence": "NASA data: no copyright restrictions; acknowledgement requested",
        "url": "https://irsa.ipac.caltech.edu/",
        "wording_source": "https://irsa.ipac.caltech.edu/ack.html (identical, checked 2026-11-13)",
        "wording_verified": True,
    },
    {
        "id": "irsa_wise",
        "name": "WISE / AllWISE (NASA/IPAC IRSA)",
        "used_for": "AllWISE catalogue cross-matching and colours",
        "acknowledgement": (
            "This publication makes use of data products from the Wide-field Infrared "
            "Survey Explorer, which is a joint project of the University of California, "
            "Los Angeles, and the Jet Propulsion Laboratory/California Institute of "
            "Technology, funded by the National Aeronautics and Space Administration."),
        "citation": "Wright et al. 2010, AJ, 140, 1868",
        "licence": "NASA data: no copyright restrictions; acknowledgement requested",
        "url": "https://irsa.ipac.caltech.edu/Missions/wise.html",
        "wording_source": "https://wise2.ipac.caltech.edu/docs/release/allsky/expsup/sec1_6b.html",
        "wording_verified": True,
    },
    {
        "id": "irsa_neowise",
        "name": "NEOWISE (NASA/IPAC IRSA)",
        "used_for": "NEOWISE single-exposure time series (motion consistency)",
        "acknowledgement": (
            "This publication also makes use of data products from NEOWISE, which is a "
            "project of the Jet Propulsion Laboratory/California Institute of "
            "Technology, funded by the Planetary Science Division of the National "
            "Aeronautics and Space Administration."),
        "citation": "Mainzer et al. 2011, ApJ, 731, 53; Mainzer et al. 2014, ApJ, 792, 30",
        "licence": "NASA data: no copyright restrictions; acknowledgement requested",
        "url": "https://irsa.ipac.caltech.edu/Missions/wise.html",
        "wording_source": "https://wise2.ipac.caltech.edu/docs/release/allsky/expsup/sec1_6b.html",
        "wording_verified": True,
    },
    {
        "id": "gaia",
        "name": "ESA Gaia (DR3)",
        "used_for": "Astrometry and proper motions of known stars",
        "acknowledgement": (
            "This work has made use of data from the European Space Agency (ESA) "
            "mission Gaia (https://www.cosmos.esa.int/gaia), processed by the Gaia "
            "Data Processing and Analysis Consortium (DPAC, "
            "https://www.cosmos.esa.int/web/gaia/dpac/consortium). Funding for the "
            "DPAC has been provided by national institutions, in particular the "
            "institutions participating in the Gaia Multilateral Agreement."),
        "citation": "Gaia Collaboration et al. 2016 (The Gaia mission); "
                    "Gaia Collaboration et al. 2023 (Gaia DR3 summary)",
        "licence": "CC BY-NC 3.0 IGO (Gaia data licence)",
        "licence_url": "https://www.cosmos.esa.int/web/gaia-users/license",
        "url": "https://www.cosmos.esa.int/web/gaia-users/credits",
        "wording_source": "https://gea.esac.esa.int/archive/documentation/GDR3/Miscellaneous/sec_credit_and_citation_instructions/",
        "wording_verified": True,
    },
    {
        "id": "mast",
        "name": "STScI Mikulski Archive for Space Telescopes (MAST)",
        "used_for": "Gaia/MAST portal links for each detection",
        "acknowledgement": (
            "This research has made use of data obtained from the Mikulski Archive for "
            "Space Telescopes (MAST) at the Space Telescope Science Institute, which is "
            "operated by the Association of Universities for Research in Astronomy, "
            "Inc., under NASA contract NAS5–26555."),
        "licence": "Public archive; see MAST data attribution guidance",
        "url": "https://archive.stsci.edu/publishing/mission-acknowledgements",
        "wording_source": "https://archive.stsci.edu/publishing/mission-acknowledgements "
                          "(MAST publishes per-mission templates only, no generic sentence; "
                          "adapted from them, checked 2026-11-13). MAST also asks papers to "
                          "cite a dataset DOI: https://archive.stsci.edu/doi/search/",
        "wording_verified": False,
    },
    {
        "id": "jpl_ssd",
        "name": "NASA/JPL Solar System Dynamics (SBDB, SB Identification)",
        "used_for": "Known-asteroid check (sb_ident) and small-body lookups (SBDB)",
        "acknowledgement": (
            "NASA/JPL Solar System Dynamics. Data retrieved on (Year, Month, Date). "
            "(Title of the Page). https://ssd.jpl.nasa.gov"),
        "licence": "U.S. Government work; citation requested",
        "url": "https://ssd.jpl.nasa.gov/",
        "wording_source": "https://ssd.jpl.nasa.gov/about/",
        "wording_verified": True,
    },
    {
        "id": "cadc_ssois",
        "name": "CADC Solar System Object Image Search (SSOIS)",
        "used_for": "Independent asteroid ephemeris check and image search links",
        "acknowledgement": (
            "Asteroid image search by the Solar System Object Image Search (SSOIS) of "
            "the Canadian Astronomy Data Centre (Gwyn, Hill & Kavelaars 2012)."),
        "citation": "Gwyn, Hill & Kavelaars 2012, PASP, 124, 579 (2012PASP..124..579G), "
                    "requested on the SSOIS page",
        "licence": "CADC terms of use",
        "url": "https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/en/ssois/",
        "wording_source": "https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/en/ssois/ "
                          "(asks for the Gwyn et al. 2012 citation; CADC publishes no "
                          "acknowledgement sentence, checked 2026-11-13)",
        "wording_verified": True,
    },
    {
        "id": "neossat",
        "name": "Canadian Space Agency NEOSSat (via CADC)",
        "used_for": "Follow-up frame search for candidates",
        "acknowledgement": (
            "Contains information licensed under the Open Government Licence – "
            "Canada. NEOSSat data courtesy of the Canadian Space Agency, obtained "
            "through the Canadian Astronomy Data Centre."),
        "licence": "Open Government Licence – Canada",
        "licence_url": "https://open.canada.ca/en/open-government-licence-canada",
        "url": "https://data.asc-csa.gc.ca/dataset/neossat",
        "wording_source": "https://open.canada.ca/en/open-government-licence-canada "
                          "(first sentence is the OGL default attribution, verbatim; CSA "
                          "states no credit line, so the second sentence is ours; checked "
                          "2026-11-13)",
        "wording_verified": True,
    },
    {
        "id": "vsx",
        "name": "AAVSO International Variable Star Index (VSX), via CDS VizieR (B/vsx)",
        "used_for": "Known variable stars",
        "acknowledgement": (
            "This research made use of the International Variable Star Index (VSX) "
            "database, operated at AAVSO, Cambridge, Massachusetts, USA."),
        "citation": "Watson, Henden & Price 2006, SASS, 25, 47",
        "licence": "AAVSO VSX terms; CDS/VizieR conditions of use",
        "url": "https://www.aavso.org/vsx/",
        "wording_source": "https://www.aavso.org/vsx/ (page did not state wording on the check date)",
        "wording_verified": False,
    },
    {
        "id": "vizier",
        "name": "CDS VizieR",
        "used_for": "Access to the VSX catalogue",
        "acknowledgement": (
            "This research has made use of the VizieR catalogue access tool, CDS, "
            "Strasbourg, France (DOI: 10.26093/cds/vizier). The original description "
            "of the VizieR service was published in 2000, A&AS 143, 23."),
        "licence": "CDS conditions of use (data under the original authors' terms)",
        "url": "https://vizier.cds.unistra.fr/",
        "wording_source": "https://cds.unistra.fr/help/acknowledgement/ (blocked for automated fetch on the check date)",
        "wording_verified": False,
    },
    {
        "id": "wiseview",
        "name": "WiseView / Backyard Worlds: Planet 9",
        "used_for": "Per-detection WiseView flip-book links (byw.tools)",
        "acknowledgement": (
            "This research has made use of WiseView (Caselden et al. 2018), a tool "
            "developed for the Backyard Worlds: Planet 9 citizen-science project "
            "(Kuchner et al. 2017)."),
        "citation": "Caselden et al. 2018, ascl:1806.004 (2018ascl.soft06004C, the ASCL's "
                    "preferred citation); Kuchner et al. 2017, ApJL, 841, L19",
        "licence": "Linked tool; no data redistributed",
        "url": "http://byw.tools/wiseview",
        "wording_source": "https://ascl.net/1806.004 (no acknowledgement sentence published; "
                          "ours. Citation bibcode verified 2026-11-13; byw.tools refused "
                          "the connection)",
        "wording_verified": False,
    },
]


@router.get("/api/credits")
def credits():
    return {"checked": CREDITS_CHECKED,
            "note": "wording_verified=false: check wording_source before quoting.",
            "sources": SOURCES}
