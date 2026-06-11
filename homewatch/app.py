"""FastAPI app: route wiring for releases, probes, TIL, and timeline.

One process, one SQLite DB. Optional bearer auth via HOMEWATCH_TOKEN gates
every route except the URL drop-in (spec §5). See the spec for the full surface.
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
)
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from dataclasses import asdict

from . import devices as devices_mod
from . import netinfo, notes
from . import probes as probes_mod
from . import sources, til, timeline
from .config import Settings, get_settings
from .db import checkpoint, connect, get_db
from .models import Probe
from .sources import base

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Bootstrap schema once at startup; per-request connections skip migration.
    get_db(settings.db).close()
    app.state.client = httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent},
        follow_redirects=True,
        timeout=30,
    )
    try:
        yield
    finally:
        await app.state.client.aclose()
        # Checkpoint the WAL on shutdown so the .sqlite is self-contained (§10).
        conn = connect(settings.db)
        checkpoint(conn)
        conn.close()


app = FastAPI(title="homewatch", version="0.1.0", lifespan=lifespan)


# --- dependencies --------------------------------------------------------------


def get_conn():
    settings = get_settings()
    conn = connect(settings.db)
    try:
        yield conn
    finally:
        conn.close()


def get_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.client


def require_auth(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Bearer-token gate. No-op when HOMEWATCH_TOKEN is unset (spec §5)."""
    if not settings.token:
        return
    expected = f"Bearer {settings.token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
Client = Annotated[httpx.AsyncClient, Depends(get_client)]
Cfg = Annotated[Settings, Depends(get_settings)]

# Authed routers; the drop-in router below is intentionally unauthenticated.
auth = [Depends(require_auth)]


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


# --- releases ------------------------------------------------------------------

releases_router = APIRouter(prefix="/releases", tags=["releases"], dependencies=auth)


def _release_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


@releases_router.get("")
def list_releases(
    conn: Conn,
    product: str | None = None,
    since: str | None = None,
    until: str | None = None,
    channel: str | None = None,
) -> dict:
    rows = base.list_releases(
        conn, product=product, since=since, until=until, channel=channel
    )
    out = []
    for r in rows:
        d = _release_dict(r)
        d["date_display"] = timeline.date_display(conn, r)
        out.append(d)
    return {"releases": out}


@releases_router.get("/latest")
def latest_release(conn: Conn, product: str, channel: str = "stable") -> dict:
    row = base.latest_release(conn, product, channel)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no releases for {product}")
    out = _release_dict(row)
    out["date_display"] = timeline.date_display(conn, row)
    out["date_source_url"] = timeline.date_source(conn, row)
    return out


@releases_router.get("/show")
async def show_release(
    conn: Conn,
    client: Client,
    product: str,
    version: str | None = None,
    channel: str = "stable",
) -> dict:
    if version:
        row = conn.execute(
            "SELECT * FROM releases WHERE product=? AND version=? AND channel=?",
            (product, version, channel),
        ).fetchone()
    else:
        row = base.latest_release(conn, product, channel)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no release for {product}")
    out = _release_dict(row)
    out["date_display"] = timeline.date_display(conn, row)
    out["date_source_url"] = timeline.date_source(conn, row)
    out["notes_full"] = await notes.fetch_full_notes(client, row)
    return out


@releases_router.post("/refresh")
async def refresh_releases(conn: Conn, client: Client, source: str | None = None) -> dict:
    try:
        return await sources.refresh(conn, client, source=source)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown source {source!r}")


@releases_router.get("/sources")
def list_sources(conn: Conn) -> dict:
    out = []
    for src in sources.ALL_SOURCES:
        st = base.load_state(conn, src.name)
        out.append(
            {
                "name": src.name,
                "products": list(src.products),
                "last_fetched_at": st.last_fetched_at,
                "last_status": st.last_status,
            }
        )
    return {"sources": out}


# --- probes --------------------------------------------------------------------

probe_router = APIRouter(prefix="/probe", tags=["probe"], dependencies=auth)


@probe_router.post("/ha")
async def probe_ha(conn: Conn, client: Client, settings: Cfg) -> dict:
    probe = await probes_mod.probe_ha(settings.ha_url, settings.ha_token, client=client)
    pid = probes_mod.observe(conn, probe, **netinfo.context())
    return {"id": pid, "version": probe.version, "ok": probe.error is None,
            "error": probe.error}


@probe_router.post("/homepods")
async def probe_homepods(conn: Conn, settings: Cfg) -> dict:
    found = await probes_mod.probe_homepods(settings.homepod_discovery)
    ctx = netinfo.context()
    out = []
    for probe in found:
        pid = probes_mod.observe(conn, probe, **ctx)
        out.append({"id": pid, "target_id": probe.target_id, "version": probe.version})
    return {"discovery": settings.homepod_discovery, "homepods": out}


@probe_router.post("/ingest")
def probe_ingest(conn: Conn, probe: dict) -> dict:
    """Accept a probe row from a LAN agent (spec §7-2)."""
    p = Probe(
        target_kind=probe["target_kind"],
        target_id=probe["target_id"],
        version=probe.get("version"),
        extra=probe.get("extra"),
        error=probe.get("error"),
        probed_at=probe.get("probed_at"),
    )
    return {"id": probes_mod.insert_probe(conn, p)}


