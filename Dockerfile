# Public tennis API (services.api.app_tennis) — same gunicorn + uvicorn setup as deploy/racket-edge-api.service.
#
#   docker build -t racketedge-api .
#   docker run --rm -p 8001:8001 --env-file env/prod.env -v /path/to/models:/app/models:ro racketedge-api
#
# Configuration comes from the environment (DB_*, FRONTEND_ORIGINS, RAPIDAPI_PROXY_SECRET ...);
# no env file or model is baked into the image. The prediction model is mounted at runtime.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=prod \
    MATCH_WINNER_MODEL_PATH=/app/models/RacketEdgeModelV1.joblib

# libgomp1: OpenMP runtime needed by LightGBM
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY config.py ./
COPY libs/ libs/
COPY services/ services/

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=4)"

CMD ["gunicorn", "services.api.app_tennis:app", \
     "--workers", "2", "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8001", "--timeout", "30", "--graceful-timeout", "10", \
     "--access-logfile", "-", "--error-logfile", "-", "--log-level", "info"]
