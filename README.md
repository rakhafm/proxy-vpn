# proxy-vpn

Ambil exit IP dari PIA (OpenVPN) atau Proton VPN (WireGuard), jalankan lewat gluetun, lalu
uji URL target dari tiap IP dan catat mana yang lolos dan mana yang diblokir.

Dibuat untuk satu pertanyaan konkret: **exit IP mana yang hari ini masih bisa membuka OLX
tanpa kena blokir bot.** Jawabannya berubah dari hari ke hari, jadi alurnya dibuat berulang.

Butuh: `bash`, `curl`, `awk`, `python3`, Docker, Google Chrome, dan akun PIA dan/atau
Proton VPN aktif.

## Alur cepat

Isi kredensial sesuai provider yang mau dipakai:

```bash
printf '%s\n%s\n' 'pXXXXXXX' 'passwordnya' > .pia-credentials && chmod 600 .pia-credentials
echo 'PROTON_KEY=isi-private-key-nya' > .env && chmod 600 .env
```

Lalu sapu, dan jalankan yang lolos:

```bash
./servers.sh pia sea | xargs ./sweep.sh pia
./run-clean.sh pia --pool 3
```

Satu sapuan Asia Tenggara ~25 menit (28 server Proton / 16 server PIA).

## Isi

Jalur aktif — provider bawaan gluetun, tanpa file profil:

| File | Guna |
|---|---|
| [servers.sh](servers.sh) | daftar server dari provider bawaan gluetun (tanpa login) |
| [sweep.sh](sweep.sh) | sapu server, tulis `hasil-<provider>.csv` |
| [run-clean.sh](run-clean.sh) | jalankan gluetun persisten memakai server yang lolos di sweep terakhir |
| [docker-compose.gluetun.yml](docker-compose.gluetun.yml) | gluetun provider `protonvpn` atau `private internet access` |
| [probe.js](probe.js) | buka URL via Chrome, simpan HTML + screenshot, klasifikasi blokir |
| [test-proxy.sh](test-proxy.sh) | cek cepat: IP berubah? URL kena challenge? |
| [shot.sh](shot.sh) | screenshot satu halaman lewat proxy |

Pendukung:

| Isi | Guna |
|---|---|
| [legacy-ovpn/](legacy-ovpn/README.md) | jalur lama `.ovpn` tulis-sendiri; masih dipakai `vpn-pia` di prod |
| [PRD-proxy-pool.html](PRD-proxy-pool.html) | rancangan layanan pool manager yang menggantikan alur manual ini |
| [pool/](pool/README.md) | pool manager, Fase 1: slot, orkestrasi Docker, kedua job, SQLite, `GET /proxies` |
| `hasil-pia.csv`, `hasil-proton.csv` | hasil sapuan per provider |
| `probe-out/` | HTML + screenshot tiap probe, sebagai bukti |
| `servers-pia.txt`, `servers-proton.txt` | cache daftar server; regenerable lewat `servers.sh -r` |

## Jalur lama: profil `.ovpn` tulis-sendiri

Pindah ke [legacy-ovpn/](legacy-ovpn/README.md) — `sweep-asia.sh`, `daily.sh`, dan
`docker-compose.yml` mode `custom`. Generatornya sendiri, [`pool/get-pia-ovpn.sh`](pool/get-pia-ovpn.sh),
tinggal di `pool/` supaya ikut ter-deploy ke VM; jalur ini memanggilnya lewat `..`.
Digantikan jalur provider bawaan di bawah,
tapi tetap disimpan karena `vpn-pia` di `crawler-prod` masih memakai `custom.conf`, dan karena
daftar server bawaan gluetun bisa sewaktu-waktu basi.

## Provider bawaan gluetun (Proton & PIA)

Jalur ini tidak men-download file config sama sekali. gluetun sudah menyimpan daftar server
dan kunci tiap server di dalam image-nya, jadi yang perlu disediakan cuma kredensial akun.
Jauh lebih ringkas dan lebih tahan banting daripada jalur `get-pia-ovpn.sh` yang harus
meng-grep HTML halaman PIA.

Dua provider dipakai lewat script yang sama, dibedakan argumen pertama:

