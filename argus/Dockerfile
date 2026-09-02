# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# --------------------------------------------------------------------------
# Builder: install runtime dependencies into a dedicated virtualenv
# --------------------------------------------------------------------------
FROM base AS builder

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --------------------------------------------------------------------------
# Runtime
# --------------------------------------------------------------------------
FROM base AS runtime

COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# Certificates + tzdata so collectors can talk to the internet over TLS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN mkdir -p /data && chown -R root:root /data

ENV ARGUS_CONFIG_DIR=app/config
ENV ARGUS_DATABASE_URL=sqlite+aiosqlite:////data/argus.db

EXPOSE 8012

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8012/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8012"]
