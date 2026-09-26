-- Opportunity lifecycle: a new opportunity starts as NEW; an employee can mark it INTERESTED.
-- ADD VALUE must be committed before the values are used, so the indexes are rebuilt in 008.
-- Run this file OUTSIDE an explicit transaction.
ALTER TYPE opportunity_status ADD VALUE IF NOT EXISTS 'NEW';
ALTER TYPE opportunity_status ADD VALUE IF NOT EXISTS 'INTERESTED';
