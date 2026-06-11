"""Typer CLI — local-first (spec §11.3).

By default every command talks straight to the local SQLite file and the
upstream feeds via ``LocalBackend`` (no server needed). Pass ``--remote URL``
(or set ``HOMEWATCH_URL``) to drive a running daemon instead.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import date, timedelta
from typing import Awaitable, Callable

import httpx
import typer

from . import __version__
from .client import Backend, get_backend
from .config import config_root, env_files, get_settings
from .models import PRODUCT_LABELS, PRODUCT_PAGE, PRODUCTS

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd]?)\s*$", re.IGNORECASE)
_DURATION_UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> int:
    """Parse '30s' / '5m' / '1h' / '7d' (or bare seconds) to seconds; '0' = off."""
    m = _DURATION_RE.match(text or "")
    if not m:
        raise typer.BadParameter(f"bad duration {text!r}; use e.g. 30s, 5m, 1h, 7d")
    return int(m.group(1)) * _DURATION_UNIT[m.group(2).lower()]

# Accept -h everywhere (top-level and subcommands inherit via context).
_CTX = {"help_option_names": ["-h", "--help"]}

# Help panels — group commands by verb/intent (shown in `homewatch -h`).
P_FETCH = "Fetch — pull upstream data"
P_QUERY = "Query — releases & timeline"
P_DEPLOYED = "Deployed — running versions on the LAN"
P_LOG = "Log — your observations"
P_META = "Discover & meta"
P_DAEMON = "Daemon"

app = typer.Typer(
    help="homewatch — HA × Apple/HomePod release correlator (local-first).",
    no_args_is_help=True,
    context_settings=_CTX,
)
til_app = typer.Typer(help="Append to the event log.", no_args_is_help=True,
                      context_settings=_CTX)
probe_app = typer.Typer(help="Probe HA / HomePods on the LAN.", no_args_is_help=True,
                        context_settings=_CTX)
app.add_typer(til_app, name="til", rich_help_panel=P_LOG)
app.add_typer(probe_app, name="probe", rich_help_panel=P_DEPLOYED)

_state: dict[str, str | None] = {"remote": None}


def _months_ago(n: int) -> str:
    """ISO date ~n months back (30-day months; good enough for a list window)."""
    return (date.today() - timedelta(days=30 * n)).isoformat()


def _complete_product(incomplete: str):
    """Shell-completion for product args."""
    for pid in PRODUCTS:
        if pid.startswith(incomplete):
            yield (pid, PRODUCT_LABELS.get(pid, ""))


def _validate_product(value: str | None) -> str | None:
    """Reject unknown product ids with a hint listing the valid ones."""
    if value is None or value in PRODUCTS:
        return value
    raise typer.BadParameter(
        f"unknown product {value!r}. Valid: {', '.join(PRODUCTS)} "
        "(see `homewatch products`)"
    )


# Reusable parameter specs so every product argument behaves the same.
def _product_arg():
    return typer.Argument(..., callback=_validate_product,
                          autocompletion=_complete_product,
                          help="Product id (see `homewatch products`).")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"homewatch {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    remote: str = typer.Option(
        None, "--remote", help="Drive a daemon at URL instead of the local DB."
    ),
    version: bool = typer.Option(
        None, "-V", "--version", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    _state["remote"] = remote


def _run(fn: Callable[[Backend], Awaitable]):
    """Open the selected backend, run one coroutine, surface errors as exit 1."""
    async def go():
        backend = get_backend(get_settings(), remote=_state["remote"])
        async with backend as b:
            return await fn(b)

    try:
        return asyncio.run(go())
    except httpx.HTTPError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


# --- TIL -----------------------------------------------------------------------


def _til(kind: str, target: str, text: str, tags: list[str] | None, probe: bool):
    rid = _run(lambda b: b.til(kind=kind, target=target, text=text,
                               tags=",".join(tags) if tags else None,
                               at=None, probe=probe))
    typer.echo(f"OK {rid}")


@til_app.command("down")
def til_down(target: str, text: str = typer.Argument(""),
             probe: bool = typer.Option(True, "--probe/--no-probe")) -> None:
    """Record a 'down' observation (auto-probes the target unless --no-probe)."""
    _til("down", target, text, None, probe)


@til_app.command("up")
def til_up(target: str, text: str = typer.Argument(""),
           probe: bool = typer.Option(True, "--probe/--no-probe")) -> None:
    """Record an 'up' observation."""
    _til("up", target, text, None, probe)


@til_app.command("note")
def til_note(target: str, text: str = typer.Argument(""),
             tag: list[str] = typer.Option(None, "--tag", "-t", help="Repeatable."),
             probe: bool = typer.Option(True, "--probe/--no-probe")) -> None:
    """Record a free-form note, optionally tagged."""
    _til("note", target, text, tag, probe)


# --- releases ------------------------------------------------------------------


async def _refresh_impl(b: Backend, source: str | None, probe: bool) -> dict:
    counts = await b.refresh(source)
    out: dict = {"refresh": counts}
    if probe:
        out["probe_ha"] = await b.probe_ha()
        out["probe_homepods"] = await b.probe_homepods()
    return out


@app.command(rich_help_panel=P_FETCH)
def refresh(
    source: str = typer.Option(None, help="Single source name; default all."),
    probe: bool = typer.Option(False, help="Also probe HA + HomePods (single-shot)."),
) -> None:
    """Pull releases from one or all sources (single-shot)."""
    res = _run(lambda b: _refresh_impl(b, source, probe))
    for name, c in res["refresh"].items():
        line = f"{name}: +{c['new']} new, {c['seen']} seen"
        if c["errors"]:
            line += f", errors={c['errors']}"
        typer.echo(line)
    if "probe_ha" in res:
        ha = res["probe_ha"]
        typer.echo(f"probe ha: {'ok ' + str(ha.get('version')) if ha.get('ok') else 'FAIL ' + str(ha.get('error'))}")
        typer.echo(f"probe homepods: {len(res['probe_homepods'])} found")


async def _watch_impl(b, *, interval_s, total_s, until_new, product, source, probe):
    start = time.monotonic()
    baseline = None
    if until_new and product:
        row = await b.latest(product, "stable")
        baseline = row["version"] if row else None
    n = 0
    while True:
        n += 1
        res = await _refresh_impl(b, source, probe)
        new_total = sum(c["new"] for c in res["refresh"].values())
        typer.echo(f"[{n}] +{new_total} new"
                   + (f", probed ha={res['probe_ha'].get('version')}" if probe else ""))
        if until_new:
            if product:
                row = await b.latest(product, "stable")
                cur = row["version"] if row else None
                if cur and cur != baseline:
                    typer.secho(f"new {product} release: {cur}", fg=typer.colors.GREEN)
                    return
            elif new_total > 0:
                typer.secho(f"new release(s): {new_total}", fg=typer.colors.GREEN)
                return
        if total_s and (time.monotonic() - start) >= total_s:
            typer.echo("watch window elapsed")
            return
        await asyncio.sleep(interval_s)


@app.command(rich_help_panel=P_FETCH)
def watch(
    interval: str = typer.Option("1h", help="Poll cadence, e.g. 30s 5m 1h 1d."),
    duration: str = typer.Option("0", "--for", help="Window, e.g. 7d; 0 = forever."),
    until_new: bool = typer.Option(False, "--until-new", help="Stop on a new release."),
    product: str = typer.Option(None, help="With --until-new: watch this product."),
    source: str = typer.Option(None, help="Limit refresh to one source."),
    probe: bool = typer.Option(False, help="Also probe each cycle."),
) -> None:
    """Repeat the single-shot refresh on an interval for a bounded window.

    The cron-friendly default is `homewatch refresh`; this is the foreground
    'watchman' loop for temporary active polling until an update lands.
    """
    interval_s = parse_duration(interval)
    total_s = parse_duration(duration)
    try:
        _run(lambda b: _watch_impl(b, interval_s=interval_s, total_s=total_s,
                                   until_new=until_new, product=product,
                                   source=source, probe=probe))
    except KeyboardInterrupt:
        typer.echo("\nstopped")


@app.command(rich_help_panel=P_QUERY)
def releases(
    product: str = typer.Option(None, callback=_validate_product,
                                autocompletion=_complete_product,
                                help="Product id (see `homewatch products`)."),
    months: int = typer.Option(3, help="Window in months; 0 = all time."),
    since: str = typer.Option(None, help="Explicit ISO date (overrides --months)."),
    channel: str = typer.Option("stable", help="Channel filter; ignored with --all."),
    all_: bool = typer.Option(False, "--all", help="All channels, all time."),
    urls: bool = typer.Option(False, "-u", "--urls", help="Show upstream URLs."),
) -> None:
    """List releases (newest first). Defaults to the last 3 months, stable."""
    if all_:
        since_eff, channel_eff = None, None
    elif since:
        since_eff, channel_eff = since, channel
    else:
        since_eff = _months_ago(months) if months > 0 else None
        channel_eff = channel
    rows = _run(lambda b: b.releases(product=product, since=since_eff, until=None,
                                     channel=channel_eff))
    for r in rows:
        line = (f"{r.get('released_at') or '?':<20} {r['product']:<20} "
                f"{r['version']:<12} {r.get('channel') or ''}")
        if urls and r.get("url"):
            line += f"  {r['url']}"
        typer.echo(line)


@app.command(rich_help_panel=P_QUERY)
def latest(product: str = _product_arg(),
           channel: str = typer.Option("stable")) -> None:
    """Most recent release for a product."""
    row = _run(lambda b: b.latest(product, channel))
    if row is None:
        typer.secho(f"no releases for {product}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)
    typer.echo(f"{row['product']} {row['version']} ({row.get('channel')}) "
               f"{row.get('date_display') or row.get('released_at') or ''}")
    if row.get("url"):
        typer.echo(row["url"])


@app.command(rich_help_panel=P_QUERY)
def show(product: str = _product_arg(), version: str = typer.Argument(None),
         channel: str = typer.Option("stable")) -> None:
    """Show full release notes for a release (fetched on demand)."""
    row = _run(lambda b: b.show(product, version, channel))
    if row is None:
        typer.secho(f"no release for {product} {version or ''}",
                    fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)
    typer.secho(f"{row['product']} {row['version']} ({row.get('channel')}) "
                f"{row.get('date_display') or ''}", bold=True)
    if row.get("url"):
        typer.echo(row["url"])
    typer.echo("")
    typer.echo(row.get("notes_full") or row.get("notes") or "(no notes)")


@app.command(rich_help_panel=P_QUERY)
def timeline(
    since: str = typer.Option(None),
    products: str = typer.Option(None, help="Comma-separated product filter."),
    include_betas: bool = typer.Option(False),
    format: str = typer.Option("md", "--format", help="md | json | html"),
) -> None:
    """Print the merged timeline."""
    product_list = [p for p in (products or "").split(",") if p] or None
    out = _run(lambda b: b.timeline(since=since, until=None, products=product_list,
                                    include_betas=include_betas, fmt=format))
    typer.echo(out)


# --- probes --------------------------------------------------------------------


@probe_app.command("ha")
def probe_ha_cmd() -> None:
    """Probe the configured HA instance and record a row."""
    r = _run(lambda b: b.probe_ha())
    if r.get("ok"):
        typer.echo(f"ha ok: {r.get('version')}")
    else:
        typer.secho(f"ha FAILED: {r.get('error')}", fg=typer.colors.RED)


@probe_app.command("homepods")
def probe_homepods_cmd() -> None:
    """Discover HomePods on the LAN and record a row each."""
    found = _run(lambda b: b.probe_homepods())
    if not found:
        typer.echo("no homepods discovered (check HOMEWATCH_HOMEPOD_DISCOVERY)")
    for hp in found:
        typer.echo(f"{hp['target_id']}: {hp.get('version')}")


@probe_app.command("history")
def probe_history_cmd(
    target_kind: str = typer.Option(None, help="home_assistant | homepod"),
    limit: int = typer.Option(20),
) -> None:
    """Show recorded deployed (running) versions over time."""
    rows = _run(lambda b: b.probe_history(target_kind=target_kind,
                                          target_id=None, limit=limit))
    if not rows:
        typer.echo("no probes recorded yet (run `homewatch probe ha|homepods`)")
    for r in rows:
        ver = r.get("version") or f"FAIL: {r.get('error')}"
        typer.echo(f"{r['probed_at']:<20} {r['target_kind']:<15} "
                   f"{r['target_id']:<24} {ver}")


# --- meta ----------------------------------------------------------------------


@app.command(rich_help_panel=P_META)
def sources() -> None:
    """List release sources, their update streams, and freshness."""
    for s in _run(lambda b: b.sources()):
        typer.echo(f"{s['name']:<20} {s.get('last_status') or '-':<10} "
                   f"{s.get('last_fetched_at') or 'never':<22} {s.get('url') or ''}")


@app.command(rich_help_panel=P_META)
def products() -> None:
    """List valid product ids and the latest release we have for each."""
    async def fn(b):
        return [(pid, await b.latest(pid, "stable")) for pid in PRODUCTS]

    for pid, row in _run(fn):
        latest = (f"{row['version']} {row.get('date_display', '')}".strip()
                  if row else "—")
        typer.echo(f"{pid:<22} {PRODUCT_LABELS.get(pid, ''):<32} "
                   f"{latest:<26} {PRODUCT_PAGE.get(pid, '')}")


@app.command(rich_help_panel=P_META)
def info() -> None:
    """Show effective paths and config (debug 'which DB am I using?')."""
    s = get_settings()
    typer.echo(f"homewatch {__version__}")
    typer.echo(f"db:          {s.db}  ({'exists' if s.db.exists() else 'new'})")
    typer.echo(f"home:        {s.home or '(unset — XDG default)'}")
    typer.echo(f"config dir:  {config_root()}")
    typer.echo(f"env files:   {', '.join(env_files())}")
    typer.echo(f"remote:      {_state['remote'] or s.url or '(local)'}")


@app.command(rich_help_panel=P_DAEMON)
def serve(reload: bool = typer.Option(False, help="Auto-reload (dev).")) -> None:
    """Run the homewatch daemon (secondary transport for other devices)."""
    import uvicorn

    settings = get_settings()
    uvicorn.run("homewatch.app:app", host=settings.bind_host,
                port=settings.bind_port, reload=reload)


if __name__ == "__main__":
    app()
