#!/usr/bin/env bash
# Sapu profil Asia: tukar profil yang di-mount, restart gluetun, probe URL, tulis CSV.
# Tidak butuh kredensial - container memakai ulang env yang sudah ada di dalamnya.
set -uo pipefail
cd "$(dirname "$0")"   # skrip ini hidup di legacy-ovpn/, root repo ada di ..
URL=${URL:-https://www.olx.co.id/mobil-bekas_c198}
CSV=${CSV:-../hasil-ovpn.csv}
OUTDIR=${OUTDIR:-../probe-out}
PROXY=${PROXY:-http://127.0.0.1:8888}
export NODE_PATH="$(dirname "$(ls -d ~/.npm/_npx/*/node_modules/playwright | head -1)")"

# file yang sedang di-mount ke /gluetun/custom.conf; isinya kita tukar per iterasi
MOUNT=$(docker inspect gluetun --format '{{range .Mounts}}{{.Source}}{{end}}')
[ -f "$MOUNT" ] || { echo "gluetun tidak jalan / mount tidak ketemu" >&2; exit 1; }
BACKUP="$MOUNT.bak"
[ -f "$BACKUP" ] || cp "$MOUNT" "$BACKUP"
trap 'cp "$BACKUP" "$MOUNT"; docker restart gluetun >/dev/null 2>&1; echo "profil awal dipulihkan"' EXIT

mkdir -p "$OUTDIR"
[ -f "$CSV" ] || echo "waktu,profil,server_ip,ip_publik,negara,org,http_status,diblokir,alasan,nomor_referensi,byte_html,file_html,file_png" > "$CSV"

for p in "$@"; do
  src="vpn-profile/$p"
  [ -f "$src" ] || { echo "lewati (tidak ada): $p"; continue; }
  server_ip=$(awk '/^remote /{print $2; exit}' "$src")
  cp "$src" "$MOUNT"                      # inode tetap, jadi bind mount tidak putus
  docker restart gluetun >/dev/null

  ok=
  for i in $(seq 40); do
    [ "$(docker inspect gluetun --format '{{.State.Health.Status}}' 2>/dev/null)" = healthy ] && { ok=1; break; }
    sleep 2
  done
  ts=$(date -u +%FT%TZ)
  if [ -z "$ok" ]; then
    echo "$ts,$p,$server_ip,,,,,yes,vpn-tidak-connect,,0,," >> "$CSV"
    echo "$p -> VPN tidak connect"; continue
  fi

  ipjson=$(curl -sS --max-time 20 -x "$PROXY" https://ipinfo.io/json 2>/dev/null || echo '{}')
  read -r pub country org <<<"$(python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("ip","-"),d.get("country","-"),(d.get("org","") or "-").replace(",", " "))' <<<"$ipjson")"

  base="$OUTDIR/${p%.ovpn}"
  res=$(node ../probe.js "$URL" "$base" "$PROXY" 2>/dev/null | tail -1)
  python3 - "$CSV" "$ts" "$p" "$server_ip" "$pub" "$country" "$org" "$base" "$res" <<'PY'
import csv, json, sys
csv_path, ts, prof, server_ip, pub, country, org, base, res = sys.argv[1:10]
try: r = json.loads(res)
except Exception: r = {'status':'','blocked':'error','reason':'probe gagal','reference':'','bytes':0}
with open(csv_path, 'a', newline='') as f:
    csv.writer(f).writerow([ts, prof, server_ip, pub, country, org, r['status'], r['blocked'],
                            r['reason'], r['reference'], r['bytes'], base+'.html', base+'.png'])
print(f"{prof} -> {pub} {country} : {r['blocked']} {r['reason'][:40]} {r['bytes']}b")
PY
done
