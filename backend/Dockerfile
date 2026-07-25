FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates docker.io \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/astra/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN python -m pip install --no-cache-dir .

COPY backend/alembic.ini ./
COPY backend/alembic ./alembic

RUN mkdir -p /var/lib/astra/artifacts /var/lib/astra/workspaces \
    && chmod 0700 /var/lib/astra/artifacts /var/lib/astra/workspaces

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
