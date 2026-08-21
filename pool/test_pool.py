"""Self-check tanpa Docker/jaringan: skema, dedupe kandidat, dan endpoint API
di atas SQLite sementara. Jalankan: python3 -m pool.test_pool
(dari root repo, dengan venv yang sudah pip install -r pool/requirements.txt)."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from . import candidates, config, db, jobs
from .app import _ago, app


class PoolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._orig = (config.ROOT, config.DB_PATH, config.SLOT_DEFS)
        config.ROOT = root
        config.DB_PATH = str(root / "pool.db")
        config.SLOT_DEFS = [("slot-1", "pia", 9001), ("slot-2", "proton", 9002)]
        # candidates._all_servers() memanggil servers.sh sungguhan - tiruan
        # kecil ini cukup untuk menguji dedupe tanpa Docker/jaringan.
        fake_servers_sh = root / "servers.sh"
        fake_servers_sh.write_text("#!/bin/sh\nprintf 'Server-1\\nServer-2\\n'\n")
        fake_servers_sh.chmod(0o755)
        db.init()

    def tearDown(self):
        config.ROOT, config.DB_PATH, config.SLOT_DEFS = self._orig
        self.tmp.cleanup()

    def test_schema_seeds_configured_slots(self):
        with db.connect() as conn:
            rows = conn.execute("SELECT id, status FROM slots ORDER BY id").fetchall()
        self.assertEqual([r["id"] for r in rows], ["slot-1", "slot-2"])
        self.assertTrue(all(r["status"] == "dead" for r in rows))

    def test_next_candidate_skips_failed(self):
        with db.connect() as conn:
            candidates.record(conn, "pia", "Server-1", "slot-1", "blocked")
            got = candidates.next_candidate(conn, "pia", in_use=set())
        self.assertEqual(got, "Server-2")

    def test_next_candidate_skips_in_use(self):
        with db.connect() as conn:
            got = candidates.next_candidate(conn, "pia", in_use={"Server-1"})
        self.assertEqual(got, "Server-2")

    def test_next_candidate_none_when_exhausted(self):
        with db.connect() as conn:
            got = candidates.next_candidate(conn, "pia", in_use={"Server-1", "Server-2"})
        self.assertIsNone(got)

    def test_next_candidate_retries_after_window_expires(self):
        from datetime import datetime, timedelta, timezone

        old = (datetime.now(timezone.utc) - timedelta(hours=config.CANDIDATE_RETRY_HOURS + 1)).isoformat()
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO candidates (provider, server, slot_id, result, tried_at) VALUES (?,?,?,?,?)",
                ("pia", "Server-1", "slot-1", "blocked", old),
            )
            conn.commit()
            got = candidates.next_candidate(conn, "pia", in_use=set())
        self.assertEqual(got, "Server-1")

    def test_proxies_503_when_pool_empty(self):
        r = app.test_client().get("/proxies")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.data, b"")

    def test_proxies_lists_active_slots(self):
        with db.connect() as conn:
            conn.execute(
                "UPDATE slots SET status='active', server='Server-1', exit_ip='1.2.3.4' "
                "WHERE id='slot-1'"
            )
            conn.commit()
        r = app.test_client().get("/proxies")
        self.assertEqual(r.status_code, 200)
        self.assertIn(f"http://{config.ADVERTISE_HOST}:9001", r.get_data(as_text=True))

    def test_slot_bad_marks_blocked_and_logs_probe(self):
        with db.connect() as conn:
            conn.execute(
                "UPDATE slots SET status='active', server='Server-1', exit_ip='1.2.3.4' "
                "WHERE id='slot-1'"
            )
            conn.commit()
        r = app.test_client().post("/slots/slot-1/bad")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        with db.connect() as conn:
            row = conn.execute("SELECT status FROM slots WHERE id='slot-1'").fetchone()
            probe = conn.execute(
                "SELECT verdict, kind FROM probes WHERE slot_id='slot-1' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            cand = conn.execute(
                "SELECT result FROM candidates WHERE provider='pia' AND server='Server-1'"
            ).fetchone()
        self.assertEqual(row["status"], "blocked")
        self.assertEqual((probe["kind"], probe["verdict"]), ("consumer", "blocked"))
        self.assertEqual(cand["result"], "blocked")

    def test_slot_bad_404_for_unknown_slot(self):
        r = app.test_client().post("/slots/slot-does-not-exist/bad")
        self.assertEqual(r.status_code, 404)

    def test_health_counts_slots(self):
        body = app.test_client().get("/health").get_json()
        self.assertEqual(body["total_slots"], 2)
        self.assertEqual(body["active_slots"], 0)

    def test_dashboard_renders_with_data(self):
        with db.connect() as conn:
            conn.execute(
                "UPDATE slots SET status='active', server='Server-1', exit_ip='1.2.3.4', "
                "negara='SG', org='AS1 Test Org' WHERE id='slot-1'"
            )
            conn.execute(
                "INSERT INTO probes (slot_id, kind, verdict, detail, at) VALUES "
                "('slot-1','daily','ok','markers=5','2026-01-01T00:00:00+00:00')"
            )
            conn.commit()
        r = app.test_client().get("/")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn("1.2.3.4", body)
        self.assertIn("AS1 Test Org", body)
        self.assertIn("markers=5", body)

    def test_dashboard_renders_with_empty_pool(self):
        r = app.test_client().get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Belum ada riwayat probe", r.get_data(as_text=True))

    def test_ago_clamps_future_timestamp(self):
        from datetime import datetime, timedelta, timezone
        future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        self.assertEqual(_ago(future), "0 detik lalu")

    def test_migrate_adds_missing_columns_to_existing_db(self):
        # pool.db dari sebelum Fase 2: tabel slots tanpa negara/org - persis
        # bug yang benar-benar terjadi (OperationalError "no such column").
        with db.connect() as raw:
            raw.execute("DROP TABLE slots")
            raw.execute(
                "CREATE TABLE slots (id TEXT PRIMARY KEY, port INTEGER NOT NULL, "
                "provider TEXT NOT NULL, server TEXT, exit_ip TEXT, "
                "status TEXT NOT NULL DEFAULT 'dead', last_ok_at TEXT)"
            )
            raw.execute("INSERT INTO slots (id, port, provider) VALUES ('slot-1', 9001, 'pia')")
            raw.commit()
        db.init()  # harus migrasi diam-diam, bukan meledak
        with db.connect() as conn:
            conn.execute("UPDATE slots SET negara='SG', org='AS1 Test' WHERE id='slot-1'")
            conn.commit()
            row = conn.execute("SELECT negara, org FROM slots WHERE id='slot-1'").fetchone()
        self.assertEqual((row["negara"], row["org"]), ("SG", "AS1 Test"))

    def test_jobs_rotate_reports_success_as_json(self):
        with mock.patch("pool.app.jobs.rotate_daily", return_value=None):
            r = app.test_client().post("/jobs/rotate")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])

    def test_jobs_rotate_reports_failure_as_json(self):
        with mock.patch("pool.app.jobs.rotate_daily", side_effect=RuntimeError("boom")):
            r = app.test_client().post("/jobs/rotate")
        self.assertEqual(r.status_code, 500)
        self.assertFalse(r.get_json()["ok"])

    def test_jobs_rotate_ui_redirects_with_status_on_failure(self):
        with mock.patch("pool.app.jobs.rotate_daily", side_effect=RuntimeError("boom")):
            r = app.test_client().post("/jobs/rotate", data={"from": "ui"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("done=rotate", r.headers["Location"])
        self.assertIn("ok=0", r.headers["Location"])

    def test_dashboard_shows_success_banner(self):
        r = app.test_client().get("/?done=rotate&ok=1")
        self.assertIn("selesai", r.get_data(as_text=True))

    def test_dashboard_shows_failure_banner(self):
        r = app.test_client().get("/?done=verify&ok=0")
        self.assertIn("gagal", r.get_data(as_text=True))

    def test_notify_posts_when_no_active_slots(self):
        with mock.patch.object(config, "DISCORD_WEBHOOK_URL", "https://discord.example/webhook"), \
             mock.patch("pool.jobs.requests.post") as post:
            with db.connect() as conn:
                jobs._notify_pool_state(conn)
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "https://discord.example/webhook")
        self.assertIn("kosong", post.call_args.kwargs["json"]["content"])

    def test_notify_posts_on_partial_degradation(self):
        # 1 dari 2 slot aktif - bukan kosong total, tapi tetap bukan "penuh".
        with db.connect() as conn:
            conn.execute("UPDATE slots SET status='active' WHERE id='slot-1'")
            conn.commit()
        with mock.patch.object(config, "DISCORD_WEBHOOK_URL", "https://discord.example/webhook"), \
             mock.patch("pool.jobs.requests.post") as post:
            with db.connect() as conn:
                jobs._notify_pool_state(conn)
        post.assert_called_once()
        self.assertIn("1/2", post.call_args.kwargs["json"]["content"])

    def test_notify_skips_when_pool_full(self):
        with db.connect() as conn:
            conn.execute("UPDATE slots SET status='active'")
            conn.commit()
        with mock.patch.object(config, "DISCORD_WEBHOOK_URL", "https://discord.example/webhook"), \
             mock.patch("pool.jobs.requests.post") as post:
            with db.connect() as conn:
                jobs._notify_pool_state(conn)
        post.assert_not_called()

    def test_notify_skips_when_webhook_unset(self):
        with mock.patch.object(config, "DISCORD_WEBHOOK_URL", None), \
             mock.patch("pool.jobs.requests.post") as post:
            with db.connect() as conn:
                jobs._notify_pool_state(conn)
        post.assert_not_called()

    def test_prune_probe_out_removes_old_keeps_recent(self):
        import os

        out_dir = config.ROOT / "pool" / "probe-out"
        out_dir.mkdir(parents=True)
        old = out_dir / "old.html"
        recent = out_dir / "recent.html"
        old.write_text("x")
        recent.write_text("x")
        old_time = time.time() - 20 * 86400  # 20 hari lalu
        os.utime(old, (old_time, old_time))

        jobs._prune_probe_out(retain_days=14)

        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())


if __name__ == "__main__":
    unittest.main()
