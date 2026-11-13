# SPHEREx Discovery Portal -- container image for running the API locally.
#
# There is no local data cache: the app reads the published Cloud snapshot,
# which the Cloud Run job refreshes from IRSA every 12 hours.  The container
# therefore needs the bucket name and Google credentials:
#
#   docker build -t spherex-lens .
#   docker run -p 8000:8000 ^
#     -e SPHEREX_SNAPSHOT_BUCKET=spherex-lens-spherex-snapshots ^
#     -e GOOGLE_CLOUD_PROJECT=spherex-lens ^
#     -e GOOGLE_APPLICATION_CREDENTIALS=/gcp/adc.json ^
#     -v "%APPDATA%\gcloud\application_default_credentials.json:/gcp/adc.json:ro" ^
#     spherex-lens
#
# The production image is Dockerfile.cloud (built by cloud/cloudbuild.yaml).

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SPHEREX_CACHE=/tmp/spherex-data \
    PORT=8000

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY app.py step1_ingest.py step2_coadd.py step3_zogy.py step4_triage.py \
     step5_spectrum.py step6_injection.py step7_neowise.py live_ingest.py \
     catalog_tools.py snapshot_store.py cloud_refresh.py config.py version.py ./
COPY spherexlens ./spherexlens
COPY ml ./ml
COPY eval ./eval
COPY static ./static

# Security review S14: run as an unprivileged user (writes go to /tmp only).
RUN useradd --create-home --uid 10001 spherex
USER spherex

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/targets', timeout=4).status == 200 else 1)"

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
