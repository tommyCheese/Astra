FROM python:3.12.7-slim
ENV MPLBACKEND=Agg TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0
RUN groupadd -g 65532 nonroot && useradd -u 65532 -g nonroot -M nonroot \
    && apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /runtime/requirements.lock
RUN pip install --no-cache-dir -r /runtime/requirements.lock
COPY entrypoint.py /runtime/entrypoint
RUN chmod 0555 /runtime/entrypoint && mkdir -p /output && chown 65532:65532 /output
USER 65532:65532
ENTRYPOINT ["python", "/runtime/entrypoint"]
