#!/usr/bin/env bash
# Screenshot halaman lewat proxy gluetun, pakai Chrome asli (bukan chromium headless bundling).
# Usage: ./shot.sh <url> [output.png]
set -euo pipefail
URL=${1:?usage: ./shot.sh <url> [output.png]}
OUT=${2:-shot.png}
PROXY=${PROXY:-http://127.0.0.1:8888}
# ponytail: UA wajib di-override. Default headless Chrome mengirim "HeadlessChrome/..." dan
# Akamai me-reset koneksi (ERR_HTTP2_PROTOCOL_ERROR) sebelum halaman sempat dimuat.
UA=${UA:-'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'}

npx playwright screenshot \
  --channel=chrome \
  --user-agent="$UA" \
  --proxy-server="$PROXY" \
  --viewport-size=1440,900 \
  --full-page \
  --wait-for-timeout=10000 \
  --timeout=120000 \
  "$URL" "$OUT"
