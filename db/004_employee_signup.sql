-- ============================================================================
-- 004 — Self-serve employee signup with email OTP verification
--   * employee_signups: pending (unverified) signups. An employee row is only
--     created once the emailed code is verified; this table never grants login.
--   * Case-insensitive uniqueness for employee email and employee code, so
--     "Priya@X.com" and "priya@x.com" can't become two accounts.
-- Safe to re-run.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS employee_signups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL UNIQUE,                 -- stored lower-cased
    employee_code TEXT NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,                -- bcrypt; plaintext is never stored
    otp_hash TEXT,                              -- HMAC-SHA256 of the code; NULL = no usable code
    otp_expires_at TIMESTAMPTZ,
    otp_failed_attempts SMALLINT NOT NULL DEFAULT 0,
    last_otp_sent_at TIMESTAMPTZ,               -- resend cooldown
    otp_send_window_started_at TIMESTAMPTZ,     -- hourly send quota window
    otp_sends_in_window SMALLINT NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ NOT NULL,            -- whole pending signup is discarded after this
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_employee_signups_email_lower CHECK (email = lower(email))
);

DROP TRIGGER IF EXISTS trg_employee_signups_updated_at ON employee_signups;
CREATE TRIGGER trg_employee_signups_updated_at BEFORE UPDATE ON employee_signups
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX IF NOT EXISTS idx_employee_signups_expires_at ON employee_signups(expires_at);

CREATE UNIQUE INDEX IF NOT EXISTS uq_employees_email_lower ON employees (lower(email));
CREATE UNIQUE INDEX IF NOT EXISTS uq_employees_employee_code_lower ON employees (lower(employee_code));

COMMIT;
