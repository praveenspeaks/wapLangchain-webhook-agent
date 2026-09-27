BEGIN;

CREATE TABLE IF NOT EXISTS whatsapp_messages (
    id BIGSERIAL PRIMARY KEY,
    instance TEXT NOT NULL,
    message_id TEXT NOT NULL,
    chat_jid TEXT NOT NULL,
    is_group BOOLEAN NOT NULL,
    sender_jid TEXT,
    sender_name TEXT,
    from_me BOOLEAN NOT NULL,
    message_type TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    message_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    command_status TEXT NOT NULL DEFAULT 'ignored'
        CHECK (command_status IN ('ignored', 'pending', 'done')),
    UNIQUE (instance, chat_jid, message_id)
);
CREATE INDEX IF NOT EXISTS whatsapp_messages_summary_idx
    ON whatsapp_messages (instance, received_at) WHERE NOT from_me;
CREATE INDEX IF NOT EXISTS whatsapp_messages_commands_idx
    ON whatsapp_messages (instance, id) WHERE command_status = 'pending';

CREATE TABLE IF NOT EXISTS whatsapp_entry_drafts (
    id BIGSERIAL PRIMARY KEY,
    instance TEXT NOT NULL,
    entity TEXT NOT NULL,
    data JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'saved', 'cancelled')),
    saved_record_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS whatsapp_entry_active_idx
    ON whatsapp_entry_drafts (instance) WHERE status = 'draft';

CREATE TABLE IF NOT EXISTS whatsapp_outbox (
    id BIGSERIAL PRIMARY KEY,
    instance TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    recipient TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'unknown')),
    provider_message_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempted_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    UNIQUE (instance, dedup_key)
);
CREATE INDEX IF NOT EXISTS whatsapp_outbox_pending_idx
    ON whatsapp_outbox (instance, id) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS whatsapp_summary_runs (
    id BIGSERIAL PRIMARY KEY,
    instance TEXT NOT NULL,
    local_date DATE NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    message_count INTEGER NOT NULL,
    included_count INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (instance, local_date)
);

CREATE TABLE IF NOT EXISTS services (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    category VARCHAR(100) NOT NULL,
    phone_number VARCHAR(16) NOT NULL,
    location TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS restaurants (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    location TEXT NOT NULL,
    cuisine VARCHAR(100),
    phone_number VARCHAR(16),
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS places_to_visit (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    location TEXT NOT NULL,
    category VARCHAR(100) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMIT;
