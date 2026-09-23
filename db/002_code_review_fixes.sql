-- ============================================================================
-- 002 — Code-review hardening
--   * Idempotency keys are scoped per employee (one employee's key can no
--     longer collide with, or return, another employee's site visit).
--   * "Live" opportunity uniqueness covers ATTRIBUTION_CONFLICT as well as
--     ACTIVE, matching what the application treats as live. A conflict can then
--     never be resolved back to ACTIVE alongside a second ACTIVE opportunity.
-- Safe to re-run.
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- site_visits.idempotency_key: global UNIQUE -> UNIQUE per employee
-- ---------------------------------------------------------------------------
ALTER TABLE site_visits DROP CONSTRAINT IF EXISTS site_visits_idempotency_key_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_site_visits_employee_idempotency_key
    ON site_visits(employee_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

-- ---------------------------------------------------------------------------
-- opportunities: one LIVE (ACTIVE or ATTRIBUTION_CONFLICT) opportunity per
-- lead+plot, and per lead+project when no plot is selected.
-- ---------------------------------------------------------------------------
DROP INDEX IF EXISTS uq_opportunities_active_lead_property;
CREATE UNIQUE INDEX uq_opportunities_active_lead_property
    ON opportunities(lead_id, property_id)
    WHERE status IN ('ACTIVE', 'ATTRIBUTION_CONFLICT') AND property_id IS NOT NULL;

DROP INDEX IF EXISTS uq_opportunities_active_lead_project_no_property;
CREATE UNIQUE INDEX uq_opportunities_active_lead_project_no_property
    ON opportunities(lead_id, project_id)
    WHERE status IN ('ACTIVE', 'ATTRIBUTION_CONFLICT') AND property_id IS NULL;

COMMIT;
