"""CLI transport backends (spec §11.1).

One async ``Backend`` interface, two implementations the CLI picks between:

* ``LocalBackend`` — the default; talks straight to the local SQLite file and
  the upstream feeds, no server required.
* ``RemoteBackend`` — used when ``HOMEWATCH_URL`` / ``--remote`` is set; the same
  operations over HTTP to a running daemon, carrying the bearer token.

Both expose the same coroutine methods returning plain dict/list/str so the CLI
is transport-agnostic.
"""

from __future__ import annotations

import sqlite3
from typing import Protocol

import httpx

from . import notes, probes, sources, til, timeline
from .config import Settings
from .db import checkpoint, get_db
from .sources import base


def _row_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


class Backend(Protocol):
    async def refresh(self, source: str | None = None) -> dict: ...
    async def til(self, *, kind: str, target: str | None, text: str,
                  tags: str | None, at: str | None, probe: bool) -> int: ...
    async def timeline(self, *, since: str | None, until: str | None,
                       products: list[str] | None, include_betas: bool,
                       fmt: str) -> str: ...
    async def releases(self, *, product: str | None, since: str | None,
                       until: str | None, channel: str | None) -> list[dict]: ...
    async def latest(self, product: str, channel: str) -> dict | None: ...
    async def show(self, product: str, version: str | None, channel: str) -> dict | None: ...
    async def probe_ha(self) -> dict: ...
    async def probe_homepods(self) -> list[dict]: ...
    async def sources(self) -> list[dict]: ...


class LocalBackend:
    """Direct DB + fetch. Use as an async context manager so the WAL is
    checkpointed and the httpx client closed on exit."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.conn: sqlite3.Connection | None = None
        self.client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "LocalBackend":
        self.conn = get_db(self.settings.db)
        self.client = httpx.AsyncClient(
            headers={"User-Agent": self.settings.user_agent},
            follow_redirects=True,
            timeout=30,
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self.client is not None:
            await self.client.aclose()
        if self.conn is not None:
            checkpoint(self.conn)
            self.conn.close()

    async def refresh(self, source: str | None = None) -> dict:
        return await sources.refresh(self.conn, self.client, source=source)

    async def til(self, *, kind, target, text, tags, at, probe) -> int:
        rid = til.record(self.conn, kind=kind, target=target, text=text,
                         tags=tags, at=at, source="cli")
        if probe:
            await probes.autoprobe(self.conn, self.client, target, self.settings)
        return rid

    async def timeline(self, *, since, until, products, include_betas, fmt) -> str:
        items = timeline.build(self.conn, since=since, until=until,
                               products=products, include_betas=include_betas)
        if fmt == "html":
            return timeline.render_html(items)
        if fmt == "md":
            return timeline.render_md(items)
        return timeline.to_json(items)

    async def releases(self, *, product, since, until, channel) -> list[dict]:
        rows = base.list_releases(self.conn, product=product, since=since,
                                  until=until, channel=channel)
        return [_row_dict(r) for r in rows]

    async def latest(self, product, channel) -> dict | None:
        row = base.latest_release(self.conn, product, channel)
        return _row_dict(row) if row else None

    async def show(self, product, version, channel) -> dict | None:
        if version:
            row = self.conn.execute(
                "SELECT * FROM releases WHERE product=? AND version=? AND channel=?",
                (product, version, channel),
            ).fetchone()
        else:
            row = base.latest_release(self.conn, product, channel)
        if row is None:
            return None
        full = await notes.fetch_full_notes(self.client, row)
        out = _row_dict(row)
        out["notes_full"] = full
        return out

    async def probe_ha(self) -> dict:
        probe = await probes.probe_ha(
            self.settings.ha_url, self.settings.ha_token, client=self.client
        )
        pid = probes.insert_probe(self.conn, probe)
        return {"id": pid, "version": probe.version, "ok": probe.error is None,
                "error": probe.error}

    async def probe_homepods(self) -> list[dict]:
        found = await probes.probe_homepods(self.settings.homepod_discovery)
        out = []
        for probe in found:
            pid = probes.insert_probe(self.conn, probe)
            out.append({"id": pid, "target_id": probe.target_id,
                        "version": probe.version})
        return out

    async def sources(self) -> list[dict]:
        out = []
        for src in sources.ALL_SOURCES:
            st = base.load_state(self.conn, src.name)
            out.append({
                "name": src.name,
                "products": list(src.products),
                "url": getattr(src, "url", None),
                "last_fetched_at": st.last_fetched_at,
                "last_status": st.last_status,
            })
        return out


class RemoteBackend:
    """Drive a homewatch daemon over HTTP (cht.sh-style remote use)."""

    def __init__(self, settings: Settings, base_url: str) -> None:
        self.settings = settings
        self.base_url = base_url.rstrip("/")
        self.client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "RemoteBackend":
        headers = {}
        if self.settings.token:
            headers["Authorization"] = f"Bearer {self.settings.token}"
        self.client = httpx.AsyncClient(base_url=self.base_url, headers=headers,
                                        timeout=60)
        return self

    async def __aexit__(self, *exc) -> None:
        if self.client is not None:
            await self.client.aclose()

    async def _json(self, method: str, path: str, **kw) -> dict:
        r = await self.client.request(method, path, **kw)
        r.raise_for_status()
        return r.json()

    async def refresh(self, source: str | None = None) -> dict:
        params = {"source": source} if source else {}
        return await self._json("POST", "/releases/refresh", params=params)

    async def til(self, *, kind, target, text, tags, at, probe) -> int:
        params = {"text": text, "tags": tags or "", "probe": str(probe).lower()}
        if at:
            params["at"] = at
        r = await self.client.get(f"/til/drop/{kind}/{target}", params=params)
        r.raise_for_status()
        # "OK <id>"
        return int(r.text.strip().split()[-1])

    async def timeline(self, *, since, until, products, include_betas, fmt) -> str:
        params: dict = {"format": fmt, "include_betas": str(include_betas).lower()}
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        if products:
            params["products"] = ",".join(products)
        r = await self.client.get("/timeline", params=params)
        r.raise_for_status()
        return r.text

    async def releases(self, *, product, since, until, channel) -> list[dict]:
        params = {k: v for k, v in
                  {"product": product, "since": since, "until": until,
                   "channel": channel}.items() if v}
        return (await self._json("GET", "/releases", params=params))["releases"]

    async def latest(self, product, channel) -> dict | None:
        r = await self.client.get("/releases/latest",
                                  params={"product": product, "channel": channel})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def show(self, product, version, channel) -> dict | None:
        params = {"product": product, "channel": channel}
        if version:
            params["version"] = version
        r = await self.client.get("/releases/show", params=params)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def probe_ha(self) -> dict:
        return await self._json("POST", "/probe/ha")

    async def probe_homepods(self) -> list[dict]:
        return (await self._json("POST", "/probe/homepods"))["homepods"]

    async def sources(self) -> list[dict]:
        return (await self._json("GET", "/releases/sources"))["sources"]


def get_backend(settings: Settings, remote: str | None = None) -> Backend:
    """Pick a backend: ``--remote`` > ``HOMEWATCH_URL`` > local (spec §11.1)."""
    url = remote or settings.url
    if url:
        return RemoteBackend(settings, url)
    return LocalBackend(settings)
