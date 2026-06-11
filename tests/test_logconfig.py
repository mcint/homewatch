"""Verbosity → logging level mapping."""

from __future__ import annotations

import logging

from homewatch import logconfig


def test_levels_for_tiers():
    assert logconfig.levels_for(0) == (logging.WARNING, logging.WARNING)
    assert logconfig.levels_for(1) == (logging.INFO, logging.WARNING)   # info: this project
    assert logconfig.levels_for(2) == (logging.DEBUG, logging.INFO)     # verbose: + libs info
    assert logconfig.levels_for(3) == (logging.DEBUG, logging.DEBUG)    # debug: all
    assert logconfig.levels_for(9) == (logging.DEBUG, logging.DEBUG)    # clamps


def test_setup_applies_levels():
    logconfig.setup(2)
    assert logging.getLogger("homewatch").level == logging.DEBUG
    assert logging.getLogger().level == logging.INFO
    logconfig.setup(0)
    assert logging.getLogger("homewatch").level == logging.WARNING


def test_uvicorn_level():
    assert logconfig.uvicorn_level(0) == "warning"
    assert logconfig.uvicorn_level(1) == "info"
    assert logconfig.uvicorn_level(3) == "debug"
