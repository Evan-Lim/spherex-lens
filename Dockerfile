# SPHEREx Discovery Portal -- container image for the full FastAPI app.
#
#   docker build -t spherex-lens .
#   docker run -p 8000:8000 spherex-lens
#
# The cached FITS cutouts (~21 MB per target) are baked into the image so the
# container is self-contained and needs no network at runtime.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SPHEREX_CACHE=data_cache \
    PORT=8000

WORKDIR /app

# All heavy dependencies (numpy, scipy, astropy, reproject) ship manylinux
# wheels, so no compiler toolchain is needed.
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY app.py step1_ingest.py step2_coadd.py step3_zogy.py step4_triage.py \
     step5_spectrum.py step6_injection.py make_demo_data.py make_figures.py \
     export_static.py ./
COPY static ./static
COPY data_cache ./data_cache
COPY data_cache_demo ./data_cache_demo
COPY figures ./figures
COPY figures_real ./figures_real

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/targets', timeout=4).status == 200 else 1)"

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
