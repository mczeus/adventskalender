import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
DB_PATH = os.environ.get("DB_PATH", "/data/gutscheine.db")
PORT = int(os.environ.get("PORT", "8080"))
IOBROKER_URL = os.environ.get("IOBROKER_URL", "").rstrip("/")
IOBROKER_ENABLED = os.environ.get("IOBROKER_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
ALL_USERS = "__ALL_USERS__"
SESSION_SECRET = os.environ.get("SESSION_SECRET", "change-this-session-secret")
if SESSION_SECRET == "change-this-session-secret":
    print("WARNING: SESSION_SECRET is still the default value.")

CODES = {
    "C5X3": ("Jan", 0.00, "Startcode"),
    "B1X5": ("Jan", 0.80, ""), "B1K8": ("Jan", 1.00, "Kalender"), "B9V1": ("Jan", 1.30, "Mama knuddeln"),
    "B7C4": ("Jan", 1.40, "Kalender und Mama eine Gschmiert"), "B4E2": ("Jan", 1.20, "Kim eine Gschmiert"),
    "B2Y6": ("Jan", 1.00, "Kalender und Getränke"), "B6G6": ("Jan", 1.30, ""), "B2Q9": ("Jan", 1.20, "Papa eine Gschmiert"),
    "B0F3": ("Jan", 1.00, "Kalender"), "B4T2": ("Jan", 1.50, "Kalender und lustiges Selfie in die Family-Gruppe stellen"),
    "B4P0": ("Jan", 0.90, ""), "B6R8": ("Jan", 1.10, "Kalender"), "B9J5": ("Jan", 1.10, "Knutscha auf Mamas Backe"),
    "B7U4": ("Jan", 0.80, "Kalender"), "B2D3": ("Jan", 1.10, ""), "B1W9": ("Jan", 0.90, "Kalender und lustiges Selfie mit Kim in die Family-Gruppe stellen"),
    "B5M3": ("Jan", 0.60, ""), "B3H9": ("Jan", 1.00, "Kalender"), "B8N6": ("Jan", 0.70, "Mama gaaanz fest drücken"),
    "B8S1": ("Jan", 1.00, "Kalender"), "B0Z5": ("Jan", 0.50, ""), "B3X2": ("Jan", 0.80, "Kalender"),
    "B3L7": ("Jan", 0.90, "Kalender und Family-Gruppenknuddeln"), "B5A7": ("Jan", 0.90, "Snack und Getränke"), "B7G7": ("Jan", 6.00, "Frohe Weihnachten"),
    "S1A3": ("Kim", 1.50, "Kalender"), "S1L2": ("Kim", 1.00, ""), "S9J4": ("Kim", 0.90, "Kalender bekommt PAPA oder?"),
    "S7G5": ("Kim", 0.80, "Mama eine Gschmiert"), "S4P7": ("Kim", 1.00, "Kalender und Jan eine Gschmiert"), "S2X2": ("Kim", 1.10, "Packung Haribo"),
    "S6R5": ("Kim", 0.90, "Kalender und Papa eine Gschmiert"), "S2B7": ("Kim", 0.70, "Kalender"), "S0V3": ("Kim", 0.80, ""),
    "S4D2": ("Kim", 0.90, "Lustiges Selfie in die Family-Gruppe stellen"), "S4Z0": ("Kim", 1.00, "Kalender"), "S6F1": ("Kim", 1.00, ""),
    "S9U8": ("Kim", 1.10, "Kalender"), "S7S9": ("Kim", 1.00, "Knutscha auf Papas Backe"), "S2M6": ("Kim", 1.20, "Kalender"),
    "S1W7": ("Kim", 1.00, "Lustiges Selfie mit Jan in die Family-Gruppe stellen"), "S5E6": ("Kim", 1.20, "Kalender"), "S3N3": ("Kim", 0.80, "Papa gaaanz fest drücken"),
    "S8H8": ("Kim", 1.30, "Kalender"), "S8T4": ("Kim", 1.00, ""), "S0K9": ("Kim", 1.10, "Kalender"),
    "S3Y6": ("Kim", 0.90, ""), "S3C9": ("Kim", 1.10, "Family-Gruppenknuddeln"), "S5Q1": ("Kim", 1.10, "Kalender, Snack und Getränke"), "S7G9": ("Kim", 4.50, "Frohe Weihnachten")
}

SESSIONS = {}
DB_LOCK = threading.RLock()

def db():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection

def now():
    return datetime.now(timezone.utc).isoformat()

def valid_username(value):
    value = str(value or "").strip()
    if not 1 <= len(value) <= 32 or value.lower() in ("admin", "administrator") or any(ord(ch) < 32 for ch in value):
        return None
    return value

def username_key(value):
    return valid_username(value).casefold() if valid_username(value) else ""

def valid_owner(value):
    value = str(value or "").strip()
    if value == ALL_USERS or value.casefold() in ("alle benutzer", "alle user", "all users"):
        return ALL_USERS
    return valid_username(value)

def owner_label(value):
    return "Alle Benutzer" if value == ALL_USERS else value

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 240000).hex()

