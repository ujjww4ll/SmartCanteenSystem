from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, time, json, os

app = Flask(__name__)

# ── CORS: allow both localhost dev AND deployed Vercel frontend ────────────────
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*")
CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)

# ── JWT secret from environment variable (REQUIRED in production) ─────────────
app.config["JWT_SECRET_KEY"] = os.environ.get(
    "JWT_SECRET_KEY", "local-dev-secret-change-in-prod"
)
jwt = JWTManager(app)

# ── DB path: works both locally and on Railway ────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "canteen.db")

# ── DB connection ─────────────────────────────────────────────────────────────
def db_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # lets us access columns by name
    return conn

# ── Init DB ───────────────────────────────────────────────────────────────────
def init_db():
    conn = db_conn()
    cur  = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT,
        email      TEXT UNIQUE,
        password   TEXT,
        role       TEXT DEFAULT 'student',
        canteen_id INTEGER
    )""")

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
    conn = db_conn()
    cur  = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM canteens")
    if cur.fetchone()[0] == 0:
        cur.executemany("INSERT INTO canteens(name) VALUES(?)", [
            ("Maggi Hotspot",),
            ("Southern Stories",),
            ("SnapEats",),
            ("Infinity Kitchen",)
        ])
        conn.commit()
    conn.close()

seed()

# ── Priority algorithm ────────────────────────────────────────────────────────
def calc_priority(o):
    waiting = time.time() - o["created_time"]
    return (o["expected_time"] * 2) + (o["items_count"] * 3) - (waiting / 10)

# ── Health check (useful for Railway uptime checks) ───────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "B.U Eats API", "version": "1.0"})

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok"})

# ── REGISTER ──────────────────────────────────────────────────────────────────
@app.route("/register", methods=["POST"])
def register():
    d    = request.json
    conn = db_conn()
    cur  = conn.cursor()

    # Default role to "student" if the frontend doesn't send one
    role       = d.get("role", "student")
    canteen_id = d.get("canteen_id", None)

    try:
        hashed = generate_password_hash(d["password"])
        cur.execute("""
            INSERT INTO users(name, email, password, role, canteen_id)
            VALUES (?, ?, ?, ?, ?)
        """, (d["name"], d["email"], hashed, role, canteen_id))
        conn.commit()
        conn.close()
        return jsonify({"msg": "registered"})

    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Email already registered"}), 400

    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

# ── LOGIN ─────────────────────────────────────────────────────────────────────
@app.route("/login", methods=["POST"])
def login():
    d    = request.json
    conn = db_conn()
    cur  = conn.cursor()

    cur.execute("SELECT * FROM users WHERE email = ?", (d["email"],))
    u = cur.fetchone()
    conn.close()

    if not u or not check_password_hash(u["password"], d["password"]):
        return jsonify({"error": "Invalid email or password"}), 401

    token = create_access_token(identity={
        "id":         u["id"],
        "role":       u["role"],
        "canteen_id": u["canteen_id"]
    })

    return jsonify({
        "token":      token,
        "id":         u["id"],
        "name":       u["name"],
        "role":       u["role"],
        "canteen_id": u["canteen_id"]
    })

# ── CREATE ORDER ──────────────────────────────────────────────────────────────
@app.route("/order/create", methods=["POST"])
@jwt_required()
def create_order():
    user = get_jwt_identity()
    if user["role"] != "student":
        return jsonify({"error": "Only students can place orders"}), 403

    d    = request.json
    conn = db_conn()
    cur  = conn.cursor()

    oid         = int(time.time() * 1000)
    items       = d["items"]
    total_price = sum(i["price"] for i in items)
    total_time  = sum(i["time"]  for i in items)

    cur.execute("""
        INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        oid, d["canteen_id"], user["id"],
        json.dumps(items), len(items),
        total_price, total_time,
        "WAITING", time.time(), None, None
    ))

    conn.commit()
    conn.close()
    return jsonify({"order_id": oid})

# ── CANTEEN ORDERS (dashboard polling) ───────────────────────────────────────
@app.route("/canteen/orders", methods=["GET"])
@jwt_required()
def canteen_orders():
    user = get_jwt_identity()
    if user["role"] != "canteen":
        return jsonify({"error": "Only canteen staff can view this"}), 403

    conn = db_conn()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE canteen_id = ?", (user["canteen_id"],))
    rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        o = {
            "order_id":     r["order_id"],
            "canteen_id":   r["canteen_id"],
            "student_id":   r["student_id"],
            "items":        json.loads(r["items"]),
            "items_count":  r["items_count"],
            "price":        r["price"],
            "expected_time":r["expected_time"],
            "status":       r["status"],
            "created_time": r["created_time"]
        }
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
    conn = db_conn()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE order_id = ?", (oid,))
    r = cur.fetchone()
    conn.close()

    if not r:
        return jsonify({"error": "Order not found"}), 404

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
    conn = db_conn()
    cur  = conn.cursor()
    cur.execute("SELECT status FROM orders WHERE order_id = ?", (oid,))
    r = cur.fetchone()
    if not r:
        conn.close()
        return False
    if next_status in FLOW.get(r["status"], []):
        cur.execute("UPDATE orders SET status = ? WHERE order_id = ?", (next_status, oid))
        conn.commit()
    conn.close()
    return True

@app.route("/order/accept",    methods=["POST"])
@jwt_required()
def accept():
    set_status(request.json["order_id"], "ACCEPTED")
    return jsonify({"ok": 1})

@app.route("/order/preparing", methods=["POST"])
@jwt_required()
def preparing():
    set_status(request.json["order_id"], "PREPARING")
    return jsonify({"ok": 1})

@app.route("/order/ready",     methods=["POST"])
@jwt_required()
def ready():
    set_status(request.json["order_id"], "READY")
    return jsonify({"ok": 1})

@app.route("/order/complete",  methods=["POST"])
@jwt_required()
def complete():
    set_status(request.json["order_id"], "COMPLETED")
    return jsonify({"ok": 1})

# ── Run locally ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)