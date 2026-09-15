"""Profil .ovpn untuk provider 'pia-custom' - didownload dari config generator
PIA lewat pool/get-pia-ovpn.sh, BUKAN ditulis ulang di sini. Login+scrape
HTML PIA cuma boleh punya satu implementasi (skrip itu sendiri menandainya
rapuh - "rusak kalau PIA ubah markup") - dua tempat yang bisa bedrift lebih
buruk daripada shell out sekali lagi, sama seperti candidates.py memanggil
../servers.sh apa adanya.

Skrip itu dulu tinggal di legacy-ovpn/ dan karenanya tidak pernah ikut ter-scp
ke VM oleh pool/deploy/deploy.sh (yang cuma menyalin "pool servers.sh"). Sejak
dipindah ke dalam pool/, slot pia-custom bisa hidup di produksi; legacy-ovpn/
daily.sh yang sekarang memanggil ke sini, bukan sebaliknya.

Dipakai jobs.rotate_slot() untuk provider 'pia-custom': `region` hanya dipakai
untuk menyegarkan cache. Kandidat yang benar-benar dipasang adalah tiap file
.ovpn dengan `remote` IP unik, sehingga dua slot dapat memakai dua profil dari
region yang sama tanpa berbenturan.
"""
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

log = logging.getLogger("pool.pia_custom")

_GEN_SCRIPT = config.ROOT / "pool" / "get-pia-ovpn.sh"
_PROFILE_SET_VERSION = "all-dedup-v1"


def _profile_manifest(region):
    """Penanda bahwa cache `region` sudah dibuat lewat seluruh delapan
    pilihan port/enkripsi generator, dengan remote IP yang dideduplikasi."""
    return Path(config.PIA_CUSTOM_PROFILE_DIR) / f".{region}.{_PROFILE_SET_VERSION}"


def _cached_profiles(region):
    """Semua profil cache untuk `region`, terbaru lebih dulu."""
    out_dir = Path(config.PIA_CUSTOM_PROFILE_DIR)
    return sorted(
        out_dir.glob(f"{region}-*.ovpn"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _remote_ip(profile):
    """IP endpoint dari `remote <ip> <port>` pada satu profil OpenVPN."""
    try:
        for line in profile.read_text(errors="ignore").splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "remote":
                return parts[1]
    except OSError as e:
        log.warning("gagal baca profil %s: %s", profile, e)
    return None


def _unique_profiles(profiles):
    """Satu profil terbaru per IP remote; backup lama dengan endpoint yang
    sama tetap disimpan di disk, tetapi tidak dipakai kandidat lagi."""
    unique = []
    seen = set()
    for profile in profiles:
        remote_ip = _remote_ip(profile)
        if not remote_ip:
            log.warning("profil %s tidak punya baris remote, dilewati", profile)
            continue
        if remote_ip not in seen:
            seen.add(remote_ip)
            unique.append(profile)
    return unique


def ensure_profiles(region, pia_user, pia_pass):
    """Daftar profil unik siap-pakai untuk `region`. Download ulang lewat
    get-pia-ovpn.sh kalau belum ada atau lebih tua dari
    PIA_CUSTOM_PROFILE_MAX_AGE_HOURS. File dari backup batch lama ikut dibaca,
    tetapi hanya satu kandidat per IP remote yang dikembalikan."""
    existing = _cached_profiles(region)
    if existing:
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(
            existing[0].stat().st_mtime, tz=timezone.utc
        )
        # Cache dari sebelum mode all+dedup cuma punya profil default. Jangan
        # tunggu 12 jam untuk menaikkannya: sekali generator sukses, manifest
        # ditulis dan rotasi berikutnya kembali memakai cache seperti biasa.
        if (age < timedelta(hours=config.PIA_CUSTOM_PROFILE_MAX_AGE_HOURS)
                and _profile_manifest(region).is_file()):
            return _unique_profiles(existing)

    if not pia_user or not pia_pass:
        log.warning("region %s: kredensial PIA kosong, tidak bisa download profil", region)
        return _unique_profiles(existing)  # cache lama lebih baik daripada slot mati

    out_dir = Path(config.PIA_CUSTOM_PROFILE_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PIA_USER": pia_user, "PIA_PASS": pia_pass, "PIA_OUT": str(out_dir)}
    # Suffix unik per refresh: generator hanya menghapus duplikat IP dari
    # batch yang sedang dibuat. Profil dari refresh sebelumnya tetap utuh
    # sebagai backup dan tidak tertimpa saat cache 12 jam diperbarui.
    suffix = f"pool-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    try:
        r = subprocess.run(
            # Generator PIA punya delapan kartu port/enkripsi. Ambil semua
            # supaya pool tidak terpaku pada UDP/1198, lalu biarkan skrip
            # membuang file yang `remote` IP-nya sama. `--dedup-ip` berlaku
            # untuk seluruh daftar `all`, bukan terpisah UDP/TCP.
            [str(_GEN_SCRIPT), "-t", "all", "--dedup-ip", "-s", suffix, region],
            capture_output=True, text=True, env=env, timeout=60,
        )
    except subprocess.TimeoutExpired:
        log.warning("region %s: get-pia-ovpn.sh timeout", region)
        return _unique_profiles(existing)
    if r.returncode != 0:
        log.warning(
            "region %s: get-pia-ovpn.sh gagal (exit %d): %s",
            region, r.returncode, (r.stdout + r.stderr).strip()[-300:],
        )
        return _unique_profiles(existing)  # cache lama lebih baik daripada slot mati

    fresh = _cached_profiles(region)
    if not fresh:
        log.warning("region %s: get-pia-ovpn.sh sukses tapi file profil tidak ketemu", region)
    else:
        _profile_manifest(region).touch()
    return _unique_profiles(fresh)


def ensure_profile(region, pia_user, pia_pass):
    """Kompatibilitas untuk caller lama: profil kandidat pertama, atau None."""
    profiles = ensure_profiles(region, pia_user, pia_pass)
    return profiles[0] if profiles else None
