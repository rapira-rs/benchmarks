#!/usr/bin/env bash
# Prints the counters of one stage snapshot:
#
#   cpu <busy> <total>
#   ena <counter_name> <value>
#   conns <established> <time_wait>
#
# cpu comes from the first line of /proc/stat: busy is user+nice+system+irq+softirq+steal,
# total is busy+idle+iowait, in clock ticks.
# ena is one line per ethtool -S counter of the default-route device whose name contains allowance_exceeded.
# conns counts the TCP sockets on the local port PORT, and only when PORT is given.
#
#   snapshot.sh [PORT]
set -euo pipefail

port=${1:-}

awk '$1 == "cpu" { busy = $2 + $3 + $4 + $7 + $8 + $9; printf "cpu %d %d\n", busy, busy + $5 + $6; exit }' /proc/stat

dev=$(ip -o route show default | awk '{ for (i = 1; i < NF; i++) if ($i == "dev" && dev == "") dev = $(i + 1) } END { print dev }')
if [ -n "$dev" ]; then
  ethtool -S "$dev" | awk -F: '$1 ~ /allowance_exceeded/ { gsub(/[ \t]/, "", $1); gsub(/[ \t]/, "", $2); print "ena", $1, $2 }'
fi

if [ -n "$port" ]; then
  established=$(ss -Htn state established "( sport = :$port )" | wc -l)
  time_wait=$(ss -Htn state time-wait "( sport = :$port )" | wc -l)
  echo "conns $established $time_wait"
fi
