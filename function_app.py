import azure.functions as func
import azure.durable_functions as df
import logging, os, json, base64
import pymssql
from datetime import date
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, To, From, Personalization

app = df.DFApp(http_auth_level=func.AuthLevel.FUNCTION)

# Reminder emails are sent to SendGrid in batches of this size (SendGrid
# accepts up to 1000 personalizations per call). Batching keeps the
# reminder job to a handful of API calls even at 5,000+ overdue students,
# instead of one SMTP connection per student.
SENDGRID_BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def get_db_connection():
    server = os.environ["SQL_SERVER"]
    database = os.environ["SQL_DATABASE"]
    user = os.environ["SQL_USER"]
    password = os.environ["SQL_PASSWORD"]
    return pymssql.connect(
        server=server,
        user=user,
        password=password,
        database=database,
        port=1433,
        tds_version='7.4',
    )


def compute_status(total_fee, paid_amount, due_date):
    if paid_amount >= total_fee:
        return "Paid"
    if paid_amount > 0 and due_date >= date.today():
        return "Partially Paid"
    if due_date < date.today():
        return "Overdue"
    return "Partially Paid"


def get_client_principal(req: func.HttpRequest):
    header = req.headers.get("X-MS-CLIENT-PRINCIPAL")
    if not header:
        return None
    try:
        decoded = base64.b64decode(header)
        return json.loads(decoded)
    except Exception as e:
        logging.warning(f"Could not decode client principal: {e}")
        return None


def has_role(principal, role_name):
    if not principal:
        return False
    role_claim_types = {
        "roles",
        "http://schemas.microsoft.com/ws/2008/06/identity/claims/role",
    }
    for claim in principal.get("claims", []):
        if claim.get("typ") in role_claim_types and claim.get("val") == role_name:
            return True
    return False


def get_claim_value(principal, claim_type):
    if not principal:
        return None
    for claim in principal.get("claims", []):
        if claim.get("typ") == claim_type:
            return claim.get("val")
    return None


