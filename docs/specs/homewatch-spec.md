# homewatch — spec

A small self-hosted service for correlating Home Assistant outages with
Apple / HomePod / HA software releases. Two halves:

1. **`releases`** — pull release metadata (RSS/Atom + scraped HTML + LAN probes)
   for HA, HomePod, and iOS/macOS, on demand.
2. **`til`** — append-only event log ("noticed it down at 19:42", "back up 20:11")
   with web form, GET-URL drop-in, and CLI surface, persisted to SQLite alongside
   the release data.

Goal: confirm or refute the hypothesis that HomePod ↔ HA breakage correlates
with Apple Home / HomePod software updates landing before HA's matter / homekit
integrations catch up (or vice versa).

Target: **Python primary**, separate VPS, single SQLite DB.

---

## 0. Non-goals

- Not a monitor / uptime checker. The user observes outages; this just records
  the observation and lines it up against release timelines.
- Not a notification system (initially). On-demand pulls only — `urlwatch`-style
  scheduled diffing is a v2 stretch.
- No auth model beyond a shared bearer token. Single-user.

---

## 1. Architecture

```
                    ┌────────────────────────┐
   curl/browser ───▶│  FastAPI app (uvicorn) │──▶  SQLite (WAL)
                    │  - /til  (POST + GET)  │
                    │  - /releases/*         │
                    │  - /probe/*            │
                    │  - /timeline           │
                    └─────────┬──────────────┘
                              │
                  ┌───────────┼─────────────┐
                  ▼           ▼             ▼
              feed pull   HTML scrape   LAN probe
              (feedparser) (httpx+sel.) (pyatv, HA REST)
```

SQLite with WAL. No queue, no scheduler.

**CLI-first (v1.1).** The primary way to use homewatch is the local CLI talking
*directly* to the SQLite file and the upstream feeds — no server required. The
FastAPI daemon is a *secondary transport* for reaching the same data from other
devices. Both go through one core service layer (`sources.refresh`,
`til.record`, `timeline.build`, `probes.*`); the CLI selects a **backend**
(`client.py`): `LocalBackend` (direct DB + fetch, the default) or
`RemoteBackend` (HTTP to a running daemon, when `HOMEWATCH_URL`/`--remote` is
set). See §11 for the operation modes and §12 for on-demand notes.

**Layout:**

```
homewatch/
├── pyproject.toml
├── homewatch/
│   ├── __init__.py
│   ├── app.py              # FastAPI app, route wiring (secondary transport)
│   ├── client.py           # LocalBackend / RemoteBackend — CLI transport
│   ├── db.py               # sqlite connection + migrations
│   ├── config.py           # pydantic-settings, .env
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── base.py         # Source protocol
│   │   ├── ha_release.py   # github releases atom + blog atom
│   │   ├── apple_security.py
│   │   ├── apple_developer.py
│   │   ├── homepod_notes.py    # support.apple.com/en-us/108045 scraper
│   │   └── ha_blog.py
│   ├── probes/
│   │   ├── ha.py           # HA REST API
│   │   └── homepod.py      # pyatv / mDNS
│   ├── til.py              # event log model + parsing
│   ├── timeline.py         # join releases × til events
│   └── cli.py              # typer; thin curl wrapper for `htsh`-style use
├── data/
│   └── homewatch.sqlite
├── tests/
└── README.md
```

---

## 2. Data model (SQLite)

WAL mode. One DB file. Migrations as numbered .sql files; bootstrap on first
run if `schema_version` table is missing.

