# Lead Detail View - Opportunities API Specification

## Site visit to opportunity display

### Plot or lead held by another employee
`POST /api/v1/site-visits` no longer fails with `PROPERTY_LOCKED` / `LEAD_LOCKED`. The visit is always saved (200), and the response tells you what happened:
- `waitlisted: true`: the plot is held by another employee. The visit and lead are saved and the lead is protected for the visiting employee (follow-ups work), but there is no plot lock and no opportunity (`opportunity: null`). `held_until` is when the current hold ends. The customer joins the plot's waitlist and the holder is notified.
- `lead_held: true`: the lead is locked to another employee. The visit is saved only (no lock, no opportunity); the lead stays with its owner, who is notified.
- Otherwise both flags are `false` and the flow is unchanged (locks taken, `NEW` opportunity created or renewed).
- When the holder's plot is freed (opportunity `LOST` / `RELEASED` / `DEAL_REJECTED` or protection expiry), the oldest waiting employee gets a `PROPERTY_LOCK_RELEASED` notification and takes the plot by logging a new site visit.
- `DEAL_IN_PROGRESS` keeps the opportunity, lead lock and plot lock for 15 days instead of 3. Follow-ups and revisits never shorten a longer hold.

- `POST /api/v1/site-visits` saves the visit, lead/property locks, and opportunity claim in one transaction. A newly created opportunity has status `NEW`.
- Create, list (`GET /api/v1/site-visits`), and detail (`GET /api/v1/site-visits/{id}`) responses now include `opportunity` (the full opportunity object) and `can_update_opportunity` (boolean) on each visit. Existing visits get these fields too, without recreating them.
- The association follows `opportunity_claims.evidence_site_visit_id`. Repeat visits can share an opportunity; an older visit retains its original opportunity even if a later visit creates another one. Project-only visits are supported.
- Display the opportunity card for each visit, including closed/expired records. Enable the status action only when `can_update_opportunity` is true, and submit `opportunity.id` to `POST /api/v1/opportunities/{id}/status`. Refresh the visit list, lead detail, dashboard, and inventory after the action.
- `can_update_opportunity` requires an open, unexpired opportunity owned or handled by the employee. The update API still enforces permissions and transitions. `opportunity: null` means there is no linked opportunity visible to that employee; show a fallback instead of throwing or creating an opportunity during a read.
- After saving, use the returned `data.lead_id` to call `GET /api/v1/leads/{lead_id}` and render `data.opportunities`.
- `GET /api/v1/leads/active-locks` returns lock information, not opportunities. Its `lead_lock_id` is not an opportunity ID.
- For a single opportunity, use `GET /api/v1/opportunities/{opportunity_id}` with an ID from `data.opportunities[].id`. The employee opportunity list is `GET /api/v1/opportunities`.
- Frontend status types, labels, and open-status controls must include `NEW`. Guard unknown status labels before calling string methods; an unfamiliar status should render a fallback with editing disabled.
- The "Opportunities couldn't load" widget message can indicate a React rendering exception even when the API succeeds. The missing `NEW` label previously caused `undefined.toLowerCase()` in the opportunity card.

## Endpoint

```
GET /api/v1/leads/{lead_id}
Authorization: Bearer <access_token>
```

---

## Request

### Parameters
- **lead_id** (path, required, UUID): Unique identifier of the lead

### Example Request
```bash
curl -X GET "http://localhost:8000/api/v1/leads/550e8400-e29b-41d4-a716-446655440000" \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
  -H "Content-Type: application/json"
```

---

## Response

### Success Response (200 OK)

