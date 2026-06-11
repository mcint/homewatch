"""Typer CLI — a thin client over the HTTP surface (cht.sh-style).

The TIL subcommands just hit ``/til/drop/...`` on ``HOMEWATCH_URL``, so the
same CLI works against a local or remote homewatch instance. (The spec
suggests a literal ``curl`` shellout; we use httpx — already a dependency —
for identical remote behaviour without requiring curl on the host.)
"""

from __future__ import annotations

import os

import httpx
import typer

from .config import get_settings

app = typer.Typer(help="homewatch — HA × Apple/HomePod release correlator.", no_args_is_help=True)
til_app = typer.Typer(help="Append to the event log.", no_args_is_help=True)
app.add_typer(til_app, name="til")


def _base_url() -> str:
    env = os.environ.get("HOMEWATCH_URL")
    if env:
        return env.rstrip("/")
    return f"http://{get_settings().bind}"


def _auth_headers() -> dict[str, str]:
    token = get_settings().token
    return {"Authorization": f"Bearer {token}"} if token else {}


def _drop(kind: str, target: str, text: str, tags: list[str] | None) -> None:
    params: dict[str, str] = {}
    if text:
        params["text"] = text
    if tags:
        params["tags"] = ",".join(tags)
    url = f"{_base_url()}/til/drop/{kind}/{target}"
    try:
        r = httpx.get(url, params=params, timeout=15)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.echo(r.text.strip())


@til_app.command("down")
def til_down(target: str, text: str = typer.Argument("")) -> None:
    """Record a 'down' observation, e.g. `homewatch til down homepod-kitchen "siri dead"`."""
    _drop("down", target, text, None)


@til_app.command("up")
def til_up(target: str, text: str = typer.Argument("")) -> None:
    """Record an 'up' observation."""
    _drop("up", target, text, None)


@til_app.command("note")
def til_note(
    target: str,
    text: str = typer.Argument(""),
    tag: list[str] = typer.Option(None, "--tag", "-t", help="Repeatable tag."),
) -> None:
    """Record a free-form note, optionally tagged."""
    _drop("note", target, text, tag)


@app.command()
def refresh(source: str = typer.Option(None, help="Single source name; default all.")) -> None:
    """Pull releases from one or all sources."""
    params = {"source": source} if source else {}
    try:
        r = httpx.post(f"{_base_url()}/releases/refresh", params=params,
                       headers=_auth_headers(), timeout=60)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    for name, counts in r.json().items():
        typer.echo(f"{name}: +{counts['new']} new, {counts['seen']} seen"
                   + (f", errors={counts['errors']}" if counts["errors"] else ""))


@app.command()
def timeline(
    since: str = typer.Option(None),
    products: str = typer.Option(None, help="Comma-separated product filter."),
    fmt: str = typer.Option("md", "--format", help="json | md | html."),
) -> None:
    """Print the merged timeline."""
    params = {"format": fmt}
    if since:
        params["since"] = since
    if products:
        params["products"] = products
    try:
        r = httpx.get(f"{_base_url()}/timeline", params=params,
                      headers=_auth_headers(), timeout=30)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.echo(r.text)


@app.command()
def serve(
    reload: bool = typer.Option(False, help="Auto-reload (dev)."),
) -> None:
    """Run the homewatch server (uvicorn)."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "homewatch.app:app",
        host=settings.bind_host,
        port=settings.bind_port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
