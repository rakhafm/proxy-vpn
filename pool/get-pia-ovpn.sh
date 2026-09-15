#!/usr/bin/env bash
# Download profil OpenVPN dari PIA config generator. Login otomatis pakai kredensial akun.
#
# Kredensial: isi ~/.pia-credentials, dua baris (username lalu password), chmod 600.
#   printf '%s\n%s\n' 'pXXXXXXX' 'passwordnya' > ~/.pia-credentials && chmod 600 ~/.pia-credentials
# Atau lewat env: PIA_USER / PIA_PASS.
#
# Skrip ini tinggal di pool/ supaya ikut ter-scp ke VM oleh pool/deploy/deploy.sh
# (yang cuma menyalin "pool servers.sh") - tanpa itu slot pia-custom di produksi
# tidak pernah bisa generate profil. legacy-ovpn/daily.sh memanggilnya dari sini
# juga; INI SATU-SATUNYA implementasi login+scrape PIA, jangan disalin balik.
#
# Usage: ./pool/get-pia-ovpn.sh [-l] [-t TYPE] [-v VER] [-p PLATFORM] [--no-ip] [--dedup-ip] [-s SUFFIX] REGION...
#   -l          tampilkan daftar kode region (kolom: benua, kode) lalu keluar
#   -t TYPE     aes-128-cbc-udp (default) | aes-256-cbc-udp | aes-128-gcm-udp | aes-256-gcm-udp
#               aes-128-cbc-tcp | aes-256-cbc-tcp | aes-128-gcm-tcp | aes-256-gcm-tcp
#               udp = keempat tipe UDP; tcp = keempat tipe TCP; all = seluruh
#               delapan tipe UDP+TCP sekaligus.
#   -v VER      2.4 (default) | 2.3
#   -p PLATFORM desktop (default) | mobile
#   --no-ip     pakai hostname server; default pakai IP (Use IP dicentang)
#   --dedup-ip  dalam satu region, kalau beberapa tipe menghasilkan IP yang sama, simpan satu saja.
#               Catatan: tipe yang dibuang bisa beda port/cipher, jadi bukan pengganti yang setara.
#   -s SUFFIX   kode unik di akhir nama file, default tanggal hari ini (YYYYMMDD). "-s ''" = tanpa
#               suffix. Berguna untuk run harian: profil lama tidak tertimpa, jadi bisa dibandingkan.
#   REGION      kode region, nama benua (asia|europe|north-america|south-america|oceania|africa),
#               atau "all" untuk semua region
# Contoh: ./pool/get-pia-ovpn.sh sg japan jakarta
#         ./pool/get-pia-ovpn.sh -t udp asia     # semua region Asia x semua tipe UDP
#         ./pool/get-pia-ovpn.sh -t all --dedup-ip jakarta  # 8 tipe, satu per IP remote
set -euo pipefail
cd "$(dirname "$0")"   # skrip ini hidup di pool/, root repo ada di ..

SITE=https://www.privateinternetaccess.com
GEN=$SITE/account/ovpn-config-generator
OUT="${PIA_OUT:-vpn-profile}"   # relatif ke pool/ -> default = pool/vpn-profile
CRED_FILE="${PIA_CREDENTIALS:-}"
# ".." = root repo di mesin dev, dan $REMOTE (/opt/proxy-pool, tempat deploy.sh
# menaruh .pia-credentials) di VM - sama seperti waktu skrip ini di legacy-ovpn/.
[ -n "$CRED_FILE" ] || for c in ../.pia-credentials .pia-credentials "$HOME/.pia-credentials"; do
  [ -r "$c" ] && { CRED_FILE=$c; break; }
done
CRED_FILE=${CRED_FILE:-$HOME/.pia-credentials}
type=aes-128-cbc-udp version=2.4 platform=desktop use_ip=1 list_only= dedup_ip=
suffix=$(date +%Y%m%d)

while [ $# -gt 0 ]; do
  case "$1" in
    -l) list_only=1; shift ;;
    -t) type=$2; shift 2 ;;
    -v) version=$2; shift 2 ;;
    -p) platform=$2; shift 2 ;;
    --no-ip) use_ip=; shift ;;
    --dedup-ip) dedup_ip=1; shift ;;
    -s) suffix=$2; shift 2 ;;
    -*) echo "opsi tidak dikenal: $1" >&2; exit 1 ;;
    *) break ;;
  esac
done

[ -z "$dedup_ip" ] || [ -n "$use_ip" ] || { echo "--dedup-ip butuh mode IP; jangan pakai bareng --no-ip" >&2; exit 1; }

user="${PIA_USER:-}" pass="${PIA_PASS:-}"
if [ -z "$user" ] || [ -z "$pass" ]; then
  [ -r "$CRED_FILE" ] || { echo "kredensial tidak ada: isi $CRED_FILE (baris 1 user, baris 2 pass) atau \$PIA_USER/\$PIA_PASS" >&2; exit 1; }
  user=$(sed -n 1p "$CRED_FILE"); pass=$(sed -n 2p "$CRED_FILE")
fi
[ -n "$user" ] && [ -n "$pass" ] || { echo "user/pass kosong di $CRED_FILE" >&2; exit 1; }

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
jar=$tmp/jar.txt

# ponytail: scrape token & region pakai grep, bukan parser HTML. Rusak kalau PIA ubah markup.
token_of() { grep -o 'name="authenticity_token"[^>]*value="[^"]*"' "$1" | head -1 | sed 's/.*value="//;s/"//' || true; }

