#!/usr/bin/env bash
# Run harian: generate profil baru (nama bersuffix tanggal), uji semuanya terhadap OLX,
# lalu laporkan profil mana yang hari ini lolos tanpa blokir.
#
# Usage: ./daily.sh [region...]        (default: asia)
# Env:   URL, GEN_ARGS, KEEP_DAYS, CSV
set -euo pipefail
cd "$(dirname "$0")"

URL=${URL:-https://www.olx.co.id/mobil-bekas_c198}
GEN_ARGS=${GEN_ARGS:--t udp --dedup-ip}
KEEP_DAYS=${KEEP_DAYS:-7}
CSV=${CSV:-../hasil-ovpn.csv}
CRED_FILE=${PIA_CREDENTIALS:-}
[ -n "$CRED_FILE" ] || for c in ../.pia-credentials .pia-credentials "$HOME/.pia-credentials"; do
  [ -r "$c" ] && { CRED_FILE=$c; break; }
done
CRED_FILE=${CRED_FILE:-$HOME/.pia-credentials}
today=$(date +%Y%m%d)
regions=("$@"); [ ${#regions[@]} -gt 0 ] || regions=(asia)

# docker compose butuh kredensial untuk env OPENVPN_USER/PASSWORD
if [ -z "${PIA_USER:-}" ] || [ -z "${PIA_PASS:-}" ]; then
  [ -r "$CRED_FILE" ] || { echo "kredensial tidak ada: $CRED_FILE" >&2; exit 1; }
  PIA_USER=$(sed -n 1p "$CRED_FILE"); PIA_PASS=$(sed -n 2p "$CRED_FILE")
fi
export PIA_USER PIA_PASS

echo "== 1/4 generate profil $today"
./get-pia-ovpn.sh $GEN_ARGS -s "$today" "${regions[@]}"

mapfile -t today_files < <(cd vpn-profile && ls *-"$today".ovpn 2>/dev/null)
[ ${#today_files[@]} -gt 0 ] || { echo "tidak ada profil bersuffix $today" >&2; exit 1; }
echo "   ${#today_files[@]} profil"

echo "== 2/4 pastikan gluetun jalan"
PROFILE="${today_files[0]}" docker compose up -d >/dev/null
for i in $(seq 40); do
  [ "$(docker inspect gluetun --format '{{.State.Health.Status}}' 2>/dev/null)" = healthy ] && break
  [ "$i" = 40 ] && { echo "gluetun tidak healthy - docker logs gluetun" >&2; exit 1; }
  sleep 2
done

echo "== 3/4 uji ${#today_files[@]} profil terhadap $URL"
URL="$URL" CSV="$CSV" ./sweep-asia.sh "${today_files[@]}"

echo "== 4/4 profil bersih hari ini"
python3 - "$CSV" "$today" <<'PY'
import csv, sys
csv_path, today = sys.argv[1:3]
rows = [r for r in csv.DictReader(open(csv_path)) if f'-{today}.ovpn' in r['profil']]
ok = [r for r in rows if r['diblokir'] == 'no']
for r in ok:
    print(f"  BERSIH  {r['profil']:52} {r['ip_publik']:16} {r['negara']:3} {r['byte_html']}b")
print(f"\n{len(ok)} bersih / {len(rows)} diuji")
if not ok:
    print("tidak ada yang lolos - coba region lain: ./daily.sh europe, atau GEN_ARGS='-t udp' ./daily.sh asia")
PY

# ponytail: buang profil lebih tua dari KEEP_DAYS hari biar direktori tidak menggunung.
find vpn-profile -name '*.ovpn' -type f -mtime +"$KEEP_DAYS" -delete 2>/dev/null || true
