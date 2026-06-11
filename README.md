# homewatch

A small self-hosted service for correlating Home Assistant outages with
Apple / HomePod / HA software releases. It answers one question: *did a
HomePod / iOS update land just before Home Assistant's HomeKit/Matter
integrations broke (or vice versa)?*

It does this by pulling release metadata (RSS/Atom feeds + scraped Apple
support pages + on-LAN version probes) and lining it up on a single
timeline against your own append-only log of "noticed it down at 19:42".

See [`docs/specs/homewatch-spec.md`](docs/specs/homewatch-spec.md) for the
full design.

## Quick start (local-first — no server needed)

```bash
uv sync                      # create venv, install deps
cp .env.example .env         # fill in HA url/token, optional bearer
uv run homewatch refresh     # pull all release streams into the local DB
uv run homewatch timeline    # see releases × probes × TIL, interleaved
```

The CLI talks straight to the local SQLite file and the upstream feeds. Log an
observation, pull a single source, read full notes on demand:

```bash
homewatch til down homepod-kitchen "siri unresponsive"
homewatch til up   homepod-kitchen
homewatch latest homepod_software
homewatch show    home_assistant_core          # full release notes, fetched live
homewatch sources                              # streams + freshness
```

### Cadence

`homewatch refresh` is a single-shot updater — schedule it however you like:

```cron
0 * * * *  homewatch refresh            # hourly via cron/launchd/systemd
```

Or run the foreground "watchman" loop until an update lands:

```bash
homewatch watch --interval 1h --until-new --product homepod_software
homewatch watch --interval 30m --for 7d --probe          # poll a window
```

### Daemon (secondary — access from other devices)

Run a server so phones/other hosts can hit the URL drop-in and timeline:

```bash
uv run homewatch serve       # http://127.0.0.1:8765
```

```
http://homewatch.local:8765/til/drop/down/homepod-kitchen?text=siri+timer+dead
```

Point the CLI at a remote daemon with `--remote URL` (or `HOMEWATCH_URL`):

```bash
homewatch --remote https://homewatch.example til down ha "core upgrade broke matter"
```

## Surfaces

| Half       | What                                                              |
|------------|------------------------------------------------------------------|
| `releases` | `POST /releases/refresh`, `GET /releases`, `GET /releases/latest` |
| `probes`   | `POST /probe/ha`, `POST /probe/homepods`, `GET /probe/history`    |
| `til`      | web form `GET /til`, URL drop-in `GET /til/drop/{kind}/{target}`  |
| `timeline` | `GET /timeline` — releases × probes × TIL, interleaved by time   |

## HomePod probes

HomePod versions are only discoverable over mDNS (link-local), so the
HomePod probe must run on the same L2 network as the HomePods. Install the
probe extra there:

```bash
uv sync --extra probe    # pyatv
# or the lighter raw-mDNS path:
uv sync --extra zeroconf
```

On a VPS, run the release/TIL halves remotely and a thin probe agent on the
LAN that posts results up (see spec §7).

## Docs

- [`docs/STATUS.md`](docs/STATUS.md) — orientation: where it is, what works, what's open.
- [`docs/specs/`](docs/specs/) — design intent (the spec); §11.4 is the CLI
  verb×noun grammar.
- [`docs/refs/`](docs/refs/) — durable reference: [product vocabulary, update
  streams, tvOS↔HomePod, date precision](docs/refs/products-and-streams.md).
- [`docs/sessions/`](docs/sessions/) — per-session reports + the
  [REQUESTS log](docs/sessions/REQUESTS.md).

## Development

```bash
uv run pytest
```
