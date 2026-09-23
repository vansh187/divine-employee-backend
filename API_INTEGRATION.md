# Divine Vision — Employee Portal API Integration Guide

Written for the frontend Employee Portal team. Covers everything needed to wire the
React app (currently pointed at mock data on `localhost:5175`) to this backend.

- **Base URL (dev):** `http://localhost:8000/api/v1` (adjust host/port to however the backend is run)
- **Format:** JSON in, JSON out. All responses share one of two envelopes (see below).
- **Auth:** JWT bearer tokens.
- **Versioning:** all routes are prefixed `/api/v1`; a breaking change ships as `/api/v2` alongside it.
- **Rate limit:** 15 requests / 60 seconds per (caller, route). Exceeding it returns `429` with code `RATE_LIMIT_EXCEEDED`.

---

## 1. Response envelopes

Every endpoint returns one of these two shapes. Never assume a bare object/array at the top level.

**Single object / action result:**
```json
{ "success": true, "data": { ... }, "message": "optional human string or null" }
```

**Paginated list:**
```json
{
  "success": true,
  "data": [ { ... }, { ... } ],
  "pagination": { "page": 1, "page_size": 20, "total_items": 42, "total_pages": 3 }
}
```

**Error (any 4xx/5xx):**
```json
{ "success": false, "error": { "code": "LEAD_LOCKED", "message": "This lead is currently locked to another employee" } }
```

Validation errors (`422`) additionally include a `fields` array:
```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "Request validation failed",
    "fields": [{ "field": "body.phone", "message": "Field required" }]
  }
}
```

**Always branch UI logic on `error.code`, never on `error.message`** (message text may change; code is the stable contract).

### Error codes you will see

| Code | HTTP | Meaning |
|---|---|---|
| `VALIDATION_FAILED` | 422 | Bad request shape/fields |
| `UNAUTHORIZED` | 401 | Missing/invalid/expired token, or bad login |
| `FORBIDDEN` | 403 | Authenticated but not allowed to do this |
| `NOT_FOUND` | 404 | Entity doesn't exist |
| `DAY_OFF_CONFLICT` | 409 | Action blocked by a frozen Day Off on that date |
| `LEAD_LOCKED` | 409 | Lead is locked to another employee |
| `PROPERTY_LOCKED` | 409 | Plot is locked to another employee |
| `DAY_OFF_ALLOWANCE_EXCEEDED` | 409 | Weekly Day-Off already used |
| `DAY_OFF_VISIT_EXISTS` | 409 | Can't freeze a Day Off on a date with an existing visit |
| `OPPORTUNITY_NOT_ACTIVE` | 409 | Tried to convert a non-ACTIVE opportunity into a Deal |
| `DEAL_LOCKED` | 409 | Plot already under a confirmed Deal Lock |
| `DEAL_NOT_CONFIRMED` | 409 | Tried to complete/cancel a deal that isn't currently CONFIRMED (already completed/cancelled) |
| `ALREADY_CHECKED_IN` / `ALREADY_CHECKED_OUT` | 409 | Duplicate attendance action |
| `RATE_LIMIT_EXCEEDED` | 429 | Too many requests |
| `INTERNAL_ERROR` | 500 | Unexpected server error (sanitized — no stack traces ever reach the client) |

---

## 2. Auth flow

```
POST /auth/login        { email, password }              -> access_token, refresh_token, expires_in_minutes
POST /auth/refresh       { refresh_token }                -> new access_token + refresh_token (old refresh token is revoked)
POST /auth/logout        { refresh_token }                -> revokes it
GET  /auth/me            (bearer)                         -> current employee profile
```

- Send `Authorization: Bearer <access_token>` on every other endpoint.
- Access tokens expire in `expires_in_minutes` (default 30). Refresh **before** expiry, or catch a `401 UNAUTHORIZED` and refresh reactively, then retry the original request once.
- Refresh tokens rotate on every use — the old one is immediately invalidated. Always persist the **latest** refresh token you received; don't reuse a stale one (replay returns `401`).
- There is no employee self-registration endpoint in V1 — accounts are provisioned directly in the database.

