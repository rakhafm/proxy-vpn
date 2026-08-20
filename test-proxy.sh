#!/usr/bin/env bash
# Cek proxy gluetun benar keluar lewat VPN, lalu ambil halaman OLX.
# Jalankan setelah: docker compose up -d
set -euo pipefail
PROXY=${PROXY:-http://127.0.0.1:8888}
URL=${1:-https://www.olx.co.id/mobil-bekas_c198}

show_ip() {  # $1 = label, $2... = argumen curl tambahan
  local label=$1; shift
  local j; j=$(curl -sS --max-time 20 "$@" https://ipinfo.io/json || true)
  case "$j" in
    '{'*) python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("ip"),d.get("country"),d.get("org"))' <<<"$j" | sed "s/^/$label: /" ;;
    *)    echo "$label: GAGAL - tidak ada respons JSON" >&2
          echo "  cek: docker ps --filter name=gluetun / docker logs gluetun" >&2
          return 1 ;;
  esac
}

# tunggu VPN benar-benar naik; handshake OpenVPN butuh ~15 detik
for i in $(seq 30); do
  [ "$(docker inspect gluetun --format '{{.State.Health.Status}}' 2>/dev/null)" = healthy ] && break
  [ "$i" = 30 ] && { echo "gluetun tidak kunjung healthy - docker logs gluetun" >&2; exit 1; }
  sleep 2
done

show_ip "langsung"
show_ip "via proxy" -x "$PROXY"

echo "== $URL"
# ponytail: sengaja TIDAK menyamar jadi Chrome - UA Chrome + fingerprint TLS curl bikin
# Akamai me-reset stream (curl error 92), jadi hasilnya malah kosong.
code=$(curl -sS -x "$PROXY" -o page.html -w '%{http_code}' --max-time 60 "$URL")
echo "HTTP $code, $(wc -c < page.html | tr -d ' ') byte -> page.html"
if grep -qiE 'cf-browser-verification|Just a moment|Attention Required' page.html; then
  echo "!! kena Cloudflare challenge"
elif grep -qiE 'bm-verify|_sec/verify|akam' page.html; then
  echo "!! kena Akamai Bot Manager (halaman interstitial, bukan konten asli)"
fi
