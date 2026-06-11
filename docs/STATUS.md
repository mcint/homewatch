# STATUS — homewatch

Orientation surface. One screen: where the project is, what works, what's open.
Details live in [`specs/`](specs/), [`refs/`](refs/), [`sessions/`](sessions/).

**Version:** 0.2.x (toward `v0.3.0`).  **Shape:** local-first CLI; daemon
secondary. One SQLite DB, seven release sources, on-demand notes.

## The model (deeper simplicity)

Everything is **VERB × NOUN**, under two envelopes **TRANSPORT × FORMAT**
(spec §11.4). The project's whole question lives on the noun axis:

```
release (available upstream)  ⋈  deployed (running on device)  →  timeline (the mix)
                                                     + event (human TIL)
```

## What works

| Verb \ Noun | release                          | deployed              | event                | timeline   |
|-------------|----------------------------------|-----------------------|----------------------|------------|
| fetch       | `refresh` · `watch`              | `refresh --probe`     | —                    | —          |
| query       | `releases` · `latest` · `show`   | `probe history`       | `til` (read)         | `timeline` |
| probe       | —                                | `probe ha` · `homepods` | —                  | —          |
| log         | —                                | —                     | `til down/up/note`   | —          |
| admin       | `sources` · `products` · `info` · `serve`                                            |

- 7 sources: HA core/blog/OS atoms, Apple security + HomePod-notes scrapers,
  Apple developer RSS, **endoflife.date** (Apple-OS dates).
- Dates: exact → **HomePod≈tvOS** derived → `≤bound`. (refs/products-and-streams)
- Data root: XDG by default; `HOMEWATCH_HOME` project root; `HOMEWATCH_DB`
  override. `homewatch info` shows what's in effect.
- 95 tests green.

## Open cells / next

- **`status`** — `query × (release ⋈ deployed)`: per target, running version vs
  latest available ("am I behind?"). Highest-value gap.
- **notify on `watch --until-new`** — currently prints + exits.
- **`show` via GitHub API** — cleaner release-note bodies than page scraping.
- **`latest` tie-break** — same-day Apple cross-cycle patches order by id.
- **LAN probes live** — HA/HomePod probes unit-covered only (need hardware).

## Releases

`v0.1.0` daemon-first v1 · `v0.2.x` CLI-first + endoflife + grammar ·
`v0.3.0` (pending) the CLI-first feature milestone.