#### Response Structure
```json
{
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "John Doe",
    "normalized_phone": "+919876543210",
    "email": "john@example.com",
    "source": "WEBSITE",
    "lifecycle_status": "QUALIFIED",
    "first_visit_at": "2026-09-20T10:30:00Z",
    "latest_visit_at": "2026-09-24T14:15:00Z",
    "created_at": "2026-09-20T10:30:00Z",
    "updated_at": "2026-09-24T14:15:00Z",
    "opportunities": [
      {
        "id": "660e8400-e29b-41d4-a716-446655440001",
        "project_id": "770e8400-e29b-41d4-a716-446655440002",
        "property_id": "880e8400-e29b-41d4-a716-446655440003",
        "source_owner_type": "EMPLOYEE",
        "source_owner_employee_id": "990e8400-e29b-41d4-a716-446655440004",
        "source_owner_channel_partner_id": null,
        "handling_employee_id": "990e8400-e29b-41d4-a716-446655440004",
        "source": "EMPLOYEE_SITE_VISIT",
        "status": "ACTIVE",
        "attribution_status": "VERIFIED",
        "locked_at": "2026-09-24T14:15:00Z",
        "expires_at": "2026-09-27T14:15:00Z",
        "created_at": "2026-09-24T14:15:00Z",
        "updated_at": "2026-09-24T14:15:00Z"
      },
      {
        "id": "660e8400-e29b-41d4-a716-446655440005",
        "project_id": "770e8400-e29b-41d4-a716-446655440006",
        "property_id": null,
        "source_owner_type": "CHANNEL_PARTNER",
        "source_owner_employee_id": null,
        "source_owner_channel_partner_id": "aa0e8400-e29b-41d4-a716-446655440007",
        "handling_employee_id": "990e8400-e29b-41d4-a716-446655440008",
        "source": "EMPLOYEE_SITE_VISIT",
        "status": "ATTRIBUTION_CONFLICT",
        "attribution_status": "CONFLICT",
        "locked_at": "2026-09-23T08:00:00Z",
        "expires_at": "2026-09-26T08:00:00Z",
        "created_at": "2026-09-23T08:00:00Z",
        "updated_at": "2026-09-23T10:30:00Z"
      }
    ]
  }
}
```

---

## Opportunity Fields Reference

| Field | Type | Nullable | Description |
|-------|------|----------|-------------|
| **id** | UUID string | No | Unique opportunity identifier |
| **project_id** | UUID string | No | Associated project ID |
| **property_id** | UUID string | Yes | Associated property/plot ID (null if project-level opportunity) |
| **source_owner_type** | string enum | No | Owner type: `EMPLOYEE` or `CHANNEL_PARTNER` |
| **source_owner_employee_id** | UUID string | Yes | Employee owner ID (null if owner is channel partner) |
| **source_owner_channel_partner_id** | UUID string | Yes | Channel partner owner ID (null if owner is employee) |
| **handling_employee_id** | UUID string | Yes | Employee currently handling the opportunity |
| **source** | string enum | No | How created: `EMPLOYEE_SITE_VISIT`, `CHANNEL_PARTNER` |
| **status** | string enum | No | Current status: `NEW`, `INTERESTED`, `DEAL_IN_PROGRESS`, `DEAL_REJECTED`, `ACTIVE`, `CONVERTED`, `EXPIRED`, `LOST`, `ATTRIBUTION_CONFLICT`, `RELEASED` |
| **attribution_status** | string enum | No | Attribution status: `VERIFIED`, `CONFLICT`, `RESOLVED` |
| **locked_at** | ISO 8601 datetime | No | When opportunity was created/locked |
| **expires_at** | ISO 8601 datetime | No | When 3-day protection period expires |
| **created_at** | ISO 8601 datetime | No | Opportunity creation timestamp |
| **updated_at** | ISO 8601 datetime | No | Last update timestamp |

---

## Status Values Explained

### `status` field
- **NEW**: Starting status of every opportunity (protected, 3-day expiry)
- **INTERESTED**: Employee marked the lead interested; the lead is now treated as a customer
- **DEAL_IN_PROGRESS**: Customer is on the booking table
- **CONVERTED**: Deal complete (deal created, plot deal-locked)
- **DEAL_REJECTED**: Deal fell through after being in progress
- **LOST**: Lead explicitly marked as lost
- **RELEASED**: Lock was released by the employee
- **EXPIRED**: 3-day protection ended (system only)
- **ATTRIBUTION_CONFLICT**: Multiple sources claimed the same lead/plot (system only, needs back office)
- **ACTIVE**: Legacy starting status of older opportunities; treated like `NEW`

## Update Opportunity Status

```
POST /api/v1/opportunities/{opportunity_id}/status
Authorization: Bearer <access_token>
{ "status": "INTERESTED" | "DEAL_IN_PROGRESS" | "CONVERTED" | "DEAL_REJECTED" | "LOST" | "RELEASED" }
```

