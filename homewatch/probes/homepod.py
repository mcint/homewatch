"""HomePod LAN probe via mDNS. See spec §4.2.

HomePods broadcast their OS version in the ``_airplay._tcp.local`` TXT record
(``osvers``, ``srcvers``, ``model``, ``deviceid``). Two backends, in order of
preference: pyatv (does the model→family mapping for us) and raw zeroconf (a
lighter dependency). Both are optional extras — imported lazily so the core
service installs without them.
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
    blob = f"{model or ''} {txt.get('model', '')} {txt.get('am', '')}".lower()
    return "audioaccessory" in blob or "homepod" in blob


async def discover_pyatv(timeout: float = 5.0) -> list[Probe]:
    """Discover HomePods with pyatv (preferred). Requires the ``probe`` extra."""
    import asyncio

    try:
        import pyatv
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "pyatv not installed; `uv sync --extra probe` on the LAN host"
        ) from exc

    loop = asyncio.get_running_loop()
    atvs = await pyatv.scan(loop, timeout=timeout)
    probes: list[Probe] = []
    for atv in atvs:
        info = atv.device_info
        model_name = getattr(getattr(info, "model", None), "name", "") or ""
        if not model_name.startswith("HomePod"):
            continue
        probes.append(
            Probe(
                target_kind="homepod",
                target_id=str(atv.identifier),
                version=getattr(info, "version", None),
                extra={
                    "name": atv.name,
                    "model": model_name,
                    "build_number": getattr(info, "build_number", None),
                },
            )
        )
    return probes


async def discover_zeroconf(timeout: float = 5.0) -> list[Probe]:
    """Discover HomePods by reading ``_airplay._tcp`` TXT records directly.

    Lighter than pyatv. Requires the ``zeroconf`` extra.
    """
    import asyncio

    try:
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "zeroconf not installed; `uv sync --extra zeroconf` on the LAN host"
        ) from exc

    service_type = "_airplay._tcp.local."
    found: dict[str, Probe] = {}
    aiozc = AsyncZeroconf()

    async def _resolve(name: str) -> None:
        info = await aiozc.async_get_service_info(service_type, name, timeout=int(timeout * 1000))
        if info is None:
            return
        txt = {
            k.decode("utf-8", "replace"): (v.decode("utf-8", "replace") if isinstance(v, bytes) else v)
            for k, v in (info.properties or {}).items()
        }
        identifier = txt.get("deviceid") or name
        if _is_homepod(None, txt):
            found[identifier] = _txt_to_probe(identifier, txt)

    def _on_change(zeroconf, service_type, name, state_change, **_):
        if state_change is ServiceStateChange.Added:
            asyncio.ensure_future(_resolve(name))

    browser = AsyncServiceBrowser(aiozc.zeroconf, service_type, handlers=[_on_change])
    try:
        await asyncio.sleep(timeout)
    finally:
        await browser.async_cancel()
        await aiozc.async_close()
    return list(found.values())


async def probe_homepods(
    discovery: str = "pyatv", timeout: float = 5.0
) -> list[Probe]:
    """Discover HomePods on the LAN via the configured backend.

    ``discovery`` is one of ``pyatv`` | ``zeroconf`` | ``disabled``.
    """
    if discovery == "disabled":
        return []
    if discovery == "pyatv":
        return await discover_pyatv(timeout)
    if discovery == "zeroconf":
        return await discover_zeroconf(timeout)
    raise ValueError(f"unknown discovery backend {discovery!r}")
