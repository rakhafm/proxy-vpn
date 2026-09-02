"""Menyalakan/mematikan container gluetun per slot lewat `docker` CLI.

subprocess, bukan docker-py: output `docker` mudah dibaca saat mendiagnosa
(lihat PRD-proxy-pool.html §Teknologi).
"""
import subprocess
import time

import requests

from . import config


def container_name(slot_id):
    return f"gluetun-{slot_id}"


def start(slot_id, port, provider, server, pia_user=None, pia_pass=None,
          proton_key=None, proton_user=None, proton_pass=None):
    name = container_name(slot_id)
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)

    env = [
        "-e", "HTTPPROXY=on",
        "-e", "HTTPPROXY_LISTENING_ADDRESS=:8888",
        "-e", "FIREWALL_OUTBOUND_SUBNETS=172.16.0.0/12",
        "-e", "TZ=Asia/Jakarta",
    ]
    if provider == "pia":
        env += [
            "-e", "VPN_SERVICE_PROVIDER=private internet access",
            "-e", "VPN_TYPE=openvpn",
            "-e", f"OPENVPN_USER={pia_user or ''}",
            "-e", f"OPENVPN_PASSWORD={pia_pass or ''}",
            "-e", f"SERVER_NAMES={server}",
        ]
        if config.OPENVPN_MSSFIX:
            env += ["-e", f"OPENVPN_MSSFIX={config.OPENVPN_MSSFIX}"]
    elif provider == "proton":
        # SERVER_HOSTNAMES sama persis untuk openvpn maupun wireguard - gluetun
        # (format-servers) mendaftar tiap hostname Proton dua kali, satu baris
        # per VPN_TYPE, jadi cache servers-proton.txt (yang cuma menyimpan
        # kolom hostname, sudah tersaring lewat servers.sh) valid dipakai apa
        # adanya untuk kedua mode - tidak perlu cache/kandidat terpisah.
        if config.PROTON_VPN_TYPE == "openvpn":
            env += [
                "-e", "VPN_SERVICE_PROVIDER=protonvpn",
                "-e", "VPN_TYPE=openvpn",
                "-e", f"OPENVPN_USER={proton_user or ''}",
                "-e", f"OPENVPN_PASSWORD={proton_pass or ''}",
                "-e", f"SERVER_HOSTNAMES={server}",
            ]
            if config.OPENVPN_MSSFIX:
                env += ["-e", f"OPENVPN_MSSFIX={config.OPENVPN_MSSFIX}"]
        else:
            env += [
                "-e", "VPN_SERVICE_PROVIDER=protonvpn",
                "-e", "VPN_TYPE=wireguard",
                "-e", f"WIREGUARD_PRIVATE_KEY={proton_key or ''}",
                "-e", f"SERVER_HOSTNAMES={server}",
            ]
            if config.WIREGUARD_MTU:
                env += ["-e", f"WIREGUARD_MTU={config.WIREGUARD_MTU}"]
    else:
        raise ValueError(f"provider tidak dikenal: {provider}")

    cmd = [
        "docker", "run", "-d", "--name", name,
        "--cap-add=NET_ADMIN",
        "--device=/dev/net/tun:/dev/net/tun",
        "-p", f"{port}:8888",
        *env,
        "qmcgaw/gluetun:latest",
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def stop(slot_id):
    subprocess.run(["docker", "rm", "-f", container_name(slot_id)], capture_output=True)


def logs(slot_id, tail=40):
    """Log gluetun sampai saat ini - panggil SEBELUM stop(), container yang
    sudah dihapus tidak punya log lagi untuk diambil."""
    r = subprocess.run(
        ["docker", "logs", "--tail", str(tail), container_name(slot_id)],
        capture_output=True, text=True,
    )
    return (r.stdout + r.stderr).strip()


def wait_healthy(slot_id, timeout):
    name = container_name(slot_id)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = subprocess.run(
            ["docker", "inspect", name, "--format", "{{.State.Health.Status}}"],
            capture_output=True, text=True,
        )
        if r.stdout.strip() == "healthy":
            return True
        time.sleep(2)
    return False


def exit_info(port, timeout=15):
    """(ip, negara, org) lewat satu panggilan ipinfo.io - halaman pantau
    (Fase 2) butuh negara/ASN, tidak ada alasan menembak dua kali untuk data
    yang sudah ada di respons yang sama. (None, None, None) kalau proxy
    tidak menjawab."""
    proxy = f"http://127.0.0.1:{port}"
    try:
        r = requests.get(
            "https://ipinfo.io/json",
            proxies={"http": proxy, "https": proxy},
            timeout=timeout,
        )
        d = r.json()
        return d.get("ip"), d.get("country"), d.get("org")
    except requests.RequestException:
        return None, None, None
