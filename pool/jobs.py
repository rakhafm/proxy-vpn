import functools
import logging
import threading
import time
from datetime import datetime, timezone

import requests

from . import candidates, config, orchestrator, pia_custom, probes

log = logging.getLogger("pool.jobs")

# Rotasi dan verifikasi sama-sama menyalakan/mematikan container dan menulis
# tabel slots, tapi berjalan di thread berbeda: scheduler.py punya loop
# sendiri, sedangkan tombol di halaman pantau memanggilnya di thread request
# Flask. Tanpa lock keduanya bisa tumpang tindih - terbukti di proxy-1, dua
# probe per jam mencatat "curl exit 7: Failed to connect to port 9003 after
# 0 ms" tepat saat rotasi sedang di antara dua kandidat, lalu slotnya ditandai
# 'connecting' padahal cuma kebetulan tertembak saat containernya mati.
# RLock, bukan Lock: verify_hourly memanggil rotate_slot di dalam dirinya.
JOB_LOCK = threading.RLock()


def serialized(fn):
    """Jalankan satu job pada satu waktu. Menunggu, bukan melewati: job yang
    dilewati diam-diam lebih membingungkan daripada job yang telat."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not JOB_LOCK.acquire(blocking=False):
            log.info("%s: menunggu job lain selesai", fn.__name__)
            JOB_LOCK.acquire()
        try:
            return fn(*args, **kwargs)
        finally:
            JOB_LOCK.release()
    return wrapper


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


def _notify_pool_state(conn):
    """Kirim pesan Discord tiap kali dipanggil, laporan status kolam apa
    adanya (sehat, degradasi, atau kosong) - dipanggil di akhir tiap job,
    supaya ada konfirmasi rutin bukan cuma peringatan saat ada yang mati.
    ponytail: tanpa dedupe - tiap job kirim ulang meski status sama persis
    dengan panggilan sebelumnya. Tambah state 'sudah dinotif' kalau ini
    jadi berisik di praktiknya."""
    if not config.DISCORD_WEBHOOK_URL:
        return
    total = conn.execute("SELECT COUNT(*) n FROM slots").fetchone()["n"]
    active = conn.execute("SELECT COUNT(*) n FROM slots WHERE status='active'").fetchone()["n"]
    # Hostname mesin pengirim, bukan server VPN slot: satu webhook dipakai
    # beberapa pool manager (proxy-1, proxy-2, dev), dan tanpa penanda ini
    # pesannya identik. Backtick = blok kode di Discord, biar gampang dipindai.
    host = f"`{config.NOTIFY_HOSTNAME}`"
    if active == total:
        content = f"✅ {host} OLX proxy pool sehat - {active}/{total} slot aktif."
    elif active == 0:
        content = f"⚠️ {host} OLX proxy pool kosong - tidak ada slot aktif."
    else:
        content = f"⚠️ {host} OLX proxy pool degradasi - {active}/{total} slot aktif."
    try:
        requests.post(config.DISCORD_WEBHOOK_URL, json={"content": content}, timeout=10)
    except requests.RequestException as e:
        log.warning("gagal kirim notifikasi Discord: %s", e)


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
    # Tarik slot dari daftar terbit SEBELUM container lama dihentikan. Selain
    # mencegah /proxies menunjuk port yang sudah mati selama rotasi, ini
    # memastikan kandidat milik slot ini sendiri tidak ikut dihitung sebagai
    # `in_use`. Tanpa transisi ini, pool dengan satu kandidat (mis. cuma
    # pia-custom region jakarta) tidak pernah bisa dirotasi saat statusnya
    # masih active: jakarta dikecualikan oleh barisnya sendiri, lalu slot
    # jatuh dead dengan "kandidat habis".
    conn.execute("UPDATE slots SET status='connecting' WHERE id=?", (slot["id"],))
    conn.commit()
    orchestrator.stop(slot["id"])
    in_use = _active_servers(conn, slot["provider"]) | _active_ips(conn)

    # Kredensial openvpn Proton itu satu akun dibagi semua slot (seperti PIA),
    # bukan per-slot seperti kunci WireGuard - aman dibaca ulang tiap rotasi,
    # filenya cuma dua baris.
    proton_user = proton_pass = None
    if slot["provider"] == "proton" and config.PROTON_VPN_TYPE == "openvpn":
        proton_user, proton_pass = config.proton_credentials()

    # Untuk pia-custom, kandidat bukan region (jakarta) melainkan nama file
    # profile cache. Cache yang punya remote IP sama disaring oleh
    # ensure_profiles(), sehingga dua slot bisa memakai dua endpoint unik dari
    # Jakarta dan kegagalan satu profile dapat lanjut ke profile berikutnya.
    custom_profiles = {}
    if slot["provider"] == "pia-custom":
        for region in config.PIA_CUSTOM_REGIONS:
            for profile in pia_custom.ensure_profiles(region, pia_user, pia_pass):
                custom_profiles[profile.name] = profile

    for attempt in range(1, config.MAX_CANDIDATE_TRIES + 1):
        server = candidates.next_candidate(
            conn, slot["provider"], in_use,
            servers=custom_profiles if slot["provider"] == "pia-custom" else None,
        )
        if server is None:
            log.warning("slot %s: kandidat %s habis", slot["id"], slot["provider"])
            break

        proton_key = None
        if slot["provider"] == "proton" and config.PROTON_VPN_TYPE != "openvpn":
            proton_key = config.proton_key(slot["id"])
        try:
            orchestrator.start(
                slot["id"], slot["port"], slot["provider"], server,
                pia_user=pia_user, pia_pass=pia_pass, proton_key=proton_key,
                proton_user=proton_user, proton_pass=proton_pass,
                custom_profile=custom_profiles.get(server),
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
                "fail_streak=0, last_ok_at=? WHERE id=?",
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


@serialized
def rotate_daily(conn):
    _prune_probe_out(config.PROBE_OUT_RETAIN_DAYS)
    pia_user, pia_pass = config.pia_credentials()
    slots = conn.execute("SELECT * FROM slots ORDER BY id").fetchall()
    for slot in slots:
        if slot["provider"] in ("pia", "pia-custom") and not pia_user:
            log.warning("slot %s: .pia-credentials tidak ada, dilewati", slot["id"])
            continue
        if slot["provider"] == "proton":
            if config.PROTON_VPN_TYPE == "openvpn":
                proton_user, _ = config.proton_credentials()
                if not proton_user:
                    log.warning("slot %s: .proton-credentials tidak ada, dilewati", slot["id"])
                    continue
            elif not config.proton_key(slot["id"]):
                log.warning("slot %s: PROTON_KEY_%s tidak diset, dilewati", slot["id"], slot["id"].upper())
                continue
        rotate_slot(conn, slot, pia_user, pia_pass)
    _notify_pool_state(conn)


def _apply_hourly_verdict(conn, slot, verdict, pia_user, pia_pass):
    """Terjemahkan satu vonis probe per jam jadi perubahan status slot.
    Return True kalau ada yang benar-benar berubah (buat memutuskan apakah
    cek ulang antar-jam perlu memberi kabar)."""
    slot_id = slot["id"]

    # Tunnel sehat tapi OLX tidak menjawab: tidak ada yang bisa disimpulkan
    # tentang exit IP-nya. Dicatat di /probes sebagai jejak, tapi status slot
    # sengaja tidak disentuh - inilah kasus yang dulu mencoret slot sehat.
    if verdict == "inconclusive":
        return False

    if verdict == "ok":
        changed = slot["status"] != "active" or (slot["fail_streak"] or 0) != 0
        conn.execute(
            "UPDATE slots SET status='active', fail_streak=0, last_ok_at=? WHERE id=?",
            (_now(), slot_id),
        )
        conn.commit()
        if changed and slot["status"] != "active":
            log.info("slot %s: pulih, kembali diterbitkan", slot_id)
        return changed

    # 'blocked' = referenceNum benar-benar terbaca. Itu bukti langsung tentang
    # exit IP-nya, bukan probe yang gagal, jadi menjatuhkan seketika seperti
    # sebelumnya - tidak ikut aturan streak.
    if verdict == "blocked":
        # changed dihitung dari status SEBELUMNYA: slot yang memang sudah
        # 'blocked' dan tetap 'blocked' bukan kabar baru. recheck_stuck()
        # jalan 12x sejam dan _notify_pool_state() tidak punya dedupe.
        changed = slot["status"] != "blocked"
        conn.execute(
            "UPDATE slots SET status='blocked', fail_streak=? WHERE id=?",
            ((slot["fail_streak"] or 0) + 1, slot_id),
        )
        conn.commit()
        if changed:
            log.info("slot %s: diblokir, dikeluarkan dari daftar terbit", slot_id)
        if config.HOURLY_REFILL:
            rotate_slot(conn, slot, pia_user, pia_pass)
            changed = True
        return changed

    # 'connecting' - tunnelnya tidak menjawab. Beri toleransi beberapa kali
    # berturut-turut sebelum mencoret: satu probe meleset (rotasi berbarengan,
    # hiccup jaringan sesaat) tidak sepadan dengan menghilangkan proxy yang
    # masih dipakai konsumen.
    streak = (slot["fail_streak"] or 0) + 1
    if streak < config.FAIL_STREAK_LIMIT and slot["status"] == "active":
        conn.execute("UPDATE slots SET fail_streak=? WHERE id=?", (streak, slot_id))
        conn.commit()
        log.info(
            "slot %s: probe gagal %d/%d berturut-turut, masih diterbitkan",
            slot_id, streak, config.FAIL_STREAK_LIMIT,
        )
        return False

    conn.execute(
        "UPDATE slots SET status='connecting', fail_streak=? WHERE id=?", (streak, slot_id)
    )
    conn.commit()
    # Tanpa ini slot 'connecting' tidak punya jalan keluar sama sekali:
    # HOURLY_REFILL cuma dicek di cabang 'blocked', jadi satu-satunya
    # pemulihan dulu adalah rotasi manual.
    if config.ROTATE_STUCK and streak >= config.FAIL_STREAK_LIMIT:
        log.warning(
            "slot %s: macet 'connecting' %d kali berturut-turut, dirotasi otomatis",
            slot_id, streak,
        )
        rotate_slot(conn, slot, pia_user, pia_pass)
    return True


def _verify_slots(conn, slots):
    pia_user, pia_pass = config.pia_credentials()
    changed = False
    for slot in slots:
        verdict, detail = probes.run_hourly(slot["port"])
        _log_probe(conn, slot["id"], "hourly", verdict, detail)
        # Baca ulang: rotate_slot() pada iterasi sebelumnya bisa sudah menulis
        # baris ini (mis. slot yang sama muncul lagi), jadi jangan bekerja di
        # atas snapshot yang mungkin sudah basi.
        fresh = conn.execute("SELECT * FROM slots WHERE id=?", (slot["id"],)).fetchone()
        if fresh is None:
            continue
        if _apply_hourly_verdict(conn, fresh, verdict, pia_user, pia_pass):
            changed = True
    return changed


@serialized
def verify_hourly(conn):
    slots = conn.execute("SELECT * FROM slots WHERE status IN ('active','connecting')").fetchall()
    _verify_slots(conn, slots)
    _notify_pool_state(conn)


@serialized
def recheck_stuck(conn):
    """Cek ulang khusus slot yang tidak aktif, jauh lebih sering daripada
    siklus per jam (config.RECHECK_SECONDS). Slot 'connecting' tidak
    menerbitkan apa pun, jadi tidak ada yang dirugikan kalau ia dicek tiap
    5 menit - dan itu memangkas jendela pemulihan dari 59 menit jadi 5.
    Notifikasi hanya kalau ada yang benar-benar berubah: dipanggil 12x per
    jam, dan _notify_pool_state() sengaja tanpa dedupe."""
    slots = conn.execute("SELECT * FROM slots WHERE status IN ('connecting','blocked')").fetchall()
    if not slots:
        return
    if _verify_slots(conn, slots):
        _notify_pool_state(conn)
