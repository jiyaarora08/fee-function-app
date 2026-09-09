# Fee Management System Using Azure

Serverless fee-management backend built with **Azure Functions (Python 3.12)**, **Azure SQL Database**, **Azure API Management**, and **Microsoft Entra ID**. The system exposes APIs for student fee status, secure administrator updates, and automated overdue-payment reminders.

**Assessment:** Fusion Practices — AI Product Engineer, Round 3 Technical Assessment  
**Deliverable:** Code repository for a system deployed and tested in Azure

---

## Overview

| Capability | Implementation |
| --- | --- |
| View fee details & payment status | HTTP GET via Azure Functions + API Management |
| Update fee records | HTTP POST — see [Authentication and authorization](#authentication-and-authorization) |
| Overdue reminders | Azure Functions Timer Trigger + Gmail SMTP |
| Data store | Azure SQL Database |
| Monitoring | Application Insights |
| Retry | Host-level fixed-delay retry in `host.json` |

Payment status is **computed in the Function**, not stored as a SQL column.

---

## Architecture

```
Student / API client
  → Azure API Management (subscription key, 10 calls / 60 s)
    → Azure Function get_fee_status
      → Azure SQL Database

Administrator
  → Easy Auth (see Authentication and authorization)
    → Azure Function update_fee
      → Azure SQL Database

Timer Trigger (daily 08:00 UTC)
  → Azure Function send_fee_reminders
    → Azure SQL Database
      → Gmail SMTP → student email

Application Insights ← Function App
```

### Design choices

| Decision | Rationale |
| --- | --- |
| **Azure Functions** | Single Python app for HTTP APIs and scheduled reminders; Flex Consumption scales with demand; integrates with Easy Auth, APIM, and Application Insights |
| **Azure SQL** | Structured fee records, parameterized queries, and indexing for overdue lookups |
| **pymssql** | Reliable TDS client on Linux Flex Consumption (port 1433, TDS 7.4) |
| **Admin route `/manage/...`** | Azure Functions reserves `/admin/` for host administration; `/manage/students/{studentId}/update` avoids that collision |
| **Timer Trigger for reminders** | See [Automated reminders](#automated-reminders) |

---

## Azure resources

| Resource | Detail |
| --- | --- |
| Resource group | `fee-management-rg` (Central India) |
| SQL Server / DB | `fee-management-server-jiya` / `free-sql-db-4359734` |
| Function App | `fee-mgmt-func-jiya` — Linux, Python 3.12, Flex Consumption |
| API Management | `fee-mgmt-apim-jiya` |
| Identity | Microsoft Entra ID — see [Authentication and authorization](#authentication-and-authorization) |
| Monitoring | Application Insights |

---

## Project structure

```
fee-function-app/
├── function_app.py                 # HTTP + timer functions
├── host.json                       # retry + App Insights logging
├── requirements.txt                # azure-functions, pymssql
├── local.settings.example.json     # env template (no secrets)
├── database/database_setup.sql     # schema, index, sample data
├── architecture.md                 # architecture notes + embedded diagram
├── architecture_diagram.png        # architecture diagram (tracked file)
├── README.md
└── .gitignore
```

Use `local.settings.example.json` as a template for local `local.settings.json` (gitignored; never commit secrets).

---

## Database

Schema and seed data: `database/database_setup.sql` (non-destructive; suitable for a fresh database).

### Students

| Column | Type | Notes |
| --- | --- | --- |
| StudentID | INT PRIMARY KEY | Route parameter `{studentId}` |
| Name | NVARCHAR | |
| Course | NVARCHAR | |
| Email | NVARCHAR | Used by reminders |
| TotalFee | DECIMAL(12,2) | |
| PaidAmount | DECIMAL(12,2) DEFAULT 0 | Updated by admin API |
| DueDate | DATE | Indexed (`idx_duedate`) |

### Administrators

| Column | Type |
| --- | --- |
| AdminID | INT PRIMARY KEY |
| Name | NVARCHAR |
| Role | NVARCHAR |

Authorization for updates uses Entra ID role **`FeeAdmin`**, not the `Administrators` table. The table is included to match the assignment schema. The script seeds **20 students** and **2 administrators** (synthetic emails only).

---

## API endpoints

Default Functions route prefix: `/api`.

| Function | Method | Route | Auth |
| --- | --- | --- | --- |
| `get_fee_status` | GET | `/api/students/{studentId}/status` | Function key; APIM subscription key when called via APIM |
| `update_fee` | POST | `/api/manage/students/{studentId}/update` | Easy Auth + `FeeAdmin` (function trigger is `ANONYMOUS` so Easy Auth can inject the principal) |
| `send_fee_reminders` | — | Timer `0 0 8 * * *` (08:00 UTC daily) | Not HTTP |

### GET — payment status

```json
{
  "studentId": 1,
  "name": "Aarav Mehta",
  "course": "B.Tech CSE",
  "totalFee": 120000.0,
  "paidAmount": 700.0,
  "dueDate": "2026-08-08",
  "status": "Overdue"
}
```

| Status | Condition |
| --- | --- |
| 200 | Student found |
| 404 | Student not found |
| 500 | Unexpected error (details in Application Insights) |

### POST — update paid amount

Request body:

```json
{ "paidAmount": 50000 }
```

Success (`200`) returns fee fields plus `status` and `updatedBy`.  
`400` — invalid JSON, missing `paidAmount`, or non-negative number validation failure.  
`401` — missing / invalid `X-MS-CLIENT-PRINCIPAL`.  
`403` — principal lacks `FeeAdmin`.  
`404` — student not found.

---

## Payment status calculation

`compute_status(total_fee, paid_amount, due_date)` in `function_app.py`:

1. `PaidAmount >= TotalFee` → **Paid**
2. `PaidAmount > 0` and `DueDate >= today` → **Partially Paid**
3. `DueDate < today` → **Overdue**
4. Otherwise → **Partially Paid** (includes unpaid, not-yet-due records)

`today` is `date.today()` in the Function runtime (typically UTC).

---

## Authentication and authorization

### Fee status (GET)

- Protected by **function key** at the Function App
- Protected by **APIM subscription key** when called through API Management
- No Entra role check on this path

### Fee update (POST)

1. Easy Auth (Microsoft Entra ID) on the Function App
2. Decode `X-MS-CLIENT-PRINCIPAL` (Base64 JSON)
3. Require claim value **`FeeAdmin`** (`roles` or the WS-* role claim type)

`AuthLevel.ANONYMOUS` on the trigger allows Easy Auth to forward authenticated requests; security depends on Easy Auth remaining enabled on the Function App.

---

## API Management

Instance: **`fee-mgmt-apim-jiya`**

- Exposes `get_fee_status` and `update_fee`
- **Subscription key required** (`Ocp-Apim-Subscription-Key`)
- **Rate limit:** 10 calls per 60 seconds

---

## Automated reminders

| Setting | Value |
| --- | --- |
| Function | `send_fee_reminders` |
| Trigger | Timer (`@app.timer_trigger`) |
| Schedule | `0 0 8 * * *` — 08:00 UTC daily |
| `run_on_startup` | `false` |

**Workflow**

1. Query students where `DueDate < today` and `PaidAmount < TotalFee`
2. Send reminder email via Gmail SMTP (`smtp.gmail.com:587`, STARTTLS)
3. Credentials from `SENDER_EMAIL` and `GMAIL_APP_PASSWORD`

This is a **Timer Trigger**, not Durable Functions and not Logic Apps.

---

## Monitoring and retry

**Application Insights** is connected to the Function App. `host.json` enables App Insights logging with sampling.

**Host retry** (`host.json`):

| Property | Value |
| --- | --- |
| strategy | `fixedDelay` |
| maxRetryCount | `3` |
| delayInterval | `00:00:05` |

---

## Scalability (5,000+ students)

- Primary-key lookups by `StudentID`; `idx_duedate` for overdue queries
- Flex Consumption for HTTP scale; APIM rate limiting on the public front door
- Payment status computed per request (no denormalized status column)
- Reminder SMTP is per overdue student — large overdue batches take longer and face mail-provider limits

---

## Environment variables

| Name | Purpose |
| --- | --- |
| `AzureWebJobsStorage` | Functions storage (`UseDevelopmentStorage=true` locally) |
| `FUNCTIONS_WORKER_RUNTIME` | `python` |
| `SQL_SERVER` / `SQL_DATABASE` | SQL host / database |
| `SQL_USER` / `SQL_PASSWORD` | SQL credentials |
| `SENDER_EMAIL` / `GMAIL_APP_PASSWORD` | SMTP sender credentials |

Configure in Function App Application Settings (Azure) or `local.settings.json` (local).

---

## Local development and deploy

**Prerequisites:** Python 3.12, [Azure Functions Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local), Azurite.

```bash
cp local.settings.example.json local.settings.json   # fill placeholders
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
func start
```

```text
GET http://localhost:7071/api/students/1/status?code=<function-key>
```

Admin POST requires Easy Auth; validate against the deployed app with Entra sign-in.

```bash
func azure functionapp publish fee-mgmt-func-jiya
```

---

## API testing examples

Placeholders only — do not embed real keys.

```bash
# Via APIM
curl -X GET "https://<apim-name>.azure-api.net/<api-path>/students/1/status" \
  -H "Ocp-Apim-Subscription-Key: <subscription-key>"

# Direct to Function App
curl -X GET "https://<function-app>.azurewebsites.net/api/students/1/status?code=<function-key>"

# Admin update (requires Easy Auth session)
curl -X POST "https://<function-app>.azurewebsites.net/api/manage/students/1/update" \
  -H "Content-Type: application/json" \
  -d "{\"paidAmount\": 50000}"
```

---

## Security

- Secrets only in Application Settings / local settings — never in source control
- Parameterized SQL via `pymssql` (`%s`)
- GET: function key + APIM subscription (not per-student identity)
- POST: see [Authentication and authorization](#authentication-and-authorization)
- Generic `500` responses; details in Application Insights

---

## Implementation trade-offs

| Topic | Implemented | Notes |
| --- | --- | --- |
| Reminders | Timer Trigger | See [Automated reminders](#automated-reminders) |
| Email | Gmail SMTP | Practical working path for this deployment |
| Admin URL | `/manage/...` | See [Design choices](#design-choices) |
| SQL driver | `pymssql` | Stable on Linux Flex Consumption |
| Payment status | Computed in code | Always derived from fee amounts and due date |
| Admin auth | Entra `FeeAdmin` | See [Authentication and authorization](#authentication-and-authorization) |

---

## Assignment requirement mapping

| Requirement | Implementation |
| --- | --- |
| Students view fee status via API | `get_fee_status` |
| Automated overdue reminders | Timer Trigger + SMTP (`0 0 8 * * *` UTC) |
| Admins query fee details | Same GET API via APIM / function key |
| Admins update fees securely | `update_fee` + Easy Auth + `FeeAdmin` |
| Azure SQL | Deployed database + `pymssql` |
| Students / Administrators tables + ≥20 students | `database/database_setup.sql` |
| Azure Functions for API & calculation | HTTP functions + `compute_status` |
| APIM, subscription auth, rate limit | APIM instance; 10 calls / 60 s |
| Entra ID + RBAC | Easy Auth + `FeeAdmin` |
| Scale for 5,000+ records | SQL + index + Flex Consumption |
| Application Insights + retry | App Insights + `host.json` retry |
| Logic Apps or Durable Functions | **Timer Trigger used instead** (documented above) |
