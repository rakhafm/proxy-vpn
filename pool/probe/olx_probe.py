#!/usr/bin/env python3
"""Probe harian pool manager. Jalan di image sendiri (pool/probe/Dockerfile),
BUKAN image crawler produksi - tidak butuh login Harbor, dan build/rilis
crawler tidak ikut mempengaruhi probe.

`_chrome_options()` dan `OLX_SEARCH_MARKERS`/`olx_search_markers()` di bawah
adalah SALINAN, bukan import, dari:
  config/scrape.py:88   _chrome_options()          (asl-crawler-pricing-engine-v2)
  utility/utility.py:76 OLX_SEARCH_MARKERS
  utility/utility.py:90 olx_search_markers()

Disalin apa adanya supaya fingerprint browser dan kriteria "lolos" identik
dengan scrapeselenium() yang sebenarnya memuat OLX di produksi - itu satu-
satunya alasan probe ini menjalankan Chrome sama sekali, bukan sekadar
`requests`. Kalau salah satu berubah di repo crawler, sinkronkan ulang di
sini secara manual; tidak ada mekanisme otomatis yang menjaga keduanya sama.

Exit 0 = lolos - OLX_URL (root domain) dimuat, tanpa penanda deny
         (id="referenceNum"). Sejak 2026-09-03 vonis dipatok ke root domain,
         BUKAN ke OLX_SEARCH_MARKERS di halaman listing lagi: OLX/Akamai
         terbukti me-redirect diam-diam mobil-bekas_c198 ke homepage (200
         penuh, 0/5 marker, tanpa referenceNum) - dulu ini jatuh sebagai
         exit 1 dan mencoret exit yang sebenarnya bersih.
Exit 1 = diblokir - id="referenceNum" ditemukan di OLX_URL (bukan error
         jaringan - lihat exit 2)
Exit 2 = error lain: proxy kosong, tunnel tidak menjawab, Chrome gagal start,
         atau halaman error jaringan bawaan Chrome (neterror, mis.
         ERR_HTTP2_PROTOCOL_ERROR) - exit ini TIDAK menilai apa pun tentang
         exit IP, jangan dicatat sebagai "blocked" di candidates table

OLX_VALIDATE_URL (listing, default mobil-bekas_c198) ikut dites best-effort
setelah vonis di atas selesai, murni untuk bukti/log (markers, bytes) -
kegagalan atau 0 marker di sini TIDAK PERNAH mengubah exit code. Ini
keputusan sadar yang melonggarkan jaminan PRD "vonis pool = vonis crawler"
(dulu satu-satunya kriteria "lolos" adalah OLX_SEARCH_MARKERS produksi) demi
proxy tetap terbit selama homepage-nya bersih, walau listing masih diredirect.
"""
import os
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# --- salinan dari utility/utility.py:76-96 -------------------------------

OLX_SEARCH_MARKERS = ('wnLBS', 'rui-TLmk0', '_21Jxw', '_3V_Ww', '_3VRSm')
OLX_SEARCH_MARKER_SELECTOR = ", ".join(f".{marker}" for marker in OLX_SEARCH_MARKERS)


def olx_search_markers(soup):
    found = []
    for marker in OLX_SEARCH_MARKERS:
        if soup.find(class_=marker) is not None:
            found.append(marker)
    return found

# --- salinan dari config/scrape.py:88-97 (headless() disederhanakan: probe
# selalu headless, tidak ada mode HEADLESS=0 untuk menonton di layar) -------


def _chrome_options(user_agent, proxy=None):
    options = webdriver.ChromeOptions()
    options.add_argument(f"user-agent={user_agent}")
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")
    options.add_argument("--headless=new")
    for arg in ('--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled'):
        options.add_argument(arg)
    return options


# --- bagian probe sendiri, bukan salinan ----------------------------------

# Bukan salinan dari crawler produksi - kriteria pool sendiri, sama dengan
# _BLOCK_MARKER di pool/probes.py (cek per-jam via curl).
_BLOCK_MARKER = 'id="referenceNum"'

