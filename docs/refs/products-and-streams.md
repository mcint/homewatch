# Reference — products, update streams, date precision

Durable facts the code relies on. If upstream reality drifts from this, update
here and in the matching source. See the full design in
[`../specs/homewatch-spec.md`](../specs/homewatch-spec.md).

## Product vocabulary

Canonical `releases.product` ids (also `homewatch products`):

| id                     | label                          | dated by                         |
|------------------------|--------------------------------|----------------------------------|
| `home_assistant_core`  | Home Assistant Core            | GH atom + blog (exact)           |
| `home_assistant_os`    | Home Assistant OS              | GH atom (exact)                  |
| `homepod_software`     | HomePod Software (tracks tvOS) | derived from tvOS; else bound    |
| `ios`                  | iOS                            | security page, dev RSS, endoflife|
| `ipados`               | iPadOS                         | "                                |
| `macos`                | macOS                          | "                                |
| `tvos`                 | tvOS                           | "                                |
| `watchos`              | watchOS                        | "                                |
| `visionos`             | visionOS                       | "                                |
| `safari`               | Safari                         | security page                    |

## Update streams (linked per release `url`)

| Product family        | Stream                                                        |
|-----------------------|---------------------------------------------------------------|
| HA Core               | `github.com/home-assistant/core/releases.atom`                |
| HA blog               | `home-assistant.io/atom.xml` (breaking changes)               |
| HA OS                 | `github.com/home-assistant/operating-system/releases.atom`    |
| Apple security        | `support.apple.com/en-us/100100`                              |
| Apple developer       | `developer.apple.com/news/releases/rss/releases.rss`          |
| HomePod Software       | `support.apple.com/en-us/108045`                             |
| Apple OS dates         | `endoflife.date/api/{ios,ipados,macos,tvos,watchos,visionos}`|

## tvOS ↔ HomePod (load-bearing fact)

**HomePod software is built on tvOS and shares its version numbers** (HomePod
18.4 ↔ tvOS 18.4). HomePod release *dates* are not published anywhere
machine-readable, so an undated HomePod release inherits the same-version tvOS
date, shown as `≈DATE (tracks tvOS)`. Bare HomePod majors (`26`) match tvOS
`26.0`.

## Date precision

`timeline.derive_date` resolves a display date per release:

1. **exact** — feed `pubDate`, Apple security table, or endoflife.
2. **tvos** — HomePod inheriting the same-version tvOS date (`≈`).
3. **bound** — still undated → `≤discovered_at` (we know it existed by then).

Stored `released_at` is left NULL when truly unknown; the derivation is
display-only.

## Known upstream quirks

- Apple **removed HomePod rows from the security-table HTML** (now a JS data
  blob); `apple_security` no longer yields HomePod dates — hence the tvOS
  derivation. Re-check periodically.
- The **HomePod notes page** (`108045`) has versions + notes but no per-version
  dates; the parser stores notes and leaves dates NULL.
- **endoflife.date** has no HomePod and no Home Assistant.
- `latest` can tie-break oddly when several Apple cycles ship a patch the same
  day (same `latestReleaseDate`); ordering then falls to row id. Low impact.
