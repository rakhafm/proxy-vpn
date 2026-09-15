import logging
import pathlib
import re
from datetime import datetime, timezone

from flask import (
    Flask, Response, abort, jsonify, redirect, render_template, request,
    send_from_directory, url_for,
)
from markupsafe import Markup, escape

from . import candidates, config, db, jobs, scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("pool.app")

app = Flask(__name__)

PROBE_OUT_DIR = config.ROOT / "pool" / "probe-out"
_PROBE_OUT_EXTS = {".html", ".png"}


def _ago(iso_ts):
    """'12 menit lalu' - satu-satunya alasan halaman pantau butuh filter Jinja
    kustom, tidak ada padanan bawaan untuk waktu relatif."""
    if not iso_ts:
        return "-"
    then = datetime.fromisoformat(iso_ts)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    # max(0, ...): timestamp lebih baru dari "sekarang" (drift jam, atau baris
    # yang baru saja ditulis saat request sedang diproses) tidak boleh jadi
    # detik negatif di layar.
    secs = max(0, int((datetime.now(timezone.utc) - then).total_seconds()))
    if secs < 60:
        return f"{secs} detik lalu"
    if secs < 3600:
        return f"{secs // 60} menit lalu"
    if secs < 86400:
        return f"{secs // 3600} jam lalu"
    return f"{secs // 86400} hari lalu"


app.jinja_env.filters["ago"] = _ago


_BUKTI_RE = re.compile(r"pool/probe-out/([\w.-]+\.(?:html|png))")


def _linkify_bukti(detail):
    """Detail probe menulis 'bukti: pool/probe-out/xxx.html, ...' - path lokal
    di VM, bukan URL. Ubah jadi tautan ke /probe-out/xxx supaya bukti bisa
    dibuka langsung dari halaman pantau. escape() dulu baru linkify, supaya
    detail probe (bisa berisi log gluetun mentah) tidak menyuntik HTML."""
    if not detail:
        return ""
    escaped = str(escape(detail))
    linked = _BUKTI_RE.sub(
        lambda m: f'<a href="/probe-out/{m.group(1)}" target="_blank" rel="noopener">{m.group(0)}</a>',
        escaped,
    )
    return Markup(linked)


app.jinja_env.filters["linkify_bukti"] = _linkify_bukti


def _slot_url(slot):
    return f"http://{config.ADVERTISE_HOST}:{slot['port']}"


@app.get("/proxies")
def proxies():
    with db.connect() as conn:
        active = conn.execute("SELECT * FROM slots WHERE status='active' ORDER BY id").fetchall()
    if not active:
        # 503, bukan 200 kosong: supaya `curl -f` di sisi konsumen gagal dan
        # proxies.txt lama dibiarkan utuh (lihat PRD §Keputusan: kolam kosong).
        return Response("", status=503, mimetype="text/plain")
    body = "\n".join(_slot_url(s) for s in active) + "\n"
    return Response(body, mimetype="text/plain")


@app.get("/proxies.json")
def proxies_json():
    with db.connect() as conn:
        active = conn.execute("SELECT * FROM slots WHERE status='active' ORDER BY id").fetchall()
    return jsonify([
        {
            "url": _slot_url(s),
            "provider": s["provider"],
            "server": s["server"],
            "exit_ip": s["exit_ip"],
            "negara": s["negara"],
            "last_ok_at": s["last_ok_at"],
        }
        for s in active
    ])


@app.get("/slots")
def slots():
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM slots ORDER BY id").fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/probes")
def probes_view():
    limit = min(int(request.args.get("limit", 100)), 1000)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM probes ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/probe-out/<path:filename>")
def probe_out(filename):
    """Serve bukti probe (html + screenshot) yang dirujuk kolom detail di
    halaman pantau - supaya link 'bukti: pool/probe-out/...' bisa dibuka
    langsung di browser, bukan cuma path lokal di VM. send_from_directory
    menolak path traversal sendiri; ekstensi dibatasi html/png karena itu
    satu-satunya jenis file yang ditulis probes.run_daily()."""
    if pathlib.Path(filename).suffix not in _PROBE_OUT_EXTS:
        abort(404)
    return send_from_directory(PROBE_OUT_DIR, filename)


@app.get("/health")
def health():
    with db.connect() as conn:
        active = conn.execute("SELECT COUNT(*) n FROM slots WHERE status='active'").fetchone()["n"]
        total = conn.execute("SELECT COUNT(*) n FROM slots").fetchone()["n"]
    return jsonify({"status": "ok", "active_slots": active, "total_slots": total})


@app.post("/slots/<slot_id>/bad")
def slot_bad(slot_id):
    """Umpan balik dari konsumen (crawler): tandai slot ini blocked SEKARANG,
    tanpa menunggu verify_hourly berikutnya (sampai 59 menit). Konsumen yang
    barusan kena deny tahu duluan daripada probe curl kita."""
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM slots WHERE id=?", (slot_id,)).fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "slot tidak ada"}), 404
        conn.execute("UPDATE slots SET status='blocked' WHERE id=?", (slot_id,))
        conn.execute(
            "INSERT INTO probes (slot_id, kind, verdict, detail, at) VALUES (?,?,?,?,?)",
            (slot_id, "consumer", "blocked", "dilaporkan konsumen", datetime.now(timezone.utc).isoformat()),
        )
        if row["server"]:
            candidates.record(conn, row["provider"], row["server"], slot_id, "blocked")
        conn.commit()
    log.info("slot %s: dilaporkan blocked oleh konsumen", slot_id)
    return jsonify({"ok": True}), 200


