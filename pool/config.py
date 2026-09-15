import os
import pathlib
import socket

ROOT = pathlib.Path(__file__).resolve().parent.parent

PORT_BASE = int(os.environ.get("POOL_PORT_BASE", "9000"))
PIA_SLOTS = int(os.environ.get("PIA_SLOTS", "3"))
PROTON_SLOTS = int(os.environ.get("PROTON_SLOTS", "3"))
# Opsional, default 0 (mati) - provider terpisah, bukan pengganti PIA_SLOTS.
# Pakai profil .ovpn hasil download dari config generator PIA (lihat
# pool/pia_custom.py + pool/get-pia-ovpn.sh), bukan server bawaan
# gluetun. Eksperimen 2026-09-03: satu profil begini terbukti lolos sampai
# ke halaman listing (markers penuh) di saat server bawaan gluetun untuk
# region yang sama kena redirect homepage - belum tentu sistematis, jadi
# dijalankan berdampingan supaya bisa dibandingkan, bukan menggantikan.
PIA_CUSTOM_SLOTS = int(os.environ.get("PIA_CUSTOM_SLOTS", "0"))
# Opsional, default 0 (mati) - provider ketiga bawaan gluetun
# (VPN_SERVICE_PROVIDER=nordvpn). Tambahan di belakang slot lain, bukan
# pengganti, supaya exit ASN NordVPN bisa dibandingkan berdampingan dengan
# PIA/Proton (semua kandidat SEA PIA ada di satu ASN Datacamp - lihat
# deploy/proxy-pool.env.example).
NORD_SLOTS = int(os.environ.get("NORD_SLOTS", "0"))

# [(slot_id, provider, port), ...] - urutan tetap, port = PORT_BASE + n.
SLOT_DEFS = [
    (f"slot-{n}", provider, PORT_BASE + n)
    for n, provider in enumerate(
        ["pia"] * PIA_SLOTS + ["proton"] * PROTON_SLOTS + ["pia-custom"] * PIA_CUSTOM_SLOTS
        + ["nord"] * NORD_SLOTS,
        start=1,
    )
]

# Region TETAP untuk provider pia-custom - bukan "all", supaya rotasi harian
# (sampai POOL_MAX_TRIES percobaan per slot) tidak login+generate ke akun PIA
# terlalu sering (risiko rate-limit/flag akun). Kode ini milik
# get-pia-ovpn.sh sendiri (lihat "./pool/get-pia-ovpn.sh -l"), padanan
# kasar dari grup "sea" PIA di servers.sh (Singapore|Indonesia|Malaysia|
# Philippines|Vietnam).
PIA_CUSTOM_REGIONS = [
    r.strip() for r in
    os.environ.get("PIA_CUSTOM_REGIONS", "sg,jakarta,kualalumpur,philippines,vietnam").split(",")
    if r.strip()
]

# Direktori profil .ovpn hasil download - MILIK pool sendiri, sengaja terpisah
# dari legacy-ovpn/vpn-profile/ supaya kebijakan refresh di sini (lihat
# PIA_CUSTOM_PROFILE_MAX_AGE_HOURS) tidak tercampur dengan file yang disimpan
# manual (bersuffix tanggal, retensi KEEP_DAYS punya daily.sh) di sana.
PIA_CUSTOM_PROFILE_DIR = os.environ.get(
    "PIA_CUSTOM_PROFILE_DIR", str(ROOT / "pool" / "vpn-profile")
)

# Profil dipakai ulang sampai selama ini sebelum didownload ulang - SINKRON
# dengan siklus rotasi harian, bukan sekali per percobaan kandidat (bisa
# sampai POOL_MAX_TRIES x tiap slot pia-custom per rotasi) - itu berarti
# login ke akun PIA berkali-kali dalam hitungan menit tiap hari.
PIA_CUSTOM_PROFILE_MAX_AGE_HOURS = int(os.environ.get("PIA_CUSTOM_PROFILE_MAX_AGE_HOURS", "12"))

# Satu atau lebih grup dikenal servers.sh, dipisah koma: sea (Asia Tenggara,
# default) | asia | nama negara/region persis (mis. "China", "JP Tokyo" untuk
# pia). OLX Indonesia belum terbukti menolak berdasar geografi (lihat temuan
# exit Frankfurt di README), tapi kandidat dibatasi SEA(+) supaya tetap
# sejalan dengan hasil sweep yang sudah teruji (hasil-pia.csv/-proton.csv).
CANDIDATE_GROUP = os.environ.get("POOL_CANDIDATE_GROUP", "sea")

