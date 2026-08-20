import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

PORT_BASE = int(os.environ.get("POOL_PORT_BASE", "9000"))
PIA_SLOTS = int(os.environ.get("PIA_SLOTS", "3"))
PROTON_SLOTS = int(os.environ.get("PROTON_SLOTS", "3"))

# [(slot_id, provider, port), ...] - urutan tetap, port = PORT_BASE + n.
SLOT_DEFS = [
    (f"slot-{n}", provider, PORT_BASE + n)
    for n, provider in enumerate(
        ["pia"] * PIA_SLOTS + ["proton"] * PROTON_SLOTS, start=1
    )
]

# Grup yang dikenal servers.sh: sea (Asia Tenggara, default) | asia | nama
# negara/region persis. OLX Indonesia belum terbukti menolak berdasar geografi
# (lihat temuan exit Frankfurt di README), tapi kandidat dibatasi SEA supaya
# tetap sejalan dengan hasil sweep yang sudah teruji (hasil-pia.csv/-proton.csv).
CANDIDATE_GROUP = os.environ.get("POOL_CANDIDATE_GROUP", "sea")

DB_PATH = os.environ.get("POOL_DB", str(ROOT / "pool" / "pool.db"))
API_PORT = int(os.environ.get("POOL_API_PORT", "8080"))
ADVERTISE_HOST = os.environ.get("POOL_ADVERTISE_HOST", "127.0.0.1")

OLX_URL = os.environ.get("OLX_URL", "https://www.olx.co.id/mobil-bekas_c198")
# Image sendiri (pool/probe/Dockerfile), bukan image crawler produksi - tidak
# butuh login Harbor. Chrome-nya sejenis karena instalasinya disalin dari
# Dockerfile crawler; kode vonisnya disalin ke pool/probe/olx_probe.py.
PROBE_IMAGE = os.environ.get("PROBE_IMAGE", "olx-pool-probe:latest")

DAILY_TIME = os.environ.get("POOL_DAILY_TIME", "03:00")  # HH:MM, Asia/Jakarta
HOURLY_REFILL = os.environ.get("HOURLY_REFILL", "0") == "1"
MAX_CANDIDATE_TRIES = int(os.environ.get("POOL_MAX_TRIES", "5"))
HEALTHY_TIMEOUT = int(os.environ.get("POOL_HEALTHY_TIMEOUT", "90"))
# Bukti HTML+screenshot (pool/probe-out/) numpuk ~1.6 MB per percobaan probe
# harian - tanpa batas ini tumbuh tak terbatas. Dipangkas tiap rotasi harian,
# bukan tugas terpisah - satu-satunya jadwal yang sudah pasti jalan tiap hari.
PROBE_OUT_RETAIN_DAYS = int(os.environ.get("PROBE_OUT_RETAIN_DAYS", "14"))


def pia_credentials():
    """(user, pass) dari .pia-credentials, sama seperti run-clean.sh."""
    cred = os.environ.get("PIA_CREDENTIALS")
    if not cred:
        for c in (ROOT / ".pia-credentials", pathlib.Path.home() / ".pia-credentials"):
            if c.is_file():
                cred = str(c)
                break
    if not cred:
        return None, None
    lines = pathlib.Path(cred).read_text().splitlines()
    return (lines[0].strip(), lines[1].strip()) if len(lines) >= 2 else (None, None)


def proton_key(slot_id):
    """Kunci WireGuard per-slot: PROTON_KEY_SLOT_4, dst. Tidak jatuh ke PROTON_KEY
    bersama - satu kunci dipakai banyak slot sekaligus adalah risiko yang sudah
    ditandai di PRD (identitas per-perangkat, bisa membentur/collide)."""
    return os.environ.get(f"PROTON_KEY_{slot_id.upper().replace('-', '_')}")
