"""Dua jalur uji terhadap OLX, sesuai tabel 'tiga kelas jawaban' di PRD:
`curl` cukup untuk memastikan sesuatu *buruk* (referenceNum), tapi vonis
*bersih* butuh browser sungguhan - karena itu ada dua fungsi, bukan satu."""
import subprocess
from datetime import datetime, timezone

from . import config

OUT_DIR = config.ROOT / "pool" / "probe-out"

_BLOCK_MARKER = 'id="referenceNum"'


def _curl(port, url, timeout):
    """(returncode, body, error) lewat binary `curl` - BUKAN lewat library
    `requests`. Keduanya kelihatan sama ("HTTP client generik tanpa browser")
    tapi fingerprint TLS/HTTP2-nya beda di mata Akamai: `requests` disuguhi
    silent-timeout (dites langsung, 15s macet total tanpa balasan di SEMUA 6
    slot yang baru saja terbukti bersih lewat probe browser). Sama prinsipnya
    dengan probe harian yang pakai Chrome asli, bukan reimplementasi - pakai
    alat yang fingerprint-nya sudah terbukti, jangan mendekati lewat library
    lain. returncode -1 = curl sendiri tidak selesai dalam waktunya."""
    proxy = f"http://127.0.0.1:{port}"
    cmd = ["curl", "-sS", "--max-time", str(timeout), "-x", proxy, url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        return -1, "", f"curl timeout {timeout}s"
    return r.returncode, r.stdout, r.stderr.strip()[-300:]


def run_hourly(port, timeout=15):
    """Murah, dua langkah - memisahkan "tunnelnya mati" dari "vonisnya tidak
    diperoleh". Sebelumnya keduanya digabung: SETIAP curl exit != 0 dianggap
    'connecting', sehingga reset HTTP/2 dari Akamai (exit 92, fingerprint TLS
    curl ditolak) mencoret slot yang exit IP-nya sehat - 17/21 vonis
    'connecting' di proxy-1 dan 20/29 di proxy-2 adalah kasus ini.

    Langkah (a) menembak endpoint netral (config.IP_CHECK_URL, teks polos di
    luar CDN anti-bot): gagal di sini = tunnelnya memang tidak menjawab.
    Langkah (b) baru menembak OLX. Kegagalan di (b) SETELAH (a) lolos tidak
    bisa disimpulkan apa-apa tentang exit IP-nya, jadi divonis 'inconclusive'
    dan sengaja tidak mengubah status slot - bukan 'connecting'.

    Klasifikasi lewat dua URL, bukan lewat daftar exit code curl yang
    "berarti tunnel mati": kalau Akamai berganti cara menolak lagi, daftar
    kode itu langsung basi, sedangkan pertanyaan "apakah tunnelnya hidup"
    tetap terjawab benar.

    Return (verdict, detail) - 'ok' (termasuk interstitial bm-verify, itu
    jawaban normal untuk curl), 'blocked', 'connecting', 'inconclusive'."""
    rc, body, err = _curl(port, config.IP_CHECK_URL, timeout)
    if rc != 0:
        return "connecting", f"tunnel mati - {config.IP_CHECK_URL} curl exit {rc}: {err}"
    exit_ip = body.strip().splitlines()[0][:45] if body.strip() else "?"

    rc, body, err = _curl(port, config.OLX_HOURLY_URL, timeout)
    if rc != 0:
        return "inconclusive", (
            f"tunnel sehat (exit {exit_ip}) tapi {config.OLX_HOURLY_URL} "
            f"curl exit {rc}: {err}"
        )
    if _BLOCK_MARKER in body:
        return "blocked", f"referenceNum, {len(body)} byte, exit {exit_ip}"
    return "ok", f"{len(body)} byte, exit {exit_ip}"


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