```sql
-- Every release we know about, from any source.
CREATE TABLE releases (
    id              INTEGER PRIMARY KEY,
    product         TEXT NOT NULL,        -- 'home_assistant_core', 'homepod_software',
                                          -- 'ios', 'ipados', 'macos', 'home_assistant_os'
    version         TEXT NOT NULL,        -- '2026.4.3', '18.4', '15.4.1'
    channel         TEXT,                 -- 'stable' | 'beta' | 'rc' | NULL
    released_at     TEXT,                 -- ISO 8601, may be NULL if unknown
    title           TEXT,
    url             TEXT,
    source          TEXT NOT NULL,        -- which sources/*.py produced this
    raw_id          TEXT,                 -- feed entry guid / url / hash
    notes           TEXT,                 -- markdown; release notes excerpt
    discovered_at   TEXT NOT NULL,        -- when *we* first saw it
    UNIQUE(product, version, channel)
);

CREATE INDEX idx_releases_product_date ON releases(product, released_at DESC);

-- Snapshots from on-network probes: "what version was this device running on date X"
CREATE TABLE probes (
    id              INTEGER PRIMARY KEY,
    probed_at       TEXT NOT NULL,
    target_kind     TEXT NOT NULL,        -- 'home_assistant' | 'homepod'
    target_id       TEXT NOT NULL,        -- HA URL, or HomePod identifier (mDNS name / mac)
    version         TEXT,                 -- NULL if probe failed
    extra_json      TEXT,                 -- TXT record dump, /api/config dump, etc.
    error           TEXT                  -- if probe failed: short reason
);

CREATE INDEX idx_probes_target_time ON probes(target_kind, target_id, probed_at DESC);

-- TIL / event log: human-entered observations.
CREATE TABLE til_events (
    id              INTEGER PRIMARY KEY,
    occurred_at     TEXT NOT NULL,        -- when the event happened (user-supplied or now)
    recorded_at     TEXT NOT NULL,        -- when we wrote the row
    kind            TEXT NOT NULL,        -- 'down' | 'up' | 'note' | 'observation'
    target          TEXT,                 -- e.g. 'homepod-living-room', 'ha', 'ha+homepod'
    text            TEXT NOT NULL,        -- the body
    tags            TEXT,                 -- JSON array of strings
    source          TEXT                  -- 'web' | 'url' | 'cli' | 'api'
);

CREATE INDEX idx_til_occurred ON til_events(occurred_at DESC);

-- Last-fetched timestamp per source so we can avoid hammering and report freshness.
CREATE TABLE source_state (
    source          TEXT PRIMARY KEY,
    last_fetched_at TEXT,
    last_status     TEXT,                 -- 'ok' | 'error: ...'
    etag            TEXT,
    last_modified   TEXT
);
```

---

## 3. Release sources

Every source implements:

```python
class Source(Protocol):
    name: str            # stable identifier, used in releases.source
    products: list[str]  # which `product` values it can emit
    async def fetch(self, state: SourceState) -> list[Release]: ...
```

`fetch()` is allowed to use `state.etag` / `state.last_modified` to skip work.

### 3.1 Home Assistant Core — GitHub releases atom

- **URL:** `https://github.com/home-assistant/core/releases.atom`
- **Why:** Authoritative, machine-readable, includes RC and beta tags, has
  exact UTC timestamps. Adding `.atom` to any GitHub releases URL gives you
  the feed.
- **Parse:** `feedparser`. Map `entry.title` → version (strip leading `v`,
  detect `b1`/`rc1` for channel). `entry.published_parsed` → `released_at`.
- **product:** `home_assistant_core`

### 3.2 Home Assistant blog — release announcements

- **URL:** `https://www.home-assistant.io/atom.xml`
- **Why:** Curated release-day posts (e.g. "2026.4: …") with breaking changes
  and integration shake-ups — better signal for *what changed in HomeKit /
  Matter integrations* than the raw GH tag.
- **Parse:** `feedparser`. Filter entries whose title matches `^\d{4}\.\d+(\.\d+)?:`.
- **product:** `home_assistant_core` (channel='stable', notes=summary)

### 3.3 Home Assistant OS — GitHub releases atom

- **URL:** `https://github.com/home-assistant/operating-system/releases.atom`
- **product:** `home_assistant_os`

### 3.4 Apple security releases

- **URL:** `https://support.apple.com/en-us/100100`
- **No RSS.** This is the canonical Apple page listing every security release
  across all OSes (iOS, macOS, watchOS, tvOS, visionOS, HomePod Software,
  Safari). Plain HTML table.
- **Parse:** `httpx` + `selectolax` (or `BeautifulSoup`). The page is a single
  big `<table>` with columns: Name, Available for, Release date.
- **dedupe:** `(product, version)`; raw_id = sha1(name+date).
- **products produced:** `ios`, `ipados`, `macos`, `tvos`, `watchos`,
  `visionos`, `homepod_software`, `safari`.
- **Note:** This page is updated *before* the per-release `support.apple.com/HT…`
  pages get linked, so check it first.

