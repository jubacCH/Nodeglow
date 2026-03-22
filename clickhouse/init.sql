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
SETTINGS
    index_granularity = 8192,
    ttl_only_drop_parts = 1;

-- Aggregated syslog table for dashboards and trend analysis (storage-level dedup)
CREATE TABLE IF NOT EXISTS syslog_aggregated
(
    bucket          DateTime NOT NULL,
    source_ip       LowCardinality(String) NOT NULL,
    hostname        LowCardinality(String) DEFAULT '',
    host_id         Nullable(Int32),
    severity        Int8 DEFAULT 6,
    app_name        LowCardinality(String) DEFAULT '',
    template_hash   LowCardinality(String) DEFAULT '',
    message_sample  String DEFAULT '',
    count           UInt32 DEFAULT 1,
    first_seen      DateTime64(3, 'UTC') NOT NULL,
    last_seen       DateTime64(3, 'UTC') NOT NULL
)
ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(bucket)
ORDER BY (source_ip, template_hash, severity, bucket)
TTL
    toDateTime(bucket) + INTERVAL 90 DAY
SETTINGS
    index_granularity = 8192;

-- Materialized view: auto-aggregate incoming messages into 1-minute buckets
CREATE MATERIALIZED VIEW IF NOT EXISTS syslog_aggregated_mv
TO syslog_aggregated
AS
SELECT
    toStartOfMinute(timestamp) AS bucket,
    source_ip,
    hostname,
    host_id,
    severity,
    app_name,
    template_hash,
    any(message) AS message_sample,
    count() AS count,
    min(timestamp) AS first_seen,
    max(timestamp) AS last_seen
FROM syslog_messages
GROUP BY bucket, source_ip, hostname, host_id, severity, app_name, template_hash;
