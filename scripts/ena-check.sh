#!/usr/bin/env bash
set -euo pipefail

dev=$(ip -o -4 route show default | awk '{print $5; exit}')
ethtool -S "$dev" | awk '/allowance_exceeded/ { gsub(":", "", $1); print $1, $2 }'
