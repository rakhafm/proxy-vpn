"""Tiga job berulang lewat thread biasa - tidak perlu Celery/APScheduler
untuk segini (lihat PRD §Teknologi). Ketiganya berebut JOB_LOCK di jobs.py,
jadi walaupun jadwalnya bertabrakan tidak ada dua job yang menyentuh Docker
dan tabel slots bersamaan."""
import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, db, jobs

log = logging.getLogger("pool.scheduler")
JAKARTA = ZoneInfo("Asia/Jakarta")


def _seconds_until(hh_mm):
    hh, mm = (int(x) for x in hh_mm.split(":"))
    now = datetime.now(JAKARTA)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _loop(name, first_delay, interval, fn):
    def run():
        time.sleep(max(first_delay, 0))
        while True:
            try:
                with db.connect() as conn:
                    fn(conn)
            except Exception:
                log.exception("%s gagal", name)
            time.sleep(interval)

    t = threading.Thread(target=run, name=name, daemon=True)
    t.start()
    return t


def start(daily_time):
    _loop("rotate-daily", _seconds_until(daily_time), 24 * 3600, jobs.rotate_daily)
    _loop("verify-hourly", 0, 3600, jobs.verify_hourly)
    # Selang pendek, tapi hanya menyentuh slot yang tidak aktif (query di
    # recheck_stuck) - slot sehat tetap diperiksa sekali per jam seperti dulu,
    # bukan 12x. Delay awal = satu selang penuh supaya tidak menabrak
    # verify-hourly yang baru saja jalan di detik nol.
    _loop("recheck-stuck", config.RECHECK_SECONDS, config.RECHECK_SECONDS, jobs.recheck_stuck)
    log.info(
        "scheduler jalan: rotasi harian %s Asia/Jakarta, verifikasi tiap jam, "
        "cek ulang slot macet tiap %d detik",
        daily_time, config.RECHECK_SECONDS,
    )