def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (name TEXT PRIMARY KEY, salt TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS admin (id INTEGER PRIMARY KEY CHECK (id = 1), salt TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS codes (code TEXT PRIMARY KEY, name TEXT NOT NULL, amount REAL NOT NULL, description TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS redeemed (code TEXT PRIMARY KEY, name TEXT NOT NULL, amount REAL NOT NULL, description TEXT NOT NULL, redeemed_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        seeded = conn.execute("SELECT value FROM app_meta WHERE key = 'default_codes_seeded'").fetchone()
        if not seeded:
            timestamp = now()
            existing_codes = conn.execute("SELECT COUNT(*) AS count FROM codes").fetchone()["count"]
            if existing_codes == 0:
                for code, (name, amount, description) in CODES.items():
                    conn.execute("INSERT OR IGNORE INTO codes (code, name, amount, description, active, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)", (code, name, amount, description or "", timestamp, timestamp))
            conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('default_codes_seeded', '1')")
        conn.commit()

def make_session(kind, subject):
    token = secrets.token_urlsafe(32)
    signature = hmac.new(SESSION_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()
    SESSIONS[token] = (kind, subject, time.time() + 60 * 60 * 24 * 14)
    return token + "." + signature

def session_info(handler):
    raw = handler.headers.get("Cookie", "")
    signed = next((part.strip()[8:] for part in raw.split(";") if part.strip().startswith("session=")), "")
    if "." not in signed:
        return None
    token, signature = signed.rsplit(".", 1)
    expected = hmac.new(SESSION_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    session = SESSIONS.get(token)
    if not session or session[2] < time.time():
        SESSIONS.pop(token, None)
        return None
    return {"kind": session[0], "subject": session[1], "token": token}

def user_session(handler):
    session = session_info(handler)
    return session["subject"] if session and session["kind"] == "user" else None

def admin_session(handler):
    session = session_info(handler)
    return bool(session and session["kind"] == "admin")

def json_body(handler):
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        return json.loads(handler.rfile.read(length) or b"{}")
    except (ValueError, json.JSONDecodeError):
        return {}

def totals():
    with db() as conn:
        rows = conn.execute("SELECT name, amount FROM redeemed ORDER BY name").fetchall()
    result = {}
    display_names = {}
    for row in rows:
        key = username_key(row["name"])
        display_names.setdefault(key, row["name"])
        result[key] = result.get(key, 0.0) + float(row["amount"])
    return {display_names[key]: round(value, 2) for key, value in sorted(result.items(), key=lambda item: display_names[item[0]].casefold())}

def all_redeemed():
    with db() as conn:
        rows = conn.execute("SELECT code, name, amount, description, redeemed_at FROM redeemed ORDER BY redeemed_at DESC").fetchall()
    return [dict(row) for row in rows]

def user_data(user):
    with db() as conn:
        rows = conn.execute("SELECT code, name, amount, description, redeemed_at FROM redeemed WHERE lower(name) = lower(?) ORDER BY redeemed_at DESC", (user,)).fetchall()
    entries = [dict(row) for row in rows]
    return {"user": user, "entries": entries, "total": round(sum(float(x["amount"]) for x in entries), 2)}

def admin_dashboard():
    with db() as conn:
        code_rows = conn.execute("""
            SELECT c.code, c.name, c.amount, c.description, c.active,
                   r.name AS redeemed_by, r.redeemed_at
            FROM codes c LEFT JOIN redeemed r ON r.code = c.code
            ORDER BY c.name, c.code
        """).fetchall()
    with db() as conn:
        user_rows = conn.execute("SELECT name FROM users ORDER BY name COLLATE NOCASE").fetchall()
    codes = [dict(row) for row in code_rows]
    for code in codes:
        code["owner_label"] = owner_label(code["name"])
    return {"users": [row["name"] for row in user_rows], "codes": codes, "redeemed": all_redeemed(), "totals": totals()}

def sync_iobroker():
    if not IOBROKER_ENABLED:
        return {"ok": True, "enabled": False, "message": "ioBroker-Synchronisierung ist deaktiviert."}
    if not IOBROKER_URL:
        return {"ok": False, "message": "Keine ioBroker-Adresse konfiguriert."}
    values = all_redeemed()
    totals_data = totals()
    # Both payloads are JSON text. type=string prevents ioBroker from treating them as objects.
    requests = [
        ("javascript.0.Adventskalender.gutscheine", json.dumps(values, ensure_ascii=False, separators=(",", ":"))),
        ("javascript.0.Adventskalender.betraege", json.dumps(totals_data, ensure_ascii=False, separators=(",", ":")))
    ]
    try:
        for datapoint, value in requests:
            query = urllib.parse.urlencode({"value": value, "type": "string"})
            request = urllib.request.Request(f"{IOBROKER_URL}/set/{datapoint}?{query}", method="GET")
            with urllib.request.urlopen(request, timeout=4) as response:
                if response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status}")
        return {"ok": True, "message": "Mit ioBroker synchronisiert."}
    except Exception as exc:
        return {"ok": False, "message": "Lokal gespeichert; ioBroker nicht erreichbar.", "detail": str(exc)}

def send_user_login(handler, user):
    cookie = f"session={make_session('user', user)}; Max-Age=1209600; Path=/; HttpOnly; SameSite=Lax"
    handler.send_json({"ok": True, "data": user_data(user)}, cookies=[cookie])

def send_admin_login(handler):
    cookie = f"session={make_session('admin', 'Admin')}; Max-Age=1209600; Path=/; HttpOnly; SameSite=Lax"
    handler.send_json({"ok": True, "data": admin_dashboard()}, cookies=[cookie])

class Handler(BaseHTTPRequestHandler):
    server_version = "WeihnachtsGutscheine/2.0"
    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}")
    def send_json(self, payload, status=200, cookies=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookies:
            for cookie in cookies:
                self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)
    def send_file(self, path):
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        content_type = "text/html; charset=utf-8" if path.suffix == ".html" else "image/jpeg" if path.suffix in (".jpg", ".jpeg") else "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)
    def require_admin(self):
        if not admin_session(self):
            self.send_json({"error": "Admin-Anmeldung erforderlich."}, 401)
            return False
        return True
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/config":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            requested_user = valid_username(query.get("user", [""])[0])
            with db() as conn:
                admin_configured = bool(conn.execute("SELECT 1 FROM admin WHERE id = 1").fetchone())
                user_configured = bool(requested_user and conn.execute("SELECT 1 FROM users WHERE lower(name) = lower(?)", (requested_user,)).fetchone())
            self.send_json({"userConfigured": user_configured, "adminConfigured": admin_configured})
        elif path == "/api/me":
            user = user_session(self)
            self.send_json({"authenticated": bool(user), "data": user_data(user) if user else None})
        elif path == "/api/admin/dashboard":
            if self.require_admin(): self.send_json(admin_dashboard())
        elif path == "/" or path == "/index.html":
            self.send_file(PUBLIC / "index.html")
        elif path == "/hintergrund.jpg":
            self.send_file(PUBLIC / "hintergrund.jpg")
        else:
            self.send_error(HTTPStatus.NOT_FOUND)
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        payload = json_body(self)
        if path == "/api/setup":
            user, password, confirm = valid_username(payload.get("user")), payload.get("password", ""), payload.get("confirm", "")
            if not user or len(password) < 4 or password != confirm:
                self.send_json({"error": "Benutzer oder Passwortdaten sind ungueltig."}, 400); return
            with DB_LOCK, db() as conn:
                if conn.execute("SELECT 1 FROM users WHERE lower(name) = lower(?)", (user,)).fetchone():
                    self.send_json({"error": "Fuer diesen Benutzer wurde bereits ein Passwort eingerichtet."}, 409); return
                salt = secrets.token_hex(16)
                conn.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (user, salt, password_hash(password, salt), now()))
                conn.commit()
            send_user_login(self, user); return
        if path == "/api/login":
            user, password = valid_username(payload.get("user")), payload.get("password", "")
            with db() as conn:
                row = conn.execute("SELECT name, salt, password_hash FROM users WHERE lower(name) = lower(?)", (user,)).fetchone() if user else None
            if not row:
                self.send_json({"error": "Benutzer nicht gefunden. Lege zuerst ein Passwort fest.", "setupRequired": True}, 404); return
            if not hmac.compare_digest(password_hash(password, row["salt"]), row["password_hash"]):
                self.send_json({"error": "Benutzername oder Passwort ist nicht korrekt."}, 401); return
            send_user_login(self, row["name"]); return
        if path == "/api/admin/setup":
            password, confirm = payload.get("password", ""), payload.get("confirm", "")
            if len(password) < 4 or password != confirm:
                self.send_json({"error": "Das Admin-Passwort ist ungueltig oder stimmt nicht ueberein."}, 400); return
            with DB_LOCK, db() as conn:
                if conn.execute("SELECT 1 FROM admin WHERE id = 1").fetchone():
                    self.send_json({"error": "Das Admin-Passwort wurde bereits eingerichtet."}, 409); return
                salt = secrets.token_hex(16)
                conn.execute("INSERT INTO admin VALUES (1, ?, ?, ?)", (salt, password_hash(password, salt), now()))
                conn.commit()
            send_admin_login(self); return
        if path == "/api/admin/login":
            password = payload.get("password", "")
            with db() as conn:
                row = conn.execute("SELECT salt, password_hash FROM admin WHERE id = 1").fetchone()
            if not row or not hmac.compare_digest(password_hash(password, row["salt"]), row["password_hash"]):
                self.send_json({"error": "Admin-Passwort ist nicht korrekt."}, 401); return
            send_admin_login(self); return
        if path == "/api/logout":
            session = session_info(self)
            if session: SESSIONS.pop(session["token"], None)
            self.send_json({"ok": True}, cookies=["session=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax"]); return
        if path == "/api/redeem":
            user = user_session(self)
            if not user:
                self.send_json({"error": "Nicht angemeldet."}, 401); return
            code = str(payload.get("code", "")).strip().upper()
            if len(code) != 4 or not code.isalnum():
                self.send_json({"error": "Bitte genau vier Buchstaben oder Zahlen eingeben."}, 400); return
            with db() as conn:
                voucher = conn.execute("SELECT code, name, amount, description, active FROM codes WHERE code = ?", (code,)).fetchone()
            if not voucher or not voucher["active"]:
                self.send_json({"error": "Dieser Gutscheincode ist ungueltig oder deaktiviert."}, 400); return
            if voucher["name"] != ALL_USERS and username_key(voucher["name"]) != username_key(user):
                self.send_json({"error": f"Dieser Code gehoert zu {owner_label(voucher['name'])}."}, 403); return
            try:
                with DB_LOCK, db() as conn:
                    conn.execute("INSERT INTO redeemed VALUES (?, ?, ?, ?, ?)", (code, user, voucher["amount"], voucher["description"], now()))
                    conn.commit()
            except sqlite3.IntegrityError:
                self.send_json({"error": "Dieser Code wurde bereits eingeloest."}, 409); return
            sync = sync_iobroker()
            self.send_json({"ok": True, "entry": {"code": code, "name": user, "amount": voucher["amount"], "description": voucher["description"]}, "data": user_data(user), "sync": sync}); return
        if path == "/api/sync":
            if not session_info(self): self.send_json({"error": "Nicht angemeldet."}, 401); return
            self.send_json(sync_iobroker()); return
        if path.startswith("/api/admin/code/") and path.endswith("/owner"):
            if not self.require_admin(): return
            code = urllib.parse.unquote(path[len("/api/admin/code/"):-len("/owner")]).strip("/").upper()
            name = valid_owner(payload.get("name"))
            if not name:
                self.send_json({"error": "Benutzername ist ungueltig."}, 400); return
            with DB_LOCK, db() as conn:
                result = conn.execute("UPDATE codes SET name = ?, updated_at = ? WHERE code = ?", (name, now(), code))
                if result.rowcount == 0:
                    self.send_json({"error": "Code nicht gefunden."}, 404); return
                conn.commit()
            sync = sync_iobroker()
            self.send_json({"ok": True, "code": code, "name": name, "data": admin_dashboard(), "sync": sync}); return
        if path == "/api/admin/code":
            if not self.require_admin(): return
            code = str(payload.get("code", "")).strip().upper()
            name = payload.get("name")
            description = str(payload.get("description", "")).strip()
            try: amount = round(float(payload.get("amount", 0)), 2)
            except (TypeError, ValueError): amount = -1
            name = valid_owner(name)
            if len(code) != 4 or not code.isalnum() or not name or amount < 0:
                self.send_json({"error": "Code, Benutzername oder Betrag ist ungueltig."}, 400); return
            with DB_LOCK, db() as conn:
                try:
                    conn.execute("INSERT INTO codes VALUES (?, ?, ?, ?, 1, ?, ?)", (code, name, amount, description, now(), now()))
                    conn.commit()
                except sqlite3.IntegrityError:
                    self.send_json({"error": "Dieser Code existiert bereits."}, 409); return
            self.send_json({"ok": True, "data": admin_dashboard()}); return
        if path == "/api/admin/sync":
            if not self.require_admin(): return
            self.send_json(sync_iobroker()); return
        if path.startswith("/api/admin/redeemed/") and path.endswith("/undo"):
            if not self.require_admin(): return
            code = urllib.parse.unquote(path[len("/api/admin/redeemed/"):-len("/undo")]).strip("/").upper()
            with DB_LOCK, db() as conn:
                conn.execute("DELETE FROM redeemed WHERE code = ?", (code,)); conn.commit()
            self.send_json({"ok": True, "data": admin_dashboard()}); return
        self.send_error(HTTPStatus.NOT_FOUND)
    def do_PUT(self):
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/admin/code/"):
            self.send_error(HTTPStatus.NOT_FOUND); return
        if not self.require_admin(): return
        code = urllib.parse.unquote(path[len("/api/admin/code/"):]).strip("/").upper()
        payload = json_body(self)
        name = valid_owner(payload.get("name"))
        description = str(payload.get("description", "")).strip()
        try: amount = round(float(payload.get("amount", 0)), 2)
        except (TypeError, ValueError): amount = -1
        if amount < 0:
            self.send_json({"error": "Der Betrag muss 0 oder groesser sein."}, 400); return
        if not name:
            self.send_json({"error": "Benutzername ist ungueltig."}, 400); return
        with DB_LOCK, db() as conn:
            result = conn.execute("UPDATE codes SET name = ?, amount = ?, description = ?, updated_at = ? WHERE code = ?", (name, amount, description, now(), code))
            if result.rowcount == 0:
                self.send_json({"error": "Code nicht gefunden."}, 404); return
            conn.commit()
        self.send_json({"ok": True, "data": admin_dashboard()})
    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if not self.require_admin(): return
        if path == "/api/admin/codes/all":
            with DB_LOCK, db() as conn:
                conn.execute("DELETE FROM redeemed")
                conn.execute("DELETE FROM codes")
                conn.commit()
            sync = sync_iobroker()
            self.send_json({"ok": True, "data": admin_dashboard(), "sync": sync}); return
        if path.startswith("/api/admin/code/"):
            code = urllib.parse.unquote(path[len("/api/admin/code/"):]).strip("/").upper()
            with DB_LOCK, db() as conn:
                result = conn.execute("DELETE FROM codes WHERE code = ?", (code,))
                conn.execute("DELETE FROM redeemed WHERE code = ?", (code,))
                conn.commit()
            if result.rowcount == 0:
                self.send_json({"error": "Code nicht gefunden."}, 404); return
            sync = sync_iobroker()
            self.send_json({"ok": True, "data": admin_dashboard(), "sync": sync}); return
        if path.startswith("/api/admin/redeemed/"):
            code = urllib.parse.unquote(path[len("/api/admin/redeemed/"):]).strip("/").upper()
            with DB_LOCK, db() as conn:
                conn.execute("DELETE FROM redeemed WHERE code = ?", (code,)); conn.commit()
            sync = sync_iobroker()
            self.send_json({"ok": True, "data": admin_dashboard(), "sync": sync}); return
        self.send_error(HTTPStatus.NOT_FOUND)

if __name__ == "__main__":
    init_db()
    print(f"Weihnachts-Gutscheine laufen auf 0.0.0.0:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
