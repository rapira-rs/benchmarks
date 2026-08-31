#!/bin/bash
set -u

outer=180
shutdown -h "+$outer" "rapira-bench outer TTL"
date -u -d "+$outer minutes" +%s > /etc/rapira-bench-deadline

cat > /usr/local/sbin/rapira-bench-ttl-arm <<'ARM'
#!/bin/bash
set -u
if [ $# -ge 1 ]; then
  date -u -d "+$1 minutes" +%s > /etc/rapira-bench-deadline
fi
deadline=$(cat /etc/rapira-bench-deadline 2>/dev/null) || exit 0
now=$(date -u +%s)
mins=$(( (deadline - now + 59) / 60 ))
if [ "$mins" -lt 2 ]; then mins=2; fi
shutdown -c 2>/dev/null || true
shutdown -h "+$mins" "rapira-bench TTL"
ARM
chmod 0755 /usr/local/sbin/rapira-bench-ttl-arm

cat > /etc/systemd/system/rapira-bench-ttl.service <<'UNIT'
[Unit]
Description=Re-arm the rapira-bench TTL after boot

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/rapira-bench-ttl-arm

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable rapira-bench-ttl.service
