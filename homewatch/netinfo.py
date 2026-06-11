"""Best-effort local network context for probe sightings (spec §13).

Everything here is best-effort and may return None: SSID needs platform tools,
and DHCP/DNS names can change. Callers store whatever they get as *last-seen*.
"""

from __future__ import annotations

import socket
import subprocess


def local_ip() -> str | None:
    """The prober's primary IPv4 (no traffic actually sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def subnet_of(ip: str | None, prefix: int = 24) -> str | None:
    """Coarse L2 range as CIDR (assumes /24 — best-effort)."""
    if not ip or ip.count(".") != 3:
        return None
    a, b, c, _ = ip.split(".")
    return f"{a}.{b}.{c}.0/{prefix}"


def current_ssid() -> str | None:
    """Connected WiFi SSID via platform tools (macOS / Linux), else None."""
    probes = (
        ["/usr/sbin/networksetup", "-getairportnetwork", "en0"],  # macOS
        ["iwgetid", "-r"],                                        # Linux
        ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],      # NetworkManager
    )
    for cmd in probes:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode != 0:
            continue
        text = out.stdout.strip()
        if not text:
            continue
        if cmd[0].endswith("networksetup"):
            # "Current Wi-Fi Network: <ssid>"
            return text.split(":", 1)[1].strip() if ":" in text else None
        if cmd[0] == "nmcli":
            for line in text.splitlines():
                if line.startswith("yes:"):
                    return line.split(":", 1)[1] or None
            continue
        return text  # iwgetid
    return None


def context() -> dict[str, str | None]:
    """The prober's (ssid, ip, subnet) in one call."""
    ip = local_ip()
    return {"ssid": current_ssid(), "ip": ip, "subnet": subnet_of(ip)}
