import logging
import time
from datetime import datetime, timezone

from . import candidates, config, orchestrator, probes

log = logging.getLogger("pool.jobs")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _prune_probe_out(retain_days):
    """Hapus bukti HTML/PNG lebih tua dari retain_days - dipanggil di rotasi
    harian, bukan job terpisah, supaya tetap jalan tanpa jadwal tambahan.
    Hitung path sendiri dari config.ROOT (bukan probes.OUT_DIR, yang di-cache
    saat import dan tidak ikut berubah kalau config.ROOT di-monkeypatch)."""
    out_dir = config.ROOT / "pool" / "probe-out"
    cutoff = time.time() - retain_days * 86400
    if not out_dir.is_dir():
        return
    removed = 0
    for f in out_dir.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError as e:
            log.warning("gagal hapus bukti lama %s: %s", f, e)
    if removed:
        log.info("prune probe-out: %d file lebih tua dari %d hari dihapus", removed, retain_days)


def _log_probe(conn, slot_id, kind, verdict, detail):
    conn.execute(
        "INSERT INTO probes (slot_id, kind, verdict, detail, at) VALUES (?,?,?,?,?)",
        (slot_id, kind, verdict, detail, _now()),
    )
    conn.commit()


def _active_ips(conn):
    return {
        row["exit_ip"]
        for row in conn.execute("SELECT exit_ip FROM slots WHERE status='active' AND exit_ip IS NOT NULL")
    }


def _active_servers(conn, provider):
    return {
        row["server"]
        for row in conn.execute(
            "SELECT server FROM slots WHERE status='active' AND provider=? AND server IS NOT NULL",
            (provider,),
        )
    }


def rotate_slot(conn, slot, pia_user, pia_pass):
    """Coba pasang kandidat baru ke satu slot sampai lolos atau kehabisan
    percobaan. Slot lama (kalau ada) dimatikan lebih dulu."""
    orchestrator.stop(slot["id"])
    in_use = _active_servers(conn, slot["provider"]) | _active_ips(conn)

    for attempt in range(1, config.MAX_CANDIDATE_TRIES + 1):
        server = candidates.next_candidate(conn, slot["provider"], in_use)
        if server is None:
            log.warning("slot %s: kandidat %s habis", slot["id"], slot["provider"])
            break

        proton_key = config.proton_key(slot["id"]) if slot["provider"] == "proton" else None
        try:
            orchestrator.start(
                slot["id"], slot["port"], slot["provider"], server,
                pia_user=pia_user, pia_pass=pia_pass, proton_key=proton_key,
            )
        except Exception as e:
            log.error("slot %s: docker run gagal untuk %s: %s", slot["id"], server, e)
            candidates.record(conn, slot["provider"], server, slot["id"], "connect_fail")
            _log_probe(conn, slot["id"], "daily", "connect_fail", f"{server} docker run gagal: {e}")
            continue

        if not orchestrator.wait_healthy(slot["id"], config.HEALTHY_TIMEOUT):
            # Ambil log SEBELUM stop() - container yang sudah dihapus tidak
            # bisa di-`docker logs` lagi, dan ini satu-satunya kesempatan
            # untuk tahu gluetun macet di step mana (auth ditolak, remote
            # timeout, dst) alih-alih cuma tahu "tidak healthy".
            gluetun_log = orchestrator.logs(slot["id"])
            candidates.record(conn, slot["provider"], server, slot["id"], "connect_fail")
            _log_probe(
                conn, slot["id"], "daily", "connect_fail",
                f"{server} tidak healthy dalam {config.HEALTHY_TIMEOUT}s\n{gluetun_log[-1500:]}",
            )
            orchestrator.stop(slot["id"])
            continue

        ip, negara, org = orchestrator.exit_info(slot["port"])
        if not ip or ip in in_use:
            reason = "exit IP dipakai slot lain" if ip else "exit IP tidak terbaca lewat proxy"
            candidates.record(conn, slot["provider"], server, slot["id"], "dup_ip")
            _log_probe(conn, slot["id"], "daily", "dup_ip", f"{server} {ip or '-'} {reason}")
            orchestrator.stop(slot["id"])
            continue

        verdict, detail = probes.run_daily(slot["port"], slot["id"])
        _log_probe(conn, slot["id"], "daily", verdict, f"{server} {ip} {detail}")

        if verdict == "ok":
            candidates.record(conn, slot["provider"], server, slot["id"], "ok")
            conn.execute(
                "UPDATE slots SET server=?, exit_ip=?, negara=?, org=?, status='active', "
                "last_ok_at=? WHERE id=?",
                (server, ip, negara, org, _now(), slot["id"]),
            )
            conn.commit()
            log.info("slot %s: aktif di %s (%s), percobaan %d", slot["id"], server, ip, attempt)
            return True

        # 'blocked' = Akamai beneran menolak (halaman dimuat, nol marker) - itu
        # sinyal tentang IP-nya. 'error' = probe gagal sebelum sempat menilai
        # (Chrome crash, proxy putus) - bukan sinyal tentang IP sama sekali,
        # jadi dicatat terpisah ('probe_error') supaya /probes dan /candidates
        # tidak salah mencatat "diblokir" untuk kegagalan yang sumbernya
        # tooling probe sendiri, bukan reputasi exit-nya.
        result = "blocked" if verdict == "blocked" else "probe_error"
        candidates.record(conn, slot["provider"], server, slot["id"], result)
        orchestrator.stop(slot["id"])

    conn.execute("UPDATE slots SET status='dead' WHERE id=?", (slot["id"],))
    conn.commit()
    _log_probe(conn, slot["id"], "daily", "error", "kandidat habis atau semua gagal")
    log.warning("slot %s: dead setelah %d percobaan", slot["id"], config.MAX_CANDIDATE_TRIES)
    return False


def rotate_daily(conn):
    _prune_probe_out(config.PROBE_OUT_RETAIN_DAYS)
    pia_user, pia_pass = config.pia_credentials()
    slots = conn.execute("SELECT * FROM slots ORDER BY id").fetchall()
    for slot in slots:
        if slot["provider"] == "pia" and not pia_user:
            log.warning("slot %s: .pia-credentials tidak ada, dilewati", slot["id"])
            continue
        if slot["provider"] == "proton" and not config.proton_key(slot["id"]):
            log.warning("slot %s: PROTON_KEY_%s tidak diset, dilewati", slot["id"], slot["id"].upper())
            continue
        rotate_slot(conn, slot, pia_user, pia_pass)


def verify_hourly(conn):
    slots = conn.execute("SELECT * FROM slots WHERE status IN ('active','connecting')").fetchall()
    pia_user, pia_pass = config.pia_credentials()
    for slot in slots:
        verdict, detail = probes.run_hourly(slot["port"])
        _log_probe(conn, slot["id"], "hourly", verdict, detail)

        if verdict == "ok":
            conn.execute("UPDATE slots SET status='active', last_ok_at=? WHERE id=?", (_now(), slot["id"]))
            conn.commit()
        elif verdict == "blocked":
            conn.execute("UPDATE slots SET status='blocked' WHERE id=?", (slot["id"],))
            conn.commit()
            log.info("slot %s: diblokir, dikeluarkan dari daftar terbit", slot["id"])
            if config.HOURLY_REFILL:
                rotate_slot(conn, slot, pia_user, pia_pass)
        else:  # connecting
            conn.execute("UPDATE slots SET status='connecting' WHERE id=?", (slot["id"],))
            conn.commit()
