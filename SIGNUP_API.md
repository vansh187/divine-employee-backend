# Employee Signup API — Implemented Contract

The three signup endpoints from the frontend spec are built and match it: same routes, fields,
status codes and error codes. This page lists the confirmed contract and the few additions
the frontend should handle.

**Base URL:** `https://divine-employee-backend.vercel.app/api/v1`
**Auth:** none on all three endpoints (no bearer token).

| Endpoint | Success |
|---|---|
| `POST /auth/signup` | `201` `{ email, expires_at }` |
| `POST /auth/signup/resend-otp` | `200` `{ email, expires_at }` |
| `POST /auth/signup/verify-otp` | `200` `{ access_token, refresh_token, token_type, expires_in_minutes }` |

`expires_at` is when the emailed code stops working, in India time (`…+05:30`). The code is only
ever sent by email, never in a response.

## Answers to the spec's open questions

| Question | Answer |
|---|---|
| Routes and field names | Confirmed exactly as proposed. |
| Email delivery | Resend, from `noreply@divinevisioninfra.com`. |
| Manager approval | Not required. A verified code activates the account immediately. |
| Password storage | bcrypt. Plaintext is never stored or logged. |
| CORS | Same allowed origins as the rest of the API (`employee.divinevisioninfra.com`, http/https, with or without `www.`). |

## Rules

- **Allowed email domains:** `@divinevisioninfra.com`, and `@gmail.com` while testing is in progress
  (it will be removed before launch). Any other address gets `422` with a `fields` entry for
  `body.email` (for example "Only @divinevisioninfra.com, @gmail.com email addresses can sign up").
- **Email and employee ID are case-insensitive.** `Priya@…` and `priya@…` are the same account.
  Login is case-insensitive too.
- **Employee ID format:** letters, digits and `. _ / -`, starting with a letter or digit,
  up to 50 characters (for example `DVI-1042`).
- **Password:** 8 to 72 bytes (bcrypt's limit), and not only spaces.
- **Codes:** 6 digits, valid for 10 minutes. Spaces are ignored (`482 913` works).
- **Wrong codes:** 5 attempts per code. After that the code stops working, even if correct, until a
  new one is requested.
- **Sending limits per email address:** at least 30 seconds between codes, and at most 5 codes per
  hour. This applies to both `signup` (resubmitting the form) and `resend-otp`.
- **Resubmitting the form** replaces the pending signup (name, employee ID, password and code).
- **Pending signups expire after 24 hours.** After that, `resend-otp` and `verify-otp` return `404`
  and the user starts again.

## Errors

All in the standard shape `{ "success": false, "error": { "code", "message", "fields"? } }`.

### `POST /auth/signup`

| Status | Code | When |
|---|---|---|
| `422` | `VALIDATION_FAILED` | Invalid or missing field, or non-company email. Includes `fields`. |
| `409` | `EMAIL_ALREADY_REGISTERED` | An employee already uses this email. |
| `409` | `EMPLOYEE_ID_ALREADY_REGISTERED` | An employee already uses this employee ID. |
| `429` | `RATE_LIMIT_EXCEEDED` | **New:** resubmitted within 30 s, or 5 codes already sent this hour. |
| `503` | `EMAIL_DELIVERY_FAILED` | **New:** the email couldn't be sent. Safe to retry right away. |

### `POST /auth/signup/resend-otp`

| Status | Code | When |
|---|---|---|
| `404` | `NOT_FOUND` | No pending signup (never started, already verified, or older than 24 h). |
| `429` | `RATE_LIMIT_EXCEEDED` | Within 30 s of the last code, or 5 codes already sent this hour. |
| `503` | `EMAIL_DELIVERY_FAILED` | **New:** the email couldn't be sent. Safe to retry right away. |

### `POST /auth/signup/verify-otp`

| Status | Code | When |
|---|---|---|
| `404` | `NOT_FOUND` | No pending signup for this email. |
| `410` | `OTP_EXPIRED` | The code is older than 10 minutes, or was replaced by a resend that failed to send. |
| `422` | `INVALID_OTP` | Wrong code. The message says how many attempts are left. |
| `429` | `RATE_LIMIT_EXCEEDED` | **New:** 5 wrong attempts used up. Show "Request a new code". |
| `409` | `EMPLOYEE_ID_ALREADY_REGISTERED` | **New, rare:** someone else verified the same employee ID first. Send the user back to the form. |

### Any endpoint

| Status | Code | When |
|---|---|---|
| `503` | `SERVICE_UNAVAILABLE` | Temporary database problem. Retry after a few seconds. |
| `429` | `RATE_LIMIT_EXCEEDED` | More than 15 requests per minute from one network to one endpoint. |

If `verify-otp` returns `503 SERVICE_UNAVAILABLE` with the message
*"Your account was created. Please log in."*, the account exists and the user can log in normally.

## Suggested messages

| Code | Message |
|---|---|
| `EMAIL_DELIVERY_FAILED` | "We couldn't send the code. Please try again." |
| `RATE_LIMIT_EXCEEDED` (signup / resend) | "Please wait a moment before requesting another code." |
| `RATE_LIMIT_EXCEEDED` (verify) | "Too many incorrect codes. Request a new code." |
| `OTP_EXPIRED` | "This code has expired. Request a new one." |
| `INVALID_OTP` | Show the API's message (it includes attempts left). |
