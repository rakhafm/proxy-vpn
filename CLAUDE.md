# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Apa isi repo ini

Cari exit IP VPN (PIA lewat OpenVPN, Proton lewat WireGuard/OpenVPN) yang hari ini masih bisa
membuka OLX tanpa kena blokir bot Akamai, lalu terbitkan proxy yang lolos. Dua generasi hidup
berdampingan:

- **Jalur manual (root repo)** — script bash sekali jalan: `servers.sh` → `sweep.sh` → `run-clean.sh`,
  hasilnya `hasil-<provider>.csv` + bukti di `probe-out/`.
- **`pool/`** — pool manager (Flask + SQLite) yang mengotomatiskan alur itu sebagai layanan.
  Ini yang aktif dikembangkan.
- **`legacy-ovpn/`** — jalur `.ovpn` tulis-sendiri, tidak lagi dikembangkan tapi masih dipakai
  `vpn-pia` di prod.

Rancangan pool manager ada di `docs/PRD-proxy-pool.html` — kode `pool/` sering menyitasi bagiannya
(`§Model slot`, `§Teknologi`, `§Keputusan`). Baca itu sebelum mengubah keputusan desain.

**Bahasa: dokumentasi, komentar, log, dan pesan error semuanya bahasa Indonesia.** Nama
kolom DB dan variabel campuran (`negara`, `org`, `diblokir`, `alasan`). Ikuti pola yang ada.

## Perintah

`Makefile` di root membungkus semua perintah di bawah (`make help` untuk daftar; `make -n <target>`
mencetak perintah aslinya). Itu pembungkus tipis, bukan sumber kebenaran — perintah di bawah tetap
yang asli, dan target `DANGER-*` (reset DB, hapus bukti, deploy) sengaja tidak pernah jadi
prasyarat target lain.

Jalur manual (butuh Docker, Chrome, `npx playwright`):

```bash
./servers.sh pia sea                          # daftar server (cache: servers-<prov>.txt; -r segarkan)
./servers.sh pia sea | xargs ./sweep.sh pia   # sapu → hasil-pia.csv (~25 menit)
./run-clean.sh pia --pool 3                   # jalankan server yang lolos di sweep terakhir
./shot.sh <url> out.png                       # screenshot satu halaman lewat proxy
```

Pool manager:

```bash
python3 -m venv .venv-pool && .venv-pool/bin/pip install -r pool/requirements.txt
docker build -t olx-pool-probe:latest pool/probe/          # sekali, sebelum rotasi pertama
PIA_SLOTS=3 PROTON_SLOTS=3 PROTON_KEY_SLOT_4=... \
  .venv-pool/bin/python3 -m pool.app                       # scheduler + API di :8080

.venv-pool/bin/python3 -m pool.test_pool -v                # self-check, tanpa Docker/jaringan
.venv-pool/bin/python3 -m pool.test_pool -v PoolTest.test_next_candidate_skips_failed   # satu test
```

Tidak ada linter/formatter yang dikonfigurasi di repo ini. Yang terdekat: `make check`
(= `bash -n` semua skrip + `pool.test_pool`) — jalankan sebelum commit.

Memicu job manual tanpa menunggu jadwal:

```bash
curl -X POST http://127.0.0.1:8080/jobs/rotate
curl -X POST http://127.0.0.1:8080/jobs/verify
curl -X POST http://127.0.0.1:8080/jobs/check         # cek URL di pool/checks.csv (non-gating)
curl -X POST http://127.0.0.1:8080/slots/slot-1/bad   # konsumen lapor IP kena deny
```

## Arsitektur `pool/`

Alur satu rotasi (`jobs.rotate_slot`) — tiap langkah bisa menggagalkan kandidat dan mencoba
yang berikutnya, sampai `POOL_MAX_TRIES`:

```
candidates.next_candidate()  → server acak, belum gagal <24 jam, tidak dipakai slot lain
orchestrator.start()         → docker run gluetun, HTTP proxy di port PORT_BASE+n
orchestrator.wait_healthy()  → gagal ⇒ ambil docker logs DULU (sebelum stop), catat connect_fail
orchestrator.exit_info()     → ipinfo.io lewat proxy; exit IP duplikat ⇒ dup_ip
probes.run_daily()           → Chrome di container probe ⇒ ok | blocked | error
                               ⇒ slot active + tulis exit_ip/negara/org
```

`verify_hourly` menjalankan `probes.run_hourly` pada slot active/connecting: langkah tunnel
(`IP_CHECK_URL`) selalu curl, langkah OLX ikut `POOL_VERIFY_MODE` — `chrome` (default, =
`run_daily`, bukti tersimpan) atau `curl`. `recheck_stuck` melakukan hal yang sama tiap 5 menit
khusus slot non-aktif, **selalu mode curl** (12×/jam terlalu mahal untuk Chrome). Keduanya lewat
`jobs._apply_hourly_verdict()`, yang memegang seluruh aturan status — baca itu sebelum
mengubah perilaku probe.