@probe_router.get("/history")
def probe_history(
    conn: Conn,
    target_kind: str | None = None,
    target_id: str | None = None,
    limit: int = 50,
) -> dict:
    rows = probes_mod.history(
        conn, target_kind=target_kind, target_id=target_id, limit=limit
    )
    return {"probes": [{k: r[k] for k in r.keys()} for r in rows]}


# --- TIL -----------------------------------------------------------------------

til_router = APIRouter(prefix="/til", tags=["til"], dependencies=auth)


def _render_til(request: Request, conn: sqlite3.Connection, fmt: str,
                since: str | None, until: str | None, kind: str | None,
                target: str | None) -> Response:
    events = til.query(conn, since=since, until=until, kind=kind, target=target)
    if fmt == "json":
        return JSONResponse({"events": [
            {"id": e.id, "occurred_at": e.occurred_at, "kind": e.kind,
             "target": e.target, "text": e.text, "tags": e.tags, "source": e.source}
            for e in events
        ]})
    if fmt == "tsv":
        return PlainTextResponse(til.render_tsv(events))
    return TEMPLATES.TemplateResponse(request, "til.html", {"events": events})


@til_router.get("", response_class=HTMLResponse)
def til_index(
    request: Request,
    conn: Conn,
    since: str | None = None,
    until: str | None = None,
    kind: str | None = None,
    target: str | None = None,
    format: str = "html",
) -> Response:
    return _render_til(request, conn, format, since, until, kind, target)


@til_router.post("")
def til_create(
    request: Request,
    conn: Conn,
    kind: Annotated[str, Form()] = "note",
    target: Annotated[str, Form()] = "",
    text: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
    at: Annotated[str, Form()] = "",
) -> Response:
    til.record(conn, kind=kind, target=target or None, text=text,
               tags=tags, at=at or None, source="web")
    # Re-render the log so the browser shows the result (spec §5.3-A).
    return _render_til(request, conn, "html", None, None, None, None)


@til_router.delete("/{event_id}")
def til_delete(conn: Conn, event_id: int) -> dict:
    ok = til.soft_delete(conn, event_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"no event {event_id}")
    return {"deleted": event_id}


# --- TIL URL drop-in (NO auth — the whole point is zero-friction logging) ------

drop_router = APIRouter(prefix="/til/drop", tags=["til"])


@drop_router.get("/{kind}/{target}")
async def til_drop(
    request: Request,
    conn: Conn,
    client: Client,
    settings: Cfg,
    kind: str,
    target: str,
    text: str = "",
    tags: str = "",
    at: str = "",
    probe: bool = True,
) -> Response:
    try:
        rid = til.record(conn, kind=kind, target=target, text=text, tags=tags,
                         at=at or None, source="url")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if probe:
        await probes_mod.autoprobe(conn, client, target, settings)

    # Browser → show the log; curl/plain → "OK <id>" so it composes with shells.
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return RedirectResponse(url="/til", status_code=303)
    return PlainTextResponse(f"OK {rid}\n")


# --- timeline ------------------------------------------------------------------

timeline_router = APIRouter(tags=["timeline"], dependencies=auth)


@timeline_router.get("/timeline")
def get_timeline(
    conn: Conn,
    since: str | None = None,
    until: str | None = None,
    products: str | None = None,
    include_betas: bool = False,
    format: str = "json",
) -> Response:
    product_list = [p for p in (products or "").split(",") if p] or None
    items = timeline.build(
        conn, since=since, until=until, products=product_list,
        include_betas=include_betas,
    )
    if format == "html":
        return HTMLResponse(timeline.render_html(items))
    if format == "md":
        return PlainTextResponse(timeline.render_md(items))
    return JSONResponse({"items": items})


# --- devices -------------------------------------------------------------------

devices_router = APIRouter(prefix="/devices", tags=["devices"], dependencies=auth)


@devices_router.get("")
def list_devices_route(conn: Conn, kind: str | None = None,
                       include_retired: bool = False) -> dict:
    return {"devices": [asdict(d) for d in devices_mod.list_devices(
        conn, kind=kind, include_retired=include_retired)]}


@devices_router.post("/enroll")
def enroll_device(conn: Conn, body: dict) -> dict:
    dev, _ = devices_mod.enroll(
        conn, body["device_id"], body["kind"],
        product=body.get("product"), name=body.get("name"),
    )
    return asdict(dev)


# device_id is a query param (it can contain '/' and ':' for HA URLs).
@devices_router.get("/get")
def get_device(conn: Conn, device_id: str) -> dict:
    dev = devices_mod.get(conn, device_id)
    if dev is None:
        raise HTTPException(status_code=404, detail=f"no device {device_id}")
    return asdict(dev)


@devices_router.post("/retire")
def retire_device(conn: Conn, device_id: str) -> dict:
    return {"retired": devices_mod.retire(conn, device_id)}


@devices_router.post("/rename")
def rename_device(conn: Conn, device_id: str, display_name: str = "") -> dict:
    return {"renamed": devices_mod.rename(conn, device_id, display_name or None)}


for r in (releases_router, probe_router, til_router, drop_router, timeline_router,
          devices_router):
    app.include_router(r)
