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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
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
DEFAULT_IOBROKER_VOUCHERS = "javascript.0.Adventskalender.gutscheine"
DEFAULT_IOBROKER_TOTALS = "javascript.0.Adventskalender.betraege"
TIMEZONE_NAME = os.environ.get("TZ", "Europe/Berlin")
try:
    APP_TIMEZONE = ZoneInfo(TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    print(f"WARNUNG: Zeitzone {TIMEZONE_NAME!r} nicht gefunden; UTC wird verwendet.")
    APP_TIMEZONE = timezone.utc
ALL_USERS = "__ALL_USERS__"
SESSION_SECRET = os.environ.get("SESSION_SECRET", "change-this-session-secret")
if SESSION_SECRET == "change-this-session-secret":
    print("WARNING: SESSION_SECRET is still the default value.")

CODES = {
    "C5X3": ("Jan", 0.00, "Startcode"),
    "FROH": ("__ALL_USERS__", 0.00, "HO HO HO der Testcode scheint zu funktionieren :)"),
    "GAME": ("__ALL_USERS__", 0.00, "Hast du mal das rote Geschenk im Adminbereich gecheckt?"),
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
    # Store local application time without a timezone suffix. The API formats it
    # for people and for ioBroker as DD.MM.YYYY HH:MM:SS.
    return datetime.now(APP_TIMEZONE).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")

def format_timestamp(value):
    if not value:
        return ""
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(APP_TIMEZONE).replace(tzinfo=None)
        return parsed.strftime("%d.%m.%Y %H:%M:%S")
    except ValueError:
        return text

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

def valid_internal_label(value):
    value = str(value or "").strip()
    if len(value) > 120 or any(ord(ch) < 32 for ch in value):
        return None
    return value

def owner_label(value):
    return "Alle Benutzer" if value == ALL_USERS else value

def clean_iobroker_url(value):
    value = str(value or "").strip().rstrip("/")
    if len(value) > 300 or any(ord(ch) < 32 for ch in value):
        return None
    if value and not (value.startswith("http://") or value.startswith("https://")):
        return None
    return value

def clean_datapoint(value, fallback):
    value = str(value or "").strip()
    if not value:
        value = fallback
    if len(value) > 250 or any(ord(ch) < 32 for ch in value) or "?" in value or "#" in value:
        return None
    return value

def default_iobroker_config():
    return {
        "enabled": IOBROKER_ENABLED,
        "url": IOBROKER_URL,
        "vouchers_datapoint": DEFAULT_IOBROKER_VOUCHERS,
        "totals_datapoint": DEFAULT_IOBROKER_TOTALS,
    }

def read_iobroker_config(conn=None):
    close = conn is None
    conn = conn or db()
    try:
        defaults = default_iobroker_config()
        rows = conn.execute("SELECT key, value FROM app_meta WHERE key LIKE 'iobroker_%'").fetchall()
        values = {row["key"]: row["value"] for row in rows}
        return {
            "enabled": values.get("iobroker_enabled", "1" if defaults["enabled"] else "0") == "1",
            "url": values.get("iobroker_url", defaults["url"]),
            "vouchers_datapoint": values.get("iobroker_vouchers_datapoint", defaults["vouchers_datapoint"]),
            "totals_datapoint": values.get("iobroker_totals_datapoint", defaults["totals_datapoint"]),
        }
    finally:
        if close:
            conn.close()

def write_iobroker_config(conn, config):
    values = {
        "iobroker_enabled": "1" if config["enabled"] else "0",
        "iobroker_url": config["url"],
        "iobroker_vouchers_datapoint": config["vouchers_datapoint"],
        "iobroker_totals_datapoint": config["totals_datapoint"],
    }
    for key, value in values.items():
        conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)", (key, value))

def validate_iobroker_config(payload, current=None):
    current = current or default_iobroker_config()
    enabled = payload.get("enabled", current["enabled"])
    enabled = enabled in (True, 1, "1", "true", "on", "yes")
    url = clean_iobroker_url(payload.get("url", current["url"]))
    vouchers = clean_datapoint(payload.get("vouchers_datapoint", current["vouchers_datapoint"]), DEFAULT_IOBROKER_VOUCHERS)
    totals_dp = clean_datapoint(payload.get("totals_datapoint", current["totals_datapoint"]), DEFAULT_IOBROKER_TOTALS)
    if url is None or vouchers is None or totals_dp is None:
        return None
    return {"enabled": enabled, "url": url, "vouchers_datapoint": vouchers, "totals_datapoint": totals_dp}

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 240000).hex()

