"""Verbosity → logging levels, shared by the CLI and daemon.

Conventional repeatable `-v` count; each level widens the net:

    0       homewatch=WARNING  libs=WARNING   (quiet — only problems)
    1  -v   homewatch=INFO     libs=WARNING   (what this project is doing)
    2  -vv  homewatch=DEBUG    libs=INFO      (+ network/library operations)
    3  -vvv homewatch=DEBUG    libs=DEBUG     (everything; --debug)

"libs" = third-party loggers (httpx, httpcore, pyatv, zeroconf, …) — everything
under the root logger that isn't ``homewatch``.
"""

from __future__ import annotations

import logging
import sys


def levels_for(verbosity: int) -> tuple[int, int]:
    """Return (homewatch_level, lib_level) for a verbosity count."""
    hw = (logging.DEBUG if verbosity >= 2 else
          logging.INFO if verbosity == 1 else logging.WARNING)
    lib = (logging.DEBUG if verbosity >= 3 else
           logging.INFO if verbosity >= 2 else logging.WARNING)
    return hw, lib


def setup(verbosity: int) -> None:
    """Configure root + homewatch loggers for the given verbosity (idempotent)."""
    hw, lib = levels_for(verbosity)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]  # replace, so re-runs don't duplicate output
    root.setLevel(lib)
    logging.getLogger("homewatch").setLevel(hw)


def uvicorn_level(verbosity: int) -> str:
    """uvicorn's --log-level string for a verbosity count."""
    return "debug" if verbosity >= 3 else "info" if verbosity >= 1 else "warning"
