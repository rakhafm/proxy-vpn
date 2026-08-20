#!/usr/bin/env bash
# Daftar server yang dikenal gluetun, untuk provider bawaannya. Tidak butuh login sama
# sekali - daftarnya tertanam di image (`format-servers`), jadi ini murni baca lokal.
#
# Usage: ./servers.sh <proton|pia> [-l] [-r] [sea|asia|Filter...]
#   -l   tampilkan tabel lengkap, bukan hanya kolom pemilih server
#   -r   segarkan cache (default: pakai cache kalau ada)
#   grup: sea (Asia Tenggara), asia (seluruh Asia). Selain itu dicocokkan persis ke
#         kolom pertama: nama negara untuk proton, nama region untuk pia.
#
# Kolom yang dicetak adalah yang dipakai gluetun untuk memilih server:
#   proton -> hostname (SERVER_HOSTNAMES),  pia -> nama server (SERVER_NAMES)
#
# Contoh: ./servers.sh pia sea
#         ./servers.sh proton -l Japan
set -euo pipefail
cd "$(dirname "$0")"

prov=${1:-}; shift || true
case "$prov" in
  proton) flag=-protonvpn ;;
  pia)    flag=-private-internet-access ;;
  *) echo "usage: $0 <proton|pia> [-l] [-r] [sea|asia|Filter...]" >&2; exit 1 ;;
esac
CACHE="servers-$prov.txt"

long= refresh=
while [ $# -gt 0 ]; do
  case "$1" in
    -l) long=1; shift ;;
    -r) refresh=1; shift ;;
    -*) echo "opsi tidak dikenal: $1" >&2; exit 1 ;;
    *) break ;;
  esac
done

# Tabel kedua provider beda kolom, jadi normalkan jadi 3 kolom: grup, keterangan, pemilih.
#   proton: | Country | Region | City | Hostname | VPN | ...   -> negara, kota, hostname
#   pia:    | Region | Hostname | Name | TCP | ...             -> region, hostname, nama
if [ -n "$refresh" ] || [ ! -s "$CACHE" ]; then
  raw=$(docker run --rm qmcgaw/gluetun format-servers "$flag" 2>/dev/null)
  case "$prov" in
    # ambil baris wireguard saja; tiap server muncul dua kali (openvpn + wireguard)
    proton) printf '%s\n' "$raw" | awk -F'|' '$6 ~ /wireguard/ {
               for (i=2;i<=5;i++) gsub(/^ +| +$|`/,"",$i); print $2 "\t" $4 "\t" $5 }' ;;
    pia)    printf '%s\n' "$raw" | awk -F'|' 'NR>2 {
               for (i=2;i<=4;i++) gsub(/^ +| +$|`/,"",$i); print $2 "\t" $3 "\t" $4 }' ;;
  esac | sort -u > "$CACHE"
fi
[ -s "$CACHE" ] || { echo "gagal membaca daftar server dari image gluetun" >&2; exit 1; }

# ponytail: grup benua ditulis tangan - gluetun tidak punya kolom benua, dan penamaan
# negaranya beda antar provider (pia memakai nama region, bukan negara).
case "$prov" in
  proton)
    sea="Brunei Darussalam|Cambodia|Indonesia|Lao People's Democratic Republic|Malaysia|Myanmar|Philippines|Singapore|Thailand|Vietnam"
    asia="Afghanistan|Armenia|Azerbaijan|Bahrain|Bangladesh|Bhutan|Brunei Darussalam|Cambodia|Cyprus|Georgia|Hong Kong|India|Indonesia|Iraq|Israel|Japan|Jordan|Kazakhstan|Korea|Kuwait|Kyrgyzstan|Lao People's Democratic Republic|Lebanon|Macao|Malaysia|Mongolia|Myanmar|Nepal|Oman|Pakistan|Palestine, State of|Philippines|Qatar|Saudi Arabia|Singapore|Sri Lanka|Syrian Arab Republic|Taiwan|Tajikistan|Thailand|Turkey|Turkmenistan|United Arab Emirates|Uzbekistan|Vietnam|Yemen" ;;
  pia)
    sea="Singapore|Indonesia|Malaysia|Philippines|SG Streaming Optimized|Vietnam"
    asia="Armenia|Bangladesh|Cambodia|China|Cyprus|Georgia|Hong Kong|India|Indonesia|Israel|JP Tokyo|Kazakhstan|Macao|Malaysia|Mongolia|Nepal|Philippines|Qatar|Saudi Arabia|SG Streaming Optimized|Singapore|South Korea|Sri Lanka|Taiwan|Turkey|United Arab Emirates|Vietnam" ;;
esac

want=
for a in "$@"; do
  case "$a" in
    sea|southeast-asia|asean) want="$want|$sea" ;;
    asia|as)                  want="$want|$asia" ;;
    *)                        want="$want|$a" ;;
  esac
done
want=${want#|}

# cocokkan kolom pertama persis, bukan substring (biar "Singapore" tidak menarik
# "SG Streaming Optimized" atau sebaliknya)
out=$(awk -F'\t' -v w="$want" 'w == "" { print; next }
  { n = split(w, a, "|"); for (i = 1; i <= n; i++) if ($1 == a[i]) { print; next } }' "$CACHE")
[ -n "$out" ] || { echo "tidak ada server yang cocok (lihat: $0 $prov -l)" >&2; exit 1; }
if [ -n "$long" ]; then printf '%s\n' "$out"; else printf '%s\n' "$out" | cut -f3; fi