```json
// POST /auth/login response
{
  "success": true,
  "data": {
    "access_token": "eyJ...",
    "refresh_token": "eyJ...",
    "token_type": "bearer",
    "expires_in_minutes": 30
  }
}
```

```json
// GET /auth/me response
{
  "id": "uuid", "employee_code": "E-101", "name": "Rohan Kaushik",
  "email": "rohan.kaushik@example.com", "phone": "9876543210",
  "team": "Sales", "designation": "Site Executive", "status": "ACTIVE",
  "weekly_day_off_allowance": 1, "business_timezone": "Asia/Kolkata",
  "created_at": "...", "updated_at": "..."
}
```

`employee_id` is **never** sent by the client on any write endpoint — it's derived from the token server-side. Don't add it to request bodies even if you have it client-side.

---

## 3. Dashboard

```
GET /dashboard   (bearer)
```

One call gives you everything the dashboard screen needs:

```json
{
  "employee_name": "Rohan Kaushik",
  "visits_today_count": 1,
  "active_locks_count": 3,
  "conversions_this_week_count": 0,
  "next_day_off": { "day_off_date": "2026-09-23", "status": "FROZEN" },
  "today_attendance": { "check_in_at": "...", "check_out_at": null, "status": "PRESENT" },
  "locked_to_you": [
    { "lead_lock_id": "uuid", "lead_id": "uuid", "lead_name": "Neha & Sameer Kapoor",
      "expires_at": "...", "property_id": "uuid", "plot_no": "42", "project_name": "Suraksha Enclave" }
  ],
  "recent_visits": [
    { "id": "uuid", "visit_at": "...", "outcome": "INTERESTED", "lead_name": "...", "project_name": "..." }
  ]
}
```

`next_day_off.status` will be `null` if nothing is selected yet this week; otherwise `SELECTED`, `FROZEN`, or `COMPLETED` (frozen + date already passed).

---

## 4. Site Visits (creates/updates a Lead automatically)

```
POST /site-visits   (bearer)
GET  /site-visits?page=1&page_size=20   (bearer)
GET  /site-visits/{site_visit_id}   (bearer)
```

**Create request:**
```json
{
  "visitor_name": "Rahul Sharma",
  "phone": "9876543210",
  "email": "optional@example.com",
  "project_id": "uuid",
  "property_id": "uuid or omit/null for a project-level visit",
  "visit_at": "2026-09-20T11:00:00+05:30",
  "notes": "optional",
  "attachments": ["optional", "array of URLs/refs"],
  "outcome": "INTERESTED | NOT_INTERESTED | FOLLOW_UP_REQUIRED | PROPOSAL_REQUESTED | NO_SHOW | OTHER",
  "idempotency_key": "optional client-generated string — safe to always send"
}
```

**Always send `idempotency_key`** (e.g. a UUID generated client-side per form submission). If the request is retried (flaky network, double-tap), the same key returns the original visit instead of creating a duplicate — critical for the "Log Visit" button on mobile networks.

Phone numbers are normalized server-side (10-digit Indian mobile, with/without `+91`/leading `0` all converge to the same Lead) — don't pre-format beyond basic digit entry.

**Possible errors on create:**
- `DAY_OFF_CONFLICT` — the visit date is the employee's frozen Day Off. Show: *"You can't log a visit on your Day Off."*
- `LEAD_LOCKED` — another employee already owns this lead (matched by phone). Show: *"This lead is currently being handled by another team member."*
- `PROPERTY_LOCKED` — the selected plot is locked to another employee's lead. Show: *"This plot is currently reserved by another team member."*
- `NOT_FOUND` — bad `project_id`/`property_id`.
- `VALIDATION_FAILED` — bad phone format, missing required field, `property_id` not part of `project_id`, etc.

