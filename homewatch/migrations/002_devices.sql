-- Device registry + per-probe network context. See spec §13.
-- A device is anything we track a running version / presence for: HomePod, Home
-- Assistant, ESPHome nodes, or a self-reporting app. Identity is a stable
-- synthetic device_id with an identifiers JSON blob (MAC, mDNS name, pyatv id…)
-- so we're not locked to MAC.

CREATE TABLE devices (
    device_id     TEXT PRIMARY KEY,     -- stable id (MAC-derived, mDNS, or given)
    kind          TEXT NOT NULL,        -- 'homepod' | 'home_assistant' | 'esphome' | 'custom'
    product       TEXT,                 -- maps to releases.product for "behind?" (nullable)
    name          TEXT,                 -- friendly, e.g. 'homepod-kitchen'
    identifiers   TEXT,                 -- JSON: {"mac": "...", "mdns": "...", ...}
    enrolled_at   TEXT NOT NULL,        -- first detection / enrollment
    last_seen_at  TEXT,
    last_version  TEXT,                 -- most recent detected/reported version
    status        TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'retired'
    ssid          TEXT,                 -- last-seen WiFi network
    subnet        TEXT,                 -- last-seen L2 range (CIDR)
    ip            TEXT,                 -- last-seen IP
    notes         TEXT
);

CREATE INDEX idx_devices_kind ON devices(kind, status);

-- Per-sighting network context, linking a probe row to a device.
ALTER TABLE probes ADD COLUMN device_id TEXT;
ALTER TABLE probes ADD COLUMN ssid TEXT;
ALTER TABLE probes ADD COLUMN ip TEXT;
ALTER TABLE probes ADD COLUMN subnet TEXT;
ALTER TABLE probes ADD COLUMN mac TEXT;
