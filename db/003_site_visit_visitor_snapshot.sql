-- ============================================================================
-- 003 — Keep the visitor details exactly as entered on each Site Visit form
--   The Lead holds one master name/phone/email per customer (deduplicated by
--   normalized phone). Each Site Visit now also keeps what the employee typed
--   on that visit's form, so a returning customer's visit never loses its own
--   name/phone/email.
-- Safe to re-run.
-- ============================================================================

BEGIN;

ALTER TABLE site_visits ADD COLUMN IF NOT EXISTS visitor_name TEXT;
ALTER TABLE site_visits ADD COLUMN IF NOT EXISTS visitor_phone TEXT;
ALTER TABLE site_visits ADD COLUMN IF NOT EXISTS visitor_email TEXT;

-- Backfill existing visits from their Lead (best available record).
UPDATE site_visits sv
SET visitor_name = COALESCE(sv.visitor_name, l.name),
    visitor_phone = COALESCE(sv.visitor_phone, l.raw_phone, l.normalized_phone),
    visitor_email = COALESCE(sv.visitor_email, l.email)
FROM leads l
WHERE l.id = sv.lead_id
  AND (sv.visitor_name IS NULL OR sv.visitor_phone IS NULL);

-- Deliberately left nullable: this migration is applied while the previous app
-- version is still serving, and that version doesn't write these columns. The
-- API always fills them (both are required on the form); re-running this script
-- backfills any row written by the old version in between.

COMMIT;
