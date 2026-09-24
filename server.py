import base64
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

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
DB_PATH = os.environ.get("DB_PATH", "/data/gutscheine.db")
PORT = int(os.environ.get("PORT", "8080"))
IOBROKER_URL = os.environ.get("IOBROKER_URL", "").rstrip("/")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "change-this-session-secret")
if SESSION_SECRET == "change-this-session-secret":
    print("WARNUNG: SESSION_SECRET ist noch der Standardwert.")

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
DB_LOCK = __import__("threading").RLock()

def db():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection

def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (name TEXT PRIMARY KEY, salt TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS redeemed (code TEXT PRIMARY KEY, name TEXT NOT NULL, amount REAL NOT NULL, description TEXT NOT NULL, redeemed_at TEXT NOT NULL)")
        conn.commit()

def now():
    return datetime.now(timezone.utc).isoformat()

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 240000).hex()

def make_session(user):
    token = secrets.token_urlsafe(32)
    signature = hmac.new(SESSION_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()
    SESSIONS[token] = (user, time.time() + 60 * 60 * 24 * 14)
    return token + "." + signature

def session_user(handler):
    raw = handler.headers.get("Cookie", "")
    token = next((part.strip()[8:] for part in raw.split(";") if part.strip().startswith("session=")), "")
    if "." not in token:
        return None
    value, signature = token.rsplit(".", 1)
    expected = hmac.new(SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    session = SESSIONS.get(value)
    if not session or session[1] < time.time():
        SESSIONS.pop(value, None)
        return None
    return session[0]

def json_body(handler):
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        return json.loads(handler.rfile.read(length) or b"{}")
    except (ValueError, json.JSONDecodeError):
        return {}

def totals():
    with db() as conn:
        rows = conn.execute("SELECT name, COALESCE(SUM(amount), 0) AS total FROM redeemed GROUP BY name").fetchall()
    result = {"Jan": 0.0, "Kim": 0.0}
    for row in rows:
        result[row["name"]] = round(float(row["total"]), 2)
    return result

def all_redeemed():
    with db() as conn:
        rows = conn.execute("SELECT code, name, amount, description, redeemed_at FROM redeemed ORDER BY redeemed_at").fetchall()
    return [dict(row) for row in rows]

def sync_iobroker():
    if not IOBROKER_URL:
        return {"ok": False, "message": "Keine ioBroker-Adresse konfiguriert."}
    values = all_redeemed()
    totals_data = totals()
    requests = [
        ("javascript.0.Adventskalender.gutscheine", json.dumps(values, ensure_ascii=False, separators=(",", ":"))),
        ("javascript.0.Adventskalender.janBetrag", f"{totals_data['Jan']:.2f}€"),
        ("javascript.0.Adventskalender.kimBetrag", f"{totals_data['Kim']:.2f}€")
    ]
    try:
        for datapoint, value in requests:
            query = urllib.parse.urlencode({"value": value})
            request = urllib.request.Request(f"{IOBROKER_URL}/set/{datapoint}?{query}", method="GET")
            with urllib.request.urlopen(request, timeout=4) as response:
                if response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status}")
        return {"ok": True, "message": "Mit ioBroker synchronisiert."}
    except Exception as exc:
        return {"ok": False, "message": "Lokal gespeichert; ioBroker nicht erreichbar.", "detail": str(exc)}

def user_data(user):
    with db() as conn:
        rows = conn.execute("SELECT code, name, amount, description, redeemed_at FROM redeemed WHERE name = ? ORDER BY redeemed_at DESC", (user,)).fetchall()
    entries = [dict(row) for row in rows]
    return {"user": user, "entries": entries, "total": round(sum(float(x["amount"]) for x in entries), 2)}

class Handler(BaseHTTPRequestHandler):
    server_version = "WeihnachtsGutscheine/1.0"
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
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/config":
            with db() as conn:
                setup = {name: bool(conn.execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone()) for name in ("Jan", "Kim")}
            self.send_json({"users": setup})
        elif path == "/api/me":
            user = session_user(self)
            self.send_json({"authenticated": bool(user), "data": user_data(user) if user else None})
        elif path == "/" or path == "/index.html":
            self.send_file(PUBLIC / "index.html")
        elif path == "/hintergrund.jpg":
            self.send_file(PUBLIC / "hintergrund.jpg")
        else:
            self.send_error(HTTPStatus.NOT_FOUND)
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        payload = json_body(self)
        if path == "/api/setup":
            user, password, confirm = payload.get("user"), payload.get("password", ""), payload.get("confirm", "")
            if user not in ("Jan", "Kim") or len(password) < 4 or password != confirm:
                self.send_json({"error": "Benutzer oder Passwortdaten sind ungueltig."}, 400); return
            with DB_LOCK, db() as conn:
                if conn.execute("SELECT 1 FROM users WHERE name = ?", (user,)).fetchone():
                    self.send_json({"error": "Für diesen Benutzer wurde bereits ein Passwort eingerichtet."}, 409); return
                salt = secrets.token_hex(16)
                conn.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (user, salt, password_hash(password, salt), now()))
                conn.commit()
            self.finish_login(user); return
        if path == "/api/login":
            user, password = payload.get("user"), payload.get("password", "")
            with db() as conn:
                row = conn.execute("SELECT salt, password_hash FROM users WHERE name = ?", (user,)).fetchone()
            if not row or not hmac.compare_digest(password_hash(password, row["salt"]), row["password_hash"]):
                self.send_json({"error": "Benutzername oder Passwort ist nicht korrekt."}, 401); return
            self.finish_login(user); return
        if path == "/api/logout":
            raw = self.headers.get("Cookie", "")
            token = next((part.strip()[8:].split(".", 1)[0] for part in raw.split(";") if part.strip().startswith("session=")), "")
            SESSIONS.pop(token, None)
            self.send_json({"ok": True}, cookies=["session=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax"]); return
        if path == "/api/redeem":
            user = session_user(self)
            if not user:
                self.send_json({"error": "Nicht angemeldet."}, 401); return
            code = str(payload.get("code", "")).strip().upper()
            if len(code) != 4 or not code.isalnum() or code not in CODES:
                self.send_json({"error": "Dieser Gutscheincode ist ungueltig."}, 400); return
            owner, amount, description = CODES[code]
            if owner != user:
                self.send_json({"error": f"Dieser Code gehoert zu {owner}."}, 403); return
            try:
                with DB_LOCK, db() as conn:
                    conn.execute("INSERT INTO redeemed VALUES (?, ?, ?, ?, ?)", (code, owner, amount, description, now()))
                    conn.commit()
            except sqlite3.IntegrityError:
                self.send_json({"error": "Dieser Code wurde bereits eingeloest."}, 409); return
            sync = sync_iobroker()
            self.send_json({"ok": True, "entry": {"code": code, "name": owner, "amount": amount, "description": description}, "data": user_data(user), "sync": sync})
            return
        if path == "/api/sync":
            if not session_user(self):
                self.send_json({"error": "Nicht angemeldet."}, 401); return
            self.send_json(sync_iobroker()); return
        self.send_error(HTTPStatus.NOT_FOUND)
    def finish_login(self, user):
        cookie = f"session={make_session(user)}; Max-Age=1209600; Path=/; HttpOnly; SameSite=Lax"
        self.send_json({"ok": True, "data": user_data(user)}, cookies=[cookie])

if __name__ == "__main__":
    init_db()
    print(f"Weihnachts-Gutscheine laufen auf 0.0.0.0:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
