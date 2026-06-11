# STATUS — homewatch

Orientation surface. One screen: where the project is, what works, what's open.
Details live in [`specs/`](specs/), [`refs/`](refs/), [`sessions/`](sessions/).

**Version:** 0.4.6.  **Shape:** local-first CLI; daemon secondary. One SQLite
DB, seven release sources, a device registry, on-demand notes. Install/run from
GitHub (uvx/pip/uv tool — see README); PyPI-ready but not published.

## The model (deeper simplicity)

Everything is **VERB × NOUN**, under two envelopes **TRANSPORT × FORMAT**
(spec §11.4). The project's whole question lives on the noun axis:

```
release (available upstream)  ⋈  deployed (running on device)  →  timeline (the mix)
                                                     + event (human TIL)
```

## What works

| Verb \ Noun | release                          | deployed / device              | event                | timeline   |
|-------------|----------------------------------|--------------------------------|----------------------|------------|
| fetch       | `refresh` · `watch` · `fetch`    | `probe ha/homepods` (auto-enroll) | —                 | —          |
| query       | `releases` · `latest` · `show`   | `devices list/show/history` · `status` · `probe history` | `til` (read) | `timeline` |
| log         | —                                | `enroll` · `devices retire`    | `til down/up/note`   | —          |
| admin       | `sources` · `products` · `info` · `serve`                                                     |

**status** = `query × (release ⋈ deployed)` — per device, running vs latest.

- 7 sources: HA core/blog/OS atoms, Apple security + HomePod-notes scrapers,
  Apple developer RSS, **endoflife.date** (Apple-OS dates).
- Dates: exact → **HomePod≈tvOS** derived → `≤bound`. (refs/products-and-streams)
- Devices: probes **auto-enroll** (MAC/mDNS identifiers, SSID/IP/subnet, name),
  write `enrolled`/`retired` events to the timeline; `devices rename` sets a
  display name (kept separate from detected); `devices list PATTERN` is a
  smartcase/regex search across all fields; `status` flags "behind".
- pyatv detection uses AirPlay TXT + name (not just model) so HomePods aren't
  missed; `probe homepods --raw [--host IP]` to debug / unicast.
- `admin migrate status|backup`, `admin info`. Data root: XDG / `HOMEWATCH_HOME`
  / `HOMEWATCH_DB`; config `env` (XDG) / `.env` (cwd). `homewatch info` shows it.
- 123 tests green.

## Open cells / next

- **ESPHome / self-reporting ingest** — a `POST /devices/report` (+ CLI) for
  devices that report their own version; model already supports `kind=esphome
  |custom`. Highest-value next.
- **notify on `watch --until-new`** — currently prints + exits.
- **`show` via GitHub API** — cleaner release-note bodies than page scraping.
- **`latest` tie-break** — same-day Apple cross-cycle patches order by id.
- **LAN probes live** — HA probe unit-covered only; HomePod verified live.
- **graceful migrate** — `--backup` exists; down/rollback only if needed (yoyo).

## Releases

`v0.1.0` daemon-first v1 · `v0.2.x` CLI-first + endoflife + grammar ·
`v0.3.x` XDG/data-root, relative `--since`, time-flows-down, URL fixes ·
`v0.4.x` device inventory, `status`, display names + search, pyatv fix,
admin/migrate, install-from-GitHub.
