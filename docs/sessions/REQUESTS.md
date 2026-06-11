# REQUESTS — inbox + resolution log

Rolling log of asks and how they were resolved. Newest first. Keeps the rapid
back-and-forth auditable without bloating the spec.

## 2026-06-10 session

| # | Request                                                        | Resolution | Commit/ver |
|---|----------------------------------------------------------------|------------|-----------|
| 1 | Build to spec                                                  | done       | v0.1.0    |
| 2 | CLI-first; daemon secondary; single-shot/cron + watch-until    | done       | 0.2.0     |
| 3 | Pull docs/release notes; link HA + Apple update streams        | done (summary+link, `show` for full) | 0.2.0 / 0.2.1 |
| 4 | Unset stale `SSL_CERT_FILE` → use Homebrew bundle              | done (~/.zshenv self-heal) | n/a |
| 5 | endoflife.date source (API, dates)                            | done       | 0.2.x     |
| 6 | `sources` "failing / not using env"                            | root cause: cwd-relative DB → XDG data root + `HOMEWATCH_HOME` | 0.2.1 |
| 7 | before-(date) for undated tvOS/HomePod; check RSS pubDate      | done: RSS has dates; HomePod≈tvOS derived; else `≤bound` | 0.2.x |
| 8 | HomePod runs tvOS / shares release bundle                      | applied: tvOS dates date HomePod | 0.2.x |
| 9 | per-user XDG dir / `HOMEWATCH_HOME` data root                  | done       | 0.2.1     |
| 10| `-V/--version`, `-h` at all levels, `--urls` for click-view    | done       | 0.2.1     |
| 11| product discoverability at every step                          | `products` + completion + validation hints | 0.2.1 |
| 12| `releases` default recent window + stable; `--all`, `=0`       | done (`--months`, `--all`) | 0.2.1 |
| 13| patch-bump per commit                                          | adopted    | from 0.2.1|
| 14| clearer command grouping (verb/noun)                           | `-h` panels | 0.2.3    |
| 15| spec the grammar as a target                                   | spec §11.4 verb×noun Cartesian | 0.2.3 |
| 16| session summary + principled organization (Cartesian)          | STATUS.md + grammar + this log | 0.2.4 |

### Open (carried to STATUS)

- `status` command (release ⋈ deployed); notify-on-update; `show` via GitHub
  API; `latest` tie-break; LAN probes live.
