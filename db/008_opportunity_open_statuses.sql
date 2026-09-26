-- One LIVE (NEW, INTERESTED, legacy ACTIVE, or ATTRIBUTION_CONFLICT) opportunity per lead+plot,
-- and per lead+project when no plot is selected. Requires 007 to be committed first.
BEGIN;

ALTER TABLE opportunities ALTER COLUMN status SET DEFAULT 'NEW';

DROP INDEX IF EXISTS uq_opportunities_active_lead_property;
CREATE UNIQUE INDEX uq_opportunities_active_lead_property
    ON opportunities(lead_id, property_id)
    WHERE status IN ('NEW', 'INTERESTED', 'ACTIVE', 'ATTRIBUTION_CONFLICT') AND property_id IS NOT NULL;

DROP INDEX IF EXISTS uq_opportunities_active_lead_project_no_property;
CREATE UNIQUE INDEX uq_opportunities_active_lead_project_no_property
    ON opportunities(lead_id, project_id)
    WHERE status IN ('NEW', 'INTERESTED', 'ACTIVE', 'ATTRIBUTION_CONFLICT') AND property_id IS NULL;

COMMIT;
