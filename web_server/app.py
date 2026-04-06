from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity, get_jwt, get_jwt
from werkzeug.security import generate_password_hash, check_password_hash
import time, json, os

app = Flask(__name__)

# ── CORS ──────────────────────────────────────────────────────────────────────
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*")
CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)

# ── JWT: reads JWT_SECRET_KEY or API_SECRET (Railway sets API_SECRET) ─────────
app.config["JWT_SECRET_KEY"] = (
    os.environ.get("JWT_SECRET_KEY") or
    os.environ.get("API_SECRET") or
    "local-dev-secret-change-in-prod"
)
jwt = JWTManager(app)

# ── Database: PostgreSQL on Railway, SQLite locally ───────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Railway sometimes gives postgres:// — psycopg2 needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

USE_POSTGRES = bool(DATABASE_URL)

def db_conn():
    """Return a connection + placeholder character for the active DB."""
    if USE_POSTGRES:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn, "%s"
    else:
        import sqlite3
        db_path = os.path.join(os.path.dirname(__file__), "canteen.db")
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn, "?"

def row_to_dict(row, cursor=None):
    """Convert a DB row to a plain dict regardless of DB engine."""
    if USE_POSTGRES:
        cols = [desc[0] for desc in cursor.description]
        return dict(zip(cols, row))
    else:
        return dict(row)

# ── Init DB ───────────────────────────────────────────────────────────────────
def init_db():
    conn, ph = db_conn()
    cur = conn.cursor()

    if USE_POSTGRES:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id             SERIAL PRIMARY KEY,
            name           TEXT,
            email          TEXT UNIQUE,
            password       TEXT,
            role           TEXT DEFAULT 'student',
            canteen_id     INTEGER,
            phone          TEXT,
            enrollment_no  TEXT,
            phone_verified INTEGER DEFAULT 0,
            registered_at  DOUBLE PRECISION DEFAULT 0,
            last_login     DOUBLE PRECISION DEFAULT 0,
            credits        DOUBLE PRECISION DEFAULT 0
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS canteens(
            id   SERIAL PRIMARY KEY,
            name TEXT
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS orders(
            order_id      BIGINT PRIMARY KEY,
            canteen_id    INTEGER,
            student_id    INTEGER,
            items         TEXT,
            items_count   INTEGER,
            price         DOUBLE PRECISION,
            expected_time INTEGER,
            status        TEXT,
            created_time  DOUBLE PRECISION,
            accepted_time DOUBLE PRECISION,
            ready_time    DOUBLE PRECISION,
            completed_time DOUBLE PRECISION,
            late_penalty  DOUBLE PRECISION DEFAULT 0
        )""")
        # Add missing columns if they don't exist
        try:
            cur.execute("ALTER TABLE users ADD COLUMN credits DOUBLE PRECISION DEFAULT 0")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE orders ADD COLUMN completed_time DOUBLE PRECISION")
            cur.execute("ALTER TABLE orders ADD COLUMN late_penalty DOUBLE PRECISION DEFAULT 0")
        except Exception:
            pass
    else:
        import sqlite3
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT,
            email          TEXT UNIQUE,
            password       TEXT,
            role           TEXT DEFAULT 'student',
            canteen_id     INTEGER,
            phone          TEXT,
            enrollment_no  TEXT,
            phone_verified INTEGER DEFAULT 0,
            registered_at  REAL DEFAULT 0,
            last_login     REAL DEFAULT 0,
            credits        REAL DEFAULT 0
        )""")
        # Migrate existing tables safely
        for col, typ in [("registered_at","REAL DEFAULT 0"),("last_login","REAL DEFAULT 0"),
                         ("phone","TEXT"),("enrollment_no","TEXT"),("phone_verified","INTEGER DEFAULT 0"),
                         ("credits","REAL DEFAULT 0")]:
            try:
                cur.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
            except Exception:
                pass

        cur.execute("""
        CREATE TABLE IF NOT EXISTS canteens(
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS orders(
            order_id      INTEGER PRIMARY KEY,
            canteen_id    INTEGER,
            student_id    INTEGER,
            items         TEXT,
            items_count   INTEGER,
            price         REAL,
            expected_time INTEGER,
            status        TEXT,
            created_time  REAL,
            accepted_time REAL,
            ready_time    REAL,
            completed_time REAL,
            late_penalty  REAL DEFAULT 0
        )""")
        # Add missing columns
        for col, typ in [("completed_time","REAL"),("late_penalty","REAL DEFAULT 0")]:
            try:
                cur.execute(f"ALTER TABLE orders ADD COLUMN {col} {typ}")
            except Exception:
                pass

    conn.commit()
    conn.close()

