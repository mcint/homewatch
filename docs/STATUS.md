# STATUS — homewatch

Orientation surface. One screen: where the project is, what works, what's open.
Details live in [`specs/`](specs/), [`refs/`](refs/), [`sessions/`](sessions/).

**Version:** 0.4.0.  **Shape:** local-first CLI; daemon secondary. One SQLite
DB, seven release sources, a device registry, on-demand notes.

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
- Devices: probes **auto-enroll** (MAC/mDNS identifiers, SSID/IP/subnet), write
  `enrolled`/`retired` events to the timeline; `status` flags "behind".
- Data root: XDG by default; `HOMEWATCH_HOME` project root; `HOMEWATCH_DB`
  override. Config file `env` (XDG) / `.env` (cwd). `homewatch info` shows it.
- 118 tests green.

## Open cells / next

- **notify on `watch --until-new`** — currently prints + exits.
- **`show` via GitHub API** — cleaner release-note bodies than page scraping.
- **`latest` tie-break** — same-day Apple cross-cycle patches order by id.
- **LAN probes live** — HA/HomePod probes unit-covered only (need hardware).
- **ESPHome / self-reporting ingest** — a `POST`/`enroll` path for devices that
  report their own version (the model already supports `kind=esphome|custom`).

## Releases

`v0.1.0` daemon-first v1 · `v0.2.x` CLI-first + endoflife + grammar ·
`v0.3.x` XDG/data-root, relative `--since`, time-flows-down, URL fixes ·
`v0.4.0` device inventory + `status` + verb×noun groups.
