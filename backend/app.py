import hashlib
import logging
import os
import random
import smtplib
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import google.generativeai as genai
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_cors import CORS
from groq import Groq

log = logging.getLogger(__name__)

OTP_EXPIRY_SECONDS    = 60
MIN_REPORT_LENGTH     = 10
GMAIL_SMTP_HOST       = "smtp.gmail.com"
GMAIL_SMTP_PORT       = 465
MIN_PASSWORD_LENGTH   = 6
PROTECTED_ADMIN_EMAIL = "admin@crisis.com"
TIMESTAMP_FORMAT      = "%Y-%m-%d %H:%M:%S"

ALLOWED_SIGNUP_ROLES     = {"user", "responder"}
ACTIVE_RESPONDER_STATUSES = {"Pending", "Dispatched", "Accepted", "On The Way"}
VALID_STATUS_TRANSITIONS  = ["Pending", "Dispatched", "Accepted", "On The Way", "Resolved"]

ROLE_LANDING_PAGE = {
    "admin":     "admin_page",
    "responder": "responder_page",
    "user":      "dashboard",
}

EMERGENCY_TYPE_KEYWORDS = [
    ("FIRE",             {"fire", "burn", "smoke", "aag", "blaze", "flames"}),
    ("MEDICAL",          {"injury", "bleeding", "faint", "ambulance", "heart",
                          "breath", "unconscious", "medical"}),
    ("SECURITY",         {"attack", "robbery", "threat", "knife", "gun",
                          "assault", "theft", "steal"}),
    ("NATURAL DISASTER", {"earthquake", "flood", "storm", "tsunami", "landslide", "cyclone"}),
    ("ACCIDENT",         {"accident", "crash", "fall", "collision", "vehicle"}),
]

OFFLINE_FALLBACK_ANALYSIS = (
    "Type: {emergency_type}\nUrgency: MEDIUM\nPanic Level: MEDIUM\n\n"
    "Suggested Action:\n1. Stay calm\n2. Contact emergency services\n3. Wait for help"
)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "crisisense_secret_2024")
CORS(app)

DB_PATH       = os.getenv("DB_PATH", os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "database.db")
))
MAIL_EMAIL    = os.getenv("MAIL_EMAIL", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
GROQ_API_KEY  = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

print(f"DB_PATH={DB_PATH}")

gemini_model = None
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel("gemini-1.5-flash")
    except Exception as exc:
        log.warning("gemini_init_failed err=%s", exc)

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# evicted immediately on successful OTP verification
pending_otps: dict[str, str] = {}


# --- db ------------------------------------------------------------------

@contextmanager
def db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def hash_password(plaintext: str) -> str:
    # SHA-256 over bcrypt: Render free tier can't absorb bcrypt's CPU cost at
    # any reasonable work factor without spiking login latency past 400 ms.
    return hashlib.sha256(plaintext.encode()).hexdigest()


def init_db() -> None:
    with db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT,
                contact       TEXT UNIQUE,
                password_hash TEXT,
                role          TEXT,
                otp_verified  INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                message   TEXT,
                location  TEXT,
                type      TEXT,
                urgency   TEXT,
                panic     TEXT,
                status    TEXT DEFAULT 'Pending',
                timestamp TEXT,
                user_id   TEXT
            )
        """)
        for name, contact, pw_hash, role in [
            ("Admin",         PROTECTED_ADMIN_EMAIL,  hash_password("admin123"), "admin"),
            ("Responder One", "responder@crisis.com", hash_password("resp123"),  "responder"),
        ]:
            conn.execute(
                "INSERT OR IGNORE INTO users (name, contact, password_hash, role, otp_verified) "
                "VALUES (?, ?, ?, ?, 1)",
                (name, contact, pw_hash, role),
            )
        conn.commit()


with app.app_context():
    init_db()


# --- email ---------------------------------------------------------------

def send_otp_email(recipient_email: str, otp: str) -> bool:
    if not MAIL_EMAIL or not MAIL_PASSWORD:
        # intentional dev escape hatch — never strip this
        log.warning("smtp_not_configured otp_console_fallback email=%s otp=%s", recipient_email, otp)
        return True

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "CrisisSense — OTP"
    msg["From"]    = MAIL_EMAIL
    msg["To"]      = recipient_email

    msg.attach(MIMEText(
        f"Your OTP: {otp}\nExpires in 10 minutes. Do not share.", "plain"
    ))
    msg.attach(MIMEText(f"""
