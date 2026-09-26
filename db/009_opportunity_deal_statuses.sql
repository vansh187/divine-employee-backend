-- Deal-stage opportunity statuses. Run OUTSIDE an explicit transaction (ADD VALUE must commit before use).
ALTER TYPE opportunity_status ADD VALUE IF NOT EXISTS 'DEAL_IN_PROGRESS';
ALTER TYPE opportunity_status ADD VALUE IF NOT EXISTS 'DEAL_REJECTED';
