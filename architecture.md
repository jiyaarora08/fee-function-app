<img width="1536" height="1024" alt="Fee Management System — Azure Architecture" src="https://github.com/user-attachments/assets/1ec3c8a4-21a7-470a-a7f5-8c4e3f2e5673" />

## Architecture notes

The deployed system can be described as follows. Only resources that were actually used are listed.

### Student fee-status flow

```
Student / API client
    → Azure API Management (subscription key, 10 calls / 60 seconds)
        → Azure Function get_fee_status (HTTP GET, function key)
            → [if an Easy Auth principal is present: enforce self-access
               via the 'preferred_username' claim, unless FeeAdmin]
            → Azure SQL Database (Students)
```

Payment status is computed in the Function. It is not stored as a SQL column.

A caller with only the function/APIM key and no signed-in principal is
granted access at the key level, same as before — this is a documented
service-to-service trust boundary, not per-user identity enforcement.
When a signed-in principal is present, the caller may only view the
StudentID whose registered Email matches their own sign-in
`preferred_username`, unless they hold the `FeeAdmin` role.

### Admin update flow

```
Administrator
    → Microsoft Entra ID / Azure Easy Auth
        → Azure Function update_fee (HTTP POST /manage/students/{studentId}/update)
            → FeeAdmin application-role check (X-MS-CLIENT-PRINCIPAL)
                → Azure SQL Database (UPDATE Students.PaidAmount)
```

The application route is `/manage/...`. It is not `/admin/...`.

### Scheduled reminder flow

```
Azure Functions Timer Trigger (daily 08:00 UTC)
    → fee_reminder_starter (Durable Functions client)
        → fee_reminder_orchestrator
            → get_overdue_students (activity, one DB round trip)
                → Azure SQL Database (overdue Students)
            → send_reminder_batch (activity, parallel fan-out via
              context.task_all, one SendGrid API call per batch of up
              to 500 students)
                → SendGrid (dynamic template)
                    → Student email
```

This is a **Durable Functions orchestration**, not a plain Timer Trigger
and not a Logic App. The orchestration checkpoints after each activity
(a mid-run crash resumes from the last completed step) and fans out
reminder batches in parallel, rather than sending one email per student
sequentially.

A manual trigger endpoint, `POST /manage/trigger-reminders` (function
key), starts the same orchestration on demand — useful for testing the
reminder flow without waiting for the daily timer.

### Monitoring

```
Application Insights  ←  Azure Function App (requests, traces, failures)
```

Host-level retry for failed invocations is configured in `host.json` (fixed delay, 3 retries, 5 seconds).
