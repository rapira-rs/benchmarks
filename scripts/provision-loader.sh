#!/usr/bin/env bash
set -euo pipefail

WRK_TAG=${WRK_TAG:-4.2.0}
K6_VERSION=${K6_VERSION:-2.2.0}

echo "==> packages"
sudo dnf -y install gcc make git openssl-devel zlib-devel ethtool curl tar

if ! command -v wrk >/dev/null; then
  echo "==> build wrk $WRK_TAG"
  rm -rf "$HOME/wrk-src"
  git clone -q --depth 1 --branch "$WRK_TAG" https://github.com/wg/wrk "$HOME/wrk-src"
  make -s -C "$HOME/wrk-src" -j"$(nproc)" WITH_OPENSSL=/usr
  sudo install -m 0755 "$HOME/wrk-src/wrk" /usr/local/bin/wrk
fi

if ! command -v k6 >/dev/null; then
  echo "==> k6 $K6_VERSION"
  sudo dnf -y install "https://github.com/grafana/k6/releases/download/v$K6_VERSION/k6-v$K6_VERSION-linux-amd64.rpm"
fi

echo "==> system knobs"
sudo tee /etc/sysctl.d/90-rapira-bench.conf >/dev/null <<'EOF'
net.ipv4.ip_local_port_range = 1024 65000
EOF
sudo sysctl -q -p /etc/sysctl.d/90-rapira-bench.conf
sudo tee /etc/security/limits.d/90-rapira-bench.conf >/dev/null <<'EOF'
fedora soft nofile 1048576
fedora hard nofile 1048576
EOF

echo "==> loader provisioned: wrk $(wrk --version 2>&1 | head -1 || true), $(k6 version | head -1)"
