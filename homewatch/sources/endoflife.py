"""endoflife.date — authoritative release dates for Apple OSes. See spec §3.8.

endoflife.date exposes, per product, a list of release *cycles* (majors) with a
``releaseDate`` (when ``X.0`` shipped) and the cycle's ``latest`` patch with its
``latestReleaseDate``. It does not carry HomePod or Home Assistant, but HomePod
software tracks tvOS versions, so tvOS dates are reused to date HomePod releases
at display time (see timeline.derive_date).

We emit two dated points per cycle — ``{cycle}.0`` and ``latest`` — which
gap-fill dates onto versions other sources already discovered (Apple security /
developer) and add the major-release dates outright.
"""

from __future__ import annotations

import httpx

from ..models import Release, SourceState
from .base import sha1, summarize

API = "https://endoflife.date/api/{slug}.json"

# endoflife slug -> our product id (identical here, but explicit).
PRODUCTS = {
    "ios": "ios",
    "ipados": "ipados",
    "macos": "macos",
    "tvos": "tvos",
    "watchos": "watchos",
    "visionos": "visionos",
}


def _cycle_dot_zero(cycle: str) -> str:
    """'26' -> '26.0'; '10.15' -> '10.15' (already has a minor)."""
    return cycle if "." in cycle else f"{cycle}.0"


class EndOfLifeSource:
    name = "endoflife"
    products = list(PRODUCTS.values())
    url = "https://endoflife.date/"

    async def fetch(
        self, client: httpx.AsyncClient, state: SourceState
    ) -> list[Release]:
        out: list[Release] = []
        errors: list[str] = []
        for slug, product in PRODUCTS.items():
            try:
                r = await client.get(API.format(slug=slug))
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                cycles = r.json()
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"{slug}: {type(exc).__name__}")
                continue
            out.extend(self._cycles_to_releases(product, slug, cycles))
        if errors:
            state.last_status = "warning: " + ", ".join(errors)
        return out

    def _cycles_to_releases(self, product: str, slug: str, cycles: list) -> list[Release]:
        url = f"https://endoflife.date/{slug}"
        out: list[Release] = []
        for c in cycles:
            cycle = str(c.get("cycle") or "").strip()
            eol = c.get("eol")
            notes = summarize(f"EOL {eol}") if isinstance(eol, str) else None
            # Cycle initial release (X.0).
            if cycle and c.get("releaseDate"):
                v = _cycle_dot_zero(cycle)
                out.append(Release(
                    product=product, version=v, channel="stable",
                    released_at=c["releaseDate"], title=f"{product} {v}", url=url,
                    source=self.name, raw_id=sha1("eol", product, v), notes=notes,
                ))
            # Latest patch on the cycle.
            latest = str(c.get("latest") or "").strip()
            if latest and c.get("latestReleaseDate"):
                out.append(Release(
                    product=product, version=latest, channel="stable",
                    released_at=c["latestReleaseDate"], title=f"{product} {latest}",
                    url=url, source=self.name, raw_id=sha1("eol", product, latest),
                    notes=notes,
                ))
        return out
