"""Plain dataclasses for the domain rows. Persistence lives in the owning
module (til.py, sources/base.py, probes/__init__.py); these are just the
shapes that flow between layers. See spec §2–3."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Canonical product identifiers used in releases.product (spec §2).
PRODUCTS = (
    "home_assistant_core",
    "home_assistant_os",
    "homepod_software",
    "ios",
    "ipados",
    "macos",
    "tvos",
    "watchos",
    "visionos",
    "safari",
)

TIL_KINDS = ("down", "up", "note", "observation")

# Human labels for the product vocabulary — surfaced by `homewatch products`
# and CLI completion so valid ids are discoverable at every step.
PRODUCT_LABELS = {
    "home_assistant_core": "Home Assistant Core",
    "home_assistant_os": "Home Assistant OS",
    "homepod_software": "HomePod Software (tracks tvOS)",
    "ios": "iOS",
    "ipados": "iPadOS",
    "macos": "macOS",
    "tvos": "tvOS",
    "watchos": "watchOS",
    "visionos": "visionOS",
    "safari": "Safari",
}


@dataclass(slots=True)
class Release:
    product: str
    version: str
    source: str
    channel: str | None = None  # 'stable' | 'beta' | 'rc' | None
    released_at: str | None = None  # ISO 8601
    title: str | None = None
    url: str | None = None
    raw_id: str | None = None
    notes: str | None = None


@dataclass(slots=True)
class Probe:
    target_kind: str  # 'home_assistant' | 'homepod'
    target_id: str
    version: str | None = None
    extra: dict[str, Any] | None = None  # serialized to extra_json on insert
    error: str | None = None
    probed_at: str | None = None  # set at insert if None


@dataclass(slots=True)
class TilEvent:
    kind: str
    text: str
    target: str | None = None
    occurred_at: str | None = None  # set at insert if None
    recorded_at: str | None = None
    tags: list[str] = field(default_factory=list)
    source: str | None = None
    id: int | None = None


@dataclass(slots=True)
class SourceState:
    source: str
    last_fetched_at: str | None = None
    last_status: str | None = None
    etag: str | None = None
    last_modified: str | None = None
