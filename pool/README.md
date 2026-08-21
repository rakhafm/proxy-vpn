# Pool manager — Fase 1 + Fase 2

Implementasi Fase 1 dari [`../PRD-proxy-pool.html`](../PRD-proxy-pool.html): slot,
orkestrasi Docker, kedua job (rotasi harian + verifikasi per jam), SQLite,
`GET /proxies`. Jalan di mesin lokal, dipantau lewat `curl`. Belum menyentuh prod.

Fase 2 (halaman pantau) juga sudah ada: buka `http://127.0.0.1:8080/` di browser —
tabel slot (server, exit IP, negara/ASN, status, kapan terakhir lolos), riwayat 30
probe terakhir, dan dua tombol (rotasi/verifikasi manual). Jinja2 + HTML/CSS bawaan
Flask, tanpa build step — halaman menyegarkan diri sendiri tiap 30 detik lewat
`<meta http-equiv="refresh">`, tidak ada JS.

> Skema `slots` menambah kolom `negara`/`org` di Fase 2. Kalau `pool/pool.db`
> peninggalan sebelum Fase 2, hapus dan biarkan rotasi berikutnya membuatnya
> ulang (`CREATE TABLE IF NOT EXISTS` tidak menambah kolom ke tabel yang sudah
> ada) — tidak ada mekanisme migrasi, dan untuk state lokal yang dites ulang-
> alik ini tidak sepadan dibangun.

## Prasyarat

- Docker (untuk container gluetun tiap slot, dan untuk probe harian).
- `.pia-credentials` di root repo (`user`\n`pass`), seperti yang sudah dipakai
  `run-clean.sh`.
- **Satu kunci WireGuard Proton per slot Proton** — bukan satu kunci dipakai
  ulang. Ambil dari <https://account.proton.me/u/2/vpn/WireGuard>, satu per slot,
  lalu set `PROTON_KEY_SLOT_4`, `PROTON_KEY_SLOT_5`, dst (nama env = `PROTON_KEY_` +
  id slot huruf besar, `-`→`_`). Ini prasyarat keras dari PRD — tanpa ini slot
  Proton dilewati (dicatat di log), bukan dipaksa jalan dengan kunci yang sama.
- `servers-pia.txt` / `servers-proton.txt` di root repo sudah ada (`./servers.sh
  pia -r`, `./servers.sh proton -r`) — pool manager membaca cache ini untuk daftar
  kandidat, tidak menjalankan `gluetun format-servers` sendiri.
- Image probe harian sudah di-build (lihat langsung di bawah).

## Build image probe

Probe harian jalan di image sendiri, **bukan** image crawler produksi — jadi
tidak butuh login registry apa pun. Instalasi Chrome + chromedriver-nya disalin
dari `Dockerfile` crawler (supaya versi Chrome-nya sejenis dengan yang dipakai
`scrapeselenium()` di produksi), dan kode vonisnya (`_chrome_options()`,
`OLX_SEARCH_MARKERS`, `olx_search_markers()`) disalin ke
[`pool/probe/olx_probe.py`](probe/olx_probe.py) — lihat komentar di file itu
untuk sitasi baris asalnya di repo crawler, dan sinkronkan ulang manual kalau
sumbernya berubah.

```bash
docker build -t olx-pool-probe:latest pool/probe/
```

## Menjalankan

```bash
python3 -m venv .venv-pool && .venv-pool/bin/pip install -r pool/requirements.txt
PIA_SLOTS=3 PROTON_SLOTS=3 \
PROTON_KEY_SLOT_4=... PROTON_KEY_SLOT_5=... PROTON_KEY_SLOT_6=... \
.venv-pool/bin/python3 -m pool.app
```

Ini menyalakan scheduler (rotasi 03:00 Asia/Jakarta, verifikasi tiap jam) dan
membuka API di `:8080`. Untuk memicu manual tanpa menunggu jadwal:

```bash
curl -X POST http://127.0.0.1:8080/jobs/rotate
curl -X POST http://127.0.0.1:8080/jobs/verify
curl http://127.0.0.1:8080/proxies
curl http://127.0.0.1:8080/slots
curl http://127.0.0.1:8080/probes?limit=20
curl -X POST http://127.0.0.1:8080/slots/slot-1/bad   # konsumen lapor IP kena deny
```

Tiap probe harian (`kind=daily` di `/probes`) menyimpan HTML + screenshot halaman
yang benar-benar dirender ke `pool/probe-out/<slot>-<waktu>.{html,png}` — patokan
paling langsung untuk melihat apa yang sebenarnya OLX kirim, bukan cuma jumlah
marker. `detail` di baris `/probes` menyebut nama filenya.

## Variabel lingkungan

| Var | Default | Guna |
|---|---|---|
| `PIA_SLOTS` / `PROTON_SLOTS` | `3` / `3` | jumlah slot per provider |
| `POOL_CANDIDATE_GROUP` | `sea` | grup `servers.sh` untuk kandidat — `sea`, `asia`, atau nama negara/region persis |
| `POOL_PORT_BASE` | `9000` | port slot pertama = base+1 |
| `POOL_API_PORT` | `8080` | port Flask |
| `POOL_ADVERTISE_HOST` | `127.0.0.1` | host yang dicetak di `GET /proxies` |
| `POOL_DB` | `pool/pool.db` | path SQLite |
| `POOL_DAILY_TIME` | `03:00` | jam rotasi, Asia/Jakarta |
| `HOURLY_REFILL` | `0` | `1` = slot yang diblokir langsung dirotasi saat verifikasi per jam |
| `POOL_MAX_TRIES` | `5` | percobaan kandidat maksimum per slot per rotasi |
| `POOL_CANDIDATE_RETRY_HOURS` | `24` | jam sebelum server yang gagal/blocked boleh dicoba lagi |
| `DISCORD_WEBHOOK_URL` | — | kalau diset, kirim pesan ke sini saat kolam tidak penuh (sebagian atau semua slot mati) di akhir tiap job |
| `PROBE_IMAGE` | `olx-pool-probe:latest` | image probe harian sendiri, hasil `docker build pool/probe/` |
| `PIA_CREDENTIALS` | cari `.pia-credentials` di root lalu `$HOME` | path kredensial PIA |
| `PROTON_KEY_<SLOT>` | — | kunci WireGuard per slot Proton, wajib |

## Self-check

```bash
.venv-pool/bin/python3 -m pool.test_pool -v
```

Tidak menyentuh Docker maupun jaringan — hanya skema SQLite, dedupe kandidat, dan
endpoint Flask di atas database sementara.

## Yang belum ada (di luar Fase 1 + 2)

Pemindahan ke VM + CronJob sync di `crawler-prod` (Fase 3), notifikasi (Fase 4) —
lihat `../PRD-proxy-pool.html#fase`.
