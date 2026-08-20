#!/usr/bin/env bash
# Sapu server lewat provider bawaan gluetun: recreate container per server, probe URL,
# tulis satu baris CSV. Tidak ada file config yang di-download.
#
# Usage: ./sweep.sh <proton|pia> <server>...
#        ./servers.sh pia sea | xargs ./sweep.sh pia
#        ./servers.sh proton sea | xargs ./sweep.sh proton
# Env:   URL, CSV, OUTDIR, PROXY_PORT
set -uo pipefail
cd "$(dirname "$0")"
URL=${URL:-https://www.olx.co.id/mobil-bekas_c198}
OUTDIR=${OUTDIR:-probe-out}
PROXY_PORT=${PROXY_PORT:-8888}
PROXY="http://127.0.0.1:$PROXY_PORT"
COMPOSE="docker compose -f docker-compose.gluetun.yml"
export NODE_PATH="$(dirname "$(ls -d ~/.npm/_npx/*/node_modules/playwright | head -1)")"

prov=${1:-}; shift || true
case "$prov" in
  proton)
    export GLUETUN_PROVIDER=protonvpn GLUETUN_VPN_TYPE=wireguard
    grep -q '^PROTON_KEY=.' .env 2>/dev/null || { echo "PROTON_KEY belum diisi di .env - lihat README" >&2; exit 1; }
    ;;
  pia)
    export GLUETUN_PROVIDER='private internet access' GLUETUN_VPN_TYPE=openvpn
    # kredensial bisa di direktori repo atau di $HOME
    cred=${PIA_CREDENTIALS:-}
    [ -n "$cred" ] || for c in .pia-credentials "$HOME/.pia-credentials"; do [ -r "$c" ] && { cred=$c; break; }; done
    if [ -z "${PIA_USER:-}" ] || [ -z "${PIA_PASS:-}" ]; then
      [ -r "${cred:-}" ] || { echo "kredensial PIA tidak ada: isi .pia-credentials (baris 1 user, baris 2 pass)" >&2; exit 1; }
      PIA_USER=$(sed -n 1p "$cred"); PIA_PASS=$(sed -n 2p "$cred")
    fi
    export PIA_USER PIA_PASS
    ;;
  *) echo "usage: $0 <proton|pia> <server>..." >&2; exit 1 ;;
esac
CSV=${CSV:-hasil-$prov.csv}
[ $# -gt 0 ] || { echo "sebutkan minimal satu server (lihat: ./servers.sh $prov sea)" >&2; exit 1; }

trap 'docker rm -f gluetun-native >/dev/null 2>&1; echo "gluetun-native dimatikan"' EXIT

mkdir -p "$OUTDIR"
[ -f "$CSV" ] || echo "waktu,profil,server_ip,ip_publik,negara,org,http_status,diblokir,alasan,nomor_referensi,byte_html,file_html,file_png" > "$CSV"

for s in "$@"; do
  # proton dipilih lewat hostname, pia lewat nama server - yang tidak dipakai dibiarkan kosong
  if [ "$prov" = proton ]; then export SERVER_HOSTNAMES="$s" SERVER_NAMES=
  else                          export SERVER_NAMES="$s" SERVER_HOSTNAMES=; fi
  PROXY_PORT="$PROXY_PORT" $COMPOSE up -d --force-recreate >/dev/null 2>&1

  ok=
  for i in $(seq 40); do
    [ "$(docker inspect gluetun-native --format '{{.State.Health.Status}}' 2>/dev/null)" = healthy ] && { ok=1; break; }
    sleep 2
  done
  ts=$(date -u +%FT%TZ)
  if [ -z "$ok" ]; then
    echo "$ts,$s,,,,,,yes,vpn-tidak-connect,,0,," >> "$CSV"
    echo "$s -> VPN tidak connect"; continue
  fi

  ipjson=$(curl -sS --max-time 20 -x "$PROXY" https://ipinfo.io/json 2>/dev/null || echo '{}')
  read -r pub country org <<<"$(python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("ip","-"),d.get("country","-"),(d.get("org","") or "-").replace(",", " "))' <<<"$ipjson")"

  base="$OUTDIR/$prov-${s//[^A-Za-z0-9_-]/-}"
  res=$(node probe.js "$URL" "$base" "$PROXY" 2>/dev/null | tail -1)
  python3 - "$CSV" "$ts" "$s" "$pub" "$country" "$org" "$base" "$res" <<'PY'
import csv, json, sys
csv_path, ts, srv, pub, country, org, base, res = sys.argv[1:9]
try: r = json.loads(res)
except Exception: r = {'status':'','blocked':'error','reason':'probe gagal','reference':'','bytes':0}
with open(csv_path, 'a', newline='') as f:
    csv.writer(f).writerow([ts, srv, '', pub, country, org, r['status'], r['blocked'],
                            r['reason'], r['reference'], r['bytes'], base+'.html', base+'.png'])
print(f"{srv} -> {pub} {country} : {r['blocked']} {r['reason'][:40]} {r['bytes']}b")
PY
done
