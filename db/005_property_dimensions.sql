-- ============================================================================
-- 005 — Plot dimensions as given in the site inventory sheets
--   Inventory sheets list width/length in metres and area in square metres and
--   square yards. area_sqft stays as the canonical area (derived from sq m).
-- Additive and nullable; safe to re-run.
-- ============================================================================

BEGIN;

ALTER TABLE properties ADD COLUMN IF NOT EXISTS width_m NUMERIC(10, 3);
ALTER TABLE properties ADD COLUMN IF NOT EXISTS length_m NUMERIC(10, 3);
ALTER TABLE properties ADD COLUMN IF NOT EXISTS area_sqm NUMERIC(12, 3);
ALTER TABLE properties ADD COLUMN IF NOT EXISTS area_sqyd NUMERIC(12, 2);

COMMIT;
