-- homewatch initial schema. See spec §2.

-- Every release we know about, from any source.
CREATE TABLE releases (
    id              INTEGER PRIMARY KEY,
    product         TEXT NOT NULL,        -- 'home_assistant_core', 'homepod_software',
                                          -- 'ios', 'ipados', 'macos', 'home_assistant_os'
    version         TEXT NOT NULL,        -- '2026.4.3', '18.4', '15.4.1'
    channel         TEXT,                 -- 'stable' | 'beta' | 'rc' | NULL
    released_at     TEXT,                 -- ISO 8601, may be NULL if unknown
    title           TEXT,
    url             TEXT,
    source          TEXT NOT NULL,        -- which sources/*.py produced this
    raw_id          TEXT,                 -- feed entry guid / url / hash
    notes           TEXT,                 -- markdown; release notes excerpt
    discovered_at   TEXT NOT NULL,        -- when *we* first saw it
    UNIQUE(product, version, channel)
);

CREATE INDEX idx_releases_product_date ON releases(product, released_at DESC);

-- Snapshots from on-network probes: "what version was this device running on date X".
CREATE TABLE probes (
    id              INTEGER PRIMARY KEY,
    probed_at       TEXT NOT NULL,
    target_kind     TEXT NOT NULL,        -- 'home_assistant' | 'homepod'
    target_id       TEXT NOT NULL,        -- HA URL, or HomePod identifier (mDNS name / mac)
    version         TEXT,                 -- NULL if probe failed
    extra_json      TEXT,                 -- TXT record dump, /api/config dump, etc.
    error           TEXT                  -- if probe failed: short reason
);

CREATE INDEX idx_probes_target_time ON probes(target_kind, target_id, probed_at DESC);

-- TIL / event log: human-entered observations.
CREATE TABLE til_events (
    id              INTEGER PRIMARY KEY,
    occurred_at     TEXT NOT NULL,        -- when the event happened (user-supplied or now)
    recorded_at     TEXT NOT NULL,        -- when we wrote the row
    kind            TEXT NOT NULL,        -- 'down' | 'up' | 'note' | 'observation' | 'deleted'
    target          TEXT,                 -- e.g. 'homepod-living-room', 'ha', 'ha+homepod'
    text            TEXT NOT NULL,        -- the body
    tags            TEXT,                 -- JSON array of strings
    source          TEXT                  -- 'web' | 'url' | 'cli' | 'api'
);

CREATE INDEX idx_til_occurred ON til_events(occurred_at DESC);

-- Last-fetched timestamp per source so we can avoid hammering and report freshness.
CREATE TABLE source_state (
    source          TEXT PRIMARY KEY,
    last_fetched_at TEXT,
    last_status     TEXT,                 -- 'ok' | 'error: ...'
    etag            TEXT,
    last_modified   TEXT
);