CHROMEDRIVER_PATH = os.environ.get("CHROMEDRIVER_PATH", "/usr/src/app/chromedriver/chromedriver")
URL = os.environ.get("OLX_URL", "https://www.olx.co.id/")
# Validasi non-gating saja - lihat docstring modul. Kosong = lewati.
VALIDATE_URL = os.environ.get("OLX_VALIDATE_URL", "")
PROXY = os.environ.get("PROXIES", "").splitlines()[0].strip() if os.environ.get("PROXIES") else ""
UA = os.environ.get(
    "AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
)
# Dipetakan ke direktori host lewat -v di pool/probes.py - kalau kosong (mis.
# dipanggil manual tanpa mount), bukti tidak disimpan, itu bukan error.
OUT_DIR = os.environ.get("PROBE_OUT_DIR", "")
OUT_NAME = os.environ.get("PROBE_OUT_NAME", "probe")


def _save_evidence(driver, name=None):
    if not OUT_DIR:
        return
    name = name or OUT_NAME
    try:
        out = Path(OUT_DIR)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{name}.html").write_text(driver.page_source, errors="ignore")
        driver.save_screenshot(str(out / f"{name}.png"))
    except Exception as e:
        print(f"gagal simpan bukti: {e}", file=sys.stderr)


def main():
    if not PROXY:
        print("PROXIES kosong", file=sys.stderr)
        return 2

    # Chrome gagal start (crash proses, driver tidak reachable) BUKAN sinyal
    # tentang exit IP - jangan divoniskan "diblokir" karena itu meracuni
    # candidates table (server itu tidak akan dicoba lagi selamanya) padahal
    # yang salah cuma browsernya. Kegagalan di sini -> exit 2 (error), dipisah
    # tegas dari "halaman dimuat tapi nol marker" -> exit 1 (blocked).
    try:
        service = Service(CHROMEDRIVER_PATH)
        driver = webdriver.Chrome(service=service, options=_chrome_options(UA, PROXY))
    except Exception as e:
        print(f"chrome gagal start: {e}", file=sys.stderr)
        return 2

    try:
        driver.set_page_load_timeout(30)
        try:
            driver.get(URL)
        except Exception as e:
            print(f"gagal memuat {URL}: {e}", file=sys.stderr)
            _save_evidence(driver)  # best-effort - mungkin cuma about:blank
            return 2

        # Selenium TIDAK melempar exception untuk error jaringan bawaan Chrome
        # (mis. ERR_HTTP2_PROTOCOL_ERROR, ERR_CONNECTION_RESET) - halaman
        # interstitial-nya dirender sebagai DOM biasa, jadi try/except di atas
        # tidak pernah kena. current_url TIDAK bisa dipakai mendeteksinya:
        # sejak "committed interstitials" (Chrome ~71+) halaman error dicommit
        # di URL yang diminta, bukan chrome-error:// (dicoba & terbukti gagal
        # di produksi - lihat riwayat probe 2026-08-24). Penanda yang terbukti
        # stabil dari bukti nyata (pool/probe-out/slot-1-20260824T101039Z.html,
        # ERR_HTTP2_PROTOCOL_ERROR): template neterror.html Chromium selalu
        # punya id="main-frame-error". Tanpa cek ini, error transport ikut
        # dihitung 0 marker -> divoniskan "blocked" (exit 1) dan meracuni
        # candidates table 24 jam padahal exit IP-nya tidak pernah benar-benar
        # dites ke OLX.
        if 'id="main-frame-error"' in driver.page_source:
            print(f"halaman error jaringan Chrome saat memuat {URL}", file=sys.stderr)
            _save_evidence(driver)
            return 2

        blocked = _BLOCK_MARKER in driver.page_source
        print(f"blocked={blocked} bytes={len(driver.page_source)}")
        _save_evidence(driver)
        verdict = 1 if blocked else 0

        # Validasi non-gating: dicatat, tidak pernah mengubah `verdict`.
        if VALIDATE_URL:
            try:
                driver.set_page_load_timeout(30)
                driver.get(VALIDATE_URL)
                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, OLX_SEARCH_MARKER_SELECTOR))
                    )
                except TimeoutException:
                    pass  # marker tidak muncul dalam waktu - tetap dinilai dari isinya di bawah
                markers = olx_search_markers(BeautifulSoup(driver.page_source, "html.parser"))
                print(f"validasi {VALIDATE_URL}: markers={len(markers)} bytes={len(driver.page_source)}")
                _save_evidence(driver, name=f"{OUT_NAME}-validate")
            except Exception as e:
                print(f"validasi {VALIDATE_URL} gagal (non-gating): {e}", file=sys.stderr)

        return verdict
    finally:
        driver.quit()


if __name__ == "__main__":
    sys.exit(main())
