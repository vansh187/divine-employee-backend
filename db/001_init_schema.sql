-- ============================================================================
-- Divine Vision Infra — Employee Site Visit Portal
-- V1.5 schema: V1 core modules + REQ-25 Unified Opportunity & Attribution Engine
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- ENUMS
-- ============================================================================

CREATE TYPE employee_status AS ENUM ('ACTIVE', 'INACTIVE', 'SUSPENDED');

CREATE TYPE lead_lifecycle_status AS ENUM ('NEW', 'IN_PROGRESS', 'CONVERTED', 'LOST', 'DORMANT');

CREATE TYPE lock_status AS ENUM ('ACTIVE', 'EXPIRED', 'RELEASED');

CREATE TYPE follow_up_action_type AS ENUM ('CALL_LOGGED', 'NEXT_VISIT_SCHEDULED', 'PROPOSAL_SENT', 'NOTE');

CREATE TYPE attendance_status AS ENUM ('PRESENT', 'LATE', 'HALF_DAY', 'ABSENT', 'ON_SITE_VISIT', 'WORK_FROM_HOME', 'DAY_OFF');

CREATE TYPE day_off_status AS ENUM ('OPEN', 'SELECTED', 'FROZEN', 'COMPLETED');

CREATE TYPE property_status AS ENUM ('AVAILABLE', 'LOCKED', 'DEAL_LOCKED', 'SOLD');

CREATE TYPE site_visit_outcome AS ENUM ('INTERESTED', 'NOT_INTERESTED', 'FOLLOW_UP_REQUIRED', 'PROPOSAL_REQUESTED', 'NO_SHOW', 'OTHER');

CREATE TYPE source_owner_type AS ENUM ('EMPLOYEE', 'CHANNEL_PARTNER');

CREATE TYPE opportunity_source AS ENUM ('EMPLOYEE_SITE_VISIT', 'CHANNEL_PARTNER');

CREATE TYPE opportunity_status AS ENUM ('ACTIVE', 'ATTRIBUTION_CONFLICT', 'CONVERTED', 'LOST', 'EXPIRED', 'RELEASED');

CREATE TYPE attribution_status AS ENUM ('VERIFIED', 'CONFLICT', 'RESOLVED');

CREATE TYPE claim_type AS ENUM ('SITE_VISIT', 'ACCEPTED_APPOINTMENT', 'PLOT_PRESENTATION', 'PROPOSAL');

CREATE TYPE deal_status AS ENUM ('PENDING', 'CONFIRMED', 'CANCELLED', 'COMPLETED');

CREATE TYPE deal_lock_status AS ENUM ('ACTIVE', 'RELEASED');

CREATE TYPE notification_event_type AS ENUM (
    'SITE_VISIT_LOGGED', 'LEAD_LOCKED', 'LEAD_LOCK_EXPIRING', 'LEAD_LOCK_RELEASED',
    'PROPERTY_LOCKED', 'PROPERTY_LOCK_RELEASED', 'DAY_OFF_FROZEN', 'DAY_OFF_COMPLETED',
    'OPPORTUNITY_CREATED', 'OPPORTUNITY_CONFLICT', 'OPPORTUNITY_RESOLVED', 'DEAL_LOCKED'
);

-- ============================================================================
-- EMPLOYEE
-- ============================================================================

CREATE TABLE employees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT,
    password_hash TEXT NOT NULL,
    team TEXT,
    designation TEXT,
    status employee_status NOT NULL DEFAULT 'ACTIVE',
    weekly_day_off_allowance SMALLINT NOT NULL DEFAULT 1,
    business_timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_employees_updated_at BEFORE UPDATE ON employees
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Refresh tokens (JWT refresh-token rotation store)
CREATE TABLE refresh_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id UUID NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    replaced_by_token_hash TEXT
);

CREATE INDEX idx_refresh_tokens_employee ON refresh_tokens(employee_id);

