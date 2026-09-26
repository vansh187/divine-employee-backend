-- A DEAL_IN_PROGRESS opportunity is still live: keep one live opportunity per lead+plot / lead+project.
-- Requires 009 to be committed first.
BEGIN;

DROP INDEX IF EXISTS uq_opportunities_active_lead_property;
CREATE UNIQUE INDEX uq_opportunities_active_lead_property
    ON opportunities(lead_id, property_id)
    WHERE status IN ('NEW', 'INTERESTED', 'DEAL_IN_PROGRESS', 'ACTIVE', 'ATTRIBUTION_CONFLICT') AND property_id IS NOT NULL;

DROP INDEX IF EXISTS uq_opportunities_active_lead_project_no_property;
CREATE UNIQUE INDEX uq_opportunities_active_lead_project_no_property
    ON opportunities(lead_id, project_id)
    WHERE status IN ('NEW', 'INTERESTED', 'DEAL_IN_PROGRESS', 'ACTIVE', 'ATTRIBUTION_CONFLICT') AND property_id IS NULL;

COMMIT;
