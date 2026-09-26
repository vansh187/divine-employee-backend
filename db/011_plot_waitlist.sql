-- A visit to a plot held by another employee is still recorded; the visitor's lead waits here.
BEGIN;

CREATE TABLE IF NOT EXISTS plot_waitlist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    employee_id UUID NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    site_visit_id UUID NOT NULL REFERENCES site_visits(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'WAITING' CHECK (status IN ('WAITING', 'PROMOTED', 'CANCELLED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_plot_waitlist_waiting
    ON plot_waitlist(property_id, lead_id) WHERE status = 'WAITING';
CREATE INDEX IF NOT EXISTS idx_plot_waitlist_queue
    ON plot_waitlist(property_id, created_at) WHERE status = 'WAITING';

COMMIT;