DB_PATH = os.environ.get("POOL_DB", str(ROOT / "pool" / "pool.db"))
API_PORT = int(os.environ.get("POOL_API_PORT", "8080"))
ADVERTISE_HOST = os.environ.get("POOL_ADVERTISE_HOST", "127.0.0.1")

# URL yang menggerbang vonis probe HARIAN (browser sungguhan). Sejak
# 2026-09-03 ini root domain, BUKAN halaman listing: OLX/Akamai terbukti
# membalas mobil-bekas_c198 dengan redirect diam-diam ke homepage (200,
# byte penuh, 0/5 OLX_SEARCH_MARKERS - bukan referenceNum) - vonis 'blocked'
# yang sebelumnya jatuh di sini akhirnya mencoret exit yang homepage-nya
# sendiri bersih. Root domain dipakai sebagai penanda tunggal yang sama
# dengan cek per-jam (lihat OLX_HOURLY_URL) supaya kedua job konsisten.
# Keputusan sadar: ini melonggarkan jaminan PRD "vonis pool = vonis
# crawler" (OLX_SEARCH_MARKERS produksi) - slot bisa 'active' walau
# mobil-bekas_c198 masih diblokir. Trade-off yang diambil: proxy tetap
# terbit selama homepage-nya bersih; cek listing dipindah jadi validasi
# non-gating, lihat OLX_VALIDATE_URL.
OLX_URL = os.environ.get("OLX_URL", "https://www.olx.co.id/")

# URL listing untuk VALIDASI SAJA di probe harian - dicatat ke bukti/log,
# TIDAK dipakai menjatuhkan atau meluluskan slot (lihat olx_probe.py). Kalau
# ini konsisten gagal sementara OLX_URL bersih, itu tandanya redirect diam
# Akamai di atas masih berlangsung untuk path pencarian.
OLX_VALIDATE_URL = os.environ.get("OLX_VALIDATE_URL", "https://www.olx.co.id/mobil-bekas_c198")

# URL yang dipakai probe PER JAM (curl). Root domain sejak 2026-09-02: Akamai
# me-reset stream HTTP/2 untuk `curl` di path pencarian (exit 92) bahkan pada
# exit IP yang probe browser buktikan bersih beberapa menit sebelumnya -
# 17/21 vonis 'connecting' di proxy-1 dan 20/29 di proxy-2 ternyata false
# negative jenis ini. Root domain lewat proxy yang sama membalas normal
# (586 KB). Halaman deny Akamai bersifat per-IP, jadi kalau exit-nya
# benar-benar ditolak, root domain pun ikut membawa referenceNum - vonis
# 'blocked' tidak hilang ketajamannya. Memaksa --http1.1 BUKAN obatnya: dites,
# malah timeout 5/5.
OLX_HOURLY_URL = os.environ.get("OLX_HOURLY_URL", "https://www.olx.co.id/")

# Endpoint netral untuk memastikan tunnel benar-benar hidup sebelum kegagalan
# apa pun terhadap OLX ditafsirkan. Teks polos (satu baris IP), tidak lewat
# CDN anti-bot, jadi kegagalan di sini benar-benar berarti tunnelnya mati.
# ipinfo.io tetap dipakai orchestrator.exit_info() saat rotasi - di sana yang
# dibutuhkan negara + ASN untuk halaman pantau, dan ifconfig.me tidak
# menyediakan keduanya.
IP_CHECK_URL = os.environ.get("IP_CHECK_URL", "https://ifconfig.me/ip")

# CSV daftar URL untuk job "Cek URL" (kolom: name,url) - tiap baris dites
# lewat SETIAP slot aktif dengan Chrome (pool/probe/url_check.py), hasilnya
# tabel di halaman pantau. Non-gating: tidak pernah mengubah status slot.
CHECKS_CSV = os.environ.get("POOL_CHECKS_CSV", str(ROOT / "pool" / "checks.csv"))

# Image sendiri (pool/probe/Dockerfile), bukan image crawler produksi - tidak
# butuh login Harbor. Chrome-nya sejenis karena instalasinya disalin dari
# Dockerfile crawler; kode vonisnya disalin ke pool/probe/olx_probe.py.
PROBE_IMAGE = os.environ.get("PROBE_IMAGE", "olx-pool-probe:latest")

# Berapa jam server yang gagal/blocked dikeluarkan dari kandidat sebelum
# dicoba lagi - lihat candidates.next_candidate().
CANDIDATE_RETRY_HOURS = int(os.environ.get("POOL_CANDIDATE_RETRY_HOURS", "24"))

