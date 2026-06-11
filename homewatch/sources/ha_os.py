"""Home Assistant OS — GitHub releases atom feed. See spec §3.3."""

from __future__ import annotations

import feedparser
import httpx

from ..models import Release, SourceState
from .base import (
    clean_version,
    conditional_headers,
    detect_channel,
    feed_content_text,
    feed_published_iso,
    summarize,
)

URL = "https://github.com/home-assistant/operating-system/releases.atom"


class HAOSSource:
    name = "ha_os_atom"
    products = ["home_assistant_os"]
    url = URL
    product = "home_assistant_os"

    async def fetch(
        self, client: httpx.AsyncClient, state: SourceState
    ) -> list[Release]:
        r = await client.get(self.url, headers=conditional_headers(state))
        if r.status_code == 304:
            return []
        r.raise_for_status()
        state.etag = r.headers.get("ETag", state.etag)
        state.last_modified = r.headers.get("Last-Modified", state.last_modified)

        feed = feedparser.parse(r.content)
        out: list[Release] = []
        for entry in feed.entries:
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            out.append(
                Release(
                    product=self.product,
                    version=clean_version(title),
                    channel=detect_channel(title),
                    released_at=feed_published_iso(entry),
                    title=title,
                    url=entry.get("link"),
                    source=self.name,
                    raw_id=entry.get("id") or entry.get("link"),
                    notes=summarize(feed_content_text(entry)),
                )
            )
        return out