# ---------------------------------------------------------------------------
# GET /students/{studentId}/status
#
# Auth model (documented in README "Authentication and authorization"):
#   - If the request carries a validated Easy Auth principal (student or
#     admin signed in via Entra ID), the caller may only view their own
#     StudentID unless they hold the FeeAdmin role.
#   - If there is no principal (APIM subscription key / raw function key
#     call, e.g. a trusted backend integration), access is granted at the
#     function-key level, same as before. This is a deliberate
#     service-to-service trust boundary, not per-user identity, and is
#     called out explicitly rather than silently treated as user-level auth.
# ---------------------------------------------------------------------------
@app.function_name(name="get_fee_status")
@app.route(route="students/{studentId}/status", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_fee_status(req: func.HttpRequest) -> func.HttpResponse:
    student_id = req.route_params.get("studentId")
    principal = get_client_principal(req)

    try:
        conn = get_db_connection()
        cursor = conn.cursor(as_dict=True)
        cursor.execute(
            "SELECT StudentID, Name, Course, Email, TotalFee, PaidAmount, DueDate FROM Students WHERE StudentID = %s",
            (student_id,),
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            return func.HttpResponse(json.dumps({"error": "Student not found"}), status_code=404, mimetype="application/json")

        # A signed-in caller without the admin role may only view a student
        # record whose registered Email matches their own sign-in identity.
        # 'preferred_username' is a standard Entra ID ID token claim (the
        # user's UPN) that's always populated, unlike 'email' which
        # requires the account to have a separate Mail attribute set and
        # isn't guaranteed to appear even when requested as an optional
        # claim (see README "Authentication and authorization").
        if principal and not has_role(principal, "FeeAdmin"):
            caller_email = get_claim_value(principal, "preferred_username")
            if caller_email is None or caller_email.lower() != (row["Email"] or "").lower():
                return func.HttpResponse(
                    json.dumps({"error": "Forbidden - you may only view your own fee status"}),
                    status_code=403, mimetype="application/json",
                )

        status = compute_status(float(row["TotalFee"]), float(row["PaidAmount"]), row["DueDate"])
        result = {
            "studentId": row["StudentID"], "name": row["Name"], "course": row["Course"],
            "totalFee": float(row["TotalFee"]), "paidAmount": float(row["PaidAmount"]),
            "dueDate": row["DueDate"].isoformat(), "status": status,
        }
        return func.HttpResponse(json.dumps(result), status_code=200, mimetype="application/json")
    except Exception as e:
        logging.error(f"Error: {e}")
        return func.HttpResponse(json.dumps({"error": "Internal server error"}), status_code=500, mimetype="application/json")


# ---------------------------------------------------------------------------
# POST /manage/students/{studentId}/update  (unchanged from before)
# ---------------------------------------------------------------------------
@app.function_name(name="update_fee")
@app.route(route="manage/students/{studentId}/update", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def update_fee(req: func.HttpRequest) -> func.HttpResponse:
    principal = get_client_principal(req)
    if not principal:
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized - sign in required"}),
            status_code=401, mimetype="application/json",
        )
    if not has_role(principal, "FeeAdmin"):
        return func.HttpResponse(
            json.dumps({"error": "Forbidden - FeeAdmin role required"}),
            status_code=403, mimetype="application/json",
        )

    student_id = req.route_params.get("studentId")
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(json.dumps({"error": "Invalid JSON body"}), status_code=400, mimetype="application/json")

    paid_amount = body.get("paidAmount")
    if paid_amount is None:
        return func.HttpResponse(json.dumps({"error": "paidAmount is required"}), status_code=400, mimetype="application/json")
    try:
        paid_amount = float(paid_amount)
        if paid_amount < 0:
            raise ValueError("negative")
    except (ValueError, TypeError):
        return func.HttpResponse(json.dumps({"error": "paidAmount must be a non-negative number"}), status_code=400, mimetype="application/json")

    try:
        conn = get_db_connection()
        cursor = conn.cursor(as_dict=True)
        cursor.execute("SELECT StudentID FROM Students WHERE StudentID = %s", (student_id,))
        if not cursor.fetchone():
            conn.close()
            return func.HttpResponse(json.dumps({"error": "Student not found"}), status_code=404, mimetype="application/json")

        cursor.execute(
            "UPDATE Students SET PaidAmount = %s WHERE StudentID = %s",
            (paid_amount, student_id),
        )
        conn.commit()

        cursor.execute(
            "SELECT StudentID, Name, TotalFee, PaidAmount, DueDate FROM Students WHERE StudentID = %s",
            (student_id,),
        )
        updated = cursor.fetchone()
        conn.close()

        status = compute_status(float(updated["TotalFee"]), float(updated["PaidAmount"]), updated["DueDate"])
        admin_name = principal.get("userDetails", "unknown-admin")
        logging.info(f"Admin '{admin_name}' updated PaidAmount for student {student_id} to {paid_amount}")

        result = {
            "studentId": updated["StudentID"],
            "name": updated["Name"],
            "totalFee": float(updated["TotalFee"]),
            "paidAmount": float(updated["PaidAmount"]),
            "dueDate": updated["DueDate"].isoformat(),
            "status": status,
            "updatedBy": admin_name,
        }
        return func.HttpResponse(json.dumps(result), status_code=200, mimetype="application/json")
    except Exception as e:
        logging.error(f"Error updating fee: {e}")
        return func.HttpResponse(json.dumps({"error": "Internal server error"}), status_code=500, mimetype="application/json")


# ---------------------------------------------------------------------------
# Automated reminders — now a Durable Functions orchestration:
#
#   Timer (08:00 UTC daily)
#     -> fee_reminder_starter  (client function, starts the orchestration)
#        -> fee_reminder_orchestrator
#             -> get_overdue_students        (activity, one DB round trip)
#             -> send_reminder_batch  x N    (activities, run in parallel
#                                              via context.task_all, one
#                                              SendGrid call per batch of
#                                              SENDGRID_BATCH_SIZE students)
#
# This replaces the plain Timer Trigger used previously. It gives durable
# checkpointing (a crash mid-run resumes from the last completed activity
# instead of restarting the whole batch) and parallel fan-out instead of
# a sequential per-student loop.
# ---------------------------------------------------------------------------

@app.timer_trigger(schedule="0 0 8 * * *", arg_name="mytimer", run_on_startup=False)
@app.durable_client_input(client_name="client")
async def fee_reminder_starter(mytimer: func.TimerRequest, client) -> None:
    instance_id = await client.start_new("fee_reminder_orchestrator")
    logging.info(f"Started fee_reminder_orchestrator, instance ID = {instance_id}")


@app.route(route="manage/trigger-reminders", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
@app.durable_client_input(client_name="client")
async def fee_reminder_trigger(req: func.HttpRequest, client) -> func.HttpResponse:
    """Manually starts the fee reminder orchestration on demand. Useful for
    testing the reminder flow without waiting for the 08:00 UTC timer and
    without needing to toggle run_on_startup (which would require a
    redeploy each way). Protected by function key, same as the other
    admin-adjacent endpoints."""
    instance_id = await client.start_new("fee_reminder_orchestrator")
    return func.HttpResponse(
        json.dumps({"message": "Reminder orchestration started", "instanceId": instance_id}),
        status_code=202, mimetype="application/json",
    )


@app.orchestration_trigger(context_name="context")
def fee_reminder_orchestrator(context: df.DurableOrchestrationContext):
    overdue_students = yield context.call_activity("get_overdue_students")

    batches = [
        overdue_students[i:i + SENDGRID_BATCH_SIZE]
        for i in range(0, len(overdue_students), SENDGRID_BATCH_SIZE)
    ]

    tasks = [context.call_activity("send_reminder_batch", batch) for batch in batches]
    results = yield context.task_all(tasks)

    total_sent = sum(results)
    return {"overdueCount": len(overdue_students), "remindersSent": total_sent}


@app.activity_trigger(input_name="ignore")
def get_overdue_students(ignore) -> list:
    conn = get_db_connection()
    cursor = conn.cursor(as_dict=True)
    cursor.execute(
        "SELECT StudentID, Name, Email, TotalFee, PaidAmount, DueDate FROM Students "
        "WHERE DueDate < %s AND PaidAmount < TotalFee",
        (date.today(),),
    )
    rows = cursor.fetchall()
    conn.close()
    # DueDate isn't JSON-serializable by default; convert before it crosses
    # the durable-functions activity boundary.
    for row in rows:
        row["DueDate"] = row["DueDate"].isoformat()
        row["TotalFee"] = float(row["TotalFee"])
        row["PaidAmount"] = float(row["PaidAmount"])
    return rows


@app.activity_trigger(input_name="students")
def send_reminder_batch(students: list) -> int:
    """Sends one SendGrid API call covering up to SENDGRID_BATCH_SIZE
    students. Each recipient gets a genuinely personalized email (name,
    balance, due date) via a SendGrid dynamic template — one HTTP call
    handles the whole batch instead of one SMTP session per student.

    Requires a SendGrid dynamic template (SENDGRID_TEMPLATE_ID) with
    {{name}}, {{balance}}, and {{due_date}} placeholders. See README
    "Automated reminders" for the one-time template setup.
    """
    if not students:
        return 0

    sender_email = os.environ["SENDER_EMAIL"]
    sendgrid_api_key = os.environ["SENDGRID_API_KEY"]
    template_id = os.environ["SENDGRID_TEMPLATE_ID"]

    message = Mail(from_email=From(sender_email))
    message.template_id = template_id

    for student in students:
        balance = float(student["TotalFee"]) - float(student["PaidAmount"])
        personalization = Personalization()
        personalization.add_to(To(student["Email"]))
        personalization.dynamic_template_data = {
            "name": student["Name"],
            "balance": f"{balance:.2f}",
            "due_date": student["DueDate"],
        }
        message.add_personalization(personalization)

    try:
        sg = SendGridAPIClient(sendgrid_api_key)
        response = sg.send(message)
        logging.info(f"SendGrid batch sent: status={response.status_code}, count={len(students)}")
        return len(students)
    except Exception as e:
        logging.error(f"Error sending reminder batch via SendGrid: {e}")
        return 0