"""Keep the test suite offline.

app.py defaults to the production snapshot bucket; tests that need a store
inject a fake one, so real GCS reads are switched off for everything else.
"""
import os

os.environ["SPHEREX_SNAPSHOT_READS"] = "false"
# No background warm-up threads racing the fake stores tests inject.
os.environ["SPHEREX_SNAPSHOT_WARM"] = "false"
# The dashboard's archive-coverage enricher queries live archives; off in tests.
os.environ.setdefault("SPHEREX_COVERAGE_ENRICH", "false")
