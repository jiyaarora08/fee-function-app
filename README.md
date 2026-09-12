# Fee Management System Using Azure

Serverless fee-management backend built with **Azure Functions (Python 3.12)**, **Azure Durable Functions**, **Azure SQL Database**, **Azure API Management**, and **Microsoft Entra ID**. The system exposes APIs for student fee status, secure administrator updates, and automated overdue-payment reminders.

**Assessment:** Fusion Practices — AI Product Engineer, Round 3 Technical Assessment
**Deliverable:** Code repository for a system deployed and tested in Azure

---

## Overview

| Capability                        | Implementation                                                                                         |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------- |
| View fee details & payment status | HTTP GET via Azure Functions + API Management, identity-scoped for signed-in students                    |
| Update fee records                | HTTP POST — see [Authentication and authorization](#authentication-and-authorization)                    |
| Overdue reminders                 | **Durable Functions orchestration**, timer-started, fanned out to SendGrid                               |
| Data store                        | Azure SQL Database                                                                                        |
| Monitoring                        | Application Insights                                                                                      |
| Retry                             | Host-level fixed-delay retry in `host.json`                                                               |

Payment status is **computed in the Function**, not stored as a SQL column.

---

## Architecture

```
Student / API client
  → Azure API Management (subscription key, 10 calls / 60 s)
    → Azure Function get_fee_status
      → [if Easy Auth principal present: enforce self-access unless FeeAdmin]
      → Azure SQL Database

Administrator
  → Easy Auth (see Authentication and authorization)
    → Azure Function update_fee
      → Azure SQL Database

Timer Trigger (daily 08:00 UTC)
  → fee_reminder_starter (Durable Functions client)
    → fee_reminder_orchestrator
        → get_overdue_students (activity)
        → send_reminder_batch (activity, parallel fan-out, SendGrid dynamic template)
          → Student email

Application Insights ← Function App
```

### Design choices

| Decision                          | Rationale                                                                                                                                               |
| ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Azure Functions**                | Single Python app for HTTP APIs and orchestrated reminders; Flex Consumption scales with demand; integrates with Easy Auth, APIM, and Application Insights |
| **Azure SQL**                      | Structured fee records, parameterized queries, and indexing for overdue lookups                                                                         |
| **pymssql**                        | Reliable TDS client on Linux Flex Consumption (port 1433, TDS 7.4)                                                                                      |
| **Admin route `/manage/...`**      | Azure Functions reserves `/admin/` for host administration; `/manage/students/{studentId}/update` avoids that collision                                 |
| **Durable Functions for reminders**| Checkpointed, parallel fan-out replaces a plain Timer Trigger — see [Automated reminders](#automated-reminders)                                          |
| **SendGrid, batched dynamic template** | Genuine per-recipient personalization at scale via one API call per batch, replacing a per-student Gmail SMTP loop                                    |

---

## Azure resources

| Resource        | Detail                                                                                         |
| --------------- | ------------------------------------------------------------------------------------------------ |
| Resource group  | `fee-management-rg` (Central India)                                                              |
| SQL Server / DB | `fee-management-server-jiya` / `free-sql-db-4359734`                                             |
| Function App    | `fee-mgmt-func-jiya` — Linux, Python 3.12, Flex Consumption                                       |
| API Management  | `fee-mgmt-apim-jiya`                                                                              |
| Identity        | Microsoft Entra ID — see [Authentication and authorization](#authentication-and-authorization)    |
| Monitoring      | Application Insights                                                                              |
| Email           | SendGrid (dynamic template) — see [Automated reminders](#automated-reminders)                     |

---

## Project structure

```
fee-function-app/
├── function_app.py                 # HTTP, timer, orchestrator, and activity functions
├── host.json                       # retry + durable task hub + App Insights logging
├── requirements.txt                # pinned dependencies
├── local.settings.example.json     # env template (no secrets)
├── database/database_setup.sql     # schema, index, sample data
<<<<<<< HEAD
├── docs/architecture_diagram.png
├── architecture.md                 # per-flow architecture notes
├── DEMO_SCRIPT.md                  # recording script for the submission demo
=======
├── architecture.md                 # architecture notes + embedded diagram
├── architecture_diagram.png        # architecture diagram (tracked file)
>>>>>>> f14da6f355ca8df298ee81bae4f115fb58d7fe5f
├── README.md
└── .gitignore
```

Use `local.settings.example.json` as a template for local `local.settings.json` (gitignored; never commit secrets).

---

## Database

Schema and seed data: `database/database_setup.sql` (non-destructive; suitable for a fresh database).

### Students

| Column     | Type                    | Notes                         |
| ---------- | ----------------------- | ------------------------------ |
| StudentID  | INT PRIMARY KEY         | Route parameter `{studentId}` |
| Name       | NVARCHAR                |                                |
| Course     | NVARCHAR                |                                |
| Email      | NVARCHAR                | Used by reminders              |
| TotalFee   | DECIMAL(12,2)           |                                |
| PaidAmount | DECIMAL(12,2) DEFAULT 0 | Updated by admin API           |
| DueDate    | DATE                    | Indexed (`idx_duedate`)        |

### Administrators

| Column  | Type            |
| ------- | ---------------- |
| AdminID | INT PRIMARY KEY |
| Name    | NVARCHAR         |
| Role    | NVARCHAR         |

Authorization for updates uses Entra ID role **`FeeAdmin`**, not the `Administrators` table. The table is included to match the assignment schema. The script seeds **20 students** and **2 administrators** (synthetic emails only).

---

## API endpoints

Default Functions route prefix: `/api`.

| Function             | Method | Route                                     | Auth                                                                                              |
| --------------------- | ------ | ------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| `get_fee_status`      | GET    | `/api/students/{studentId}/status`        | Function key; APIM subscription key when called via APIM; self-scoped if an Easy Auth principal is present |
| `update_fee`          | POST   | `/api/manage/students/{studentId}/update` | Easy Auth + `FeeAdmin` (function trigger is `ANONYMOUS` so Easy Auth can inject the principal)      |
| `fee_reminder_starter`| —      | Timer `0 0 8 * * *` (08:00 UTC daily)     | Not HTTP — starts the Durable Functions orchestration                                               |
| `fee_reminder_orchestrator` | — | Durable orchestration (internal)          | Not HTTP                                                                                             |
| `get_overdue_students`| —      | Durable activity (internal)               | Not HTTP                                                                                             |
| `send_reminder_batch` | —      | Durable activity (internal)               | Not HTTP                                                                                             |

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

| Status | Condition                                                                                     |
| ------ | ----------------------------------------------------------------------------------------------- |
| 200    | Student found                                                                                    |
| 403    | Signed-in caller (non-`FeeAdmin`) requesting a `studentId` other than their own                  |
| 404    | Student not found                                                                                |
| 500    | Unexpected error (details in Application Insights)                                               |

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
- **If** the request also carries a validated Easy Auth principal (a student or admin signed in via Entra ID), the caller may only view their own `StudentID` (matched against an `extension_StudentID` claim) unless they hold the `FeeAdmin` role.
- **If there is no principal** (a raw function-key or APIM-key call with no signed-in user — e.g. a trusted backend integration), access is granted at the key level, same as before. This is a deliberate service-to-service trust boundary rather than per-user identity, and is called out here explicitly: a caller with only the shared key can still query any StudentID. Closing that fully would mean requiring Easy Auth on every GET call and dropping the raw-key path entirely, which the current setup does not do.
- The `extension_StudentID` claim requires a custom claim/attribute mapping in the Entra ID app registration tying a signed-in student's identity to their `StudentID`. If student-level Entra accounts aren't provisioned for this assessment, self-scoped access is a documented forward-looking design rather than something exercised against real student logins.

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

| Setting          | Value                                              |
| ----------------- | ---------------------------------------------------- |
| Starter function  | `fee_reminder_starter` (Timer, `0 0 8 * * *` UTC)    |
| Orchestrator      | `fee_reminder_orchestrator`                           |
| Activities        | `get_overdue_students`, `send_reminder_batch`         |
| Batch size        | 500 students per SendGrid API call                    |
| Email provider    | SendGrid, dynamic template                            |
| `run_on_startup`  | `false`                                                |

**Workflow**

1. Timer starts a new Durable Functions orchestration instance daily at 08:00 UTC.
2. `get_overdue_students` runs once, querying `DueDate < today AND PaidAmount < TotalFee`.
3. The orchestrator splits the result into batches of 500 and fans out `send_reminder_batch` activities in parallel via `context.task_all`.
4. Each activity sends one SendGrid API call covering its whole batch, using a dynamic template (`SENDGRID_TEMPLATE_ID`) so every student still gets a personalized name/balance/due-date email.

**Why Durable Functions instead of a plain Timer Trigger**

An earlier version of this project used a bare Timer Trigger with a sequential per-student SMTP loop. That doesn't checkpoint (a mid-run crash restarts from student #1) and doesn't parallelize (one SMTP connection per student becomes the bottleneck at 5,000+ overdue records). The Durable Functions orchestration checkpoints after each activity and fans out in parallel, which is what the assignment's Task 2 (Logic Apps or Durable Functions) asks for.

**One-time SendGrid setup**

1. Create a SendGrid dynamic template with placeholders `{{name}}`, `{{balance}}`, `{{due_date}}`.
2. Verify your sender identity/domain in SendGrid.
3. Set `SENDGRID_API_KEY`, `SENDER_EMAIL`, and `SENDGRID_TEMPLATE_ID` in Function App Application Settings.

---

## Monitoring and retry

**Application Insights** is connected to the Function App. `host.json` enables App Insights logging with sampling.

**Host retry** (`host.json`):

| Property      | Value        |
| ------------- | ------------- |
| strategy      | `fixedDelay`  |
| maxRetryCount | `3`           |
| delayInterval | `00:00:05`    |

Durable Functions orchestration history (per-instance status, replay, and failures) is also visible via the Durable Functions HTTP management API and in Application Insights traces.

---

## Scalability (5,000+ students)

- Primary-key lookups by `StudentID`; `idx_duedate` for overdue queries
- Flex Consumption for HTTP scale; APIM rate limiting on the public front door
- Payment status computed per request (no denormalized status column)
- Reminder delivery is batched (500 students/SendGrid call) and fanned out in parallel via Durable Functions, instead of one SMTP connection per overdue student — this removes the earlier per-student mail bottleneck at scale

---

## Environment variables

| Name                                  | Purpose                                                       |
| --------------------------------------- | ---------------------------------------------------------------- |
| `AzureWebJobsStorage`                  | Functions storage (`UseDevelopmentStorage=true` locally); also used as the Durable Functions storage backend |
| `FUNCTIONS_WORKER_RUNTIME`             | `python`                                                          |
| `SQL_SERVER` / `SQL_DATABASE`          | SQL host / database                                               |
| `SQL_USER` / `SQL_PASSWORD`            | SQL credentials                                                   |
| `SENDER_EMAIL`                         | Verified SendGrid sender identity                                 |
| `SENDGRID_API_KEY`                     | SendGrid API key for reminder emails                              |
| `SENDGRID_TEMPLATE_ID`                 | Dynamic template ID with `name` / `balance` / `due_date` placeholders |

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

```
GET http://localhost:7071/api/students/1/status?code=<function-key>
```

Admin POST requires Easy Auth; validate against the deployed app with Entra sign-in.

To exercise the reminder flow locally without waiting for the timer, use the Durable Functions HTTP management API exposed by the local runtime to start `fee_reminder_orchestrator` directly, or temporarily set `run_on_startup` to `true` on `fee_reminder_starter`.

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
- GET: function key + APIM subscription (self-scoped by Entra identity when a principal is present; key-scoped otherwise — see [Authentication and authorization](#authentication-and-authorization))
- POST: see [Authentication and authorization](#authentication-and-authorization)
- Generic `500` responses; details in Application Insights
- Dependencies pinned in `requirements.txt`; confirm pinned versions against the deployed Function App's Python worker before release

---

## Implementation trade-offs

| Topic            | Implemented                          | Notes                                                                                                    |
| ------------------ | --------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Reminders         | Durable Functions orchestration         | Timer-started, parallel fan-out, checkpointed — see [Automated reminders](#automated-reminders)          |
| Email             | SendGrid, dynamic template               | Batched personalizations (500/call); replaced an earlier Gmail SMTP loop                                 |
| Admin URL         | `/manage/...`                            | See [Design choices](#design-choices)                                                                    |
| SQL driver        | `pymssql`                                 | Stable on Linux Flex Consumption                                                                          |
| Payment status    | Computed in code                         | Always derived from fee amounts and due date                                                              |
| Admin auth        | Entra `FeeAdmin`                          | See [Authentication and authorization](#authentication-and-authorization)                                 |
| Student GET auth  | Self-access enforced when signed in       | Raw function-key/APIM-key calls remain key-scoped, not identity-scoped — documented above, not hidden     |

---

## Demo

See `DEMO_SCRIPT.md` for the recording script used for the submission demo (API functionality, reminders being sent, secure admin operations, and the self-access check).

---

## Assignment requirement mapping

| Requirement                                     | Implementation                                                    |
| -------------------------------------------------- | --------------------------------------------------------------------- |
| Students view fee status via API                  | `get_fee_status`, self-scoped when signed in                          |
| Automated overdue reminders                       | Durable Functions orchestration + SendGrid (`0 0 8 * * *` UTC start) |
| Admins query fee details                          | Same GET API via APIM / function key                                  |
| Admins update fees securely                       | `update_fee` + Easy Auth + `FeeAdmin`                                  |
| Azure SQL                                         | Deployed database + `pymssql`                                         |
| Students / Administrators tables + ≥20 students   | `database/database_setup.sql`                                         |
| Azure Functions for API & calculation             | HTTP functions + `compute_status`                                     |
| APIM, subscription auth, rate limit               | APIM instance; 10 calls / 60 s                                        |
| Entra ID + RBAC                                   | Easy Auth + `FeeAdmin`; self-access check on GET                       |
| Scale for 5,000+ records                          | SQL + index + Flex Consumption + batched/parallel reminder delivery    |
| Application Insights + retry                      | App Insights + `host.json` retry                                      |
| Logic Apps or Durable Functions                   | **Durable Functions orchestration** (timer-started, parallel fan-out) |