A successful visit automatically: creates or reuses the Lead (matched by phone), locks the Lead to you for 3 days, locks the plot (if given) for 3 days, and pushes a notification.

---

## 5. Leads

```
GET  /leads?q=<search>&page=1&page_size=20   (bearer)   -- your own leads only
GET  /leads/active-locks   (bearer)                     -- "Locked to you" widget data
GET  /leads/{lead_id}   (bearer)
POST /leads/{lead_id}/follow-ups   (bearer)              -- log a call/next-visit/proposal/note
GET  /leads/{lead_id}/follow-ups   (bearer)
```

**Follow-up request:**
```json
{ "action_type": "CALL_LOGGED | NEXT_VISIT_SCHEDULED | PROPOSAL_SENT | NOTE", "notes": "optional", "reference": "optional" }
```

Only `CALL_LOGGED`, `NEXT_VISIT_SCHEDULED`, and `PROPOSAL_SENT` renew the 3-day lock (a plain `NOTE` does not — surface that distinction in the UI, e.g. a tooltip: *"Only calls, scheduled visits, and proposals extend your hold on this lead."*).

You can only log a follow-up on a lead you currently hold an active lock on — otherwise `FORBIDDEN`. `GET /leads/{lead_id}` and its follow-ups are also scoped: you can only view a lead currently assigned to you (`FORBIDDEN` otherwise) — don't build a "browse all leads" screen against this endpoint.

---

## 6. Properties / Plot Inventory

```
GET /properties/projects   (bearer)
GET /properties/projects/{project_id}/plots   (bearer)
GET /properties/inventory?project_id=&status=&page=1&page_size=50   (bearer)
```

Use `/properties/projects` + `/properties/projects/{id}/plots` to populate the "Log a Visit" form's Project/Plot dropdowns. Use `/properties/inventory` for the standalone "View Inventory" screen (supports filtering by `status`: `AVAILABLE | LOCKED | DEAL_LOCKED | SOLD`).

A plot's `status` reflects the lock engine automatically — `LOCKED` means an employee has an active 3-day hold, `DEAL_LOCKED` means a confirmed deal exists (hard lock, no countdown), `SOLD` is terminal.

---

## 7. Attendance

```
POST /attendance/check-in   { "latitude": optional, "longitude": optional }   (bearer)
POST /attendance/check-out   (bearer)
GET  /attendance/today   (bearer)
GET  /attendance/history?page=1&page_size=30   (bearer)
GET  /attendance/calendar?year=2026&month=9   (bearer)
```

Server timestamp is authoritative — the client never sends check-in/out times. `status` is computed server-side: `PRESENT`/`LATE` at check-in (cutoff 9:30am business time), refined to `HALF_DAY` or `ON_SITE_VISIT` at check-out. Check-in on a frozen Day Off returns `DAY_OFF_CONFLICT`.

---

## 8. Weekly Day Off

```
GET  /day-off/current-week   (bearer)
POST /day-off/select   { "day_off_date": "2026-09-23" }   (bearer)
GET  /day-off/history?page=1&page_size=20   (bearer)
```

Selection **freezes immediately** on confirmation — there is no separate "select then confirm later" step in this API. Warn the user before they submit (e.g. a confirm dialog: *"Freezing this date can't be undone from here."*). Statuses: `OPEN` (nothing selected — `current-week` returns `null` data), `FROZEN`, `COMPLETED` (frozen + date has passed).

Errors: `DAY_OFF_VISIT_EXISTS` (a visit is already logged/scheduled that date), `DAY_OFF_ALLOWANCE_EXCEEDED` (already froze a different date this week).

---

## 9. Notifications

```
GET  /notifications?page=1&page_size=20   (bearer)
POST /notifications/{notification_id}/read   (bearer)
```

