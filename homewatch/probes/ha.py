"""Home Assistant LAN probe via the REST API. See spec §4.1.

``GET {HA_URL}/api/config`` returns the running version + installation_type.
A failed probe (HA actually down, token expired) is itself useful signal, so
we return a Probe with ``error`` set rather than raising.
"""

from __future__ import annotations

import httpx

from ..models import Probe


async def probe_ha(
    url: str, token: str | None, *, client: httpx.AsyncClient | None = None, timeout: float = 10.0
) -> Probe:
    """Probe the configured HA instance. Never raises — failures become a Probe."""
    target = url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        r = await client.get(f"{target}/api/config", headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        return Probe(
            target_kind="home_assistant",
            target_id=url,
            version=data.get("version"),
            extra=data,
        )
    except httpx.HTTPStatusError as exc:
        return Probe(
            target_kind="home_assistant",
            target_id=url,
            error=f"HTTP {exc.response.status_code}",
        )
    except httpx.HTTPError as exc:
        # Connection refused / timeout / TLS — HA may simply be down.
        return Probe(
            target_kind="home_assistant",
            target_id=url,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if owns_client:
            await client.aclose()
