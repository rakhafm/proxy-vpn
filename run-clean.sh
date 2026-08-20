#!/usr/bin/env bash
# Jalankan gluetun secara persisten memakai server yang terbukti lolos di hasil sweep
# terakhir (hasil-pia.csv / hasil-proton.csv), bukan menyapu ulang.
#
# Usage: ./run-clean.sh <proton|pia> [--pool N] [csv]
#   --pool N   pakai N server terbersih terakhir sebagai daftar kandidat (default 1 = pin
#              ke satu server saja). Dengan N>1, gluetun sendiri yang memilih di antaranya
#              dan akan pindah ke kandidat lain kalau salah satu gagal connect - tapi TIDAK
#              tahu soal blokir Akamai (lihat catatan di README).
# Env:   PROXY_PORT (default 8888)
set -euo pipefail
cd "$(dirname "$0")"
prov=${1:-}; shift || true
pool=1
if [ "${1:-}" = --pool ]; then pool=$2; shift 2; fi
csv=${1:-hasil-$prov.csv}

case "$prov" in
  proton) export GLUETUN_PROVIDER=protonvpn GLUETUN_VPN_TYPE=wireguard ;;
  pia)    export GLUETUN_PROVIDER='private internet access' GLUETUN_VPN_TYPE=openvpn
          cred=${PIA_CREDENTIALS:-}
          [ -n "$cred" ] || for c in .pia-credentials "$HOME/.pia-credentials"; do [ -r "$c" ] && { cred=$c; break; }; done
          [ -r "${cred:-}" ] || { echo "kredensial PIA tidak ada: isi .pia-credentials" >&2; exit 1; }
          export PIA_USER=$(sed -n 1p "$cred") PIA_PASS=$(sed -n 2p "$cred") ;;
  *) echo "usage: $0 <proton|pia> [--pool N] [csv]" >&2; exit 1 ;;
esac
[ -r "$csv" ] || { echo "tidak ada $csv - jalankan ./sweep.sh $prov dulu" >&2; exit 1; }

picks=$(python3 -c '
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1])))
clean = [r["profil"] for r in rows if r["diblokir"] == "no"]
seen, out = set(), []
for p in reversed(clean):          # terbaru dulu
    if p not in seen:
        seen.add(p); out.append(p)
    if len(out) == int(sys.argv[2]):
        break
if not out:
    sys.exit("tidak ada baris bersih (diblokir=no) di " + sys.argv[1])
print(",".join(out))
' "$csv" "$pool")

echo "kandidat ($prov, pool=$pool): $picks"
if [ "$prov" = proton ]; then export SERVER_HOSTNAMES="$picks" SERVER_NAMES=
else                          export SERVER_NAMES="$picks" SERVER_HOSTNAMES=; fi

docker compose -f docker-compose.gluetun.yml up -d --force-recreate
for i in $(seq 40); do
  [ "$(docker inspect gluetun-native --format '{{.State.Health.Status}}' 2>/dev/null)" = healthy ] && break
  [ "$i" = 40 ] && { echo "gluetun tidak healthy - docker logs gluetun-native" >&2; exit 1; }
  sleep 2
done
pub=$(curl -sS --max-time 20 -x "http://127.0.0.1:${PROXY_PORT:-8888}" https://ipinfo.io/json 2>/dev/null | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("ip","?"),d.get("country","?"))')
echo "gluetun-native jalan, exit IP: $pub"
