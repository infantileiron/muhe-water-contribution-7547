-- 抄表批次分摊功能（累计读数 → 时长分摊月度观测）。
-- 新环境由 SQLAlchemy metadata 直接建表；本文件供既有生产库 reviewed ALTER 使用。

CREATE TABLE IF NOT EXISTS meter_batches (
    id VARCHAR PRIMARY KEY,
    idempotency_key VARCHAR NOT NULL UNIQUE,
    name VARCHAR NOT NULL UNIQUE,
    source VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'accepted',
    readings JSON NOT NULL,
    person_days JSON NOT NULL,
    created_at VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS meter_observation_sets (
    id VARCHAR PRIMARY KEY,
    batch_id VARCHAR NOT NULL UNIQUE REFERENCES meter_batches(id),
    version INTEGER NOT NULL DEFAULT 1,
    status VARCHAR NOT NULL DEFAULT 'accepted',
    monthly JSON NOT NULL,
    segments JSON NOT NULL,
    reconciliation JSON NOT NULL,
    checksum VARCHAR NOT NULL,
    dataset_id VARCHAR NOT NULL REFERENCES consumption_datasets(id),
    created_at VARCHAR NOT NULL
);

-- 既有观测集补充来源标记；原 JSON 表单导入为 manual，抄表分摊为 meter。
ALTER TABLE consumption_datasets ADD COLUMN IF NOT EXISTS origin VARCHAR NOT NULL DEFAULT 'manual';
ALTER TABLE consumption_datasets ADD COLUMN IF NOT EXISTS meter_set_id VARCHAR REFERENCES meter_observation_sets(id);

CREATE INDEX IF NOT EXISTS ix_meter_batches_idempotency_key ON meter_batches(idempotency_key);
