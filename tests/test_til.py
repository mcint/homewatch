"""TIL parsing + persistence."""

from __future__ import annotations

import pytest

from homewatch import til


def test_record_and_query_roundtrip(db):
    rid = til.record(db, kind="down", target="homepod-kitchen", text="siri dead",
                     tags="upgrade,maybe-fixed", source="url")
    assert rid > 0
    events = til.query(db)
    assert len(events) == 1
    e = events[0]
    assert e.kind == "down"
    assert e.target == "homepod-kitchen"
    assert e.tags == ["upgrade", "maybe-fixed"]
    assert e.source == "url"


def test_text_defaults_to_kind_word(db):
    til.record(db, kind="up", target="homepod-kitchen")
    assert til.query(db)[0].text == "up"


def test_unknown_kind_rejected(db):
    with pytest.raises(ValueError):
        til.record(db, kind="exploded", text="x")


def test_at_override_normalized_to_utc(db):
    til.record(db, kind="note", text="x", at="2026-04-15 19:42")
    assert til.query(db)[0].occurred_at == "2026-04-15T19:42:00Z"


def test_at_with_z_suffix(db):
    til.record(db, kind="note", text="x", at="2026-04-15T19:42:00Z")
    assert til.query(db)[0].occurred_at == "2026-04-15T19:42:00Z"


def test_filters(db):
    til.record(db, kind="down", target="ha", text="a", at="2026-01-01T00:00:00Z")
    til.record(db, kind="up", target="ha", text="b", at="2026-02-01T00:00:00Z")
    til.record(db, kind="down", target="hp", text="c", at="2026-03-01T00:00:00Z")
    assert len(til.query(db, kind="down")) == 2
    assert len(til.query(db, target="ha")) == 2
    assert len(til.query(db, since="2026-02-15T00:00:00Z")) == 1


def test_query_is_reverse_chron(db):
    til.record(db, kind="note", text="old", at="2026-01-01T00:00:00Z")
    til.record(db, kind="note", text="new", at="2026-05-01T00:00:00Z")
    assert [e.text for e in til.query(db)] == ["new", "old"]


def test_soft_delete_hides_from_default_query(db):
    rid = til.record(db, kind="note", text="typo")
    assert til.soft_delete(db, rid) is True
    assert til.query(db) == []
    assert len(til.query(db, include_deleted=True)) == 1
    # Deleting again is a no-op.
    assert til.soft_delete(db, rid) is False


def test_render_tsv_has_header_and_row(db):
    til.record(db, kind="down", target="ha", text="line\twith\ttabs")
    out = til.render_tsv(til.query(db))
    assert out.startswith("id\toccurred_at\tkind\ttarget\ttags\ttext")
    assert "line with tabs" in out  # tabs in body flattened