-- ============================================================================
-- CHANNEL PARTNER (minimal — referenced by Opportunity Engine only)
-- ============================================================================

CREATE TABLE channel_partners (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_channel_partners_updated_at BEFORE UPDATE ON channel_partners
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- PROJECTS / PROPERTIES / PLOTS
-- ============================================================================

CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    location TEXT,
    phase TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_projects_updated_at BEFORE UPDATE ON projects
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE properties (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    plot_no TEXT NOT NULL,
    unit_type TEXT,
    area_sqft NUMERIC(10, 2),
    status property_status NOT NULL DEFAULT 'AVAILABLE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, plot_no)
);

CREATE TRIGGER trg_properties_updated_at BEFORE UPDATE ON properties
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_properties_project ON properties(project_id);
CREATE INDEX idx_properties_status ON properties(status);

-- ============================================================================
-- MASTER LEAD  (REQ-25 §16.3 — normalized phone is the primary dedup key)
-- ============================================================================

CREATE TABLE leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    normalized_phone TEXT NOT NULL UNIQUE,
    raw_phone TEXT,
    email TEXT,
    source TEXT NOT NULL DEFAULT 'EMPLOYEE_SITE_VISIT',
    originating_employee_id UUID REFERENCES employees(id) ON DELETE SET NULL,
    current_employee_id UUID REFERENCES employees(id) ON DELETE SET NULL,
    lifecycle_status lead_lifecycle_status NOT NULL DEFAULT 'NEW',
    first_visit_at TIMESTAMPTZ,
    latest_visit_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_leads_updated_at BEFORE UPDATE ON leads
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_leads_normalized_phone ON leads(normalized_phone);
CREATE INDEX idx_leads_current_employee ON leads(current_employee_id);

-- ============================================================================
-- SITE VISIT
-- ============================================================================

CREATE TABLE site_visits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id UUID NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE RESTRICT,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    property_id UUID REFERENCES properties(id) ON DELETE SET NULL,
    visit_at TIMESTAMPTZ NOT NULL,
    notes TEXT,
    attachments JSONB NOT NULL DEFAULT '[]'::jsonb,
    outcome site_visit_outcome,
    idempotency_key TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_site_visits_updated_at BEFORE UPDATE ON site_visits
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_site_visits_employee ON site_visits(employee_id);
CREATE INDEX idx_site_visits_lead ON site_visits(lead_id);
CREATE INDEX idx_site_visits_property ON site_visits(property_id);
CREATE INDEX idx_site_visits_visit_at ON site_visits(visit_at);

-- ============================================================================
-- LEAD LOCK / PROPERTY LOCK  (V1 §5 — 3-day protection, global property block)
-- ============================================================================

CREATE TABLE lead_locks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    employee_id UUID NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    site_visit_id UUID NOT NULL REFERENCES site_visits(id) ON DELETE RESTRICT,
    locked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    status lock_status NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only one ACTIVE lock per lead at a time (DB-level conflict protection)
CREATE UNIQUE INDEX uq_lead_locks_active_lead ON lead_locks(lead_id) WHERE status = 'ACTIVE';
CREATE INDEX idx_lead_locks_employee ON lead_locks(employee_id);
CREATE INDEX idx_lead_locks_expires_at ON lead_locks(expires_at) WHERE status = 'ACTIVE';

CREATE TABLE property_locks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    employee_id UUID NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    site_visit_id UUID NOT NULL REFERENCES site_visits(id) ON DELETE RESTRICT,
    lead_lock_id UUID NOT NULL REFERENCES lead_locks(id) ON DELETE CASCADE,
    locked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    status lock_status NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only one ACTIVE lock per property at a time — global reservation (V1 §5)
CREATE UNIQUE INDEX uq_property_locks_active_property ON property_locks(property_id) WHERE status = 'ACTIVE';
CREATE INDEX idx_property_locks_employee ON property_locks(employee_id);
CREATE INDEX idx_property_locks_expires_at ON property_locks(expires_at) WHERE status = 'ACTIVE';

-- ============================================================================
-- FOLLOW-UP ACTION (qualifying actions renew lock protection — V1 §5)
-- ============================================================================

CREATE TABLE follow_up_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    employee_id UUID NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    action_type follow_up_action_type NOT NULL,
    notes TEXT,
    reference TEXT,
    logged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_follow_up_actions_lead ON follow_up_actions(lead_id);
CREATE INDEX idx_follow_up_actions_employee ON follow_up_actions(employee_id);

-- ============================================================================
-- ATTENDANCE
-- ============================================================================

CREATE TABLE attendance_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id UUID NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    work_date DATE NOT NULL,
    check_in_at TIMESTAMPTZ,
    check_out_at TIMESTAMPTZ,
    status attendance_status NOT NULL DEFAULT 'ABSENT',
    check_in_lat NUMERIC(9, 6),
    check_in_lng NUMERIC(9, 6),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (employee_id, work_date)
);