# Webhook Discord (Server Settings -> Integrations -> Webhooks -> New Webhook,
# salin URL-nya) buat notifikasi kolam tidak penuh (sebagian atau semua slot
# mati). Kosong = notifikasi dimatikan.
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# Nama mesin yang dicantumkan di tiap notifikasi Discord. Satu webhook dipakai
# beberapa pool manager sekaligus (proxy-1, proxy-2, dan mesin dev yang
# kebetulan jalan) - tanpa ini pesan "kolam kosong" tidak bisa dilacak ke VM
# mana, dan dua VM sehat terlihat seperti satu VM yang mengirim dobel.
# POOL_HOSTNAME menimpanya kalau hostname mesin tidak informatif.
NOTIFY_HOSTNAME = os.environ.get("POOL_HOSTNAME") or socket.gethostname()

DAILY_TIME = os.environ.get("POOL_DAILY_TIME", "03:00")  # HH:MM, Asia/Jakarta
HOURLY_REFILL = os.environ.get("HOURLY_REFILL", "0") == "1"
MAX_CANDIDATE_TRIES = int(os.environ.get("POOL_MAX_TRIES", "5"))
HEALTHY_TIMEOUT = int(os.environ.get("POOL_HEALTHY_TIMEOUT", "90"))

# Berapa kali probe per jam harus gagal BERTURUT-TURUT sebelum slot dicoret
# dari daftar terbit. 1 = perilaku lama (jatuh di kegagalan pertama). Hanya
# berlaku untuk vonis 'connecting' - probe yang tidak dapat jawaban bukan
# bukti; 'blocked' (referenceNum benar-benar terbaca) tetap menjatuhkan slot
# seketika, karena itu bukti langsung tentang exit IP-nya.
FAIL_STREAK_LIMIT = int(os.environ.get("POOL_FAIL_STREAK_LIMIT", "3"))

# Alat untuk langkah OLX di verifikasi per jam (tombol "Verifikasi sekarang"
# dan job terjadwal tiap jam): 'chrome' (default) = probe browser yang sama
# dengan rotasi (probes.run_daily, bukti html+png ikut tersimpan), 'curl' =
# perilaku lama. Langkah tunnel (IP_CHECK_URL) tetap curl di kedua mode -
# itu yang memisahkan 'connecting' dari 'inconclusive'. Kegagalan probe
# Chrome (exit 2) jatuh ke 'inconclusive', bukan 'connecting': tunnelnya
# sudah terbukti hidup di langkah sebelumnya. Biaya: ~1 menit + ~500 MB per
# slot aktif tiap jam - recheck_stuck (tiap 5 menit) SENGAJA tetap curl.
VERIFY_MODE = os.environ.get("POOL_VERIFY_MODE", "chrome")

# Selang cek ulang khusus slot yang TIDAK aktif, detik. Jauh lebih pendek dari
# siklus per jam: slot 'connecting' tidak menerbitkan apa pun, jadi menunggu
# sampai 59 menit untuk tahu ia sudah pulih itu kerugian tanpa imbalan.
RECHECK_SECONDS = int(os.environ.get("POOL_RECHECK_SECONDS", "300"))

# Rotasi otomatis slot yang macet 'connecting' setelah menembus
# FAIL_STREAK_LIMIT. Default menyala - slot 'connecting' sudah tidak
# diterbitkan, jadi merotasinya tidak bisa memperburuk keadaan, dan tanpa ini
# satu-satunya jalan keluar adalah rotasi manual. Beda dari HOURLY_REFILL,
# yang mengatur rotasi slot 'blocked' (masih default mati - di sana rotasi
# membuang exit yang mungkin pulih sendiri saat reputasinya bergeser).
ROTATE_STUCK = os.environ.get("POOL_ROTATE_STUCK", "1") == "1"
# Bukti HTML+screenshot (pool/probe-out/) numpuk ~1.6 MB per percobaan probe
# harian - tanpa batas ini tumbuh tak terbatas. Dipangkas tiap rotasi harian,
# bukan tugas terpisah - satu-satunya jadwal yang sudah pasti jalan tiap hari.
PROBE_OUT_RETAIN_DAYS = int(os.environ.get("PROBE_OUT_RETAIN_DAYS", "14"))

