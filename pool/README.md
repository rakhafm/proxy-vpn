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
- **Satu kunci WireGuard Proton per slot Proton** (default, `PROTON_VPN_TYPE=wireguard`)
  — bukan satu kunci dipakai ulang. Ambil dari
  <https://account.proton.me/u/2/vpn/WireGuard>, satu per slot, lalu set
  `PROTON_KEY_SLOT_4`, `PROTON_KEY_SLOT_5`, dst (nama env = `PROTON_KEY_` +
  id slot huruf besar, `-`→`_`). Ini prasyarat keras dari PRD — tanpa ini slot
  Proton dilewati (dicatat di log), bukan dipaksa jalan dengan kunci yang sama.
  Set `PROTON_VPN_TYPE=openvpn` untuk pakai OpenVPN sebagai gantinya — beda
  dari WireGuard, kredensialnya **satu akun dibagi semua slot Proton**
  (`.proton-credentials` di root repo, format sama seperti `.pia-credentials`):
  username/password khusus **"OpenVPN/IKEv2"** dari
  <https://account.proton.me/u/2/account-password>, BUKAN login akun Proton
  biasa.
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

## Dua probe, dan kenapa `connecting` dipisah dari `inconclusive`

Probe **harian** memuat `OLX_URL` di Chrome sungguhan. Sejak 2026-09-03 URL itu
adalah **root domain**, bukan halaman pencarian lagi, dan vonisnya dijatuhkan dari
ada/tidaknya `id="referenceNum"` — bukan lagi dari jumlah `OLX_SEARCH_MARKERS`.
Alasannya: Akamai terbukti me-redirect diam-diam `mobil-bekas_c198` ke homepage
(200 penuh, 0/5 marker, tanpa `referenceNum`), dan aturan lama menjatuhkan itu
sebagai `blocked` — mencoret exit yang homepage-nya sendiri bersih.

Halaman pencarian tetap dibuka lewat `OLX_VALIDATE_URL`, tapi **non-gating**:
jumlah marker dan buktinya disimpan (`<slot>-<waktu>-validate.{html,png}`) dan
tidak pernah mengubah exit code probe. Ini melonggarkan jaminan PRD "vonis pool =
vonis crawler" secara sadar — slot bisa `active` walau listing masih diredirect.
Kalau `OLX_VALIDATE_URL` konsisten nol marker sementara `OLX_URL` bersih, itu
tandanya redirect diam tadi masih berlangsung.

Probe **per jam** murah dan jalan dua langkah:

1. `IP_CHECK_URL` (`ifconfig.me/ip`) — endpoint teks polos di luar CDN anti-bot.
   Gagal di sini berarti tunnelnya memang mati → `connecting`.
2. `OLX_HOURLY_URL` (`https://www.olx.co.id/`) — root domain, **bukan** halaman
   pencarian. Ada `referenceNum` → `blocked`; balas normal → `ok`; gagal padahal
   langkah 1 lolos → `inconclusive`, dan status slot **tidak disentuh**.

Kelas `inconclusive` itu yang dulu tidak ada, dan absennya adalah bug: setiap
`curl exit != 0` divonis `connecting`, sehingga slot sehat hilang dari
`/proxies`. Sejak 2026-09-02 Akamai me-reset stream HTTP/2 untuk `curl` di path
pencarian (exit 92) bahkan pada exit IP yang probe browser buktikan bersih
beberapa menit sebelumnya — 17 dari 21 vonis `connecting` di proxy-1 dan 20 dari
29 di proxy-2 ternyata false negative jenis ini. Root domain lewat proxy yang
sama membalas 586 KB dengan normal. Memaksa `--http1.1` bukan obatnya (dites:
malah timeout 5/5); yang menyelesaikan adalah pindah URL.

Sisanya menyusul dari situ:

- Vonis `connecting` diberi toleransi `POOL_FAIL_STREAK_LIMIT` kali berturut-turut
  sebelum slot dicoret dari daftar terbit. `blocked` **tidak** — `referenceNum`
  yang benar-benar terbaca itu bukti langsung tentang exit IP-nya, bukan probe
  yang gagal, jadi tetap menjatuhkan slot seketika.
- Slot yang macet `connecting` dirotasi otomatis (`POOL_ROTATE_STUCK`, default
  menyala). Sebelumnya `HOURLY_REFILL` cuma dicek di cabang `blocked`, jadi slot
  `connecting` tidak punya jalan keluar selain rotasi manual.
