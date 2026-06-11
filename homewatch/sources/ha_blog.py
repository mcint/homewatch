"""Home Assistant blog — release-day announcement posts. See spec §3.2.

Curated posts like "2026.4: …" carry breaking-changes / integration notes
that are better signal for HomeKit/Matter churn than the raw GH tag. We only
keep entries whose title looks like a release announcement.
"""

from __future__ import annotations

import re

import feedparser
import httpx

from ..models import Release, SourceState
from .base import (
    conditional_headers,
    feed_content_text,
    feed_published_iso,
    summarize,
)

URL = "https://www.home-assistant.io/atom.xml"

# Titles look like "2026.4: Performance, voice, …" or "2026.4.1:".
_RELEASE_TITLE = re.compile(r"^(\d{4}\.\d+(?:\.\d+)?):")


class HABlogSource:
    name = "ha_blog_atom"
    products = ["home_assistant_core"]
    url = URL
    product = "home_assistant_core"

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
            m = _RELEASE_TITLE.match(title)
            if not m:
                continue
            summary = summarize(feed_content_text(entry))
            out.append(
                Release(
                    product=self.product,
                    version=m.group(1),
                    channel="stable",  # blog only posts stable release-day write-ups
                    released_at=feed_published_iso(entry),
                    title=title,
                    url=entry.get("link"),
                    source=self.name,
                    raw_id=entry.get("id") or entry.get("link"),
                    notes=summary,
                )
            )
        return out
