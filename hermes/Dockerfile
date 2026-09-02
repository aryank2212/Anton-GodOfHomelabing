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

# Run as an unprivileged user.
RUN useradd --create-home --uid 1000 hermes

# Docker CLI so `docker_restart` remediations and the bot's /cmd can manage
# containers through the host socket mounted at runtime (docker-compose.yml).
# Debian splits the client into its own package; install both just in case.
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker-cli procps \
    || apt-get install -y --no-install-recommends docker.io procps \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=hermes:hermes app ./app
COPY --chown=hermes:hermes .env.example ./

RUN mkdir -p /data && chown -R hermes:hermes /data /app

USER hermes
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