Berkas per tanggung jawab:

| Berkas | Isi |
|---|---|
| `config.py` | semua env var + pembaca kredensial; satu-satunya tempat default hidup |
| `db.py` | koneksi SQLite + `_migrate()` untuk kolom yang ditambah belakangan |
| `candidates.py` | pilih server; memanggil `../servers.sh` sebagai subprocess, tidak parse cache sendiri |
| `pia_custom.py` | profil `.ovpn` provider `pia-custom`; memanggil `get-pia-ovpn.sh` sebagai subprocess |
| `get-pia-ovpn.sh` | login + scrape config generator PIA; satu-satunya implementasinya, dipakai juga oleh `legacy-ovpn/daily.sh` |
| `orchestrator.py` | `docker` CLI lewat subprocess (bukan docker-py) |
| `probes.py` | dua jalur uji: `curl` per jam, Chrome-in-Docker harian |
| `jobs.py` | rotasi harian & verifikasi per jam, notifikasi Discord |
| `scheduler.py` | dua `threading.Timer` loop, sengaja bukan Celery/APScheduler |
| `app.py` | Flask: `/proxies`, `/proxies.json`, `/slots`, `/probes`, `/checks.json`, `/health`, `/probe-out/<f>`, halaman pantau `/` |
| `probe/olx_probe.py` | jalan **di dalam** container probe, bukan di proses pool |
| `probe/url_check.py` | job **Cek URL** (`/jobs/check`): tiap baris `checks.csv` dimuat Chrome lewat tiap slot aktif; mengimpor `olx_probe.py`, hasil ke tabel `checks`, **non-gating** |

## Invarian yang gampang dilanggar

- **Empat vonis probe per jam, dan tiga di antaranya gampang tertukar.** `connecting` = tunnel
  mati (langkah `IP_CHECK_URL` gagal). `inconclusive` = tunnel sehat tapi OLX tidak menjawab —
  **tidak menyentuh status slot**. `blocked` = `referenceNum` terbaca, bukti langsung tentang
  exit IP. `ok` = normal. Menggabungkan `inconclusive` ke `connecting` adalah bug yang persis
  sudah pernah terjadi: 17/21 vonis `connecting` di proxy-1 mencoret slot yang sehat.
- **`blocked` ≠ `error`.** `blocked` = Akamai menolak (halaman dimuat, nol marker) — sinyal tentang
  exit IP-nya. `error`/exit 2 dari `olx_probe.py` = probe gagal sebelum menilai (Chrome crash,
  tunnel putus) — dicatat `probe_error`, jangan pernah dicampur jadi "diblokir".
- **Halaman deny OLX tetap HTTP 200.** Klasifikasi berbasis pola HTML (`id="referenceNum"`,
  jumlah `OLX_SEARCH_MARKERS`), bukan status code. `curl` cukup untuk memvonis *buruk*; vonis
  *bersih* butuh browser sungguhan.
- **`run_hourly` memakai binary `curl` (langkah tunnel, dan langkah OLX di mode `curl`), bukan
  `requests`.** Fingerprint TLS/HTTP2 `requests` disuguhi silent-timeout oleh Akamai. Jangan
  "rapikan" jadi library HTTP. Di mode `chrome`, `run_daily` yang mengembalikan `error` jatuh ke
  `inconclusive` (tunnel sudah terbukti hidup), bukan `connecting`.
- **Probe per jam dan probe harian sama-sama menggerbang vonis dari root domain sejak
  2026-09-03**, bukan dari halaman pencarian lagi. `mobil-bekas_c198` terbukti diredirect diam
  oleh Akamai ke homepage (200 penuh, 0/5 `OLX_SEARCH_MARKERS`, tanpa `referenceNum`) — dulu ini
  jatuh sebagai `blocked` dan mencoret exit yang sebenarnya bersih. Vonis sekarang: `id="referenceNum"`
  ditemukan di root domain → `blocked`; tidak ada → lolos. `OLX_VALIDATE_URL` (listing, default
  `mobil-bekas_c198`) tetap dites di probe harian tapi **non-gating** — hasilnya cuma tercatat ke
  bukti/log, tidak pernah mengubah exit code. Ini keputusan sadar yang melonggarkan jaminan PRD
  "vonis pool = vonis crawler" (dulu satu-satunya kriteria lolos adalah `OLX_SEARCH_MARKERS`
  produksi) — proxy tetap terbit selama homepage-nya bersih walau listing masih diredirect.