| Dropdown label | Send |
|---|---|
| Interested | `INTERESTED` |
| Deal In Progress | `DEAL_IN_PROGRESS` |
| Deal Complete | `CONVERTED` (`DEAL_CLOSED` also accepted) |
| Deal Rejected | `DEAL_REJECTED` |
| Lost | `LOST` |
| Release Lock | `RELEASED` |

Matching ignores case, spaces, hyphens and underscores, so the label text also works.

- Allowed only while the opportunity is open (`NEW`, `INTERESTED`, `DEAL_IN_PROGRESS`, legacy `ACTIVE`) and unexpired, and only for its source owner or handling employee. Show the dropdown only in those statuses.
- Pipeline is forward-only: `NEW -> INTERESTED -> DEAL_IN_PROGRESS`. Going back returns 409 `INVALID_STATUS_TRANSITION`. Re-sending the current status is a no-op.
- `CONVERTED`, `DEAL_REJECTED`, `LOST` and `RELEASED` can be chosen from any open status.
- `INTERESTED`, `DEAL_IN_PROGRESS` and `CONVERTED` set the lead's `lifecycle_status` to `CONVERTED` (lead is a customer).
- `CONVERTED` on an opportunity with a plot creates the deal and deal-locks the plot. Without a plot, only the status changes.
- `DEAL_REJECTED` / `LOST` / `RELEASED` on an opportunity with a plot frees the plot lock (plot returns to `AVAILABLE`).
- Source owner and handling employee are both notified (`entity_type: "OPPORTUNITY"`).
- Returns `{"success": true, "data": <opportunity>, "message": "Opportunity status updated"}`.
- Errors: 401 `UNAUTHORIZED`, 403 `FORBIDDEN`, 404 `NOT_FOUND`, 409 `OPPORTUNITY_NOT_ACTIVE`, `INVALID_STATUS_TRANSITION` or `DEAL_LOCKED`, 422 `VALIDATION_FAILED` (status missing or unrecognised).

## Opportunity Status Lifecycle

`EXPIRED` and `ATTRIBUTION_CONFLICT` are set by the system only. Employees set the other statuses via the endpoint above. The 3-day expiry applies to every open status.

```mermaid
flowchart TD
    A[Employee logs site visit<br/>POST /api/v1/site-visits] --> B{Active opportunity exists<br/>for this lead + plot?}
    B -- No --> C[Create opportunity<br/>status NEW, attribution VERIFIED<br/>expires_at = now + 3 days]
    B -- "Yes, same employee owns it" --> D[Renew expires_at<br/>stays open]
    B -- "Yes, owned by Channel Partner" --> E[Employee becomes handling_employee<br/>owner unchanged, stays open]
    B -- "Yes, different employee owns it" --> F[ATTRIBUTION_CONFLICT<br/>attribution CONFLICT<br/>notification sent]
    C --> G{What happens next?}
    D --> G
    E --> G
    G -- "expires_at passes" --> H[EXPIRED<br/>lock sweeper]
    G -- "Deal created<br/>POST /api/v1/deals" --> I[CONVERTED]
    F -- "Back office<br/>POST /opportunities/id/resolve" --> J[ACTIVE again<br/>attribution RESOLVED]
    J --> G
```

Frontend rules:
- Show `status` and `attribution_status` as badges. Offer the separate status dropdown for open statuses as described above; attribution is read-only.
- `NEW`, `INTERESTED`, `DEAL_IN_PROGRESS`, legacy `ACTIVE`: show countdown to `expires_at`. `ATTRIBUTION_CONFLICT`: show "under review". `CONVERTED`: link to the deal. `EXPIRED`: greyed out.
- Employees change status with `POST /opportunities/{id}/status` (or `POST /api/v1/deals`, which also moves it to `CONVERTED`).

---

## Error Responses

All errors use this envelope: `{"success": false, "error": {"code": "...", "message": "..."}}`

### 404 - Lead Not Found (route is correct, lead id does not exist)
```json
{ "success": false, "error": { "code": "NOT_FOUND", "message": "Lead not found" } }
```

### 404 - Wrong URL (no such route)
```json
{ "success": false, "error": { "code": "HTTP_ERROR", "message": "Not Found" } }
```
If you see this one, the path is wrong. The only lead detail route is `GET /api/v1/leads/{lead_id}`. There is no `/leads/{id}/opportunities`.

