#!/usr/bin/env bash
# Jalankan DI VM (lewat tsh ssh), bukan di mesin lokal.
#
#   tsh scp pool/deploy/ufw.sh root@asl-prd-prod-crawler-proxy-1:/tmp/ufw.sh
#   tsh ssh root@asl-prd-prod-crawler-proxy-1 "bash /tmp/ufw.sh"
#
# Rentang port dibuka sampai 9006 (bukan cuma 9003) supaya naik ke 6 slot
# nanti tidak perlu edit firewall lagi - port yang belum dipakai tetap
# aman karena dibatasi ke subnet internal, bukan internet.
#
# Trafik dari pod k8s ke luar cluster CIDR biasanya di-SNAT jadi IP node
# oleh Canal (VM melihat asal dari 10.0.0.21-24, bukan 10.42.x.x), tapi itu
# bergantung konfigurasi ipMasq - izinkan keduanya, tidak perlu menebak.
set -euo pipefail

ufw allow from 10.0.0.0/24  to any port 8080      proto tcp comment 'pool: API + halaman pantau'
ufw allow from 10.0.0.0/24  to any port 9001:9006 proto tcp comment 'pool: slot proxy'
ufw allow from 10.42.0.0/16 to any port 8080      proto tcp comment 'pool: API + halaman pantau (pod CIDR)'
ufw allow from 10.42.0.0/16 to any port 9001:9006 proto tcp comment 'pool: slot proxy (pod CIDR)'

echo "==> status ufw setelah perubahan:"
ufw status verbose

cat <<'EOF'

PENTING - verifikasi dari LUAR jaringan internal ini sebelum menganggap
Fase 3 selesai (lihat PRD-proxy-pool.html §Risiko, "Slot proxy terjangkau
dari luar jaringan"): port 8080 dan 9001-9006 TIDAK BOLEH terjangkau dari
internet. Kalau ufw default policy bukan "deny incoming", aturan di atas
saja tidak cukup - cek `ufw status verbose` menunjukkan "Default: deny
(incoming)" sebelum lanjut.
EOF
