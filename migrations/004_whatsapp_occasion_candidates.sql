BEGIN;

-- Birthday/anniversary wishes the owner sent, captured for manual review.
-- Nothing here reaches greeting_occasions until the owner adds and saves it.
CREATE TABLE IF NOT EXISTS whatsapp_occasion_candidates (
    id BIGSERIAL PRIMARY KEY,
    instance TEXT NOT NULL,
    message_id TEXT NOT NULL,
    chat_jid TEXT NOT NULL,
    is_group BOOLEAN NOT NULL,
    recipient_jid TEXT,
    phone_number TEXT,
    name TEXT,
    occasion TEXT NOT NULL CHECK (occasion IN ('birthday', 'anniversary')),
    month SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),
    day SMALLINT NOT NULL CHECK (day BETWEEN 1 AND 31),
    belated BOOLEAN NOT NULL DEFAULT false,
    body TEXT NOT NULL DEFAULT '',
    message_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'added', 'dismissed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (instance, chat_jid, message_id)
);
CREATE INDEX IF NOT EXISTS whatsapp_occasion_candidates_new_idx
    ON whatsapp_occasion_candidates (instance, id) WHERE status = 'new';

COMMIT;