### 403 - Not Your Lead
Only the employee the lead is currently assigned to can view it.
```json
{ "success": false, "error": { "code": "FORBIDDEN", "message": "You do not have access to this lead" } }
```

---

## Response Scenarios

### Scenario 1: Lead with No Opportunities
```json
{
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "Jane Smith",
    "normalized_phone": "+919876543211",
    "email": "jane@example.com",
    "source": "CALL",
    "lifecycle_status": "NEW",
    "first_visit_at": null,
    "latest_visit_at": null,
    "created_at": "2026-09-25T10:00:00Z",
    "updated_at": "2026-09-25T10:00:00Z",
    "opportunities": []
  }
}
```

### Scenario 2: Lead with Single Active Opportunity
```json
{
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "John Doe",
    "normalized_phone": "+919876543210",
    "email": "john@example.com",
    "source": "WEBSITE",
    "lifecycle_status": "QUALIFIED",
    "first_visit_at": "2026-09-20T10:30:00Z",
    "latest_visit_at": "2026-09-24T14:15:00Z",
    "created_at": "2026-09-20T10:30:00Z",
    "updated_at": "2026-09-24T14:15:00Z",
    "opportunities": [
      {
        "id": "660e8400-e29b-41d4-a716-446655440001",
        "project_id": "770e8400-e29b-41d4-a716-446655440002",
        "property_id": "880e8400-e29b-41d4-a716-446655440003",
        "source_owner_type": "EMPLOYEE",
        "source_owner_employee_id": "990e8400-e29b-41d4-a716-446655440004",
        "source_owner_channel_partner_id": null,
        "handling_employee_id": "990e8400-e29b-41d4-a716-446655440004",
        "source": "EMPLOYEE_SITE_VISIT",
        "status": "ACTIVE",
        "attribution_status": "VERIFIED",
        "locked_at": "2026-09-24T14:15:00Z",
        "expires_at": "2026-09-27T14:15:00Z",
        "created_at": "2026-09-24T14:15:00Z",
        "updated_at": "2026-09-24T14:15:00Z"
      }
    ]
  }
}
```

### Scenario 3: Lead with Conflicting Opportunities
```json
{
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "John Doe",
    "normalized_phone": "+919876543210",
    "email": "john@example.com",
    "source": "WEBSITE",
    "lifecycle_status": "QUALIFIED",
    "first_visit_at": "2026-09-20T10:30:00Z",
    "latest_visit_at": "2026-09-24T14:15:00Z",
    "created_at": "2026-09-20T10:30:00Z",
    "updated_at": "2026-09-24T14:15:00Z",
    "opportunities": [
      {
        "id": "660e8400-e29b-41d4-a716-446655440001",
        "project_id": "770e8400-e29b-41d4-a716-446655440002",
        "property_id": "880e8400-e29b-41d4-a716-446655440003",
        "source_owner_type": "EMPLOYEE",
        "source_owner_employee_id": "990e8400-e29b-41d4-a716-446655440004",
        "source_owner_channel_partner_id": null,
        "handling_employee_id": "990e8400-e29b-41d4-a716-446655440004",
        "source": "EMPLOYEE_SITE_VISIT",
        "status": "ATTRIBUTION_CONFLICT",
        "attribution_status": "CONFLICT",
        "locked_at": "2026-09-24T14:15:00Z",
        "expires_at": "2026-09-27T14:15:00Z",
        "created_at": "2026-09-24T14:15:00Z",
        "updated_at": "2026-09-24T15:30:00Z"
      },
      {
        "id": "660e8400-e29b-41d4-a716-446655440010",
        "project_id": "770e8400-e29b-41d4-a716-446655440002",
        "property_id": "880e8400-e29b-41d4-a716-446655440003",
        "source_owner_type": "EMPLOYEE",
        "source_owner_employee_id": "bb0e8400-e29b-41d4-a716-446655440005",
        "source_owner_channel_partner_id": null,
        "handling_employee_id": "bb0e8400-e29b-41d4-a716-446655440005",
        "source": "EMPLOYEE_SITE_VISIT",
        "status": "ATTRIBUTION_CONFLICT",
        "attribution_status": "CONFLICT",
        "locked_at": "2026-09-24T14:20:00Z",
        "expires_at": "2026-09-27T14:20:00Z",
        "created_at": "2026-09-24T14:20:00Z",
        "updated_at": "2026-09-24T14:20:00Z"
      }
    ]
  }
}
```

