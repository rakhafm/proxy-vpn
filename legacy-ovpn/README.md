# legacy-ovpn — jalur profil `.ovpn` buatan sendiri

Jalur ini memakai `VPN_SERVICE_PROVIDER=custom`: profil di-download dari config generator PIA,
lalu disuapkan ke gluetun sebagai file. Digantikan oleh jalur provider bawaan di root repo
(`servers.sh` + `sweep.sh` + `docker-compose.gluetun.yml`), yang tidak butuh file profil sama
sekali karena daftar server sudah tertanam di image gluetun.

**Tetap disimpan, bukan dibuang**, karena dua alasan:

- `vpn-pia` di namespace `crawler-prod` masih memakai `custom.conf` di ConfigMap.
  `get-pia-ovpn.sh` adalah cara membuat penggantinya kalau endpoint-nya mati.
- Provider bawaan gluetun kadang membawa daftar server yang sudah basi. Kalau itu terjadi,
  profil tulis-sendiri adalah jalan keluarnya.

| File | Guna |
|---|---|
| [../pool/get-pia-ovpn.sh](../pool/get-pia-ovpn.sh) | download profil `.ovpn` dari generator PIA (login otomatis) — **pindah ke `pool/`**, lihat di bawah |
| [docker-compose.yml](docker-compose.yml) | gluetun mode `custom`, membaca satu file profil |
| [sweep-asia.sh](sweep-asia.sh) | sapu banyak profil dengan menukar file yang di-mount |
| [daily.sh](daily.sh) | rantai harian: generate → nyalakan → sapu → laporkan |
| `vpn-profile/` | hasil download; regenerable, aman dihapus |

Semua skrip di sini bisa dijalankan dari mana saja — masing-masing `cd` ke direktorinya
sendiri dulu. Yang di luar direktori ini dirujuk lewat `..`: `probe.js`, `probe-out/`,
`hasil-ovpn.csv`, dan `.pia-credentials` semuanya ada di root repo.

```bash
PIA_OUT="$PWD/vpn-profile" ../pool/get-pia-ovpn.sh -t udp --dedup-ip asia   # bersuffix tanggal
PROFILE=sg-aes-128-cbc-udp-ip-20260819.ovpn docker compose up -d
./daily.sh                                   # rantai lengkap
```

Exit code `get-pia-ovpn.sh`: **1** salah pakai · **2** login ditolak · **3** markup halaman
berubah · **4** respons generate bukan file config.

## `get-pia-ovpn.sh` sekarang ada di `pool/`

Generator itu **satu-satunya** implementasi login + scrape HTML PIA, dan `pool/` juga
membutuhkannya (provider `pia-custom`). Ia dipindah ke sana, bukan disalin, karena
`pool/deploy/deploy.sh` hanya menyalin `pool servers.sh` ke VM produksi — selama skrip itu
tinggal di sini, ia tidak pernah sampai ke VM dan rotasi `pia-custom` di produksi selalu gagal
generate profil.

Jalur lama tidak berubah perilakunya: `daily.sh` memanggil `../pool/get-pia-ovpn.sh` dengan
`PIA_OUT` absolut ke `legacy-ovpn/vpn-profile/`, jadi hasilnya tetap mendarat di sini. Kalau
memanggil manual, ikut sertakan `PIA_OUT` juga — tanpa itu skrip menulis ke `pool/vpn-profile/`
(direktori kerja default-nya sekarang), yang punya kebijakan retensi berbeda.
