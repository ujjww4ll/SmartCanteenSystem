from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
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
            id            SERIAL PRIMARY KEY,
            name          TEXT,
            email         TEXT UNIQUE,
            password      TEXT,
            role          TEXT DEFAULT 'student',
            canteen_id    INTEGER,
            registered_at DOUBLE PRECISION DEFAULT 0,
            last_login    DOUBLE PRECISION DEFAULT 0
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
            ready_time    DOUBLE PRECISION
        )""")
    else:
        import sqlite3
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT,
            email         TEXT UNIQUE,
            password      TEXT,
            role          TEXT DEFAULT 'student',
            canteen_id    INTEGER,
            registered_at REAL DEFAULT 0,
            last_login    REAL DEFAULT 0
        )""")
        for col in ["registered_at", "last_login"]:
            try:
                cur.execute(f"ALTER TABLE users ADD COLUMN {col} REAL DEFAULT 0")
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
            ready_time    REAL
        )""")

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

    if not email.endswith(ALLOWED_DOMAIN):
        return jsonify({"error": f"Only {ALLOWED_DOMAIN} emails are allowed."}), 400

    role       = d.get("role", "student")
    canteen_id = d.get("canteen_id", None)
    now        = time.time()
    hashed     = generate_password_hash(d["password"])

    conn, ph = db_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"INSERT INTO users(name, email, password, role, canteen_id, registered_at) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph})",
            (d["name"], email, hashed, role, canteen_id, now)
        )
        conn.commit()
        conn.close()
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

    token = create_access_token(identity={
        "id":         u["id"],
        "role":       u["role"],
        "canteen_id": u["canteen_id"]
    })

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

# ── CREATE ORDER ──────────────────────────────────────────────────────────────
@app.route("/order/create", methods=["POST"])
@jwt_required()
def create_order():
    user = get_jwt_identity()
    if user["role"] != "student":
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
        (oid, d["canteen_id"], user["id"],
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
    user = get_jwt_identity()
    if user["role"] != "canteen":
        return jsonify({"error": "Only canteen staff can view this"}), 403

    conn, ph = db_conn()
    cur  = conn.cursor()
    cur.execute(f"SELECT * FROM orders WHERE canteen_id = {ph}", (user["canteen_id"],))
    rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        o = dict(zip([d[0] for d in cur.description], r)) if USE_POSTGRES else dict(r)
        o["items"]    = json.loads(o["items"])
        o["priority"] = round(calc_priority(o), 2)
        result.append(o)

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
        "expected_time": r["expected_time"]
    })

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
    cur.execute(f"SELECT status FROM orders WHERE order_id = {ph}", (oid,))
    row = cur.fetchone()
    if row:
        current = row[0] if USE_POSTGRES else row["status"]
        if next_status in FLOW.get(current, []):
            cur.execute(f"UPDATE orders SET status = {ph} WHERE order_id = {ph}", (next_status, oid))
            conn.commit()
    conn.close()

@app.route("/order/accept",    methods=["POST"])
@jwt_required()
def accept():
    set_status(request.json["order_id"], "ACCEPTED");   return jsonify({"ok": 1})

@app.route("/order/preparing", methods=["POST"])
@jwt_required()
def preparing():
    set_status(request.json["order_id"], "PREPARING");  return jsonify({"ok": 1})

@app.route("/order/ready",     methods=["POST"])
@jwt_required()
def ready():
    set_status(request.json["order_id"], "READY");      return jsonify({"ok": 1})

@app.route("/order/complete",  methods=["POST"])
@jwt_required()
def complete():
    set_status(request.json["order_id"], "COMPLETED");  return jsonify({"ok": 1})

# ── Run locally ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)