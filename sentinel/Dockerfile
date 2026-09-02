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

# Tools the observers need:
#   iproute2  - `ip neigh` (network observer), /proc/net/route (router)
#   iputils-ping - ICMP gateway checks (router observer)
#   nut-client - `upsc` (UPS observer)
#   procps    - ps/uptime helpers used by some observers
# Runs as root because observers read /proc, ping the gateway and reach the
# docker socket; the host network mode is provided by docker-compose.yml.
RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 iputils-ping nut-client procps \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN mkdir -p /data && chown -R root:root /data

ENV SENTINEL_CONFIG_DIR=app/config
ENV SENTINEL_DATABASE_URL=sqlite+aiosqlite:////data/sentinel.db

EXPOSE 8011

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8011/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8011"]