| Provider | Argumen | Protokol | Butuh |
|---|---|---|---|
| Proton VPN | `proton` | WireGuard | `PROTON_KEY` di `.env` |
| Private Internet Access | `pia` | OpenVPN | `.pia-credentials` |

### Kredensial

**Proton** — buka [halaman WireGuard Proton](https://account.proton.me/u/2/vpn/WireGuard),
beri nama konfigurasi, pilih server mana pun, klik **Buat**, lalu salin nilai `PrivateKey`:

```bash
echo 'PROTON_KEY=isi-private-key-nya' > .env && chmod 600 .env
```

Private key hanya ditampilkan sekali saat pembuatan. Kunci itu terikat ke akun, bukan ke
server, jadi **satu kunci melayani semua exit** — tidak perlu satu config per server.
Kalau hilang, buat konfigurasi baru dan cabut yang lama.

**PIA** — pakai `.pia-credentials` yang sama seperti jalur OpenVPN (baris 1 user, baris 2
password). Dicari di direktori repo dulu, lalu di `$HOME`, atau tunjuk lewat `PIA_CREDENTIALS`.

### Daftar server

```bash
./servers.sh pia sea              # 16 server PIA di Asia Tenggara
./servers.sh proton sea           # 28 server Proton di Asia Tenggara
./servers.sh proton asia          # 108 server Proton di seluruh Asia
./servers.sh pia -l Singapore     # tabel lengkap
./servers.sh proton -r            # segarkan cache servers-proton.txt
```

Dibaca dari `docker run --rm qmcgaw/gluetun format-servers`, jadi tanpa login. Kolom yang
dicetak adalah pemilih server yang dipakai gluetun: hostname untuk Proton, nama server
untuk PIA. Grup `sea` dan `asia` ditulis tangan di dalam script — gluetun tidak punya kolom
benua, dan penamaan negaranya berbeda antar provider.

### Menyapu

```bash
./servers.sh pia sea | xargs ./sweep.sh pia
```
```bash
./servers.sh proton sea | xargs ./sweep.sh proton
```

Hasil masuk ke `hasil-pia.csv` / `hasil-proton.csv`. Semua file hasil memakai kolom yang sama
(`hasil-ovpn.csv` dari jalur lama juga):

```
waktu, profil, server_ip, ip_publik, negara, org, http_status,
diblokir, alasan, nomor_referensi, byte_html, file_html, file_png
```

`alasan` berisi `akamai-deny`, `akamai-interstitial`, `cloudflare-challenge`,
`vpn-tidak-connect`, atau judul halaman kalau lolos. Pada jalur provider bawaan `server_ip`
dikosongkan, karena server dipilih lewat nama dan bukan lewat IP.

Mulai dari `sea` sebelum `asia`: cukup untuk tahu kredensialnya benar dan seperti apa pola
blokirnya, tanpa menunggu satu setengah jam.

Container-nya bernama `gluetun-native` dan memakai port 8888. Kalau port itu sedang dipakai
container lain, jalankan dengan `PROXY_PORT=8889`. Ganti server berarti ganti env, jadi
script ini memakai `up -d --force-recreate`, bukan `docker restart` seperti
`legacy-ovpn/sweep-asia.sh`.

## Menjalankan server yang sudah terbukti lolos

Setelah `./sweep.sh <proton|pia> ...` menghasilkan `hasil-<provider>.csv`, `run-clean.sh`
membaca baris `diblokir=no` yang paling baru dan menjalankan `gluetun-native` dengan server
itu — tanpa menyapu ulang.

```bash
./run-clean.sh proton              # pin ke 1 server terbersih paling baru
```
```bash
./run-clean.sh pia --pool 3        # beri gluetun 3 kandidat, ia yang memilih
```

**Pendapat saya: jangan andalkan ini lebih dari beberapa jam.** Saya uji barusan — pin ke
`node-vn-01.protonvpn.net` yang tercatat bersih jam 06:01 UTC hari ini, lalu langsung cek
ulang manual: sudah kena `akamai-deny` (5.6 KB, bukan ~1.6 MB). Ini persis temuan "blokir
per-IP dan bergerak dari waktu ke waktu" di bagian Temuan — CSV adalah snapshot, bukan
jaminan. `--pool N` juga tidak menyelesaikan ini: healthcheck gluetun hanya menguji
konektivitas VPN (DNS/TCP ke server umum), bukan apakah OLX memblokir IP-nya, jadi ia tidak
akan pindah kandidat walau exit IP-nya sudah kena deny.

Pola yang lebih realistis: jalankan `./sweep.sh` di pagi hari, langsung
pakai `run-clean.sh` selagi hasilnya masih segar, dan anggap perlu disapu ulang kalau sudah
berjam-jam — bukan sekali sapu lalu jalan terus-menerus tanpa verifikasi ulang.

## Screenshot satu halaman

```bash
./shot.sh https://www.olx.co.id/mobil-bekas_c198 olx.png
```

## Temuan

Hal-hal yang ditemukan lewat percobaan, bukan asumsi. Semuanya menghemat waktu debug kalau
sesuatu tiba-tiba rusak.

**Blokir OLX bersifat per-IP, bukan per-ASN atau per-negara.** Dari 40 profil Asia yang
disapu, ASN yang sama muncul di kedua sisi — GSL Networks 7 lolos / 6 deny, M247 4 lolos /
6 deny, Datacamp 3 lolos / 2 deny. IP dalam satu blok `/24` pun bisa berbeda nasib. Dan
reputasinya bergerak: satu IP SG lolos pukul 22:44, IP tetangganya kena deny pukul 22:54.
Karena itu alurnya harian, bukan sekali pilih lalu selesai.

**Halaman deny tetap HTTP 200.** Kalau kamu hanya memeriksa status code, blokir ini lolos
tanpa terdeteksi. Pembedanya ukuran: ~5.6 KB (halaman "Ada yang tidak beres" plus nomor
referensi Akamai) versus ~1.6 MB untuk halaman listing asli.

**UA headless wajib di-override.** Chrome headless mengirim `HeadlessChrome/...` dan Akamai
me-reset koneksi di lapisan HTTP/2 (`ERR_HTTP2_PROTOCOL_ERROR`, atau curl error 92) sebelum
halaman sempat dimuat. Ini bukan gejala VPN — reproduksinya sama persis tanpa proxy.
Sebaliknya, memalsukan UA Chrome pada `curl` justru memicu reset yang sama, karena
fingerprint TLS-nya tidak cocok. `probe.js` dan `shot.sh` sudah memakai UA yang benar.

**Profil GCM dari PIA memuat `ncp-disable`.** Opsi itu dihapus di OpenVPN 2.6 dan bersifat
fatal, sedangkan gluetun memakai 2.6.20 — 10 profil gagal connect karena ini.
`pool/get-pia-ovpn.sh` sekarang membuangnya saat generate.

**Label cipher pada profil tidak mencerminkan kenyataan.** OpenVPN 2.6 mengabaikan
`--cipher` dan menegosiasi lewat `--data-ciphers`, jadi profil `aes-128-cbc` sebenarnya
berjalan dengan AES-256-GCM. Memilih varian CBC versus GCM di generator tidak berpengaruh
pada enkripsi yang dipakai gluetun.

**Untuk PIA, `VPN_SERVICE_PROVIDER=custom` adalah jalur yang lebih rendah.** gluetun punya
provider PIA native yang mengurus daftar server dan port forwarding sendiri. Mode custom
mematikan itu — dipakai di sini justru karena tujuannya menguji profil per-IP.

**Konten OLX tidak di-geo-gate.** Meski keluar dari Singapura, halaman tetap menampilkan
Jakarta Selatan. VPN di sini berfungsi sebagai sumber IP alternatif, bukan alat mengubah lokasi.

**Hostname Proton TIDAK mengunci exit IP.** Diuji 3x berturut-turut pada
`node-vn-01.protonvpn.net`: entry endpoint berganti (185.159.156.85 lalu 188.214.152.226),
exit IP konsisten 188.214.152.229 — tapi 4 jam sebelumnya hostname yang sama tercatat
exit 159.26.115.123 di `hasil-proton.csv`. Jadi stabil dalam hitungan menit, bergeser dalam
hitungan jam. Konsekuensinya: mencatat "profil yang lolos hari ini" lalu memakainya lagi besok
tidak menjamin apa pun — yang dicatat adalah nama, dan nama itu menunjuk IP yang berbeda nanti.

**Beberapa hostname Proton berbagi satu exit IP.** Pada sweep 2026-08-19, lima hostname
(`node-sg-34`, `node-sg-38`, `node-sg-42`, `node-th-01`, `node-vn-01`) semuanya keluar dari
159.26.115.123 — Proton memakai NAT keluar bersama, dan lokasi seperti Thailand/Vietnam
sebagian dilayani hub Singapura. Artinya jumlah baris `diblokir=no` di CSV **melebih-lebihkan**
keragaman IP: memilih 3 "profil berbeda" bisa berarti 1 exit IP yang sama.

**`curl` saja tidak bisa memastikan sebuah IP bersih.** Tiga hasil yang mungkin, dan hanya
satu yang bisa dibedakan tanpa browser:
| Ukuran | Tanda | Arti |
|---|---|---|
| ~2.2 KB | `bm-verify`, meta-refresh | interstitial Akamai — browser lolos, `curl` berhenti di sini |
| ~5.6 KB | `id="referenceNum"` | deny keras — browser pun ditolak |
| ~1.6 MB | 5/5 marker pipeline | halaman asli, hanya tercapai setelah JS dijalankan |
IP yang bersih pun menjawab `curl` dengan interstitial 2.2 KB dan **0/5 marker**, sama seperti
IP yang diblokir. Jadi `curl` bagus untuk membuang yang pasti buruk (`referenceNum`), tapi
verdict "bersih" butuh browser sungguhan. `probe.js` sudah membedakan keduanya.

**Marker pipeline cocok dengan klasifikasi `probe.js`.** Ketiga HTML yang divonis lolos
(`proton-node-id-03`, `pia-Server-10881-2a`, `proton-my-09`) memuat kelima
`OLX_SEARCH_MARKERS` dari `utility/utility.py:76` — kriteria yang sama dipakai pipeline
produksi, jadi verdict sweep dan verdict crawler tidak berbeda ukuran.

**Server individual PIA bisa mati tanpa hubungannya dengan blokir OLX sama sekali.**
`cambodia401` gagal dengan `TLS key negotiation failed to occur within 20 seconds` di jalur
provider native — server itu sendiri tidak menjawab handshake OpenVPN. `sweep.sh` mencatat
ini sebagai `vpn-tidak-connect`, sama seperti kegagalan `ncp-disable` di jalur manual;
keduanya perlu dibedakan dari `alasan` sebelum menyimpulkan exit IP mana yang "aman".

## Otomatis harian

Alur manual di atas dimaksudkan untuk dijalankan berulang, dan itu tugas yang seharusnya
dipegang layanan, bukan cron di laptop. Rancangannya ada di
[PRD-proxy-pool.html](PRD-proxy-pool.html): pool manager di VM Ubuntu yang merotasi slot tiap
hari, memverifikasi tiap jam, dan menerbitkan daftar proxy yang masih hidup lewat satu URL.
Fase 1 (inti, jalan lokal — belum menyentuh prod) sudah ada di [pool/](pool/README.md).

Sampai itu jalan di VM (Fase 3), cron untuk jalur lama:

```bash
(crontab -l 2>/dev/null; echo "0 7 * * * cd $PWD/legacy-ovpn && ./daily.sh >> ../daily.log 2>&1") | crontab -
```

Exit code `pool/get-pia-ovpn.sh`: **1** salah pakai · **2** login ditolak ·
**3** markup halaman berubah · **4** respons generate bukan file config.

## Catatan rapuh

Token CSRF, daftar region, dan pengelompokan benua diambil dengan grep/awk dari HTML
halaman PIA, bukan API resmi. Kalau PIA mengubah markup, script berhenti dengan pesan jelas
(`authenticity_token ... tidak ketemu`, `daftar region kosong`) — bukan diam-diam salah.
Klasifikasi blokir di `probe.js` juga berbasis pola HTML, jadi perlu disesuaikan kalau OLX
mengganti halaman errornya.