- Slot yang tidak aktif dicek ulang tiap `POOL_RECHECK_SECONDS` (default 5 menit),
  bukan menunggu siklus per jam — jendela pemulihannya turun dari 59 menit ke 5.
- Rotasi dan verifikasi antre di satu `JOB_LOCK` (`jobs.serialized`). Tanpa itu
  keduanya bisa tumpang tindih: dua probe di proxy-1 mencatat `curl exit 7`
  ("port 9003 after 0 ms") tepat saat rotasi sedang di antara dua kandidat.

Konsekuensi yang perlu disadari: sejak vonis harian ikut pindah ke root domain,
blokir yang **hanya** muncul di path pencarian tidak lagi menjatuhkan slot sama
sekali — cuma tercatat sebagai validasi non-gating. Jalur cepatnya tetap
`POST /slots/<id>/bad` — konsumen yang
benar-benar kena deny adalah sinyal yang jauh lebih akurat daripada probe `curl`.

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
| `HOURLY_REFILL` | `0` | `1` = slot yang **diblokir** langsung dirotasi saat verifikasi per jam |
| `POOL_FAIL_STREAK_LIMIT` | `3` | berapa probe per jam gagal berturut-turut sebelum slot dicoret dari `/proxies`; `1` = perilaku lama |
| `POOL_RECHECK_SECONDS` | `300` | selang cek ulang khusus slot yang tidak aktif |
| `POOL_ROTATE_STUCK` | `1` | `1` = slot yang macet **`connecting`** dirotasi otomatis setelah menembus streak |
| `OLX_URL` | `https://www.olx.co.id/` | URL yang **menggerbang** vonis probe harian - root domain, dinilai dari `referenceNum` |
| `OLX_VALIDATE_URL` | `https://www.olx.co.id/mobil-bekas_c198` | halaman listing, dites di probe harian untuk bukti/log saja - **tidak** mengubah vonis; kosongkan untuk melewati |
| `OLX_HOURLY_URL` | `https://www.olx.co.id/` | URL probe per jam - root domain, bukan halaman pencarian |
| `IP_CHECK_URL` | `https://ifconfig.me/ip` | endpoint netral untuk memastikan tunnel hidup |
| `POOL_MAX_TRIES` | `5` | percobaan kandidat maksimum per slot per rotasi |
| `POOL_CANDIDATE_RETRY_HOURS` | `24` | jam sebelum server yang gagal/blocked boleh dicoba lagi |
| `DISCORD_WEBHOOK_URL` | — | kalau diset, kirim pesan ke sini saat kolam tidak penuh (sebagian atau semua slot mati) di akhir tiap job |
| `PROBE_IMAGE` | `olx-pool-probe:latest` | image probe harian sendiri, hasil `docker build pool/probe/` |
| `PIA_CREDENTIALS` | cari `.pia-credentials` di root lalu `$HOME` | path kredensial PIA |
| `PROTON_VPN_TYPE` | `wireguard` | `wireguard` (kunci per slot) atau `openvpn` (satu akun untuk semua slot Proton) |
| `PROTON_KEY_<SLOT>` | — | kunci WireGuard per slot Proton, wajib kalau `PROTON_VPN_TYPE=wireguard` |
| `PROTON_CREDENTIALS` | cari `.proton-credentials` di root lalu `$HOME` | path kredensial OpenVPN/IKEv2 Proton, wajib kalau `PROTON_VPN_TYPE=openvpn` |
| `WIREGUARD_MTU` | `1280` | MTU tunnel WireGuard Proton - kosongkan untuk pakai default gluetun |
| `OPENVPN_MSSFIX` | `1280` | MSS clamp OpenVPN (PIA, atau Proton kalau `PROTON_VPN_TYPE=openvpn`) - kosongkan untuk pakai default gluetun |

## Self-check

```bash
.venv-pool/bin/python3 -m pool.test_pool -v
```

Tidak menyentuh Docker maupun jaringan — hanya skema SQLite, dedupe kandidat, dan
endpoint Flask di atas database sementara.

## Yang belum ada (di luar Fase 1 + 2)

Pemindahan ke VM + CronJob sync di `crawler-prod` (Fase 3), notifikasi (Fase 4) —
lihat `../PRD-proxy-pool.html#fase`.
