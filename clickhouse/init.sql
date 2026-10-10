CREATE DATABASE IF NOT EXISTS nodeglow;

CREATE TABLE IF NOT EXISTS syslog_messages
(
    timestamp       DateTime64(3, 'UTC') NOT NULL,
    received_at     DateTime64(3, 'UTC') NOT NULL DEFAULT now64(),
    source_ip       LowCardinality(String) NOT NULL,
    hostname        LowCardinality(String) DEFAULT '',
    host_id         Nullable(Int32),
    facility        Nullable(Int8),
    severity        Int8 DEFAULT 6,
    app_name        LowCardinality(String) DEFAULT '',
    message         String NOT NULL,
    template_hash   LowCardinality(String) DEFAULT '',
    tags            String DEFAULT '',
    noise_score     Int8 DEFAULT 50,
    extracted_fields Map(String, String) DEFAULT map(),
    geo_country     LowCardinality(String) DEFAULT '',
    geo_city        LowCardinality(String) DEFAULT '',
    INDEX idx_message_bloom message TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 4,
    INDEX idx_hostname_bloom hostname TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (severity, source_ip, timestamp)
TTL
    toDateTime(timestamp) + INTERVAL 1  DAY  WHERE severity = 7  AND noise_score >= 80,
    toDateTime(timestamp) + INTERVAL 2  DAY  WHERE severity = 7,
    toDateTime(timestamp) + INTERVAL 2  DAY  WHERE severity = 6  AND noise_score >= 80,
    toDateTime(timestamp) + INTERVAL 3  DAY  WHERE severity = 6,
    toDateTime(timestamp) + INTERVAL 7  DAY  WHERE severity = 5,
    toDateTime(timestamp) + INTERVAL 30 DAY  WHERE severity = 4,
    toDateTime(timestamp) + INTERVAL 90 DAY  WHERE severity IN (0, 1, 2, 3),
    toDateTime(timestamp) + INTERVAL 180 DAY WHERE noise_score <= 10
-- ttl_only_drop_parts = 0: the TTL rules above are row-level (WHERE severity
-- ...) on monthly partitions, so dropping only fully expired parts would keep
-- 1-day debug rows until the month's 90-day error rows expire. TTL merges
-- delete expired rows instead (at most every merge_with_ttl_timeout per
-- partition). Existing installs are switched by the app's startup migrations.
SETTINGS
    index_granularity = 8192,
    ttl_only_drop_parts = 0;

-- (syslog_aggregated and syslog_aggregated_mv were removed: nothing read them,
-- and the view aggregated every insert. The app's startup migrations drop them
-- on existing installs.)


-- ─────────────────────────────────────────────────────────────────────────────
-- Phase 2: time-series tables migrating from PostgreSQL.
-- These run dual-write alongside Postgres until cutover. See backend/services/
-- clickhouse_client.py for the insert helpers.
-- ─────────────────────────────────────────────────────────────────────────────

-- Ping check results — replaces ping_results in Postgres.
-- Volume estimate: 1 row per host per ping interval (~1/min). At 100 hosts,
-- that's ~144k rows/day. Daily partitions keep TTL deletes cheap.
-- host_name is denormalized so dashboard queries don't need to JOIN Postgres.
CREATE TABLE IF NOT EXISTS ping_checks
(
    timestamp   DateTime64(3, 'UTC') NOT NULL,
    host_id     UInt32 NOT NULL,
    host_name   LowCardinality(String) DEFAULT '',
    success     UInt8  NOT NULL,
    latency_ms  Nullable(Float32),
    -- Which probe produced this result; 0 means the core checked it itself.
    probe_id    UInt32 DEFAULT 0
)
ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(timestamp)
ORDER BY (host_id, timestamp)
TTL toDateTime(timestamp) + INTERVAL 30 DAY
SETTINGS
    index_granularity = 8192,
    ttl_only_drop_parts = 1;


-- Agent metric snapshots — replaces agent_snapshots in Postgres.
-- Volume estimate: 1 row per agent per heartbeat (~30s). At 50 agents,
-- ~144k rows/day. The data_json blob stays as raw JSON for now; we can
-- promote individual columns as the schema stabilises.
-- agent_name is denormalized so dashboard queries don't need to JOIN Postgres.
CREATE TABLE IF NOT EXISTS agent_metrics
(
    timestamp     DateTime64(3, 'UTC') NOT NULL,
    agent_id      UInt32 NOT NULL,
    agent_name    LowCardinality(String) DEFAULT '',
    cpu_pct       Nullable(Float32),
    mem_pct       Nullable(Float32),
    mem_used_mb   Nullable(Float32),
    mem_total_mb  Nullable(Float32),
    disk_pct      Nullable(Float32),
    load_1        Nullable(Float32),
    load_5        Nullable(Float32),
    load_15       Nullable(Float32),
    uptime_s      Nullable(UInt64),
    rx_bytes      Nullable(Float64),
    tx_bytes      Nullable(Float64),
    data_json     String DEFAULT ''
)
ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(timestamp)
ORDER BY (agent_id, timestamp)
TTL toDateTime(timestamp) + INTERVAL 7 DAY
SETTINGS
    index_granularity = 8192,
    ttl_only_drop_parts = 1;


-- Bandwidth samples — replaces bandwidth_samples in Postgres.
-- Source can be an agent, a Proxmox node, or a UniFi device.
-- Volume varies with how many interfaces / devices are tracked.
-- source_name denormalizes the agent/integration display name.
CREATE TABLE IF NOT EXISTS bandwidth_metrics
(
    timestamp       DateTime64(3, 'UTC') NOT NULL,
    source_type     LowCardinality(String) NOT NULL,   -- agent | proxmox | unifi
    source_id       String NOT NULL,                   -- agent_id, config_id, device_mac
    source_name     LowCardinality(String) DEFAULT '',
    interface_name  LowCardinality(String) NOT NULL,
    rx_bytes        UInt64 DEFAULT 0,
    tx_bytes        UInt64 DEFAULT 0,
    rx_rate_bps     UInt64 DEFAULT 0,
    tx_rate_bps     UInt64 DEFAULT 0
)
ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(timestamp)
ORDER BY (source_type, source_id, interface_name, timestamp)
TTL toDateTime(timestamp) + INTERVAL 7 DAY
SETTINGS
    index_granularity = 8192,
    ttl_only_drop_parts = 1;
