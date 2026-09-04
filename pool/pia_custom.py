"""Profil .ovpn untuk provider 'pia-custom' - didownload dari config generator
PIA lewat legacy-ovpn/get-pia-ovpn.sh, BUKAN ditulis ulang di sini. Login+scrape
HTML PIA cuma boleh punya satu implementasi (skrip itu sendiri menandainya
rapuh - "rusak kalau PIA ubah markup") - dua tempat yang bisa bedrift lebih
buruk daripada shell out sekali lagi, sama seperti candidates.py memanggil
../servers.sh apa adanya.

Dipakai orchestrator.start() untuk provider 'pia-custom': `region` di sini
adalah "server" yang dikembalikan candidates.next_candidate() untuk provider
itu (kode region punya get-pia-ovpn.sh, mis. "sg", "jakarta" - lihat
config.PIA_CUSTOM_REGIONS), bukan hostname/IP seperti provider pia/proton.
"""
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

log = logging.getLogger("pool.pia_custom")

_GEN_SCRIPT = config.ROOT / "legacy-ovpn" / "get-pia-ovpn.sh"


def _existing_profile(region):
    """Profil TERBARU untuk `region` di PIA_CUSTOM_PROFILE_DIR, atau None.
    Glob by prefix, bukan nama file tetap - default cipher/tipe get-pia-ovpn.sh
    bukan sesuatu yang mau disalin ulang di sini, biar tidak dua tempat bisa
    beda kalau default-nya berubah."""
    out_dir = Path(config.PIA_CUSTOM_PROFILE_DIR)
    matches = sorted(
        out_dir.glob(f"{region}-*.ovpn"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def ensure_profile(region, pia_user, pia_pass):
    """Path profil siap-pakai untuk `region`. Download ulang lewat
    get-pia-ovpn.sh kalau belum ada atau lebih tua dari
    PIA_CUSTOM_PROFILE_MAX_AGE_HOURS. Return None kalau tidak ada profil sama
    sekali (download gagal DAN tidak ada cache lama) - caller (orchestrator)
    yang menerjemahkan itu jadi kegagalan kandidat, bukan exception di sini,
    supaya rotasi lanjut ke kandidat berikutnya alih-alih berhenti total."""
    existing = _existing_profile(region)
    if existing is not None:
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(
            existing.stat().st_mtime, tz=timezone.utc
        )
        if age < timedelta(hours=config.PIA_CUSTOM_PROFILE_MAX_AGE_HOURS):
            return existing

    if not pia_user or not pia_pass:
        log.warning("region %s: kredensial PIA kosong, tidak bisa download profil", region)
        return existing  # cache lama (kalau ada) lebih baik daripada slot mati

    out_dir = Path(config.PIA_CUSTOM_PROFILE_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PIA_USER": pia_user, "PIA_PASS": pia_pass, "PIA_OUT": str(out_dir)}
    try:
        r = subprocess.run(
            [str(_GEN_SCRIPT), "-s", "", region],
            capture_output=True, text=True, env=env, timeout=60,
        )
    except subprocess.TimeoutExpired:
        log.warning("region %s: get-pia-ovpn.sh timeout", region)
        return existing
    if r.returncode != 0:
        log.warning(
            "region %s: get-pia-ovpn.sh gagal (exit %d): %s",
            region, r.returncode, (r.stdout + r.stderr).strip()[-300:],
        )
        return existing  # exit 2 kredensial ditolak, 3 markup berubah, 4 respons aneh - lihat README

    fresh = _existing_profile(region)
    if fresh is None:
        log.warning("region %s: get-pia-ovpn.sh sukses tapi file profil tidak ketemu", region)
    return fresh