init_db()

# ── Seed canteens ─────────────────────────────────────────────────────────────
def seed():
    conn, ph = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM canteens")
    row = cur.fetchone()
    count = row[0] if USE_POSTGRES else row[0]
    if count == 0:
        for name in ["Maggi Hotspot", "Southern Stories", "SnapEats", "Infinity Kitchen"]:
            cur.execute(f"INSERT INTO canteens(name) VALUES ({ph})", (name,))
        conn.commit()
    conn.close()

seed()

# ── In-memory OTP store {email: {otp, expires, verified}} ───────────────────
import random, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
otp_store = {}

SMTP_EMAIL    = os.environ.get("SMTP_EMAIL", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

def send_email(to_addr, otp):
    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"🍽️ B.U Eats — Your Verification Code: {otp}"
    msg["From"]    = f"B.U Eats <{SMTP_EMAIL}>"
    msg["To"]      = to_addr

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:32px;background:#0f0f1a;border-radius:16px;">
      <div style="text-align:center;margin-bottom:24px;">
        <div style="font-size:48px;">&#x1F37D;&#xFE0F;</div>
        <h2 style="color:#ff9f4a;margin:8px 0;font-size:22px;">B.U Eats Verification</h2>
        <p style="color:#9998aa;font-size:13px;">Use the code below to verify your email</p>
      </div>
      <div style="background:#1e1d2e;border-radius:12px;padding:24px;text-align:center;margin:20px 0;">
        <div style="font-size:42px;font-weight:900;letter-spacing:10px;color:#ffe393;font-family:monospace;">{otp}</div>
        <p style="color:#9998aa;font-size:12px;margin-top:8px;">Valid for <b style="color:#ff9f4a;">10 minutes</b>. Do not share with anyone.</p>
      </div>
      <p style="color:#9998aa;font-size:11px;text-align:center;">If you didn't request this, ignore this email.</p>
    </div>"""

    msg.attach(MIMEText(html, "html"))
    
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as s:
            s.starttls()
            s.login(SMTP_EMAIL, SMTP_PASSWORD)
            s.sendmail(SMTP_EMAIL, to_addr, msg.as_string())
        return True
    except Exception as e:
        print(f"Email send error: {e}")
        return False

# ── SEND OTP (email-based) ──────────────────────────────────────────────────────
@app.route("/send-otp", methods=["POST"])
def send_otp():
    email = (request.json.get("email") or "").strip().lower()
    if not email.endswith("@bennett.edu.in"):
        return jsonify({"error": "Only @bennett.edu.in emails are accepted."}), 400

    otp = str(random.randint(100000, 999999))
    otp_store[email] = {"otp": otp, "expires": time.time() + 600, "verified": False}

    # If SMTP not configured, return dev mode
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        return jsonify({"msg": "DEV MODE: OTP generated (no SMTP configured)",
                        "dev_mode": True, "otp": otp}), 200
    
    # Try to send real email
    try:
        send_email(email, otp)
        return jsonify({"msg": f"OTP sent to {email}", "dev_mode": False}), 200
    except Exception as e:
        print(f"Email error: {e}")
        return jsonify({"error": f"Email service failed: {str(e)}"}), 500

# ── VERIFY OTP ────────────────────────────────────────────────────────────────
@app.route("/verify-otp", methods=["POST"])
def verify_otp():
    email = (request.json.get("email") or "").strip().lower()
    otp   = (request.json.get("otp")   or "").strip()

    record = otp_store.get(email)
    if not record:
        return jsonify({"error": "No OTP found for this email. Request a new one."}), 400
    if time.time() > record["expires"]:
        otp_store.pop(email, None)
        return jsonify({"error": "OTP expired. Request a new one."}), 400
    if record["otp"] != otp:
        return jsonify({"error": "Wrong OTP. Try again."}), 400

    otp_store[email]["verified"] = True
    return jsonify({"msg": "Email verified!"})

# ── Priority algorithm ────────────────────────────────────────────────────────
def calc_priority(o):
    waiting = time.time() - o["created_time"]
    return (o["expected_time"] * 2) + (o["items_count"] * 3) - (waiting / 10)

# ── Health check ──────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status":   "ok",
        "service":  "B.U Eats API",
        "db":       "postgres" if USE_POSTGRES else "sqlite",
        "version":  "2.0"
    })

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok"})

# ── Domain restriction ────────────────────────────────────────────────────────
ALLOWED_DOMAIN = "@bennett.edu.in"

# ── REGISTER ──────────────────────────────────────────────────────────────────
@app.route("/register", methods=["POST"])
def register():
    d     = request.json
    email = (d.get("email") or "").strip().lower()

    role       = d.get("role", "student")
    canteen_id = d.get("canteen_id", None)
    phone      = (d.get("phone") or "").strip()

    if role == "student" and not email.endswith(ALLOWED_DOMAIN):
        return jsonify({"error": f"Only {ALLOWED_DOMAIN} emails are allowed for students."}), 400

    # Validate email OTP for students
    if role == "student":
        otp_record = otp_store.get(email)
        if not otp_record or not otp_record.get("verified"):
            return jsonify({"error": "Email not verified. Please verify your @bennett.edu.in email first."}), 400

    # Extract enrollment number from email prefix
    enrollment_no = email.split("@")[0].upper() if role == "student" else None

    now    = time.time()
    hashed = generate_password_hash(d["password"])

    conn, ph = db_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"INSERT INTO users(name, email, password, role, canteen_id, phone, enrollment_no, phone_verified, registered_at) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (d["name"], email, hashed, role, canteen_id, phone if role=="student" else None,
             enrollment_no, 1 if role=="student" else 0, now)
        )
        conn.commit()
        conn.close()
        # Clear OTP after successful registration
        if role == "student":
            otp_store.pop(email, None)
        return jsonify({"msg": "registered"})

    except Exception as e:
        conn.rollback()
        conn.close()
        err = str(e)
        if "unique" in err.lower() or "duplicate" in err.lower():
            return jsonify({"error": "Email already registered"}), 400
        return jsonify({"error": err}), 500

# ── LOGIN ─────────────────────────────────────────────────────────────────────
@app.route("/login", methods=["POST"])
def login():
    d     = request.json
    email = (d.get("email") or "").strip().lower()

    conn, ph = db_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM users WHERE email = {ph}", (email,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return jsonify({"msg": "Invalid email or password"}), 401

    u = row_to_dict(row, cur)

    if not check_password_hash(u["password"], d["password"]):
        conn.close()
        return jsonify({"msg": "Invalid email or password"}), 401

    # Record last login
    cur.execute(f"UPDATE users SET last_login = {ph} WHERE id = {ph}", (time.time(), u["id"]))
    conn.commit()
    conn.close()

    token = create_access_token(
        identity=str(u["id"]),
        additional_claims={
            "role": u["role"],
            "canteen_id": u["canteen_id"]
        }
    )

    return jsonify({
        "access_token": token,
        "user": {
            "id":         u["id"],
            "name":       u["name"],
            "email":      email,
            "role":       u["role"],
            "canteen_id": u["canteen_id"]
        }
    })

# ── GET CANTEENS (for registration/login dropdowns) ──────────────────────────
@app.route("/canteens", methods=["GET"])
def get_canteens():
    conn, ph = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM canteens ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    
    canteens = []
    for row in rows:
        canteens.append({
            "id": row[0] if USE_POSTGRES else row["id"],
            "name": row[1] if USE_POSTGRES else row["name"]
        })
    
    return jsonify({"canteens": canteens})

# ── CREATE ORDER ──────────────────────────────────────────────────────────────
@app.route("/order/create", methods=["POST"])
@jwt_required()
def create_order():
    user_id = get_jwt_identity()
    claims = get_jwt()
    
    if claims.get("role") != "student":
        return jsonify({"error": "Only students can place orders"}), 403

    d    = request.json
    conn, ph = db_conn()
    cur  = conn.cursor()

    oid         = int(time.time() * 1000)
    items       = d["items"]
    total_price = sum(i["price"] for i in items)
    total_time  = sum(i["time"]  for i in items)

    cur.execute(
        f"INSERT INTO orders VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
        (oid, d["canteen_id"], int(user_id),
         json.dumps(items), len(items),
         total_price, total_time,
         "WAITING", time.time(), None, None)
    )
    conn.commit()
    conn.close()
    return jsonify({"order_id": oid})

# ── CANTEEN ORDERS ────────────────────────────────────────────────────────────
@app.route("/canteen/orders", methods=["GET"])
@jwt_required()
def canteen_orders():
    user_id = get_jwt_identity()
    claims = get_jwt()
    
    if claims.get("role") != "canteen":
        return jsonify({"error": "Only canteen staff can view this"}), 403

    conn, ph = db_conn()
    cur  = conn.cursor()
    query = f"""
        SELECT o.*,
               u.name          AS student_name,
               u.enrollment_no AS enrollment_no,
               u.phone         AS student_phone
        FROM orders o
        LEFT JOIN users u ON o.student_id = u.id
        WHERE o.canteen_id = {ph}
    """
    cur.execute(query, (claims.get("canteen_id"),))
    rows = cur.fetchall()
    
    result = []
    for r in rows:
        o = row_to_dict(r, cur)
        o["items"]    = json.loads(o["items"])
        o["priority"] = round(calc_priority(o), 2)
        result.append(o)
    
    conn.close()

    result.sort(key=lambda x: x["priority"])
    for i, o in enumerate(result):
        o["queue_position"] = i + 1

    return jsonify(result)

# ── ORDER STATUS ──────────────────────────────────────────────────────────────
@app.route("/order/status/<int:oid>", methods=["GET"])
@jwt_required()
def order_status(oid):
    conn, ph = db_conn()
    cur  = conn.cursor()
    cur.execute(f"SELECT * FROM orders WHERE order_id = {ph}", (oid,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "Order not found"}), 404

    r = row_to_dict(row, cur)
    return jsonify({
        "status":        r["status"],
        "items":         json.loads(r["items"]),
        "price":         r["price"],
        "expected_time": r["expected_time"],
        "created_time":  r["created_time"],
        "accepted_time": r["accepted_time"],
        "ready_time":    r["ready_time"],
        "completed_time": r["completed_time"],
        "late_penalty":  r["late_penalty"]
    })

# ── GET STUDENT CREDITS ───────────────────────────────────────────────────────
@app.route("/student/credits", methods=["GET"])
@jwt_required()
def get_credits():
    try:
        user_id = get_jwt_identity()
        claims = get_jwt()
        
        if claims.get("role") != "student":
            return jsonify({"error": "Only students can view credits"}), 403
        
        conn, ph = db_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT credits FROM users WHERE id = {ph}", (int(user_id),))
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return jsonify({"credits": 0}), 200
        
        credits = row[0] if USE_POSTGRES else row["credits"]
        return jsonify({"credits": float(credits or 0)})
    except Exception as e:
        print(f"Error in get_credits: {e}")
        return jsonify({"error": str(e)}), 500

# ── GET ORDER HISTORY ─────────────────────────────────────────────────────────
@app.route("/student/orders", methods=["GET"])
@jwt_required()
def get_order_history():
    try:
        user_id = get_jwt_identity()
        claims = get_jwt()
        
        if claims.get("role") != "student":
            return jsonify({"error": "Only students can view orders"}), 403
        
        conn, ph = db_conn()
        cur = conn.cursor()
        query = f"SELECT * FROM orders WHERE student_id = {ph} ORDER BY created_time DESC LIMIT 10"
        cur.execute(query, (int(user_id),))
        rows = cur.fetchall()
        
        result = []
        for row in rows:
            try:
                o = row_to_dict(row, cur)
                o["items"] = json.loads(o.get("items", "[]"))
                result.append(o)
            except Exception as e:
                print(f"Error converting row: {e}")
                continue
        
        conn.close()
        return jsonify(result)
    except Exception as e:
        print(f"Error in get_order_history: {e}")
        return jsonify({"error": str(e)}), 500

# ── UPDATE ORDER STATUS & CALCULATE PENALTIES ─────────────────────────────────
@app.route("/order/status/update/<int:oid>", methods=["POST"])
@jwt_required()
def update_order_status(oid):
    user_id = get_jwt_identity()
    claims = get_jwt()
    
    if claims.get("role") != "canteen":
        return jsonify({"error": "Only canteen staff can update orders"}), 403
    
    d = request.json
    next_status = d.get("status")
    
    if not next_status:
        return jsonify({"error": "Status required"}), 400
    
    conn, ph = db_conn()
    cur = conn.cursor()
    
    # Get current order
    cur.execute(f"SELECT * FROM orders WHERE order_id = {ph}", (oid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Order not found"}), 404
    
    order = row_to_dict(row, cur)
    
    # Calculate late penalty if completing
    late_penalty = 0
    now = time.time()
    
    if next_status == "COMPLETED":
        order["completed_time"] = now
        # Calculate minutes late
        expected_completion = order["created_time"] + (order["expected_time"] * 60)
        if now > expected_completion:
            late_mins = int((now - expected_completion) / 60)
            late_penalty = late_mins * 1  # ₹1 per minute
            # Add credits to student
            cur.execute(f"UPDATE users SET credits = credits + {ph} WHERE id = {ph}", 
                       (late_penalty, order["student_id"]))
    
    # Update order status
    status_col = "accepted_time" if next_status == "ACCEPTED" else \
                 "ready_time" if next_status == "READY" else \
                 "completed_time" if next_status == "COMPLETED" else None
    
    if status_col:
        cur.execute(f"UPDATE orders SET status = {ph}, {status_col} = {ph}, late_penalty = {ph} WHERE order_id = {ph}",
                   (next_status, now, late_penalty, oid))
    else:
        cur.execute(f"UPDATE orders SET status = {ph}, late_penalty = {ph} WHERE order_id = {ph}",
                   (next_status, late_penalty, oid))
    
    conn.commit()
    conn.close()
    
    return jsonify({"msg": "Order updated", "late_penalty": late_penalty})

# ── STATE MACHINE ─────────────────────────────────────────────────────────────
FLOW = {
    "WAITING":   ["ACCEPTED"],
    "ACCEPTED":  ["PREPARING"],
    "PREPARING": ["READY"],
    "READY":     ["COMPLETED"],
    "COMPLETED": []
}

def set_status(oid, next_status):
    conn, ph = db_conn()
    cur  = conn.cursor()
    cur.execute(f"SELECT status, created_time, expected_time, student_id FROM orders WHERE order_id = {ph}", (oid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    
    if USE_POSTGRES:
        cols = [desc[0] for desc in cur.description]
        order = dict(zip(cols, row))
    else:
        order = dict(row)
    
    current = order["status"]
    if next_status not in FLOW.get(current, []):
        conn.close()
        return None
    
    now = time.time()
    late_penalty = 0
    
    # Calculate late penalty if completing
    if next_status == "COMPLETED":
        expected_completion = order["created_time"] + (order["expected_time"] * 60)
        if now > expected_completion:
            late_mins = int((now - expected_completion) / 60)
            late_penalty = late_mins * 1  # ₹1 per minute
            # Add credits to student
            cur.execute(f"UPDATE users SET credits = credits + {ph} WHERE id = {ph}", 
                       (late_penalty, order["student_id"]))
    
    # Update with timestamp
    if next_status == "ACCEPTED":
        cur.execute(f"UPDATE orders SET status = {ph}, accepted_time = {ph} WHERE order_id = {ph}", 
                   (next_status, now, oid))
    elif next_status == "READY":
        cur.execute(f"UPDATE orders SET status = {ph}, ready_time = {ph} WHERE order_id = {ph}", 
                   (next_status, now, oid))
    elif next_status == "COMPLETED":
        cur.execute(f"UPDATE orders SET status = {ph}, completed_time = {ph}, late_penalty = {ph} WHERE order_id = {ph}",
                   (next_status, now, late_penalty, oid))
    else:
        cur.execute(f"UPDATE orders SET status = {ph} WHERE order_id = {ph}", (next_status, oid))
    
    conn.commit()
    conn.close()
    return {"late_penalty": late_penalty}

@app.route("/order/accept",    methods=["POST"])
@jwt_required()
def accept():
    result = set_status(request.json["order_id"], "ACCEPTED")
    return jsonify({"ok": 1} if result is not None else {"error": "Failed"})

@app.route("/order/preparing", methods=["POST"])
@jwt_required()
def preparing():
    result = set_status(request.json["order_id"], "PREPARING")
    return jsonify({"ok": 1} if result is not None else {"error": "Failed"})

@app.route("/order/ready",     methods=["POST"])
@jwt_required()
def ready():
    result = set_status(request.json["order_id"], "READY")
    return jsonify({"ok": 1} if result is not None else {"error": "Failed"})

@app.route("/order/complete",  methods=["POST"])
@jwt_required()
def complete():
    result = set_status(request.json["order_id"], "COMPLETED")
    return jsonify({"ok": result.get("late_penalty", 0)} if result else {"error": "Failed"})

# ── Run locally ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)