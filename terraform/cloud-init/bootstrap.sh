#!/bin/bash
# Cost failsafe only; provisioning runs later over ssh and narrows the TTL.
# Arms a fixed 180 minute outer bound first, then installs a unit that
# re-arms the deadline after a reboot (a scheduled shutdown lives in /run).
# The file must stay static: a user_data change stops and starts the instances.
set -u

outer=180
shutdown -h "+$outer" "rapira-bench outer TTL"
date -u -d "+$outer minutes" +%s > /etc/rapira-bench-deadline

cat > /usr/local/sbin/rapira-bench-ttl-arm <<'ARM'
#!/bin/bash
# Owns the deadline protocol. With a minutes argument, set a new deadline
# first; bare, re-arm from the stored one (the reboot unit calls it bare).
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
