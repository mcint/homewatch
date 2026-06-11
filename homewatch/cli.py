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
from typing import Awaitable, Callable

import httpx
import typer

from . import __version__
from .client import Backend, get_backend
from .config import config_root, env_files, get_settings

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

app = typer.Typer(
    help="homewatch — HA × Apple/HomePod release correlator (local-first).",
    no_args_is_help=True,
    context_settings=_CTX,
)
til_app = typer.Typer(help="Append to the event log.", no_args_is_help=True,
                      context_settings=_CTX)
probe_app = typer.Typer(help="Probe HA / HomePods on the LAN.", no_args_is_help=True,
                        context_settings=_CTX)
app.add_typer(til_app, name="til")
app.add_typer(probe_app, name="probe")

_state: dict[str, str | None] = {"remote": None}


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


@app.command()
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


@app.command()
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


@app.command()
def releases(
    product: str = typer.Option(None),
    since: str = typer.Option(None),
    channel: str = typer.Option(None, help="stable | beta | rc"),
) -> None:
    """List releases (newest first)."""
    rows = _run(lambda b: b.releases(product=product, since=since, until=None,
                                     channel=channel))
    for r in rows:
        typer.echo(f"{r.get('released_at') or '?':<20} {r['product']:<20} "
                   f"{r['version']:<12} {r.get('channel') or ''}")


@app.command()
def latest(product: str, channel: str = typer.Option("stable")) -> None:
    """Most recent release for a product."""
    row = _run(lambda b: b.latest(product, channel))
    if row is None:
        typer.secho(f"no releases for {product}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)
    typer.echo(f"{row['product']} {row['version']} ({row.get('channel')}) "
               f"{row.get('released_at') or ''}")
    if row.get("url"):
        typer.echo(row["url"])


@app.command()
def show(product: str, version: str = typer.Argument(None),
         channel: str = typer.Option("stable")) -> None:
    """Show full release notes for a release (fetched on demand)."""
    row = _run(lambda b: b.show(product, version, channel))
    if row is None:
        typer.secho(f"no release for {product} {version or ''}",
                    fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)
    typer.secho(f"{row['product']} {row['version']} ({row.get('channel')})",
                bold=True)
    if row.get("url"):
        typer.echo(row["url"])
    typer.echo("")
    typer.echo(row.get("notes_full") or row.get("notes") or "(no notes)")


@app.command()
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


# --- meta ----------------------------------------------------------------------


@app.command()
def sources() -> None:
    """List release sources, their update streams, and freshness."""
    for s in _run(lambda b: b.sources()):
        typer.echo(f"{s['name']:<20} {s.get('last_status') or '-':<10} "
                   f"{s.get('last_fetched_at') or 'never':<22} {s.get('url') or ''}")


@app.command()
def info() -> None:
    """Show effective paths and config (debug 'which DB am I using?')."""
    s = get_settings()
    typer.echo(f"homewatch {__version__}")
    typer.echo(f"db:          {s.db}  ({'exists' if s.db.exists() else 'new'})")
    typer.echo(f"home:        {s.home or '(unset — XDG default)'}")
    typer.echo(f"config dir:  {config_root()}")
    typer.echo(f"env files:   {', '.join(env_files())}")
    typer.echo(f"remote:      {_state['remote'] or s.url or '(local)'}")


@app.command()
def serve(reload: bool = typer.Option(False, help="Auto-reload (dev).")) -> None:
    """Run the homewatch daemon (secondary transport for other devices)."""
    import uvicorn

    settings = get_settings()
    uvicorn.run("homewatch.app:app", host=settings.bind_host,
                port=settings.bind_port, reload=reload)


if __name__ == "__main__":
    app()
