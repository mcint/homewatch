"""On-demand full release-notes fetch (spec §12.1).

The DB stores only a short summary + the upstream URL. The full body is fetched
live here when the user runs `homewatch show ...` — nothing is persisted.
"""

from __future__ import annotations

import sqlite3

import httpx
from selectolax.parser import HTMLParser

from .sources.homepod_notes import URL as HOMEPOD_NOTES_URL
from .sources.homepod_notes import HomePodNotesSource

# Preferred content containers, most-specific first; falls back to <body>.
_CONTENT_SELECTORS = (".markdown-body", "article", "main", "div[role=main]")
_BODY_CAP = 8000


def _extract_text(html: str) -> str:
    tree = HTMLParser(html)
    for sel in _CONTENT_SELECTORS:
        node = tree.css_first(sel)
        if node is not None:
            text = node.text(separator="\n", strip=True)
            if text:
                return text[:_BODY_CAP]
    body = tree.body
    return (body.text(separator="\n", strip=True)[:_BODY_CAP]) if body else ""


async def fetch_full_notes(client: httpx.AsyncClient, row: sqlite3.Row) -> str:
    """Return the full release notes for a release row, fetched from its source.

    HomePod releases are re-parsed from the notes page section; everything else
    is fetched from the stored URL and reduced to readable text. Returns the
    stored summary if there's nothing better to fetch.
    """
    product = row["product"]
    stored = row["notes"] or ""

    if product == "homepod_software":
        try:
            r = await client.get(HOMEPOD_NOTES_URL)
            r.raise_for_status()
            for rel in HomePodNotesSource().parse(r.text):
                if rel.version == row["version"]:
                    return rel.notes or stored
        except httpx.HTTPError:
            return stored
        return stored

    url = row["url"]
    if not url:
        return stored
    try:
        r = await client.get(url)
        r.raise_for_status()
    except httpx.HTTPError:
        return stored
    return _extract_text(r.text) or stored
