#!/bin/bash
# anton-pulse installer: sudoers helper, token wrapper, systemd timer, CLI.
set -euo pipefail

PULSE_DIR="$(cd "$(dirname "$0")" && pwd)"
PULSE_DIR="$(readlink -f "$PULSE_DIR")"

echo ">> installing from $PULSE_DIR"

# 1. Root-owned cache-drop helper (probe uses it to get honest disk reads)
sudo tee /usr/local/sbin/anton-pulse-drop-caches >/dev/null <<'EOF'
#!/bin/sh
sync
echo 3 > /proc/sys/vm/drop_caches
EOF
sudo chown root:root /usr/local/sbin/anton-pulse-drop-caches
sudo chmod 755 /usr/local/sbin/anton-pulse-drop-caches

# 2. Passwordless sudo for that one command only
if [ ! -f /etc/sudoers.d/anton-pulse ]; then
  echo "ak ALL=(root) NOPASSWD: /usr/local/sbin/anton-pulse-drop-caches" \
    | sudo tee /etc/sudoers.d/anton-pulse >/dev/null
  sudo chmod 440 /etc/sudoers.d/anton-pulse
fi

# 3. Wrapper: reads the Oracle token from hermes .env so no secret is copied
cat > "$PULSE_DIR/pulse-run.sh" <<EOF
#!/bin/bash
cd "$PULSE_DIR"
TOKEN=\$(grep -m1 '^HERMES_ORACLE_TOKEN=' /opt/anton/hermes/.env | cut -d= -f2-)
export ANTON_PULSE_ORACLE_TOKEN="\$TOKEN"
export ANTON_PULSE_REPORT_DIR="$PULSE_DIR"
exec python3 "$PULSE_DIR/pulse.py" "\$@"
EOF
chmod 755 "$PULSE_DIR/pulse-run.sh"

# 4. systemd service + timer (every 30 min, after the HDD is mounted)
sudo tee /etc/systemd/system/anton-pulse.service >/dev/null <<EOF
[Unit]
Description=Anton Pulse — stack performance probe
RequiresMountsFor=$PULSE_DIR
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=ak
Group=ak
ExecStart=$PULSE_DIR/pulse-run.sh
EOF

sudo tee /etc/systemd/system/anton-pulse.timer >/dev/null <<EOF
[Unit]
Description=Run Anton Pulse every 30 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=30min
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now anton-pulse.timer

# 4b. Exporter service (Prometheus metrics + Hermes events)
sudo tee /etc/systemd/system/anton-pulse-exporter.service >/dev/null <<EOF
[Unit]
Description=Anton Pulse exporter — Prometheus metrics + Hermes events
RequiresMountsFor=$PULSE_DIR
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=ak
Group=ak
ExecStart=$PULSE_DIR/pulse-run.sh --exporter
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now anton-pulse-exporter.service

# 5. CLI
sudo ln -sf "$PULSE_DIR/pulse.py" /usr/local/bin/anton-pulse

echo ">> installed:"
echo "   timer        anton-pulse.timer (next run in 30 min)"
echo "   exporter     anton-pulse-exporter.service (metrics :9654 + Hermes events)"
echo "   helper       /usr/local/sbin/anton-pulse-drop-caches (NOPASSWD)"
echo "   wrapper      $PULSE_DIR/pulse-run.sh"
echo "   cli          anton-pulse (aliases pulse.py)"
