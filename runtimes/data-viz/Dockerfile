FROM ghcr.io/astral-sh/uv:0.8.2 AS uv
FROM python:3.12.11-slim-bookworm

ENV MPLBACKEND=Agg TZ=UTC PYTHONHASHSEED=0 PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk nodejs npm ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /opt/astra/runtime
COPY pyproject.toml uv.lock package.json package-lock.json ./
RUN uv sync --frozen --no-dev && npm ci --ignore-scripts && npx playwright install --with-deps chromium && rm -rf /root/.cache /var/lib/apt/lists/*
ENV MPLCONFIGDIR=/tmp/matplotlib HOME=/tmp
COPY python.py echarts.mjs render ./
RUN mkdir -p /opt/astra/bin /input /output && install -m 0555 render /opt/astra/bin/render && chown -R 65532:65532 /opt/astra /input /output
USER 65532:65532
CMD ["sleep", "infinity"]
