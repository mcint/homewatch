"""Timeline: merge releases × probes × TIL events into one time-ordered stream.

This is the view that answers "did HomePod 18.4 land two days before HA caught
up?". See spec §5.4. Betas/RCs are excluded by default (open question §9.3).
"""

from __future__ import annotations

import html
import json
import sqlite3

from . import til


def derive_date(conn: sqlite3.Connection, row) -> tuple[str | None, str]:
    """Best date for a release row + its precision (spec §12.3).

    Returns ``(iso, precision)`` where precision is:
    - ``exact``  — a real released_at,
    - ``tvos``   — HomePod with no date, inheriting the same-version tvOS date,
    - ``bound``  — still undated; ``discovered_at`` as an upper bound.
    """
    if row["released_at"]:
        return row["released_at"], "exact"
    if row["product"] == "homepod_software":
        # HomePod tracks tvOS versions; match exactly, or a bare major like
        # "26" against tvOS's "26.0".
        tv = conn.execute(
            "SELECT released_at FROM releases"
            " WHERE product='tvos' AND released_at IS NOT NULL"
            "   AND version IN (?, ? || '.0')"
            " ORDER BY channel='stable' DESC LIMIT 1",
            (row["version"], row["version"]),
        ).fetchone()
        if tv and tv["released_at"]:
            return tv["released_at"], "tvos"
    return row["discovered_at"], "bound"


def date_display(conn: sqlite3.Connection, row) -> str:
    """Human date string with precision marker (≈ tracks tvOS / ≤ bound)."""
    t, prec = derive_date(conn, row)
    t = t or "?"
    if prec == "tvos":
        return f"≈{t} (tracks tvOS)"
    if prec == "bound":
        return f"≤{t}"
    return t


def build(
    conn: sqlite3.Connection,
    *,
    since: str | None = None,
    until: str | None = None,
    products: list[str] | None = None,
    include_betas: bool = False,
    include_probes: bool = True,
) -> list[dict]:
    """Return the merged item stream, ascending by time.

    Releases are filtered by ``products`` (when given) and channel; TIL events
    and probes are always observations of the world and pass through.
    """
    items: list[dict] = []

    # Releases. Fall back to discovered_at for ordering when released_at is NULL.
    rel_where = []
    rel_params: list[object] = []
    if not include_betas:
        rel_where.append("channel = 'stable'")
    if products:
        placeholders = ",".join("?" * len(products))
        rel_where.append(f"product IN ({placeholders})")
        rel_params.extend(products)
    if since:
        rel_where.append("COALESCE(released_at, discovered_at) >= ?")
        rel_params.append(since)
    if until:
        rel_where.append("COALESCE(released_at, discovered_at) <= ?")
        rel_params.append(until)
    rel_clause = (" WHERE " + " AND ".join(rel_where)) if rel_where else ""
    for r in conn.execute("SELECT * FROM releases" + rel_clause, rel_params):
        t, precision = derive_date(conn, r)
        items.append(
            {
                "t": t,
                "kind": "release",
                "product": r["product"],
                "version": r["version"],
                "channel": r["channel"],
                "title": r["title"],
                "url": r["url"],
                "date_precision": precision,
            }
        )

    # TIL events (excludes soft-deleted via til.query).
    for e in til.query(conn, since=since, until=until, limit=10_000):
        items.append(
            {
                "t": e.occurred_at,
                "kind": "til",
                "kind_til": e.kind,
                "target": e.target,
                "text": e.text,
                "tags": e.tags,
            }
        )

    # Probes.
    if include_probes:
        pr_where = []
        pr_params: list[object] = []
        if since:
            pr_where.append("probed_at >= ?")
            pr_params.append(since)
        if until:
            pr_where.append("probed_at <= ?")
            pr_params.append(until)
        pr_clause = (" WHERE " + " AND ".join(pr_where)) if pr_where else ""
        for p in conn.execute("SELECT * FROM probes" + pr_clause, pr_params):
            items.append(
                {
                    "t": p["probed_at"],
                    "kind": "probe",
                    "target_kind": p["target_kind"],
                    "target_id": p["target_id"],
                    "version": p["version"],
                    "error": p["error"],
                }
            )

    items.sort(key=lambda it: (it["t"] or "", it["kind"]))
    return items


def to_json(items: list[dict]) -> str:
    return json.dumps({"items": items}, indent=2)


def _label(item: dict) -> str:
    """One-line human description of a timeline item."""
    if item["kind"] == "release":
        ch = "" if item["channel"] == "stable" else f" ({item['channel']})"
        return f"release · {item['product']} {item['version']}{ch}"
    if item["kind"] == "til":
        tgt = f" [{item['target']}]" if item.get("target") else ""
        return f"til · {item['kind_til']}{tgt} — {item.get('text', '')}"
    if item["kind"] == "probe":
        if item.get("error"):
            return f"probe · {item['target_id']} FAILED: {item['error']}"
        return f"probe · {item['target_id']} = {item.get('version')}"
    return item["kind"]


def _fmt_time(item: dict) -> str:
    """Time string with a precision marker for derived/bounded release dates."""
    t = item["t"] or "?"
    prec = item.get("date_precision")
    if prec == "tvos":
        return f"≈{t} (tracks tvOS)"
    if prec == "bound":
        return f"≤{t}"
    return t


def render_md(items: list[dict]) -> str:
    """Markdown blob suitable to paste into a wiki post (spec §5.4)."""
    lines = ["# homewatch timeline", ""]
    for it in items:
        label = _label(it)
        if it.get("url"):  # link releases for click-through inspection (§12.2)
            label = f"[{label}]({it['url']})"
        lines.append(f"- **{_fmt_time(it)}** — {label}")
    return "\n".join(lines) + "\n"


def render_html(items: list[dict]) -> str:
    """Self-contained vertical timeline page (spec §5.4)."""
    rows = []
    for it in items:
        cls = it["kind"]
        label = html.escape(_label(it))
        if it.get("url"):
            label = f'<a href="{html.escape(it["url"])}">{label}</a>'
        rows.append(
            f'<li class="ev {cls}"><time>{html.escape(_fmt_time(it))}</time>'
            f'<span class="what">{label}</span></li>'
        )
    body = "\n".join(rows) or "<li>(no events in range)</li>"
    return (
        "<!doctype html><meta charset=utf-8><title>homewatch timeline</title>"
        "<style>"
        "body{font:14px/1.5 system-ui,sans-serif;max-width:48rem;margin:2rem auto;padding:0 1rem}"
        "ul{list-style:none;padding:0;border-left:2px solid #ccc}"
        ".ev{padding:.3rem 0 .3rem 1rem;position:relative}"
        ".ev time{color:#666;font-variant-numeric:tabular-nums;margin-right:.6rem}"
        ".release .what{color:#0a6}.til .what{color:#a30}.probe .what{color:#06a}"
        "</style>"
        "<h1>homewatch timeline</h1>"
        f"<ul>{body}</ul>"
    )
