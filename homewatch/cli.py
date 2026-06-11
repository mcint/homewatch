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
from datetime import datetime, timedelta, timezone
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


# systemd/journalctl-style relative time. Case matters: M=months, m=minutes.
# Months/years are approximated (30/365 days) — fine for a list window.
_SINCE_UNITS = {
    "y": 31536000, "year": 31536000,
    "M": 2592000, "month": 2592000,
    "w": 604800, "week": 604800,
    "d": 86400, "day": 86400,
    "h": 3600, "hour": 3600, "hr": 3600,
    "m": 60, "min": 60, "minute": 60,
    "s": 1, "sec": 1, "second": 1,
}
_SINCE_TOKEN = re.compile(r"(\d+)\s*([A-Za-z]+)")


def parse_since(text: str | None) -> str | None:
    """Resolve a `--since` value to an ISO date cutoff, or None for 'all time'.

    Accepts an absolute ISO date/datetime (`2026-01-01`), `0`/`all` (= None), or
    a systemd-style relative span combining `<n><unit>` tokens, e.g.
    `2y 2M 2w 2d 2h 2m 26000s`. Unit case is significant: `M` months, `m`
    minutes.
    """
    if text is None:
        return None
    s = text.strip()
    if s in ("", "0", "all"):
        return None
    if re.match(r"^\d{4}-\d{2}", s):  # absolute ISO date/datetime — pass through
        return s
    total = 0
    matched = False
    for m in _SINCE_TOKEN.finditer(s):
        n, unit = m.group(1), m.group(2)
        key = unit if unit in _SINCE_UNITS else unit.lower()
        if key not in _SINCE_UNITS and len(key) > 1 and key.endswith("s"):
            key = key[:-1]  # plural words: weeks -> week
        if key not in _SINCE_UNITS:
            raise typer.BadParameter(f"unknown time unit {unit!r} in --since {text!r}")
        total += int(n) * _SINCE_UNITS[key]
        matched = True
    if not matched:
        raise typer.BadParameter(
            f"can't parse --since {text!r}; use e.g. 3M, 2w, 1d, an ISO date, or 0"
        )
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=total)
    return cutoff.date().isoformat()


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
    except (httpx.HTTPError, RuntimeError) as exc:
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
    since: str = typer.Option(
        "3M", help="How far back: relative span (3M, 2w, 1d, 2y …) or ISO date; "
                   "0 or --all = all time."),
    channel: str = typer.Option("stable", help="Channel filter; ignored with --all."),
    all_: bool = typer.Option(False, "--all", help="All channels + all time."),
    reverse: bool = typer.Option(False, "-r", "--reverse", help="Oldest first."),
    urls: bool = typer.Option(False, "-u", "--urls", help="Show upstream URLs."),
) -> None:
    """List releases, newest first. Defaults to the last 3 months, stable
    (widen with --since 0 / 1y, or --all)."""
    if all_:
        since_eff, channel_eff = None, None
    else:
        since_eff, channel_eff = parse_since(since), channel
    rows = _run(lambda b: b.releases(product=product, since=since_eff, until=None,
                                     channel=channel_eff))
    if reverse:  # rows arrive newest-first; flip for oldest-first
        rows = list(reversed(rows))
    for r in rows:
        when = r.get("date_display") or r.get("released_at") or "?"
        line = (f"{when:<22} {r['product']:<20} "
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
    src = row.get("date_source_url")
    if src and src != row.get("url"):  # e.g. HomePod date tracks tvOS
        typer.echo(f"date via tvOS: {src}")


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
    src = row.get("date_source_url")
    if src and src != row.get("url"):
        typer.echo(f"date via tvOS: {src}")
    typer.echo("")
    typer.echo(row.get("notes_full") or row.get("notes") or "(no notes)")


@app.command(rich_help_panel=P_QUERY)
def timeline(
    since: str = typer.Option(
        None, help="Relative span (e.g. 6M, 2w) or ISO date; default all."),
    products: str = typer.Option(None, help="Comma-separated product filter."),
    include_betas: bool = typer.Option(False),
    format: str = typer.Option("md", "--format", help="md | json | html"),
) -> None:
    """Print the merged timeline."""
    product_list = [p for p in (products or "").split(",") if p] or None
    out = _run(lambda b: b.timeline(since=parse_since(since), until=None,
                                    products=product_list,
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
def probe_homepods_cmd(
    raw: bool = typer.Option(
        False, "--raw", help="List ALL AirPlay devices seen (debug, no DB write)."),
) -> None:
    """Discover HomePods on the LAN and record a row each."""
    settings = get_settings()
    if raw:  # local debug scan — runs where the mDNS is, no backend/DB
        import asyncio

        from . import probes
        if settings.homepod_discovery == "disabled":
            typer.secho("discovery is disabled — set "
                        "HOMEWATCH_HOMEPOD_DISCOVERY=pyatv|zeroconf",
                        fg=typer.colors.YELLOW, err=True)
            raise typer.Exit(1)
        try:
            devices = asyncio.run(probes.discover_raw(settings.homepod_discovery))
        except RuntimeError as exc:
            typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        if not devices:
            typer.echo("no AirPlay devices seen — same L2 segment? mDNS reachable?")
        for d in devices:
            mark = "HOMEPOD" if d.get("is_homepod") else "       "
            typer.echo(f"{mark}  {d.get('model') or '?':<22} "
                       f"{d.get('version') or '?':<10} "
                       f"{d.get('name') or d.get('identifier')}")
        return

    found = _run(lambda b: b.probe_homepods())
    if not found:
        if settings.homepod_discovery == "disabled":
            typer.echo("discovery disabled — set HOMEWATCH_HOMEPOD_DISCOVERY="
                       "pyatv|zeroconf and `uv sync --extra probe`. "
                       "Try `probe homepods --raw` to see the LAN.")
        else:
            typer.echo("no homepods discovered — try `probe homepods --raw` "
                       "(same L2 segment as the HomePods?)")
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
    import importlib.util as _u
    extras = f"pyatv={_u.find_spec('pyatv') is not None} " \
             f"zeroconf={_u.find_spec('zeroconf') is not None}"
    typer.echo(f"homepod:     discovery={s.homepod_discovery}  {extras}")


@app.command(rich_help_panel=P_DAEMON)
def serve(reload: bool = typer.Option(False, help="Auto-reload (dev).")) -> None:
    """Run the homewatch daemon (secondary transport for other devices)."""
    import uvicorn

    settings = get_settings()
    uvicorn.run("homewatch.app:app", host=settings.bind_host,
                port=settings.bind_port, reload=reload)


if __name__ == "__main__":
    app()