### 3.5 Apple developer release notes — RSS

- **URL:** `https://developer.apple.com/news/releases/rss/releases.rss`
- **Why:** Earlier signal than the security page — covers betas and final
  builds, with build numbers (`23E224`) which is what HomePods actually report
  on the LAN. (This feed has been removed and reinstated before — code defensively
  for 404, don't crash.)
- **Parse:** `feedparser`. Extract `(product family, version, build)` from the
  title with a regex; titles look like `iOS 18.4 (22E240)`.

### 3.6 HomePod release-notes page — scrape

- **URL:** `https://support.apple.com/en-us/108045` ("About Software Updates
  for HomePod")
- **Why:** This is the *only* page that publishes HomePod release notes in
  human-readable form — version number, date, and what changed. There is no
  RSS for it. Apple sometimes updates HomePod firmware silently (with the
  same major version), and this is where it's documented.
- **Parse:** Section headers are version numbers (`Software version 26`,
  `Software version 18.4`, etc.). For each `<h2>`/`<h3>`-ish header, capture
  the version, the release date if present, and the paragraph(s) underneath
  as `notes`.
- **Caveat:** The page's HTML structure has changed before. Make the parser
  tolerant: if the structure changes, log a warning and store the whole page
  hash so we can detect "the page changed but we couldn't parse it" — that's
  itself a useful signal.
- **product:** `homepod_software`

### 3.7 (v2) `urlwatch`-style page diffing

For pages without feeds (mainly the HomePod notes page and any Apple Home
app-version page that surfaces in the future), keep a `page_snapshots` table
with `(url, fetched_at, sha256, body_gz)`. `/releases/refresh?diff=true`
returns a unified diff of the body when the hash changes. This is just
"urlwatch but inline in our DB" — don't run actual urlwatch as a sidecar.

---

## 4. LAN probes

### 4.1 Home Assistant

- **Endpoint:** `GET {HA_URL}/api/config`
- **Auth:** `Authorization: Bearer {LLAT}` — long-lived access token from
  `/profile` in the HA UI.
- **Returns:** JSON with `version` (e.g. `"2026.4.3"`), `installation_type`
  (e.g. `"Home Assistant OS"`), `components` list, `config_dir`.
- **Store:** `version` + dump the whole response into `probes.extra_json`
  (it's small). `installation_type` lets us correlate with `home_assistant_os`
  releases vs `home_assistant_core`.
- **Failure modes:** 401 (token expired/wrong), connection refused (HA is
  actually down — *also a useful signal, store as a probe with `error` set*),
  TLS issues if behind reverse proxy.

```python
async def probe_ha(url: str, token: str) -> Probe:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{url.rstrip('/')}/api/config", headers=headers)
        r.raise_for_status()
        data = r.json()
    return Probe(target_kind="home_assistant", target_id=url,
                 version=data["version"], extra_json=data)
```

### 4.2 HomePod

There is **no public HTTP API on HomePods for version info**, but the version
is broadcast over mDNS in the `_airplay._tcp.local` and `_raop._tcp.local`
TXT records. Fields of interest: `osvers` (HomePod OS version, e.g. `18.4`),
`srcvers` (AirTunes/AirPlay implementation version), `model` (e.g.
`AudioAccessory5,1` for HomePod mini), `deviceid` (MAC).

Two implementation options, in order of preference:

**Option A — pyatv (recommended).**
```python
import pyatv
atvs = await pyatv.scan(loop, timeout=5)
for atv in atvs:
    if atv.device_info.model.name.startswith("HomePod"):
        # atv.device_info.version → "18.4"
        # atv.device_info.build_number → "22E240" (sometimes)
        # atv.identifier → stable id (MAC-based)
        ...
```
pyatv handles the mDNS discovery and the model→family mapping. It targets
Apple TV but explicitly supports HomePods for discovery and metadata.

**Option B — raw zeroconf.** `python-zeroconf` browse `_airplay._tcp.local.`
and read the TXT record directly. Useful if pyatv pulls in too much
(miniaudio, protobuf) and we only want a version probe. ~50 lines.

**Constraint:** mDNS is link-local. The probe service must be on the same L2
network as the HomePods, OR there must be an mDNS reflector/repeater across
the subnets. If running on a VPS, this won't work directly — see §7.

**Store:** one probe row per HomePod per probe call, keyed by
`identifier` (MAC). `extra_json` contains the full TXT dict.

---

## 5. HTTP API

All endpoints are JSON unless noted. Optional bearer auth via `HOMEWATCH_TOKEN`
env var; if set, all routes except `GET /til/drop/...` require it.

### 5.1 Releases

```
GET  /releases?product=&since=&until=&channel=stable
       List releases. Filters all optional. Default: last 90 days.
GET  /releases/latest?product=home_assistant_core
       Single most recent release.
POST /releases/refresh?source=*
       Synchronously fetch from one or all sources. Returns counts:
       {"home_assistant_core": {"new": 2, "seen": 0, "errors": []}, ...}
       This is the on-demand pull the user wants.
GET  /releases/sources
       Status: each source + last_fetched_at + last_status.
```

### 5.2 Probes

```
POST /probe/ha
       Probe configured HA instance, persist a row, return {version, ok}.
POST /probe/homepods
       Discover HomePods on LAN, persist one row each, return list.
GET  /probe/history?target_kind=&target_id=&limit=50
       Recent probe rows. Useful for "what was HA running last Tuesday".
```

### 5.3 TIL / event log

The TIL surface has three input modes — all writing to the same `til_events`
table.

**A. Web form (`GET /til`).** Plain HTML, single textarea + radio buttons for
kind, optional time (defaults to now), tag input. POSTs to `/til`. No JS
frameworks; vanilla form submit, server renders the resulting list.

**B. URL drop-in (`GET /til/drop/...`).** The `cht.sh`-style entry point.
Designed to be hit from a browser bar or `curl` with no body:

```
GET /til/drop/down/homepod-kitchen?text=cant+ask+siri+timer
GET /til/drop/up/homepod-kitchen
GET /til/drop/note/ha?text=restarted+after+core+upgrade&tags=upgrade,maybe-fixed
```

Path is `/til/drop/{kind}/{target}`. Body or query params:
- `text=` — free text (URL-encoded). If absent, kind alone is the event.
- `tags=` — comma-separated.
- `at=` — ISO timestamp override; defaults to now.

Returns `text/plain` with `OK 4231` (the row id) so it composes with shell.
If the request `Accept`s `text/html`, redirect to `/til` so a browser shows
the log.

This is the moral equivalent of `echo "homepod down" >> log` but reachable
from any device on the network without ssh.

**C. CLI (`homewatch til ...`).** Typer-based, but thin: the default
implementation is literally a `curl` shellout to `/til/drop/...`, so the CLI
also works against a remote homewatch instance with just `HOMEWATCH_URL`
set. cht.sh-style.

```bash
homewatch til down homepod-kitchen "siri unresponsive"
homewatch til up homepod-kitchen
homewatch til note ha "rebooted after 2026.4 upgrade" --tag upgrade
```

**Read endpoints:**

```
GET  /til?since=&until=&kind=&target=&format=json|html|tsv
       Default: html, last 7 days, reverse-chron.
       tsv format is what you grep/awk against.
POST /til
       Form post from web UI. Same fields.
DELETE /til/{id}
       For typos. Soft-delete (set kind='deleted') rather than hard-delete,
       since this is supposed to be append-mostly.
```

### 5.4 Timeline (the whole point)

```
GET /timeline?since=2026-01-01&products=homepod_software,home_assistant_core
```

Returns a merged stream of releases + probes + TIL events, ordered by time:

```json
{
  "items": [
    {"t": "2026-04-15T17:02:00Z", "kind": "release",
     "product": "homepod_software", "version": "18.4", "url": "..."},
    {"t": "2026-04-15T19:42:00Z", "kind": "til",
     "kind_til": "down", "target": "homepod-kitchen", "text": "..."},
    {"t": "2026-04-15T20:11:00Z", "kind": "til",
     "kind_til": "up", "target": "homepod-kitchen"},
    {"t": "2026-04-16T09:00:00Z", "kind": "release",
     "product": "home_assistant_core", "version": "2026.4.3"},
    ...
  ]
}
```

`format=html` renders this as a vertical timeline. `format=md` produces a
markdown blob suitable to paste into a Noisebridge wiki post. This is the
view that actually answers "did HomePod 18.4 land 2 days before HA caught up?"

---

## 6. Configuration

`.env` / `pydantic-settings`:

```
HOMEWATCH_DB=/var/lib/homewatch/homewatch.sqlite
HOMEWATCH_TOKEN=...                  # optional bearer for write routes
HOMEWATCH_HA_URL=http://hass.local:8123
HOMEWATCH_HA_TOKEN=eyJ...            # long-lived access token
HOMEWATCH_HOMEPOD_DISCOVERY=pyatv    # 'pyatv' | 'zeroconf' | 'disabled'
HOMEWATCH_BIND=127.0.0.1:8765
HOMEWATCH_USER_AGENT=homewatch/0.1 (+contact)
```

User-Agent matters: Apple's support pages and GitHub will rate-limit anonymous
crawlers. Set a real UA with a contact URL.

---

## 7. Deployment notes

- **VPS placement.** The release pulls work fine from anywhere. The HA probe
  works from anywhere reachable to HA (so: Tailscale tailnet member, or HA
  exposed via Nabu Casa, or the same LAN). The HomePod mDNS probe **only**
  works on the same L2 segment.
- **Recommended split:** run homewatch on the VPS for the release/TIL halves
  (always reachable, persistent storage). For HomePod probes, either:
  1. Add the VPS to a Tailscale tailnet with `--accept-routes` and run a
     small mDNS reflector on a LAN host (e.g. `mdns-repeater` on the HA
     box), OR
  2. Run a tiny `homewatch-probe` agent on the LAN that posts probe results
     up to the VPS via `POST /probe/ingest` (auth: bearer token). Same
     binary, different entrypoint. **Prefer this** — it's less fiddly and
     keeps multicast traffic local.
- **systemd timer** (later) for `curl -X POST .../releases/refresh` every
  30 min, and `.../probe/ha` every 5 min during incidents.

---

## 8. Library survey (per language)

Pinned roughly to current ecosystem; adjust at build time.

### Python (chosen for v1)

| Concern             | Library                         | Notes                                  |
|---------------------|---------------------------------|----------------------------------------|
| Web framework       | `fastapi` + `uvicorn`           | async; auto OpenAPI; tiny.             |
| HTTP client         | `httpx`                         | async, follows redirects, HTTP/2.      |
| Feed parsing        | `feedparser`                    | RSS + Atom, robust to weird feeds.     |
| HTML scraping       | `selectolax` (fast) or `bs4`    | Apple support pages: `selectolax`.     |
| HomePod LAN probe   | `pyatv` (preferred) or `zeroconf` | pyatv: `pip install pyatv`.          |
| SQLite              | `sqlite3` stdlib + `sqlite-utils` for ad-hoc | WAL via `PRAGMA journal_mode=WAL`. |
| Settings            | `pydantic-settings`             |                                        |
| Templating          | `jinja2`                        | for the small HTML views.              |
| CLI                 | `typer` + `rich`                | shellout to curl in v1.                |
| Tests               | `pytest`, `pytest-httpx`        |                                        |

### Node / TypeScript (alt)

| Concern             | Library                         | Notes                                  |
|---------------------|---------------------------------|----------------------------------------|
| Web framework       | `hono` or `fastify`             | hono if Bun; fastify if Node.          |
| Feed parsing        | `rss-parser`                    |                                        |
| HTML scraping       | `cheerio` or `linkedom`         | linkedom faster.                       |
| HomePod LAN probe   | `node-pyatv` (wraps pyatv) **or** `bonjour-service` for raw mDNS | node-pyatv still requires pyatv on host. |
| SQLite              | `better-sqlite3` (sync) or `bun:sqlite` | sync is fine for this load.    |
| HTTP client         | native `fetch`                  | Node 20+ / Bun.                        |
| CLI                 | `cac` or `commander`            |                                        |

### Go (alt)

| Concern             | Library                         | Notes                                  |
|---------------------|---------------------------------|----------------------------------------|
| Web framework       | stdlib `net/http` + `chi`       |                                        |
| Feed parsing        | `github.com/mmcdole/gofeed`     |                                        |
| HTML scraping       | `github.com/PuerkitoBio/goquery`|                                        |
| HomePod LAN probe   | `github.com/grandcat/zeroconf` or `github.com/hashicorp/mdns` | Raw mDNS only — no Go pyatv equivalent. Read TXT records yourself. |
| SQLite              | `modernc.org/sqlite` (pure Go, no cgo) or `mattn/go-sqlite3` (cgo) | `modernc.org/sqlite` for easy cross-compile. |

### Rust (alt)

| Concern             | Library                         | Notes                                  |
|---------------------|---------------------------------|----------------------------------------|
| Web framework       | `axum`                          |                                        |
| Feed parsing        | `feed-rs`                       |                                        |
| HTML scraping       | `scraper` (CSS selectors)       |                                        |
| HomePod LAN probe   | `mdns-sd` or `simple-mdns`      | Raw mDNS.                              |
| SQLite              | `rusqlite` or `sqlx`            | `rusqlite` simpler for this app.       |
| HTTP client         | `reqwest`                       |                                        |
| CLI                 | `clap`                          |                                        |

### ClojureScript (alt — likely on Node runtime)

| Concern             | Library                         | Notes                                  |
|---------------------|---------------------------------|----------------------------------------|
| Web framework       | `macchiato` (Node) or interop with `hono` via shadow-cljs | If JVM is OK, prefer Clojure + Reitit + jdbc.next. |
| Feed parsing        | npm `rss-parser` via `cljs-bean`|                                        |
| SQLite              | `better-sqlite3` via JS interop | shadow-cljs handles npm fine.          |
| HomePod LAN probe   | `bonjour-service` via interop, or shellout to `dns-sd -B _airplay._tcp` | mDNS in cljs is awkward; consider a tiny Python sidecar. |

> **Recommendation:** Python for v1. The HomePod probe is the
> highest-friction piece in any language; pyatv has done the reverse-engineering
> work and is the path of least resistance. Reach for Go only if a single
> static binary on the LAN agent matters more than dev velocity.

---

## 9. Open questions / decisions for build time

1. **mDNS reflector vs LAN agent.** Default to LAN agent (option 7-2). Decide
   if the agent is the same homewatch binary in `--probe-only` mode or a
   separate ~100-line script. *Lean toward same binary — one thing to deploy.*
2. **TIL ergonomics.** Should `/til/drop/down/X` immediately fire a probe of
   X (HA or HomePod by name) and store both the TIL row and a probe row?
   Probably yes — captures "what version was running when it broke" with
   zero extra effort. **Default: yes; add `?probe=false` to opt out.**
3. **Beta / RC handling.** Channel filter defaults to `stable`. Apple
   developer feed will dump betas; decide whether to store them with
   `channel='beta'` (yes) and exclude by default in `/timeline` (yes).
4. **Apple Home app version.** Not directly inspectable. The Home app ships
   with iOS, so `ios` releases stand in for it. Note this in the
   timeline UI ("Home app version follows iOS").
5. **Rate limits.** GitHub atom feeds: be polite, cache with ETag. Apple
   support pages: no documented rate limit but they will 403 on aggressive
   crawling; cache aggressively (5 min minimum between fetches).
6. **HA WebSocket vs REST.** REST `/api/config` is enough for version. If we
   ever want "is the Matter integration loaded and healthy" as part of a
   probe, switch to the WS API and inspect `config_entries`.

---

## 10. v1 acceptance

- [ ] `POST /releases/refresh` populates the `releases` table from all six
      sources, idempotently.
- [ ] `GET /releases/latest?product=homepod_software` returns the most recent
      HomePod version with date and notes.
- [ ] `POST /probe/ha` inserts a row with the running HA version.
- [ ] `POST /probe/homepods` (run on LAN) discovers ≥1 HomePod and records
      its `osvers`.
- [ ] `GET /til/drop/down/homepod-kitchen?text=foo` from a phone browser
      adds a row and returns `OK <id>`.
- [ ] `GET /timeline?since=…` shows releases and TIL events interleaved.
- [ ] DB survives restart; WAL checkpointed on shutdown.

That's enough to look at the past month and see whether HomePod 18.x rollouts
actually do precede HA breakage windows.

---

## 11. Operation modes (v1.1 — CLI-first)

homewatch is a local tool first. Everything works from the CLI against the
local SQLite file with no daemon running. The daemon and remote modes are
additive, not required.

### 11.1 Backends (`client.py`)

One async `Backend` interface, two implementations the CLI picks between:

```python
class Backend(Protocol):
    async def refresh(self, source: str | None) -> dict: ...
    async def til(self, kind, target, text, tags, at, probe) -> int: ...
    async def timeline(self, *, since, until, products, include_betas, fmt) -> str: ...
    async def releases(self, *, product, since, until, channel) -> list[dict]: ...
    async def latest(self, product, channel) -> dict | None: ...
    async def show(self, product, version, channel) -> dict: ...   # full notes (§12)
    async def probe_ha(self) -> dict: ...
    async def probe_homepods(self) -> list[dict]: ...
    async def sources(self) -> list[dict]: ...
```

- **`LocalBackend` (default).** Opens the DB (`get_db`), creates one httpx
  client with the configured User-Agent, calls the core service functions
  directly, and checkpoints the WAL on exit. This is what runs when you type
  `homewatch refresh`. No network listener, no port.
- **`RemoteBackend`.** Used when `HOMEWATCH_URL` (or `--remote URL`) is set:
  the same methods, implemented as HTTP calls to a daemon, carrying the bearer
  token. This is the cht.sh-style "drive a homewatch on the VPS from my laptop".

Selection precedence: `--remote` flag > `HOMEWATCH_URL` env > local (default).

### 11.2 Cadence

- **Single-shot (default / cron-friendly).** `homewatch refresh [--probe]` does
  one pull (and optional probe) and exits. Schedule it however you like:
  `0 * * * * homewatch refresh` (hourly) or a launchd plist. This is the model:
  the tool is a single-shot updater; the *scheduler* is cron/launchd/systemd.
- **Watch loop (secondary — "watchman").** `homewatch watch` is a foreground
  convenience that repeats the single-shot on an interval for a bounded window:
  `--interval 1h`, `--for 7d`, optional `--until-new [--product P]` to exit
  early the moment a new release lands (the "temporary active probing until an
  update" case). It is just a loop over the single-shot path — no daemon.

### 11.3 CLI surface (v1.1)

```
homewatch til down|up|note <target> [text] [--tag t]   # direct write, auto-probe
homewatch refresh [--source S] [--probe]               # single-shot pull
homewatch watch [--interval 1h] [--for 7d] [--until-new] [--product P] [--probe]
homewatch releases [--product P] [--since D] [--channel C]
homewatch latest <product> [--channel stable]
homewatch show <product> [version]                     # full notes on demand (§12)
homewatch timeline [--since D] [--products a,b] [--format md|json|html]
homewatch probe ha | homepods
homewatch sources                                      # streams + freshness (§12)
homewatch serve [--reload]                             # run the daemon (secondary)
```

All read/write commands accept `--remote URL` to target a daemon instead of the
local DB.

---

## 12. On-demand notes & update streams

### 12.1 Summary now, full body on demand

Refresh stores a **short summary** (excerpt, ~280 chars) plus the canonical
**URL** for each release — never the full body — to keep the DB lean. The full
release notes are fetched lazily:

```
homewatch show homepod_software 18.4
homewatch show home_assistant_core            # latest stable if version omitted
```

`show` looks up the row, then fetches/extracts the full text from its source:
re-parsing the HomePod notes page section, or fetching the GitHub release /
HA blog post body and reducing it to readable text. Nothing is persisted by
`show`; it's a live read of the linked stream.

### 12.2 Update streams we link

Every release row carries a `url` into the upstream "what's new" stream, and
`homewatch sources` lists them with last-fetched freshness:

| Product family        | Stream                                                              |
|-----------------------|--------------------------------------------------------------------|
| Home Assistant Core   | `github.com/home-assistant/core/releases` (atom)                   |
| Home Assistant (blog) | `home-assistant.io/blog` / `atom.xml` — breaking changes           |
| Home Assistant OS     | `github.com/home-assistant/operating-system/releases` (atom)       |
| Apple security        | `support.apple.com/en-us/100100`                                   |
| Apple developer       | `developer.apple.com/news/releases/` (rss)                         |
| HomePod Software      | `support.apple.com/en-us/108045`                                   |

These are the canonical Apple/HA update streams; `homewatch sources` is the
one place to see them and whether our copy is fresh.
