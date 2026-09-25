#!/usr/bin/env bash
# probe.sh URL EXPECT_FILE PROTO [METHOD] [BODY_FILE] [HEADER...]
# Fetches URL once and compares the body with EXPECT_FILE byte for byte. PROTO is http1 or grpc.
# BODY_FILE - sends no body. A HEADER is "name: value". A relative file is under the staged rig.
# Exits 0 on a match. Exits 1 on an HTTP error status or on a mismatch. A mismatch prints the first differing byte on stderr.
set -euo pipefail
# shellcheck source=box/lib.sh
. "$(dirname "$0")/lib.sh"

url=${1:?url}
expect=$(rig_path "${2:?expect file}")
proto=${3:?http1|grpc}
shift 3
method=GET
body=-
if [ $# -gt 0 ]; then
  method=$1
  shift
fi
if [ $# -gt 0 ]; then
  body=$1
  shift
fi

opts=(-s -f -m 5)
case "$proto" in
http1) opts+=(--http1.1 -X "$method") ;;
grpc) opts+=(--http2-prior-knowledge -X POST -H 'content-type: application/grpc' -H 'te: trailers' -H 'grpc-accept-encoding: identity') ;;
*) die "unknown proto $proto" ;;
esac
if [ "$body" != - ]; then
  opts+=(--data-binary "@$(rig_path "$body")")
fi
for header in "$@"; do
  opts+=(-H "$header")
done

got=$(mktemp)
trap 'rm -f "$got"' EXIT
curl "${opts[@]}" -o "$got" "$url" || die "curl failed for $url"
cmp "$expect" "$got" >&2 || exit 1
