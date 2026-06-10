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

## Quick start

```bash
uv sync                      # create venv, install deps
cp .env.example .env         # fill in HA url/token, optional bearer
uv run homewatch serve       # http://127.0.0.1:8765
```

Pull releases and look at the timeline:

```bash
curl -X POST localhost:8765/releases/refresh
curl 'localhost:8765/timeline?since=2026-01-01'
```

Log an observation from any device on the network (no body needed):

```
http://homewatch.local:8765/til/drop/down/homepod-kitchen?text=siri+timer+dead
```

…or from the shell:

```bash
homewatch til down homepod-kitchen "siri unresponsive"
homewatch til up   homepod-kitchen
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

## Development

```bash
uv run pytest
```
