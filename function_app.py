import azure.functions as func
import logging, os, json, base64
import pymssql
import smtplib
from email.mime.text import MIMEText
from datetime import date

app = func.FunctionApp()

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


@app.function_name(name="get_fee_status")
@app.route(route="students/{studentId}/status", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def get_fee_status(req: func.HttpRequest) -> func.HttpResponse:
    student_id = req.route_params.get("studentId")
    try:
        conn = get_db_connection()
        cursor = conn.cursor(as_dict=True)
        cursor.execute(
            "SELECT StudentID, Name, Course, TotalFee, PaidAmount, DueDate FROM Students WHERE StudentID = %s",
            (student_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return func.HttpResponse(json.dumps({"error": "Student not found"}), status_code=404, mimetype="application/json")
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


@app.function_name(name="send_fee_reminders")
@app.timer_trigger(schedule="0 0 8 * * *", arg_name="mytimer", run_on_startup=False)
def send_fee_reminders(mytimer: func.TimerRequest) -> None:
    logging.info("Running scheduled fee reminder check...")
    try:
        conn = get_db_connection()
        cursor = conn.cursor(as_dict=True)
        cursor.execute(
            "SELECT StudentID, Name, Email, TotalFee, PaidAmount, DueDate FROM Students WHERE DueDate < %s AND PaidAmount < TotalFee",
            (date.today(),),
        )
        overdue_students = cursor.fetchall()
        conn.close()

        sender_email = os.environ["SENDER_EMAIL"]
        app_password = os.environ["GMAIL_APP_PASSWORD"]

        for student in overdue_students:
            balance = float(student["TotalFee"]) - float(student["PaidAmount"])
            subject = "Fee Payment Reminder"
            body = (
                f"Dear {student['Name']},\n\n"
                f"This is a reminder that your fee payment is overdue.\n"
                f"Outstanding balance: {balance}\n"
                f"Due date was: {student['DueDate']}\n\n"
                f"Please make the payment at the earliest.\n\n"
                f"Regards,\nFee Management System"
            )
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = sender_email
            msg["To"] = student["Email"]

            with smtplib.SMTP("smtp.gmail.com", 587) as server:
                server.starttls()
                server.login(sender_email, app_password)
                server.sendmail(sender_email, student["Email"], msg.as_string())

            logging.info(f"Reminder sent to {student['Email']}")

    except Exception as e:
        logging.error(f"Error sending reminders: {e}")