# 1. login -> simpan cookie session ke jar
curl -fsSL -c "$jar" -o "$tmp/signin.html" "$SITE/account/client-sign-in"
tok=$(token_of "$tmp/signin.html")
[ -n "$tok" ] || { echo "authenticity_token halaman login tidak ketemu - markup berubah?" >&2; exit 3; }
code=$(curl -sSL -b "$jar" -c "$jar" -o /dev/null -w '%{http_code}' \
  --data-urlencode "authenticity_token=$tok" \
  --data-urlencode "user=$user" \
  --data-urlencode "pass=$pass" \
  "$SITE/account/ccp-sign-in")
[ "$code" = 200 ] || { echo "login ditolak (HTTP $code) - cek username/password di $CRED_FILE" >&2; exit 2; }

# 2. buka generator; kalau dilempar balik ke sign-in berarti login gagal
curl -fsSL -b "$jar" -c "$jar" -o "$tmp/gen.html" -w '%{url_effective}' "$GEN" > "$tmp/url"
case "$(cat "$tmp/url")" in
  *sign-in*) echo "login gagal - cek username/password di $CRED_FILE" >&2; exit 2 ;;
esac
token=$(token_of "$tmp/gen.html")
[ -n "$token" ] || { echo "authenticity_token generator tidak ketemu - markup berubah?" >&2; exit 3; }

# ponytail: benua diambil dari class div pembungkus (…nextgen_region as…), kode region dari
# input di dalamnya. Rusak kalau PIA ubah markup - itu sebabnya ada cek "region tidak ada".
list_regions() {  # -> "benua kode" per baris
  tr '<' '\n' < "$tmp/gen.html" | awk '
    /class="col-md-3 nextgen_region [a-z]+"/ { c = $0; sub(/.*nextgen_region /, "", c); sub(/".*/, "", c); next }
    /name="selected_nextgen_region"/ { v = $0; sub(/.*value="/, "", v); sub(/".*/, "", v); if (c != "") print c, v }
  ' | sort -u
}
if [ -n "$list_only" ]; then
  list_regions
  exit 0
fi
[ $# -gt 0 ] || { echo "sebutkan minimal satu region (lihat: $0 -l)" >&2; exit 1; }

pairs=$(list_regions)
regions=$(awk '{print $2}' <<<"$pairs")
[ -n "$regions" ] || { echo "daftar region kosong - markup halaman berubah?" >&2; exit 3; }

expand() {  # nama benua / all -> daftar kode, selain itu kembalikan apa adanya
  case "$1" in
    all)                 echo "$regions" ;;
    asia|as)             awk '$1=="as"{print $2}' <<<"$pairs" ;;
    europe|eu)           awk '$1=="eu"{print $2}' <<<"$pairs" ;;
    north-america|na)    awk '$1=="na"{print $2}' <<<"$pairs" ;;
    south-america|sa)    awk '$1=="sa"{print $2}' <<<"$pairs" ;;
    oceania|oc)          awk '$1=="oc"{print $2}' <<<"$pairs" ;;
    africa|af)           awk '$1=="af"{print $2}' <<<"$pairs" ;;
    *)                   echo "$1" ;;
  esac
}
targets=$(for a in "$@"; do expand "$a"; done | awk '!seen[$0]++')
set -- $targets

case "$type" in
  udp|tcp) types="aes-128-cbc-$type aes-256-cbc-$type aes-128-gcm-$type aes-256-gcm-$type" ;;
  all)     types="aes-128-cbc-udp aes-256-cbc-udp aes-128-gcm-udp aes-256-gcm-udp aes-128-cbc-tcp aes-256-cbc-tcp aes-128-gcm-tcp aes-256-gcm-tcp" ;;
  *)       types=$type ;;
esac

# 3. generate per region x tipe
mkdir -p "$OUT"
n=0
for r in "$@"; do
  seen_ip= kept=0
  grep -qx "$r" <<<"$regions" || { echo "region tidak ada: $r (lihat: $0 -l)" >&2; exit 1; }
  for t in $types; do
    cipher=${t%-*}      # aes-128-cbc-udp -> aes-128-cbc
    protocol=${t##*-}   # aes-128-cbc-udp -> udp
    f="$OUT/$r-$t${use_ip:+-ip}${suffix:+-$suffix}.ovpn"
    curl -fsS -b "$jar" -o "$f" \
      --data-urlencode "authenticity_token=$token" \
      --data "target_version=$version&platform=$platform&nextgen_region=$r&cipher=$cipher&protocol=$protocol&type=$t&port=" \
      ${use_ip:+--data "ip=1"} \
      "$GEN/generate"
    head -1 "$f" | grep -q '^client' || { echo "gagal: $r/$t (respons bukan config)" >&2; rm -f "$f"; exit 4; }
    # ponytail: profil GCM dari PIA memuat "ncp-disable", opsi yang DIHAPUS di OpenVPN 2.6 dan
    # membuatnya fatal (gluetun pakai 2.6). Efeknya nihil di 2.4/2.5 kalau dibuang.
    # [[:space:]]*$ biar tetap cocok kalau barisnya berakhiran CRLF
    sed -i '' '/^ncp-disable[[:space:]]*$/d' "$f" 2>/dev/null || sed -i '/^ncp-disable[[:space:]]*$/d' "$f"
    if [ -n "$dedup_ip" ]; then
      ip=$(awk '/^remote /{print $2; exit}' "$f")
      if grep -qxF "$ip" <<<"$seen_ip"; then rm -f "$f"; sleep 0.2; continue; fi
      seen_ip=$(printf '%s\n%s' "$seen_ip" "$ip")
    fi
    kept=$((kept+1)); n=$((n+1))
    sleep 0.2   # ponytail: jeda tetap biar tidak dianggap flood; naikkan kalau kena rate limit
  done
  echo "$r: $kept file"
done
echo "total $n file di $OUT"
