-- ============================================================================
-- 012 — Forgot password with an emailed OTP
--   * password_reset_requests: one row per (lower-cased) email that asked for a
--     reset. A row is kept even for emails with no ACTIVE employee
--     (employee_id NULL, no code), so throttling and wrong-code responses look
--     the same whether or not the account exists.
--   * Only hashes are stored: the OTP as an HMAC bound to the email, the reset
--     token as SHA-256.
-- Safe to re-run.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS password_reset_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL UNIQUE,                 -- stored lower-cased
    employee_id UUID REFERENCES employees(id) ON DELETE CASCADE,  -- NULL = no ACTIVE employee
    otp_hash TEXT,                              -- NULL = no usable code
    otp_expires_at TIMESTAMPTZ,
    otp_attempts SMALLINT NOT NULL DEFAULT 0,
    otp_used_at TIMESTAMPTZ,
    reset_token_hash TEXT,
    reset_token_expires_at TIMESTAMPTZ,
    reset_token_used_at TIMESTAMPTZ,
    last_otp_sent_at TIMESTAMPTZ,               -- resend cooldown
    otp_send_window_started_at TIMESTAMPTZ,     -- hourly request quota window
    otp_sends_in_window SMALLINT NOT NULL DEFAULT 0,
    request_ip TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_password_reset_requests_email_lower CHECK (email = lower(email))
);

DROP TRIGGER IF EXISTS trg_password_reset_requests_updated_at ON password_reset_requests;
CREATE TRIGGER trg_password_reset_requests_updated_at BEFORE UPDATE ON password_reset_requests
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE UNIQUE INDEX IF NOT EXISTS uq_password_reset_requests_token_hash
    ON password_reset_requests (reset_token_hash) WHERE reset_token_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_password_reset_requests_updated_at ON password_reset_requests (updated_at);

COMMIT;
