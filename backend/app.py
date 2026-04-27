import sqlite3
from flask import Flask, request, jsonify, render_template, redirect, session, url_for
from flask_cors import CORS
from groq import Groq
import os
import random
import hashlib
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import google.generativeai as genai

# =========================
# APP SETUP
# =========================
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "crisisense_secret_2024")
CORS(app)

# =========================
# DB PATH
# =========================
DB_PATH = os.getenv(
    "DB_PATH",
    os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "database.db")
    )
)
print(f"📂 DB path resolved to: {DB_PATH}")

# =========================
# EMAIL CONFIG
# =========================
# Add these 2 vars to Render → Environment:
#
#   MAIL_EMAIL    → your Gmail address  e.g.  yourapp@gmail.com
#   MAIL_PASSWORD → Gmail App Password (16 chars, no spaces)
#
# How to get a Gmail App Password (takes 2 minutes):
#   1. Go to myaccount.google.com → Security
#   2. Turn ON 2-Step Verification (if not already)
#   3. Search "App Passwords" in the search bar
#   4. App name: "CrisisSense" → click Create
#   5. Copy the 16-char password → paste as MAIL_PASSWORD in Render

MAIL_EMAIL    = os.getenv("MAIL_EMAIL", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")


def send_otp_email(to_email: str, otp: str) -> bool:
    """
    Send OTP to user's email via Gmail SMTP.
    Returns True on success, False on failure.
    Falls back to console print if email not configured.
    """
    if not MAIL_EMAIL or not MAIL_PASSWORD:
        # Graceful fallback: still works locally or if env vars not set yet
        print(f"⚠️  Email not configured. OTP for {to_email}: {otp}")
        return True

    try:
        msg            = MIMEMultipart("alternative")
        msg["Subject"] = "🚨 CrisisSense — Your OTP Code"
        msg["From"]    = MAIL_EMAIL
        msg["To"]      = to_email

        text_body = f"""
Your CrisisSense OTP code is: {otp}

This code expires in 10 minutes.
Do not share this code with anyone.

— CrisisSense Team
"""

        html_body = f"""
<html>
<body style="font-family: Arial, sans-serif; background: #1a1a2e; color: #eee; padding: 40px;">
  <div style="max-width: 480px; margin: auto; background: #16213e;
              border-radius: 12px; padding: 32px;">
    <h2 style="color: #e63946; margin-bottom: 4px;">🚨 CrisisSense</h2>
    <p style="color: #aaa; margin-top: 0;">Emergency Response Platform</p>
    <hr style="border-color: #333; margin: 24px 0;">
    <p style="font-size: 16px;">Your One-Time Password is:</p>
    <div style="font-size: 40px; font-weight: bold; letter-spacing: 10px;
                color: #e63946; background: #0f3460; padding: 20px;
                border-radius: 8px; text-align: center; margin: 20px 0;">
      {otp}
    </div>
    <p style="color: #aaa; font-size: 13px;">
      This code expires in <strong>10 minutes</strong>.<br>
      Do not share this code with anyone.
    </p>
  </div>
</body>
</html>
"""

        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(MAIL_EMAIL, MAIL_PASSWORD)
            server.sendmail(MAIL_EMAIL, to_email, msg.as_string())

        print(f"✅ OTP email sent to {to_email}")
        return True

    except smtplib.SMTPAuthenticationError:
        print("❌ Gmail auth failed — use an App Password, not your Gmail password")
        return False
    except Exception as e:
        print(f"❌ Failed to send OTP to {to_email}: {e}")
        return False


# =========================
# DATABASE HELPERS
# =========================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def init_db():
    print(f"🔧 Running init_db() on: {DB_PATH}")
    conn = get_db()
    try:
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
        conn.execute("""
            INSERT OR IGNORE INTO users (name, contact, password_hash, role, otp_verified)
            VALUES (?, ?, ?, ?, ?)
        """, ("Admin", "admin@crisis.com", _hash("admin123"), "admin", 1))
        conn.execute("""
            INSERT OR IGNORE INTO users (name, contact, password_hash, role, otp_verified)
            VALUES (?, ?, ?, ?, ?)
        """, ("Responder One", "responder@crisis.com", _hash("resp123"), "responder", 1))
        conn.commit()
        print(f"✅ Database initialised at: {DB_PATH}")
    except Exception as e:
        print(f"❌ Database init error: {e}")
        raise
    finally:
        conn.close()

with app.app_context():
    init_db()

# =========================
# API KEYS
# =========================
GROQ_API_KEY   = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel("gemini-1.5-flash")
        print("✅ Google Gemini API loaded.")
    except Exception as e:
        gemini_model = None
        print(f"⚠️  Gemini init failed: {e}")
else:
    gemini_model = None
    print("⚠️  GEMINI_API_KEY not set.")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
if GROQ_API_KEY:
    print("✅ GROQ_API_KEY loaded.")

# =========================
# IN-MEMORY STORAGE
# =========================
pending_otps: dict[str, str] = {}

# =========================
# NORMALIZATION HELPERS
# =========================
def normalize_type(raw_text: str) -> str:
    text = raw_text.lower()
    keyword_map = [
        ("FIRE",             ["fire", "burn", "smoke", "aag", "blaze", "flames"]),
        ("MEDICAL",          ["injury", "bleeding", "faint", "ambulance", "heart",
                              "breath", "unconscious", "medical"]),
        ("SECURITY",         ["attack", "robbery", "threat", "knife", "gun",
                              "assault", "theft", "steal"]),
        ("NATURAL DISASTER", ["earthquake", "flood", "storm", "tsunami",
                              "landslide", "cyclone"]),
        ("ACCIDENT",         ["accident", "crash", "fall", "collision", "vehicle"]),
    ]
    for category, keywords in keyword_map:
        for kw in keywords:
            if kw in text:
                return category
    return "OTHER"

def normalize_level(raw_text: str, field: str) -> str:
    text = raw_text.lower()
    for line in raw_text.splitlines():
        if field.lower() in line.lower():
            if "high"   in line.lower(): return "HIGH"
            if "medium" in line.lower(): return "MEDIUM"
            if "low"    in line.lower(): return "LOW"
    if "high"   in text: return "HIGH"
    if "medium" in text: return "MEDIUM"
    if "low"    in text: return "LOW"
    return "MEDIUM"

def inject_normalized_type(raw_output: str, normalized_type: str) -> str:
    lines  = raw_output.splitlines()
    result = []
    for line in lines:
        if "Type:" in line:
            result.append(f"🚑 Emergency Type: {normalized_type}")
        else:
            result.append(line)
    return "\n".join(result)

# =========================
# DUPLICATE PREVENTION
# =========================
def is_duplicate(text: str, user_id: str) -> bool:
    try:
        conn   = get_db()
        result = conn.execute("""
            SELECT timestamp FROM reports
            WHERE user_id = ? AND LOWER(message) = LOWER(?)
            ORDER BY id DESC LIMIT 1
        """, (user_id, text)).fetchone()
        conn.close()
        if result:
            ts   = datetime.strptime(result["timestamp"], "%Y-%m-%d %H:%M:%S")
            diff = (datetime.now() - ts).total_seconds()
            return diff < 60
    except Exception as e:
        print(f"⚠️  is_duplicate check failed: {e}")
    return False

def save_report(text: str, output_text: str,
                user_id: str = "anonymous", location: str = "Unknown"):
    detected_type = normalize_type(output_text + text)
    urgency       = normalize_level(output_text, "urgency")
    panic         = normalize_level(output_text, "panic")
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO reports (message, location, type, urgency, panic,
                                 status, timestamp, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            text, location, detected_type, urgency, panic,
            "Pending",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            user_id
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ save_report failed: {e}")

# =========================
# AUTH HELPERS
# =========================
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    try:
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE contact = ?", (uid,)).fetchone()
        conn.close()
        return user
    except Exception as e:
        print(f"❌ current_user DB error: {e}")
        return None

def require_login(role=None):
    user = current_user()
    if not user:
        return None, redirect(url_for("login_page"))
    if role and user["role"] != role:
        return None, redirect(url_for("login_page"))
    return user, None

# =========================
# PAGES — AUTH
# =========================
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

# =========================
# API — AUTH
# =========================
@app.route("/api/signup", methods=["POST"])
def api_signup():
    data     = request.json or {}
    name     = (data.get("name")     or "").strip()
    contact  = (data.get("contact")  or "").strip().lower()
    password = (data.get("password") or "").strip()
    role     = data.get("role", "user")

    if not name or not contact or not password:
        return jsonify({"error": "All fields are required"}), 400
    if "@" not in contact or "." not in contact:
        return jsonify({"error": "Please enter a valid email address"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    try:
        conn     = get_db()
        existing = conn.execute(
            "SELECT id FROM users WHERE contact = ?", (contact,)
        ).fetchone()
        conn.close()
    except Exception as e:
        print(f"❌ DB error in /api/signup: {e}")
        return jsonify({"error": "Database error. Please try again."}), 500

    if existing:
        return jsonify({"error": "Account already exists. Please log in."}), 400
    if role not in ("user", "responder"):
        role = "user"

    otp = str(random.randint(100000, 999999))
    pending_otps[contact] = otp
    session["pending_user"] = {
        "name":          name,
        "contact":       contact,
        "password_hash": _hash(password),
        "role":          role,
    }

    sent = send_otp_email(contact, otp)
    if not sent:
        return jsonify({
            "error": "Failed to send OTP email. Please check MAIL_EMAIL and MAIL_PASSWORD in Render environment."
        }), 500

    return jsonify({"message": f"OTP sent to {contact}. Check your inbox (and spam folder)."}), 200


@app.route("/api/verify-otp", methods=["POST"])
def api_verify_otp():
    data    = request.json or {}
    otp_in  = (data.get("otp") or "").strip()
    pending = session.get("pending_user")

    if not pending:
        return jsonify({"error": "Session expired. Please sign up again."}), 400

    contact      = pending["contact"]
    expected_otp = pending_otps.get(contact)

    if not expected_otp or otp_in != expected_otp:
        return jsonify({"error": "Invalid OTP. Please try again."}), 400

    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO users (name, contact, password_hash, role, otp_verified)
            VALUES (?, ?, ?, ?, ?)
        """, (
            pending["name"], contact,
            pending["password_hash"], pending["role"], 1,
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ DB error in /api/verify-otp: {e}")
        return jsonify({"error": "Database error. Please try again."}), 500

    pending_otps.pop(contact, None)
    session.pop("pending_user", None)
    session["user_id"] = contact

    role         = pending["role"]
    redirect_url = url_for("dashboard") if role == "user" else url_for("responder_page")
    return jsonify({"message": "Account created!", "redirect": redirect_url}), 200


@app.route("/api/login", methods=["POST"])
def api_login():
    data     = request.json or {}
    contact  = (data.get("contact")  or "").strip().lower()
    password = (data.get("password") or "").strip()

    if not contact or not password:
        return jsonify({"error": "Email and password are required"}), 400

    try:
        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE contact = ?", (contact,)
        ).fetchone()
        conn.close()
    except Exception as e:
        print(f"❌ DB error in /api/login: {e}")
        return jsonify({"error": "Database error. Please try again."}), 500

    if not user or user["password_hash"] != _hash(password):
        return jsonify({"error": "Invalid credentials"}), 401
    if not user["otp_verified"]:
        return jsonify({"error": "Please verify your OTP first"}), 403

    session["user_id"] = contact
    redirect_map = {
        "admin":     url_for("admin_page"),
        "responder": url_for("responder_page"),
        "user":      url_for("dashboard"),
    }
    return jsonify({"redirect": redirect_map.get(user["role"], url_for("dashboard"))}), 200


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"redirect": url_for("login_page")}), 200

# =========================
# PAGES — DASHBOARDS
# =========================
@app.route("/dashboard")
def dashboard():
    user, resp = require_login()
    if resp:
        return resp
    try:
        conn         = get_db()
        user_reports = conn.execute(
            "SELECT * FROM reports WHERE user_id = ? ORDER BY id DESC",
            (session["user_id"],)
        ).fetchall()
        conn.close()
    except Exception as e:
        print(f"❌ DB error in /dashboard: {e}")
        user_reports = []
    return render_template("dashboard.html", user=user, reports=user_reports)

@app.route("/admin")
def admin_page():
    user, resp = require_login(role="admin")
    if resp:
        return resp
    try:
        conn    = get_db()
        reports = conn.execute("SELECT * FROM reports ORDER BY id DESC").fetchall()
        conn.close()
    except Exception as e:
        print(f"❌ DB error in /admin: {e}")
        reports = []
    return render_template("admin.html", user=user, reports=reports)

@app.route("/responder")
def responder_page():
    user, resp = require_login(role="responder")
    if resp:
        return resp
    try:
        conn    = get_db()
        reports = conn.execute("""
            SELECT * FROM reports
            WHERE status IN ('Pending','Dispatched','Accepted','On The Way')
            ORDER BY id DESC
        """).fetchall()
        conn.close()
    except Exception as e:
        print(f"❌ DB error in /responder: {e}")
        reports = []
    return render_template("responder.html", user=user, reports=reports)

# =========================
# API — ANALYZE
# =========================
@app.route("/analyze", methods=["POST"])
def analyze():
    data     = request.json or {}
    text     = (data.get("text")     or "").strip()
    location = (data.get("location") or "Unknown").strip()
    user_id  = session.get("user_id", "anonymous")

    if not text:
        return jsonify({"error": "Please enter a message"}), 400
    if len(text) < 10:
        return jsonify({"error": "Message too short. Please describe the emergency."}), 400
    if is_duplicate(text, user_id):
        return jsonify({"error": "Duplicate report detected. Your report was already submitted."}), 409

    prompt = f"""
Analyze this emergency report:

"{text}"

Format your response EXACTLY like this:
Type: [emergency type]
Urgency: [HIGH / MEDIUM / LOW]
Panic Level: [HIGH / MEDIUM / LOW]

Suggested Action:
1. [step]
2. [step]
3. [step]
"""

    raw_output = None

    if gemini_model:
        try:
            print("🌐 Using Google Gemini...")
            response   = gemini_model.generate_content(prompt)
            raw_output = response.text
        except Exception as e:
            print("❌ Gemini failed:", e)

    if not raw_output and client:
        try:
            print("📡 Using Groq fallback...")
            response   = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
            )
            raw_output = response.choices[0].message.content
        except Exception as e:
            print("❌ Groq failed:", e)

    if not raw_output:
        t          = normalize_type(text)
        raw_output = (
            f"Type: {t}\nUrgency: MEDIUM\nPanic Level: MEDIUM\n\n"
            "Suggested Action:\n1. Stay calm\n2. Contact emergency services\n3. Wait for help"
        )

    normalized_output = inject_normalized_type(raw_output, normalize_type(raw_output + text))
    clean_output      = normalized_output.replace("**", "")
    save_report(text, raw_output, user_id=user_id, location=location)

    return jsonify({"output": clean_output}), 200

# =========================
# API — REPORTS
# =========================
@app.route("/reports", methods=["GET"])
def get_reports():
    try:
        conn    = get_db()
        rows    = conn.execute("SELECT * FROM reports ORDER BY id DESC").fetchall()
        conn.close()
        reports = [dict(r) for r in rows]
    except Exception as e:
        print(f"❌ DB error in /reports: {e}")
        reports = []
    return jsonify({"total": len(reports), "reports": reports}), 200

    @app.route("/api/admin/delete-user", methods=["POST"])
    def delete_user():
        user, resp = require_login(role="admin")
        if resp:
            return resp

        data = request.json or {}
        contact = (data.get("contact") or "").strip().lower()

        if not contact:
            return jsonify({"error": "Email required"}), 400

        if contact == "admin@crisis.com":
            return jsonify({"error": "Cannot delete admin"}), 403

        conn = get_db()
        deleted = conn.execute(
            "DELETE FROM users WHERE contact = ?",
            (contact,)
        ).rowcount

        conn.commit()
        conn.close()

        if deleted == 0:
            return jsonify({"error": "User not found"}), 404

        return jsonify({"message": "User deleted"}), 200

@app.route("/update_status", methods=["POST"])
def update_status():
    user, resp = require_login()
    if resp:
        return resp
    if current_user()["role"] not in ("admin", "responder"):
        return jsonify({"error": "Unauthorized"}), 403

    data       = request.json or {}
    idx        = data.get("id")
    new_status = (data.get("status") or "").strip()

    valid_statuses = ["Pending", "Dispatched", "Accepted", "On The Way", "Resolved"]
    if new_status not in valid_statuses:
        return jsonify({"error": "Invalid status"}), 400

    try:
        conn = get_db()
        conn.execute("UPDATE reports SET status = ? WHERE id = ?", (new_status, int(idx)))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ DB error in /update_status: {e}")
        return jsonify({"error": "Database error. Please try again."}), 500

    return jsonify({"message": "Status updated"}), 200

# =========================
if __name__ == "__main__":
    app.run(debug=True)