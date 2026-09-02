#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec uvicorn app.main:app --host "${ORACLE_HOST:-0.0.0.0}" --port "${ORACLE_PORT:-8003}"
