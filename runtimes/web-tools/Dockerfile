FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY runtimes/web-tools/requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --requirement /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock \
    && rm -rf /root/.cache

RUN mkdir -p /opt/astra/app/tools /opt/astra/app/grounding /opt/astra/bin
COPY backend/app/__init__.py /opt/astra/app/__init__.py
COPY backend/app/tools/__init__.py /opt/astra/app/tools/__init__.py
COPY backend/app/tools/base.py /opt/astra/app/tools/base.py
COPY backend/app/tools/web.py /opt/astra/app/tools/web.py
COPY backend/app/grounding/__init__.py /opt/astra/app/grounding/__init__.py
COPY backend/app/grounding/schemas.py /opt/astra/app/grounding/schemas.py
COPY backend/app/grounding/identity.py /opt/astra/app/grounding/identity.py
COPY backend/app/grounding/fragments.py /opt/astra/app/grounding/fragments.py
COPY backend/app/grounding/ledger.py /opt/astra/app/grounding/ledger.py

COPY runtimes/web-tools/tool_runtime.py /opt/astra/bin/tool-runtime
RUN chmod 0555 /opt/astra/bin/tool-runtime \
    && mkdir -p /input /output \
    && chown -R 65532:65532 /input /output /opt/astra

USER 65532:65532
ENV PYTHONPATH=/opt/astra
WORKDIR /opt/astra
CMD ["sleep", "infinity"]
