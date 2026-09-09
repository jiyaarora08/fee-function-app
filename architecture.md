# Architecture notes


<img width="663" height="540" alt="image" src="https://github.com/user-attachments/assets/0706a937-0963-40f7-8f70-036f2cce4785" />


The deployed system can be described as follows. Only resources that were actually used are listed.

## Student fee-status flow

```
Student / API client
    → Azure API Management (subscription key, 10 calls / 60 seconds)
        → Azure Function get_fee_status (HTTP GET, function key)
            → Azure SQL Database (Students)
```

Payment status is computed in the Function. It is not stored as a SQL column.

## Admin update flow

```
Administrator
    → Microsoft Entra ID / Azure Easy Auth
        → Azure Function update_fee (HTTP POST /manage/students/{studentId}/update)
            → FeeAdmin application-role check (X-MS-CLIENT-PRINCIPAL)
                → Azure SQL Database (UPDATE Students.PaidAmount)
```

The application route is `/manage/...`. It is not `/admin/...`.

## Scheduled reminder flow

```
Azure Functions Timer Trigger (daily 08:00 UTC)
    → Azure Function send_fee_reminders
        → Azure SQL Database (overdue Students)
            → Gmail SMTP
                → Student email
```

This is a **Timer Trigger**, not a Durable Function and not a Logic App.

## Monitoring

```
Application Insights  ←  Azure Function App (requests, traces, failures)
```

Host-level retry for failed invocations is configured in `host.json` (fixed delay, 3 retries, 5 seconds).
