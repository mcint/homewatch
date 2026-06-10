"""Apple developer release notes — RSS. See spec §3.5.

Earliest signal (betas + final builds) with build numbers like ``22E240`` —
the build string is what HomePods actually report on the LAN. This feed has
been removed and reinstated before, so a 404 is logged and swallowed, never
fatal.
"""

from __future__ import annotations

import re

import feedparser
import httpx

from ..models import Release, SourceState
from .base import conditional_headers, detect_channel, feed_published_iso, sha1

URL = "https://developer.apple.com/news/releases/rss/releases.rss"

# Longest-first so "ipados" wins over "ios" and "homepod software" over "homepod".
_FAMILY_MAP = {
    "homepod software": "homepod_software",
    "visionos": "visionos",
    "ipados": "ipados",
    "watchos": "watchos",
    "homepod": "homepod_software",
    "macos": "macos",
    "safari": "safari",
    "tvos": "tvos",
    "ios": "ios",
}
_FAMILIES = sorted(_FAMILY_MAP, key=len, reverse=True)

_VERSION_RE = re.compile(r"(\d+(?:\.\d+)*)")
_BUILD_RE = re.compile(r"\(([0-9A-Za-z]+)\)")


def parse_title(title: str) -> Release | None:
    """Parse ``iOS 18.4 (22E240)`` → Release, or None if it isn't a release line."""
    low = title.lower().strip()
    family = next((f for f in _FAMILIES if low.startswith(f)), None)
    if family is None:
        return None
    rest = title[len(family):].strip()
    vm = _VERSION_RE.search(rest)
    if not vm:
        return None
    version = vm.group(1)
    bm = _BUILD_RE.search(rest)
    build = bm.group(1) if bm else None
    notes = f"build {build}" if build else None
    return Release(
        product=_FAMILY_MAP[family],
        version=version,
        channel=detect_channel(title),
        title=title,
        notes=notes,
        source="apple_developer_rss",
        raw_id=sha1(_FAMILY_MAP[family], version, build or ""),
    )


class AppleDeveloperSource:
    name = "apple_developer_rss"
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
        if r.status_code == 404:
            # Feed has been pulled before; treat as "no news", don't crash.
            state.last_status = "error: feed 404 (developer RSS unavailable)"
            return []
        r.raise_for_status()
        state.etag = r.headers.get("ETag", state.etag)
        state.last_modified = r.headers.get("Last-Modified", state.last_modified)

        feed = feedparser.parse(r.content)
        out: list[Release] = []
        for entry in feed.entries:
            rel = parse_title((entry.get("title") or "").strip())
            if rel is None:
                continue
            rel.url = entry.get("link")
            rel.released_at = feed_published_iso(entry)
            out.append(rel)
        return out
