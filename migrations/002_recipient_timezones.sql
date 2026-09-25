-- Existing records remain intact but are not sent until a timezone is supplied.
-- Do not guess a timezone from country or apply a global default.
BEGIN;
ALTER TABLE greeting_occasions ADD COLUMN IF NOT EXISTS timezone TEXT
    CHECK (timezone IS NULL OR length(trim(timezone)) > 0);
CREATE INDEX IF NOT EXISTS greeting_occasions_timezone_date_idx
    ON greeting_occasions (timezone, month, day) WHERE enabled;
COMMIT;