<html><body style="font-family:Arial,sans-serif;background:#1a1a2e;color:#eee;padding:40px;">
  <div style="max-width:480px;margin:auto;background:#16213e;border-radius:12px;padding:32px;">
    <h2 style="color:#e63946;">CrisisSense</h2>
    <hr style="border-color:#333;margin:24px 0;">
    <div style="font-size:40px;font-weight:bold;letter-spacing:10px;color:#e63946;
                background:#0f3460;padding:20px;border-radius:8px;text-align:center;">
      {otp}
    </div>
    <p style="color:#aaa;font-size:13px;margin-top:16px;">
      Expires in <strong>10 minutes</strong>. Do not share.
    </p>
  </div>
</body></html>""", "html"))

    try:
        with smtplib.SMTP_SSL(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT) as srv:
            srv.login(MAIL_EMAIL, MAIL_PASSWORD)
            srv.sendmail(MAIL_EMAIL, recipient_email, msg.as_string())
        return True
    except smtplib.SMTPAuthenticationError:
        log.error("smtp_auth_failed — ensure App Password is set, not account password")
        return False
    except smtplib.SMTPException as exc:
        log.error("smtp_send_failed recipient=%s err=%s", recipient_email, exc)
        return False


# --- emergency classification --------------------------------------------

def classify_emergency_type(text: str) -> str:
    lowered = text.lower()
    for category, keywords in EMERGENCY_TYPE_KEYWORDS:
        if any(kw in lowered for kw in keywords):
            return category
    return "OTHER"


def extract_severity_level(ai_output: str, field_label: str) -> str:
    # field-scoped scan first; document-wide fallback handles non-compliant model output
    for line in ai_output.splitlines():
        if field_label.lower() not in line.lower():
            continue
        ll = line.lower()
        if "high"   in ll: return "HIGH"
        if "medium" in ll: return "MEDIUM"
        if "low"    in ll: return "LOW"

    lowered = ai_output.lower()
    if "high"   in lowered: return "HIGH"
    if "medium" in lowered: return "MEDIUM"
    if "low"    in lowered: return "LOW"
    return "MEDIUM"


def rewrite_type_line(ai_output: str, normalized_type: str) -> str:
    return "\n".join(
        f"🚑 Emergency Type: {normalized_type}" if "Type:" in line else line
        for line in ai_output.splitlines()
    )


# --- report persistence --------------------------------------------------

def report_is_duplicate(message: str, submitting_user: str) -> bool:
    try:
        with db_connection() as conn:
            row = conn.execute(
                "SELECT timestamp FROM reports "
                "WHERE user_id = ? AND LOWER(message) = LOWER(?) "
                "ORDER BY id DESC LIMIT 1",
                (submitting_user, message),
            ).fetchone()
        if not row:
            return False
        elapsed = (datetime.now() - datetime.strptime(row["timestamp"], TIMESTAMP_FORMAT)).total_seconds()
        return elapsed < OTP_EXPIRY_SECONDS
    except sqlite3.Error as exc:
        log.warning("duplicate_check_failed err=%s", exc)
        return False


def persist_emergency_report(
    message: str,
    ai_analysis: str,
    submitting_user: str = "anonymous",
    location: str = "Unknown",
) -> None:
    emergency_type = classify_emergency_type(ai_analysis + message)
    urgency        = extract_severity_level(ai_analysis, "urgency")
    panic_level    = extract_severity_level(ai_analysis, "panic")
    try:
        with db_connection() as conn:
            conn.execute(
                "INSERT INTO reports "
                "(message, location, type, urgency, panic, status, timestamp, user_id) "
                "VALUES (?, ?, ?, ?, ?, 'Pending', ?, ?)",
                (message, location, emergency_type, urgency, panic_level,
                 datetime.now().strftime(TIMESTAMP_FORMAT), submitting_user),
            )
            conn.commit()
    except sqlite3.Error as exc:
        log.error("persist_report_failed user=%s err=%s", submitting_user, exc)


# --- auth helpers --------------------------------------------------------

def get_session_user():
    email = session.get("user_id")
    if not email:
        return None
    try:
        with db_connection() as conn:
            return conn.execute(
                "SELECT * FROM users WHERE contact = ?", (email,)
            ).fetchone()
    except sqlite3.Error as exc:
        log.error("session_user_lookup_failed err=%s", exc)
        return None


def require_login(role: str | None = None):
    account = get_session_user()
    if not account:
        return None, redirect(url_for("login_page"))
    if role and account["role"] != role:
        return None, redirect(url_for("login_page"))
    return account, None


# --- page routes ---------------------------------------------------------

@app.route("/")
def home():
    return redirect(url_for("index"))

@app.route("/index")
def index():
    return render_template("index.html")

@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/signup")
def signup_page():
    return render_template("signup.html")

@app.route("/verify-otp")
def verify_otp_page():
    if "pending_user" not in session:
        return redirect(url_for("signup_page"))
    return render_template("verify_otp.html")

@app.route("/dashboard")
def dashboard():
    account, redir = require_login()
    if redir:
        return redir
    try:
        with db_connection() as conn:
            user_reports = conn.execute(
                "SELECT * FROM reports WHERE user_id = ? ORDER BY id DESC",
                (session["user_id"],),
            ).fetchall()
    except sqlite3.Error as exc:
        log.error("dashboard_fetch_failed err=%s", exc)
        user_reports = []
    return render_template("dashboard.html", user=account, reports=user_reports)

@app.route("/admin")
def admin_page():
    account, redir = require_login(role="admin")
    if redir:
        return redir
    try:
        with db_connection() as conn:
            all_reports = conn.execute("SELECT * FROM reports ORDER BY id DESC").fetchall()
    except sqlite3.Error as exc:
        log.error("admin_fetch_failed err=%s", exc)
        all_reports = []
    return render_template("admin.html", user=account, reports=all_reports)

@app.route("/responder")
def responder_page():
    account, redir = require_login(role="responder")
    if redir:
        return redir
    status_placeholders = ",".join("?" * len(ACTIVE_RESPONDER_STATUSES))
    try:
        with db_connection() as conn:
            active_reports = conn.execute(
                f"SELECT * FROM reports WHERE status IN ({status_placeholders}) ORDER BY id DESC",
                tuple(ACTIVE_RESPONDER_STATUSES),
            ).fetchall()
    except sqlite3.Error as exc:
        log.error("responder_fetch_failed err=%s", exc)
        active_reports = []
    return render_template("responder.html", user=account, reports=active_reports)


# --- auth API ------------------------------------------------------------

@app.route("/api/signup", methods=["POST"])
def api_signup():
    body     = request.json or {}
    name     = (body.get("name")     or "").strip()
    email    = (body.get("contact")  or "").strip().lower()
    password = (body.get("password") or "").strip()
    role     = body.get("role", "user")

    if not name or not email or not password:
        return jsonify({"error": "signup_fields_incomplete"}), 400
    if "@" not in email or "." not in email:
        return jsonify({"error": f"invalid_email_format: {email}"}), 400
    if len(password) < MIN_PASSWORD_LENGTH:
        return jsonify({"error": f"password_too_short min={MIN_PASSWORD_LENGTH}"}), 400

    try:
        with db_connection() as conn:
            existing_account = conn.execute(
                "SELECT id FROM users WHERE contact = ?", (email,)
            ).fetchone()
    except sqlite3.Error as exc:
        log.error("signup_db_lookup_failed err=%s", exc)
        return jsonify({"error": "db_error"}), 500

    if existing_account:
        return jsonify({"error": "contact_already_registered"}), 400

    if role not in ALLOWED_SIGNUP_ROLES:
        role = "user"

    issued_otp = str(random.randint(100_000, 999_999))
    pending_otps[email] = issued_otp
    session["pending_user"] = {
        "name":          name,
        "contact":       email,
        "password_hash": hash_password(password),
        "role":          role,
    }

    if not send_otp_email(email, issued_otp):
        return jsonify({"error": "otp_dispatch_failed"}), 500

    return jsonify({"message": f"otp_sent to={email}"}), 200


@app.route("/api/verify-otp", methods=["POST"])
def api_verify_otp():
    body          = request.json or {}
    submitted_otp = (body.get("otp") or "").strip()
    pending       = session.get("pending_user")

    if not pending:
        return jsonify({"error": "pending_session_missing or expired"}), 400

    email        = pending["contact"]
    expected_otp = pending_otps.get(email)

    if not expected_otp or submitted_otp != expected_otp:
        return jsonify({"error": "otp_mismatch"}), 400

    try:
        with db_connection() as conn:
            conn.execute(
                "INSERT INTO users (name, contact, password_hash, role, otp_verified) "
                "VALUES (?, ?, ?, ?, 1)",
                (pending["name"], email, pending["password_hash"], pending["role"]),
            )
            conn.commit()
    except sqlite3.Error as exc:
        log.error("otp_verify_db_write_failed err=%s", exc)
        return jsonify({"error": "db_error"}), 500

    pending_otps.pop(email, None)
    session.pop("pending_user", None)
    session["user_id"] = email

    landing = ROLE_LANDING_PAGE.get(pending["role"], "dashboard")
    return jsonify({"message": "account_created", "redirect": url_for(landing)}), 200


@app.route("/api/login", methods=["POST"])
def api_login():
    body     = request.json or {}
    email    = (body.get("contact")  or "").strip().lower()
    password = (body.get("password") or "").strip()

    if not email or not password:
        return jsonify({"error": "login_fields_incomplete"}), 400

    try:
        with db_connection() as conn:
            account = conn.execute(
                "SELECT * FROM users WHERE contact = ?", (email,)
            ).fetchone()
    except sqlite3.Error as exc:
        log.error("login_db_lookup_failed err=%s", exc)
        return jsonify({"error": "db_error"}), 500

    if not account or account["password_hash"] != hash_password(password):
        return jsonify({"error": "invalid_credentials"}), 401
    if not account["otp_verified"]:
        return jsonify({"error": "otp_verification_required"}), 403

    session["user_id"] = email
    landing = ROLE_LANDING_PAGE.get(account["role"], "dashboard")
    return jsonify({"redirect": url_for(landing)}), 200


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"redirect": url_for("login_page")}), 200


# --- analysis API --------------------------------------------------------

def _build_analysis_prompt(report_text: str) -> str:
    return (
        f'Analyze this emergency report:\n\n"{report_text}"\n\n'
        "Format your response EXACTLY like this:\n"
        "Type: [emergency type]\n"
        "Urgency: [HIGH / MEDIUM / LOW]\n"
        "Panic Level: [HIGH / MEDIUM / LOW]\n\n"
        "Suggested Action:\n1. [step]\n2. [step]\n3. [step]"
    )


def _call_gemini(prompt: str) -> str | None:
    if not gemini_model:
        return None
    try:
        return gemini_model.generate_content(prompt).text
    except Exception as exc:
        log.warning("gemini_inference_failed err=%s", exc)
        return None


def _call_groq(prompt: str) -> str | None:
    if not groq_client:
        return None
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content
    except Exception as exc:
        log.warning("groq_inference_failed err=%s", exc)
        return None


@app.route("/analyze", methods=["POST"])
def analyze():
    body     = request.json or {}
    message  = (body.get("text")     or "").strip()
    location = (body.get("location") or "Unknown").strip()
    reporter = session.get("user_id", "anonymous")

    if not message:
        return jsonify({"error": "report_message_empty"}), 400
    if len(message) < MIN_REPORT_LENGTH:
        return jsonify({"error": f"report_too_short min={MIN_REPORT_LENGTH}"}), 400
    if report_is_duplicate(message, reporter):
        return jsonify({"error": "duplicate_report_within_window"}), 409

    prompt    = _build_analysis_prompt(message)
    ai_output = _call_gemini(prompt) or _call_groq(prompt)

    if not ai_output:
        # both providers down — degrade gracefully rather than returning 503
        ai_output = OFFLINE_FALLBACK_ANALYSIS.format(
            emergency_type=classify_emergency_type(message)
        )

    normalized_type = classify_emergency_type(ai_output + message)
    clean_output    = rewrite_type_line(ai_output, normalized_type).replace("**", "")

    persist_emergency_report(message, ai_output, submitting_user=reporter, location=location)
    return jsonify({"output": clean_output}), 200


# --- reports & admin API -------------------------------------------------

@app.route("/reports", methods=["GET"])
def get_reports():
    try:
        with db_connection() as conn:
            all_reports = [dict(r) for r in conn.execute(
                "SELECT * FROM reports ORDER BY id DESC"
            ).fetchall()]
    except sqlite3.Error as exc:
        log.error("report_list_fetch_failed err=%s", exc)
        all_reports = []
    return jsonify({"total": len(all_reports), "reports": all_reports}), 200


@app.route("/update_status", methods=["POST"])
def update_status():
    account, redir = require_login()
    if redir:
        return redir
    if account["role"] not in ("admin", "responder"):
        return jsonify({"error": f"role_unauthorized role={account['role']}"}), 403

    body       = request.json or {}
    report_id  = body.get("id")
    new_status = (body.get("status") or "").strip()

    if not report_id:
        return jsonify({"error": "report_id_missing"}), 400
    if new_status not in VALID_STATUS_TRANSITIONS:
        return jsonify({"error": f"invalid_status_transition value={new_status}"}), 400

    try:
        with db_connection() as conn:
            conn.execute(
                "UPDATE reports SET status = ? WHERE id = ?", (new_status, int(report_id))
            )
            conn.commit()
    except sqlite3.Error as exc:
        log.error("status_update_failed report_id=%s err=%s", report_id, exc)
        return jsonify({"error": "db_error"}), 500

    return jsonify({"message": "status_updated"}), 200


@app.route("/api/admin/users", methods=["GET"])
def get_users():
    _, redir = require_login(role="admin")
    if redir:
        return redir
    try:
        with db_connection() as conn:
            registered_users = [dict(u) for u in conn.execute(
                "SELECT id, name, contact, role FROM users ORDER BY id DESC"
            ).fetchall()]
    except sqlite3.Error as exc:
        log.error("admin_user_list_failed err=%s", exc)
        registered_users = []
    return jsonify(registered_users), 200


@app.route("/api/admin/delete-user", methods=["POST"])
def delete_user():
    _, redir = require_login(role="admin")
    if redir:
        return redir

    body  = request.json or {}
    email = (body.get("contact") or "").strip().lower()

    if not email:
        return jsonify({"error": "contact_email_missing"}), 400
    if email == PROTECTED_ADMIN_EMAIL:
        return jsonify({"error": "root_admin_deletion_blocked"}), 403

    try:
        with db_connection() as conn:
            deleted_rows = conn.execute(
                "DELETE FROM users WHERE contact = ?", (email,)
            ).rowcount
            conn.commit()
    except sqlite3.Error as exc:
        log.error("user_deletion_failed email=%s err=%s", email, exc)
        return jsonify({"error": "db_error"}), 500

    if deleted_rows == 0:
        return jsonify({"error": f"user_not_found contact={email}"}), 404

    return jsonify({"message": "user_deleted"}), 200


if __name__ == "__main__":
    app.run(debug=True)