"""HomePod release-notes page — scrape. See spec §3.6.

``support.apple.com/en-us/108045`` is the only human-readable source of HomePod
release notes (version, date, what changed). There is no RSS, and Apple
sometimes ships firmware silently under the same major version, so this page is
the record. The parser is deliberately tolerant: if it can't find any version
headers it logs a warning and records the page hash in source_state — "the page
changed but we couldn't parse it" is itself a useful signal.
"""

from __future__ import annotations

import logging
import re

import httpx
from selectolax.parser import HTMLParser

from ..models import Release, SourceState
from .base import conditional_headers, parse_human_date, sha1

logger = logging.getLogger("homewatch.sources.homepod_notes")

URL = "https://support.apple.com/en-us/108045"

# Section headers read "Software version 26" / "Software version 18.4", but the
# page structure has changed before — match the version with or without the
# "Software version" prefix.
_VERSION_HEADER_RE = re.compile(
    r"Software version\s+(\d+(?:\.\d+)*)", re.IGNORECASE
)
# A date appearing in the notes body, e.g. "January 22, 2026" or "22 January 2026".
_DATE_IN_TEXT_RE = re.compile(
    r"\b(?:[A-Z][a-z]+ \d{1,2}, \d{4}|\d{1,2} [A-Z][a-z]+ \d{4})\b"
)

_NOTES_CAP = 1000


class HomePodNotesSource:
    name = "homepod_notes"
    products = ["homepod_software"]
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
        return self.parse(r.text, state)

    def parse(self, html: str, state: SourceState | None = None) -> list[Release]:
        text = HTMLParser(html).body.text(separator="\n", strip=True) if html else ""
        markers = list(_VERSION_HEADER_RE.finditer(text))
        if not markers:
            page_hash = sha1(text)[:16]
            msg = f"warning: no version sections parsed (page sha={page_hash})"
            logger.warning("%s: %s", self.name, msg)
            if state is not None:
                state.last_status = msg
            return []

        out: list[Release] = []
        seen: set[str] = set()
        for i, m in enumerate(markers):
            version = m.group(1)
            if version in seen:
                continue
            seen.add(version)
            body_start = m.end()
            body_end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
            body = text[body_start:body_end].strip()
            date_m = _DATE_IN_TEXT_RE.search(body)
            released_at = parse_human_date(date_m.group(0)) if date_m else None
            out.append(
                Release(
                    product="homepod_software",
                    version=version,
                    channel="stable",
                    released_at=released_at,
                    title=f"HomePod Software {version}",
                    url=self.url,
                    source=self.name,
                    raw_id=sha1("homepod_software", version),
                    notes=(body[:_NOTES_CAP] or None),
                )
            )
        return out
