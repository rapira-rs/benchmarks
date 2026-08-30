#!/usr/bin/env bash
# Print the ENA allowance counters, one "name value" per line. A moved counter
# means AWS throttled the flow and the run measured the network, not the server.
# https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/monitoring-network-performance-ena.html
set -euo pipefail

# The interface name varies (ens5 on Fedora cloud images); take it from the
# default route. Only the exceeded counters: *_allowance_available is a
# headroom gauge that moves on every run and would false-flag cells.
dev=$(ip -o -4 route show default | awk '{print $5; exit}')
ethtool -S "$dev" | awk '/allowance_exceeded/ { gsub(":", "", $1); print $1, $2 }'
