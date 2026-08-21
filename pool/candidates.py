"""Kandidat server per provider, dibatasi grup `servers.sh` (default: sea).

Lewat `servers.sh <provider> <grup>` apa adanya, bukan mem-parsing ulang cache
`servers-<provider>.txt` sendiri - daftar negara per grup (dan kolom pemilih
yang beda antar provider) sudah didefinisikan sekali di sana; duplikasi di sini
cuma bikin dua tempat bisa bedrift."""
import subprocess
from datetime import datetime, timedelta, timezone

from . import config


def _all_servers(provider):
    script = config.ROOT / "servers.sh"
    r = subprocess.run(
        [str(script), provider, config.CANDIDATE_GROUP],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"servers.sh {provider} {config.CANDIDATE_GROUP} gagal: {r.stderr.strip()}"
        )
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def next_candidate(conn, provider, in_use):
    """Server pertama yang belum gagal BARU-BARU INI dan sedang tidak dipakai
    slot lain. Kegagalan lama (lebih tua dari CANDIDATE_RETRY_HOURS) tidak
    lagi mengecualikan - blokir OLX bergerak per-IP dalam hitungan jam
    (README §Temuan), jadi exclude permanen bikin daftar kandidat SEA habis
    dalam beberapa hari dan tiap rotasi berakhir 'dead'.

    Filter tanggal dilakukan di Python, bukan `datetime('now', ...)` SQLite:
    tried_at disimpan lewat isoformat() (pemisah 'T'), sedangkan datetime()
    SQLite menghasilkan pemisah spasi - keduanya beda hasil kalau
    dibandingkan sebagai string pada tanggal yang sama."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=config.CANDIDATE_RETRY_HOURS)).isoformat()
    failed = {
        row["server"]
        for row in conn.execute(
            "SELECT server FROM candidates WHERE provider=? AND result IN "
            "('blocked','dup_ip','connect_fail','probe_error') AND tried_at > ?",
            (provider, cutoff),
        )
    }
    skip = failed | set(in_use)
    for server in _all_servers(provider):
        if server not in skip:
            return server
    return None


def record(conn, provider, server, slot_id, result):
    conn.execute(
        "INSERT INTO candidates (provider, server, slot_id, result, tried_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(provider, server) DO UPDATE SET slot_id=excluded.slot_id, "
        "result=excluded.result, tried_at=excluded.tried_at",
        (provider, server, slot_id, result, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
