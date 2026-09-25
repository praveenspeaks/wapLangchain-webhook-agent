-- Additive migration: apply to the business database (TESTING_DB_URL).
-- Does not change or remove existing business tables.
BEGIN;

CREATE TABLE IF NOT EXISTS greeting_occasions (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL CHECK (length(trim(name)) > 0),
    occasion VARCHAR(20) NOT NULL CHECK (occasion IN ('birthday', 'anniversary')),
    month SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),
    day SMALLINT NOT NULL CHECK (day BETWEEN 1 AND 31),
    year SMALLINT CHECK (year BETWEEN 1 AND 9999),
    country CHAR(2) NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
    phone_number VARCHAR(16) NOT NULL CHECK (phone_number ~ '^\+[1-9][0-9]{7,14}$'),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT valid_occasion_date CHECK (make_date(COALESCE(year, 2000), month, day) IS NOT NULL),
    UNIQUE (phone_number, occasion, month, day)
);

CREATE INDEX IF NOT EXISTS greeting_occasions_date_idx
    ON greeting_occasions (month, day) WHERE enabled;

-- A durable delivery ledger survives process restarts and multiple replicas.
CREATE TABLE IF NOT EXISTS greeting_deliveries (
    id BIGSERIAL PRIMARY KEY,
    occasion_id BIGINT NOT NULL REFERENCES greeting_occasions(id),
    occurrence_year SMALLINT NOT NULL CHECK (occurrence_year BETWEEN 1 AND 9999),
    scheduled_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'unknown')),
    provider_message_id TEXT,
    attempted_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (occasion_id, occurrence_year)
);

COMMIT;