def _run_job(name, fn):
    """Jalankan satu job dan laporkan hasilnya - dipanggil sinkron di dalam
    request, jadi baik tombol UI maupun `curl` sama-sama tahu pasti selesai
    atau gagal saat balasannya datang, bukan menebak dari diam."""
    from_ui = request.form.get("from") == "ui"
    try:
        with db.connect() as conn:
            fn(conn)
    except Exception:
        log.exception("job %s gagal", name)
        if from_ui:
            return redirect(url_for("index", done=name, ok="0"))
        return jsonify({"job": name, "ok": False}), 500
    if from_ui:
        return redirect(url_for("index", done=name, ok="1"))
    return jsonify({"job": name, "ok": True}), 200


@app.post("/jobs/rotate")
def jobs_rotate():
    return _run_job("rotate", jobs.rotate_daily)


@app.post("/jobs/verify")
def jobs_verify():
    return _run_job("verify", jobs.verify_hourly)


@app.post("/jobs/check")
def jobs_check():
    return _run_job("check", jobs.check_urls)


@app.post("/jobs/check/<path:name>")
def jobs_check_one(name):
    """Cek satu nama dari CSV saja (tombol Cek di baris tabel) - tetap lewat
    semua slot aktif, tetap @serialized lewat check_urls. `path:` karena
    load_checks() memakai URL sebagai nama kalau kolom name kosong, dan
    nama berisi '/' ditolak converter default (404)."""
    return _run_job(f"check:{name}", lambda conn: jobs.check_urls(conn, names=[name]))


def _latest_checks(conn):
    """Hasil TERBARU per (slot aktif, nama, URL di CSV) - bukan per jalannya job,
    supaya cek satu nama (tombol Cek per baris) tidak menghilangkan hasil
    nama lain dari tabel. Urutan: slot, lalu urutan baris CSV. Nama yang
    belum pernah dicek tetap dapat baris (result None) supaya tombol Cek-nya
    ada."""
    # URL adalah bagian dari identitas hasil. Jika checks.csv mengganti URL
    # tetapi mempertahankan name, hasil untuk target lama tidak boleh tampil
    # sebagai hasil target baru sebelum target baru benar-benar diperiksa.
    latest = {
        (r["slot_id"], r["name"], r["url"]): r
        for r in conn.execute(
            "SELECT * FROM checks WHERE id IN (SELECT MAX(id) FROM checks GROUP BY slot_id, name, url)"
        )
    }
    active = conn.execute("SELECT id, exit_ip FROM slots WHERE status='active' ORDER BY id").fetchall()
    rows = []
    for slot in active:
        for c in jobs.load_checks():
            r = latest.get((slot["id"], c["name"], c["url"]))
            # Yang diuji sebenarnya exit IP, bukan slot-nya: setelah rotasi
            # hasil lama menceritakan exit yang sudah tidak ada. Tampilkan
            # sebagai belum dicek (dengan catatan), bukan sebagai 'ok' basi.
            stale = r is not None and bool(r["exit_ip"]) and r["exit_ip"] != slot["exit_ip"]
            if stale:
                r = None
            rows.append({
                "slot_id": slot["id"], "name": c["name"], "url": c["url"],
                "exit_ip": r["exit_ip"] if r else None,
                "verdict": r["verdict"] if r else None,
                "detail": r["detail"] if r else ("exit berganti sejak cek terakhir" if stale else None),
                "run_at": r["run_at"] if r else None,
                "bukti": [f for f in ((r["bukti"] if r else "") or "").split(",") if f],
            })
    return rows


@app.get("/checks.json")
def checks_json():
    with db.connect() as conn:
        rows = _latest_checks(conn)
    return jsonify([
        {**r, "bukti": [f"/probe-out/{f}" for f in r["bukti"]]}
        for r in rows if r["verdict"] is not None
    ])


@app.post("/slots/<slot_id>/rotate")
def slot_rotate(slot_id):
    # @serialized: rotasi satu slot lewat tombol pantau menyentuh Docker dan
    # tabel slots sama seperti job terjadwal, jadi harus antre di lock yang
    # sama - bukan berjalan berbarengan dengan verifikasi yang sedang jalan.
    @jobs.serialized
    def fn(conn):
        slot = conn.execute("SELECT * FROM slots WHERE id=?", (slot_id,)).fetchone()
        if slot is None:
            raise ValueError(f"slot {slot_id} tidak ada")
        pia_user, pia_pass = config.pia_credentials()
        jobs.rotate_slot(conn, slot, pia_user, pia_pass)
        jobs._notify_pool_state(conn)

    return _run_job(f"rotate:{slot_id}", fn)


@app.get("/")
def index():
    with db.connect() as conn:
        slot_rows = conn.execute("SELECT * FROM slots ORDER BY id").fetchall()
        probe_rows = conn.execute("SELECT * FROM probes ORDER BY id DESC LIMIT 30").fetchall()
        check_rows = _latest_checks(conn)
    return render_template(
        "index.html", slots=slot_rows, probes=probe_rows, checks=check_rows,
        fail_streak_limit=config.FAIL_STREAK_LIMIT,
    )


def main():
    db.init()
    scheduler.start(config.DAILY_TIME)
    app.run(host="0.0.0.0", port=config.API_PORT)


if __name__ == "__main__":
    main()
