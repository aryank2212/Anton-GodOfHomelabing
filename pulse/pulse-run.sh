#!/bin/bash
cd "/media/ak/Drive/anton-pulse"
TOKEN=$(grep -m1 '^HERMES_ORACLE_TOKEN=' /opt/anton/hermes/.env | cut -d= -f2-)
export ANTON_PULSE_ORACLE_TOKEN="$TOKEN"
export ANTON_PULSE_REPORT_DIR="/media/ak/Drive/anton-pulse"
exec python3 "/media/ak/Drive/anton-pulse/pulse.py" "$@"