def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (name TEXT PRIMARY KEY, salt TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS admin (id INTEGER PRIMARY KEY CHECK (id = 1), salt TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS codes (code TEXT PRIMARY KEY, name TEXT NOT NULL, amount REAL NOT NULL, description TEXT NOT NULL DEFAULT '', internal_label TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, reusable INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        try:
            conn.execute("ALTER TABLE codes ADD COLUMN reusable INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE codes ADD COLUMN internal_label TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        conn.execute("CREATE TABLE IF NOT EXISTS redeemed (code TEXT PRIMARY KEY, name TEXT NOT NULL, amount REAL NOT NULL, description TEXT NOT NULL, redeemed_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS redemption_events (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, name TEXT NOT NULL, amount REAL NOT NULL, description TEXT NOT NULL, redeemed_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS user_input_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_name TEXT NOT NULL, entered_code TEXT NOT NULL, result TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        initial_iobroker = default_iobroker_config()
        write_iobroker_config(conn, {
            "enabled": initial_iobroker["enabled"],
            "url": initial_iobroker["url"],
            "vouchers_datapoint": initial_iobroker["vouchers_datapoint"],
            "totals_datapoint": initial_iobroker["totals_datapoint"],
        }) if not conn.execute("SELECT 1 FROM app_meta WHERE key = 'iobroker_enabled'").fetchone() else None
        migrated = conn.execute("SELECT value FROM app_meta WHERE key = 'redeemed_events_migrated'").fetchone()
        if not migrated:
            conn.execute("INSERT INTO redemption_events (code, name, amount, description, redeemed_at) SELECT code, name, amount, description, redeemed_at FROM redeemed")
            conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('redeemed_events_migrated', '1')")
        seeded = conn.execute("SELECT value FROM app_meta WHERE key = 'default_codes_seeded'").fetchone()
        timestamp = now()
        existing_codes = conn.execute("SELECT COUNT(*) AS count FROM codes").fetchone()["count"]
        if not seeded:
            if existing_codes == 0:
                for code, (name, amount, description) in CODES.items():
                    conn.execute("INSERT OR IGNORE INTO codes (code, name, amount, description, active, reusable, created_at, updated_at) VALUES (?, ?, ?, ?, 1, 0, ?, ?)", (code, name, amount, description or "", timestamp, timestamp))
            conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('default_codes_seeded', '1')")
        # Keep the protected built-in codes available after upgrades, even if the database already contains codes.
        protected_codes = ("FROH", "GAME")
        for code in protected_codes:
            name, amount, description = CODES[code]
            conn.execute("INSERT OR IGNORE INTO codes (code, name, amount, description, active, reusable, created_at, updated_at) VALUES (?, ?, ?, ?, 1, 1, ?, ?)", (code, name, amount, description or "", timestamp, timestamp))
            conn.execute("UPDATE codes SET reusable = 1 WHERE code = ?", (code,))
        # GAME has a fixed public hint because it points to the red-gift Easter egg.
        conn.execute("UPDATE codes SET description = ?, active = 1, reusable = 1 WHERE code = ?", (CODES["GAME"][2], "GAME"))
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
        rows = conn.execute("SELECT name, amount FROM redemption_events ORDER BY name").fetchall()
    result = {}
    display_names = {}
    for row in rows:
        key = username_key(row["name"])
        display_names.setdefault(key, row["name"])
        result[key] = result.get(key, 0.0) + float(row["amount"])
    return {display_names[key]: round(value, 2) for key, value in sorted(result.items(), key=lambda item: display_names[item[0]].casefold())}

def all_redeemed():
    with db() as conn:
        rows = conn.execute("SELECT code, name, amount, description, redeemed_at FROM redemption_events ORDER BY redeemed_at DESC, id DESC").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["redeemed_at"] = format_timestamp(item.get("redeemed_at"))
        result.append(item)
    return result

def user_data(user):
    with db() as conn:
        rows = conn.execute("SELECT code, name, amount, description, redeemed_at FROM redemption_events WHERE lower(name) = lower(?) ORDER BY redeemed_at DESC, id DESC", (user,)).fetchall()
    entries = []
    for row in rows:
        item = dict(row)
        item["redeemed_at"] = format_timestamp(item.get("redeemed_at"))
        entries.append(item)
    return {"user": user, "entries": entries, "total": round(sum(float(x["amount"]) for x in entries), 2)}

def clean_entered_code(value):
    value = str(value or "").strip()
    value = value[:120]
    return "".join(ch for ch in value if ord(ch) >= 32)

def log_user_input(user, entered_code, result, detail=""):
    try:
        with DB_LOCK, db() as conn:
            conn.execute(
                "INSERT INTO user_input_log (user_name, entered_code, result, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(user), clean_entered_code(entered_code), str(result)[:80], str(detail)[:240], now())
            )
            conn.commit()
    except Exception as exc:
        # Logging must never prevent the actual voucher request from completing.
        print(f"WARNUNG: Benutzereingabe konnte nicht protokolliert werden: {exc}")

def user_input_log():
    with db() as conn:
        rows = conn.execute(
            "SELECT id, user_name, entered_code, result, detail, created_at FROM user_input_log ORDER BY id DESC"
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["created_at"] = format_timestamp(item.get("created_at"))
        result.append(item)
    return result

def admin_dashboard():
    with db() as conn:
        code_rows = conn.execute("""
            SELECT c.code, c.name, c.amount, c.description, c.internal_label, c.active, c.reusable,
                   (SELECT e.name FROM redemption_events e WHERE e.code = c.code ORDER BY e.id DESC LIMIT 1) AS redeemed_by,
                   (SELECT e.redeemed_at FROM redemption_events e WHERE e.code = c.code ORDER BY e.id DESC LIMIT 1) AS redeemed_at
            FROM codes c
            ORDER BY c.name, c.code
        """).fetchall()
    with db() as conn:
        user_rows = conn.execute("SELECT name FROM users ORDER BY name COLLATE NOCASE").fetchall()
    codes = [dict(row) for row in code_rows]
    for code in codes:
        code["owner_label"] = owner_label(code["name"])
        code["redeemed_at"] = format_timestamp(code.get("redeemed_at"))
    return {"users": [row["name"] for row in user_rows], "codes": codes, "redeemed": all_redeemed(), "totals": totals(), "user_inputs": user_input_log(), "iobroker": read_iobroker_config()}

def sync_iobroker(config=None):
    config = config or read_iobroker_config()
    if not config["enabled"]:
        return {"ok": True, "enabled": False, "message": "ioBroker-Synchronisierung ist deaktiviert."}
    if not config["url"]:
        return {"ok": False, "enabled": True, "message": "Keine ioBroker-Adresse konfiguriert."}
    values = all_redeemed()
    totals_data = totals()
    # Both payloads are JSON text. type=string prevents ioBroker from treating them as objects.
    requests = [
        (config["vouchers_datapoint"], json.dumps(values, ensure_ascii=False, separators=(",", ":"))),
        (config["totals_datapoint"], json.dumps(totals_data, ensure_ascii=False, separators=(",", ":")))
    ]
    try:
        for datapoint, value in requests:
            query = urllib.parse.urlencode({"value": value, "type": "string"})
            request = urllib.request.Request(f"{config['url']}/set/{datapoint}?{query}", method="GET")
            with urllib.request.urlopen(request, timeout=4) as response:
                if response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status}")
        return {"ok": True, "enabled": True, "message": "Mit ioBroker synchronisiert."}
    except Exception as exc:
        return {"ok": False, "enabled": True, "message": "Lokal gespeichert; ioBroker nicht erreichbar.", "detail": str(exc)}

def test_iobroker(config):
    if not config["url"]:
        return {"ok": False, "message": "Bitte zuerst eine ioBroker-Adresse eintragen."}
    try:
        request = urllib.request.Request(f"{config['url']}/help", method="GET")
        with urllib.request.urlopen(request, timeout=4) as response:
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}")
        return {"ok": True, "message": "ioBroker-Adresse erreichbar."}
    except Exception as exc:
        return {"ok": False, "message": "ioBroker-Adresse nicht erreichbar.", "detail": str(exc)}

def send_user_login(handler, user):
    cookie = f"session={make_session('user', user)}; Max-Age=1209600; Path=/; HttpOnly; SameSite=Lax"
    handler.send_json({"ok": True, "data": user_data(user)}, cookies=[cookie])

def send_admin_login(handler):
    cookie = f"session={make_session('admin', 'Admin')}; Max-Age=1209600; Path=/; HttpOnly; SameSite=Lax"
    handler.send_json({"ok": True, "data": admin_dashboard()}, cookies=[cookie])

def pdf_hex_text(value):
    text = str(value or "").replace("\n", " ").replace("\r", " ")
    return text.encode("cp1252", "replace").hex().upper()

def pdf_number(value):
    return f"{float(value):.2f}".rstrip("0").rstrip(".")

def pdf_text(commands, text, x, y, size=10, bold=False):
    font = "/F2" if bold else "/F1"
    commands.append(f"BT {font} {pdf_number(size)} Tf {pdf_number(x)} {pdf_number(y)} Td <{pdf_hex_text(text)}> Tj ET")

def pdf_rect(commands, x, y, width, height, fill=None, stroke=(0.43, 0.11, 0.16), line_width=0.8):
    if fill:
        r, g, b = fill
        commands.append(f"q {pdf_number(r)} {pdf_number(g)} {pdf_number(b)} rg {pdf_number(x)} {pdf_number(y)} {pdf_number(width)} {pdf_number(height)} re f Q")
    if stroke:
        r, g, b = stroke
        commands.append(f"q {pdf_number(line_width)} w {pdf_number(r)} {pdf_number(g)} {pdf_number(b)} RG {pdf_number(x)} {pdf_number(y)} {pdf_number(width)} {pdf_number(height)} re S Q")

def pdf_line(commands, x1, y1, x2, y2, color=(0.45, 0.12, 0.16), line_width=0.8):
    r, g, b = color
    commands.append(f"q {pdf_number(line_width)} w {pdf_number(r)} {pdf_number(g)} {pdf_number(b)} RG {pdf_number(x1)} {pdf_number(y1)} m {pdf_number(x2)} {pdf_number(y2)} l S Q")

def wrap_pdf_text(value, max_chars):
    words = str(value or "").split()
    if not words:
        return []
    lines, current = [], ""
    for word in words:
        if len(word) > max_chars:
            if current:
                lines.append(current)
                current = ""
            while len(word) > max_chars:
                lines.append(word[:max_chars - 1] + "-")
                word = word[max_chars - 1:]
            current = word
        elif not current:
            current = word
        elif len(current) + 1 + len(word) <= max_chars:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines

def pdf_page_content(commands):
    return ("\n".join(commands) + "\n").encode("ascii")

def build_pdf(page_commands):
    page_count = len(page_commands) or 1
    objects = [None, b"<< /Type /Catalog /Pages 2 0 R >>", None,
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    content_refs = []
    page_refs = []
    for commands in page_commands or [[]]:
        content_refs.append(len(objects))
        payload = pdf_page_content(commands)
        objects.append(f"<< /Length {len(payload)} >>\nstream\n".encode("ascii") + payload + b"endstream")
        page_refs.append(len(objects))
        objects.append(None)
    objects[2] = (f"<< /Type /Pages /Kids [{' '.join(f'{ref} 0 R' for ref in page_refs)}] /Count {page_count} >>").encode("ascii")
    for ref, content_ref in zip(page_refs, content_refs):
        objects[ref] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.28 841.89] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_ref} 0 R >>").encode("ascii")
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for obj in objects[1:]:
        offsets.append(len(output))
        output.extend(f"{len(offsets) - 1} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(f"trailer\n<< /Size {len(objects)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(output)

def code_owner_for_pdf(value):
    return "Alle Benutzer" if value == ALL_USERS else str(value or "")

def money_for_pdf(value):
    return f"{float(value or 0):.2f} EUR"

def build_code_list_pdf(codes):
    pages, commands = [], []
    def start_page(page_number):
        nonlocal commands
        commands = []
        pdf_text(commands, "Weihnachts-Gutscheine", 32, 805, 20, True)
        pdf_text(commands, "Code-Liste zum Ausdrucken", 32, 784, 10)
        pdf_text(commands, f"Seite {page_number}", 500, 805, 8)
        y = 758
        pdf_rect(commands, 28, y - 7, 539, 25, fill=(0.95, 0.88, 0.72), stroke=(0.45, 0.12, 0.16), line_width=0.7)
        for label, x in (("Code", 38), ("Gültig für", 170)):
            pdf_text(commands, label, x, y + 1, 8, True)
        return y - 32
    y = start_page(1)
    page_number = 1
    for index, code in enumerate(codes, 1):
        if y < 62:
            pages.append(commands)
            page_number += 1
            y = start_page(page_number)
        fill = (1.0, 0.98, 0.93) if index % 2 else (0.98, 0.94, 0.86)
        pdf_rect(commands, 28, y - 7, 539, 31, fill=fill, stroke=(0.82, 0.72, 0.55), line_width=0.35)
        pdf_text(commands, str(code["code"]), 38, y + 3, 12, True)
        pdf_text(commands, code_owner_for_pdf(code["name"]), 170, y + 3, 10)
        y -= 34
    pages.append(commands)
    return build_pdf(pages)

def build_advent_calendar_pdf(codes):
    commands = []
    pdf_text(commands, "Unser Adventskalender", 32, 807, 21, True)
    pdf_text(commands, "24 Gutscheincodes zum Ausschneiden oder Aufkleben", 32, 786, 9)
    margin_x, gap = 28, 8
    grid_top = 755
    card_width = (595.28 - 2 * margin_x - 3 * gap) / 4
    card_height = 112
    for index, code in enumerate(codes):
        col = index % 4
        row = index // 4
        x = margin_x + col * (card_width + gap)
        y = grid_top - (row + 1) * card_height - row * gap
        fill = [(0.93, 0.96, 0.91), (0.98, 0.91, 0.84), (0.91, 0.94, 0.98), (0.98, 0.90, 0.91)][index % 4]
        pdf_rect(commands, x, y, card_width, card_height, fill=fill, stroke=(0.45, 0.12, 0.16), line_width=1.0)
        pdf_text(commands, f"Türchen {index + 1}", x + 9, y + card_height - 18, 8, True)
        pdf_text(commands, str(code["code"]), x + 9, y + card_height - 53, 19, True)
        pdf_text(commands, code_owner_for_pdf(code["name"]), x + 9, y + 25, 9)
    return build_pdf([commands])

def load_export_codes(selected_codes=None):
    with db() as conn:
        if selected_codes is None:
            return [dict(row) for row in conn.execute("SELECT code, name FROM codes ORDER BY name COLLATE NOCASE, code").fetchall()]
        placeholders = ",".join("?" for _ in selected_codes)
        rows = conn.execute(f"SELECT code, name FROM codes WHERE code IN ({placeholders})", selected_codes).fetchall()
    by_code = {row["code"]: dict(row) for row in rows}
    return [by_code[code] for code in selected_codes]

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
    def send_binary(self, data, content_type, filename):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f"attachment; filename=\"{filename}\"")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)
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
        if path == "/api/admin/export/codes":
            if not self.require_admin(): return
            if not isinstance(payload, dict):
                self.send_json({"error": "Ungültige Exportdaten."}, 400); return
            mode = str(payload.get("mode", "list")).lower()
            if mode == "advent":
                selected = payload.get("codes")
                if not isinstance(selected, list) or len(selected) != 24:
                    self.send_json({"error": "Für den Adventskalender müssen genau 24 Codes ausgewählt werden."}, 400); return
                normalized = [str(code).strip().upper() for code in selected]
                if any(len(code) != 4 or not code.isalnum() for code in normalized) or len(set(normalized)) != 24:
                    self.send_json({"error": "Die 24 Türchen müssen unterschiedliche gültige Codes enthalten."}, 400); return
                codes = load_export_codes(normalized)
                if len(codes) != 24:
                    self.send_json({"error": "Mindestens ein ausgewählter Code wurde nicht gefunden."}, 400); return
                self.send_binary(build_advent_calendar_pdf(codes), "application/pdf", "adventskalender-24-tuerchen.pdf"); return
            codes = load_export_codes()
            self.send_binary(build_code_list_pdf(codes), "application/pdf", "gutscheine-codeliste.pdf"); return
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
            raw_code = payload.get("code", "")
            entered_code = clean_entered_code(raw_code)
            code = entered_code.upper()
            if len(code) != 4 or not code.isalnum():
                detail = "Bitte genau vier Buchstaben oder Zahlen eingeben."
                log_user_input(user, entered_code, "Ungueltiges Format", detail)
                self.send_json({"error": detail}, 400); return
            with db() as conn:
                voucher = conn.execute("SELECT code, name, amount, description, active, reusable FROM codes WHERE code = ?", (code,)).fetchone()
            if not voucher or not voucher["active"]:
                detail = "Dieser Gutscheincode ist ungueltig oder deaktiviert."
                log_user_input(user, entered_code, "Ungueltiger Code", detail)
                self.send_json({"error": detail}, 400); return
            if voucher["name"] != ALL_USERS and username_key(voucher["name"]) != username_key(user):
                detail = "Dieser Gutscheincode ist ungueltig."
                log_user_input(user, entered_code, "Ungueltiger Code", detail)
                self.send_json({"error": detail}, 400); return
            try:
                with DB_LOCK, db() as conn:
                    if not voucher["reusable"]:
                        if conn.execute("SELECT 1 FROM redemption_events WHERE code = ? LIMIT 1", (code,)).fetchone():
                            raise sqlite3.IntegrityError("one-time code already used")
                        conn.execute("INSERT INTO redeemed VALUES (?, ?, ?, ?, ?)", (code, user, voucher["amount"], voucher["description"], now()))
                    conn.execute("INSERT INTO redemption_events (code, name, amount, description, redeemed_at) VALUES (?, ?, ?, ?, ?)", (code, user, voucher["amount"], voucher["description"], now()))
                    conn.commit()
            except sqlite3.IntegrityError:
                detail = "Dieser Code wurde bereits eingeloest."
                log_user_input(user, entered_code, "Bereits eingeloest", detail)
                self.send_json({"error": detail}, 409); return
            detail = "Code erfolgreich eingeloest."
            log_user_input(user, entered_code, "Erfolgreich", detail)
            sync = sync_iobroker()
            self.send_json({"ok": True, "entry": {"code": code, "name": user, "amount": voucher["amount"], "description": voucher["description"]}, "data": user_data(user), "sync": sync}); return
        if path == "/api/sync":
            if not session_info(self): self.send_json({"error": "Nicht angemeldet."}, 401); return
            self.send_json(sync_iobroker()); return
        if path == "/api/admin/iobroker/config":
            if not self.require_admin(): return
            current = read_iobroker_config()
            config = validate_iobroker_config(payload, current)
            if config is None:
                self.send_json({"error": "ioBroker-Adresse oder Datenpunkt ist ungueltig."}, 400); return
            with DB_LOCK, db() as conn:
                write_iobroker_config(conn, config)
                conn.commit()
            self.send_json({"ok": True, "config": config, "data": admin_dashboard(), "message": "ioBroker-Einstellungen gespeichert."}); return
        if path == "/api/admin/iobroker/test":
            if not self.require_admin(): return
            current = read_iobroker_config()
            config = validate_iobroker_config(payload, current)
            if config is None:
                self.send_json({"error": "ioBroker-Adresse oder Datenpunkt ist ungueltig."}, 400); return
            self.send_json(test_iobroker(config)); return
        if path.startswith("/api/admin/code/") and path.endswith("/save"):
            if not self.require_admin(): return
            code = urllib.parse.unquote(path[len("/api/admin/code/"):-len("/save")]).strip("/").upper()
            name = valid_owner(payload.get("name"))
            description = str(payload.get("description", "")).strip()
            internal_label = valid_internal_label(payload.get("internal_label"))
            reusable = 1 if payload.get("reusable") in (True, 1, "true", "on", "yes") else 0
            if code == "GAME":
                description = CODES["GAME"][2]
                reusable = 1
            try:
                amount = round(float(payload.get("amount", 0)), 2)
            except (TypeError, ValueError):
                amount = -1
            if not name or internal_label is None or amount < 0:
                self.send_json({"error": "Benutzername, interne Bezeichnung oder Betrag ist ungueltig."}, 400); return
            with DB_LOCK, db() as conn:
                result = conn.execute("UPDATE codes SET name = ?, amount = ?, description = ?, internal_label = ?, reusable = ?, updated_at = ? WHERE code = ?", (name, amount, description, internal_label, reusable, now(), code))
                if result.rowcount == 0:
                    self.send_json({"error": "Code nicht gefunden."}, 404); return
                conn.commit()
            sync = sync_iobroker()
            self.send_json({"ok": True, "code": code, "data": admin_dashboard(), "sync": sync}); return
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
            internal_label = valid_internal_label(payload.get("internal_label"))
            reusable = 1 if payload.get("reusable") in (True, 1, "true", "on", "yes") else 0
            try: amount = round(float(payload.get("amount", 0)), 2)
            except (TypeError, ValueError): amount = -1
            name = valid_owner(name)
            if len(code) != 4 or not code.isalnum() or not name or internal_label is None or amount < 0:
                self.send_json({"error": "Code, Benutzername, interne Bezeichnung oder Betrag ist ungueltig."}, 400); return
            with DB_LOCK, db() as conn:
                try:
                    conn.execute("INSERT INTO codes (code, name, amount, description, internal_label, active, reusable, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)", (code, name, amount, description, internal_label, reusable, now(), now()))
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
                conn.execute("DELETE FROM redeemed WHERE code = ?", (code,))
                conn.execute("DELETE FROM redemption_events WHERE code = ?", (code,)); conn.commit()
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
        internal_label = valid_internal_label(payload.get("internal_label"))
        reusable = 1 if payload.get("reusable") in (True, 1, "true", "on", "yes") else 0
        if code == "GAME":
            description = CODES["GAME"][2]
            reusable = 1
        try: amount = round(float(payload.get("amount", 0)), 2)
        except (TypeError, ValueError): amount = -1
        if amount < 0 or internal_label is None:
            self.send_json({"error": "Interne Bezeichnung oder Betrag ist ungueltig."}, 400); return
        if not name:
            self.send_json({"error": "Benutzername ist ungueltig."}, 400); return
        with DB_LOCK, db() as conn:
            result = conn.execute("UPDATE codes SET name = ?, amount = ?, description = ?, internal_label = ?, reusable = ?, updated_at = ? WHERE code = ?", (name, amount, description, internal_label, reusable, now(), code))
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
                conn.execute("DELETE FROM redemption_events")
                conn.execute("DELETE FROM codes WHERE code NOT IN (?, ?)", ("FROH", "GAME"))
                timestamp = now()
                for protected_code in ("FROH", "GAME"):
                    protected_name, protected_amount, protected_description = CODES[protected_code]
                    conn.execute("INSERT OR IGNORE INTO codes (code, name, amount, description, internal_label, active, reusable, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, 1, ?, ?)", (protected_code, protected_name, protected_amount, protected_description, "", timestamp, timestamp))
                    conn.execute("UPDATE codes SET reusable = 1, active = 1, description = ? WHERE code = ?", (protected_description, protected_code))
                conn.commit()
            sync = sync_iobroker()
            self.send_json({"ok": True, "data": admin_dashboard(), "sync": sync}); return
        if path.startswith("/api/admin/code/"):
            code = urllib.parse.unquote(path[len("/api/admin/code/"):]).strip("/").upper()
            if code in ("FROH", "GAME"):
                self.send_json({"error": f"Der geschuetzte Code {code} kann nicht geloescht werden."}, 400); return
            with DB_LOCK, db() as conn:
                result = conn.execute("DELETE FROM codes WHERE code = ?", (code,))
                conn.execute("DELETE FROM redeemed WHERE code = ?", (code,))
                conn.execute("DELETE FROM redemption_events WHERE code = ?", (code,))
                conn.commit()
            if result.rowcount == 0:
                self.send_json({"error": "Code nicht gefunden."}, 404); return
            sync = sync_iobroker()
            self.send_json({"ok": True, "data": admin_dashboard(), "sync": sync}); return
        if path.startswith("/api/admin/redeemed/"):
            code = urllib.parse.unquote(path[len("/api/admin/redeemed/"):]).strip("/").upper()
            with DB_LOCK, db() as conn:
                conn.execute("DELETE FROM redeemed WHERE code = ?", (code,))
                conn.execute("DELETE FROM redemption_events WHERE code = ?", (code,)); conn.commit()
            sync = sync_iobroker()
            self.send_json({"ok": True, "data": admin_dashboard(), "sync": sync}); return
        self.send_error(HTTPStatus.NOT_FOUND)

if __name__ == "__main__":
    init_db()
    print(f"Weihnachts-Gutscheine laufen auf 0.0.0.0:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
