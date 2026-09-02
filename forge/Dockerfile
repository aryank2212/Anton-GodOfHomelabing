# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    FORGE_CONFIG_FILE=/app/forge.yaml \
    FORGE_STATE_FILE=/data/forge-state.json \
    FORGE_AUDIT_FILE=/data/forge-audit.jsonl
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
RUN useradd --create-home --uid 10001 forge \
    && mkdir -p /data \
    && chown -R forge:forge /app /data
USER forge
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
