# Fase 3 — deploy ke VM

Artefak untuk memindahkan pool manager dari mesin lokal ke VM produksi dan
(opsional, belakangan) menyambungkannya ke `crawler-prod`. Semuanya
dirancang untuk **kamu jalankan sendiri** lewat `tsh` — tidak ada yang
otomatis menyentuh VM atau cluster.

## VM target

| | |
|---|---|
| Host | `asl-prd-prod-crawler-proxy-1` (akses via `tsh ssh root@...`) |
| Alamat | `10.0.0.51/24` — satu network dengan node k8s |
| OS | Ubuntu 24.04.3 LTS, x86_64, 2 vCPU |
| RAM | **1.9 GB, tanpa swap** |
| Disk | 9.2 GB total, ~3.9 GB tersisa saat dicek |
| Docker | belum terpasang (bersih, tidak ada konflik) |

RAM dan disk di sini **lebih kecil** dari asumsi kapasitas 6-slot di
`../../PRD-proxy-pool.html#kapasitas`. Karena itu semua artefak di sini
dikonfigurasi untuk **3 slot** (2 PIA + 1 Proton) dulu — sudah memenuhi
target minimum PRD ("≥3 exit hidup"), naik ke 6 setelah dipantau beberapa
hari dan RAM/disk terbukti cukup longgar.

## Urutan menjalankan

**1. Deploy kode + image** (dari mesin lokal, di root repo):

```bash
./pool/deploy/deploy.sh
```

Ini menginstal `docker.io`/`python3-flask`/`python3-requests`, memuat modul
`tun` permanen, menyalin `pool/` + `servers.sh`, menyegarkan cache server
SEA langsung dari VM, dan build image probe (native amd64 — jauh lebih
cepat dari build di Mac lewat emulasi QEMU). Output di akhir mencetak
langkah 2-5 di bawah, dengan host/path yang sudah terisi.

**2. Kredensial** (manual, sengaja tidak diotomatiskan):

```bash
tsh scp .pia-credentials root@asl-prd-prod-crawler-proxy-1:/opt/proxy-pool/.pia-credentials
```

**3. Generate kunci WireGuard Proton** — satu, khusus untuk slot ini (bukan
pakai ulang kunci yang sudah dipakai tes lokal), dari
<https://account.proton.me/u/2/vpn/WireGuard>. Salin `PrivateKey` dari
`.conf` yang diunduh.

**4. Buat env file di VM:**

```bash
tsh scp pool/deploy/proxy-pool.env.example root@asl-prd-prod-crawler-proxy-1:/etc/proxy-pool.env
tsh ssh root@asl-prd-prod-crawler-proxy-1 "chmod 600 /etc/proxy-pool.env"
```

lalu edit `/etc/proxy-pool.env` langsung di VM (`tsh ssh` masuk, `nano` atau
`vi`) — isi `PROTON_KEY_SLOT_3` dengan kunci dari langkah 3.

**5. Pasang systemd unit:**

```bash
tsh scp pool/deploy/pool-manager.service root@asl-prd-prod-crawler-proxy-1:/etc/systemd/system/pool-manager.service
tsh ssh root@asl-prd-prod-crawler-proxy-1 "systemctl daemon-reload && systemctl enable --now pool-manager"
tsh ssh root@asl-prd-prod-crawler-proxy-1 "systemctl status pool-manager --no-pager"
```

**6. Verifikasi:**

```bash
tsh ssh root@asl-prd-prod-crawler-proxy-1 "curl -s http://127.0.0.1:8080/health"
tsh ssh root@asl-prd-prod-crawler-proxy-1 "free -h"   # pantau RAM setelah rotasi pertama
```

Halaman pantau (`http://10.0.0.51:8080/`) dan port proxy (9001-9006) publicly
reachable — server ini tidak pakai firewall. Buka lewat SSH tunnel
(`tsh ssh -L 8080:localhost:8080 root@asl-prd-prod-crawler-proxy-1`) atau
dari mesin lain di jaringan itu.

## Yang BELUM dijalankan — sengaja

`crawler-prod-cronjob.yaml` **tidak** diterapkan oleh langkah manapun di
atas. Ini keputusan desain PRD sendiri (Fase 3: *"jalankan berdampingan
dengan `vpn-pia`/`vpn-proton` yang sudah ada dulu, jangan menggantinya,
sampai kolamnya terbukti tidak pernah kosong sehari penuh"*). Terapkan
manual setelah pool manager di VM sudah dipantau beberapa hari:

```bash
kubectl --context asl-k8s apply -f pool/deploy/crawler-prod-cronjob.yaml
```

Sudah divalidasi `--dry-run=server` terhadap cluster sungguhan (skema
benar, PVC `crawler-prod-pvc-fs` ada) — tapi belum pernah benar-benar
diterapkan.

## Naik ke 6 slot nanti

Edit `/etc/proxy-pool.env` di VM: `PIA_SLOTS=3 PROTON_SLOTS=3`, tambah
`PROTON_KEY_SLOT_4`/`_5`/`_6` (kunci baru, bukan pakai ulang), lalu
`systemctl restart pool-manager`. Pantau `free -h` selama beberapa siklus
rotasi — kalau `available` terus menipis, VM ini butuh swap file (2-4 GB)
sebelum naik lebih jauh; ukurannya bukan sesuatu yang pool manager bisa
ukur sendiri, jadi ini keputusan manual berdasarkan yang kamu lihat.
