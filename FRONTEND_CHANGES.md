# Employee Portal API — Frontend Changes

What the frontend needs to change to work with the deployed backend. The full endpoint reference
is in `API_INTEGRATION.md`; this document only covers what's new or different.

**Base URL:** `https://divine-employee-backend.vercel.app/api/v1`
**API docs (try endpoints in the browser):** https://divine-employee-backend.vercel.app/docs

| # | Change | Priority |
|---|---|---|
| 1 | Point the app at the deployed API, from the allowed domain | Required |
| 2 | Refresh the token once, not in parallel | Required |
| 3 | Send the "Log a Site Visit" form in the expected shape | Required |
| 4 | Show email validation errors on the form | Required |
| 5 | Handle new `403 FORBIDDEN` responses | Required |
| 6 | Handle new `409` codes when logging a site visit | Required |
| 7 | Handle an expired opportunity when creating a deal | Required |
| 8 | Show visitor details as entered on each visit | Recommended |
| 9 | Treat plot status as a hint, not a hard block | Recommended |

All errors keep the existing shape:

```json
{ "success": false, "error": { "code": "SOME_CODE", "message": "Human-readable text" } }
```

Validation errors (`422`, code `VALIDATION_FAILED`) also include
`"fields": [{ "field": "body.email", "message": "..." }]`.

---

## 1. Point the app at the deployed API

Set the API base URL to `https://divine-employee-backend.vercel.app/api/v1`.

Browser requests are only accepted from:

- `https://employee.divinevisioninfra.com`
- `http://employee.divinevisioninfra.com`
- `https://www.employee.divinevisioninfra.com`
- `http://www.employee.divinevisioninfra.com`

Requests from `localhost` or any other origin are blocked by CORS. The browser shows a CORS error,
and the request never reaches the API. If you need another origin allowed (a staging URL, or
`localhost` for a short while), ask the backend team to add it; it's a configuration change, not
a code change.

The first request after a quiet period can take 1–3 seconds while the server starts. The requests
after it are fast. Avoid very short request timeouts (keep them at 10 seconds or more).

## 2. Refresh the token once, not in parallel

Each refresh token can now be used **exactly once**. `POST /auth/refresh` returns a new access
token and a new refresh token, and the old refresh token stops working immediately.

If several requests get `401` at the same moment and each one calls `/auth/refresh` with the same
refresh token, only the first succeeds. The others get `401 UNAUTHORIZED`, and if your interceptor
logs the user out on a failed refresh, the user is signed out for no reason.

Make sure only one refresh runs at a time, and have every other request wait for it:

```ts
let refreshInFlight: Promise<string> | null = null;

async function getFreshAccessToken(): Promise<string> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      const res = await fetch(`${BASE_URL}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: getStoredRefreshToken() }),
      });
      if (!res.ok) throw new Error("refresh failed");
      const { data } = await res.json();
      storeTokens(data.access_token, data.refresh_token); // store BOTH — the old refresh token is now dead
      return data.access_token;
    })().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

