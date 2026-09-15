#!/usr/bin/env python3
"""Cek daftar URL (dari CSV pool, lihat pool/jobs.check_urls) lewat satu proxy
dengan Chrome sungguhan. Jalan di image probe yang sama dengan olx_probe.py
dan MENGIMPOR _chrome_options()/olx_search_markers() dari sana - bukan
menyalin lagi, supaya salinan manual dari repo crawler tetap cuma satu
(lihat header olx_probe.py).

Beda dari olx_probe.py, skrip ini TIDAK menggerbang apa pun: status slot
tidak berubah karena hasil di sini. Ia cuma menjawab "apakah URL X benar-
benar bisa dipakai crawler lewat exit ini" dan menyimpan bukti.

Input (env):
  PROXIES        proxy http://host:port (baris pertama kalau banyak)
  CHECK_URLS     JSON: [{"name": ..., "url": ...}, ...]
  PROBE_OUT_DIR  direktori bukti (kosong = bukti tidak disimpan)
  PROBE_OUT_NAME prefiks nama file bukti; per URL jadi <prefiks>-<name>

Output: satu baris JSON per URL di stdout:
  {"name", "url", "verdict", "detail", "final_url", "bytes", "bukti": [..]}
verdict: ok | blocked | empty | error
  ok      = konten yang diharapkan benar-benar ada (lihat _judge)
  blocked = penanda deny terbaca (Akamai referenceNum / challenge Cloudflare)
  empty   = halaman dimuat tanpa penanda deny, tapi konten listing tidak ada
            (mis. redirect diam Akamai ke homepage) - bagi crawler ini GAGAL
  error   = halaman error jaringan Chrome / timeout - bukan vonis tentang IP

Exit 0 kalau semua URL sempat dinilai (apa pun vonisnya), 2 kalau Chrome
gagal start (tidak ada satu pun URL yang dinilai).
"""
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

sys.path.insert(0, str(Path(__file__).resolve().parent))
from olx_probe import (  # noqa: E402
    CHROMEDRIVER_PATH, OLX_SEARCH_MARKER_SELECTOR, UA, _BLOCK_MARKER,
    _chrome_options, olx_search_markers,
)

PROXY = os.environ.get("PROXIES", "").splitlines()[0].strip() if os.environ.get("PROXIES") else ""
OUT_DIR = os.environ.get("PROBE_OUT_DIR", "")
OUT_NAME = os.environ.get("PROBE_OUT_NAME", "check")

# Penanda challenge Cloudflare ("Just a moment..."): halaman interstitial
# yang dimuat sebelum situs aslinya. Kalau Chrome masih berhenti di sini
# setelah menunggu, exit IP-nya ditahan Cloudflare - itu vonis tentang IP,
# sama kelasnya dengan referenceNum Akamai. BUKAN "challenge-platform":
# string itu ada di halaman mobil123 yang normal juga (script pasif
# /cdn-cgi/challenge-platform/), terbukti dari bukti 2026-09-15 - dipakai
# sebagai penanda ia memvonis halaman 50 listing sebagai "blocked".
_CF_MARKERS = ("cf-chl", "<title>Just a moment")

# Penanda kartu listing mobil123 - dari bukti nyata halaman listing lewat
# Chrome 2026-09-15 (curl langsung kena challenge Cloudflare, jadi tidak
# bisa dilihat tanpa browser): tiap iklan satu <div class="listing
# listing--card ...">, 50 per halaman.
_MOBIL123_LISTING_MARKER = "listing--card"


def _safe(name):
    return re.sub(r"[^\w.-]+", "_", name)[:60]


def _save(driver, name):
    if not OUT_DIR:
        return []
    try:
        out = Path(OUT_DIR)
        out.mkdir(parents=True, exist_ok=True)
        html, png = out / f"{name}.html", out / f"{name}.png"
        html.write_text(driver.page_source, errors="ignore")
        driver.save_screenshot(str(png))
        return [html.name, png.name]
    except Exception as e:
        print(f"gagal simpan bukti {name}: {e}", file=sys.stderr)
        return []


def _judge(url, source):
    """(verdict, detail) untuk satu halaman yang sudah dimuat penuh."""
    if 'id="main-frame-error"' in source:
        return "error", "halaman error jaringan Chrome"
    if _BLOCK_MARKER in source:
        return "blocked", "referenceNum (deny Akamai)"
    if any(m in source for m in _CF_MARKERS):
        return "blocked", "challenge Cloudflare tidak lewat"

    host = urlparse(url).netloc.lower()
    path = urlparse(url).path.rstrip("/")
    if host.endswith("olx.co.id"):
        if not path:
            return "ok", "homepage tanpa referenceNum"
        markers = olx_search_markers(BeautifulSoup(source, "html.parser"))
        if markers:
            return "ok", f"markers={len(markers)}"
        return "empty", "0 OLX_SEARCH_MARKERS (redirect diam ke homepage?)"
    if host.endswith("mobil123.com"):
        n = source.count(_MOBIL123_LISTING_MARKER)
        if n:
            return "ok", f"{n} kartu listing"
        return "empty", "tidak ada kartu listing mobil123"
    # Situs lain: tidak ada pengetahuan konten, cukup "dimuat tanpa deny".
    return "ok", "dimuat tanpa penanda deny"


def _check_one(driver, item):
    name, url = item["name"], item["url"]
    result = {"name": name, "url": url, "final_url": "", "bytes": 0, "bukti": []}
    try:
        driver.set_page_load_timeout(45)
        driver.get(url)
        host = urlparse(url).netloc.lower()
        # Beri waktu konten dinamis: listing OLX dirender klien, dan
        # challenge Cloudflare butuh beberapa detik untuk lolos sendiri.
        try:
            if host.endswith("olx.co.id"):
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, OLX_SEARCH_MARKER_SELECTOR))
                )
            else:
                WebDriverWait(driver, 20).until(
                    lambda d: not any(m in d.page_source for m in _CF_MARKERS)
                )
        except TimeoutException:
            pass  # dinilai dari isinya di bawah apa adanya
        source = driver.page_source
        result["final_url"] = driver.current_url
        result["bytes"] = len(source)
        result["verdict"], result["detail"] = _judge(url, source)
    except Exception as e:
        result["verdict"], result["detail"] = "error", f"gagal memuat: {e}"[:300]
    result["bukti"] = _save(driver, f"{OUT_NAME}-{_safe(name)}")
    return result


def main():
    if not PROXY:
        print("PROXIES kosong", file=sys.stderr)
        return 2
    try:
        items = json.loads(os.environ.get("CHECK_URLS", "[]"))
    except json.JSONDecodeError as e:
        print(f"CHECK_URLS bukan JSON: {e}", file=sys.stderr)
        return 2
    if not items:
        print("CHECK_URLS kosong", file=sys.stderr)
        return 2

    try:
        driver = webdriver.Chrome(service=Service(CHROMEDRIVER_PATH), options=_chrome_options(UA, PROXY))
    except Exception as e:
        print(f"chrome gagal start: {e}", file=sys.stderr)
        return 2
    try:
        for item in items:
            print(json.dumps(_check_one(driver, item), ensure_ascii=False), flush=True)
    finally:
        driver.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
