"""Dua jalur uji terhadap OLX, sesuai tabel 'tiga kelas jawaban' di PRD:
`curl` cukup untuk memastikan sesuatu *buruk* (referenceNum), tapi vonis
*bersih* butuh browser sungguhan - karena itu ada dua fungsi, bukan satu."""
import subprocess
from datetime import datetime, timezone

from . import config

OUT_DIR = config.ROOT / "pool" / "probe-out"

_BLOCK_MARKER = 'id="referenceNum"'


def run_hourly(port, timeout=15):
    """Murah, lewat binary `curl` - BUKAN lewat library `requests`. Keduanya
    kelihatan sama ("HTTP client generik tanpa browser") tapi fingerprint
    TLS/HTTP2-nya beda di mata Akamai: `requests` disuguhi silent-timeout
    (dites langsung, 15s macet total tanpa balasan di SEMUA 6 slot yang
    baru saja terbukti bersih lewat probe browser), sedangkan `curl` biasa
    dapat balasan dalam ~1.5 detik. Sama prinsipnya dengan probe harian yang
    pakai Chrome asli, bukan reimplementasi - pakai alat yang fingerprint-nya
    sudah terbukti, jangan mendekati lewat library lain.
    Return (verdict, detail) - verdict 'ok' (termasuk interstitial bm-verify,
    itu jawaban normal untuk curl), 'blocked', 'connecting' (proxy/tunnel
    tidak menjawab sama sekali)."""
    proxy = f"http://127.0.0.1:{port}"
    cmd = ["curl", "-sS", "--max-time", str(timeout), "-x", proxy, config.OLX_URL]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        return "connecting", f"curl timeout {timeout}s"
    if r.returncode != 0:
        return "connecting", f"curl exit {r.returncode}: {r.stderr.strip()[-300:]}"
    body = r.stdout
    if _BLOCK_MARKER in body:
        return "blocked", f"referenceNum, {len(body)} byte"
    return "ok", f"{len(body)} byte"


def run_daily(port, slot_id, timeout=90):
    """Mahal, lewat browser sungguhan - dijalankan di image probe sendiri
    (pool/probe/, bukan image crawler produksi; lihat pool/probe/olx_probe.py
    untuk asal salinan kode vonisnya). HTML + screenshot yang dirender ikut
    disimpan ke pool/probe-out/ - bukti yang sama gunanya dengan probe.js,
    dan satu-satunya cara memeriksa "apa yang sebenarnya OLX kirim" tanpa
    menonton Chrome langsung. Return (verdict, detail); verdict 'ok' |
    'blocked' | 'error'."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{slot_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    script = config.ROOT / "pool" / "probe" / "olx_probe.py"
    cmd = [
        # --platform SENGAJA tidak diset di sini (beda dari docker build): image-nya
        # sudah pasti amd64 (dikunci di Dockerfile), tapi --platform di `docker run`
        # membuat Docker Desktop tidak mengenali image lokal yang sudah cocok dan
        # malah mencoba pull dari registry - gagal karena image ini cuma ada lokal.
        # Reproduksi: docker run --platform linux/amd64 olx-pool-probe:latest
        # -> "Unable to find image locally" walau docker images menunjukkannya ada.
        "docker", "run", "--rm", "--network", "host",
        "-e", f"PROXIES=http://127.0.0.1:{port}",
        "-e", f"OLX_URL={config.OLX_URL}",
        "-e", "PROBE_OUT_DIR=/out",
        "-e", f"PROBE_OUT_NAME={name}",
        "-v", f"{script}:/app/olx_probe.py:ro",
        "-v", f"{OUT_DIR}:/out",
        config.PROBE_IMAGE,
        "python3", "olx_probe.py",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "error", f"timeout {timeout}s"

    detail = (r.stdout + r.stderr).strip()[-500:]
    html, png = OUT_DIR / f"{name}.html", OUT_DIR / f"{name}.png"
    if html.exists() or png.exists():
        detail += f" [bukti: pool/probe-out/{name}.html, pool/probe-out/{name}.png]"

    if r.returncode == 0:
        return "ok", detail
    if r.returncode == 1:
        return "blocked", detail
    return "error", detail
