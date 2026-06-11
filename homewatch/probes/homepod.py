"""HomePod LAN probe via mDNS. See spec §4.2.

HomePods broadcast their OS version in the ``_airplay._tcp.local`` TXT record
(``osvers``, ``srcvers``, ``model``, ``deviceid``). Two backends, in order of
preference: pyatv (does the model→family mapping for us) and raw zeroconf (a
lighter dependency). Both are optional extras — imported lazily so the core
service installs without them.

mDNS is link-local: the probe host must be on the **same L2 segment** as the
HomePods (or behind an mDNS reflector). ``discover_raw`` lists every AirPlay
device seen (HomePod or not) so "I'm not seeing my HomePod" is debuggable.
"""

from __future__ import annotations

from typing import Any

from ..models import Probe


def _txt_to_probe(identifier: str, txt: dict[str, Any], model: str | None = None) -> Probe:
    """Build a Probe from a TXT-record dict (raw-mDNS path)."""
    version = txt.get("osvers")
    return Probe(
        target_kind="homepod",
        target_id=identifier,
        version=version,
        extra={"model": model, **txt} if model else dict(txt),
    )


def _is_homepod(model: str | None, txt: dict[str, Any]) -> bool:
    """Heuristic: HomePod (incl. mini) reports model 'AudioAccessoryN,N'."""
    blob = f"{model or ''} {txt.get('model', '')} {txt.get('am', '')}".lower()
    return "audioaccessory" in blob or "homepod" in blob


# --- pyatv ---------------------------------------------------------------------


def _service_props(atv) -> dict:
    """Merge AirPlay/RAOP TXT records (osvers, model, am, deviceid, …)."""
    out: dict = {}
    for s in getattr(atv, "services", []) or []:
        for k, v in (getattr(s, "properties", {}) or {}).items():
            out.setdefault(k, v if isinstance(v, str) else str(v))
    return out


async def _scan_pyatv(timeout: float, hosts: list[str] | None = None) -> list[dict]:
    import asyncio

    try:
        import pyatv
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "pyatv not installed; run `uv sync --extra probe` on the LAN host"
        ) from exc

    loop = asyncio.get_running_loop()
    atvs = await pyatv.scan(loop, timeout=timeout, hosts=hosts)
    out = []
    for atv in atvs:
        info = atv.device_info
        txt = _service_props(atv)
        model_name = getattr(getattr(info, "model", None), "name", "") or ""
        out.append({
            # model.name is flaky (often Unknown); fall back to the TXT model
            # (e.g. AudioAccessory1,1) which is the reliable HomePod signal.
            "name": atv.name,
            "identifier": str(atv.identifier),
            "model": model_name or txt.get("model") or "",
            "version": getattr(info, "version", None) or txt.get("osvers"),
            "build_number": getattr(info, "build_number", None),
            "mac": getattr(info, "mac", None) or txt.get("deviceid"),
            "address": str(getattr(atv, "address", "") or "") or None,
            "txt": txt,
        })
    return out


async def discover_pyatv(timeout: float = 5.0,
                         hosts: list[str] | None = None) -> list[Probe]:
    """Discover HomePods with pyatv (preferred). Requires the ``probe`` extra.

    ``hosts`` unicast-scans specific IPs — robust when multicast discovery is
    flaky (the "I know the IP, just probe it" path).
    """
    probes: list[Probe] = []
    for dev in await _scan_pyatv(timeout, hosts=hosts):
        # Check model + name + TXT (model.name alone misses Unknown-model HomePods).
        if not _is_homepod(f"{dev['model']} {dev['name']}", dev["txt"]):
            continue
        probes.append(Probe(
            target_kind="homepod", target_id=dev["identifier"],
            version=dev["version"], extra=dev, ip=dev.get("address"),
            mac=dev.get("mac"),
        ))
    return probes


# --- zeroconf ------------------------------------------------------------------


async def _collect_airplay(timeout: float) -> list[dict]:
    """Browse ``_airplay._tcp`` and return every device's name + TXT dict."""
    import asyncio

    try:
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "zeroconf not installed; run `uv sync --extra zeroconf` on the LAN host"
        ) from exc

    service_type = "_airplay._tcp.local."
    devices: dict[str, dict] = {}
    aiozc = AsyncZeroconf()

    async def _resolve(name: str) -> None:
        info = await aiozc.async_get_service_info(
            service_type, name, timeout=int(timeout * 1000))
        if info is None:
            return
        txt = {
            k.decode("utf-8", "replace"):
                (v.decode("utf-8", "replace") if isinstance(v, bytes) else v)
            for k, v in (info.properties or {}).items()
        }
        identifier = txt.get("deviceid") or name
        devices[identifier] = {"name": name, "identifier": identifier, "txt": txt}

    def _on_change(zeroconf, service_type, name, state_change, **_):
        if state_change is ServiceStateChange.Added:
            asyncio.ensure_future(_resolve(name))

    browser = AsyncServiceBrowser(aiozc.zeroconf, service_type, handlers=[_on_change])
    try:
        await asyncio.sleep(timeout)
    finally:
        await browser.async_cancel()
        await aiozc.async_close()
    return list(devices.values())


async def discover_zeroconf(timeout: float = 5.0) -> list[Probe]:
    """Discover HomePods by reading ``_airplay._tcp`` TXT records directly."""
    probes: list[Probe] = []
    for dev in await _collect_airplay(timeout):
        txt = dev["txt"]
        if _is_homepod(None, txt):
            probes.append(_txt_to_probe(dev["identifier"], txt))
    return probes


# --- dispatch + raw debug scan -------------------------------------------------


async def discover_raw(discovery: str = "pyatv", timeout: float = 5.0,
                       hosts: list[str] | None = None) -> list[dict]:
    """List EVERY AirPlay device discovered (no HomePod filter, no DB write).

    The debugging aid for "I'm not seeing my HomePod": shows what's actually on
    the segment, each device's model/version/IP, and whether we classify it as a
    HomePod. ``hosts`` unicast-scans specific IPs.
    """
    if discovery == "disabled":
        return []
    if discovery == "pyatv":
        return [{"name": d["name"], "identifier": d["identifier"],
                 "model": d["model"], "version": d["version"],
                 "ip": d.get("address"),
                 "is_homepod": _is_homepod(f"{d['model']} {d['name']}", d["txt"])}
                for d in await _scan_pyatv(timeout, hosts=hosts)]
    if discovery == "zeroconf":
        out = []
        for d in await _collect_airplay(timeout):
            txt = d["txt"]
            out.append({
                "name": d["name"], "identifier": d["identifier"],
                "model": txt.get("model", ""), "version": txt.get("osvers"),
                "is_homepod": _is_homepod(None, txt),
            })
        return out
    raise ValueError(f"unknown discovery backend {discovery!r}")


async def probe_homepods(
    discovery: str = "pyatv", timeout: float = 5.0, hosts: list[str] | None = None,
) -> list[Probe]:
    """Discover HomePods on the LAN via the configured backend.

    ``discovery`` is one of ``pyatv`` | ``zeroconf`` | ``disabled``.
    ``hosts`` (pyatv only) unicast-scans specific IPs.
    """
    if discovery == "disabled":
        return []
    if discovery == "pyatv":
        return await discover_pyatv(timeout, hosts=hosts)
    if discovery == "zeroconf":
        return await discover_zeroconf(timeout)
    raise ValueError(f"unknown discovery backend {discovery!r}")