// In your 401 handler: await getFreshAccessToken(), then retry the original request once.
// Only log the user out if getFreshAccessToken() itself fails.
```

Always save the **new** refresh token from each refresh response.

## 3. Send the "Log a Site Visit" form in the expected shape

`POST /site-visits` with a bearer token.

| Form input | Request field | What to send |
|---|---|---|
| Logging as | — | Nothing. The employee comes from the bearer token. |
| Visitor name | `visitor_name` | Required. |
| Phone | `phone` | Required. Any common format: `+91 98xxx xxxxx`, `98xxxxxxxx`. |
| Email (optional) | `email` | A valid email, or `null` / `""`. |
| Project / Site | `project_id` | Required. The `id` from `GET /properties/projects`. |
| Unit / Plot (optional) | `property_id` | The `id` from `GET /properties/projects/{project_id}/plots`. "No specific plot" → `null` or `""`. |
| Visit date + Visit time | `visit_at` | **One combined value** with the India offset: `"2026-09-23T23:41:00+05:30"`. |
| Outcome | `outcome` | Exactly one of the values below, or `null` / `""`. |
| Notes | `notes` | Optional, up to 5000 characters. |
| (hidden) | `idempotency_key` | A new UUID each time the form is opened. |

Outcome values (the option `value`s must match exactly; the labels can be anything):

| Value | Suggested label |
|---|---|
| `INTERESTED` | Interested |
| `NOT_INTERESTED` | Not interested |
| `FOLLOW_UP_REQUIRED` | Follow-up required |
| `PROPOSAL_REQUESTED` | Proposal requested |
| `NO_SHOW` | No show |
| `OTHER` | Other |

Any other outcome value is rejected with `422`.

Example request:

```json
{
  "visitor_name": "Priya Mehta",
  "phone": "+91 98555 00001",
  "email": "priya@example.com",
  "project_id": "3f2c…",
  "property_id": null,
  "visit_at": "2026-09-23T23:41:00+05:30",
  "outcome": "INTERESTED",
  "notes": "Liked the corner plot; wants pricing.",
  "idempotency_key": "b7e1c9d2-…"
}
```

Building `visit_at` from the date and time inputs:

```ts
// dateInput "2026-09-23" (from <input type="date">), timeInput "23:41" (from <input type="time">)
const visit_at = `${dateInput}T${timeInput}:00+05:30`;
```

If the offset is left off, the API treats the time as India time, so `"2026-09-23T23:41"` also
works. Don't convert it to UTC yourself: `new Date(...).toISOString()` on a device set to another
timezone would shift the visit.

**Idempotency key:** generate it once when the form opens (`crypto.randomUUID()`) and send the same
key if the user retries a failed or timed-out submit. The API then returns the original visit
instead of creating a duplicate. Generate a new key for the next visit.

Untouched optional inputs can be sent as `""`; the API treats them as "not provided".

## 4. Show email validation errors on the form

An invalid email now returns `422 VALIDATION_FAILED`, with a `fields` entry for `body.email`:

```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "Request validation failed",
    "fields": [{ "field": "body.email", "message": "value is not a valid email address: ..." }]
  }
}
```

Show it on the Email field (for example *"Enter a valid email address"*) rather than as a generic
error. The same `fields` list is used for any other invalid input.

## 5. Handle new `403 FORBIDDEN` responses

Employees can now only read or act on their own records. These endpoints return
`403 FORBIDDEN` when the record belongs to someone else:

| Endpoint | Forbidden when |
|---|---|
| `GET /site-visits/{id}` | The visit was logged by another employee. |
| `GET /opportunities/{id}` | The employee isn't the opportunity's owner or handling employee. |
| `GET /opportunities/{id}/claims` | Same as above. |
| `POST /deals` | Same as above, for the opportunity being converted. |

Show *"You don't have access to this record"* and go back to the list. Only link to records that
came from the employee's own lists (`GET /site-visits`, `GET /opportunities`), and this won't
happen in normal use.

`GET /site-visits/{id}` with an id that doesn't exist now returns `404 NOT_FOUND` instead of `500`.

## 6. Handle new `409` codes when logging a site visit

Existing codes (`DAY_OFF_CONFLICT`, `LEAD_LOCKED`, `PROPERTY_LOCKED`) are unchanged. Two new ones:

| Code | When | What to show |
|---|---|---|
| `OPPORTUNITY_CONFLICT` | Rare: another claim on the same customer and plot was saved at the same moment. | *"This customer and plot were just claimed by someone else."* |
| `CONCURRENT_UPDATE` | Two submissions collided. | *"Please try again."* Retry with the **same** `idempotency_key`. |

## 7. Handle an expired opportunity when creating a deal

Opportunities stay active for 3 days after the last site visit. Creating a deal from one that has
expired returns `409 OPPORTUNITY_NOT_ACTIVE`, even if the list still showed it as `ACTIVE` a moment
ago:

```json
{ "success": false, "error": { "code": "OPPORTUNITY_NOT_ACTIVE", "message": "Only an ACTIVE, unexpired opportunity can be converted to a deal" } }
```

Show *"This opportunity has expired. Log a new site visit to reopen it."* A new site visit for
the same customer and plot creates a fresh `ACTIVE` opportunity. The expired one stays in the list
as history.

Hide or disable **Create deal** when an opportunity's `expires_at` is in the past.

## 8. Show visitor details as entered on each visit

Site visit responses (`POST /site-visits`, `GET /site-visits`, `GET /site-visits/{id}`) now include
exactly what was typed on that visit's form:

```json
{
  "visitor_name": "Priya M.",
  "visitor_phone": "+91-98555-00002",
  "visitor_email": "priya.m@example.com",
  "lead_name": "Priya Mehta",
  "...": "other fields unchanged"
}
```

- Use `visitor_name`, `visitor_phone` and `visitor_email` in visit history and visit details.
- `lead_name` is the customer's master name, set on their first visit. For a returning customer it
  can differ from this visit's `visitor_name`.

Visits logged before this change have these fields filled in from the customer's record.

## 9. Treat plot status as a hint, not a hard block

Plot statuses (`AVAILABLE`, `LOCKED`, `DEAL_LOCKED`, `SOLD`) from `GET /properties/inventory` and
`GET /properties/projects/{project_id}/plots` are refreshed every few minutes. A plot shown as
`LOCKED` may already be free, because locks expire after 3 days.

In the Plot dropdown:

- Hide `DEAL_LOCKED` and `SOLD` plots.
- Show `LOCKED` plots, but mark them (for example *"(reserved)"*) instead of hiding them.

When the employee submits, the API makes the final decision. If the plot really is taken it
returns `409 PROPERTY_LOCKED`; show that message.

---

## Checklist

- [ ] Base URL set to `https://divine-employee-backend.vercel.app/api/v1`
- [ ] App served from `employee.divinevisioninfra.com`
- [ ] Token refresh runs once at a time; the new refresh token is saved each time
- [ ] `visit_at` sent as one value with `+05:30`
- [ ] Outcome option values match the enum exactly
- [ ] New `idempotency_key` per form open, reused on retry
- [ ] Email `422` shown on the Email field
- [ ] `403 FORBIDDEN` handled on visit, opportunity and deal screens
- [ ] `OPPORTUNITY_CONFLICT` and `CONCURRENT_UPDATE` handled on site visit submit
- [ ] Expired opportunities: Create deal disabled, and `OPPORTUNITY_NOT_ACTIVE` handled
- [ ] Visit history shows `visitor_name` / `visitor_phone` / `visitor_email`
- [ ] `LOCKED` plots shown as reserved, not hidden
