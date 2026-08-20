"""Dua job berulang lewat threading.Timer - tidak perlu Celery/APScheduler
untuk dua job (lihat PRD §Teknologi)."""
import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import db, jobs

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
    log.info("scheduler jalan: rotasi harian %s Asia/Jakarta, verifikasi tiap jam", daily_time)