CREATE TRIGGER trg_attendance_updated_at BEFORE UPDATE ON attendance_records
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_attendance_employee_date ON attendance_records(employee_id, work_date);

-- ============================================================================
-- WEEKLY DAY OFF
-- ============================================================================

CREATE TABLE weekly_day_offs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id UUID NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    week_key TEXT NOT NULL, -- ISO week, e.g. '2026-W39'
    day_off_date DATE NOT NULL,
    status day_off_status NOT NULL DEFAULT 'OPEN',
    selected_at TIMESTAMPTZ,
    frozen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (employee_id, week_key)
);

CREATE TRIGGER trg_weekly_day_offs_updated_at BEFORE UPDATE ON weekly_day_offs
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_weekly_day_offs_employee ON weekly_day_offs(employee_id);
CREATE INDEX idx_weekly_day_offs_date ON weekly_day_offs(day_off_date);

-- ============================================================================
-- NOTIFICATIONS / AUDIT
-- ============================================================================

CREATE TABLE notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id UUID NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    event_type notification_event_type NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id UUID,
    message TEXT NOT NULL,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_notifications_employee ON notifications(employee_id, created_at DESC);

CREATE TABLE audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id UUID REFERENCES employees(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id UUID,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_events_entity ON audit_events(entity_type, entity_id);

-- ============================================================================
-- REQ-25 — UNIFIED OPPORTUNITY & ATTRIBUTION ENGINE
-- ============================================================================

CREATE TABLE opportunities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE RESTRICT,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    property_id UUID REFERENCES properties(id) ON DELETE RESTRICT,
    source_owner_type source_owner_type NOT NULL,
    source_owner_employee_id UUID REFERENCES employees(id) ON DELETE SET NULL,
    source_owner_channel_partner_id UUID REFERENCES channel_partners(id) ON DELETE SET NULL,
    handling_employee_id UUID REFERENCES employees(id) ON DELETE SET NULL,
    source opportunity_source NOT NULL,
    status opportunity_status NOT NULL DEFAULT 'ACTIVE',
    attribution_status attribution_status NOT NULL DEFAULT 'VERIFIED',
    locked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_source_owner_reference CHECK (
        (source_owner_type = 'EMPLOYEE' AND source_owner_employee_id IS NOT NULL AND source_owner_channel_partner_id IS NULL)
        OR
        (source_owner_type = 'CHANNEL_PARTNER' AND source_owner_channel_partner_id IS NOT NULL AND source_owner_employee_id IS NULL)
    )
);

CREATE TRIGGER trg_opportunities_updated_at BEFORE UPDATE ON opportunities
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- FROZEN RULE (§16.2): only ONE active Opportunity per Lead + Property/Plot.
-- OP-D03 default assumption: a Lead MAY have simultaneous active Opportunities
-- across *different* plots, so the uniqueness key includes property_id.
CREATE UNIQUE INDEX uq_opportunities_active_lead_property
    ON opportunities(lead_id, property_id) WHERE status = 'ACTIVE' AND property_id IS NOT NULL;