# Eksperimen: banyak rotasi (PIA & Proton, OpenVPN & WireGuard) berakhir
# ERR_HTTP2_PROTOCOL_ERROR dari Chrome saat probe (ditemukan dari bukti
# probe-out 2026-08-24, tersebar acak lintas puluhan server & dua provider -
# pola fragmentasi tunnel, bukan blokir OLX per-IP). Wiki gluetun menyebut
# WIREGUARD_MTU/OPENVPN_MSSFIX sebagai obat untuk gejala koneksi semacam ini.
# Kosongkan (string kosong) untuk pakai default gluetun apa adanya.
WIREGUARD_MTU = os.environ.get("WIREGUARD_MTU", "1280")
OPENVPN_MSSFIX = os.environ.get("OPENVPN_MSSFIX", "1280")

# Proton bisa jalan lewat WireGuard (default, kunci per-slot - lihat
# proton_key()) atau OpenVPN (kredensial akun dibagi semua slot Proton, lihat
# proton_credentials()). Default tetap wireguard supaya slot yang sudah jalan
# tidak berubah perilaku diam-diam kalau env ini tidak diset.
PROTON_VPN_TYPE = os.environ.get("PROTON_VPN_TYPE", "wireguard")

# NordVPN juga bisa wireguard (default, NordLynx) atau openvpn. Beda dari
# Proton: kedua kredensialnya SATU per akun, dibagi semua slot nord - Nord
# tidak menerbitkan kunci WireGuard per perangkat (satu kunci diturunkan dari
# access token akun), dan kredensial OpenVPN-nya adalah "service credentials"
# akun. Jadi tidak ada NORD_KEY_SLOT_N; yang membatasi jumlah slot adalah
# kuota perangkat akun Nord (default 10), bukan jumlah kunci.
NORD_VPN_TYPE = os.environ.get("NORD_VPN_TYPE", "wireguard")


def _credentials_from_file(env_var, filename):
    """(user, pass) dari file dua baris (user lalu pass) - path dicari lewat
    env_var dulu, lalu <filename> di root repo, lalu $HOME. Dipakai
    pia_credentials() dan proton_credentials(): dua provider, format
    kredensial yang identik, jadi satu implementasi bukan dua yang bisa
    bedrift."""
    cred = os.environ.get(env_var)
    if not cred:
        for c in (ROOT / filename, pathlib.Path.home() / filename):
            if c.is_file():
                cred = str(c)
                break
    if not cred:
        return None, None
    lines = pathlib.Path(cred).read_text().splitlines()
    return (lines[0].strip(), lines[1].strip()) if len(lines) >= 2 else (None, None)


def pia_credentials():
    """(user, pass) dari .pia-credentials, sama seperti run-clean.sh."""
    return _credentials_from_file("PIA_CREDENTIALS", ".pia-credentials")


def proton_credentials():
    """(user, pass) OpenVPN Proton dari .proton-credentials - kredensial
    "OpenVPN/IKEv2" khusus di akun Proton (account.proton.me/u/2/account-
    password), BUKAN login akun biasa. Dipakai kalau PROTON_VPN_TYPE=openvpn;
    satu akun dipakai semua slot Proton openvpn sekaligus - beda dari kunci
    WireGuard yang wajib per-slot (proton_key(), identitas per-perangkat)."""
    return _credentials_from_file("PROTON_CREDENTIALS", ".proton-credentials")


def proton_key(slot_id):
    """Kunci WireGuard per-slot: PROTON_KEY_SLOT_4, dst. Tidak jatuh ke PROTON_KEY
    bersama - satu kunci dipakai banyak slot sekaligus adalah risiko yang sudah
    ditandai di PRD (identitas per-perangkat, bisa membentur/collide)."""
    return os.environ.get(f"PROTON_KEY_{slot_id.upper().replace('-', '_')}")


def nord_credentials():
    """(user, pass) OpenVPN NordVPN dari .nord-credentials - "service
    credentials" dari https://my.nordaccount.com/dashboard/nordvpn/manual-
    configuration/service-credentials/ (BUKAN email+password akun). Dipakai
    kalau NORD_VPN_TYPE=openvpn; satu akun untuk semua slot nord."""
    return _credentials_from_file("NORD_CREDENTIALS", ".nord-credentials")


def nord_key():
    """Kunci WireGuard (NordLynx) akun Nord, satu untuk semua slot nord - dari
    env NORD_KEY. Diambil lewat access token akun (Manual setup -> Set up
    NordVPN manually -> generate token, lalu
    curl -u token:<TOKEN> https://api.nordvpn.com/v1/users/services/credentials
    dan salin field nordlynx_private_key). Sengaja bukan per slot seperti
    Proton: Nord memang cuma punya satu kunci per akun."""
    return os.environ.get("NORD_KEY")
