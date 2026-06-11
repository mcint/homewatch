# Reference — CLI conventions

Small, opinionated conventions homewatch follows, with the reasoning so future
commands stay consistent.

## Time flows down (oldest-first default)

Time-series listings (`releases`, `timeline`) print **oldest-first**, so reading
down the screen moves forward in time and the **newest line sits nearest the
prompt** — your next fixation point. `-r/--reverse` gives newest-first for
pager/`fzf`/"glance at the top" use.

Why: a terminal grows downward by turns (new output appends at the bottom); so
*within* a turn, time should grow the same way rather than zig-zag. This matches
how log files and `journalctl` work (append at bottom, oldest-first), and keeps
the static view consistent with follow mode (`-f` streams new entries in at the
bottom).

There are two camps in the ecosystem, and we picked the log/journal one:

- **Newest-first** (reverse-chronological default): `ls -t`, `git log`. Optimizes
  for "what just happened", at the top.
- **Oldest-first / append-at-bottom**: `journalctl`, `tail`, plain log files,
  shell `history`. Optimizes for time-consistency and follow-mode parity. The
  systemd maintainers kept journalctl's oldest-first default despite pushback,
  exposing `-r` (reverse), `-e` (jump to end), and `-n` (last N) instead of
  flipping it — see the thread below.

We follow the journal camp; `-r` is the escape hatch.

### Links — people talking this through

- systemd-devel: ["Make journalctl start at the end of the journal by
  default"](https://systemd-devel.freedesktop.narkive.com/s2Yc2T7d/make-journalctl-start-at-the-end-of-the-journal-by-default)
  — the debate over default ordering; conclusion was to document `-r`/`-e`/`-n`
  rather than change the default.
- [`journalctl(1)` man page](https://man7.org/linux/man-pages/man1/journalctl.1.html)
  — default oldest-first; `-r` reverses, `-e`/`-n` for recent.
- [Reversing `ls` listings (`ls -t` / `-r`)](https://www.simplified.guide/linux/file-folder-list-reverse)
  — the newest-first camp, for contrast.

## Other conventions in use

- **Relative time** (`--since`) parses systemd-style spans — `3M 2w 1d 2h 2m
  26000s`, case-significant (`M` months, `m` minutes) — or an ISO date, or `0`
  = all. (`homewatch/cli.py:parse_since`)
- **Verb × Noun grammar** with `-h` help panels; `--remote`/`HOMEWATCH_URL` as
  the transport envelope. See spec §11.4.
- **Discoverable at every step**: invalid product ids error with the valid list;
  `homewatch products` enumerates them.
- **Network/enrollment is best-effort & mutable**: SSID/IP/subnet and DHCP/DNS
  (incl. parent) names can change; stored as *last-seen* and identifiers are
  merged on re-sighting rather than treated as immutable keys.
