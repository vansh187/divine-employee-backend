-- ============================================================================
-- 006 — Plot numbers are unique per project regardless of case
--   The original UNIQUE (project_id, plot_no) is case-sensitive, so "B46a" and
--   "B46A" could exist as two separate plots and the same plot be sold twice.
--   This index rejects that at the database level, whatever inserts the row.
-- Fails (and changes nothing) if case-only duplicates already exist.
-- Safe to re-run.
-- ============================================================================

BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS uq_properties_project_plot_no_lower
    ON properties (project_id, lower(plot_no));

COMMIT;
