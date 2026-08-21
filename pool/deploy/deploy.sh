#!/usr/bin/env bash
# Deploy pool manager ke VM produksi lewat Teleport (tsh). Jalankan dari
# root repo di MESIN LOKAL - bukan di VM, dan bukan otomatis lewat systemd
# apa pun. Kamu yang menjalankan ini, sesuai kesepakatan.
#
# Paket sistem + kode saja. TIDAK menyentuh kredensial, TIDAK memasang
# systemd unit, TIDAK menyentuh crawler-prod - itu langkah manual
# terpisah, dicetak di akhir skrip ini.
#
# Usage: ./pool/deploy/deploy.sh
# Env (opsional): POOL_VM_HOST, POOL_VM_LOGIN, POOL_VM_REMOTE
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

HOST=${POOL_VM_HOST:-asl-prd-prod-crawler-proxy-2}
LOGIN=${POOL_VM_LOGIN:-root}
REMOTE=${POOL_VM_REMOTE:-/opt/proxy-pool}

echo "==> [1/5] paket sistem (python3-flask, python3-requests) docker.io sudah di install manual"
tsh ssh "$LOGIN@$HOST" "apt-get update -qq && apt-get install -y -qq python3-flask python3-requests"

echo "==> [2/5] modul kernel tun, dipersiapkan permanen lintas boot"
tsh ssh "$LOGIN@$HOST" "modprobe tun && (grep -qxF tun /etc/modules-load.d/tun.conf 2>/dev/null || echo tun > /etc/modules-load.d/tun.conf)"

echo "==> [3/5] menyalin kode (pool/, servers.sh) ke $HOST:$REMOTE"
tsh ssh "$LOGIN@$HOST" "mkdir -p $REMOTE"
tsh scp -r pool servers.sh "$LOGIN@$HOST:$REMOTE/"
# pool.db/__pycache__/probe-out ikut ke-scp kalau ada di lokal (state tes) -
# state VM harus mulai bersih, bukan warisan dari mesin dev.
tsh ssh "$LOGIN@$HOST" "rm -rf $REMOTE/pool/pool.db $REMOTE/pool/__pycache__ $REMOTE/pool/probe/__pycache__ $REMOTE/pool/probe-out"

echo "==> [4/5] segarkan cache server SEA langsung dari VM (bukan salinan dari lokal)"
tsh ssh "$LOGIN@$HOST" "cd $REMOTE && ./servers.sh pia -r >/dev/null && ./servers.sh proton -r >/dev/null && echo ok"

echo "==> [5/5] build image probe - native amd64 di VM, tanpa emulasi QEMU"
tsh ssh "$LOGIN@$HOST" "cd $REMOTE && docker build -t olx-pool-probe:latest pool/probe/"

cat <<EOF

==> Kode + image sudah siap di $HOST:$REMOTE.

Langkah yang TERSISA - sengaja manual, menyentuh kredensial dan systemd:

  1. Salin kredensial PIA (satu file, satu perintah, kamu yang jalankan):
       tsh scp .pia-credentials $LOGIN@$HOST:$REMOTE/.pia-credentials

  2. Buat /etc/proxy-pool.env di VM dari templat
     pool/deploy/proxy-pool.env.example - isi PROTON_KEY_SLOT_3 dengan
     kunci WireGuard yang sudah di-generate khusus untuk slot ini.

  3. Pasang unit systemd:
       tsh scp pool/deploy/pool-manager.service $LOGIN@$HOST:/etc/systemd/system/pool-manager.service
       tsh ssh $LOGIN@$HOST "systemctl daemon-reload && systemctl enable --now pool-manager"

  4. Cek halaman pantau (SSH tunnel, atau langsung kalau network sudah
     bisa route): http://$HOST:8080/

CronJob crawler-prod (pool/deploy/crawler-prod-cronjob.yaml) SENGAJA belum
disinggung di atas - itu langkah Fase 3 yang mengubah perilaku produksi,
cuma diterapkan setelah kolam di VM terbukti stabil beberapa hari.
EOF
