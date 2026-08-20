import logging
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

from . import config, db, jobs, scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("pool.app")

app = Flask(__name__)


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


@app.get("/health")
def health():
    with db.connect() as conn:
        active = conn.execute("SELECT COUNT(*) n FROM slots WHERE status='active'").fetchone()["n"]
        total = conn.execute("SELECT COUNT(*) n FROM slots").fetchone()["n"]
    return jsonify({"status": "ok", "active_slots": active, "total_slots": total})


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


@app.get("/")
def index():
    with db.connect() as conn:
        slot_rows = conn.execute("SELECT * FROM slots ORDER BY id").fetchall()
        probe_rows = conn.execute("SELECT * FROM probes ORDER BY id DESC LIMIT 30").fetchall()
    return render_template("index.html", slots=slot_rows, probes=probe_rows)


def main():
    db.init()
    scheduler.start(config.DAILY_TIME)
    app.run(host="0.0.0.0", port=config.API_PORT)


if __name__ == "__main__":
    main()
