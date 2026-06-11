"""Apple security releases — scraped HTML table. See spec §3.4.

``support.apple.com/en-us/100100`` is the canonical list of every security
release across all OSes, in one big ``<table>`` (Name, Available for, Release
date). It's updated before the per-release HT pages get linked, so it's the
first place a new HomePod/iOS version shows up with a date.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser

from ..models import Release, SourceState
from .base import conditional_headers, parse_human_date, sha1

URL = "https://support.apple.com/en-us/100100"

# Family keyword -> product id. A single Name cell can mention several
# ("iOS 17.4.1 and iPadOS 17.4.1"), so we scan for every (family, version) pair.
_FAMILY_MAP = {
    "homepod software": "homepod_software",
    "visionos": "visionos",
    "ipados": "ipados",
    "watchos": "watchos",
    "macos": "macos",
    "tvos": "tvos",
    "ios": "ios",
    "safari": "safari",
}
# Alternation longest-first; version requires at least one dot to avoid catching
# stray numbers. Marketing words (e.g. "Sonoma") between name and version are skipped.
_NAME_RE = re.compile(
    r"(homepod software|visionos|ipados|watchos|macos|tvos|ios|safari)"
    r"[^0-9]*?(\d+(?:\.\d+)+)",
    re.IGNORECASE,
)


def parse_security_name(name: str) -> list[tuple[str, str]]:
    """Extract ``[(product, version), …]`` from a security-release Name cell."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in _NAME_RE.finditer(name):
        product = _FAMILY_MAP[m.group(1).lower()]
        pair = (product, m.group(2))
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out


class AppleSecuritySource:
    name = "apple_security"
    products = [
        "ios",
        "ipados",
        "macos",
        "tvos",
        "watchos",
        "visionos",
        "homepod_software",
        "safari",
    ]
    url = URL

    async def fetch(
        self, client: httpx.AsyncClient, state: SourceState
    ) -> list[Release]:
        r = await client.get(self.url, headers=conditional_headers(state))
        if r.status_code == 304:
            return []
        r.raise_for_status()
        state.etag = r.headers.get("ETag", state.etag)
        state.last_modified = r.headers.get("Last-Modified", state.last_modified)
        return self.parse(r.text)

    def parse(self, html: str) -> list[Release]:
        tree = HTMLParser(html)
        out: list[Release] = []
        seen: set[tuple[str, str]] = set()
        for row in tree.css("table tr"):
            cells = row.css("td")
            if len(cells) < 3:
                continue  # header rows use <th>
            name = cells[0].text(strip=True)
            link_node = cells[0].css_first("a")
            href = link_node.attributes.get("href") if link_node else None
            # Apple uses relative hrefs (/en-us/127118); make them absolute.
            url = urljoin(self.url, href) if href else None
            date_text = cells[2].text(strip=True)
            released_at = parse_human_date(date_text)
            for product, version in parse_security_name(name):
                key = (product, version)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    Release(
                        product=product,
                        version=version,
                        channel="stable",  # security releases are shipping builds
                        released_at=released_at,
                        title=name,
                        url=url,
                        source=self.name,
                        raw_id=sha1(name, date_text or ""),
                    )
                )
        return out