---

## Frontend Integration Example

### JavaScript/TypeScript
```typescript
interface Opportunity {
  id: string;
  project_id: string;
  property_id: string | null;
  source_owner_type: 'EMPLOYEE' | 'CHANNEL_PARTNER';
  source_owner_employee_id: string | null;
  source_owner_channel_partner_id: string | null;
  handling_employee_id: string | null;
  source: string;
  status: 'NEW' | 'INTERESTED' | 'DEAL_IN_PROGRESS' | 'DEAL_REJECTED' | 'ACTIVE' | 'CONVERTED' | 'EXPIRED' | 'LOST' | 'ATTRIBUTION_CONFLICT' | 'RELEASED';
  attribution_status: 'VERIFIED' | 'CONFLICT' | 'RESOLVED';
  locked_at: string;
  expires_at: string;
  created_at: string;
  updated_at: string;
}

interface Lead {
  id: string;
  name: string;
  normalized_phone: string;
  email: string | null;
  source: string;
  lifecycle_status: string;
  first_visit_at: string | null;
  latest_visit_at: string | null;
  created_at: string;
  updated_at: string;
  opportunities: Opportunity[];
}

// Fetch lead with opportunities
async function fetchLeadDetails(leadId: string): Promise<Lead> {
  const response = await fetch(`/api/v1/leads/${leadId}`, {
    headers: { Authorization: `Bearer ${accessToken}` }
  });
  const json = await response.json();
  return json.data;
}

// Usage
const lead = await fetchLeadDetails('550e8400-e29b-41d4-a716-446655440000');

// Access opportunities directly from lead object
lead.opportunities.forEach(opp => {
  console.log(`Opportunity: ${opp.id}`);
  console.log(`Status: ${opp.status}`);
  console.log(`Attribution: ${opp.attribution_status}`);
  console.log(`Expires at: ${opp.expires_at}`);
});
```

### React Example
```jsx
function LeadDetailView({ leadId }) {
  const [lead, setLead] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`/api/v1/leads/${leadId}`, {
      headers: { Authorization: `Bearer ${token}` }
    })
      .then(res => res.json())
      .then(json => {
        setLead(json.data);
        setLoading(false);
      });
  }, [leadId]);

  if (loading) return <div>Loading...</div>;

  return (
    <div>
      <h1>{lead.name}</h1>
      <p>Phone: {lead.normalized_phone}</p>
      
      <h2>Opportunities</h2>
      {lead.opportunities.length === 0 ? (
        <p>No opportunities</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Status</th>
              <th>Attribution</th>
              <th>Property ID</th>
              <th>Expires At</th>
            </tr>
          </thead>
          <tbody>
            {lead.opportunities.map(opp => (
              <tr key={opp.id}>
                <td>{opp.status}</td>
                <td>{opp.attribution_status}</td>
                <td>{opp.property_id || 'Project-level'}</td>
                <td>{new Date(opp.expires_at).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
```

---

## Key Points for Frontend

✅ **Single API Call**: No need for separate opportunity endpoint  
✅ **Complete Data**: All lead + opportunity data in one response  
✅ **Performance**: Search endpoint (list) doesn't load opportunities (lightweight)  
✅ **Detail View**: This endpoint includes opportunities for rich context  
✅ **Empty Opportunities**: Valid scenario - returns empty array `[]`  
✅ **Null Fields**: Some fields can be null (property_id, employee_id) - check before display  

---

## Testing Checklist

- [ ] Lead with no opportunities returns empty array
- [ ] Lead with single opportunity displays correctly
- [ ] Lead with multiple opportunities shows all
- [ ] Conflicting opportunities (ATTRIBUTION_CONFLICT) display properly
- [ ] Null fields (property_id, employee_id) handled gracefully
- [ ] Expiry dates calculated correctly
- [ ] Status badges render correctly (ACTIVE, EXPIRED, etc.)
- [ ] Handle 404 when lead not found
- [ ] Handle 403 when user doesn't have access to lead
