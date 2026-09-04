"""Self-check tanpa Docker/jaringan: skema, dedupe kandidat, dan endpoint API
di atas SQLite sementara. Jalankan: python3 -m pool.test_pool
(dari root repo, dengan venv yang sudah pip install -r pool/requirements.txt)."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from . import candidates, config, db, jobs, orchestrator, pia_custom
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

    def test_next_candidate_order_is_shuffled(self):
        # setUp() cuma kasih 2 server (Server-1/2) - tidak cukup buat mengetes
        # keacakan tanpa flaky. Ganti fake servers.sh dengan daftar lebih
        # panjang khusus test ini.
        fake = Path(config.ROOT) / "servers.sh"
        fake.write_text(
            "#!/bin/sh\nprintf 'S1\\nS2\\nS3\\nS4\\nS5\\nS6\\nS7\\nS8\\n'\n"
        )
        fake.chmod(0o755)
        with db.connect() as conn:
            seen = {candidates.next_candidate(conn, "pia", in_use=set()) for _ in range(20)}
        # Peluang 20 panggilan selalu balik server yang sama dari 8 pilihan
        # ~ (1/8)^19 - praktis nol kalau memang diacak, bukan deterministik.
        self.assertGreater(len(seen), 1)

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

    def test_candidate_group_comma_list_passed_as_separate_args(self):
        # fake servers.sh dari setUp() mengabaikan argumen - ganti dengan versi
        # yang mencetaknya balik, supaya split koma -> argv terpisah bisa dicek.
        fake = Path(config.ROOT) / "servers.sh"
        fake.write_text('#!/bin/sh\nshift\nfor a in "$@"; do echo "$a"; done\n')
        fake.chmod(0o755)
        self._orig_group = config.CANDIDATE_GROUP
        config.CANDIDATE_GROUP = "sea,China,JP Tokyo,Hong Kong,Taiwan,South Korea"
        try:
            got = candidates._all_servers("pia")
        finally:
            config.CANDIDATE_GROUP = self._orig_group
        self.assertEqual(
            got, ["sea", "China", "JP Tokyo", "Hong Kong", "Taiwan", "South Korea"]
        )

    def test_next_candidate_pia_custom_uses_fixed_regions_not_serversh(self):
        # servers.sh dari setUp() cuma tahu Server-1/2 - kalau pia-custom
        # kepeleset lewat _all_servers("pia") apa adanya, hasilnya bukan
        # region sama sekali. servers.sh juga sengaja TIDAK dipanggil untuk
        # provider ini (tidak dikenalnya) - lihat candidates._all_servers().
        self._orig_regions = config.PIA_CUSTOM_REGIONS
        config.PIA_CUSTOM_REGIONS = ["sg", "jakarta"]
        try:
            got = candidates._all_servers("pia-custom")
        finally:
            config.PIA_CUSTOM_REGIONS = self._orig_regions
        self.assertEqual(got, ["sg", "jakarta"])

    def test_orchestrator_start_sets_wireguard_mtu_for_proton(self):
        with mock.patch.object(config, "WIREGUARD_MTU", "1280"), \
             mock.patch("pool.orchestrator.subprocess.run") as run:
            orchestrator.start("slot-2", 9002, "proton", "node-id-01.protonvpn.net", proton_key="k")
        cmd = run.call_args[0][0]
        self.assertIn("WIREGUARD_MTU=1280", cmd)

    def test_orchestrator_start_sets_openvpn_mssfix_for_pia(self):
        with mock.patch.object(config, "OPENVPN_MSSFIX", "1280"), \
             mock.patch("pool.orchestrator.subprocess.run") as run:
            orchestrator.start("slot-1", 9001, "pia", "Server-1", pia_user="u", pia_pass="p")
        cmd = run.call_args[0][0]
        self.assertIn("OPENVPN_MSSFIX=1280", cmd)

    def test_orchestrator_start_skips_mtu_flags_when_blank(self):
        with mock.patch.object(config, "WIREGUARD_MTU", ""), \
             mock.patch.object(config, "OPENVPN_MSSFIX", ""), \
             mock.patch("pool.orchestrator.subprocess.run") as run:
            orchestrator.start("slot-2", 9002, "proton", "node-id-01.protonvpn.net", proton_key="k")
        cmd = run.call_args[0][0]
        self.assertFalse(any(a.startswith("WIREGUARD_MTU=") for a in cmd))

    def test_orchestrator_start_pia_custom_mounts_profile_and_skips_server_names(self):
        with mock.patch("pool.orchestrator.pia_custom.ensure_profile", return_value=Path("/tmp/sg.ovpn")) as ensure, \
             mock.patch("pool.orchestrator.subprocess.run") as run:
            orchestrator.start("slot-5", 9005, "pia-custom", "sg", pia_user="u", pia_pass="p")
        ensure.assert_called_once_with("sg", "u", "p")
        cmd = run.call_args[0][0]
        self.assertIn("VPN_SERVICE_PROVIDER=custom", cmd)
        self.assertIn("OPENVPN_CUSTOM_CONFIG=/gluetun/custom.conf", cmd)
        self.assertIn("OPENVPN_USER=u", cmd)
        self.assertIn("-v", cmd)
        self.assertIn("/tmp/sg.ovpn:/gluetun/custom.conf:ro", cmd)
        # 'sg' cuma dipakai memilih profil, bukan diteruskan sebagai SERVER_NAMES
        # (itu opsi mode provider bawaan gluetun, tidak berlaku di mode custom).
        self.assertFalse(any(a.startswith("SERVER_NAMES=") for a in cmd))

    def test_orchestrator_start_pia_custom_raises_when_profile_unavailable(self):
        with mock.patch("pool.orchestrator.pia_custom.ensure_profile", return_value=None), \
             mock.patch("pool.orchestrator.subprocess.run"):
            with self.assertRaises(RuntimeError):
                orchestrator.start("slot-5", 9005, "pia-custom", "sg", pia_user="u", pia_pass="p")

    def _pia_custom_dir(self):
        d = Path(config.ROOT) / "vpn-profile"
        d.mkdir(exist_ok=True)
        self._orig_profile_dir = config.PIA_CUSTOM_PROFILE_DIR
        config.PIA_CUSTOM_PROFILE_DIR = str(d)
        self.addCleanup(setattr, config, "PIA_CUSTOM_PROFILE_DIR", self._orig_profile_dir)
        return d

    def test_pia_custom_ensure_profile_reuses_fresh_file_without_downloading(self):
        d = self._pia_custom_dir()
        f = d / "sg-aes-128-cbc-udp-ip.ovpn"
        f.write_text("client\n")
        with mock.patch("pool.pia_custom.subprocess.run") as run:
            got = pia_custom.ensure_profile("sg", "u", "p")
        run.assert_not_called()
        self.assertEqual(got, f)

    def test_pia_custom_ensure_profile_downloads_when_missing(self):
        d = self._pia_custom_dir()

        def fake_run(cmd, **kwargs):
            (d / "jakarta-aes-128-cbc-udp-ip.ovpn").write_text("client\n")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("pool.pia_custom.subprocess.run", side_effect=fake_run) as run:
            got = pia_custom.ensure_profile("jakarta", "u", "p")
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["env"]["PIA_USER"], "u")
        self.assertEqual(got.name, "jakarta-aes-128-cbc-udp-ip.ovpn")

    def test_pia_custom_ensure_profile_falls_back_to_stale_on_download_failure(self):
        d = self._pia_custom_dir()
        f = d / "sg-aes-128-cbc-udp-ip.ovpn"
        f.write_text("client\n")
        old = time.time() - 25 * 3600  # lebih tua dari PIA_CUSTOM_PROFILE_MAX_AGE_HOURS (24)
        os.utime(f, (old, old))
        with mock.patch(
            "pool.pia_custom.subprocess.run",
            return_value=mock.Mock(returncode=2, stdout="", stderr="login ditolak"),
        ):
            got = pia_custom.ensure_profile("sg", "u", "p")
        # download gagal, tapi cache lama masih ada - lebih baik daripada slot mati
        self.assertEqual(got, f)

    def test_pia_custom_ensure_profile_none_without_credentials_or_cache(self):
        self._pia_custom_dir()
        with mock.patch("pool.pia_custom.subprocess.run") as run:
            got = pia_custom.ensure_profile("sg", None, None)
        run.assert_not_called()
        self.assertIsNone(got)

    def test_orchestrator_start_proton_openvpn_mode(self):
        with mock.patch.object(config, "PROTON_VPN_TYPE", "openvpn"), \
             mock.patch("pool.orchestrator.subprocess.run") as run:
            orchestrator.start(
                "slot-4", 9004, "proton", "node-id-01.protonvpn.net",
                proton_user="u", proton_pass="p",
            )
        cmd = run.call_args[0][0]
        self.assertIn("VPN_TYPE=openvpn", cmd)
        self.assertIn("OPENVPN_USER=u", cmd)
        self.assertIn("OPENVPN_PASSWORD=p", cmd)
        self.assertFalse(any(a.startswith("WIREGUARD_PRIVATE_KEY=") for a in cmd))

    def test_orchestrator_start_proton_wireguard_mode_unaffected_by_openvpn_creds(self):
        with mock.patch.object(config, "PROTON_VPN_TYPE", "wireguard"), \
             mock.patch("pool.orchestrator.subprocess.run") as run:
            orchestrator.start(
                "slot-4", 9004, "proton", "node-id-01.protonvpn.net", proton_key="wgkey",
            )
        cmd = run.call_args[0][0]
        self.assertIn("VPN_TYPE=wireguard", cmd)
        self.assertIn("WIREGUARD_PRIVATE_KEY=wgkey", cmd)
        self.assertFalse(any(a.startswith("OPENVPN_USER=") for a in cmd))

    def test_proton_credentials_reads_two_line_file(self):
        (Path(config.ROOT) / ".proton-credentials").write_text("myuser\nmypass\n")
        self.assertEqual(config.proton_credentials(), ("myuser", "mypass"))

    def test_proton_credentials_missing_file_returns_none(self):
        self.assertEqual(config.proton_credentials(), (None, None))

    def test_next_candidate_retries_after_window_expires(self):
        from datetime import datetime, timedelta, timezone

        old = (datetime.now(timezone.utc) - timedelta(hours=config.CANDIDATE_RETRY_HOURS + 1)).isoformat()
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO candidates (provider, server, slot_id, result, tried_at) VALUES (?,?,?,?,?)",
                ("pia", "Server-1", "slot-1", "blocked", old),
            )
            conn.commit()
            # in_use={"Server-2"}: isolasi ke satu-satunya kandidat eligible
            # (Server-1) - next_candidate() sekarang mengacak urutan, jadi
            # kalau keduanya dibiarkan eligible hasilnya bisa Server-1 ATAU
            # Server-2 (sama-sama benar), bikin assertEqual ke satu nilai
            # flaky. Yang mau diuji di sini murni "cooldown sudah lewat jadi
            # eligible lagi", bukan urutan pemilihan.
            got = candidates.next_candidate(conn, "pia", in_use={"Server-2"})
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

    def test_probe_out_serves_html_file(self):
        from .app import PROBE_OUT_DIR

        PROBE_OUT_DIR.mkdir(parents=True, exist_ok=True)
        f = PROBE_OUT_DIR / "slot-1-selftest.html"
        f.write_text("<html>bukti probe</html>")
        try:
            r = app.test_client().get("/probe-out/slot-1-selftest.html")
            self.assertEqual(r.status_code, 200)
            self.assertIn("bukti probe", r.get_data(as_text=True))
            r.close()
        finally:
            f.unlink()

    def test_probe_out_rejects_non_html_png_extension(self):
        r = app.test_client().get("/probe-out/../pool/app.py")
        self.assertEqual(r.status_code, 404)

    def test_probe_out_404_for_missing_file(self):
        r = app.test_client().get("/probe-out/does-not-exist.html")
        self.assertEqual(r.status_code, 404)

    def test_dashboard_linkifies_bukti_reference(self):
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO probes (slot_id, kind, verdict, detail, at) VALUES "
                "('slot-1','daily','blocked',"
                "'Server-1 1.2.3.4 markers=0 bytes=100 "
                "[bukti: pool/probe-out/slot-1-x.html, pool/probe-out/slot-1-x.png]',"
                "'2026-01-01T00:00:00+00:00')"
            )
            conn.commit()
        r = app.test_client().get("/")
        body = r.get_data(as_text=True)
        self.assertIn('<a href="/probe-out/slot-1-x.html"', body)
        self.assertIn('<a href="/probe-out/slot-1-x.png"', body)

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
            row = conn.execute(
                "SELECT negara, org, fail_streak FROM slots WHERE id='slot-1'"
            ).fetchone()
        self.assertEqual((row["negara"], row["org"]), ("SG", "AS1 Test"))
        # fail_streak ditambahkan NOT NULL DEFAULT 0, jadi baris lama harus
        # ikut terisi 0 - bukan NULL yang bikin aritmetika streak meledak.
        self.assertEqual(row["fail_streak"], 0)

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

    def test_notify_posts_when_pool_full(self):
        with db.connect() as conn:
            conn.execute("UPDATE slots SET status='active'")
            conn.commit()
        with mock.patch.object(config, "DISCORD_WEBHOOK_URL", "https://discord.example/webhook"), \
             mock.patch("pool.jobs.requests.post") as post:
            with db.connect() as conn:
                jobs._notify_pool_state(conn)
        post.assert_called_once()
        self.assertIn("sehat", post.call_args.kwargs["json"]["content"])

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

    # --- probe per jam: dua langkah, 'connecting' vs 'inconclusive' --------
    #
    # Regresi untuk bug yang bikin slot sehat dicoret: sebelum ini SETIAP
    # curl exit != 0 divonis 'connecting'. Yang ditiru di sini adalah
    # subprocess.run, bukan jaringan - tidak ada koneksi keluar sama sekali.

    @staticmethod
    def _curl_result(rc, stdout="", stderr=""):
        return mock.Mock(returncode=rc, stdout=stdout, stderr=stderr)

    def test_run_hourly_ok_reports_exit_ip(self):
        from . import probes
        with mock.patch("pool.probes.subprocess.run", side_effect=[
            self._curl_result(0, "203.0.113.9\n"),   # ifconfig.me
            self._curl_result(0, "x" * 500_000),     # olx.co.id
        ]):
            verdict, detail = probes.run_hourly(9001)
        self.assertEqual(verdict, "ok")
        self.assertIn("203.0.113.9", detail)

    def test_run_hourly_tunnel_down_is_connecting(self):
        from . import probes
        with mock.patch("pool.probes.subprocess.run", side_effect=[
            self._curl_result(7, "", "Failed to connect to 127.0.0.1 port 9001"),
        ]) as run:
            verdict, detail = probes.run_hourly(9001)
        self.assertEqual(verdict, "connecting")
        # Gagal di langkah (a) harus berhenti di situ - tidak ada gunanya
        # menembak OLX lewat tunnel yang sudah terbukti mati.
        self.assertEqual(run.call_count, 1)
        self.assertIn("tunnel mati", detail)

    def test_run_hourly_http2_reset_is_inconclusive_not_connecting(self):
        """curl exit 92 dengan tunnel sehat: inilah 17/21 vonis 'connecting'
        palsu di proxy-1 (dan 20/29 di proxy-2) sebelum perbaikan ini."""
        from . import probes
        with mock.patch("pool.probes.subprocess.run", side_effect=[
            self._curl_result(0, "146.70.14.22\n"),
            self._curl_result(92, "", "HTTP/2 stream 1 was not closed cleanly: INTERNAL_ERROR"),
        ]):
            verdict, detail = probes.run_hourly(9001)
        self.assertEqual(verdict, "inconclusive")
        self.assertIn("146.70.14.22", detail)

    def test_run_hourly_block_marker_still_wins(self):
        from . import probes
        with mock.patch("pool.probes.subprocess.run", side_effect=[
            self._curl_result(0, "203.0.113.9\n"),
            self._curl_result(0, '<div id="referenceNum">18.abc</div>'),
        ]):
            verdict, _ = probes.run_hourly(9001)
        self.assertEqual(verdict, "blocked")

    def test_run_hourly_uses_root_domain_not_search_url(self):
        from . import probes
        with mock.patch("pool.probes.subprocess.run", side_effect=[
            self._curl_result(0, "203.0.113.9\n"),
            self._curl_result(0, "ok"),
        ]) as run:
            probes.run_hourly(9001)
        urls = [call.args[0][-1] for call in run.call_args_list]
        self.assertEqual(urls, [config.IP_CHECK_URL, config.OLX_HOURLY_URL])
        self.assertNotIn(config.OLX_VALIDATE_URL, urls)

    # --- penerapan vonis: streak, toleransi, rotasi otomatis --------------

    def _slot(self, slot_id="slot-1"):
        with db.connect() as conn:
            return conn.execute("SELECT * FROM slots WHERE id=?", (slot_id,)).fetchone()

    def _set_slot(self, slot_id="slot-1", **cols):
        sets = ", ".join(f"{k}=?" for k in cols)
        with db.connect() as conn:
            conn.execute(f"UPDATE slots SET {sets} WHERE id=?", (*cols.values(), slot_id))
            conn.commit()

    def _apply(self, verdict, slot_id="slot-1"):
        with db.connect() as conn:
            slot = conn.execute("SELECT * FROM slots WHERE id=?", (slot_id,)).fetchone()
            return jobs._apply_hourly_verdict(conn, slot, verdict, "u", "p")

    def test_inconclusive_leaves_active_slot_published(self):
        self._set_slot(status="active", fail_streak=0)
        changed = self._apply("inconclusive")
        self.assertFalse(changed)
        row = self._slot()
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["fail_streak"], 0)

    def test_connecting_tolerated_below_streak_limit(self):
        self._set_slot(status="active", fail_streak=0)
        with mock.patch.object(config, "FAIL_STREAK_LIMIT", 3):
            self._apply("connecting")
            row = self._slot()
            self.assertEqual(row["status"], "active")   # masih diterbitkan
            self.assertEqual(row["fail_streak"], 1)
            self._apply("connecting")
            self.assertEqual(self._slot()["fail_streak"], 2)

    def test_connecting_drops_slot_at_streak_limit(self):
        self._set_slot(status="active", fail_streak=2)
        with mock.patch.object(config, "FAIL_STREAK_LIMIT", 3), \
             mock.patch.object(config, "ROTATE_STUCK", False):
            self._apply("connecting")
        self.assertEqual(self._slot()["status"], "connecting")

    def test_ok_resets_streak_and_republishes(self):
        self._set_slot(status="connecting", fail_streak=5)
        changed = self._apply("ok")
        self.assertTrue(changed)
        row = self._slot()
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["fail_streak"], 0)
        self.assertIsNotNone(row["last_ok_at"])

    def test_stuck_connecting_slot_is_rotated_automatically(self):
        """Tanpa ini slot 'connecting' tidak punya jalan keluar sama sekali:
        HOURLY_REFILL dulu cuma dicek di cabang 'blocked'."""
        self._set_slot(status="connecting", fail_streak=2)
        with mock.patch.object(config, "FAIL_STREAK_LIMIT", 3), \
             mock.patch.object(config, "ROTATE_STUCK", True), \
             mock.patch("pool.jobs.rotate_slot") as rotate:
            self._apply("connecting")
        rotate.assert_called_once()

    def test_blocked_drops_immediately_without_streak_grace(self):
        # referenceNum terbaca = bukti langsung tentang exit IP-nya, bukan
        # probe yang gagal - tidak diberi toleransi seperti 'connecting'.
        self._set_slot(status="active", fail_streak=0)
        with mock.patch.object(config, "FAIL_STREAK_LIMIT", 3), \
             mock.patch.object(config, "HOURLY_REFILL", False):
            self._apply("blocked")
        self.assertEqual(self._slot()["status"], "blocked")

    def test_blocked_slot_staying_blocked_is_not_reported_as_change(self):
        # recheck_stuck() jalan 12x sejam dan _notify_pool_state() tanpa
        # dedupe - status yang tidak berubah tidak boleh memicu notifikasi.
        self._set_slot(status="blocked", fail_streak=1)
        with mock.patch.object(config, "HOURLY_REFILL", False):
            self.assertFalse(self._apply("blocked"))

    def test_recheck_stuck_skips_active_slots(self):
        self._set_slot("slot-1", status="active")
        self._set_slot("slot-2", status="connecting")
        with mock.patch("pool.jobs.probes.run_hourly", return_value=("ok", "-")) as probe:
            with db.connect() as conn:
                jobs.recheck_stuck(conn)
        self.assertEqual(probe.call_count, 1)   # cuma slot-2

    def test_verify_hourly_rereads_slot_after_probe(self):
        self._set_slot("slot-1", status="active", fail_streak=0)
        self._set_slot("slot-2", status="dead")
        with mock.patch("pool.jobs.probes.run_hourly", return_value=("ok", "-")):
            with db.connect() as conn:
                jobs.verify_hourly(conn)
        self.assertEqual(self._slot("slot-1")["status"], "active")

    def test_jobs_are_serialized_by_shared_lock(self):
        """rotate dan verify tidak boleh tumpang tindih - dua exit 7 di
        proxy-1 ('port 9003 after 0 ms') terjadi persis karena ini."""
        order = []

        @jobs.serialized
        def slow(_conn):
            order.append("mulai")
            time.sleep(0.2)
            order.append("selesai")

        with db.connect() as conn:
            threads = [threading.Thread(target=slow, args=(conn,)) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(order, ["mulai", "selesai", "mulai", "selesai"])


if __name__ == "__main__":
    unittest.main()
