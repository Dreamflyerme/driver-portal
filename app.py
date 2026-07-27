import os
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from jinja2 import ChoiceLoader, DictLoader


APP_ROOT = os.path.dirname(os.path.abspath(__file__))
LOCAL_DB = os.path.join(APP_ROOT, "data", "driver_portal.sqlite3")


def default_database_path():
    return LOCAL_DB

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-before-production")
app.config["DATABASE_PATH"] = os.environ.get("DATABASE_PATH", default_database_path())


LEGACY_REQUEST_LABELS = [
    "Running late",
    "Mechanical issue",
    "Need phone call",
    "Pickup or delivery query",
    "Fatigue or break update",
    "Paperwork issue",
]

BASELINE_REQUEST_TYPES = [
    {
        "label": "CIPs",
        "sort_order": 10,
        "button_color": "#2563eb",
        "form_schema": {
            "receipt_mode": "ack_done",
            "fields": [
                {
                    "name": "cip_action",
                    "label": "CIP action",
                    "type": "choice",
                    "required": True,
                    "choices": [
                        "Add CIP Start Of Day",
                        "Add CIP End Of Day",
                        "Remove CIP Start of day",
                        "Remove CIP End Of Day",
                    ],
                }
            ]
        },
    },
    {
        "label": "Milk Left Behind",
        "sort_order": 20,
        "button_color": "#b86b00",
        "form_schema": {
            "receipt_mode": "none",
            "fields": [
                {
                    "name": "supply_number",
                    "label": "Supply number",
                    "type": "text",
                    "required": True,
                },
                {
                    "name": "milk_volume",
                    "label": "Milk left behind",
                    "type": "choice",
                    "required": True,
                    "choices": ["0-500L", "500-1000L", "1000L+"],
                },
                {
                    "name": "milk_stirred",
                    "label": "Is milk being stirred?",
                    "type": "choice",
                    "required": True,
                    "choices": ["Yes", "No"],
                    "show_when": {"field": "milk_volume", "equals": "0-500L"},
                },
            ]
        },
    },
    {
        "label": "Already Started Milking",
        "sort_order": 30,
        "button_color": "#7c3aed",
        "form_schema": {
            "receipt_mode": "none",
            "fields": [
                {
                    "name": "supply_number",
                    "label": "Supply number",
                    "type": "text",
                    "required": True,
                }
            ]
        },
    },
    {
        "label": "Still Milking",
        "sort_order": 40,
        "button_color": "#0f766e",
        "form_schema": {
            "receipt_mode": "none",
            "fields": [
                {
                    "name": "supply_number",
                    "label": "Supply number",
                    "type": "text",
                    "required": True,
                }
            ]
        },
    },
    {
        "label": "I'm Complete - Have Room",
        "sort_order": 50,
        "button_color": "#138a57",
        "form_schema": {"receipt_mode": "ack_only", "fields": []},
    },
    {
        "label": "Call Me Back",
        "sort_order": 60,
        "button_color": "#dc2626",
        "form_schema": {"receipt_mode": "ack_only", "fields": []},
    },
    {
        "label": "Split has been cleared",
        "sort_order": 65,
        "button_color": "#0891b2",
        "form_schema": {
            "receipt_mode": "none",
            "fields": [
                {
                    "name": "supply_number",
                    "label": "Supply number",
                    "type": "text",
                    "required": True,
                }
            ],
        },
    },
    {
        "label": "Free Text",
        "sort_order": 70,
        "button_color": "#334155",
        "form_schema": {
            "receipt_mode": "none",
            "note_label": "Message",
            "note_placeholder": "Type the message for Dispatch",
            "note_required": True,
            "fields": [],
        },
    },
]


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_db():
    db_path = app.config["DATABASE_PATH"]
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    if (
        os.environ.get("DATABASE_PATH")
        and db_path != LOCAL_DB
        and not os.path.exists(db_path)
        and os.path.exists(LOCAL_DB)
    ):
        shutil.copy2(LOCAL_DB, db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def query_all(sql, params=()):
    with closing(get_db()) as db:
        return db.execute(sql, params).fetchall()


def query_one(sql, params=()):
    with closing(get_db()) as db:
        return db.execute(sql, params).fetchone()


def execute(sql, params=()):
    with closing(get_db()) as db:
        cur = db.execute(sql, params)
        db.commit()
        return cur.lastrowid


def ensure_column(db, table, column, definition):
    columns = {
        row["name"]
        for row in db.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate_db(db):
    ensure_column(db, "request_types", "form_schema", "TEXT")
    ensure_column(db, "request_types", "button_color", "TEXT DEFAULT '#2563eb'")
    ensure_column(db, "driver_requests", "details_json", "TEXT")
    ensure_column(db, "driver_requests", "supply_number", "TEXT")
    ensure_column(db, "request_comments", "visible_to_driver", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(db, "users", "access_role", "TEXT")
    ensure_column(db, "users", "driver_number", "TEXT")
    ensure_column(db, "users", "home_depot_id", "INTEGER REFERENCES depots(id) ON DELETE SET NULL")
    ensure_column(db, "users", "access_status", "TEXT NOT NULL DEFAULT 'approved'")
    ensure_column(db, "users", "reviewed_at", "TEXT")
    ensure_column(db, "users", "reviewed_by", "INTEGER")
    ensure_column(db, "users", "must_change_password", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(db, "users", "default_truck_number", "TEXT")
    ensure_column(db, "users", "default_depot_id", "INTEGER REFERENCES depots(id) ON DELETE SET NULL")
    ensure_column(db, "users", "default_desk_profile_id", "INTEGER REFERENCES desk_profiles(id) ON DELETE SET NULL")


def init_db():
    with closing(get_db()) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('driver', 'dispatch', 'admin')),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dispatcher_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 100
            );

            CREATE TABLE IF NOT EXISTS depots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                dispatcher_group_id INTEGER REFERENCES dispatcher_groups(id) ON DELETE SET NULL,
                active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 100
            );

            CREATE TABLE IF NOT EXISTS request_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 100,
                button_color TEXT DEFAULT '#2563eb',
                form_schema TEXT
            );

            CREATE TABLE IF NOT EXISTS driver_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                driver_name TEXT NOT NULL,
                truck_number TEXT NOT NULL,
                depot_id INTEGER REFERENCES depots(id) ON DELETE SET NULL,
                depot_name TEXT NOT NULL,
                dispatcher_group_name TEXT,
                request_type_id INTEGER REFERENCES request_types(id) ON DELETE SET NULL,
                request_type_label TEXT NOT NULL,
                supply_number TEXT,
                note TEXT,
                details_json TEXT,
                status TEXT NOT NULL CHECK(status IN ('new', 'acknowledged', 'done')),
                created_at TEXT NOT NULL,
                acknowledged_at TEXT,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS request_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL REFERENCES driver_requests(id) ON DELETE CASCADE,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                author_name TEXT NOT NULL,
                body TEXT NOT NULL,
                visible_to_driver INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dispatch_preferences (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                dispatcher_group_id INTEGER NOT NULL REFERENCES dispatcher_groups(id) ON DELETE CASCADE,
                PRIMARY KEY (user_id, dispatcher_group_id)
            );

            CREATE TABLE IF NOT EXISTS desk_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                is_default INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 100
            );

            CREATE TABLE IF NOT EXISTS desk_profile_groups (
                desk_profile_id INTEGER NOT NULL REFERENCES desk_profiles(id) ON DELETE CASCADE,
                dispatcher_group_id INTEGER NOT NULL REFERENCES dispatcher_groups(id) ON DELETE CASCADE,
                PRIMARY KEY (desk_profile_id, dispatcher_group_id)
            );

            CREATE TABLE IF NOT EXISTS user_depots (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                depot_id INTEGER NOT NULL REFERENCES depots(id) ON DELETE CASCADE,
                PRIMARY KEY (user_id, depot_id)
            );
            """
        )
        migrate_db(db)
        db.commit()

    seed_defaults()
    app.config["_DB_READY_FOR"] = app.config["DATABASE_PATH"]


def seed_defaults():
    groups = [
        "Lower South/Upper North",
        "Te Rapa",
        "Te Awamutu",
        "Whareroa",
        "Pahiatua",
        "Darfield",
        "Clandeboye",
    ]
    depots_by_group = {
        "Lower South/Upper North": [
            "Kauri",
            "Dunedin",
            "Stirling",
            "Edendale",
            "Winton",
        ],
        "Te Rapa": ["Te Rapa", "Waitoa"],
        "Te Awamutu": [
            "Te Awamutu",
            "Hautapu",
            "Lichfield",
            "Edgecumbe",
            "Reporoa",
            "Tirau",
        ],
        "Whareroa": ["Whareroa", "Swans Road"],
        "Pahiatua": ["Pahiatua", "Longburn"],
        "Darfield": ["Takaka", "Brightwater", "Amberley", "Darfield"],
        "Clandeboye": ["Clandeboye", "Ashburton", "Studholme"],
    }
    with closing(get_db()) as db:
        for idx, name in enumerate(groups, start=1):
            db.execute(
                """
                INSERT OR IGNORE INTO dispatcher_groups (name, sort_order)
                VALUES (?, ?)
                """,
                (name, idx * 10),
            )
        for label, depot_names in depots_by_group.items():
            group = db.execute(
                "SELECT id FROM dispatcher_groups WHERE name = ?", (label,)
            ).fetchone()
            for idx, depot_name in enumerate(depot_names, start=1):
                db.execute(
                    """
                    INSERT OR IGNORE INTO depots
                        (name, dispatcher_group_id, sort_order)
                    VALUES (?, ?, ?)
                    """,
                    (depot_name, group["id"], idx * 10),
                )
        for item in BASELINE_REQUEST_TYPES:
            db.execute(
                """
                INSERT INTO request_types (label, sort_order, active, button_color, form_schema)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(label) DO UPDATE SET
                    sort_order = excluded.sort_order,
                    active = 1,
                    button_color = excluded.button_color,
                    form_schema = excluded.form_schema
                """,
                (
                    item["label"],
                    item["sort_order"],
                    item["button_color"],
                    json.dumps(item["form_schema"]),
                ),
            )
        for label in LEGACY_REQUEST_LABELS:
            db.execute(
                "UPDATE request_types SET active = 0 WHERE label = ?",
                (label,),
            )

        user_count = db.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
        if user_count == 0:
            db.execute(
                """
                INSERT INTO users
                    (username, display_name, password_hash, role, active, created_at)
                VALUES (?, ?, ?, 'admin', 1, ?)
                """,
                (
                    "admin",
                    "Portal Admin",
                    generate_password_hash("admin123"),
                    now_iso(),
                ),
            )
        seed_users = [
            ("demo_driver", "Demo Driver", "driver123", "driver"),
            ("demo_dispatch", "Demo Dispatch", "dispatch123", "dispatch"),
        ]
        for username, display_name, password, role in seed_users:
            exists = db.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            if not exists:
                db.execute(
                    """
                    INSERT INTO users
                        (username, display_name, password_hash, role, active, created_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (
                        username,
                        display_name,
                        generate_password_hash(password),
                        role,
                        now_iso(),
                    ),
                )
        desk_profiles = [
            (
                "Upper North/Lower South",
                "Standard desk for Kauri and Lower South work.",
                0,
                10,
                ["Lower South/Upper North"],
            ),
            (
                "Central South",
                "Standard desk profile for Clandeboye and Darfield queues.",
                0,
                20,
                ["Clandeboye", "Darfield"],
            ),
        ]
        for name, description, is_default, sort_order, group_names in desk_profiles:
            db.execute(
                """
                INSERT INTO desk_profiles
                    (name, description, is_default, active, sort_order)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(name) DO UPDATE SET
                    description = excluded.description,
                    sort_order = excluded.sort_order
                """,
                (name, description, is_default, sort_order),
            )
            profile = db.execute(
                "SELECT id FROM desk_profiles WHERE name = ?", (name,)
            ).fetchone()
            for group_name in group_names:
                group = db.execute(
                    "SELECT id FROM dispatcher_groups WHERE name = ?", (group_name,)
                ).fetchone()
                if group:
                    db.execute(
                        """
                        INSERT OR IGNORE INTO desk_profile_groups
                            (desk_profile_id, dispatcher_group_id)
                        VALUES (?, ?)
                        """,
                        (profile["id"], group["id"]),
                    )
        db.commit()


@app.before_request
def ensure_db():
    if app.config.get("_DB_READY_FOR") != app.config["DATABASE_PATH"]:
        init_db()


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return query_one(
        "SELECT id, username, display_name, COALESCE(access_role, role) AS role, active, driver_number, home_depot_id, access_status, must_change_password, default_truck_number, default_depot_id, default_desk_profile_id FROM users WHERE id = ?",
        (user_id,),
    )


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or not user["active"]:
            session.clear()
            return redirect(url_for("login"))
        if user["must_change_password"] and request.endpoint not in {"change_password", "logout", "static"}:
            return redirect(url_for("change_password", required=1))
        return view(*args, **kwargs)

    return wrapped


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            user = current_user()
            if user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def rows_to_dicts(rows):
    return [dict(row) for row in rows]


def depot_options(active_only=True):
    where = "WHERE d.active = 1 AND g.active = 1" if active_only else ""
    return query_all(
        f"""
        SELECT d.id, d.name, d.active, d.sort_order,
               g.id AS group_id, g.name AS group_name
        FROM depots d
        LEFT JOIN dispatcher_groups g ON g.id = d.dispatcher_group_id
        {where}
        ORDER BY COALESCE(g.sort_order, 999), g.name, d.sort_order, d.name
        """
    )


def request_type_options(active_only=True):
    where = "WHERE active = 1" if active_only else ""
    return query_all(
        f"""
        SELECT id, label, active, sort_order, button_color, form_schema
        FROM request_types
        {where}
        ORDER BY sort_order, label
        """
    )


def sanitize_color(value):
    color = (value or "#2563eb").strip()
    if len(color) == 7 and color.startswith("#"):
        allowed = set("0123456789abcdefABCDEF")
        if all(char in allowed for char in color[1:]):
            return color
    return "#2563eb"


def receipt_mode_label(raw):
    return {
        "none": "No driver receipt",
        "ack_only": "Acknowledge only",
        "ack_done": "Acknowledge + done",
    }.get(raw or "none", "No driver receipt")


def parse_form_schema(raw):
    if not raw:
        return {"fields": []}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"fields": []}
    parsed.setdefault("fields", [])
    parsed.setdefault("receipt_mode", "none")
    parsed.setdefault("version", 2)
    for index, field in enumerate(parsed["fields"]):
        field.setdefault("id", field.get("name") or f"field_{index + 1}")
        field.setdefault("name", field["id"])
    return parsed


def receipt_mode_for_schema(schema):
    mode = schema.get("receipt_mode", "none")
    if mode not in {"none", "ack_only", "ack_done"}:
        return "none"
    return mode