-- Project-only opportunities (no specific plot yet) — one active per lead+project.
CREATE UNIQUE INDEX uq_opportunities_active_lead_project_no_property
    ON opportunities(lead_id, project_id) WHERE status = 'ACTIVE' AND property_id IS NULL;

CREATE INDEX idx_opportunities_lead ON opportunities(lead_id);
CREATE INDEX idx_opportunities_property ON opportunities(property_id);
CREATE INDEX idx_opportunities_status ON opportunities(status);
CREATE INDEX idx_opportunities_handling_employee ON opportunities(handling_employee_id);

-- Claims: immutable evidence trail per §16.7 / §16.11. Never deleted, even losing claims.
CREATE TABLE opportunity_claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id UUID NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    claimant_type source_owner_type NOT NULL,
    claimant_employee_id UUID REFERENCES employees(id) ON DELETE SET NULL,
    claimant_channel_partner_id UUID REFERENCES channel_partners(id) ON DELETE SET NULL,
    claim_type claim_type NOT NULL,
    evidence_site_visit_id UUID REFERENCES site_visits(id) ON DELETE SET NULL,
    evidence_reference TEXT,
    qualifying_at TIMESTAMPTZ NOT NULL,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_claimant_reference CHECK (
        (claimant_type = 'EMPLOYEE' AND claimant_employee_id IS NOT NULL AND claimant_channel_partner_id IS NULL)
        OR
        (claimant_type = 'CHANNEL_PARTNER' AND claimant_channel_partner_id IS NOT NULL AND claimant_employee_id IS NULL)
    )
);

CREATE INDEX idx_opportunity_claims_opportunity ON opportunity_claims(opportunity_id);
CREATE INDEX idx_opportunity_claims_qualifying_at ON opportunity_claims(opportunity_id, qualifying_at);

-- Resolutions: immutable back-office resolution audit (§16.7, AC-OPP-07).
CREATE TABLE opportunity_resolutions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id UUID NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    resolved_source_owner_type source_owner_type NOT NULL,
    resolved_source_owner_employee_id UUID REFERENCES employees(id) ON DELETE SET NULL,
    resolved_source_owner_channel_partner_id UUID REFERENCES channel_partners(id) ON DELETE SET NULL,
    reason TEXT NOT NULL,
    resolved_by TEXT NOT NULL, -- authorized back-office actor identifier (OP-D04: outside employee portal)
    resolved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_opportunity_resolutions_opportunity ON opportunity_resolutions(opportunity_id);

-- ============================================================================
-- DEAL / DEAL LOCK  (§20.6 — Opportunity → Deal, hard lock, no auto-expiry)
-- ============================================================================

CREATE TABLE deals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id UUID NOT NULL REFERENCES opportunities(id) ON DELETE RESTRICT,
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE RESTRICT,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE RESTRICT,
    source_owner_type source_owner_type NOT NULL,
    source_owner_employee_id UUID REFERENCES employees(id) ON DELETE SET NULL,
    source_owner_channel_partner_id UUID REFERENCES channel_partners(id) ON DELETE SET NULL,
    handling_employee_id UUID REFERENCES employees(id) ON DELETE SET NULL,
    status deal_status NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_deals_updated_at BEFORE UPDATE ON deals
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_deals_opportunity ON deals(opportunity_id);
CREATE INDEX idx_deals_property ON deals(property_id);

CREATE TABLE deal_locks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id UUID NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE RESTRICT,
    locked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ,
    status deal_lock_status NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A DEAL_LOCKED plot cannot be selected for another Opportunity/Visit/Deal (§20.6)
CREATE UNIQUE INDEX uq_deal_locks_active_property ON deal_locks(property_id) WHERE status = 'ACTIVE';
CREATE INDEX idx_deal_locks_deal ON deal_locks(deal_id);