- **`run_hourly` mengklasifikasi lewat dua langkah URL** (endpoint netral `IP_CHECK_URL` dulu,
  baru `OLX_HOURLY_URL`), bukan lewat daftar exit code curl — daftar kode langsung basi kalau
  Akamai ganti cara menolak. Ini yang memisahkan `connecting` (tunnel mati) dari `inconclusive`
  (tunnel hidup, OLX saja tidak menjawab).
- **Semua job antre di `jobs.JOB_LOCK` lewat `@jobs.serialized`.** Rotasi dan verifikasi
  sama-sama menyentuh Docker + tabel `slots` dari thread berbeda (scheduler vs request Flask).
  Job baru yang menyentuh keduanya wajib ikut didekorasi.
- **`orchestrator.logs()` harus dipanggil sebelum `stop()`** — container yang sudah dihapus tidak
  punya log lagi.
- **Provider `pia-custom` bukan varian `pia`, dan kandidatnya bukan hostname.** Slot ini
  (opsional, `PIA_CUSTOM_SLOTS`, default 0 — **tambahan** di belakang `PIA_SLOTS`/`PROTON_SLOTS`,
  bukan pengganti) jalan di mode `custom` gluetun dengan profil `.ovpn` dari config generator PIA.
  `PIA_CUSTOM_REGIONS` memberi tahu `pia_custom.py` region mana yang cache-nya perlu disegarkan;
  setiap file `.ovpn` ber-`remote` IP unik kemudian menjadi kandidat sendiri. Jadi dua slot boleh
  memakai dua profil Jakarta berbeda dan profile kedua dicoba saat yang pertama gagal/blocked.
  `servers.sh` tidak dipanggil karena ia cuma tahu `pia|proton`. Login + scrape HTML PIA cuma
  boleh ada satu implementasi: `pia_custom.py` shell out ke skrip itu, tidak menulis ulang.
  **`get-pia-ovpn.sh` tinggal di `pool/`, bukan `legacy-ovpn/`, karena `deploy.sh` cuma menyalin
  `pool servers.sh` ke VM** — di luar `pool/` skrip itu tidak pernah sampai ke produksi dan tiap
  rotasi `pia-custom` di sana gagal generate profil. `legacy-ovpn/daily.sh` yang memanggil ke
  `../pool/`, bukan sebaliknya; jangan disalin balik supaya tidak jadi dua implementasi.
  Profil di-cache dan dipakai ulang selama
  `PIA_CUSTOM_PROFILE_MAX_AGE_HOURS` — kalau ini jadi per percobaan kandidat, satu rotasi berarti
  login ke akun PIA sampai `POOL_MAX_TRIES` × jumlah slot kali dalam hitungan menit.
- **Kunci WireGuard Proton wajib satu per slot** (`PROTON_KEY_SLOT_N`); tidak ada fallback ke
  `PROTON_KEY` bersama. Kredensial OpenVPN Proton sebaliknya satu akun untuk semua slot.
- **Provider `nord` (NordVPN, `NORD_SLOTS`, default 0) sengaja kebalikannya: kunci WireGuard
  `NORD_KEY` satu untuk semua slot** — Nord tidak menerbitkan kunci per perangkat, cuma satu
  `nordlynx_private_key` per akun. Kandidat lewat `servers.sh nord` (tabel sama dengan Proton).
- **`next_candidate()` mengacak daftar** — tanpa itu klaster negara pertama secara abjad menghabiskan
  seluruh jatah percobaan tiap rotasi.
- **`pool/probe/olx_probe.py` adalah salinan manual** dari `config/scrape.py` dan `utility/utility.py`
  di repo crawler produksi (`asl-crawler-pricing-engine-v2`), begitu juga instalasi Chrome di
  `pool/probe/Dockerfile`. Tidak ada sinkronisasi otomatis — sitasi baris asal ada di header file.
- **Image probe dikunci `--platform=linux/amd64` di `docker build`, dan sengaja TIDAK diset di
  `docker run`** (Docker Desktop malah gagal mengenali image lokal kalau diset).
- **`/proxies` balas 503 saat kolam kosong**, bukan 200 kosong, supaya `curl -f` konsumen gagal dan
  `proxies.txt` lama tidak tertimpa.

## Deploy (Fase 3)

`pool/deploy/` berisi artefak untuk VM `asl-prd-prod-crawler-proxy-1` (akses `tsh ssh`).
Semuanya **dijalankan manual oleh user** — `deploy.sh` menyalin kode, kredensial disalin sendiri,
`crawler-prod-cronjob.yaml` sengaja belum pernah diterapkan (keputusan PRD: jalan berdampingan
dengan `vpn-pia`/`vpn-proton` dulu). VM cuma 1.9 GB RAM tanpa swap → dikonfigurasi 3 slot, bukan 6.