def driver_status_for(status, receipt_mode):
    if receipt_mode == "ack_done":
        return {
            "new": ("Sent", "new"),
            "acknowledged": ("Acknowledged", "acknowledged"),
            "done": ("Done", "done"),
        }.get(status, ("Sent", "new"))
    if receipt_mode == "ack_only":
        if status in {"acknowledged", "done"}:
            return ("Acknowledged", "acknowledged")
        return ("Sent", "new")
    return ("Sent", "new")


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_age(value):
    created = parse_iso(value)
    if not created:
        return ""
    delta = datetime.now(timezone.utc) - created
    total_minutes = max(0, int(delta.total_seconds() // 60))
    if total_minutes < 60:
        return f"{total_minutes}m"
    hours = total_minutes // 60
    if hours < 48:
        return f"{hours}h"
    return f"{hours // 24}d"


def request_type_cards():
    cards = []
    for row in request_type_options():
        item = dict(row)
        item["form_schema"] = parse_form_schema(item.get("form_schema"))
        item["button_color"] = sanitize_color(item.get("button_color"))
        cards.append(item)
    return cards


def request_type_admin_rows():
    rows = []
    for row in request_type_options(active_only=False):
        item = dict(row)
        schema = parse_form_schema(item.get("form_schema"))
        item["button_color"] = sanitize_color(item.get("button_color"))
        item["receipt_mode"] = receipt_mode_for_schema(schema)
        item["receipt_label"] = receipt_mode_label(item["receipt_mode"])
        item["field_count"] = len(schema.get("fields", []))
        rows.append(item)
    return rows


def field_is_visible(field, values):
    condition = field.get("show_when")
    if not condition:
        return True
    return values.get(condition.get("field")) == condition.get("equals")


def collect_request_details(schema):
    details = {}
    errors = []
    fields = schema.get("fields", [])

    for field in fields:
        name = field.get("name")
        if not name:
            continue
        value = request.form.get(f"detail_{name}", "").strip()
        details[name] = value

    for field in fields:
        name = field.get("name")
        if not name or not field_is_visible(field, details):
            details.pop(name, None)
            continue
        if field.get("required") and not details.get(name):
            errors.append(f"{field.get('label', name)} is required.")
        choices = field.get("choices") or []
        if details.get(name) and choices and details[name] not in choices:
            errors.append(f"{field.get('label', name)} has an invalid value.")

    return details, errors


def supply_number_from_details(details):
    return (details.get("supply_number") or "").strip()


def request_details_for_display(details_json, schema=None):
    if not details_json:
        return []
    try:
        details = json.loads(details_json)
    except json.JSONDecodeError:
        return []

    labels = {}
    if schema:
        for field in schema.get("fields", []):
            labels[field.get("name")] = field.get("label", field.get("name"))

    return [
        {"label": labels.get(key, key.replace("_", " ").title()), "value": value}
        for key, value in details.items()
        if value and key != "supply_number"
    ]


def details_summary(details):
    if not details:
        return ""
    return "; ".join(f"{detail['label']}: {detail['value']}" for detail in details)


def enrich_request_item(row):
    item = dict(row)
    type_row = query_one(
        "SELECT form_schema FROM request_types WHERE id = ?",
        (row["request_type_id"],),
    )
    schema = parse_form_schema(type_row["form_schema"]) if type_row else {"fields": []}
    receipt_mode = receipt_mode_for_schema(schema)
    driver_label, driver_class = driver_status_for(item["status"], receipt_mode)
    details = request_details_for_display(item["details_json"], schema)
    item["details"] = details
    item["details_summary"] = details_summary(details)
    if not item.get("supply_number") and item.get("details_json"):
        try:
            item["supply_number"] = supply_number_from_details(json.loads(item["details_json"]))
        except json.JSONDecodeError:
            item["supply_number"] = ""
    item["receipt_mode"] = receipt_mode
    item["driver_status_label"] = driver_label
    item["driver_status_class"] = driver_class
    item["age"] = format_age(item["created_at"])
    item["row_class"] = {
        "new": "row-new",
        "acknowledged": "row-acknowledged",
        "done": "row-done",
    }.get(item["status"], "row-new")
    item["can_acknowledge"] = item["status"] == "new" and receipt_mode != "none"
    item["can_complete"] = item["status"] in {"new", "acknowledged"}
    return item


def group_options(active_only=True):
    where = "WHERE active = 1" if active_only else ""
    return query_all(
        f"""
        SELECT id, name, active, sort_order
        FROM dispatcher_groups
        {where}
        ORDER BY sort_order, name
        """
    )


def desk_profile_options(active_only=True):
    where = "WHERE active = 1" if active_only else ""
    profiles = rows_to_dicts(
        query_all(
            f"""
            SELECT id, name, description, is_default, active, sort_order
            FROM desk_profiles
            {where}
            ORDER BY sort_order, name
            """
        )
    )
    for profile in profiles:
        groups = rows_to_dicts(
            query_all(
                """
                SELECT g.id, g.name
                FROM desk_profile_groups pg
                JOIN dispatcher_groups g ON g.id = pg.dispatcher_group_id
                WHERE pg.desk_profile_id = ?
                ORDER BY g.sort_order, g.name
                """,
                (profile["id"],),
            )
        )
        profile["groups"] = groups
        profile["group_ids"] = [group["id"] for group in groups]
        profile["group_names"] = [group["name"] for group in groups]
    return profiles


def desk_profile_group_names(profile_id):
    if not profile_id:
        return []
    row = query_one(
        "SELECT id FROM desk_profiles WHERE id = ? AND active = 1",
        (profile_id,),
    )
    if not row:
        return []
    return [
        group["name"]
        for group in query_all(
            """
            SELECT g.name
            FROM desk_profile_groups pg
            JOIN dispatcher_groups g ON g.id = pg.dispatcher_group_id
            WHERE pg.desk_profile_id = ? AND g.active = 1
            ORDER BY g.sort_order, g.name
            """,
            (profile_id,),
        )
    ]


BASE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }} | Driver Dispatch Portal</title>
  <style>
    :root {
      --ink: #17202a;
      --muted: #64748b;
      --line: #d9e2ec;
      --paper: #f7fafc;
      --panel: #ffffff;
      --blue: #2563eb;
      --blue-dark: #1e40af;
      --green: #138a57;
      --amber: #b86b00;
      --red: #b42318;
      --shadow: 0 18px 42px rgba(15, 23, 42, .08);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        linear-gradient(180deg, #eef6ff 0, rgba(238, 246, 255, 0) 280px),
        var(--paper);
      min-height: 100vh;
    }
    a { color: inherit; }
    .shell { width: min(1180px, calc(100% - 28px)); margin: 0 auto; }
    .topbar {
      background: rgba(255, 255, 255, .86);
      backdrop-filter: blur(16px);
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      z-index: 10;
    }
    .topbar-inner {
      min-height: 64px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    .brand { font-weight: 800; letter-spacing: 0; }
    .nav { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .nav a, .chip {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      padding: 8px 11px;
      border-radius: 8px;
      text-decoration: none;
      font-size: 14px;
    }
    main { padding: 28px 0 48px; }
    .hero {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }
    h1 { margin: 0; font-size: clamp(28px, 4vw, 42px); line-height: 1.05; }
    h2 { margin: 0 0 14px; font-size: 22px; }
    h3 { margin: 0 0 12px; font-size: 17px; }
    p { margin: 0 0 12px; color: var(--muted); }
    .grid { display: grid; gap: 16px; }
    .grid.two { grid-template-columns: minmax(0, 1fr) minmax(320px, .55fr); }
    .grid.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 18px;
    }
    .panel.tight { padding: 12px; box-shadow: none; }
    label { display: block; font-weight: 700; margin: 0 0 7px; font-size: 14px; }
    input, select, textarea {
      width: 100%;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      padding: 11px 12px;
      font: inherit;
      min-height: 43px;
    }
    textarea { resize: vertical; min-height: 88px; }
    .field { margin-bottom: 13px; }
    .inline { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .btn {
      border: 0;
      border-radius: 8px;
      padding: 10px 13px;
      font: inherit;
      font-weight: 800;
      color: #fff;
      background: var(--blue);
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 42px;
      gap: 7px;
    }
    .btn:hover { background: var(--blue-dark); }
    .btn.secondary { background: #334155; }
    .btn.green { background: var(--green); }
    .btn.amber { background: var(--amber); }
    .btn.red { background: var(--red); }
    .btn.ghost {
      background: #fff;
      color: var(--ink);
      border: 1px solid var(--line);
    }
    .btn.full { width: 100%; }
    .request-buttons {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 10px;
    }
    .request-buttons .btn {
      min-height: 72px;
      white-space: normal;
      text-align: center;
      line-height: 1.2;
    }
    .status {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 5px 9px;
      font-size: 12px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: .03em;
    }
    .status.new { background: #dbeafe; color: #1d4ed8; }
    .status.acknowledged { background: #fef3c7; color: #92400e; }
    .status.done { background: #dcfce7; color: #166534; }
    .list { display: grid; gap: 10px; }
    .item {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 13px;
      background: #fff;
    }
    .item-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      margin-bottom: 7px;
    }
    .meta { color: var(--muted); font-size: 13px; line-height: 1.45; }
    .flash {
      padding: 12px 14px;
      border: 1px solid #bfdbfe;
      background: #eff6ff;
      color: #1e3a8a;
      border-radius: 8px;
      margin-bottom: 14px;
    }
    table { width: 100%; border-collapse: collapse; }
    th, td {
      text-align: left;
      border-bottom: 1px solid var(--line);
      padding: 10px 8px;
      vertical-align: top;
      font-size: 14px;
    }
    th { color: #475569; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
    .table-wrap { overflow-x: auto; }
    .dispatch-tools {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 12px;
      box-shadow: var(--shadow);
    }
    .segmented {
      display: inline-flex;
      gap: 4px;
      background: #e2e8f0;
      border-radius: 8px;
      padding: 4px;
    }
    .segmented a {
      text-decoration: none;
      border-radius: 7px;
      padding: 7px 10px;
      font-size: 13px;
      font-weight: 900;
    }
    .segmented a.active { background: #0f172a; color: #fff; }
    .dispatch-table {
      width: 100%;
      min-width: 1180px;
      border-collapse: collapse;
      box-shadow: var(--shadow);
      overflow: hidden;
      border-radius: 8px;
      font-size: 13px;
    }
    .dispatch-table th {
      background: #0f172a;
      color: #fff;
      padding: 7px 8px;
      font-size: 11px;
      border-bottom: 0;
    }
    .dispatch-table td {
      padding: 8px;
      border-bottom: 1px solid rgba(15, 23, 42, .08);
      font-size: 13px;
    }
    .dispatch-table .row-new { background: #fee2e2; }
    .dispatch-table .row-acknowledged { background: #fef3c7; }
    .dispatch-table .row-done { background: #dcfce7; }
    body:not(.multi-select-active) .select-col,
    body:not(.multi-select-active) .select-cell {
      display: none;
    }
    .select-cell input {
      width: 18px;
      min-height: 18px;
      height: 18px;
      margin: 0;
    }
    .bulk-delete-tools {
      display: none;
      gap: 8px;
      align-items: center;
      margin-top: 10px;
    }
    body.multi-select-active .bulk-delete-tools {
      display: flex;
    }
    .live-indicator {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      background: #ecfdf5;
      color: #047857;
      border: 1px solid #a7f3d0;
      padding: 5px 9px;
      font-size: 12px;
      font-weight: 900;
    }
    .queue-pill {
      display: inline-flex;
      align-items: center;
      border-radius: 7px;
      background: #dbeafe;
      color: #1d4ed8;
      padding: 3px 7px;
      font-weight: 900;
      font-size: 12px;
    }
    .action-pills { display: inline-flex; gap: 4px; align-items: center; }
    .action-pill {
      width: 22px;
      height: 22px;
      border: 0;
      border-radius: 999px;
      color: #fff;
      font-size: 11px;
      font-weight: 900;
      cursor: pointer;
      line-height: 22px;
      padding: 0;
      text-align: center;
    }
    .action-pill.n { background: #ef4444; }
    .action-pill.a { background: #f59e0b; }
    .action-pill.d { background: #22c55e; }
    .action-pill:disabled { opacity: .28; cursor: default; }
    .dispatch-row { cursor: default; }
    .dispatch-row:hover { outline: 2px solid rgba(37, 99, 235, .35); outline-offset: -2px; }
    .context-menu {
      position: fixed;
      z-index: 40;
      width: 260px;
      display: none;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 18px 45px rgba(15, 23, 42, .22);
      padding: 12px;
    }
    .modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 45;
      display: none;
      align-items: center;
      justify-content: center;
      background: rgba(15, 23, 42, .46);
      padding: 18px;
    }
    .modal {
      width: min(560px, 100%);
      background: #fff;
      border-radius: 8px;
      border: 1px solid var(--line);
      box-shadow: 0 24px 70px rgba(15, 23, 42, .25);
      padding: 18px;
    }
    .admin-tabs {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 16px;
    }
    .admin-tabs a {
      text-decoration: none;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 9px 12px;
      font-weight: 900;
      font-size: 14px;
    }
    .flow-preview {
      color: #fff;
      border-radius: 8px;
      padding: 9px 12px;
      font-weight: 900;
      min-width: 150px;
      text-align: center;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 48px;
    }
    .admin-section { margin-bottom: 14px; }
    .admin-section > summary {
      cursor: pointer;
      list-style: none;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      font-weight: 900;
      font-size: 20px;
    }
    .admin-section > summary::-webkit-details-marker { display: none; }
    .admin-section > summary::after {
      content: "+";
      border: 1px solid var(--line);
      border-radius: 999px;
      width: 28px;
      height: 28px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: #fff;
      font-size: 18px;
    }
    .admin-section[open] > summary::after { content: "-"; }
    .flow-tile-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 10px;
      margin-top: 12px;
    }
    .choice-tiles {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 9px;
    }
    .choice-tile {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafc;
      color: var(--ink);
      min-height: 54px;
      padding: 10px;
      font: inherit;
      font-weight: 900;
      cursor: pointer;
    }
    .choice-tile.selected {
      background: #0f172a;
      color: #fff;
      border-color: #0f172a;
    }
    input[type="color"] {
      min-height: 43px;
      padding: 4px;
      width: 68px;
    }
    .split-actions { display: flex; gap: 8px; align-items: flex-start; flex-wrap: wrap; }
    .comment { border-left: 3px solid #bfdbfe; padding-left: 10px; margin-top: 8px; }
    .small { font-size: 13px; color: var(--muted); }
    .danger-zone { border-color: #fecaca; }
    @media (max-width: 820px) {
      .grid.two, .grid.three { grid-template-columns: 1fr; }
      .hero { align-items: flex-start; flex-direction: column; }
      .topbar-inner { align-items: flex-start; padding: 12px 0; flex-direction: column; }
      table, thead, tbody, th, td, tr { display: block; }
      thead { display: none; }
      tr { border-bottom: 1px solid var(--line); padding: 8px 0; }
      td { border-bottom: 0; padding: 6px 0; }
    }

    .btn.compact { min-height: 34px; padding: 6px 10px; }
    .btn:disabled { opacity: .4; cursor: default; }
    .driver-flow[hidden], .send-state[hidden], [hidden] { display: none !important; }
    .flow-topline { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:18px; }
    .driver-question h3 { font-size:24px; margin-bottom:16px; }
    .question-heading { display:flex; justify-content:space-between; align-items:center; gap:10px; }
    .driver-answer-buttons { display:grid; gap:11px; }
    .driver-answer { width:100%; min-height:64px; border:2px solid #cbd5e1; border-radius:10px; background:#fff; color:var(--ink); font:inherit; font-size:18px; font-weight:800; padding:12px; cursor:pointer; }
    .driver-answer:active, .driver-answer.selected { border-color:var(--blue); background:#eff6ff; }
    .flow-actions { margin-top:16px; }
    .driver-next, .driver-send { min-height:58px; font-size:17px; }
    .review-card { border:1px solid var(--line); border-radius:10px; padding:14px; background:#f8fafc; }
    .review-row { display:flex; justify-content:space-between; gap:14px; padding:10px 0; border-bottom:1px solid var(--line); }
    .review-row:last-child { border-bottom:0; }
    .send-state { text-align:center; padding:26px 10px; }
    .success-mark { width:68px; height:68px; margin:0 auto 14px; border-radius:50%; background:#dcfce7; color:#166534; display:grid; place-items:center; font-size:38px; font-weight:900; }
    .error-message { background:#fee2e2; color:#991b1b; padding:12px; border-radius:8px; margin-top:10px; }
    .input-error { border-color:var(--red) !important; }
    .admin-flow-layout { align-items:start; }
    .admin-preview { position:sticky; top:16px; }
    .preview-phone { margin-top:14px; border:8px solid #0f172a; border-radius:26px; padding:18px 12px; min-height:360px; background:#f8fafc; }
    .preview-question { background:#fff; border:1px solid var(--line); border-radius:8px; padding:10px; margin-top:9px; }
    .question-editor form { margin:0; }
    .admin-create { margin: 12px 0 16px; }
    .compact-record { border:1px solid var(--line); border-radius:10px; background:#fff; overflow:hidden; margin-top:8px; }
    .compact-record > summary { cursor:pointer; list-style:none; display:grid; grid-template-columns:minmax(180px, 2fr) minmax(120px, 1fr) minmax(100px, .8fr) auto; align-items:center; gap:12px; padding:12px 14px; font-weight:800; }
    .compact-record > summary::-webkit-details-marker { display:none; }
    .compact-record > summary:hover { background:#f8fafc; }
    .compact-record > summary::after { content:'Manage'; color:var(--blue); font-size:13px; justify-self:end; }
    .compact-record[open] > summary { border-bottom:1px solid var(--line); background:#f8fafc; }
    .compact-record[open] > summary::after { content:'Close'; }
    .record-body { padding:14px; }
    .record-title { min-width:0; }
    .record-title strong, .record-title span { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .record-title span { color:var(--muted); font-size:12px; font-weight:600; margin-top:2px; }
    .status-badge { display:inline-flex; width:max-content; border-radius:999px; padding:4px 8px; font-size:12px; font-weight:900; background:#e2e8f0; color:#334155; }
    .status-badge.active { background:#dcfce7; color:#166534; }
    .status-badge.pending { background:#fef3c7; color:#92400e; }
    .status-badge.inactive { background:#fee2e2; color:#991b1b; }
    .record-actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; padding-top:12px; border-top:1px solid var(--line); }
    .routing-summary { color:var(--muted); font-size:13px; }
    .admin-list-head { display:grid; grid-template-columns:minmax(180px, 2fr) minmax(120px, 1fr) minmax(100px, .8fr) auto; gap:12px; padding:0 14px 5px; color:var(--muted); font-size:12px; font-weight:900; text-transform:uppercase; letter-spacing:.04em; }
    .user-role-group, .driver-depot-group { border:1px solid var(--line); border-radius:14px; margin:10px 0; background:rgba(255,255,255,.5); overflow:hidden; }
    .user-role-group > summary, .driver-depot-group > summary { cursor:pointer; list-style:none; padding:13px 14px; font-weight:900; display:flex; align-items:center; justify-content:space-between; gap:10px; }
    .user-role-group > summary::-webkit-details-marker, .driver-depot-group > summary::-webkit-details-marker { display:none; }
    .user-role-group > summary::after, .driver-depot-group > summary::after { content:'+'; font-size:20px; color:var(--muted); margin-left:auto; }
    .user-role-group[open] > summary::after, .driver-depot-group[open] > summary::after { content:'−'; }
    .user-role-group > .compact-record, .driver-depot-group > .compact-record { margin:8px 10px; }
    .driver-depot-group { margin:8px 12px 12px; background:var(--surface); }
    .driver-depot-group > summary { padding:11px 12px; }
    .empty-group { padding:0 14px 14px; }
    .pending-overview { display:flex; flex-wrap:wrap; gap:8px; margin:12px 0 18px; }
    .pending-link { display:inline-flex; align-items:center; gap:8px; padding:9px 12px; border-radius:999px; background:#fff7ed; border:1px solid #fed7aa; color:#9a3412; font-weight:900; text-decoration:none; }
    .pending-count { display:inline-grid; place-items:center; min-width:24px; height:24px; padding:0 7px; border-radius:999px; background:#f97316; color:white; font-size:12px; }
    .depot-people-group { border:1px solid var(--line); border-radius:14px; margin:10px 0; overflow:hidden; background:#fff; }
    .depot-people-group > summary { cursor:pointer; list-style:none; display:flex; align-items:center; gap:10px; padding:14px; font-weight:900; }
    .depot-people-group > summary::-webkit-details-marker { display:none; }
    .depot-people-group > summary::after { content:'+'; margin-left:auto; color:var(--muted); font-size:20px; }
    .depot-people-group[open] > summary::after { content:'−'; }
    .depot-people-group .compact-record { margin:8px 10px; }
    @media (max-width: 760px) {
      .driver-layout { display:block; }
      .driver-history-panel { margin-top:14px; }
      .driver-hero { align-items:flex-start; }
      .request-buttons { grid-template-columns:1fr; }
      .request-buttons .btn { min-height:68px; font-size:17px; }
      .driver-send-panel { padding:14px; }
      .admin-preview { position:static; }
    }
  .brand{display:flex;align-items:center}.dispatch-tools form.inline{gap:8px}.compact-record[style*="display: none"]{display:none!important}
</style>
</head>
<body>
  <header class="topbar">
    <div class="shell topbar-inner">
      <div class="brand"><img src="{{ url_for('static', filename='fonterra-logo.png') }}" alt="Fonterra" style="height:34px;vertical-align:middle;margin-right:10px;">Transport Driver Portal</div>
      <nav class="nav">
        {% if user %}
          <span class="chip">{{ user.display_name }} · {{ user.role|title }}</span>
          {% if user.role == 'driver' %}<a href="{{ url_for('driver_home') }}">Driver</a>{% endif %}
          {% if user.role in ['dispatch', 'admin'] %}<a href="{{ url_for('dispatch_dashboard') }}">Dispatch</a>{% endif %}
          {% if user.role == 'depot' %}<a href="{{ url_for('depot_access_dashboard') }}">Depot people</a>{% endif %}
          {% if user.role == 'admin' %}<a href="{{ url_for('admin_dashboard') }}">Admin</a>{% endif %}
          <a href="{{ url_for('change_password') }}">Change password</a>
          <a href="{{ url_for('logout') }}">Logout</a>
        {% endif %}
      </nav>
    </div>
  </header>
  <main class="shell">
    {% for message in get_flashed_messages() %}
      <div class="flash">{{ message }}</div>
    {% endfor %}
    {{ body|safe }}
  </main>
<script>
(function(){
  const key='portal-scroll:'+location.pathname;
  const editKey='portal-edited-section:'+location.pathname;
  const y=sessionStorage.getItem(key);
  if(y){requestAnimationFrame(()=>scrollTo(0,Number(y)));sessionStorage.removeItem(key);}
  const editedId=sessionStorage.getItem(editKey);
  if(editedId){const edited=document.getElementById(editedId);if(edited&&edited.tagName==='DETAILS')edited.open=false;sessionStorage.removeItem(editKey);}
  document.querySelectorAll('form').forEach(form=>form.addEventListener('submit',()=>{
    sessionStorage.setItem(key,String(scrollY));
    const section=form.closest('details[id]'); if(section)sessionStorage.setItem(editKey,section.id);
  }));
  const bindSearch=(id)=>{const el=document.getElementById(id);if(!el)return;el.addEventListener('input',()=>{
    const q=el.value.trim().toLowerCase(); let first=null;
    document.querySelectorAll('[data-driver-search]').forEach(card=>{const match=!q||card.dataset.driverSearch.includes(q);card.style.display=match?'':'none';if(match&&q){card.open=true;card.closest('details.depot-people-group')?.setAttribute('open','');if(!first)first=card;}});
    if(first)first.scrollIntoView({behavior:'smooth',block:'center'});
  });};
  bindSearch('admin-driver-search'); bindSearch('depot-driver-search');
  document.querySelectorAll('input[data-numeric-only]').forEach(input=>input.addEventListener('input',()=>input.value=input.value.replace(/\\D/g,'')));
})();
</script>
</body>
</html>
"""


def render_page(title, body, **context):
    return render_template_string(
        BASE_TEMPLATE,
        title=title,
        body=render_template_string(body, **context),
        user=current_user(),
    )


@app.route("/")
def index():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    if user["role"] == "driver":
        return redirect(url_for("driver_home"))
    if user["role"] == "dispatch":
        return redirect(url_for("dispatch_dashboard"))
    if user["role"] == "depot":
        return redirect(url_for("depot_access_dashboard"))
    return redirect(url_for("admin_dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = query_one("SELECT * FROM users WHERE username = ?", (username,))
        valid_password = bool(user and check_password_hash(user["password_hash"], password))
        if user and valid_password and user["access_status"] == "pending":
            flash("Your account is awaiting approval from your transport depot.")
        elif user and valid_password and user["access_status"] == "rejected":
            flash("Your access request was not approved. Contact your transport depot for assistance.")
        elif user and valid_password and not user["active"]:
            flash("Your account is inactive or disabled. Contact your transport depot for assistance.")
        elif not user:
            flash("Login failed. Check the driver number or username and password.")
        elif not valid_password:
            flash("Incorrect password.")
        else:
            session.clear()
            session["user_id"] = user["id"]
            if user["role"] == "driver":
                session.pop("shift_profile", None)
            if user["must_change_password"]:
                return redirect(url_for("change_password", required=1))
            return redirect(url_for("index"))

    body = """
    <section class="panel" style="max-width:460px;margin:48px auto;text-align:center;">
      <img src="{{ url_for('static', filename='fonterra-logo.png') }}" alt="Fonterra" style="max-width:300px;width:85%;margin:0 auto 18px;display:block;">
      <h1>Transport Driver Portal</h1>
      <p class="small">Non-urgent transport updates for Dispatch.</p>
      <form method="post" style="text-align:left;">
        <div class="field"><label for="username">Driver number or username</label><input id="username" name="username" autocomplete="username" required autofocus></div>
        <div class="field"><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required></div>
        <button class="btn full" type="submit">Login</button>
      </form>
      <details style="margin-top:12px;text-align:left;"><summary class="btn ghost full" style="list-style:none;text-align:center;">Forgot password?</summary><p class="small" style="margin-top:10px;">Contact your transport depot to have your password reset.</p></details>
      <a class="btn ghost full" style="margin-top:12px;" href="{{ url_for('request_access') }}">Request access</a>
    </section>
    """
    return render_page("Login", body)


@app.route("/request-access", methods=["GET", "POST"])
def request_access():
    depots = depot_options()
    if request.method == "POST":
        driver_number = request.form.get("driver_number", "").strip()
        display_name = request.form.get("display_name", "").strip()
        depot_id = request.form.get("depot_id", "").strip()
        password = request.form.get("password", "")
        if not driver_number or not display_name or not depot_id or len(password) < 6:
            flash("Complete all fields. Password must be at least 6 characters.")
        elif not query_one("SELECT id FROM depots WHERE id = ? AND active = 1", (depot_id,)):
            flash("Choose a valid depot.")
        else:
            username = driver_number
            try:
                execute("""
                    INSERT INTO users
                    (username, display_name, password_hash, role, active, created_at,
                     driver_number, home_depot_id, access_status)
                    VALUES (?, ?, ?, 'driver', 0, ?, ?, ?, 'pending')
                """, (username, display_name, generate_password_hash(password), now_iso(), driver_number, depot_id))
                flash("Access requested. Your depot team must approve the account before you can log in.")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("That driver number already has an account or pending request.")
    body = """
    <section class="panel" style="max-width:560px;margin:auto;">
      <h1>Request driver access</h1>
      <p>Your driver number will be your username. The depot team will review the request.</p>
      <form method="post">
        <div class="field"><label>Driver number</label><input name="driver_number" type="text" inputmode="numeric" pattern="[0-9]*" required autofocus></div>
        <div class="field"><label>Your name</label><input name="display_name" required></div>
        <div class="field"><label>Home depot</label><select name="depot_id" required><option value="">Choose depot</option>{% for depot in depots %}<option value="{{ depot.id }}">{{ depot.name }}</option>{% endfor %}</select></div>
        <div class="field"><label>Password</label><input name="password" type="password" minlength="6" autocomplete="new-password" required></div>
        <button class="btn full" type="submit">Submit access request</button>
        <a class="btn ghost full" style="margin-top:10px;" href="{{ url_for('login') }}">Back to login</a>
      </form>
    </section>
    """
    return render_page("Request access", body, depots=depots)


def permitted_depot_ids(user):
    if user["role"] == "admin":
        return [row["id"] for row in query_all("SELECT id FROM depots")]
    return [row["depot_id"] for row in query_all("SELECT depot_id FROM user_depots WHERE user_id = ?", (user["id"],))]


def depot_managed_driver(user_id, manager=None):
    manager = manager or current_user()
    target = query_one(
        """
        SELECT id, username, display_name, role, COALESCE(access_role, role) AS effective_role,
               active, driver_number, home_depot_id, access_status
        FROM users
        WHERE id = ?
        """,
        (user_id,),
    )
    if not target:
        abort(404)
    if target["effective_role"] != "driver":
        abort(403)
    if target["home_depot_id"] not in permitted_depot_ids(manager):
        abort(403)
    return target


@app.route("/depot-access")
@roles_required("depot", "admin")
def depot_access_dashboard():
    user = current_user()
    depot_ids = permitted_depot_ids(user)
    people = []
    managed_depots = []
    if depot_ids:
        placeholders = ",".join("?" for _ in depot_ids)
        managed_depots = [dict(row) for row in query_all(
            f"SELECT id, name FROM depots WHERE id IN ({placeholders}) AND active = 1 ORDER BY name",
            depot_ids,
        )]
        people = [dict(row) for row in query_all(
            f"""
            SELECT u.*, d.name AS depot_name
            FROM users u
            JOIN depots d ON d.id = u.home_depot_id
            WHERE COALESCE(u.access_role, u.role) = 'driver'
              AND u.home_depot_id IN ({placeholders})
            ORDER BY
              d.name,
              CASE u.access_status WHEN 'pending' THEN 0 ELSE 1 END,
              u.display_name
            """,
            depot_ids,
        )]
    for depot in managed_depots:
        depot["people"] = [person for person in people if person["home_depot_id"] == depot["id"]]
        depot["pending_count"] = sum(1 for person in depot["people"] if person["access_status"] == "pending")

    body = """
    <section class="hero">
      <div><h1>Depot people</h1><p>Approve and manage drivers for your assigned depots.</p></div>
    </section>
    {% set total_pending = managed_depots|sum(attribute='pending_count') %}
    {% if total_pending %}
      <section class="panel" style="border-color:#fed7aa;background:#fffaf5;">
        <h2 style="margin-bottom:6px;">Pending approvals <span class="status-badge pending">{{ total_pending }}</span></h2>
        <div class="pending-overview">
          {% for depot in managed_depots if depot.pending_count %}
            <a class="pending-link" href="#depot-{{ depot.id }}"><span>{{ depot.name }}</span><span class="pending-count">{{ depot.pending_count }}</span></a>
          {% endfor %}
        </div>
      </section>
    {% endif %}
    <section class="panel">
      <h2>Drivers by depot</h2><div class="field"><label for="depot-driver-search">Search driver number or name</label><input id="depot-driver-search" type="search" placeholder="Start typing..."></div>
      {% for depot in managed_depots %}
        <details id="depot-{{ depot.id }}" class="depot-people-group" {% if depot.pending_count %}open{% endif %}>
          <summary>
            <span>{{ depot.name }}</span>
            <span class="small">{{ depot.people|length }} driver{{ '' if depot.people|length == 1 else 's' }}</span>
            {% if depot.pending_count %}<span class="status-badge pending">{{ depot.pending_count }} pending</span>{% endif %}
          </summary>
          {% for item in depot.people %}
            <details class="compact-record" data-driver-search="{{ (item.display_name ~ ' ' ~ (item.driver_number or item.username))|lower }}">
              <summary>
                <span class="record-title"><strong>{{ item.display_name }}</strong><span>Driver {{ item.driver_number or item.username }}</span></span>
                <span>{{ item.depot_name }}</span>
                <span class="status-badge {{ 'active' if item.active else ('pending' if item.access_status == 'pending' else 'inactive') }}">{{ 'Active' if item.active else ('Pending' if item.access_status == 'pending' else ('Rejected' if item.access_status == 'rejected' else 'Inactive')) }}</span>
              </summary>
              <div class="record-body">
                <form id="depot-user-save-{{ item.id }}" method="post" action="{{ url_for('depot_update_driver', user_id=item.id) }}" class="grid three">
                  <div class="field"><label>Name</label><input name="display_name" value="{{ item.display_name }}" required></div>
                  <div class="field"><label>Driver number</label><input name="driver_number" value="{{ item.driver_number or item.username }}" required></div>
                  <div class="field"><label>Home depot</label><select name="home_depot_id" required>{% for destination in managed_depots %}<option value="{{ destination.id }}" {% if destination.id == item.home_depot_id %}selected{% endif %}>{{ destination.name }}</option>{% endfor %}</select></div>
                </form>
                <div class="record-actions">
                  {% if item.access_status == 'pending' %}
                    <form method="post" action="{{ url_for('approve_driver_access', user_id=item.id) }}"><button class="btn" type="submit">Approve</button></form>
                    <form method="post" action="{{ url_for('reject_driver_access', user_id=item.id) }}"><button class="btn red" type="submit">Reject</button></form>
                  {% endif %}
                  <button class="btn secondary" form="depot-user-save-{{ item.id }}" type="submit">Save details</button>
                  <details><summary class="btn amber" style="list-style:none;">Reset password</summary><form method="post" action="{{ url_for('depot_reset_driver_password', user_id=item.id) }}" class="inline" style="margin-top:8px;"><input name="password" type="password" minlength="6" placeholder="New password" required style="width:170px;"><button class="btn amber" type="submit">Apply</button></form></details>
                  <form method="post" action="{{ url_for('depot_toggle_driver', user_id=item.id) }}"><button class="btn ghost" type="submit">{{ 'Make inactive' if item.active else 'Make active' }}</button></form>
                  <form method="post" action="{{ url_for('depot_delete_driver', user_id=item.id) }}" onsubmit="return confirm('Delete this driver account? Existing request history will remain.');"><button class="btn red" type="submit">Delete</button></form>
                </div>
              </div>
            </details>
          {% else %}
            <p class="small empty-group">No drivers assigned to this depot.</p>
          {% endfor %}
        </details>
      {% else %}
        <p class="small">No depots are assigned to your account.</p>
      {% endfor %}
    </section>
    """
    return render_page("Depot people", body, managed_depots=managed_depots)


@app.route("/depot-access/<int:user_id>/approve", methods=["POST"])
@roles_required("depot", "admin")
def approve_driver_access(user_id):
    user = current_user()
    target = depot_managed_driver(user_id, user)
    if target["access_status"] != "pending":
        abort(404)
    execute(
        "UPDATE users SET active=1, access_status='approved', reviewed_at=?, reviewed_by=? WHERE id=?",
        (now_iso(), user["id"], user_id),
    )
    flash("Driver access approved.")
    return redirect(url_for("depot_access_dashboard"))


@app.route("/depot-access/<int:user_id>/reject", methods=["POST"])
@roles_required("depot", "admin")
def reject_driver_access(user_id):
    user = current_user()
    target = depot_managed_driver(user_id, user)
    if target["access_status"] != "pending":
        abort(404)
    execute(
        "UPDATE users SET active=0, access_status='rejected', reviewed_at=?, reviewed_by=? WHERE id=?",
        (now_iso(), user["id"], user_id),
    )
    flash("Driver access rejected.")
    return redirect(url_for("depot_access_dashboard"))


@app.route("/depot-access/<int:user_id>/update", methods=["POST"])
@roles_required("depot", "admin")
def depot_update_driver(user_id):
    manager = current_user()
    depot_managed_driver(user_id, manager)
    display_name = request.form.get("display_name", "").strip()
    driver_number = request.form.get("driver_number", "").strip()
    depot_id_raw = request.form.get("home_depot_id", "").strip()
    if not display_name or not driver_number or not depot_id_raw.isdigit():
        flash("Name, driver number and depot are required.")
        return redirect(url_for("depot_access_dashboard"))
    depot_id = int(depot_id_raw)
    if depot_id not in permitted_depot_ids(manager):
        abort(403)
    try:
        execute(
            "UPDATE users SET display_name=?, driver_number=?, username=?, home_depot_id=? WHERE id=?",
            (display_name, driver_number, driver_number, depot_id, user_id),
        )
        flash("Driver details updated.")
    except sqlite3.IntegrityError:
        flash("That driver number is already in use.")
    return redirect(url_for("depot_access_dashboard"))


@app.route("/depot-access/<int:user_id>/reset-password", methods=["POST"])
@roles_required("depot", "admin")
def depot_reset_driver_password(user_id):
    depot_managed_driver(user_id)
    password = request.form.get("password", "")
    if len(password) < 6:
        flash("Password must be at least 6 characters.")
    else:
        execute("UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?", (generate_password_hash(password), user_id))
        flash("Driver password reset.")
    return redirect(url_for("depot_access_dashboard"))


@app.route("/depot-access/<int:user_id>/toggle", methods=["POST"])
@roles_required("depot", "admin")
def depot_toggle_driver(user_id):
    target = depot_managed_driver(user_id)
    new_active = 0 if target["active"] else 1
    new_status = "approved" if new_active else target["access_status"]
    execute("UPDATE users SET active=?, access_status=? WHERE id=?", (new_active, new_status, user_id))
    flash("Driver status updated.")
    return redirect(url_for("depot_access_dashboard"))


@app.route("/depot-access/<int:user_id>/delete", methods=["POST"])
@roles_required("depot", "admin")
def depot_delete_driver(user_id):
    depot_managed_driver(user_id)
    execute("DELETE FROM users WHERE id=?", (user_id,))
    flash("Driver deleted. Existing request history is kept.")
    return redirect(url_for("depot_access_dashboard"))


@app.route("/admin/users/<int:user_id>/depots", methods=["POST"])
@roles_required("admin")
def admin_assign_user_depots(user_id):
    target=query_one("SELECT id, COALESCE(access_role, role) AS effective_role FROM users WHERE id=?", (user_id,))
    if not target or target["effective_role"] != "depot": abort(404)
    depot_ids=[int(v) for v in request.form.getlist("depot_id") if v.isdigit()]
    with closing(get_db()) as db:
        db.execute("DELETE FROM user_depots WHERE user_id=?", (user_id,))
        for depot_id in depot_ids:
            db.execute("INSERT OR IGNORE INTO user_depots (user_id, depot_id) VALUES (?, ?)", (user_id, depot_id))
        db.commit()
    flash("Depot assignments updated.")
    return redirect(url_for("admin_dashboard") + "#users")


@app.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    user = current_user()
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        stored_user = query_one("SELECT password_hash FROM users WHERE id = ?", (user["id"],))

        if not stored_user or not check_password_hash(stored_user["password_hash"], current_password):
            flash("Current password is incorrect.")
        elif len(new_password) < 6:
            flash("New password must be at least 6 characters.")
        elif new_password != confirm_password:
            flash("New passwords do not match.")
        elif check_password_hash(stored_user["password_hash"], new_password):
            flash("New password must be different from your current password.")
        else:
            execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                (generate_password_hash(new_password), user["id"]),
            )
            flash("Password changed successfully.")
            return redirect(url_for("index"))

    body = """
    <section class="panel" style="max-width:560px; margin:0 auto;">
      <div class="section-head">
        <div>
          <h1>{{ "Set a new password" if required else "Change password" }}</h1>
          <p class="muted">{% if required %}A temporary password was assigned to your account. Set your own password before continuing.{% else %}Update the password for {{ user.display_name }}.{% endif %}</p>
        </div>
      </div>
      <form method="post" class="stack" autocomplete="off">
        <div class="field">
          <label for="current_password">Current password</label>
          <input id="current_password" name="current_password" type="password" autocomplete="current-password" required autofocus>
        </div>
        <div class="field">
          <label for="new_password">New password</label>
          <input id="new_password" name="new_password" type="password" minlength="6" autocomplete="new-password" required>
          <div class="small">At least 6 characters.</div>
        </div>
        <div class="field">
          <label for="confirm_password">Confirm new password</label>
          <input id="confirm_password" name="confirm_password" type="password" minlength="6" autocomplete="new-password" required>
        </div>
        <div class="split-actions">
          <button class="btn" type="submit">Change password</button>
          {% if not required %}<a class="btn ghost" href="{{ url_for('index') }}">Cancel</a>{% endif %}
        </div>
      </form>
    </section>
    """
    return render_page("Change password", body, user=user, required=bool(request.args.get("required") or user["must_change_password"]))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/driver/profile", methods=["GET", "POST"])
@roles_required("driver")
def driver_profile():
    depots = depot_options()
    if request.method == "POST":
        driver_name = request.form.get("driver_name", "").strip()
        truck_number = request.form.get("truck_number", "").strip()
        depot_id = request.form.get("depot_id", "").strip()
        depot = query_one(
            """
            SELECT d.id, d.name, g.name AS group_name
            FROM depots d
            LEFT JOIN dispatcher_groups g ON g.id = d.dispatcher_group_id
            WHERE d.id = ? AND d.active = 1
            """,
            (depot_id,),
        )
        if not driver_name or not truck_number or not depot:
            flash("Please enter your name, truck number, and depot.")
        else:
            session["shift_profile"] = {
                "driver_name": driver_name,
                "truck_number": truck_number,
                "depot_id": depot["id"],
                "depot_name": depot["name"],
                "dispatcher_group_name": depot["group_name"],
            }
            if request.form.get("remember_defaults"):
                execute("UPDATE users SET display_name=?, default_truck_number=?, default_depot_id=? WHERE id=?", (driver_name, truck_number, depot["id"], current_user()["id"]))
                flash("Shift details saved and remembered for next login.")
            else:
                flash("Shift details saved for this login.")
            return redirect(url_for("driver_home"))

    profile = session.get("shift_profile", {})
    if not profile:
        u = current_user()
        profile = {"driver_name": u["display_name"], "truck_number": u["default_truck_number"] or "", "depot_id": u["default_depot_id"] or u["home_depot_id"]}
    body = """
    <section class="hero">
      <div>
        <h1>Shift details</h1>
        <p>Confirm these each time you log in. Update them any time you swap trucks.</p>
      </div>
    </section>
    <section class="panel" style="max-width: 560px;">
      <form method="post">
        <div class="field">
          <label for="driver_name">Driver name</label>
          <input id="driver_name" name="driver_name" value="{{ profile.get('driver_name', user.display_name) }}" required>
        </div>
        <div class="field">
          <label for="truck_number">Truck number</label>
          <input id="truck_number" name="truck_number" value="{{ profile.get('truck_number', '') }}" required>
        </div>
        <div class="field">
          <label for="depot_id">Depot based from</label>
          <select id="depot_id" name="depot_id" required>
            <option value="">Select depot</option>
            {% for depot in depots %}
              <option value="{{ depot.id }}" {% if profile.get('depot_id') == depot.id %}selected{% endif %}>
                {{ depot.name }}
              </option>
            {% endfor %}
          </select>
        </div>
        <label class="chip" style="display:flex;margin-bottom:12px;"><input type="checkbox" name="remember_defaults" style="width:auto;min-height:0;"> Remember these details for future logins</label><button class="btn" type="submit">Continue</button>
      </form>
    </section>
    """
    return render_page("Shift Details", body, depots=depots, profile=profile, user=current_user())


def require_shift_profile():
    if not session.get("shift_profile"):
        return redirect(url_for("driver_profile"))
    return None


@app.route("/driver")
@roles_required("driver")
def driver_home():
    redirect_response = require_shift_profile()
    if redirect_response:
        return redirect_response
    requests = driver_request_rows(current_user()["id"])
    body = """
    <section class="hero driver-hero">
      <div>
        <h1>Send Dispatch a note</h1>
        <p>{{ profile.driver_name }} · Truck {{ profile.truck_number }} · {{ profile.depot_name }}</p>
      </div>
      <div class="inline"><button id="driver-main-menu" class="btn ghost" type="button">Main Menu</button><a class="btn ghost" href="{{ url_for('driver_profile') }}">Shift details</a></div>
    </section>
    <div class="grid two driver-layout">
      <section class="panel driver-send-panel">
        <form id="request-form" method="post" action="{{ url_for('create_driver_request') }}">
          <input id="request_type_id" name="request_type_id" type="hidden" required>
          <div id="request-picker">
            <h2>What do you need?</h2>
            <div id="request-buttons" class="request-buttons">
              {% for item in request_types %}
                <button class="btn request-choice" data-request-id="{{ item.id }}" style="background: {{ item.button_color }};" type="button">{{ item.label }}</button>
              {% endfor %}
            </div>
          </div>
          <div id="flow-panel" class="driver-flow" hidden>
            <div class="flow-topline">
              <button id="flow-back" class="btn ghost compact" type="button">Back</button>
              <span id="flow-progress" class="chip"></span>
            </div>
            <h2 id="flow-title"></h2>
            <div id="flow-question"></div>
            <div id="flow-review" hidden></div>
            <div id="flow-actions" class="flow-actions"></div>
          </div>
          <div id="send-state" class="send-state" hidden aria-live="polite"></div>
        </form>
      </section>
      <section class="panel driver-history-panel">
        <div class="inline" style="justify-content: space-between; margin-bottom: 10px;">
          <h2 style="margin: 0;">Recent notes</h2>
          <span class="small" id="last-updated">Live</span>
        </div>
        <div id="driver-requests" class="list">{% include 'driver_request_items' %}</div>
      </section>
    </div>
    <script>
      function escapeHtml(value) {
        return String(value || "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char]));
      }
      function renderDetails(details) {
        if (!details || !details.length) return "";
        return `<div class="meta" style="margin-top:8px;">${details.map((detail) => `<strong>${escapeHtml(detail.label)}:</strong> ${escapeHtml(detail.value)}`).join("<br>")}</div>`;
      }
      async function refreshDriverRequests() {
        try {
          const response = await fetch("{{ url_for('driver_requests_api') }}", { headers: { "Accept": "application/json" } });
          if (!response.ok) return;
          const data = await response.json();
          document.getElementById("driver-requests").innerHTML = data.requests.map((item) => `
            <article class="item"><div class="item-head"><strong>${escapeHtml(item.request_type_label)}</strong><span class="status ${item.driver_status_class}">${escapeHtml(item.driver_status_label)}</span></div>
            <div class="meta">${escapeHtml(item.created_at)} · ${escapeHtml(item.depot_name)} · Truck ${escapeHtml(item.truck_number)}</div>${renderDetails(item.details)}
            ${item.note ? `<p style="margin-top:8px;">${escapeHtml(item.note)}</p>` : ""}
            ${item.comments.map((comment) => `<div class="comment"><strong>${escapeHtml(comment.author_name)}</strong><div>${escapeHtml(comment.body)}</div><div class="meta">${escapeHtml(comment.created_at)}</div></div>`).join("")}</article>`).join("") || `<div class="item"><p>No requests yet.</p></div>`;
          document.getElementById("last-updated").textContent = "Updated " + new Date().toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"});
        } catch (_) {}
      }

      const requestTypes = {{ request_types_json|tojson }};
      const form = document.getElementById("request-form");
      const picker = document.getElementById("request-picker");
      const flowPanel = document.getElementById("flow-panel");
      const flowTitle = document.getElementById("flow-title");
      const flowQuestion = document.getElementById("flow-question");
      const flowReview = document.getElementById("flow-review");
      const flowActions = document.getElementById("flow-actions");
      const flowProgress = document.getElementById("flow-progress");
      const flowBack = document.getElementById("flow-back");
      const sendState = document.getElementById("send-state");
      const requestInput = document.getElementById("request_type_id");
      const driverMainMenu = document.getElementById("driver-main-menu");
      let returnTimer = null;
      let selectedType = null;
      let visibleFields = [];
      let step = 0;
      let answers = {};
      let submitting = false;

      function fieldVisible(field) {
        const condition = field.show_when;
        if (!condition) return true;
        return answers[condition.field] === condition.equals;
      }
      function refreshVisibleFields() {
        visibleFields = ((selectedType && selectedType.form_schema && selectedType.form_schema.fields) || []).filter(fieldVisible);
        if (step > visibleFields.length) step = visibleFields.length;
      }
      function startFlow(id) {
        selectedType = requestTypes.find((item) => String(item.id) === String(id));
        if (!selectedType) return;
        requestInput.value = selectedType.id;
        answers = {};
        step = 0;
        picker.hidden = true;
        flowPanel.hidden = false;
        sendState.hidden = true;
        flowTitle.textContent = selectedType.label;
        refreshVisibleFields();
        renderStep();
      }
      function resetFlow() {
        if (returnTimer) { window.clearInterval(returnTimer); returnTimer = null; }
        selectedType = null; answers = {}; step = 0; submitting = false;
        requestInput.value = ""; flowPanel.hidden = true; sendState.hidden = true; picker.hidden = false;
        form.querySelectorAll('input[data-generated="true"], textarea[data-generated="true"]').forEach((el) => el.remove());
      }
      function goBack() {
        if (step <= 0) return resetFlow();
        step -= 1; renderStep();
      }
      function choose(field, value) {
        answers[field.name] = value;
        refreshVisibleFields();
        window.setTimeout(() => { step += 1; renderStep(); }, 90);
      }
      function saveTyped(field) {
        const input = document.getElementById("active-answer");
        const value = (input.value || "").trim();
        if (field.required && !value) { input.focus(); input.classList.add("input-error"); return; }
        answers[field.name] = value;
        refreshVisibleFields(); step += 1; renderStep();
      }
      function renderStep() {
        refreshVisibleFields();
        flowQuestion.innerHTML = ""; flowReview.hidden = true; flowActions.innerHTML = "";
        flowBack.textContent = step === 0 ? "All options" : "Back";
        if (step >= visibleFields.length) return renderReview();
        const field = visibleFields[step];
        flowProgress.textContent = `Question ${step + 1} of ${visibleFields.length}`;
        const requiredMark = field.required ? "" : '<span class="small">Optional</span>';
        flowQuestion.innerHTML = `<div class="driver-question"><div class="question-heading"><h3>${escapeHtml(field.label)}</h3>${requiredMark}</div><div id="answer-area"></div></div>`;
        const area = document.getElementById("answer-area");
        if (field.type === "choice" || field.type === "yes_no") {
          const choices = field.type === "yes_no" ? ["Yes", "No"] : (field.choices || []);
          area.innerHTML = `<div class="driver-answer-buttons">${choices.map((choice) => `<button type="button" class="driver-answer ${answers[field.name] === choice ? "selected" : ""}" data-value="${escapeHtml(choice)}">${escapeHtml(choice)}</button>`).join("")}</div>`;
          area.querySelectorAll(".driver-answer").forEach((button) => button.addEventListener("click", () => choose(field, button.dataset.value)));
          if (!field.required) flowActions.innerHTML = '<button class="btn ghost full" type="button" id="skip-answer">Skip</button>';
          document.getElementById("skip-answer")?.addEventListener("click", () => choose(field, ""));
        } else {
          const numericOnly = field.type === "number" || field.name === "supply_number";
          const multiline = field.type === "note";
          const numericAttrs = numericOnly ? ' inputmode="numeric" pattern="[0-9]*"' : '';
          area.innerHTML = multiline ? `<textarea id="active-answer" rows="4" placeholder="Type a short note">${escapeHtml(answers[field.name] || "")}</textarea>` : `<input id="active-answer" type="text"${numericAttrs} value="${escapeHtml(answers[field.name] || "")}" autocomplete="off">`;
          if (numericOnly) document.getElementById("active-answer").addEventListener("input", (event) => { event.target.value = event.target.value.replace(/\D/g, ""); });
          flowActions.innerHTML = '<button class="btn full driver-next" type="button">Continue</button>';
          flowActions.querySelector("button").addEventListener("click", () => saveTyped(field));
          document.getElementById("active-answer").addEventListener("keydown", (event) => { if (event.key === "Enter" && !multiline) { event.preventDefault(); saveTyped(field); }});
          document.getElementById("active-answer").focus();
        }
      }
      function renderReview() {
        flowProgress.textContent = "Review";
        flowReview.hidden = false;
        const rows = visibleFields.filter((field) => answers[field.name]).map((field) => `<div class="review-row"><span>${escapeHtml(field.label)}</span><strong>${escapeHtml(answers[field.name])}</strong></div>`).join("");
        const schema = selectedType.form_schema || {};
        flowReview.innerHTML = `<div class="review-card">${rows || '<p>No extra details required.</p>'}<div class="field" style="margin-top:14px;"><label for="review-note">${escapeHtml(schema.note_label || "Optional note")}</label><textarea id="review-note" placeholder="${escapeHtml(schema.note_placeholder || "Add context if needed")}" ${schema.note_required ? "required" : ""}></textarea></div></div>`;
        flowActions.innerHTML = '<button id="send-flow" class="btn full driver-send" type="button">Send to Dispatch</button>';
        document.getElementById("send-flow").addEventListener("click", submitFlow);
      }
      async function submitFlow() {
        if (submitting) return;
        const schema = selectedType.form_schema || {};
        const reviewNote = document.getElementById("review-note");
        if (schema.note_required && !reviewNote.value.trim()) { reviewNote.focus(); return; }
        submitting = true;
        const button = document.getElementById("send-flow"); button.disabled = true; button.textContent = "Sending…";
        const payload = new FormData(); payload.set("request_type_id", selectedType.id); payload.set("note", reviewNote.value.trim());
        visibleFields.forEach((field) => payload.set(`detail_${field.name}`, answers[field.name] || ""));
        try {
          const response = await fetch(form.action, { method: "POST", body: payload, headers: {"X-Requested-With":"fetch"} });
          if (!response.ok) throw new Error("send failed");
          flowPanel.hidden = true; sendState.hidden = false;
          let seconds = 5;
          sendState.innerHTML = `<div class="success-mark">✓</div><h2>Sent to Dispatch</h2><p>${escapeHtml(selectedType.label)}</p><button class="btn full" type="button" id="send-another">Main Menu (${seconds})</button>`;
          const mainButton = document.getElementById("send-another");
          mainButton.addEventListener("click", resetFlow);
          returnTimer = window.setInterval(() => {
            seconds -= 1;
            if (seconds <= 0) { resetFlow(); return; }
            mainButton.textContent = `Main Menu (${seconds})`;
          }, 1000);
          await refreshDriverRequests();
        } catch (_) {
          submitting = false; button.disabled = false; button.textContent = "Try again";
          sendState.hidden = false; sendState.innerHTML = '<div class="error-message">Could not send. Check the connection and try again.</div>';
        }
      }
      document.querySelectorAll(".request-choice").forEach((button) => button.addEventListener("click", () => startFlow(button.dataset.requestId)));
      flowBack.addEventListener("click", goBack);
      driverMainMenu.addEventListener("click", resetFlow);
      setInterval(refreshDriverRequests, 5000);
    </script>
    """
    return render_page("Driver", body, profile=session["shift_profile"], request_types=request_type_cards(), request_types_json=request_type_cards(), requests=requests)


app.jinja_loader = ChoiceLoader(
    [
        app.jinja_loader,
        DictLoader(
            {
                "driver_request_items": """
      {% for item in requests %}
        <article class="item">
          <div class="item-head">
            <strong>{{ item.request_type_label }}</strong>
            <span class="status {{ item.driver_status_class }}">{{ item.driver_status_label }}</span>
          </div>
          <div class="meta">{{ item.created_at }} · {{ item.depot_name }} · Truck {{ item.truck_number }}</div>
          {% if item.details %}
            <div class="meta" style="margin-top: 8px;">
              {% for detail in item.details %}
                <strong>{{ detail.label }}:</strong> {{ detail.value }}{% if not loop.last %}<br>{% endif %}
              {% endfor %}
            </div>
          {% endif %}
          {% if item.note %}<p style="margin-top: 8px;">{{ item.note }}</p>{% endif %}
          {% for comment in item.comments %}
            <div class="comment">
              <strong>{{ comment.author_name }}</strong>
              <div>{{ comment.body }}</div>
              <div class="meta">{{ comment.created_at }}</div>
            </div>
          {% endfor %}
        </article>
      {% else %}
        <div class="item"><p>No requests yet.</p></div>
      {% endfor %}
    """
            }
        ),
    ]
)


@app.route("/driver/request", methods=["POST"])
@roles_required("driver")
def create_driver_request():
    redirect_response = require_shift_profile()
    if redirect_response:
        return redirect_response
    request_type = query_one(
        "SELECT id, label, form_schema FROM request_types WHERE id = ? AND active = 1",
        (request.form.get("request_type_id"),),
    )
    if not request_type:
        flash("Choose a valid request type.")
        return redirect(url_for("driver_home"))

    schema = parse_form_schema(request_type["form_schema"])
    details, errors = collect_request_details(schema)
    supply_number = supply_number_from_details(details)
    note = request.form.get("note", "").strip()
    if schema.get("note_required") and not note:
        errors.append(f"{schema.get('note_label', 'Message')} is required.")
    if errors:
        flash(" ".join(errors))
        return redirect(url_for("driver_home"))

    profile = session["shift_profile"]
    execute(
        """
        INSERT INTO driver_requests
            (driver_user_id, driver_name, truck_number, depot_id, depot_name,
             dispatcher_group_name, request_type_id, request_type_label,
             supply_number, note, details_json, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
        """,
        (
            current_user()["id"],
            profile["driver_name"],
            profile["truck_number"],
            profile["depot_id"],
            profile["depot_name"],
            profile.get("dispatcher_group_name"),
            request_type["id"],
            request_type["label"],
            supply_number,
            note,
            json.dumps(details),
            now_iso(),
        ),
    )
    flash("Request sent to Dispatch.")
    return redirect(url_for("driver_home"))


def driver_request_rows(user_id):
    rows = query_all(
        """
        SELECT *
        FROM driver_requests
        WHERE driver_user_id = ?
        ORDER BY
          CASE status WHEN 'new' THEN 1 WHEN 'acknowledged' THEN 2 ELSE 3 END,
          created_at DESC
        LIMIT 25
        """,
        (user_id,),
    )
    requests = []
    for row in rows:
        item = enrich_request_item(row)
        item["comments"] = rows_to_dicts(
            query_all(
                """
                SELECT author_name, body, created_at
                FROM request_comments
                WHERE request_id = ? AND visible_to_driver = 1
                ORDER BY created_at
                """,
                (row["id"],),
            )
        ) if item["receipt_mode"] != "none" else []
        requests.append(item)
    return requests


@app.route("/api/driver/requests")
@roles_required("driver")
def driver_requests_api():
    return jsonify({"requests": driver_request_rows(current_user()["id"])})


def dispatch_request_rows(filters):
    params = []
    clauses = []
    if filters.get("status"):
        clauses.append("r.status = ?")
        params.append(filters["status"])
    if filters.get("depot_id"):
        clauses.append("r.depot_id = ?")
        params.append(filters["depot_id"])
    if filters.get("group_names"):
        placeholders = ",".join("?" for _ in filters["group_names"])
        clauses.append(f"r.dispatcher_group_name IN ({placeholders})")
        params.extend(filters["group_names"])
    if filters.get("search"):
        clauses.append("(r.driver_name LIKE ? OR r.truck_number LIKE ? OR r.supply_number LIKE ? OR r.note LIKE ?)")
        term = f"%{filters['search']}%"
        params.extend([term, term, term, term])

    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = query_all(
        f"""
        SELECT r.*
        FROM driver_requests r
        {where}
        ORDER BY
          CASE r.status WHEN 'new' THEN 1 WHEN 'acknowledged' THEN 2 ELSE 3 END,
          r.created_at DESC
        LIMIT 200
        """,
        params,
    )
    return rows


def saved_dispatch_group_names(user_id):
    rows = query_all(
        """
        SELECT g.name
        FROM dispatch_preferences p
        JOIN dispatcher_groups g ON g.id = p.dispatcher_group_id
        WHERE p.user_id = ? AND g.active = 1
        ORDER BY g.sort_order, g.name
        """,
        (user_id,),
    )
    return [row["name"] for row in rows]


@app.route("/dispatch")
@roles_required("dispatch", "admin")
def dispatch_dashboard():
    user = current_user()
    profile_id = request.args.get("profile_id", "")
    if not profile_id and user["role"] == "dispatch" and user["default_desk_profile_id"]:
        profile_id = str(user["default_desk_profile_id"]).strip()
    selected_group_ids = request.args.getlist("group_id")
    selected_group_names = []
    if selected_group_ids:
        placeholders = ",".join("?" for _ in selected_group_ids)
        selected_group_names = [
            row["name"]
            for row in query_all(
                f"SELECT name FROM dispatcher_groups WHERE id IN ({placeholders})",
                selected_group_ids,
            )
        ]
    elif profile_id:
        selected_group_names = desk_profile_group_names(profile_id)
    elif user["role"] == "dispatch":
        selected_group_names = saved_dispatch_group_names(user["id"])

    filters = {
        "status": request.args.get("status", ""),
        "depot_id": request.args.get("depot_id", ""),
        "search": request.args.get("search", "").strip(),
        "group_names": selected_group_names,
    }
    view_mode = request.args.get("view", "active")
    if view_mode in {"combined", "separate"}:
        view_mode = "full" if view_mode == "combined" else "active"
    if view_mode not in {"active", "full"}:
        view_mode = "active"
    requests = []
    for row in dispatch_request_rows(filters):
        requests.append(enrich_request_item(row))
    comments_by_request = {}
    if requests:
        ids = [str(row["id"]) for row in requests]
        placeholders = ",".join("?" for _ in ids)
        for comment in query_all(
            f"""
            SELECT *
            FROM request_comments
            WHERE request_id IN ({placeholders})
            ORDER BY created_at
            """,
            ids,
        ):
            comments_by_request.setdefault(comment["request_id"], []).append(comment)

    selected_group_set = set(selected_group_ids)
    if not selected_group_set and profile_id:
        selected_group_set = {
            str(group["id"])
            for profile in desk_profile_options()
            if str(profile["id"]) == str(profile_id)
            for group in profile["groups"]
        }
    saved_names = set(saved_dispatch_group_names(user["id"]))
    if not selected_group_set and not profile_id and saved_names:
        selected_group_set = {
            str(group["id"])
            for group in group_options()
            if group["name"] in saved_names
        }
    active_requests = [item for item in requests if item["status"] != "done"]
    done_requests = [item for item in requests if item["status"] == "done"]
    visible_requests = requests if view_mode == "full" else active_requests
    dense_body = """
    {% macro request_table(rows, title='') %}
      {% if title %}<h2 style="margin: 16px 0 8px;">{{ title }}</h2>{% endif %}
      <div class="table-wrap">
        <table class="dispatch-table">
          <thead>
            <tr>
              <th>Age</th>
              <th class="select-col">Select</th>
              <th>Time</th>
              <th>Queue</th>
              <th>Depot</th>
              <th>Truck</th>
              <th>Driver</th>
              <th>Supply No</th>
              <th>Request</th>
              <th>Details</th>
              <th>Notes</th>
              <th>Status</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {% for item in rows %}
              <tr class="dispatch-row {{ item.row_class }}" data-request-id="{{ item.id }}" data-request-status="{{ item.status }}" data-request-title="#{{ item.id }} - {{ item.request_type_label }}">
                <td><strong>{{ item.age }}</strong></td>
                <td class="select-cell">
                  {% if item.status == 'done' %}
                    <input form="bulk-delete-done-form" class="done-select" type="checkbox" name="request_id" value="{{ item.id }}" aria-label="Select done request {{ item.id }}">
                  {% endif %}
                </td>
                <td>{{ item.created_at.replace('T', ' ').replace('Z', '') }}</td>
                <td><span class="queue-pill">{{ item.dispatcher_group_name or 'Unassigned' }}</span></td>
                <td>{{ item.depot_name }}</td>
                <td>{{ item.truck_number }}</td>
                <td>{{ item.driver_name }}</td>
                <td><strong>{{ item.supply_number or '-' }}</strong></td>
                <td><strong>{{ item.request_type_label }}</strong></td>
                <td>{{ item.details_summary or '-' }}</td>
                <td>
                  {% set comments = comments_by_request.get(item.id, []) %}
                  {% if item.note %}
                    <div><strong>Driver:</strong> {{ item.note }}</div>
                  {% endif %}
                  {% if comments %}
                    <div><strong>Dispatch:</strong> {{ comments[-1].body }}</div>
                  {% endif %}
                  {% if not item.note and not comments %}
                    <span class="small">No notes</span>
                  {% endif %}
                </td>
                <td><strong>{{ item.status|title }}</strong></td>
                <td>
                  <div class="action-pills">
                    <form method="post" action="{{ url_for('reset_request', request_id=item.id) }}">
                      <button class="action-pill n" title="Restore to new" {% if item.status == 'new' %}disabled{% endif %}>N</button>
                    </form>
                    <form method="post" action="{{ url_for('acknowledge_request', request_id=item.id) }}">
                      <button class="action-pill a" title="Acknowledge driver" {% if not item.can_acknowledge %}disabled{% endif %}>A</button>
                    </form>
                    <form method="post" action="{{ url_for('complete_request', request_id=item.id) }}">
                      <button class="action-pill d" title="Complete or clear" {% if not item.can_complete %}disabled{% endif %}>D</button>
                    </form>
                  </div>
                </td>
              </tr>
            {% else %}
              <tr><td colspan="13">No requests match the current filters.</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    {% endmacro %}
    <section class="dispatch-tools">
      <div class="inline" style="justify-content: space-between; align-items: flex-start;">
        <div>
          <div class="segmented">
            <a class="{% if view_mode == 'active' %}active{% endif %}" href="{{ url_for('dispatch_dashboard', view='active', profile_id=profile_id, status=filters.status, depot_id=filters.depot_id, search=filters.search, group_id=selected_group_set|list) }}">Active only</a>
            <a class="{% if view_mode == 'full' %}active{% endif %}" href="{{ url_for('dispatch_dashboard', view='full', profile_id=profile_id, status=filters.status, depot_id=filters.depot_id, search=filters.search, group_id=selected_group_set|list) }}">Full list</a>
          </div>
          <p class="small" style="margin-top: 8px;">Active only hides Done items. Full list shows New, Acknowledged, and Done for the selected desk/queues.</p>
          <span class="live-indicator" id="dispatch-live-status">Live refresh on</span>
        </div>
        <form class="inline" method="get">
          <input type="hidden" name="view" value="{{ view_mode }}">
          <select id="desk-profile-select" name="profile_id" style="width: 220px;">
            <option value="">Custom view</option>
            {% for profile in desk_profiles %}
              <option value="{{ profile.id }}" {% if profile_id|string == profile.id|string %}selected{% endif %}>{{ profile.name }}</option>
            {% endfor %}
          </select>
          <select name="status" style="width: 150px;">
            <option value="">All statuses</option>
            {% for status in ['new', 'acknowledged', 'done'] %}
              <option value="{{ status }}" {% if filters.status == status %}selected{% endif %}>{{ status|title }}</option>
            {% endfor %}
          </select>
          <select name="depot_id" style="width: 180px;">
            <option value="">All depots</option>
            {% for depot in depots %}
              <option value="{{ depot.id }}" {% if filters.depot_id|string == depot.id|string %}selected{% endif %}>{{ depot.name }}</option>
            {% endfor %}
          </select>
          <input name="search" value="{{ filters.search }}" placeholder="Driver, truck, note" style="width: 220px;">
          <span class="inline" style="max-width: 560px;">
            {% for group in groups %}
              <label class="chip">
                <input type="checkbox" name="group_id" value="{{ group.id }}" style="width: auto; min-height: 0;" {% if group.id|string in selected_group_set %}checked{% endif %}>
                {{ group.name }}
              </label>
            {% endfor %}
          </span>
          <button class="btn" type="submit">Filter</button>
          <a class="btn ghost" href="{{ url_for('dispatch_dashboard', view=view_mode) }}">Clear</a>
        </form>
      </div>
      <div class="inline" style="margin-top: 10px;">
        <button id="multi-select-toggle" class="btn ghost" type="button">Select completed requests</button>
        <span class="small">Select Done items only when cleanup is needed.</span>
      </div>
      <form id="bulk-delete-done-form" class="bulk-delete-tools" method="post" action="{{ url_for('bulk_delete_done_requests') }}" onsubmit="return confirm('Delete selected Done requests? This will remove their comments too.');">
        <button id="bulk-delete-done-button" class="btn red" type="submit" disabled>Delete selected Done</button>
        <button id="multi-select-cancel" class="btn ghost" type="button">Cancel</button>
      </form>
      {% if user.role == 'dispatch' %}
        <form method="post" action="{{ url_for('save_dispatch_preferences') }}" style="margin-top: 10px;">
          <div class="inline">
            {% for group in groups %}
              <label class="chip">
                <input type="checkbox" name="group_id" value="{{ group.id }}" style="width: auto; min-height: 0;" {% if group.id|string in selected_group_set or (not selected_group_set and group.name in saved_names) %}checked{% endif %}>
                {{ group.name }}
              </label>
            {% endfor %}
            <button class="btn secondary" type="submit">Save default queues</button>
          </div>
        </form>
      {% endif %}
    </section>
    <section id="dispatch-board">
      {{ request_table(visible_requests) }}
    </section>
    <div class="context-menu" id="queue-menu">
      <form id="queue-menu-form" method="post">
        <h3 style="margin-bottom: 10px;">Reassign queue</h3>
        <div class="field">
          <label for="queue-menu-group">Queue</label>
          <select id="queue-menu-group" name="dispatcher_group_id" required>
            {% for group in groups %}
              <option value="{{ group.id }}">{{ group.name }}</option>
            {% endfor %}
          </select>
        </div>
        <button class="btn full" type="submit">Move request</button>
      </form>
      <form id="queue-menu-delete-form" method="post" style="margin-top: 8px;" onsubmit="return confirm('Delete this Done request? This will remove its comments too.');">
        <button id="queue-menu-delete-button" class="btn red full" type="submit">Delete Done request</button>
      </form>
    </div>
    <div class="modal-backdrop" id="note-modal">
      <div class="modal">
        <form id="note-modal-form" method="post">
          <div class="item-head">
            <h2 id="note-modal-title">Add dispatcher note</h2>
            <button class="btn ghost" id="note-modal-close" type="button">Close</button>
          </div>
          <div class="field">
            <label for="dispatcher-note">Dispatcher note</label>
            <textarea id="dispatcher-note" name="note" required placeholder="Internal note for Dispatch"></textarea>
          </div>
          <button class="btn full" type="submit">Save note</button>
        </form>
      </div>
    </div>
    <script>
      let dispatchRefreshBusy = false;
      const queueMenu = document.getElementById("queue-menu");
      const queueMenuForm = document.getElementById("queue-menu-form");
      const queueMenuDeleteForm = document.getElementById("queue-menu-delete-form");
      const queueMenuDeleteButton = document.getElementById("queue-menu-delete-button");
      const bulkDeleteDoneButton = document.getElementById("bulk-delete-done-button");
      const multiSelectToggle = document.getElementById("multi-select-toggle");
      const multiSelectCancel = document.getElementById("multi-select-cancel");
      const noteModal = document.getElementById("note-modal");
      const noteModalForm = document.getElementById("note-modal-form");
      const noteModalTitle = document.getElementById("note-modal-title");
      const noteModalClose = document.getElementById("note-modal-close");
      const dispatcherNote = document.getElementById("dispatcher-note");
      function dispatchOverlayOpen() {
        return queueMenu.style.display === "block" || noteModal.style.display === "flex" || document.body.classList.contains("multi-select-active");
      }
      function selectedDoneCount() {
        return document.querySelectorAll(".done-select:checked").length;
      }
      function updateBulkDeleteButton() {
        if (!bulkDeleteDoneButton) return;
        const count = selectedDoneCount();
        bulkDeleteDoneButton.disabled = count === 0;
        bulkDeleteDoneButton.textContent = count ? `Delete selected Done (${count})` : "Delete selected Done";
      }
      function setMultiSelectMode(active) {
        document.body.classList.toggle("multi-select-active", active);
        if (multiSelectToggle) {
          multiSelectToggle.textContent = active ? "Multi select on" : "Multi select";
          multiSelectToggle.classList.toggle("secondary", active);
        }
        if (!active) {
          document.querySelectorAll(".done-select").forEach((input) => {
            input.checked = false;
          });
        }
        updateBulkDeleteButton();
      }
      function hideQueueMenu() {
        queueMenu.style.display = "none";
      }
      function openNoteModal(row) {
        hideQueueMenu();
        noteModalForm.action = `/dispatch/request/${row.dataset.requestId}/note`;
        noteModalTitle.textContent = `Add dispatcher note - ${row.dataset.requestTitle}`;
        dispatcherNote.value = "";
        noteModal.style.display = "flex";
        dispatcherNote.focus();
      }
      function closeNoteModal() {
        noteModal.style.display = "none";
      }
      document.addEventListener("contextmenu", (event) => {
        const row = event.target.closest(".dispatch-row");
        if (!row) return;
        event.preventDefault();
        queueMenuForm.action = `/dispatch/request/${row.dataset.requestId}/reassign`;
        queueMenuDeleteForm.action = `/dispatch/request/${row.dataset.requestId}/delete`;
        const canDelete = row.dataset.requestStatus === "done";
        queueMenuDeleteButton.disabled = !canDelete;
        queueMenuDeleteButton.textContent = canDelete ? "Delete Done request" : "Delete only available for Done";
        queueMenu.style.display = "block";
        const maxLeft = window.innerWidth - queueMenu.offsetWidth - 12;
        const maxTop = window.innerHeight - queueMenu.offsetHeight - 12;
        queueMenu.style.left = Math.max(8, Math.min(event.clientX, maxLeft)) + "px";
        queueMenu.style.top = Math.max(8, Math.min(event.clientY, maxTop)) + "px";
      });
      document.addEventListener("dblclick", (event) => {
        if (event.target.closest("button, a, input, select, textarea, form")) return;
        const row = event.target.closest(".dispatch-row");
        if (row) openNoteModal(row);
      });
      document.addEventListener("click", (event) => {
        if (!event.target.closest("#queue-menu")) hideQueueMenu();
      });
      document.addEventListener("change", (event) => {
        if (event.target.classList && event.target.classList.contains("done-select")) {
          updateBulkDeleteButton();
        }
      });
      if (multiSelectToggle) {
        multiSelectToggle.addEventListener("click", () => {
          setMultiSelectMode(!document.body.classList.contains("multi-select-active"));
        });
      }
      if (multiSelectCancel) {
        multiSelectCancel.addEventListener("click", () => setMultiSelectMode(false));
      }
      noteModalClose.addEventListener("click", closeNoteModal);
      noteModal.addEventListener("click", (event) => {
        if (event.target === noteModal) closeNoteModal();
      });
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          hideQueueMenu();
          closeNoteModal();
        }
      });
      const deskProfileSelect = document.getElementById("desk-profile-select");
      if (deskProfileSelect) {
        deskProfileSelect.addEventListener("change", () => {
          const form = deskProfileSelect.form;
          form.querySelectorAll('input[name="group_id"]').forEach((input) => {
            input.checked = false;
          });
          form.requestSubmit();
        });
      }
      async function refreshDispatchBoard() {
        if (dispatchRefreshBusy || document.hidden || dispatchOverlayOpen()) return;
        dispatchRefreshBusy = true;
        try {
          const response = await fetch(window.location.href, {
            cache: "no-store",
            headers: { "X-Requested-With": "dispatch-refresh" }
          });
          if (!response.ok) return;
          const html = await response.text();
          const doc = new DOMParser().parseFromString(html, "text/html");
          const nextBoard = doc.getElementById("dispatch-board");
          const board = document.getElementById("dispatch-board");
          if (nextBoard && board) {
            board.innerHTML = nextBoard.innerHTML;
            updateBulkDeleteButton();
            const status = document.getElementById("dispatch-live-status");
            if (status) status.textContent = "Updated " + new Date().toLocaleTimeString();
          }
        } catch (error) {
          const status = document.getElementById("dispatch-live-status");
          if (status) status.textContent = "Refresh paused";
        } finally {
          dispatchRefreshBusy = false;
        }
      }
      updateBulkDeleteButton();
      setInterval(refreshDispatchBoard, 1000);
    </script>
    """
    return render_page(
        "Dispatch",
        dense_body,
        requests=requests,
        comments_by_request=comments_by_request,
        depots=depot_options(),
        groups=group_options(),
        filters=filters,
        selected_group_set=selected_group_set,
        saved_names=saved_names,
        profile_id=profile_id,
        desk_profiles=desk_profile_options(),
        view_mode=view_mode,
        active_requests=active_requests,
        done_requests=done_requests,
        visible_requests=visible_requests,
        user=user,
    )
    body = """
    <section class="hero">
      <div>
        <h1>Dispatch dashboard</h1>
        <p>Review incoming driver requests, add notes, acknowledge, and complete.</p>
      </div>
    </section>
    <section class="panel" style="margin-bottom: 16px;">
      <form class="grid three" method="get">
        <div class="field">
          <label for="status">Status</label>
          <select id="status" name="status">
            <option value="">All statuses</option>
            {% for status in ['new', 'acknowledged', 'done'] %}
              <option value="{{ status }}" {% if filters.status == status %}selected{% endif %}>{{ status|title }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="field">
          <label for="depot_id">Depot</label>
          <select id="depot_id" name="depot_id">
            <option value="">All depots</option>
            {% for depot in depots %}
              <option value="{{ depot.id }}" {% if filters.depot_id|string == depot.id|string %}selected{% endif %}>{{ depot.name }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="field">
          <label for="search">Search</label>
          <input id="search" name="search" value="{{ filters.search }}" placeholder="Driver, truck, note">
        </div>
        <div class="field" style="grid-column: 1 / -1;">
          <label>Dispatcher groups</label>
          <div class="inline">
            {% for group in groups %}
              <label class="chip" style="font-weight: 700;">
                <input type="checkbox" name="group_id" value="{{ group.id }}" style="width: auto; min-height: 0;"
                  {% if group.id|string in selected_group_set or (not selected_group_set and group.name in saved_names) %}checked{% endif %}>
                {{ group.name }}
              </label>
            {% endfor %}
          </div>
        </div>
        <div class="inline">
          <button class="btn" type="submit">Apply filters</button>
          <a class="btn ghost" href="{{ url_for('dispatch_dashboard') }}">Clear</a>
        </div>
      </form>
      {% if user.role == 'dispatch' %}
        <form method="post" action="{{ url_for('save_dispatch_preferences') }}" style="margin-top: 12px;">
          <div class="inline">
            {% for group in groups %}
              <label class="chip">
                <input type="checkbox" name="group_id" value="{{ group.id }}" style="width: auto; min-height: 0;" {% if group.name in saved_names %}checked{% endif %}>
                {{ group.name }}
              </label>
            {% endfor %}
            <button class="btn secondary" type="submit">Save my default regions</button>
          </div>
        </form>
      {% endif %}
    </section>
    <section class="list">
      {% for item in requests %}
        <article class="item">
          <div class="item-head">
            <div>
              <strong>#{{ item.id }} · {{ item.request_type_label }}</strong>
              <div class="meta">{{ item.driver_name }} · Truck {{ item.truck_number }} · {{ item.depot_name }} · {{ item.dispatcher_group_name or 'Unassigned' }}</div>
              <div class="meta">Created {{ item.created_at }}</div>
            </div>
            <span class="status {{ item.status }}">{{ item.status|title }}</span>
          </div>
          {% if item.details %}
            <div class="meta" style="margin-top: 8px;">
              {% for detail in item.details %}
                <strong>{{ detail.label }}:</strong> {{ detail.value }}{% if not loop.last %}<br>{% endif %}
              {% endfor %}
            </div>
          {% endif %}
          {% if item.note %}<p>{{ item.note }}</p>{% endif %}
          {% for comment in comments_by_request.get(item.id, []) %}
            <div class="comment">
              <strong>{{ comment.author_name }}</strong>
              <div>{{ comment.body }}</div>
              <div class="meta">{{ comment.created_at }}</div>
            </div>
          {% endfor %}
          <div class="split-actions" style="margin-top: 12px;">
            {% if item.status == 'new' %}
              <form method="post" action="{{ url_for('acknowledge_request', request_id=item.id) }}" class="inline">
                <input name="comment" placeholder="Comment to driver" style="min-width: min(340px, 100%);">
                <button class="btn amber" type="submit">Acknowledge</button>
              </form>
            {% endif %}
            {% if item.status in ['new', 'acknowledged'] %}
              <form method="post" action="{{ url_for('complete_request', request_id=item.id) }}" class="inline">
                <input name="comment" placeholder="Completion comment" style="min-width: min(340px, 100%);">
                <button class="btn green" type="submit">Mark done</button>
              </form>
            {% endif %}
          </div>
        </article>
      {% else %}
        <article class="item"><p>No requests match the current filters.</p></article>
      {% endfor %}
    </section>
    """
    return render_page(
        "Dispatch",
        body,
        requests=requests,
        comments_by_request=comments_by_request,
        depots=depot_options(),
        groups=group_options(),
        filters=filters,
        selected_group_set=selected_group_set,
        saved_names=saved_names,
        user=user,
    )


@app.route("/dispatch/preferences", methods=["POST"])
@roles_required("dispatch")
def save_dispatch_preferences():
    user = current_user()
    selected = request.form.getlist("group_id")
    with closing(get_db()) as db:
        db.execute("DELETE FROM dispatch_preferences WHERE user_id = ?", (user["id"],))
        for group_id in selected:
            db.execute(
                """
                INSERT OR IGNORE INTO dispatch_preferences (user_id, dispatcher_group_id)
                VALUES (?, ?)
                """,
                (user["id"], group_id),
            )
        db.commit()
    flash("Default dispatch regions saved.")
    return redirect(url_for("dispatch_dashboard"))


@app.route("/dispatch/request/<int:request_id>/reassign", methods=["POST"])
@roles_required("dispatch", "admin")
def reassign_request(request_id):
    item = query_one(
        "SELECT id, dispatcher_group_name FROM driver_requests WHERE id = ?",
        (request_id,),
    )
    if not item:
        abort(404)
    group = query_one(
        "SELECT id, name FROM dispatcher_groups WHERE id = ? AND active = 1",
        (request.form.get("dispatcher_group_id"),),
    )
    if not group:
        flash("Choose a valid queue.")
        return redirect(request.referrer or url_for("dispatch_dashboard"))
    execute(
        "UPDATE driver_requests SET dispatcher_group_name = ? WHERE id = ?",
        (group["name"], request_id),
    )
    if item["dispatcher_group_name"] != group["name"]:
        add_request_comment(
            request_id,
            f"Queue changed from {item['dispatcher_group_name'] or 'Unassigned'} to {group['name']}.",
            visible_to_driver=False,
        )
    flash(f"Request #{request_id} moved to {group['name']}.")
    return redirect(request.referrer or url_for("dispatch_dashboard"))


@app.route("/dispatch/request/<int:request_id>/note", methods=["POST"])
@roles_required("dispatch", "admin")
def add_dispatch_note(request_id):
    item = query_one("SELECT id FROM driver_requests WHERE id = ?", (request_id,))
    if not item:
        abort(404)
    note = request.form.get("note", "").strip()
    if not note:
        flash("Enter a note before saving.")
        return redirect(request.referrer or url_for("dispatch_dashboard"))
    add_request_comment(request_id, note, visible_to_driver=False)
    flash(f"Note added to request #{request_id}.")
    return redirect(request.referrer or url_for("dispatch_dashboard"))


@app.route("/dispatch/request/<int:request_id>/delete", methods=["POST"])
@roles_required("dispatch", "admin")
def delete_done_request(request_id):
    item = query_one("SELECT id, status FROM driver_requests WHERE id = ?", (request_id,))
    if not item:
        abort(404)
    if item["status"] != "done":
        flash("Only Done requests can be deleted.")
        return redirect(request.referrer or url_for("dispatch_dashboard"))
    execute("DELETE FROM driver_requests WHERE id = ?", (request_id,))
    flash(f"Done request #{request_id} deleted.")
    return redirect(request.referrer or url_for("dispatch_dashboard"))


@app.route("/dispatch/requests/delete-done", methods=["POST"])
@roles_required("dispatch", "admin")
def bulk_delete_done_requests():
    request_ids = [
        int(value)
        for value in request.form.getlist("request_id")
        if value.isdigit()
    ]
    if not request_ids:
        flash("Select one or more Done requests to delete.")
        return redirect(request.referrer or url_for("dispatch_dashboard"))
    placeholders = ",".join("?" for _ in request_ids)
    with closing(get_db()) as db:
        rows = db.execute(
            f"""
            SELECT id
            FROM driver_requests
            WHERE status = 'done' AND id IN ({placeholders})
            """,
            request_ids,
        ).fetchall()
        done_ids = [row["id"] for row in rows]
        if done_ids:
            delete_placeholders = ",".join("?" for _ in done_ids)
            db.execute(
                f"DELETE FROM driver_requests WHERE id IN ({delete_placeholders})",
                done_ids,
            )
        db.commit()
    flash(f"Deleted {len(done_ids)} Done request{'s' if len(done_ids) != 1 else ''}.")
    return redirect(request.referrer or url_for("dispatch_dashboard"))


def add_request_comment(request_id, body, visible_to_driver=True):
    comment = body.strip()
    if not comment:
        return
    user = current_user()
    execute(
        """
        INSERT INTO request_comments
            (request_id, user_id, author_name, body, visible_to_driver, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            request_id,
            user["id"],
            user["display_name"],
            comment,
            1 if visible_to_driver else 0,
            now_iso(),
        ),
    )


def request_receipt_mode(request_id):
    row = query_one(
        """
        SELECT rt.form_schema
        FROM driver_requests r
        LEFT JOIN request_types rt ON rt.id = r.request_type_id
        WHERE r.id = ?
        """,
        (request_id,),
    )
    if not row:
        return None
    return receipt_mode_for_schema(parse_form_schema(row["form_schema"]))


@app.route("/dispatch/request/<int:request_id>/reset", methods=["POST"])
@roles_required("dispatch", "admin")
def reset_request(request_id):
    item = query_one("SELECT status FROM driver_requests WHERE id = ?", (request_id,))
    if not item:
        abort(404)
    execute(
        """
        UPDATE driver_requests
        SET status = 'new', acknowledged_at = NULL, completed_at = NULL
        WHERE id = ?
        """,
        (request_id,),
    )
    flash(f"Request #{request_id} restored to new.")
    return redirect(request.referrer or url_for("dispatch_dashboard"))


@app.route("/dispatch/request/<int:request_id>/acknowledge", methods=["POST"])
@roles_required("dispatch", "admin")
def acknowledge_request(request_id):
    item = query_one("SELECT status FROM driver_requests WHERE id = ?", (request_id,))
    if not item:
        abort(404)
    receipt_mode = request_receipt_mode(request_id)
    if receipt_mode in {None, "none"}:
        flash("This request type does not send a driver acknowledgement.")
        return redirect(request.referrer or url_for("dispatch_dashboard"))
    if item["status"] == "new":
        execute(
            """
            UPDATE driver_requests
            SET status = 'acknowledged', acknowledged_at = ?
            WHERE id = ?
            """,
            (now_iso(), request_id),
        )
    add_request_comment(
        request_id,
        request.form.get("comment", "Dispatch has acknowledged this request."),
    )
    flash(f"Request #{request_id} acknowledged.")
    return redirect(request.referrer or url_for("dispatch_dashboard"))


@app.route("/dispatch/request/<int:request_id>/complete", methods=["POST"])
@roles_required("dispatch", "admin")
def complete_request(request_id):
    item = query_one("SELECT status FROM driver_requests WHERE id = ?", (request_id,))
    if not item:
        abort(404)
    receipt_mode = request_receipt_mode(request_id)
    execute(
        """
        UPDATE driver_requests
        SET status = 'done',
            acknowledged_at = COALESCE(acknowledged_at, ?),
            completed_at = ?
        WHERE id = ?
        """,
        (now_iso(), now_iso(), request_id),
    )
    if receipt_mode == "ack_done":
        add_request_comment(
            request_id,
            request.form.get("comment", "Dispatch has completed this request."),
        )
    flash(f"Request #{request_id} completed.")
    return redirect(request.referrer or url_for("dispatch_dashboard"))


@app.route("/admin")
@roles_required("admin")
def admin_dashboard():
    users = [dict(row) for row in query_all("""
        SELECT u.*, COALESCE(u.access_role, u.role) AS effective_role,
               d.name AS home_depot_name
        FROM users u
        LEFT JOIN depots d ON d.id = u.home_depot_id
        ORDER BY u.active DESC, effective_role, COALESCE(d.name, ''), u.display_name
    """)]
    for item in users:
        item["depot_ids"] = [row["depot_id"] for row in query_all("SELECT depot_id FROM user_depots WHERE user_id = ?", (item["id"],))]
    pending_rows = query_all("""
        SELECT home_depot_id, COUNT(*) AS pending_count
        FROM users
        WHERE COALESCE(access_role, role) = 'driver' AND access_status = 'pending' AND active = 0
        GROUP BY home_depot_id
    """)
    pending_by_depot = {row["home_depot_id"]: row["pending_count"] for row in pending_rows}
    total_pending = sum(pending_by_depot.values())
    categorized_body = """
    <section class="hero">
      <div>
        <h1>Admin</h1>
        <p>Manage people, driver flows, dispatcher desks, queues and depot routing.</p>
      </div>
    </section>

    {% if total_pending %}
      <section class="panel" style="border-color:#fed7aa;background:#fffaf5;">
        <h2 style="margin-bottom:6px;">Pending driver approvals <span class="status-badge pending">{{ total_pending }}</span></h2>
        <div class="pending-overview">
          {% for depot in depots_all if pending_by_depot.get(depot.id, 0) %}
            <a class="pending-link" href="#admin-driver-depot-{{ depot.id }}"><span>{{ depot.name }}</span><span class="pending-count">{{ pending_by_depot.get(depot.id, 0) }}</span></a>
          {% endfor %}
          {% if pending_by_depot.get(None, 0) %}<span class="pending-link">Unassigned depot <span class="pending-count">{{ pending_by_depot.get(None, 0) }}</span></span>{% endif %}
        </div>
      </section>
    {% endif %}

    <details id="users" class="panel admin-section" open>
      <summary>Users <span class="small">{{ users|length }}</span></summary><div class="field" style="margin-top:12px;"><label for="admin-driver-search">Search driver number or name</label><input id="admin-driver-search" type="search" placeholder="Start typing..."></div>
      <details class="admin-create">
        <summary class="btn secondary compact" style="width:max-content;">Add user</summary>
        <form method="post" action="{{ url_for('admin_create_user') }}" class="grid three" style="margin-top:12px;">
          <div class="field"><label>Username</label><input name="username" required></div>
          <div class="field"><label>Display name</label><input name="display_name" required></div>
          <div class="field"><label>Role</label><select name="role"><option value="driver">Driver</option><option value="dispatch">Dispatch</option><option value="depot">Depot</option><option value="admin">Admin</option></select></div>
          <div class="field"><label>Temporary password</label><input name="password" required></div>
          <div class="field" style="align-self:end;"><button class="btn" type="submit">Create user</button></div>
        </form>
      </details>
      {% macro user_record(item) %}
        <details class="compact-record" data-driver-search="{{ (item.display_name ~ ' ' ~ item.username ~ ' ' ~ (item.driver_number or ''))|lower }}">
          <summary>
            <span class="record-title"><strong>{{ item.display_name }}</strong><span>{{ item.username }}</span></span>
            <span>{{ item.home_depot_name or item.effective_role|title }}</span>
            <span class="status-badge {{ 'active' if item.active else ('pending' if item.access_status == 'pending' else 'inactive') }}">{{ 'Active' if item.active else ('Pending' if item.access_status == 'pending' else 'Inactive') }}</span>
          </summary>
          <div class="record-body">
            <form id="user-save-{{ item.id }}" method="post" action="{{ url_for('admin_update_user', user_id=item.id) }}" class="grid three">
              <div class="field"><label>Username / driver number</label><input name="username" value="{{ item.username }}" required></div>
              <div class="field"><label>Display name</label><input name="display_name" value="{{ item.display_name }}" required></div>
              <div class="field"><label>Role</label><select name="role">{% for role in ['driver','dispatch','depot','admin'] %}<option value="{{ role }}" {% if item.effective_role == role %}selected{% endif %}>{{ role|title }}</option>{% endfor %}</select></div><div class="field"><label>Home depot (drivers)</label><select name="home_depot_id"><option value="">Unassigned</option>{% for depot in depots_all %}<option value="{{ depot.id }}" {% if item.home_depot_id == depot.id %}selected{% endif %}>{{ depot.name }}</option>{% endfor %}</select></div><div class="field"><label>Default desk profile (dispatch)</label><select name="default_desk_profile_id"><option value="">No default profile</option>{% for profile in desk_profiles %}<option value="{{ profile.id }}" {% if item.default_desk_profile_id == profile.id %}selected{% endif %}>{{ profile.name }}</option>{% endfor %}</select></div>
            </form>
            {% if item.effective_role == 'depot' %}
              <details style="margin-top:12px;">
                <summary class="btn ghost compact" style="width:max-content;">Assigned depots ({{ item.depot_ids|length }})</summary>
                <form method="post" action="{{ url_for('admin_assign_user_depots', user_id=item.id) }}" style="margin-top:10px;">
                  <div class="choice-tiles">{% for depot in depots_all %}<label class="chip"><input type="checkbox" name="depot_id" value="{{ depot.id }}" style="width:auto;min-height:0;" {% if depot.id in item.depot_ids %}checked{% endif %}> {{ depot.name }}</label>{% endfor %}</div>
                  <button class="btn secondary compact" type="submit" style="margin-top:10px;">Save depot access</button>
                </form>
              </details>
            {% endif %}
            <div class="record-actions">
              <button class="btn secondary" form="user-save-{{ item.id }}" type="submit">Save details</button>
              <details><summary class="btn amber" style="list-style:none;">Reset password</summary><form method="post" action="{{ url_for('admin_reset_password', user_id=item.id) }}" class="inline" style="margin-top:8px;"><input name="password" placeholder="New password" required style="width:180px;"><button class="btn amber" type="submit">Apply</button></form></details>
              <form method="post" action="{{ url_for('admin_toggle_user', user_id=item.id) }}"><button class="btn ghost" type="submit">{{ 'Make inactive' if item.active else 'Make active' }}</button></form>
              <form method="post" action="{{ url_for('admin_delete_user', user_id=item.id) }}" onsubmit="return confirm('Delete this user? Existing request history will remain.');"><button class="btn red" type="submit">Delete</button></form>
            </div>
          </div>
        </details>
      {% endmacro %}

      {% for role_key, role_label in [('admin','Admins'), ('dispatch','Dispatch'), ('depot','Depot users')] %}
        {% set role_users = users|selectattr('effective_role', 'equalto', role_key)|list %}
        <details class="user-role-group" {% if role_key == 'admin' %}open{% endif %}>
          <summary>{{ role_label }} <span class="small">{{ role_users|length }}</span></summary>
          {% if role_users %}{% for item in role_users %}{{ user_record(item) }}{% endfor %}{% else %}<p class="small empty-group">No {{ role_label|lower }}.</p>{% endif %}
        </details>
      {% endfor %}

      {% set drivers = users|selectattr('effective_role', 'equalto', 'driver')|list %}
      <details class="user-role-group" open>
        <summary>Drivers <span class="small">{{ drivers|length }}</span></summary>
        {% for depot in depots_all %}
          {% set depot_drivers = drivers|selectattr('home_depot_id', 'equalto', depot.id)|list %}
          {% if depot_drivers %}
            <details id="admin-driver-depot-{{ depot.id }}" class="driver-depot-group" {% if pending_by_depot.get(depot.id, 0) %}open{% endif %}>
              <summary><span>{{ depot.name }}</span><span class="small">{{ depot_drivers|length }}</span>{% if pending_by_depot.get(depot.id, 0) %}<span class="status-badge pending">{{ pending_by_depot.get(depot.id, 0) }} pending</span>{% endif %}</summary>
              {% for item in depot_drivers %}{{ user_record(item) }}{% endfor %}
            </details>
          {% endif %}
        {% endfor %}
        {% set unassigned_drivers = drivers|selectattr('home_depot_id', 'none')|list %}
        {% if unassigned_drivers %}
          <details class="driver-depot-group">
            <summary>Unassigned depot <span class="small">{{ unassigned_drivers|length }}</span></summary>
            {% for item in unassigned_drivers %}{{ user_record(item) }}{% endfor %}
          </details>
        {% endif %}
        {% if not drivers %}<p class="small empty-group">No drivers.</p>{% endif %}
      </details>
    </details>

    <details id="driver-flows" class="panel admin-section" open>
      <summary>Driver flows <span class="small">{{ request_types|length }}</span></summary>
      <details class="admin-create"><summary class="btn secondary compact" style="width:max-content;">Add driver flow</summary>
        <form method="post" action="{{ url_for('admin_create_request_type') }}" class="grid three" style="margin-top:12px;">
          <div class="field"><label>Button label</label><input name="label" required></div><div class="field"><label>Colour</label><input name="button_color" type="color" value="#2563eb"></div><div class="field"><label>Sort</label><input name="sort_order" type="number" value="100"></div>
          <div class="field"><label>Driver receipt</label><select name="receipt_mode"><option value="none">No driver receipt</option><option value="ack_only">Acknowledge only</option><option value="ack_done">Acknowledge + done</option></select></div><div class="field" style="align-self:end;"><button class="btn" type="submit">Add flow</button></div>
        </form>
      </details>
      <div class="flow-tile-grid">{% for item in request_types %}<a class="flow-preview" href="{{ url_for('admin_edit_request_type', type_id=item.id) }}" style="background:{{ item.button_color }};">{{ item.label }}</a>{% endfor %}</div>
    </details>

    <details id="desk-profiles" class="panel admin-section">
      <summary>Dispatcher desk profiles <span class="small">{{ desk_profiles|length }}</span></summary>
      <details class="admin-create"><summary class="btn secondary compact" style="width:max-content;">Add desk profile</summary>
        <form method="post" action="{{ url_for('admin_create_desk_profile') }}" class="grid three" style="margin-top:12px;">
          <div class="field"><label>Desk name</label><input name="name" required></div><div class="field"><label>Description</label><input name="description"></div><div class="field"><label>Sort</label><input name="sort_order" type="number" value="100"></div>
          <div class="field" style="grid-column:1/-1;"><label>Queues</label><div class="choice-tiles">{% for group in groups_all %}<label class="chip"><input type="checkbox" name="group_id" value="{{ group.id }}" style="width:auto;min-height:0;"> {{ group.name }}</label>{% endfor %}</div></div>
          <div><button class="btn" type="submit">Add desk</button></div>
        </form>
      </details>
      {% for profile in desk_profiles %}
        <details class="compact-record">
          <summary><span class="record-title"><strong>{{ profile.name }}</strong><span>{{ profile.description or 'No description' }}</span></span><span>{{ profile.group_ids|length }} queue{{ '' if profile.group_ids|length == 1 else 's' }}</span><span class="status-badge {{ 'active' if profile.active else 'inactive' }}">{{ 'Active' if profile.active else 'Inactive' }}</span></summary>
          <div class="record-body"><form id="desk-save-{{ profile.id }}" method="post" action="{{ url_for('admin_update_desk_profile', profile_id=profile.id) }}" class="grid three">
            <div class="field"><label>Name</label><input name="name" value="{{ profile.name }}" required></div><div class="field"><label>Description</label><input name="description" value="{{ profile.description or '' }}"></div><div class="field"><label>Sort</label><input name="sort_order" type="number" value="{{ profile.sort_order }}"></div>
            <div class="field" style="grid-column:1/-1;"><label>Queues</label><div class="choice-tiles">{% for group in groups_all %}<label class="chip"><input type="checkbox" name="group_id" value="{{ group.id }}" style="width:auto;min-height:0;" {% if group.id in profile.group_ids %}checked{% endif %}> {{ group.name }}</label>{% endfor %}</div></div>
            <label class="chip"><input type="checkbox" name="active" style="width:auto;min-height:0;" {% if profile.active %}checked{% endif %}> Active</label>
          </form><div class="record-actions"><button class="btn secondary" form="desk-save-{{ profile.id }}" type="submit">Save desk</button><form method="post" action="{{ url_for('admin_delete_desk_profile', profile_id=profile.id) }}" onsubmit="return confirm('Delete this desk profile?');"><button class="btn red" type="submit">Delete</button></form></div></div>
        </details>
      {% endfor %}
    </details>

    <div class="grid two">
      <details id="queues" class="panel admin-section">
        <summary>Dashboard queues <span class="small">{{ groups_all|length }}</span></summary>
        <details class="admin-create"><summary class="btn secondary compact" style="width:max-content;">Add queue</summary><form method="post" action="{{ url_for('admin_create_group') }}" class="inline" style="margin-top:10px;"><input name="name" placeholder="Queue name" required><input name="sort_order" type="number" value="100" style="width:110px;"><button class="btn" type="submit">Add</button></form></details>
        {% for item in groups_all %}<details class="compact-record"><summary><span class="record-title"><strong>{{ item.name }}</strong><span>Sort {{ item.sort_order }}</span></span><span>Queue</span><span class="status-badge {{ 'active' if item.active else 'inactive' }}">{{ 'Active' if item.active else 'Inactive' }}</span></summary><div class="record-body"><form id="queue-save-{{ item.id }}" method="post" action="{{ url_for('admin_update_group', group_id=item.id) }}" class="grid three"><div class="field"><label>Name</label><input name="name" value="{{ item.name }}" required></div><div class="field"><label>Sort</label><input name="sort_order" type="number" value="{{ item.sort_order }}"></div><label class="chip"><input type="checkbox" name="active" style="width:auto;min-height:0;" {% if item.active %}checked{% endif %}> Active</label></form><div class="record-actions"><button class="btn secondary" form="queue-save-{{ item.id }}" type="submit">Save queue</button><form method="post" action="{{ url_for('admin_delete_group', group_id=item.id) }}" onsubmit="return confirm('Delete this queue? Depots using it will become unassigned.');"><button class="btn red" type="submit">Delete</button></form></div></div></details>{% endfor %}
      </details>

      <details id="depots" class="panel admin-section">
        <summary>Depots and routing <span class="small">{{ depots_all|length }}</span></summary>
        <details class="admin-create"><summary class="btn secondary compact" style="width:max-content;">Add depot</summary><form method="post" action="{{ url_for('admin_create_depot') }}" class="grid three" style="margin-top:10px;"><div class="field"><label>Name</label><input name="name" required></div><div class="field"><label>Queue</label><select name="dispatcher_group_id">{% for group in groups_all %}<option value="{{ group.id }}">{{ group.name }}</option>{% endfor %}</select></div><div class="field"><label>Sort</label><input name="sort_order" type="number" value="100"></div><div><button class="btn" type="submit">Add depot</button></div></form></details>
        {% for depot in depots_all %}<details class="compact-record"><summary><span class="record-title"><strong>{{ depot.name }}</strong><span>Sort {{ depot.sort_order }}</span></span><span class="routing-summary">{{ depot.group_name or 'Unassigned' }}{% if pending_by_depot.get(depot.id, 0) %} · <strong style="color:#92400e;">{{ pending_by_depot.get(depot.id, 0) }} pending</strong>{% endif %}</span><span class="status-badge {{ 'active' if depot.active else 'inactive' }}">{{ 'Active' if depot.active else 'Inactive' }}</span></summary><div class="record-body"><form id="depot-save-{{ depot.id }}" method="post" action="{{ url_for('admin_update_depot', depot_id=depot.id) }}" class="grid three"><div class="field"><label>Name</label><input name="name" value="{{ depot.name }}" required></div><div class="field"><label>Dashboard queue</label><select name="dispatcher_group_id">{% for group in groups_all %}<option value="{{ group.id }}" {% if depot.group_id == group.id %}selected{% endif %}>{{ group.name }}</option>{% endfor %}</select></div><div class="field"><label>Sort</label><input name="sort_order" type="number" value="{{ depot.sort_order }}"></div><label class="chip"><input type="checkbox" name="active" style="width:auto;min-height:0;" {% if depot.active %}checked{% endif %}> Active</label></form><div class="record-actions"><button class="btn secondary" form="depot-save-{{ depot.id }}" type="submit">Save depot</button><form method="post" action="{{ url_for('admin_delete_depot', depot_id=depot.id) }}" onsubmit="return confirm('Delete this depot? Existing request history is kept.');"><button class="btn red" type="submit">Delete</button></form></div></div></details>{% endfor %}
      </details>
    </div>
    <script>
      document.querySelectorAll('.admin-section').forEach((section) => {
        const key = `admin-section:${section.id}`;
        const saved = localStorage.getItem(key);
        if (saved !== null) section.open = saved === '1';
        section.addEventListener('toggle', () => localStorage.setItem(key, section.open ? '1' : '0'));
      });
    </script>
    """
    return render_page(
        "Admin",
        categorized_body,
        users=users,
        request_types=request_type_admin_rows(),
        groups_all=group_options(active_only=False),
        depots_all=depot_options(active_only=False),
        desk_profiles=desk_profile_options(active_only=False),
        pending_by_depot=pending_by_depot,
        total_pending=total_pending,
    )
    body = """
    <section class="hero">
      <div>
        <h1>Admin</h1>
        <p>Manage users, request buttons, depots, and dispatch routing.</p>
      </div>
    </section>
    <div class="grid two">
      <section class="panel">
        <h2>Users</h2>
        <form method="post" action="{{ url_for('admin_create_user') }}" class="grid three">
          <div class="field"><label>Username</label><input name="username" required></div>
          <div class="field"><label>Display name</label><input name="display_name" required></div>
          <div class="field">
            <label>Role</label>
            <select name="role">
              <option value="driver">Driver</option>
              <option value="dispatch">Dispatch</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <div class="field"><label>Temporary password</label><input name="password" required></div>
          <div class="field" style="align-self: end;"><button class="btn" type="submit">Create user</button></div>
        </form>
        <div class="table-wrap">
          <table>
            <thead><tr><th>User</th><th>Role</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody>
            {% for item in users %}
              <tr>
                <td>
                  <form method="post" action="{{ url_for('admin_update_user', user_id=item.id) }}">
                    <input name="username" value="{{ item.username }}" required style="margin-bottom: 6px;">
                    <input name="display_name" value="{{ item.display_name }}" required>
                </td>
                <td>
                    <select name="role">
                      {% for role in ['driver', 'dispatch', 'depot', 'admin'] %}
                        <option value="{{ role }}" {% if item.effective_role == role %}selected{% endif %}>{{ role|title }}</option>
                      {% endfor %}
                    </select>
                </td>
                <td>{{ 'Active' if item.active else 'Disabled' }}</td>
                <td>
                    <div class="split-actions">
                      <button class="btn secondary" type="submit">Save</button>
                  </form>
                  <form method="post" action="{{ url_for('admin_reset_password', user_id=item.id) }}" class="inline">
                    <input name="password" placeholder="New password" required style="width: 150px;">
                    <button class="btn amber" type="submit">Reset</button>
                  </form>
                  <form method="post" action="{{ url_for('admin_toggle_user', user_id=item.id) }}">
                    <button class="btn ghost" type="submit">{{ 'Disable' if item.active else 'Enable' }}</button>
                  </form>
                  <form method="post" action="{{ url_for('admin_delete_user', user_id=item.id) }}" onsubmit="return confirm('Delete this user? Existing request history will remain.');">
                    <button class="btn red" type="submit">Delete</button>
                  </form>
                    </div>
                </td>
              </tr>
            {% endfor %}
            </tbody>
          </table>
        </div>
      </section>
      <section class="grid">
        <section class="panel">
          <h2>Request buttons</h2>
          <form method="post" action="{{ url_for('admin_create_request_type') }}" class="inline">
            <input name="label" placeholder="Button label" required>
            <input name="sort_order" type="number" value="100" style="width: 120px;">
            <button class="btn" type="submit">Add</button>
          </form>
          <div class="list" style="margin-top: 12px;">
            {% for item in request_types %}
              <form method="post" action="{{ url_for('admin_update_request_type', type_id=item.id) }}" class="item inline">
                <input name="label" value="{{ item.label }}" required>
                <input name="sort_order" type="number" value="{{ item.sort_order }}" style="width: 100px;">
                <label class="chip"><input type="checkbox" name="active" style="width: auto; min-height: 0;" {% if item.active %}checked{% endif %}> Active</label>
                <button class="btn secondary" type="submit">Save</button>
              </form>
            {% endfor %}
          </div>
        </section>
        <section class="panel">
          <h2>Dispatcher groups</h2>
          <form method="post" action="{{ url_for('admin_create_group') }}" class="inline">
            <input name="name" placeholder="Group name" required>
            <input name="sort_order" type="number" value="100" style="width: 120px;">
            <button class="btn" type="submit">Add</button>
          </form>
          <div class="list" style="margin-top: 12px;">
            {% for item in groups_all %}
              <form method="post" action="{{ url_for('admin_update_group', group_id=item.id) }}" class="item inline">
                <input name="name" value="{{ item.name }}" required>
                <input name="sort_order" type="number" value="{{ item.sort_order }}" style="width: 100px;">
                <label class="chip"><input type="checkbox" name="active" style="width: auto; min-height: 0;" {% if item.active %}checked{% endif %}> Active</label>
                <button class="btn secondary" type="submit">Save</button>
              </form>
            {% endfor %}
          </div>
        </section>
        <section class="panel">
          <h2>Depots</h2>
          <form method="post" action="{{ url_for('admin_create_depot') }}" class="grid three">
            <div class="field"><label>Name</label><input name="name" required></div>
            <div class="field">
              <label>Dispatcher group</label>
              <select name="dispatcher_group_id">
                {% for group in groups_all %}
                  <option value="{{ group.id }}">{{ group.name }}</option>
                {% endfor %}
              </select>
            </div>
            <div class="field"><label>Sort</label><input name="sort_order" type="number" value="100"></div>
            <div><button class="btn" type="submit">Add depot</button></div>
          </form>
          <div class="list" style="margin-top: 12px;">
            {% for depot in depots_all %}
              <form method="post" action="{{ url_for('admin_update_depot', depot_id=depot.id) }}" class="item grid three">
                <input name="name" value="{{ depot.name }}" required>
                <select name="dispatcher_group_id">
                  {% for group in groups_all %}
                    <option value="{{ group.id }}" {% if depot.group_id == group.id %}selected{% endif %}>{{ group.name }}</option>
                  {% endfor %}
                </select>
                <input name="sort_order" type="number" value="{{ depot.sort_order }}">
                <label class="chip"><input type="checkbox" name="active" style="width: auto; min-height: 0;" {% if depot.active %}checked{% endif %}> Active</label>
                <button class="btn secondary" type="submit">Save</button>
              </form>
            {% endfor %}
          </div>
        </section>
      </section>
    </div>
    """
    return render_page(
        "Admin",
        body,
        users=users,
        request_types=request_type_options(active_only=False),
        groups_all=group_options(active_only=False),
        depots_all=depot_options(active_only=False),
    )


def require_form_values(*names):
    values = {name: request.form.get(name, "").strip() for name in names}
    if any(not values[name] for name in names):
        flash("Please complete all required fields.")
        return None
    return values


def schema_with_receipt(existing_raw, receipt_mode):
    schema = parse_form_schema(existing_raw)
    schema["receipt_mode"] = receipt_mode if receipt_mode in {"none", "ack_only", "ack_done"} else "none"
    return json.dumps(schema)


def slugify_key(value):
    cleaned = []
    previous_underscore = False
    for char in (value or "").lower():
        if char.isalnum():
            cleaned.append(char)
            previous_underscore = False
        elif not previous_underscore:
            cleaned.append("_")
            previous_underscore = True
    key = "".join(cleaned).strip("_")
    return key or "field"


def get_request_type_admin(type_id):
    row = query_one(
        "SELECT id, label, active, sort_order, button_color, form_schema FROM request_types WHERE id = ?",
        (type_id,),
    )
    if not row:
        abort(404)
    item = dict(row)
    item["button_color"] = sanitize_color(item.get("button_color"))
    item["form_schema"] = parse_form_schema(item.get("form_schema"))
    item["receipt_mode"] = receipt_mode_for_schema(item["form_schema"])
    return item


@app.route("/admin/users/create", methods=["POST"])
@roles_required("admin")
def admin_create_user():
    values = require_form_values("username", "display_name", "password", "role")
    if not values or values["role"] not in {"driver", "dispatch", "depot", "admin"}:
        return redirect(url_for("admin_dashboard"))
    storage_role = "driver" if values["role"] == "depot" else values["role"]
    access_role = "depot" if values["role"] == "depot" else None
    try:
        execute(
            """
            INSERT INTO users
                (username, display_name, password_hash, role, access_role, active, access_status, created_at)
            VALUES (?, ?, ?, ?, ?, 1, 'approved', ?)
            """,
            (
                values["username"],
                values["display_name"],
                generate_password_hash(values["password"]),
                storage_role,
                access_role,
                now_iso(),
            ),
        )
        new_user = query_one("SELECT id FROM users WHERE username = ?", (values["username"],))
        if new_user:
            execute("UPDATE users SET must_change_password = 1 WHERE id = ?", (new_user["id"],))
        flash("User created with a temporary password.")
    except sqlite3.IntegrityError:
        flash("That username is already in use.")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users/<int:user_id>/update", methods=["POST"])
@roles_required("admin")
def admin_update_user(user_id):
    values = require_form_values("username", "display_name", "role")
    if not values or values["role"] not in {"driver", "dispatch", "depot", "admin"}:
        return redirect(url_for("admin_dashboard"))
    storage_role = "driver" if values["role"] == "depot" else values["role"]
    access_role = "depot" if values["role"] == "depot" else None
    try:
        execute(
            """
            UPDATE users
            SET username = ?, display_name = ?, role = ?, access_role = ?,
                driver_number = CASE WHEN ? = 'driver' THEN ? ELSE driver_number END,
                home_depot_id = CASE WHEN ? = 'driver' THEN ? ELSE home_depot_id END,
                default_desk_profile_id = CASE WHEN ? = 'dispatch' THEN ? ELSE default_desk_profile_id END
            WHERE id = ?
            """,
            (values["username"], values["display_name"], storage_role, access_role, values["role"], values["username"], values["role"], request.form.get("home_depot_id") or None, values["role"], request.form.get("default_desk_profile_id") or None, user_id),
        )
        flash("User updated.")
    except sqlite3.IntegrityError:
        flash("That username is already in use.")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@roles_required("admin")
def admin_reset_password(user_id):
    password = request.form.get("password", "")
    if not password:
        flash("Enter a new password.")
    else:
        execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?",
            (generate_password_hash(password), user_id),
        )
        flash("Password reset.")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@roles_required("admin")
def admin_toggle_user(user_id):
    if user_id == current_user()["id"]:
        flash("You cannot disable your own account while logged in.")
    else:
        execute("UPDATE users SET active = CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id = ?", (user_id,))
        flash("User status updated.")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@roles_required("admin")
def admin_delete_user(user_id):
    if user_id == current_user()["id"]:
        flash("You cannot delete your own account while logged in.")
    else:
        execute("DELETE FROM users WHERE id = ?", (user_id,))
        flash("User deleted. Existing request history is kept.")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/request-types/create", methods=["POST"])
@roles_required("admin")
def admin_create_request_type():
    label = request.form.get("label", "").strip()
    sort_order = int(request.form.get("sort_order") or 100)
    button_color = sanitize_color(request.form.get("button_color"))
    form_schema = schema_with_receipt(None, request.form.get("receipt_mode", "none"))
    if label:
        try:
            type_id = execute(
                """
                INSERT INTO request_types
                    (label, sort_order, active, button_color, form_schema)
                VALUES (?, ?, 1, ?, ?)
                """,
                (label, sort_order, button_color, form_schema),
            )
            flash("Request button added.")
            return redirect(url_for("admin_edit_request_type", type_id=type_id))
        except sqlite3.IntegrityError:
            flash("That request button already exists.")
    return redirect(url_for("admin_dashboard") + "#driver-flows")


@app.route("/admin/request-types/<int:type_id>/edit")
@roles_required("admin")
def admin_edit_request_type(type_id):
    item = get_request_type_admin(type_id)
    fields = item["form_schema"].get("fields", [])
    body = """
    <section class="hero"><div><h1>Edit driver option</h1><p>{{ item.label }}</p></div><a class="btn ghost" href="{{ url_for('admin_dashboard') }}#driver-flows">Back to admin</a></section>
    <div class="grid two admin-flow-layout">
      <div>
        <section class="panel" style="margin-bottom:16px;">
          <h2>Button settings</h2>
          <form method="post" action="{{ url_for('admin_update_request_type', type_id=item.id) }}" class="grid three">
            <div class="flow-preview" style="background:{{ item.button_color }};">{{ item.label }}</div>
            <div class="field"><label>Button label</label><input name="label" value="{{ item.label }}" required></div>
            <div class="field"><label>Colour</label><input name="button_color" type="color" value="{{ item.button_color }}"></div>
            <div class="field"><label>Position</label><input name="sort_order" type="number" value="{{ item.sort_order }}"></div>
            <div class="field"><label>Driver receipt</label><select name="receipt_mode"><option value="none" {% if item.receipt_mode == 'none' %}selected{% endif %}>Sent only</option><option value="ack_only" {% if item.receipt_mode == 'ack_only' %}selected{% endif %}>Show acknowledgement</option><option value="ack_done" {% if item.receipt_mode == 'ack_done' %}selected{% endif %}>Show acknowledgement and done</option></select></div>
            <label class="chip"><input type="checkbox" name="active" style="width:auto;min-height:0;" {% if item.active %}checked{% endif %}> Active</label>
            <button class="btn secondary" type="submit">Save settings</button>
          </form>
          <div class="inline" style="margin-top:10px;">
            <form method="post" action="{{ url_for('admin_duplicate_request_type', type_id=item.id) }}"><button class="btn ghost" type="submit">Duplicate option</button></form>
            <form method="post" action="{{ url_for('admin_delete_request_type', type_id=item.id) }}" onsubmit="return confirm('Delete this option? Existing request history is kept.');"><button class="btn red" type="submit">Delete option</button></form>
          </div>
        </section>
        <section class="panel">
          <div class="item-head"><div><h2>Questions</h2><p>Drivers see these one at a time.</p></div></div>
          <div class="list admin-question-list">
          {% for field in fields %}
            <article class="item question-editor">
              <div class="item-head"><strong>{{ loop.index }}. {{ field.label }}</strong><div class="inline">
                <form method="post" action="{{ url_for('admin_move_request_field', type_id=item.id, field_index=loop.index0, direction='up') }}"><button class="btn ghost compact" type="submit" {% if loop.first %}disabled{% endif %}>↑</button></form>
                <form method="post" action="{{ url_for('admin_move_request_field', type_id=item.id, field_index=loop.index0, direction='down') }}"><button class="btn ghost compact" type="submit" {% if loop.last %}disabled{% endif %}>↓</button></form>
              </div></div>
              <form method="post" action="{{ url_for('admin_update_request_field', type_id=item.id, field_index=loop.index0) }}" class="grid two">
                <div class="field"><label>Question</label><input name="label" value="{{ field.label }}" required></div>
                <div class="field"><label>Answer type</label><select name="type">
                  <option value="choice" {% if field.type == 'choice' %}selected{% endif %}>Choice buttons</option><option value="yes_no" {% if field.type == 'yes_no' %}selected{% endif %}>Yes / No</option><option value="text" {% if field.type == 'text' %}selected{% endif %}>Short text</option><option value="number" {% if field.type == 'number' %}selected{% endif %}>Number</option><option value="note" {% if field.type == 'note' %}selected{% endif %}>Longer note</option>
                </select></div>
                <label class="chip"><input type="checkbox" name="required" style="width:auto;min-height:0;" {% if field.required %}checked{% endif %}> Required</label>
                <div class="field choices-field" style="grid-column:1/-1;"><label>Choices — one per line</label><textarea name="choices">{{ (field.choices or [])|join('\n') }}</textarea></div>
                <div class="field"><label>Show this question</label><select name="condition_field"><option value="">Always</option>{% for controller in fields[:loop.index0] %}{% if controller.type in ['choice','yes_no'] %}<option value="{{ controller.name }}" {% if field.show_when and field.show_when.field == controller.name %}selected{% endif %}>When “{{ controller.label }}”...</option>{% endif %}{% endfor %}</select></div>
                <div class="field"><label>...equals</label><input name="condition_equals" value="{{ field.show_when.equals if field.show_when else '' }}" placeholder="Exact answer"></div>
                <button class="btn secondary" type="submit">Save question</button>
              </form>
              <form method="post" action="{{ url_for('admin_delete_request_field', type_id=item.id, field_index=loop.index0) }}" onsubmit="return confirm('Delete this question?');" style="margin-top:8px;"><button class="btn red compact" type="submit">Delete</button></form>
            </article>
          {% else %}<div class="item"><p>No questions. Selecting this option goes straight to review and send.</p></div>{% endfor %}
          </div>
          <hr style="border:0;border-top:1px solid var(--line);margin:18px 0;">
          <h3>Add question</h3>
          <form method="post" action="{{ url_for('admin_add_request_field', type_id=item.id) }}" class="grid two">
            <div class="field"><label>Question</label><input name="label" placeholder="How much was left behind?" required></div>
            <div class="field"><label>Answer type</label><select name="type"><option value="choice">Choice buttons</option><option value="yes_no">Yes / No</option><option value="text">Short text</option><option value="number">Number</option><option value="note">Longer note</option></select></div>
            <label class="chip"><input type="checkbox" name="required" style="width:auto;min-height:0;" checked> Required</label>
            <div class="field" style="grid-column:1/-1;"><label>Choices — one per line</label><textarea name="choices" placeholder="0–500 L&#10;500–1000 L&#10;1000 L+"></textarea></div>
            <div class="field"><label>Show this question</label><select name="condition_field"><option value="">Always</option>{% for controller in fields %}{% if controller.type in ['choice','yes_no'] %}<option value="{{ controller.name }}">When “{{ controller.label }}”...</option>{% endif %}{% endfor %}</select></div>
            <div class="field"><label>...equals</label><input name="condition_equals" placeholder="Exact answer"></div>
            <button class="btn" type="submit">Add question</button>
          </form>
        </section>
      </div>
      <aside class="panel admin-preview"><h2>Driver preview</h2><div class="flow-preview" style="background:{{ item.button_color }};">{{ item.label }}</div><div class="preview-phone"><div class="small">Questions in order</div>{% for field in fields %}<div class="preview-question"><strong>{{ loop.index }}. {{ field.label }}</strong><div class="small">{{ field.type|replace('_',' ')|title }}{% if field.show_when %} · conditional{% endif %}</div></div>{% else %}<p>Sends directly.</p>{% endfor %}</div></aside>
    </div>
    """
    return render_page("Edit Flow", body, item=item, fields=fields)


@app.route("/admin/request-types/<int:type_id>/update", methods=["POST"])
@roles_required("admin")
def admin_update_request_type(type_id):
    existing = query_one("SELECT form_schema FROM request_types WHERE id = ?", (type_id,))
    if not existing:
        abort(404)
    execute(
        """
        UPDATE request_types
        SET label = ?, sort_order = ?, button_color = ?, form_schema = ?, active = ?
        WHERE id = ?
        """,
        (
            request.form.get("label", "").strip(),
            int(request.form.get("sort_order") or 100),
            sanitize_color(request.form.get("button_color")),
            schema_with_receipt(existing["form_schema"], request.form.get("receipt_mode", "none")),
            1 if request.form.get("active") else 0,
            type_id,
        ),
    )
    flash("Request button updated.")
    return redirect(url_for("admin_edit_request_type", type_id=type_id))


@app.route("/admin/request-types/<int:type_id>/duplicate", methods=["POST"])
@roles_required("admin")
def admin_duplicate_request_type(type_id):
    item = get_request_type_admin(type_id)
    label = f"{item['label']} Copy"
    sort_order = int(item.get("sort_order") or 100) + 1
    with get_db() as db:
        cursor = db.execute("INSERT INTO request_types (label, sort_order, active, button_color, form_schema) VALUES (?, ?, 0, ?, ?)", (label, sort_order, item["button_color"], json.dumps(item["form_schema"])))
        new_id = cursor.lastrowid
    flash("Option duplicated. Review it, then make it active.")
    return redirect(url_for("admin_edit_request_type", type_id=new_id))


@app.route("/admin/request-types/<int:type_id>/delete", methods=["POST"])
@roles_required("admin")
def admin_delete_request_type(type_id):
    execute("DELETE FROM request_types WHERE id = ?", (type_id,))
    flash("Flow button deleted. Existing request history is kept.")
    return redirect(url_for("admin_dashboard") + "#driver-flows")


def choices_from_form():
    return [
        line.strip()
        for line in request.form.get("choices", "").splitlines()
        if line.strip()
    ]


def save_request_schema(type_id, schema):
    execute(
        "UPDATE request_types SET form_schema = ? WHERE id = ?",
        (json.dumps(schema), type_id),
    )


@app.route("/admin/request-types/<int:type_id>/fields/add", methods=["POST"])
@roles_required("admin")
def admin_add_request_field(type_id):
    item = get_request_type_admin(type_id)
    label = request.form.get("label", "").strip()
    if not label:
        flash("Field label is required.")
        return redirect(url_for("admin_edit_request_type", type_id=type_id))
    field_type = request.form.get("type", "choice")
    allowed_types = {"choice", "yes_no", "text", "number", "note"}
    base_name = slugify_key(label) or "question"
    used_names = {field.get("name") for field in item["form_schema"].get("fields", [])}
    field_name = base_name
    suffix = 2
    while field_name in used_names:
        field_name = f"{base_name}_{suffix}"
        suffix += 1
    field = {"id": field_name, "name": field_name, "label": label, "type": field_type if field_type in allowed_types else "choice", "required": bool(request.form.get("required"))}
    if field["type"] == "choice": field["choices"] = choices_from_form()
    condition_field = request.form.get("condition_field", "").strip()
    condition_equals = request.form.get("condition_equals", "").strip()
    if condition_field and condition_equals: field["show_when"] = {"field": condition_field, "equals": condition_equals}
    item["form_schema"].setdefault("fields", []).append(field)
    save_request_schema(type_id, item["form_schema"])
    flash("Field added.")
    return redirect(url_for("admin_edit_request_type", type_id=type_id))


@app.route("/admin/request-types/<int:type_id>/fields/<int:field_index>/update", methods=["POST"])
@roles_required("admin")
def admin_update_request_field(type_id, field_index):
    item = get_request_type_admin(type_id)
    fields = item["form_schema"].setdefault("fields", [])
    if field_index < 0 or field_index >= len(fields):
        abort(404)
    existing_field = fields[field_index]
    field_type = request.form.get("type", "choice")
    allowed_types = {"choice", "yes_no", "text", "number", "note"}
    stable_name = existing_field.get("name") or slugify_key(existing_field.get("label", "question"))
    fields[field_index] = {"id": existing_field.get("id", stable_name), "name": stable_name, "label": request.form.get("label", "").strip() or "Question", "type": field_type if field_type in allowed_types else "choice", "required": bool(request.form.get("required"))}
    if fields[field_index]["type"] == "choice": fields[field_index]["choices"] = choices_from_form()
    condition_field = request.form.get("condition_field", "").strip()
    condition_equals = request.form.get("condition_equals", "").strip()
    if condition_field and condition_equals: fields[field_index]["show_when"] = {"field": condition_field, "equals": condition_equals}
    save_request_schema(type_id, item["form_schema"])
    flash("Field updated.")
    return redirect(url_for("admin_edit_request_type", type_id=type_id))


@app.route("/admin/request-types/<int:type_id>/fields/<int:field_index>/move/<direction>", methods=["POST"])
@roles_required("admin")
def admin_move_request_field(type_id, field_index, direction):
    item = get_request_type_admin(type_id)
    fields = item["form_schema"].setdefault("fields", [])
    target = field_index - 1 if direction == "up" else field_index + 1
    if 0 <= field_index < len(fields) and 0 <= target < len(fields):
        fields[field_index], fields[target] = fields[target], fields[field_index]
        save_request_schema(type_id, item["form_schema"])
    return redirect(url_for("admin_edit_request_type", type_id=type_id))


@app.route("/admin/request-types/<int:type_id>/fields/<int:field_index>/delete", methods=["POST"])
@roles_required("admin")
def admin_delete_request_field(type_id, field_index):
    item = get_request_type_admin(type_id)
    fields = item["form_schema"].setdefault("fields", [])
    if field_index < 0 or field_index >= len(fields):
        abort(404)
    fields.pop(field_index)
    save_request_schema(type_id, item["form_schema"])
    flash("Field deleted.")
    return redirect(url_for("admin_edit_request_type", type_id=type_id))


@app.route("/admin/groups/create", methods=["POST"])
@roles_required("admin")
def admin_create_group():
    name = request.form.get("name", "").strip()
    sort_order = int(request.form.get("sort_order") or 100)
    if name:
        try:
            execute(
                "INSERT INTO dispatcher_groups (name, sort_order, active) VALUES (?, ?, 1)",
                (name, sort_order),
            )
            flash("Dispatcher group added.")
        except sqlite3.IntegrityError:
            flash("That dispatcher group already exists.")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/groups/<int:group_id>/update", methods=["POST"])
@roles_required("admin")
def admin_update_group(group_id):
    execute(
        "UPDATE dispatcher_groups SET name = ?, sort_order = ?, active = ? WHERE id = ?",
        (
            request.form.get("name", "").strip(),
            int(request.form.get("sort_order") or 100),
            1 if request.form.get("active") else 0,
            group_id,
        ),
    )
    flash("Dispatcher group updated.")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/groups/<int:group_id>/delete", methods=["POST"])
@roles_required("admin")
def admin_delete_group(group_id):
    group = query_one("SELECT id, name FROM dispatcher_groups WHERE id = ?", (group_id,))
    if not group:
        abort(404)
    execute("DELETE FROM dispatcher_groups WHERE id = ?", (group_id,))
    flash(f"Queue {group['name']} deleted. Existing request history is kept.")
    return redirect(url_for("admin_dashboard") + "#queues")


def replace_desk_profile_groups(db, profile_id, group_ids):
    db.execute("DELETE FROM desk_profile_groups WHERE desk_profile_id = ?", (profile_id,))
    for group_id in group_ids:
        db.execute(
            """
            INSERT OR IGNORE INTO desk_profile_groups
                (desk_profile_id, dispatcher_group_id)
            VALUES (?, ?)
            """,
            (profile_id, group_id),
        )


@app.route("/admin/desk-profiles/create", methods=["POST"])
@roles_required("admin")
def admin_create_desk_profile():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    sort_order = int(request.form.get("sort_order") or 100)
    group_ids = request.form.getlist("group_id")
    if not name:
        flash("Desk profile name is required.")
        return redirect(url_for("admin_dashboard") + "#desk-profiles")
    try:
        with closing(get_db()) as db:
            cur = db.execute(
                """
                INSERT INTO desk_profiles
                    (name, description, is_default, active, sort_order)
                VALUES (?, ?, ?, 1, ?)
                """,
                (name, description, 0, sort_order),
            )
            replace_desk_profile_groups(db, cur.lastrowid, group_ids)
            db.commit()
        flash("Desk profile created.")
    except sqlite3.IntegrityError:
        flash("That desk profile already exists.")
    return redirect(url_for("admin_dashboard") + "#desk-profiles")


@app.route("/admin/desk-profiles/<int:profile_id>/update", methods=["POST"])
@roles_required("admin")
def admin_update_desk_profile(profile_id):
    with closing(get_db()) as db:
        profile = db.execute("SELECT id FROM desk_profiles WHERE id = ?", (profile_id,)).fetchone()
        if not profile:
            abort(404)
        is_default = 0
        db.execute(
            """
            UPDATE desk_profiles
            SET name = ?, description = ?, sort_order = ?, active = ?, is_default = ?
            WHERE id = ?
            """,
            (
                request.form.get("name", "").strip(),
                request.form.get("description", "").strip(),
                int(request.form.get("sort_order") or 100),
                1 if request.form.get("active") else 0,
                is_default,
                profile_id,
            ),
        )
        replace_desk_profile_groups(db, profile_id, request.form.getlist("group_id"))
        db.commit()
    flash("Desk profile updated.")
    return redirect(url_for("admin_dashboard") + "#desk-profiles")


@app.route("/admin/desk-profiles/<int:profile_id>/delete", methods=["POST"])
@roles_required("admin")
def admin_delete_desk_profile(profile_id):
    execute("DELETE FROM desk_profiles WHERE id = ?", (profile_id,))
    flash("Desk profile deleted.")
    return redirect(url_for("admin_dashboard") + "#desk-profiles")


@app.route("/admin/depots/create", methods=["POST"])
@roles_required("admin")
def admin_create_depot():
    name = request.form.get("name", "").strip()
    group_id = request.form.get("dispatcher_group_id") or None
    sort_order = int(request.form.get("sort_order") or 100)
    if name:
        try:
            execute(
                """
                INSERT INTO depots
                    (name, dispatcher_group_id, sort_order, active)
                VALUES (?, ?, ?, 1)
                """,
                (name, group_id, sort_order),
            )
            flash("Depot added.")
        except sqlite3.IntegrityError:
            flash("That depot already exists.")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/depots/<int:depot_id>/update", methods=["POST"])
@roles_required("admin")
def admin_update_depot(depot_id):
    execute(
        """
        UPDATE depots
        SET name = ?, dispatcher_group_id = ?, sort_order = ?, active = ?
        WHERE id = ?
        """,
        (
            request.form.get("name", "").strip(),
            request.form.get("dispatcher_group_id") or None,
            int(request.form.get("sort_order") or 100),
            1 if request.form.get("active") else 0,
            depot_id,
        ),
    )
    flash("Depot updated.")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/depots/<int:depot_id>/delete", methods=["POST"])
@roles_required("admin")
def admin_delete_depot(depot_id):
    depot = query_one("SELECT id, name FROM depots WHERE id = ?", (depot_id,))
    if not depot:
        abort(404)
    execute("DELETE FROM depots WHERE id = ?", (depot_id,))
    flash(f"Depot {depot['name']} deleted. Existing request history is kept.")
    return redirect(url_for("admin_dashboard") + "#depots")


if __name__ == "__main__":
    init_db()
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)), debug=True)
