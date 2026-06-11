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

## 2026-06-11 session

| # | Request                                                        | Resolution | Commit/ver |
|---|----------------------------------------------------------------|------------|-----------|
| 17| endoflife `/apple` default link + per-product links            | `/apple` 404s; added `PRODUCT_PAGE` per-product map | 0.3.1 |
| 18| `--since` relative (systemd-style) instead of `--months`       | `parse_since` (2y 2M 2w 2d 2h 2m 26000s) | 0.3.2 |
| 19| `latest tvos/watchos` URL cut off; absolutize Apple URLs       | parser + migration 003 backfill | 0.3.3 / 0.3.10 |
| 20| HomePod tracks tvOS → date homepod from tvos                   | effective-date ordering | 0.3.4 |
| 21| releases default 3M; then oldest-first (time flows down)       | done + cli-conventions doc | 0.3.5 / 0.3.9 |
| 22| `--channel all` keyword; drop `--all` flag                     | done | 0.3.7 |
| 23| XDG config file `env` (not .env); info read-vs-checked         | done | 0.3.12 |
| 24| "not seeing my HomePod" when probing                           | pyatv used model only; now TXT+name; verified live | 0.4.1 |
| 25| make repo public + description/topics                          | done (public) | n/a |
| 26| graceful data migrations (was Alembic in open-webui)           | refs/migrations.md; numbered-.sql + backup; Alembic = ORM-coupled | 0.4.2 |
| 27| device display name separate from detected                     | `display_name` + `devices rename` | 0.4.3 |
| 28| `migrate` not top-level → admin/meta tree                      | `admin migrate status/backup` | 0.4.4 |
| 29| smartcase/regex device search across fields                    | `devices list PATTERN` | 0.4.5 |
| 30| releases caveats out of top `--help`, in `releases --help`     | terse summary | 0.4.5 |
| 31| persist the earlier (debug) HomePod scan                       | re-probed into the real default DB | n/a |
| 32| PyPI installable → instead, run-from-GitHub instructions       | metadata+extras ready; README install (uvx/pip), no publish | 0.4.6 |

### Open (carried to STATUS)

- `status` command (release ⋈ deployed); notify-on-update; `show` via GitHub
  API; `latest` tie-break; LAN probes live.