`event_type` values: `SITE_VISIT_LOGGED`, `LEAD_LOCKED`, `LEAD_LOCK_EXPIRING`, `LEAD_LOCK_RELEASED`, `PROPERTY_LOCKED`, `PROPERTY_LOCK_RELEASED`, `DAY_OFF_FROZEN`, `DAY_OFF_COMPLETED`, `OPPORTUNITY_CREATED`, `OPPORTUNITY_CONFLICT`, `OPPORTUNITY_RESOLVED`, `DEAL_LOCKED`.

---

## 10. Opportunities & Deals (REQ-25 — read-mostly for this portal)

These exist because the backend also enforces the cross-channel (Employee vs. Channel Partner) attribution engine behind the scenes on every site visit. The Employee Portal itself only needs **read** access:

```
GET /opportunities?page=1&page_size=20   (bearer)          -- yours (owner or handling employee)
GET /opportunities/{opportunity_id}   (bearer)
GET /opportunities/{opportunity_id}/claims   (bearer)
POST /deals   { "opportunity_id": "uuid" }   (bearer)       -- convert an ACTIVE opportunity to a confirmed deal
GET  /deals?page=1&page_size=20   (bearer)
GET  /deals/{deal_id}   (bearer)
POST /deals/{deal_id}/complete   (bearer)                   -- marks plot SOLD
POST /deals/{deal_id}/cancel   (bearer)                      -- releases the deal lock
```

`status` on an opportunity can be `ACTIVE`, `ATTRIBUTION_CONFLICT`, `CONVERTED`, `LOST`, `EXPIRED`, `RELEASED`. If you surface opportunities in the UI at all, treat `ATTRIBUTION_CONFLICT` as read-only/informational — resolving it is a back-office action outside this portal, not something the employee app should offer a button for.

Deals are scoped to whoever is the source owner or handling employee — `GET/POST /deals/{deal_id}/*` returns `FORBIDDEN` for anyone else. `complete`/`cancel` also enforce the state machine: both only succeed from `CONFIRMED`; retrying either on an already-`COMPLETED`/`CANCELLED` deal returns `DEAL_NOT_CONFIRMED` rather than silently re-applying (so don't treat a double-tap on "Complete Deal" as harmless — check the response).

---

## 11. Things that will bite you if skipped

1. **Always check `success`** before touching `data` — don't assume 2xx means `success: true` is redundant; it always is, but a non-2xx still returns valid JSON with the error envelope, so `response.json().error.code` is always safe to read.
2. **Idempotency key on every Site Visit POST.** This is the #1 place double-submits happen (slow networks, eager double-taps).
3. **Don't client-side validate lock/day-off conflicts and skip the server round-trip** — the UI can pre-warn (e.g. grey out a `LOCKED` plot from `/properties/inventory`), but the create call can still legitimately race and return `409`; handle it gracefully rather than treating it as a bug.
4. **Refresh token rotates** — if you cache it in `localStorage`, overwrite it on every refresh response, and drop it entirely on any `401` from `/auth/refresh` (force re-login).
5. **Rate limit is per bearer token, 15 req/60s per route** — don't poll `/dashboard` or `/notifications` faster than that; a 429 mid-session should back off, not retry immediately.
6. **Times are ISO 8601 with offset** (business timezone `Asia/Kolkata`, i.e. `+05:30`). Send `visit_at` with an explicit offset; don't send naive local time.

---

## 12. Still open (do not build UI that assumes an answer)

The Opportunity Engine (§10 above) has five business rules Divine Vision hasn't signed off on yet (verified-qualifying-event priority, global vs. per-lead plot locking, multi-plot leads, conflict-resolution process, conflict blocking scope). The backend has shipped with documented defaults so the API is usable today, but if the client changes any of these, `status`/`attribution_status` semantics on `/opportunities/*` could shift. Keep any opportunity-facing UI minimal/read-only until that's confirmed.
