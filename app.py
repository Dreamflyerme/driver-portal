from flask import Flask, request, redirect, url_for, render_template_string, jsonify, session
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash

APP_TITLE = "Driver Request Portal"
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "requests.db"
CONFIG_PATH = BASE_DIR / "config.json"

# Driver request workflows are config-driven through custom_flows in config.json.
DEFAULT_CONFIG = {
    "queues": [],
    "depots": {},
    "dispatcher_profiles": {},
    "custom_flows": []
}

app = Flask(__name__)
app.secret_key = "CHANGE_THIS_SECRET_KEY"


def ensure_config_file():
    if not CONFIG_PATH.exists():
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)


def load_config():
    ensure_config_file()
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    if "queues" not in config:
        config["queues"] = []
    if "depots" not in config:
        config["depots"] = {}
    if "dispatcher_profiles" not in config:
        config["dispatcher_profiles"] = {}
    if "custom_flows" not in config:
        config["custom_flows"] = []

    return config


def save_config(config):
    config["queues"] = sorted(set(config.get("queues", [])))
    config["depots"] = dict(sorted(config.get("depots", {}).items()))
    config["dispatcher_profiles"] = dict(sorted(config.get("dispatcher_profiles", {}).items()))
    config["custom_flows"] = sorted(config.get("custom_flows", []), key=lambda x: (x.get("display_order", 999), x.get("label", "")))

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def get_dispatch_queues():
    return load_config().get("queues", [])


def get_depots():
    config = load_config()
    return [
        {"name": depot_name, "dispatch_queue": queue_name}
        for depot_name, queue_name in sorted(config.get("depots", {}).items())
    ]


def get_depot_queue_map():
    return load_config().get("depots", {})


def get_dispatch_queue(depot):
    return get_depot_queue_map().get(depot, "Unmapped")


def get_dispatcher_profiles():
    config = load_config()
    return [
        {"name": profile_name, "queues": queues}
        for profile_name, queues in sorted(config.get("dispatcher_profiles", {}).items())
    ]



def get_custom_flows(active_only=True):
    config = load_config()
    flows = config.get("custom_flows", [])
    if active_only:
        flows = [f for f in flows if f.get("active", True)]
    return sorted(flows, key=lambda x: (x.get("display_order", 999), x.get("label", "")))


def get_v2_default_flows():
    return [
        {
            "key": "V2_CALL_ME",
            "label": "Call me please V2",
            "active": True,
            "display_order": 110,
            "button_class": "request",
            "steps": []
        },
        {
            "key": "V2_MILK_LEFT_BEHIND",
            "label": "Milk Left Behind V2",
            "active": True,
            "display_order": 120,
            "button_class": "request",
            "steps": [
                {"id": "supply_number", "type": "number", "label": "Supply Number", "required": True},
                {"id": "volume_band", "type": "choice", "label": "Volume Band", "required": True, "choices": ["0-500L", "500-1200L", "1200L+"]},
                {"id": "milk_still_stirred", "type": "yes_no", "label": "Is milk still being stirred?", "required": True, "show_if": {"field": "volume_band", "equals": "0-500L"}}
            ]
        },
        {
            "key": "V2_CIP",
            "label": "CIP V2",
            "active": True,
            "display_order": 130,
            "button_class": "warning",
            "steps": [
                {"id": "cip_action", "type": "choice", "label": "CIP Action", "required": True, "choices": ["Remove my CIP", "Add CIP EOD"]}
            ]
        },
        {
            "key": "V2_SUPPLIER_MILKING",
            "label": "Supplier Milking V2",
            "active": True,
            "display_order": 140,
            "button_class": "request",
            "steps": [
                {"id": "supply_number", "type": "number", "label": "Supply Number", "required": True}
            ]
        },
        {
            "key": "V2_SPLIT_CLEARED",
            "label": "Split has been cleared V2",
            "active": True,
            "display_order": 150,
            "button_class": "request",
            "steps": [
                {"id": "supply_number", "type": "number", "label": "Supply Number", "required": True}
            ]
        },
        {
            "key": "V2_DRIVER_MESSAGE",
            "label": "Send Message V2",
            "active": True,
            "display_order": 160,
            "button_class": "primary",
            "steps": [
                {"id": "message", "type": "message", "label": "Message", "required": True}
            ]
        }
    ]


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(conn, table_name, column_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def init_db():
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                display_name TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                depot TEXT NOT NULL,
                dispatch_queue TEXT,
                original_dispatch_queue TEXT,
                truck_number TEXT NOT NULL,
                driver_name TEXT NOT NULL,
                request_type TEXT NOT NULL,
                supply_number TEXT,
                volume TEXT,
                cip_action TEXT,
                message TEXT,
                volume_band TEXT,
                milk_still_stirred TEXT,
                dispatcher_notes TEXT,
                reassigned_at TEXT,
                payload_json TEXT,
                supply_search TEXT,
                status TEXT NOT NULL DEFAULT 'New'
            )
            """
        )

        # Safe migration support for older prototype databases.
        for column_name, column_type in [
            ("dispatch_queue", "TEXT"),
            ("original_dispatch_queue", "TEXT"),
            ("volume", "TEXT"),
            ("cip_action", "TEXT"),
            ("message", "TEXT"),
            ("volume_band", "TEXT"),
            ("milk_still_stirred", "TEXT"),
            ("dispatcher_notes", "TEXT"),
            ("reassigned_at", "TEXT"),
            ("payload_json", "TEXT"),
            ("supply_search", "TEXT"),
        ]:
            if not column_exists(conn, "requests", column_name):
                conn.execute(f"ALTER TABLE requests ADD COLUMN {column_name} {column_type}")

        # Create default admin account if missing.
        existing_admin = conn.execute("SELECT id FROM users WHERE username = ?", ("admin",)).fetchone()
        if not existing_admin:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, active, display_name) VALUES (?, ?, ?, ?, ?)",
                (
                    "admin",
                    generate_password_hash("admin123"),
                    "admin",
                    1,
                    "System Admin",
                ),
            )

        conn.commit()


LOGIN_HTML = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Login</title>
<style>
body { font-family: Arial, sans-serif; background:#f3f4f6; margin:0; }
.wrap { max-width:420px; margin:60px auto; padding:20px; }
.card { background:white; border-radius:14px; padding:20px; box-shadow:0 2px 12px rgba(0,0,0,.08); }
input, select, button { width:100%; box-sizing:border-box; padding:14px; margin:8px 0; border-radius:10px; border:1px solid #d1d5db; }
button { background:#111827; color:white; border:0; font-weight:bold; cursor:pointer; }
.error { color:#991b1b; font-weight:bold; }
.muted { color:#6b7280; font-size:13px; }
</style>
</head>
<body>
<div class="wrap">
<div class="card">
<h2>Driver Portal Login</h2>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<form method="post">
<input name="username" placeholder="Username" required>
<input name="password" type="password" placeholder="Password" required>
<select name="role">
<option value="driver">Driver</option>
<option value="dispatcher">Dispatcher</option>
<option value="admin">Admin</option>
</select>
<button type="submit">Login</button>
</form>
<p class="muted">Default admin login:<br>admin / admin123</p>
</div>
</div>
</body>
</html>
"""


DRIVER_HTML = """
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ app_title }}</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 0; background: #f3f4f6; color: #111827; }
    .wrap { max-width: 520px; margin: 0 auto; padding: 18px; }
    .card { background: white; border-radius: 14px; padding: 18px; box-shadow: 0 2px 12px rgba(0,0,0,.08); margin-bottom: 16px; }
    h1 { font-size: 24px; margin: 8px 0 16px; }
    h2 { font-size: 18px; margin: 0 0 12px; }
    label { display:block; margin: 12px 0 6px; font-weight: bold; }
    input, select, textarea { width: 100%; box-sizing: border-box; padding: 14px; border: 1px solid #d1d5db; border-radius: 10px; font-size: 18px; }
    textarea { resize: vertical; min-height: 90px; }
    button { width: 100%; padding: 18px; margin: 8px 0; border: 0; border-radius: 12px; font-size: 20px; font-weight: bold; cursor: pointer; }
    .choiceRow { display:grid; grid-template-columns: 1fr; gap: 8px; }
    .yesno { display:grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .yesno button { margin: 0; }
    .primary { background: #111827; color: white; }
    .request { background: #2563eb; color: white; }
    .warning { background: #f59e0b; color: #111827; }
    .danger { background: #dc2626; color: white; }
    .info { background: #0ea5e9; color: white; }
    .muted { background: #e5e7eb; color: #111827; }
    .hidden { display:none; }
    .small { font-size: 14px; color: #6b7280; }
    .driver-banner { font-size: 14px; color: #374151; margin-bottom: 8px; line-height: 1.5; }
    .stepBox { background:#f9fafb; border:1px solid #e5e7eb; border-radius:12px; padding:12px; margin:10px 0; }
    .stepChoices { display:grid; grid-template-columns:1fr; gap:8px; }
    .stepChoices button { margin:0; }
    .queue-pill { display:inline-block; background:#dbeafe; color:#1e3a8a; padding: 4px 8px; border-radius: 999px; font-weight:bold; }
  </style>
</head>
<body>
<div class="wrap">
  <h1>Driver Requests</h1>

  <div id="setupCard" class="card">
    <h2>Start of shift</h2>
    <label>Depot</label>
    <select id="depot" onchange="updateQueuePreview()">
      <option value="">Select depot...</option>
      {% for depot in depots %}
      <option>{{ depot.name }}</option>
      {% endfor %}
    </select>
    <div class="small" style="margin-top:8px;">Routes to: <span id="queuePreview" class="queue-pill">-</span></div>

    <label>Truck Number</label>
    <input id="truck_number" inputmode="numeric" placeholder="e.g. 421">

    <label>Driver Name</label>
    <input id="driver_name" placeholder="e.g. Nick">

    <button class="primary" type="button" onclick="saveDriverInfo()">Start</button>
  </div>

  <div id="requestCard" class="card hidden">
    <div class="driver-banner" id="driverBanner"></div>
    <button class="muted" type="button" onclick="editDriverInfo()">Change driver details</button>

    {% for flow in custom_flows %}
      <button class="{{ flow.button_class }}" type="button" onclick="startCustomFlow('{{ flow.key }}')">{{ flow.label }}</button>
    {% endfor %}
  </div>

  <div id="customFlowPanel" class="card hidden">
    <h2 id="customFlowTitle">Custom Flow</h2>
    <div class="small" id="customFlowProgress"></div>
    <div id="customFlowSteps"></div>
    <button id="customBackBtn" class="muted" type="button" onclick="customFlowBack()">Back</button>
    <button class="muted" type="button" onclick="hidePanels()">Back to menu</button>
  </div>

  <p class="small">Dispatcher board: <a href="/dispatcher">/dispatcher</a></p>
</div>

<script>
const DEPOT_QUEUE_MAP = {{ depot_queue_map|tojson }};
const CUSTOM_FLOWS = {{ custom_flows|tojson }};
let activeCustomFlow = null;
let customFlowIndex = 0;
let customFlowPayload = {};
let customFlowHistory = [];

function getQueueForDepot(depot) {
  return DEPOT_QUEUE_MAP[depot] || 'Unmapped';
}

function updateQueuePreview() {
  const depot = document.getElementById('depot').value;
  document.getElementById('queuePreview').innerText = depot ? getQueueForDepot(depot) : '-';
}

function getDriverInfo() {
  return {
    depot: localStorage.getItem('depot') || '',
    truck_number: localStorage.getItem('truck_number') || '',
    driver_name: localStorage.getItem('driver_name') || ''
  };
}

function saveDriverInfo() {
  const depot = document.getElementById('depot').value.trim();
  const truck = document.getElementById('truck_number').value.trim();
  const driver = document.getElementById('driver_name').value.trim();

  if (!depot || !truck || !driver) {
    alert('Please enter depot, truck number, and driver name.');
    return;
  }

  localStorage.setItem('depot', depot);
  localStorage.setItem('truck_number', truck);
  localStorage.setItem('driver_name', driver);
  loadScreen();
}

function editDriverInfo() {
  document.getElementById('setupCard').classList.remove('hidden');
  document.getElementById('requestCard').classList.add('hidden');
  hidePanels();
}

function loadScreen() {
  const info = getDriverInfo();
  document.getElementById('depot').value = info.depot;
  document.getElementById('truck_number').value = info.truck_number;
  document.getElementById('driver_name').value = info.driver_name;
  updateQueuePreview();

  if (info.depot && info.truck_number && info.driver_name) {
    const q = getQueueForDepot(info.depot);
    document.getElementById('setupCard').classList.add('hidden');
    document.getElementById('requestCard').classList.remove('hidden');
    document.getElementById('driverBanner').innerHTML = `${info.depot} | Truck ${info.truck_number} | ${info.driver_name}<br>Queue: <span class="queue-pill">${q}</span>`;
  } else {
    document.getElementById('setupCard').classList.remove('hidden');
    document.getElementById('requestCard').classList.add('hidden');
  }
}

function hidePanels() {
  ['customFlowPanel'].forEach(id => {
    document.getElementById(id).classList.add('hidden');
  });

  activeCustomFlow = null;
  customFlowIndex = 0;
  customFlowPayload = {};
  customFlowHistory = [];

  const info = getDriverInfo();
  if (info.depot && info.truck_number && info.driver_name) {
    document.getElementById('requestCard').classList.remove('hidden');
  }
}

async function submitRequest(payload) {
  const info = getDriverInfo();
  const fullPayload = { ...info, ...payload };

  const res = await fetch('/submit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fullPayload)
  });

  if (res.ok) {
    hidePanels();
    alert('Sent');
  } else {
    alert('Failed to send request.');
  }
}

function startCustomFlow(flowKey) {
  const flow = CUSTOM_FLOWS.find(f => f.key === flowKey);
  if (!flow) return;

  hidePanels();

  activeCustomFlow = flow;
  customFlowIndex = 0;
  customFlowPayload = {};
  customFlowHistory = [];

  document.getElementById('requestCard').classList.add('hidden');
  document.getElementById('customFlowTitle').innerText = flow.label;

  if (!flow.steps || flow.steps.length === 0) {
    submitCustomFlow();
    return;
  }

  document.getElementById('customFlowPanel').classList.remove('hidden');
  renderCurrentCustomStep();
}

function getVisibleCustomSteps() {
  if (!activeCustomFlow) return [];
  return (activeCustomFlow.steps || []).filter(step => isStepVisibleFromPayload(step));
}

function isStepVisibleFromPayload(step) {
  if (!step.show_if) return true;
  const field = step.show_if.field;
  const expected = step.show_if.equals;
  return customFlowPayload[field] === expected;
}

function renderCurrentCustomStep() {
  const box = document.getElementById('customFlowSteps');
  box.innerHTML = '';

  const visibleSteps = getVisibleCustomSteps();

  if (customFlowIndex >= visibleSteps.length) {
    submitCustomFlow();
    return;
  }

  const step = visibleSteps[customFlowIndex];
  const progress = document.getElementById('customFlowProgress');
  progress.innerText = `Step ${customFlowIndex + 1} of ${visibleSteps.length}`;

  document.getElementById('customBackBtn').classList.toggle('hidden', customFlowHistory.length === 0);

  const wrap = document.createElement('div');
  wrap.className = 'stepBox';
  wrap.id = 'custom_wrap_' + step.id;

  if (step.type === 'text' || step.type === 'number') {
    wrap.innerHTML = `
      <label>${step.label}${step.required ? ' *' : ''}</label>
      <input id="custom_step_value" ${step.type === 'number' ? 'inputmode="numeric"' : ''} placeholder="${step.label}" value="${customFlowPayload[step.id] || ''}">
      <button class="primary" type="button" onclick="customFlowNextFromInput('${step.id}', '${step.label.replace(/'/g, "\'")}', ${step.required ? 'true' : 'false'})">Next</button>
    `;
  } else if (step.type === 'message') {
    wrap.innerHTML = `
      <label>${step.label}${step.required ? ' *' : ''}</label>
      <textarea id="custom_step_value" maxlength="200" placeholder="${step.label}">${customFlowPayload[step.id] || ''}</textarea>
      <button class="primary" type="button" onclick="customFlowNextFromInput('${step.id}', '${step.label.replace(/'/g, "\'")}', ${step.required ? 'true' : 'false'})">Next</button>
    `;
  } else if (step.type === 'choice' || step.type === 'yes_no') {
    const choices = step.type === 'yes_no' ? ['Yes', 'No'] : (step.choices || []);
    let html = `<label>${step.label}${step.required ? ' *' : ''}</label><div class="stepChoices">`;
    for (const choice of choices) {
      const safeChoice = String(choice).replace(/'/g, "\'");
      html += `<button class="request" type="button" onclick="customFlowNextFromChoice('${step.id}', '${step.label.replace(/'/g, "\'")}', '${safeChoice}')">${choice}</button>`;
    }
    html += `</div>`;
    wrap.innerHTML = html;
  }

  box.appendChild(wrap);

  const input = document.getElementById('custom_step_value');
  if (input) {
    input.focus();
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && input.tagName !== 'TEXTAREA') {
        event.preventDefault();
        customFlowNextFromInput(step.id, step.label, step.required);
      }
    });
  }
}

function customFlowNextFromInput(stepId, stepLabel, required) {
  const el = document.getElementById('custom_step_value');
  const value = el ? el.value.trim() : '';

  if (required && !value) {
    alert('Complete required field: ' + stepLabel);
    return;
  }

  customFlowHistory.push({ index: customFlowIndex, payload: JSON.parse(JSON.stringify(customFlowPayload)) });

  if (value) {
    customFlowPayload[stepId] = value;
  } else {
    delete customFlowPayload[stepId];
  }

  customFlowIndex = getNextCustomIndex();
  renderCurrentCustomStep();
}

function customFlowNextFromChoice(stepId, stepLabel, value) {
  customFlowHistory.push({ index: customFlowIndex, payload: JSON.parse(JSON.stringify(customFlowPayload)) });
  customFlowPayload[stepId] = value;
  customFlowIndex = getNextCustomIndex();
  renderCurrentCustomStep();
}

function getNextCustomIndex() {
  const visibleSteps = getVisibleCustomSteps();
  return Math.min(customFlowIndex + 1, visibleSteps.length);
}

function customFlowBack() {
  const previous = customFlowHistory.pop();
  if (!previous) return;
  customFlowIndex = previous.index;
  customFlowPayload = previous.payload;
  renderCurrentCustomStep();
}

function setCustomChoice(stepId, value) {
  customFlowPayload[stepId] = value;
}

function isStepVisible(step) {
  return isStepVisibleFromPayload(step);
}

function updateCustomVisibility() {
  // Legacy helper kept so older custom flow calls do not break.
}

function submitCustomFlow() {
  if (!activeCustomFlow) return;

  const display = [];
  const steps = activeCustomFlow.steps || [];

  for (const step of steps) {
    if (!isStepVisibleFromPayload(step)) continue;
    const value = customFlowPayload[step.id] || '';
    if (value) {
      display.push(step.label + ': ' + value);
    }
  }

  submitRequest({
    request_type: activeCustomFlow.label,
    message: display.join(' | '),
    payload_json: JSON.stringify(customFlowPayload)
  });

  activeCustomFlow = null;
  customFlowIndex = 0;
  customFlowPayload = {};
  customFlowHistory = [];
}

loadScreen();
</script>
</body>
</html>
"""


ADMIN_HTML = """
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Driver Portal Admin</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 0; background: #f3f4f6; color: #111827; }
    .wrap { max-width: 1100px; margin: 0 auto; padding: 18px; }
    h1 { margin: 8px 0 16px; }
    h2 { margin: 0 0 12px; font-size: 20px; }
    .card { background: white; border-radius: 14px; padding: 16px; box-shadow: 0 2px 12px rgba(0,0,0,.08); margin-bottom: 16px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 8px; border-bottom: 1px solid #e5e7eb; text-align: left; vertical-align: middle; }
    th { background: #111827; color: white; }
    input, select { width: 100%; box-sizing: border-box; padding: 9px; border: 1px solid #d1d5db; border-radius: 8px; }
    button { padding: 9px 12px; border: 0; border-radius: 8px; margin: 2px; cursor: pointer; font-weight: bold; }
    .primary { background: #111827; color: white; }
    .save { background: #16a34a; color: white; }
    .danger { background: #dc2626; color: white; }
    .info { background: #0ea5e9; color: white; }
    .mutedBtn { background: #e5e7eb; color: #111827; }
    .muted { color: #6b7280; font-size: 13px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .flowCard { border:1px solid #e5e7eb; border-radius:12px; margin:10px 0; background:#f9fafb; overflow:hidden; }
    .flowSummary { display:grid; grid-template-columns: 70px 1.5fr 110px 90px 90px 140px; gap:8px; align-items:center; padding:10px; background:white; border-bottom:1px solid #e5e7eb; }
    .flowEditor { padding:12px; display:none; }
    .flowEditor.open { display:block; }
    .stepLine { margin:6px 0 6px 18px; padding:8px; border-left:4px solid #2563eb; background:white; border-radius:8px; }
    .mini { font-size:12px; padding:5px 8px; }
    .style-pill { display:inline-block; padding:4px 8px; border-radius:999px; font-size:12px; font-weight:bold; }
    .pill-request { background:#dbeafe; color:#1e3a8a; }
    .pill-primary { background:#e5e7eb; color:#111827; }
    .pill-warning { background:#fef3c7; color:#92400e; }
    .pill-danger { background:#fee2e2; color:#991b1b; }
    .pill-info { background:#e0f2fe; color:#075985; }
    .pill-muted { background:#f3f4f6; color:#6b7280; }
    @media (max-width: 900px) { .flowSummary { grid-template-columns: 1fr; } }
    @media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
<div class="wrap">
  <h1>Dispatcher Admin</h1>
  <div id="adminError" class="card" style="display:none; border-left:6px solid #dc2626; color:#991b1b;"></div>
  <p class="muted"><a href="/dispatcher">Back to Dispatcher Board</a></p>

  <div class="card">
      <h2>Users</h2>
      <table>
        <thead><tr><th>Username</th><th>Role</th><th>Active</th><th>Password</th><th>Delete</th></tr></thead>
        <tbody id="userRows"></tbody>
      </table>

      <h3>Add user</h3>
      <div class="grid">
        <input id="newUsername" placeholder="Username">
        <input id="newPassword" placeholder="Password">
      </div>

      <div class="grid">
        <select id="newUserRole">
          <option value="driver">driver</option>
          <option value="dispatcher">dispatcher</option>
          <option value="admin">admin</option>
        </select>
        <button class="primary" onclick="addUser()">Add User</button>
      </div>
    </div>

    <div class="grid">
    <div class="card">
      <h2>Queues</h2>
      <table>
        <thead><tr><th>Queue</th><th>Action</th></tr></thead>
        <tbody id="queueRows"></tbody>
      </table>
      <h3>Add queue</h3>
      <input id="newQueueName" placeholder="Queue name">
      <button class="primary" onclick="addQueue()">Add Queue</button>
      <p class="muted">Deleting a queue is blocked if depots still route to it.</p>
    </div>

    <div class="card">
      <h2>Dispatcher Desk Profiles</h2>
      <table>
        <thead><tr><th>Desk/Profile</th><th>Queues</th><th>Save</th><th>Delete</th></tr></thead>
        <tbody id="profileRows"></tbody>
      </table>
      <h3>Add profile</h3>
      <input id="newProfileName" placeholder="e.g. Lower South Desk">
      <div id="newProfileQueues"></div>
      <button class="primary" onclick="addProfile()">Add Profile</button>
      <p class="muted">Profiles are desk presets only. Dispatchers can still manually add or remove queues after loading.</p>
    </div>
  </div>

  <div class="card">
    <h2>Driver Flows</h2>
    <div id="customFlowRows"></div>
    <h3>Add custom flow</h3>
    <div class="grid">
      <input id="newFlowLabel" placeholder="e.g. Yard Delay">
      <select id="newFlowStyle">
        <option value="request">request</option>
        <option value="primary">primary</option>
        <option value="warning">warning</option>
        <option value="danger">danger</option>
        <option value="muted">muted</option>
      </select>
    </div>
    <button class="primary" onclick="addCustomFlow()">Add Custom Flow</button>
    <button class="info" onclick="seedV2Defaults()">Create V2 Copies of Built-in Options</button>
    <p class="muted">All driver buttons now come from config.json custom flows. Create/edit flows here.</p>
  </div>

  <div class="card">
    <h2>Depot Routing</h2>
    <table>
      <thead><tr><th>Depot</th><th>Routes To Queue</th><th>Save</th><th>Delete</th></tr></thead>
      <tbody id="depotRows"></tbody>
    </table>
    <h3>Add depot</h3>
    <div class="grid">
      <input id="newDepotName" placeholder="Depot name">
      <select id="newDepotQueue"></select>
    </div>
    <button class="primary" onclick="addDepot()">Add Depot</button>
  </div>
</div>

<script>
let adminData = {{ admin_initial_data|tojson }};

function escapeHtml(value) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function showAdminError(message) {
  const box = document.getElementById('adminError');
  box.style.display = 'block';
  box.innerText = message;
}

function clearAdminError() {
  const box = document.getElementById('adminError');
  box.style.display = 'none';
  box.innerText = '';
}

function renderAdminFromData() {
  renderUsers();
  renderQueues();
  renderDepots();
  renderNewDepotQueue();
  renderProfiles();
  renderNewProfileQueues();
  renderCustomFlows();
}

async function loadAdmin() {
  clearAdminError();

  // First render from server-embedded data. This avoids corporate browsers/networks
  // that block JavaScript fetch/XHR calls but still allow normal page loads.
  if (adminData) {
    renderAdminFromData();
  }

  // Then try to refresh in the background. If fetch is blocked, keep the embedded data.
  try {
    const res = await fetch('/data/admin/config');
    const text = await res.text();

    if (!res.ok) {
      showAdminError('Live refresh failed: HTTP ' + res.status + ' - ' + text.slice(0, 500));
      return;
    }

    adminData = JSON.parse(text);
    renderAdminFromData();
  } catch (err) {
    if (!adminData) {
      showAdminError('Admin config failed to load: ' + err);
    }
  }
}

function renderUsers() {
  const body = document.getElementById('userRows');
  body.innerHTML = '';

  if (!adminData.users || adminData.users.length === 0) {
    body.innerHTML = '<tr><td colspan="5" class="muted">No users configured.</td></tr>';
    return;
  }

  for (const u of adminData.users) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${escapeHtml(u.username)}</td>
      <td>${escapeHtml(u.role)}</td>
      <td>${u.active ? 'Yes' : 'No'}</td>
      <td><button class="info" onclick="changePassword('${escapeHtml(u.username)}')">Change</button></td>
      <td><button class="danger" onclick="deleteUser('${escapeHtml(u.username)}')">Delete</button></td>`;
    body.appendChild(tr);
  }
}

async function addUser() {
  const username = document.getElementById('newUsername').value.trim();
  const password = document.getElementById('newPassword').value.trim();
  const role = document.getElementById('newUserRole').value;

  if (!username || !password) {
    alert('Enter username and password.');
    return;
  }

  const res = await fetch('/data/admin/user', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({username, password, role})
  });

  const data = await res.json();

  if (!res.ok) {
    alert(data.error || 'Failed to add user.');
    return;
  }

  document.getElementById('newUsername').value = '';
  document.getElementById('newPassword').value = '';
  loadAdmin();
}

async function deleteUser(username) {
  if (!confirm('Delete user ' + username + '?')) return;

  const res = await fetch('/data/admin/user/delete', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({username})
  });

  const data = await res.json();

  if (!res.ok) {
    alert(data.error || 'Failed to delete user.');
    return;
  }

  loadAdmin();
}

async function changePassword(username) {
  const password = prompt('Enter new password for ' + username);
  if (!password) return;

  const res = await fetch('/data/admin/user/password', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({username, password})
  });

  const data = await res.json();

  if (!res.ok) {
    alert(data.error || 'Failed to change password.');
    return;
  }

  alert('Password updated.');
}

function renderQueues() {
  const body = document.getElementById('queueRows');
  body.innerHTML = '';

  if (adminData.queues.length === 0) {
    body.innerHTML = '<tr><td colspan="2" class="muted">No queues configured yet.</td></tr>';
    return;
  }

  for (const q of adminData.queues) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${escapeHtml(q)}</td>
      <td><button class="danger" onclick="deleteQueue('${escapeHtml(q)}')">Delete</button></td>`;
    body.appendChild(tr);
  }
}

function renderNewDepotQueue() {
  const sel = document.getElementById('newDepotQueue');
  sel.innerHTML = '';

  for (const q of adminData.queues) {
    const opt = document.createElement('option');
    opt.value = q;
    opt.textContent = q;
    sel.appendChild(opt);
  }
}

function profileQueueCheckboxes(containerId, selectedQueues) {
  const box = document.getElementById(containerId);
  box.innerHTML = '';

  if (adminData.queues.length === 0) {
    box.innerHTML = '<span class="muted">No queues configured yet.</span>';
    return;
  }

  for (const q of adminData.queues) {
    const id = safeId(containerId + '_' + q);
    const label = document.createElement('label');
    label.style.display = 'inline-block';
    label.style.margin = '4px 12px 4px 0';
    label.innerHTML = `<input id="${id}" type="checkbox" value="${escapeHtml(q)}" ${(selectedQueues || []).includes(q) ? 'checked' : ''}> ${escapeHtml(q)}`;
    box.appendChild(label);
  }
}

function getCheckedQueues(containerId) {
  return Array.from(document.querySelectorAll('#' + containerId + ' input:checked')).map(x => x.value);
}

function renderNewProfileQueues() {
  profileQueueCheckboxes('newProfileQueues', []);
}

function renderProfiles() {
  const body = document.getElementById('profileRows');
  body.innerHTML = '';

  if (!adminData.dispatcher_profiles || adminData.dispatcher_profiles.length === 0) {
    body.innerHTML = '<tr><td colspan="4" class="muted">No dispatcher profiles configured yet.</td></tr>';
    return;
  }

  for (const p of adminData.dispatcher_profiles) {
    const id = safeId(p.name);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${escapeHtml(p.name)}</td>
      <td><div id="profile_queues_${id}"></div></td>
      <td><button class="save" onclick="saveProfile('${escapeHtml(p.name)}', 'profile_queues_${id}')">Save</button></td>
      <td><button class="danger" onclick="deleteProfile('${escapeHtml(p.name)}')">Delete</button></td>`;
    body.appendChild(tr);
    profileQueueCheckboxes('profile_queues_' + id, p.queues || []);
  }
}

function queueOptions(selected) {
  return adminData.queues
    .map(q => `<option ${q === selected ? 'selected' : ''}>${escapeHtml(q)}</option>`)
    .join('');
}

function safeId(value) {
  return btoa(unescape(encodeURIComponent(value))).replaceAll('=', '');
}

function renderCustomFlows() {
  const box = document.getElementById('customFlowRows');
  box.innerHTML = '';

  if (!adminData.custom_flows || adminData.custom_flows.length === 0) {
    box.innerHTML = '<p class="muted">No custom flows configured yet.</p>';
    return;
  }

  const styles = ['request', 'primary', 'warning', 'danger', 'info', 'muted'];
  const stepTypes = ['text', 'number', 'message', 'choice', 'yes_no'];

  for (const flow of adminData.custom_flows) {
    const key = escapeHtml(flow.key);
    const id = safeId(key);
    const styleOptions = styles.map(s => `<option value="${s}" ${flow.button_class === s ? 'selected' : ''}>${s}</option>`).join('');
    const stepTypeOptions = stepTypes.map(t => `<option value="${t}">${t}</option>`).join('');
    const stepCount = (flow.steps || []).length;
    const hasFlowText = stepCount > 0 ? `${stepCount} step${stepCount === 1 ? '' : 's'}` : 'No steps';
    const activeText = flow.active ? 'Active' : 'Hidden';
    const pillClass = 'pill-' + (flow.button_class || 'request');

    let stepHtml = '';
    if (!flow.steps || flow.steps.length === 0) {
      stepHtml = '<div class="muted">No steps yet.</div>';
    } else {
      for (const step of flow.steps) {
        const choicesText = step.choices ? ' | choices: ' + escapeHtml(step.choices.join(', ')) : '';
        const showIfText = step.show_if ? ' | show if ' + escapeHtml(step.show_if.field) + ' = ' + escapeHtml(step.show_if.equals) : '';
        stepHtml += `<div class="stepLine">
          <b>${escapeHtml(step.label)}</b> <span class="muted">(${escapeHtml(step.type)})</span><br>
          <span class="muted">ID: ${escapeHtml(step.id)}${step.required ? ' | required' : ''}${choicesText}${showIfText}</span><br>
          <button class="danger mini" onclick="deleteFlowStep('${key}', '${escapeHtml(step.id)}')">Delete Step</button>
        </div>`;
      }
    }

    const div = document.createElement('div');
    div.className = 'flowCard';
    div.innerHTML = `
      <div class="flowSummary">
        <div><b>#${flow.display_order || 999}</b></div>
        <div><b>${escapeHtml(flow.label)}</b><br><span class="muted">${escapeHtml(flow.key)}</span></div>
        <div><span class="style-pill ${pillClass}">${escapeHtml(flow.button_class || 'request')}</span></div>
        <div>${activeText}</div>
        <div>${hasFlowText}</div>
        <div>
          <button class="primary mini" onclick="toggleFlowEditor('${id}')">Edit</button>
          <button class="danger mini" onclick="deleteCustomFlow('${key}')">Delete</button>
        </div>
      </div>
      <div id="flow_editor_${id}" class="flowEditor">
        <div class="grid">
          <div>
            <label>Order</label>
            <input id="flow_order_${id}" type="number" value="${flow.display_order || 999}">
          </div>
          <div>
            <label>Button Name</label>
            <input id="flow_label_${id}" value="${escapeHtml(flow.label)}">
          </div>
        </div>
        <div class="grid">
          <div>
            <label>Color / Style</label>
            <select id="flow_style_${id}">${styleOptions}</select>
          </div>
          <div>
            <label>Visible to drivers</label><br>
            <input id="flow_active_${id}" type="checkbox" ${flow.active ? 'checked' : ''}>
          </div>
        </div>
        <button class="save" onclick="saveCustomFlow('${key}', '${id}')">Save Flow</button>

        <h4>Flow Tree / Steps</h4>
        ${stepHtml}

        <h4>Add Step</h4>
        <div class="grid">
          <input id="step_label_${id}" placeholder="Step label, e.g. Supply Number">
          <select id="step_type_${id}">${stepTypeOptions}</select>
        </div>
        <div class="grid">
          <input id="step_choices_${id}" placeholder="Choices for choice step, comma separated">
          <label><input id="step_required_${id}" type="checkbox" checked> Required</label>
        </div>
        <div class="grid">
          <input id="step_show_field_${id}" placeholder="Show only if field ID equals... e.g. volume_band">
          <input id="step_show_value_${id}" placeholder="Equals value... e.g. 0-500L">
        </div>
        <button class="primary" onclick="addFlowStep('${key}', '${id}')">Add Step</button>
      </div>
    `;
    box.appendChild(div);
  }
}

function toggleFlowEditor(id) {
  const el = document.getElementById('flow_editor_' + id);
  if (!el) return;
  el.classList.toggle('open');
}

function makeFlowKey(label) {
  return 'CUSTOM_' + label.toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 40) + '_' + Date.now().toString().slice(-5);
}

function makeStepId(label) {
  return label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 40) || ('step_' + Date.now());
}

async function addCustomFlow() {
  const label = document.getElementById('newFlowLabel').value.trim();
  const button_class = document.getElementById('newFlowStyle').value;
  if (!label) return alert('Enter a flow label.');

  const res = await fetch('/data/admin/custom-flow', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({key: makeFlowKey(label), label, button_class, active: true, display_order: 500, steps: []})
  });

  const data = await res.json();
  if (!res.ok) {
    alert(data.error || 'Failed to create flow.');
    return;
  }

  document.getElementById('newFlowLabel').value = '';
  loadAdmin();
}

async function seedV2Defaults() {
  if (!confirm('Create V2 dynamic copies of the current built-in driver options? Existing V2 copies will not be duplicated.')) return;

  const res = await fetch('/data/admin/seed-v2-flows', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({})
  });

  const data = await res.json();
  if (!res.ok) {
    alert(data.error || 'Failed to create V2 flows.');
    return;
  }

  alert('Created ' + data.created + ' V2 flow(s).');
  loadAdmin();
}

async function saveCustomFlow(key, id) {
  const label = document.getElementById('flow_label_' + id).value.trim();
  const display_order = Number(document.getElementById('flow_order_' + id).value || 999);
  const button_class = document.getElementById('flow_style_' + id).value;
  const active = document.getElementById('flow_active_' + id).checked;
  if (!label) return alert('Flow label cannot be blank.');

  const res = await fetch('/data/admin/custom-flow', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({key, label, display_order, button_class, active})
  });

  const data = await res.json();
  if (!res.ok) {
    alert(data.error || 'Failed to save flow.');
    return;
  }

  loadAdmin();
}

async function deleteCustomFlow(key) {
  if (!confirm('Delete this custom flow?')) return;
  await fetch('/data/admin/custom-flow/delete', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({key})
  });
  loadAdmin();
}

async function addFlowStep(flowKey, id) {
  const label = document.getElementById('step_label_' + id).value.trim();
  const type = document.getElementById('step_type_' + id).value;
  const choicesRaw = document.getElementById('step_choices_' + id).value.trim();
  const required = document.getElementById('step_required_' + id).checked;
  const showField = document.getElementById('step_show_field_' + id).value.trim();
  const showValue = document.getElementById('step_show_value_' + id).value.trim();

  if (!label) return alert('Enter a step label.');

  const step = { id: makeStepId(label), label, type, required };
  if (type === 'choice') {
    step.choices = choicesRaw.split(',').map(x => x.trim()).filter(Boolean);
    if (step.choices.length === 0) return alert('Choice steps need at least one choice.');
  }

  if (showField && showValue) {
    step.show_if = { field: showField, equals: showValue };
  }

  await fetch('/data/admin/custom-flow/step', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({flow_key: flowKey, step})
  });
  loadAdmin();
}

async function deleteFlowStep(flowKey, stepId) {
  if (!confirm('Delete this step?')) return;
  await fetch('/data/admin/custom-flow/step/delete', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({flow_key: flowKey, step_id: stepId})
  });
  loadAdmin();
}

function renderDepots() {
  const body = document.getElementById('depotRows');
  body.innerHTML = '';

  if (adminData.depots.length === 0) {
    body.innerHTML = '<tr><td colspan="4" class="muted">No depots configured yet.</td></tr>';
    return;
  }

  for (const d of adminData.depots) {
    const id = safeId(d.name);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${escapeHtml(d.name)}</td>
      <td><select id="d_queue_${id}">${queueOptions(d.dispatch_queue)}</select></td>
      <td><button class="save" onclick="saveDepot('${escapeHtml(d.name)}', '${id}')">Save</button></td>
      <td><button class="danger" onclick="deleteDepot('${escapeHtml(d.name)}')">Delete</button></td>`;
    body.appendChild(tr);
  }
}

async function addQueue() {
  const name = document.getElementById('newQueueName').value.trim();
  if (!name) return alert('Enter a queue name.');

  const res = await fetch('/data/admin/queue', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name})
  });

  const text = await res.text();
  let data = {};
  try { data = JSON.parse(text); } catch {}

  if (!res.ok) {
    alert(data.error || text || 'Failed to add queue.');
    return;
  }

  document.getElementById('newQueueName').value = '';
  loadAdmin();
}

async function deleteQueue(name) {
  if (!confirm('Delete queue "' + name + '"?')) return;
  const res = await fetch('/data/admin/queue/delete', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name})
  });
  const data = await res.json();
  if (!res.ok) alert(data.error || 'Failed to delete queue.');
  loadAdmin();
}

async function addDepot() {
  const name = document.getElementById('newDepotName').value.trim();
  const dispatch_queue = document.getElementById('newDepotQueue').value;
  if (!name || !dispatch_queue) return alert('Enter depot and queue.');
  await fetch('/data/admin/depot', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name, dispatch_queue})
  });
  document.getElementById('newDepotName').value = '';
  loadAdmin();
}

async function saveDepot(name, id) {
  const dispatch_queue = document.getElementById('d_queue_' + id).value;
  await fetch('/data/admin/depot', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name, dispatch_queue})
  });
  loadAdmin();
}

async function deleteDepot(name) {
  if (!confirm('Delete depot "' + name + '"?')) return;
  await fetch('/data/admin/depot/delete', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name})
  });
  loadAdmin();
}

async function addProfile() {
  const name = document.getElementById('newProfileName').value.trim();
  const queues = getCheckedQueues('newProfileQueues');
  if (!name) return alert('Enter a profile name.');
  if (queues.length === 0) return alert('Select at least one queue.');

  await fetch('/data/admin/profile', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name, queues})
  });
  document.getElementById('newProfileName').value = '';
  loadAdmin();
}

async function saveProfile(name, containerId) {
  const queues = getCheckedQueues(containerId);
  if (queues.length === 0) return alert('Select at least one queue.');

  await fetch('/data/admin/profile', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name, queues})
  });
  loadAdmin();
}

async function deleteProfile(name) {
  if (!confirm('Delete profile "' + name + '"?')) return;

  await fetch('/data/admin/profile/delete', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name})
  });
  loadAdmin();
}

loadAdmin();
</script>
</body>
</html>
"""


def extract_supply_number(data):
    direct = str(data.get("supply_number", "")).strip()
    if direct:
        return direct

    payload_raw = str(data.get("payload_json", "")).strip()
    if not payload_raw:
        return ""

    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return ""

    possible_keys = [
        "supply_number",
        "supply_no",
        "supply",
        "supplier_number",
        "supplier_no",
        "supplier",
    ]

    for key in possible_keys:
        value = str(payload.get(key, "")).strip()
        if value:
            return value

    for key, value in payload.items():
        if "supply" in key.lower() or "supplier" in key.lower():
            value = str(value).strip()
            if value:
                return value

    return ""


DISPATCHER_HTML = """
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dispatcher Board</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 0; background: #f3f4f6; color: #111827; }
    .wrap { padding: 18px; }
    h1 { margin: 8px 0 16px; }
    .card { background: white; border-radius: 14px; padding: 14px; box-shadow: 0 2px 12px rgba(0,0,0,.08); margin-bottom: 14px; }
    table { width: 100%; border-collapse: collapse; background: white; box-shadow: 0 2px 12px rgba(0,0,0,.08); }
    th, td { padding: 6px 8px; border-bottom: 1px solid #e5e7eb; text-align: left; vertical-align: top; font-size: 13px; }
    th { font-size: 12px; }
    .compactTable { font-size: 13px; }
    .ageCell { font-weight: bold; white-space: nowrap; }
    .age-fresh { color: #166534; }
    .age-warm { color: #92400e; }
    .age-hot { color: #991b1b; }
    .row-call td { box-shadow: inset 4px 0 0 #dc2626; }
    .row-milk-high td { box-shadow: inset 4px 0 0 #ea580c; }
    .row-milk td { box-shadow: inset 4px 0 0 #f59e0b; }
    .row-cip td { box-shadow: inset 4px 0 0 #2563eb; }
    .row-split td { box-shadow: inset 4px 0 0 #16a34a; }
    .row-message td { box-shadow: inset 4px 0 0 #6b7280; }
    th { background: #111827; color: white; position: sticky; top: 0; }
    th.sortable { cursor: pointer; user-select: none; }
    th.sortable:hover { background: #374151; }
    .sortArrow { color: #fbbf24; font-size: 10px; margin-left: 4px; }
    tr:hover td { filter: brightness(0.98); }
    .status-new { background: #fee2e2; font-weight: bold; }
    .status-ack { background: #fef3c7; }
    .status-done { background: #dcfce7; color: #166534; }
    .has-notes { border-left: 6px solid #7c3aed; }
    button { padding: 8px 10px; border: 0; border-radius: 8px; margin: 2px; cursor: pointer; font-weight: bold; }
    .miniBtn { padding: 3px 7px; border-radius: 6px; font-size: 11px; min-width: 24px; }
    .actionsCell { white-space: nowrap; }
    .ack { background: #f59e0b; color: #111827; }
    .done { background: #16a34a; color: white; }
    .new { background: #dc2626; color: white; }
    .primary { background: #111827; color: white; }
    .mutedBtn { background: #e5e7eb; color: #111827; }
    .muted { color: #6b7280; font-size: 13px; }
    .activeView { outline: 3px solid #2563eb; }
    .queueBox { display:inline-block; margin: 4px 12px 4px 0; white-space: nowrap; }
    .queueBox input { transform: scale(1.2); margin-right: 6px; }
    .queue-pill { display:inline-block; background:#dbeafe; color:#1e3a8a; padding: 3px 7px; border-radius: 999px; font-weight:bold; }
    .notes-pill { display:inline-block; background:#ede9fe; color:#5b21b6; padding: 3px 7px; border-radius: 999px; font-weight:bold; margin-top:4px; }
    .modalBackdrop { display:none; position:fixed; inset:0; background:rgba(0,0,0,.45); align-items:center; justify-content:center; z-index:1000; }
    .modal { width:min(620px, calc(100vw - 32px)); background:white; border-radius:14px; padding:18px; box-shadow:0 20px 60px rgba(0,0,0,.35); }
    .modal textarea { width:100%; min-height:160px; box-sizing:border-box; padding:12px; border:1px solid #d1d5db; border-radius:10px; font-size:16px; resize:vertical; }
    .modalTitle { font-size:20px; font-weight:bold; margin-bottom:8px; }
    .contextMenu { display:none; position:fixed; background:white; border:1px solid #d1d5db; border-radius:10px; box-shadow:0 10px 30px rgba(0,0,0,.25); z-index:1001; overflow:hidden; min-width:220px; }
    .contextMenu .title { padding:10px 12px; font-weight:bold; background:#f3f4f6; border-bottom:1px solid #e5e7eb; }
    .contextMenu button { display:block; width:100%; border-radius:0; margin:0; background:white; color:#111827; text-align:left; padding:10px 12px; }
    .contextMenu button:hover { background:#e5e7eb; }
    #supplySearch { width: 260px; max-width: 100%; padding: 8px; border: 1px solid #d1d5db; border-radius: 8px; }
  </style>
</head>
<body>
<div class="wrap">
  <h1>Dispatcher Board</h1>
  <div id="dispatcherError" class="card" style="display:none; border-left:6px solid #dc2626; color:#991b1b;"></div>
  <p class="muted"><a href="/admin">Dispatcher Admin</a></p>

  <div class="card">
    <b>Dispatcher Desk</b><br>
    <select id="profileSelect"></select>
    <button class="primary" onclick="loadSelectedProfile()">Load Desk</button>
    <button class="mutedBtn" onclick="saveCurrentAsProfile()">Save Current Selection as Desk</button>
    <div class="muted" id="profileNote" style="margin-top:8px;">Select a desk profile to load its default queues, or manually tick queues below.</div>
  </div>

  <div class="card">
    <b>Queues to monitor</b><br>
    <div id="queueChoices"></div>
    <button class="primary" onclick="selectAllQueues()">Select All</button>
    <button class="mutedBtn" onclick="clearQueues()">Clear</button>
    <div class="muted" style="margin-top:8px;">Selections are saved in this browser. Double-click a row for notes. Right-click a row to reassign queue.</div>
  </div>

  <div class="card">
    <b>Supply Search</b><br>
    <input id="supplySearch" placeholder="Search supply number..." oninput="supplySearchChanged()">
    <button class="mutedBtn" onclick="clearSupplySearch()">Clear Search</button>
    <div class="muted" style="margin-top:8px;">Search includes open and done requests for the selected queues.</div>
  </div>

  <div class="card">
    <button id="separateBtn" class="primary activeView" onclick="setView('separate')">Separate</button>
    <button id="combinedBtn" class="mutedBtn" onclick="setView('combined')">Combined</button>
    <div class="muted" id="viewNote" style="margin-top:8px;">Separate hides Done items. Combined shows all items.</div>
  </div>

  <table class="compactTable">
    <thead>
      <tr>
        <th class="sortable" onclick="setSort('age')">Age <span id="sort_age" class="sortArrow"></span></th>
        <th class="sortable" onclick="setSort('created_at')">Time <span id="sort_created_at" class="sortArrow"></span></th>
        <th class="sortable" onclick="setSort('dispatch_queue')">Queue <span id="sort_dispatch_queue" class="sortArrow"></span></th>
        <th class="sortable" onclick="setSort('depot')">Depot <span id="sort_depot" class="sortArrow"></span></th>
        <th class="sortable" onclick="setSort('truck_number')">Truck <span id="sort_truck_number" class="sortArrow"></span></th>
        <th class="sortable" onclick="setSort('driver_name')">Driver <span id="sort_driver_name" class="sortArrow"></span></th>
        <th class="sortable" onclick="setSort('supply_search')">Supply No <span id="sort_supply_search" class="sortArrow"></span></th>
        <th class="sortable" onclick="setSort('request_type')">Request <span id="sort_request_type" class="sortArrow"></span></th>
        <th>Details</th>
        <th class="sortable" onclick="setSort('dispatcher_notes')">Notes <span id="sort_dispatcher_notes" class="sortArrow"></span></th>
        <th class="sortable" onclick="setSort('status')">Status <span id="sort_status" class="sortArrow"></span></th>
        <th>Action</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
</div>

<div id="notesModal" class="modalBackdrop" onclick="modalBackdropClicked(event)">
  <div class="modal">
    <div class="modalTitle">Dispatcher Notes</div>
    <div class="muted" id="notesModalSubtitle"></div>
    <br>
    <textarea id="notesText" maxlength="1000" placeholder="Add notes for this request..."></textarea>
    <div class="muted"><span id="notesCount">0</span>/1000</div>
    <br>
    <button class="primary" onclick="saveNotes()">Save Notes</button>
    <button class="mutedBtn" onclick="closeNotesModal()">Cancel</button>
  </div>
</div>

<div id="reassignMenu" class="contextMenu"></div>

<script>
const DISPATCH_QUEUES = {{ dispatch_queues|tojson }};
const DISPATCHER_PROFILES = {{ dispatcher_profiles|tojson }};
const INITIAL_REQUESTS = {{ initial_requests|tojson }};
let currentView = localStorage.getItem('dispatcher_view') || 'separate';
let currentRows = [];
let activeNotesRequestId = null;
let sortColumn = localStorage.getItem('dispatcher_sort_column') || 'created_at';
let sortDirection = localStorage.getItem('dispatcher_sort_direction') || 'desc';
let supplySearch = localStorage.getItem('dispatcher_supply_search') || '';

function escapeHtml(value) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function getSelectedQueues() {
  const saved = localStorage.getItem('selected_queues');
  if (!saved) return [];
  try { return JSON.parse(saved); } catch { return []; }
}

function saveSelectedQueues(queues) {
  localStorage.setItem('selected_queues', JSON.stringify(queues));
}

function renderProfileChoices() {
  const sel = document.getElementById('profileSelect');
  const currentProfile = localStorage.getItem('dispatcher_profile') || '';
  sel.innerHTML = '<option value="">Select desk...</option>';

  for (const p of DISPATCHER_PROFILES) {
    const opt = document.createElement('option');
    opt.value = p.name;
    opt.textContent = p.name;
    if (p.name === currentProfile) opt.selected = true;
    sel.appendChild(opt);
  }
}

function loadSelectedProfile() {
  const name = document.getElementById('profileSelect').value;
  const profile = DISPATCHER_PROFILES.find(p => p.name === name);

  if (!profile) {
    alert('Select a dispatcher desk first.');
    return;
  }

  saveSelectedQueues(profile.queues || []);
  localStorage.setItem('dispatcher_profile', name);
  document.getElementById('profileNote').innerText = 'Loaded desk: ' + name;
  renderQueueChoices();
  loadRequests();
}

async function saveCurrentAsProfile() {
  const name = prompt('Desk/profile name:');
  if (!name) return;

  const queues = getSelectedQueues();
  if (queues.length === 0) {
    alert('Select at least one queue first.');
    return;
  }

  await fetch('/data/admin/profile', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name: name.trim(), queues})
  });

  alert('Saved. Refreshing board to load updated profile list.');
  location.reload();
}

function renderQueueChoices() {
  const selected = getSelectedQueues();
  const box = document.getElementById('queueChoices');
  box.innerHTML = '';

  if (DISPATCH_QUEUES.length === 0) {
    box.innerHTML = '<span class="muted">No queues configured. Use Dispatcher Admin first.</span>';
    return;
  }

  for (const q of DISPATCH_QUEUES) {
    const label = document.createElement('label');
    label.className = 'queueBox';
    label.innerHTML = `<input type="checkbox" value="${escapeHtml(q)}" ${selected.includes(q) ? 'checked' : ''} onchange="queueChanged()">${escapeHtml(q)}`;
    box.appendChild(label);
  }
}

function queueChanged() {
  const queues = Array.from(document.querySelectorAll('#queueChoices input:checked')).map(x => x.value);
  saveSelectedQueues(queues);
  loadRequests();
}

function selectAllQueues() {
  saveSelectedQueues(DISPATCH_QUEUES);
  renderQueueChoices();
  loadRequests();
}

function clearQueues() {
  saveSelectedQueues([]);
  renderQueueChoices();
  loadRequests();
}

function setView(view) {
  currentView = view;
  localStorage.setItem('dispatcher_view', view);
  document.getElementById('separateBtn').classList.toggle('activeView', view === 'separate');
  document.getElementById('combinedBtn').classList.toggle('activeView', view === 'combined');
  document.getElementById('viewNote').innerText = view === 'separate'
    ? 'Separate hides Done items from the main board.'
    : 'Combined shows New, Acknowledged, and Done items. Done items can be restored.';
  loadRequests();
}

function supplySearchChanged() {
  supplySearch = document.getElementById('supplySearch').value.trim();
  localStorage.setItem('dispatcher_supply_search', supplySearch);
  loadRequests();
}

function clearSupplySearch() {
  supplySearch = '';
  localStorage.removeItem('dispatcher_supply_search');
  document.getElementById('supplySearch').value = '';
  loadRequests();
}

function detailText(r) {
  let parts = [];
  if (r.supply_number) parts.push('Supply: ' + escapeHtml(r.supply_number));
  if (r.volume) parts.push('Volume: ' + escapeHtml(r.volume) + ' L');
  if (r.volume_band) parts.push('Volume Band: ' + escapeHtml(r.volume_band));
  if (r.milk_still_stirred && r.milk_still_stirred !== 'Not required') parts.push('Still Stirred: ' + escapeHtml(r.milk_still_stirred));
  if (r.cip_action) parts.push(escapeHtml(r.cip_action));
  if (r.message) parts.push('Message: ' + escapeHtml(r.message));
  return parts.join('<br>');
}

function notesText(r) {
  if (!r.dispatcher_notes) return '<span class="muted">Double-click to add</span>';
  const clean = escapeHtml(r.dispatcher_notes);
  const short = clean.length > 80 ? clean.slice(0, 80) + '...' : clean;
  return `<span class="notes-pill">Notes</span><br>${short}`;
}

function queueText(r) {
  let txt = `<span class="queue-pill">${escapeHtml(r.dispatch_queue)}</span>`;
  if (r.original_dispatch_queue && r.original_dispatch_queue !== r.dispatch_queue) {
    txt += `<br><span class="muted">from ${escapeHtml(r.original_dispatch_queue)}</span>`;
  }
  return txt;
}

function statusClass(status) {
  if (status === 'New') return 'status-new';
  if (status === 'Acknowledged') return 'status-ack';
  if (status === 'Done') return 'status-done';
  return '';
}

function actionButtons(r) {
  if (r.status === 'Done') {
    return `<button title="Restore New" class="miniBtn new" onclick="setStatus(${r.id}, 'New')">N</button><button title="Restore Acknowledged" class="miniBtn ack" onclick="setStatus(${r.id}, 'Acknowledged')">A</button>`;
  }
  return `<button title="New" class="miniBtn new" onclick="setStatus(${r.id}, 'New')">N</button><button title="Acknowledged" class="miniBtn ack" onclick="setStatus(${r.id}, 'Acknowledged')">A</button><button title="Done" class="miniBtn done" onclick="setStatus(${r.id}, 'Done')">D</button>`;
}

function parseCreatedAt(value) {
  // Stored as YYYY-MM-DD HH:MM:SS local time.
  if (!value) return null;
  return new Date(value.replace(' ', 'T'));
}

function ageInfo(createdAt) {
  const dt = parseCreatedAt(createdAt);
  if (!dt || isNaN(dt.getTime())) return { text: '-', cls: '' };

  const diffMs = Date.now() - dt.getTime();
  const mins = Math.max(0, Math.floor(diffMs / 60000));

  let text = 'Now';
  if (mins >= 60) {
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    text = m ? `${h}h ${m}m` : `${h}h`;
  } else if (mins >= 1) {
    text = `${mins}m`;
  }

  let cls = 'age-fresh';
  if (mins >= 15) cls = 'age-warm';
  if (mins >= 30) cls = 'age-hot';

  return { text, cls };
}

function requestRowClass(r) {
  if (r.request_type === 'Call me please') return 'row-call';
  if (r.request_type === 'Milk Left Behind' && r.volume_band === '1200L+') return 'row-milk-high';
  if (r.request_type === 'Milk Left Behind') return 'row-milk';
  if (r.request_type === 'CIP') return 'row-cip';
  if (r.request_type === 'Split has been cleared') return 'row-split';
  if (r.request_type === 'Driver Message') return 'row-message';
  return '';
}

function sortValue(r, column) {
  if (column === 'age' || column === 'created_at') {
    const dt = parseCreatedAt(r.created_at);
    return dt ? dt.getTime() : 0;
  }
  if (column === 'dispatcher_notes') {
    return r.dispatcher_notes ? r.dispatcher_notes.toLowerCase() : '';
  }
  if (column === 'supply_search') {
    return String(r.supply_search || r.supply_number || '').toLowerCase();
  }
  return String(r[column] || '').toLowerCase();
}

function sortRows(rows) {
  return [...rows].sort((a, b) => {
    const av = sortValue(a, sortColumn);
    const bv = sortValue(b, sortColumn);

    if (av < bv) return sortDirection === 'asc' ? -1 : 1;
    if (av > bv) return sortDirection === 'asc' ? 1 : -1;
    return 0;
  });
}

function setSort(column) {
  if (sortColumn === column) {
    sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
  } else {
    sortColumn = column;
    sortDirection = column === 'age' || column === 'created_at' ? 'desc' : 'asc';
  }

  localStorage.setItem('dispatcher_sort_column', sortColumn);
  localStorage.setItem('dispatcher_sort_direction', sortDirection);
  renderSortIndicators();
  renderRows(currentRows);
}

function renderSortIndicators() {
  ['age', 'created_at', 'dispatch_queue', 'depot', 'truck_number', 'driver_name', 'supply_search', 'request_type', 'dispatcher_notes', 'status'].forEach(col => {
    const el = document.getElementById('sort_' + col);
    if (!el) return;
    el.innerText = col === sortColumn ? (sortDirection === 'asc' ? '▲' : '▼') : '';
  });
}

function renderRows(data) {
  const tbody = document.getElementById('rows');
  tbody.innerHTML = '';

  if (data.length === 0) {
    tbody.innerHTML = '<tr><td colspan="12" class="muted">No requests found for selected queues.</td></tr>';
    return;
  }

  for (const r of sortRows(data)) {
    const tr = document.createElement('tr');
    const age = ageInfo(r.created_at);
    tr.className = [statusClass(r.status), requestRowClass(r), r.dispatcher_notes ? 'has-notes' : ''].filter(Boolean).join(' ');
    tr.ondblclick = () => openNotesModal(r.id);
    tr.oncontextmenu = (event) => openReassignMenu(event, r.id);
    tr.innerHTML = `
      <td class="ageCell ${age.cls}">${age.text}</td>
      <td>${escapeHtml(r.created_at)}</td>
      <td>${queueText(r)}</td>
      <td>${escapeHtml(r.depot)}</td>
      <td>${escapeHtml(r.truck_number)}</td>
      <td>${escapeHtml(r.driver_name)}</td>
      <td><b>${escapeHtml(r.supply_search || r.supply_number || '')}</b></td>
      <td><b>${escapeHtml(r.request_type)}</b></td>
      <td>${detailText(r)}</td>
      <td>${notesText(r)}</td>
      <td>${escapeHtml(r.status)}</td>
      <td class="actionsCell">${actionButtons(r)}</td>`;
    tbody.appendChild(tr);
  }
}

function showDispatcherError(message) {
  const box = document.getElementById('dispatcherError');
  box.style.display = 'block';
  box.innerText = message;
}

function clearDispatcherError() {
  const box = document.getElementById('dispatcherError');
  box.style.display = 'none';
  box.innerText = '';
}

function filterRowsLocally(rows, queues) {
  let filtered = rows.filter(r => queues.includes(r.dispatch_queue));

  if (currentView !== 'combined') {
    filtered = filtered.filter(r => r.status !== 'Done');
  }

  if (supplySearch.trim()) {
    const s = supplySearch.trim().toLowerCase();
    filtered = filtered.filter(r =>
      String(r.supply_search || '').toLowerCase().includes(s) ||
      String(r.supply_number || '').toLowerCase().includes(s) ||
      String(r.payload_json || '').toLowerCase().includes(s)
    );
  }

  return filtered;
}

async function loadRequests() {
  const queues = getSelectedQueues();
  const tbody = document.getElementById('rows');

  if (queues.length === 0) {
    tbody.innerHTML = '<tr><td colspan="12" class="muted">Select at least one queue to monitor.</td></tr>'; 
    return;
  }

  const params = new URLSearchParams();
  params.set('view', currentView);
  params.set('queues', queues.join(','));
  if (supplySearch.trim()) params.set('supply_search', supplySearch.trim());

  try {
    const res = await fetch('/data/requests?' + params.toString());
    const text = await res.text();

    if (!res.ok) {
      throw new Error('HTTP ' + res.status + ' - ' + text.slice(0, 250));
    }

    const data = JSON.parse(text);
    currentRows = data;
    clearDispatcherError();
    renderSortIndicators();
    renderRows(data);
  } catch (err) {
    // Corporate browsers/networks can block fetch/XHR. Fall back to rows embedded in the page.
    currentRows = filterRowsLocally(INITIAL_REQUESTS, queues);
    showDispatcherError('Live refresh failed. Showing page-load snapshot only: ' + err);
    renderSortIndicators();
    renderRows(currentRows);
  }
}

async function setStatus(id, status) {
  await fetch('/data/status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, status })
  });
  loadRequests();
}

function getRowById(id) {
  return currentRows.find(r => Number(r.id) === Number(id));
}

function openNotesModal(id) {
  const r = getRowById(id);
  if (!r) return;
  activeNotesRequestId = id;
  document.getElementById('notesModalSubtitle').innerText = `${r.dispatch_queue} | ${r.depot} | Truck ${r.truck_number} | ${r.request_type}`;
  document.getElementById('notesText').value = r.dispatcher_notes || '';
  document.getElementById('notesCount').innerText = document.getElementById('notesText').value.length;
  document.getElementById('notesModal').style.display = 'flex';
  document.getElementById('notesText').focus();
}

function closeNotesModal() {
  activeNotesRequestId = null;
  document.getElementById('notesModal').style.display = 'none';
}

function modalBackdropClicked(event) {
  if (event.target.id === 'notesModal') closeNotesModal();
}

async function saveNotes() {
  if (!activeNotesRequestId) return;
  const notes = document.getElementById('notesText').value.trim();
  await fetch('/data/notes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: activeNotesRequestId, notes })
  });
  closeNotesModal();
  loadRequests();
}

function openReassignMenu(event, id) {
  event.preventDefault();
  const r = getRowById(id);
  if (!r) return;

  const menu = document.getElementById('reassignMenu');
  menu.innerHTML = `<div class="title">Reassign to queue</div>`;

  for (const q of DISPATCH_QUEUES) {
    if (q === r.dispatch_queue) continue;
    const btn = document.createElement('button');
    btn.innerText = q;
    btn.onclick = () => reassignQueue(id, q);
    menu.appendChild(btn);
  }

  menu.style.left = event.clientX + 'px';
  menu.style.top = event.clientY + 'px';
  menu.style.display = 'block';
}

function closeReassignMenu() {
  document.getElementById('reassignMenu').style.display = 'none';
}

async function reassignQueue(id, queue) {
  await fetch('/data/reassign', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, dispatch_queue: queue })
  });
  closeReassignMenu();

  const selected = getSelectedQueues();
  if (!selected.includes(queue)) {
    selected.push(queue);
    saveSelectedQueues(selected);
    renderQueueChoices();
  }
  loadRequests();
}

document.addEventListener('click', closeReassignMenu);
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    closeNotesModal();
    closeReassignMenu();
  }
});
document.getElementById('notesText').addEventListener('input', () => {
  document.getElementById('notesCount').innerText = document.getElementById('notesText').value.length;
});

document.getElementById('supplySearch').value = supplySearch;
renderProfileChoices();
renderQueueChoices();
renderSortIndicators();
setView(currentView);
setInterval(loadRequests, 5000);
</script>
</body>
</html>
"""


def require_role(*roles):
    if not session.get("username"):
        return False
    return session.get("role") in roles


@app.route("/")
def home():
    if not session.get("username"):
        return redirect(url_for("login"))

    role = session.get("role")

    if role == "driver":
        return redirect(url_for("driver"))
    if role == "dispatcher":
        return redirect(url_for("dispatcher"))
    if role == "admin":
        return redirect(url_for("admin"))

    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""

    if request.method == "POST":
        username = str(request.form.get("username", "")).strip()
        password = str(request.form.get("password", ""))
        role = str(request.form.get("role", "driver")).strip()

        with db_connect() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username = ? AND role = ? AND active = 1",
                (username, role)
            ).fetchone()

        if not user or not check_password_hash(user["password_hash"], password):
            error = "Invalid username/password"
        else:
            session["username"] = user["username"]
            session["role"] = user["role"]

            if role == "driver":
                return redirect(url_for("driver"))
            if role == "dispatcher":
                return redirect(url_for("dispatcher"))
            return redirect(url_for("admin"))

    return render_template_string(LOGIN_HTML, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/driver")
def driver():
    if not require_role("driver", "admin"):
        return redirect(url_for("login"))

    return render_template_string(
        DRIVER_HTML,
        app_title=APP_TITLE,
        depots=get_depots(),
        depot_queue_map=get_depot_queue_map(),
custom_flows=get_custom_flows(active_only=True),
    )


def get_initial_requests(limit=200):
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM requests ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


@app.route("/dispatcher")
def dispatcher():
    if not require_role("dispatcher", "admin"):
        return redirect(url_for("login"))

    return render_template_string(
        DISPATCHER_HTML,
        dispatch_queues=get_dispatch_queues(),
        dispatcher_profiles=get_dispatcher_profiles(),
        initial_requests=get_initial_requests(),
    )


def get_admin_data():
    config = load_config()
    with db_connect() as conn:
        users = [dict(r) for r in conn.execute("SELECT id, username, role, active FROM users ORDER BY username").fetchall()]

    return {
        "users": users,
        "queues": sorted(config.get("queues", [])),
        "depots": get_depots(),
        "dispatcher_profiles": get_dispatcher_profiles(),
        "custom_flows": get_custom_flows(active_only=False),
    }


@app.route("/admin")
def admin():
    if not require_role("admin"):
        return redirect(url_for("login"))

    return render_template_string(ADMIN_HTML, admin_initial_data=get_admin_data())


@app.route("/submit", methods=["POST"])
def submit():
    data = request.get_json(force=True)

    required = ["depot", "truck_number", "driver_name", "request_type"]
    if any(not str(data.get(field, "")).strip() for field in required):
        return jsonify({"error": "Missing required fields"}), 400

    depot = str(data.get("depot", "")).strip()
    dispatch_queue = get_dispatch_queue(depot)

    message = str(data.get("message", "")).strip()
    if len(message) > 200:
        return jsonify({"error": "Message too long"}), 400

    supply_search = extract_supply_number(data)

    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO requests
            (created_at, depot, dispatch_queue, original_dispatch_queue, truck_number, driver_name, request_type, supply_number, volume, cip_action, message, volume_band, milk_still_stirred, dispatcher_notes, payload_json, supply_search, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, 'New')
            """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                depot,
                dispatch_queue,
                dispatch_queue,
                str(data.get("truck_number", "")).strip(),
                str(data.get("driver_name", "")).strip(),
                str(data.get("request_type", "")).strip(),
                str(data.get("supply_number", "")).strip(),
                str(data.get("volume", "")).strip(),
                str(data.get("cip_action", "")).strip(),
                message,
                str(data.get("volume_band", "")).strip(),
                str(data.get("milk_still_stirred", "")).strip(),
                str(data.get("payload_json", "")).strip(),
                supply_search,
            ),
        )
        conn.commit()

    return jsonify({"ok": True})


@app.route("/api/requests")
@app.route("/data/requests")
def api_requests():
    view = request.args.get("view", "separate")
    queues_raw = request.args.get("queues", "")
    supply_search = request.args.get("supply_search", "").strip()
    queues = [q.strip() for q in queues_raw.split(",") if q.strip()]

    if not queues:
        return jsonify([])

    placeholders = ",".join("?" for _ in queues)
    status_clause = "1 = 1" if view == "combined" else "status != 'Done'"

    sql = f"""
        SELECT * FROM requests
        WHERE {status_clause}
        AND dispatch_queue IN ({placeholders})
    """

    params = list(queues)

    if supply_search:
        sql += " AND (supply_search LIKE ? OR supply_number LIKE ? OR payload_json LIKE ?)"
        like_value = f"%{supply_search}%"
        params.extend([like_value, like_value, like_value])

    sql += " ORDER BY id DESC LIMIT 200"

    with db_connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    return jsonify([dict(row) for row in rows])


@app.route("/api/status", methods=["POST"])
@app.route("/data/status", methods=["POST"])
def api_status():
    data = request.get_json(force=True)
    req_id = data.get("id")
    status = data.get("status")

    if status not in ["New", "Acknowledged", "Done"]:
        return jsonify({"error": "Invalid status"}), 400

    with db_connect() as conn:
        conn.execute("UPDATE requests SET status = ? WHERE id = ?", (status, req_id))
        conn.commit()

    return jsonify({"ok": True})


@app.route("/api/notes", methods=["POST"])
@app.route("/data/notes", methods=["POST"])
def api_notes():
    data = request.get_json(force=True)
    req_id = data.get("id")
    notes = str(data.get("notes", "")).strip()

    if len(notes) > 1000:
        return jsonify({"error": "Notes too long"}), 400

    with db_connect() as conn:
        conn.execute("UPDATE requests SET dispatcher_notes = ? WHERE id = ?", (notes, req_id))
        conn.commit()

    return jsonify({"ok": True})


@app.route("/api/reassign", methods=["POST"])
@app.route("/data/reassign", methods=["POST"])
def api_reassign():
    data = request.get_json(force=True)
    req_id = data.get("id")
    dispatch_queue = str(data.get("dispatch_queue", "")).strip()

    if dispatch_queue not in get_dispatch_queues():
        return jsonify({"error": "Invalid queue"}), 400

    with db_connect() as conn:
        conn.execute(
            """
            UPDATE requests
            SET dispatch_queue = ?, reassigned_at = ?
            WHERE id = ?
            """,
            (dispatch_queue, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), req_id),
        )
        conn.commit()

    return jsonify({"ok": True})


@app.route("/api/admin/config")
@app.route("/data/admin/config")
def api_admin_config():
    if not require_role("admin"):
        return jsonify({"error": "Not authorised"}), 403

    try:
        data = get_admin_data()
        data["debug"] = {
            "base_dir": str(BASE_DIR),
            "config_path": str(CONFIG_PATH),
            "config_exists": CONFIG_PATH.exists(),
            "db_path": str(DB_PATH),
            "db_exists": DB_PATH.exists(),
        }
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "config_path": str(CONFIG_PATH), "db_path": str(DB_PATH)}), 500


@app.route("/api/admin/user", methods=["POST"])
@app.route("/data/admin/user", methods=["POST"])
def api_admin_user():
    data = request.get_json(force=True)

    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    role = str(data.get("role", "driver")).strip()

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    try:
        with db_connect() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, active) VALUES (?, ?, ?, 1)",
                (username, generate_password_hash(password), role)
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "Username already exists"}), 400

    return jsonify({"ok": True})


@app.route("/api/admin/user/password", methods=["POST"])
@app.route("/data/admin/user/password", methods=["POST"])
def api_admin_user_password():
    data = request.get_json(force=True)

    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    with db_connect() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        if not existing:
            return jsonify({"error": "User not found"}), 404

        conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (generate_password_hash(password), username)
        )
        conn.commit()

    return jsonify({"ok": True})


@app.route("/api/admin/user/delete", methods=["POST"])
@app.route("/data/admin/user/delete", methods=["POST"])
def api_admin_user_delete():
    data = request.get_json(force=True)
    username = str(data.get("username", "")).strip()

    if username == "admin":
        return jsonify({"error": "Cannot delete default admin"}), 400

    with db_connect() as conn:
        conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()

    return jsonify({"ok": True})


@app.route("/api/admin/queue", methods=["POST"])
@app.route("/data/admin/queue", methods=["POST"])
def api_admin_queue():
    data = request.get_json(force=True)
    name = str(data.get("name", "")).strip()

    if not name:
        return jsonify({"error": "Queue name required"}), 400

    config = load_config()
    if name not in config["queues"]:
        config["queues"].append(name)
    save_config(config)

    return jsonify({"ok": True})


@app.route("/api/admin/queue/delete", methods=["POST"])
@app.route("/data/admin/queue/delete", methods=["POST"])
def api_admin_queue_delete():
    data = request.get_json(force=True)
    name = str(data.get("name", "")).strip()

    config = load_config()

    if name in config.get("depots", {}).values():
        return jsonify({"error": "Cannot delete queue while depots still route to it."}), 400

    config["queues"] = [q for q in config.get("queues", []) if q != name]
    save_config(config)

    return jsonify({"ok": True})


@app.route("/api/admin/depot", methods=["POST"])
@app.route("/data/admin/depot", methods=["POST"])
def api_admin_depot():
    data = request.get_json(force=True)
    name = str(data.get("name", "")).strip()
    dispatch_queue = str(data.get("dispatch_queue", "")).strip()

    if not name or not dispatch_queue:
        return jsonify({"error": "Depot and queue required"}), 400

    config = load_config()
    if dispatch_queue not in config.get("queues", []):
        return jsonify({"error": "Invalid queue"}), 400

    config["depots"][name] = dispatch_queue
    save_config(config)

    return jsonify({"ok": True})


@app.route("/api/admin/depot/delete", methods=["POST"])
@app.route("/data/admin/depot/delete", methods=["POST"])
def api_admin_depot_delete():
    data = request.get_json(force=True)
    name = str(data.get("name", "")).strip()

    config = load_config()
    if name in config.get("depots", {}):
        del config["depots"][name]
    save_config(config)

    return jsonify({"ok": True})


@app.route("/api/admin/profile", methods=["POST"])
@app.route("/data/admin/profile", methods=["POST"])
def api_admin_profile():
    data = request.get_json(force=True)
    name = str(data.get("name", "")).strip()
    queues = data.get("queues", [])

    if not name:
        return jsonify({"error": "Profile name required"}), 400
    if not isinstance(queues, list) or not queues:
        return jsonify({"error": "At least one queue required"}), 400

    config = load_config()
    valid_queues = set(config.get("queues", []))
    cleaned_queues = [str(q).strip() for q in queues if str(q).strip() in valid_queues]

    if not cleaned_queues:
        return jsonify({"error": "No valid queues supplied"}), 400

    config["dispatcher_profiles"][name] = sorted(set(cleaned_queues))
    save_config(config)

    return jsonify({"ok": True})


@app.route("/api/admin/profile/delete", methods=["POST"])
@app.route("/data/admin/profile/delete", methods=["POST"])
def api_admin_profile_delete():
    data = request.get_json(force=True)
    name = str(data.get("name", "")).strip()

    config = load_config()
    if name in config.get("dispatcher_profiles", {}):
        del config["dispatcher_profiles"][name]
    save_config(config)

    return jsonify({"ok": True})


@app.route("/api/admin/custom-flow", methods=["POST"])
@app.route("/data/admin/custom-flow", methods=["POST"])
def api_admin_custom_flow():
    data = request.get_json(force=True)
    key = str(data.get("key", "")).strip()
    label = str(data.get("label", "")).strip()
    button_class = str(data.get("button_class", "request")).strip()
    active = bool(data.get("active", True))

    try:
        display_order = int(data.get("display_order", 999))
    except ValueError:
        display_order = 999

    if not key or not label:
        return jsonify({"error": "Key and label required"}), 400
    if button_class not in {"request", "primary", "warning", "danger", "info", "muted"}:
        return jsonify({"error": "Invalid button style"}), 400

    config = load_config()
    flows = config.get("custom_flows", [])
    existing = next((f for f in flows if f.get("key") == key), None)

    if existing:
        existing["label"] = label
        existing["button_class"] = button_class
        existing["active"] = active
        existing["display_order"] = display_order
        if "steps" in data:
            existing["steps"] = data.get("steps", [])
    else:
        flows.append({
            "key": key,
            "label": label,
            "button_class": button_class,
            "active": active,
            "display_order": display_order,
            "steps": data.get("steps", []),
        })

    config["custom_flows"] = flows
    save_config(config)
    return jsonify({"ok": True})


@app.route("/api/admin/custom-flow/delete", methods=["POST"])
@app.route("/data/admin/custom-flow/delete", methods=["POST"])
def api_admin_custom_flow_delete():
    data = request.get_json(force=True)
    key = str(data.get("key", "")).strip()
    config = load_config()
    config["custom_flows"] = [f for f in config.get("custom_flows", []) if f.get("key") != key]
    save_config(config)
    return jsonify({"ok": True})


@app.route("/api/admin/seed-v2-flows", methods=["POST"])
@app.route("/data/admin/seed-v2-flows", methods=["POST"])
def api_admin_seed_v2_flows():
    config = load_config()
    flows = config.get("custom_flows", [])
    existing_keys = {f.get("key") for f in flows}
    created = 0

    for flow in get_v2_default_flows():
        if flow["key"] not in existing_keys:
            flows.append(flow)
            created += 1

    config["custom_flows"] = flows
    save_config(config)
    return jsonify({"ok": True, "created": created})


@app.route("/api/admin/custom-flow/step", methods=["POST"])
@app.route("/data/admin/custom-flow/step", methods=["POST"])
def api_admin_custom_flow_step():
    data = request.get_json(force=True)
    flow_key = str(data.get("flow_key", "")).strip()
    step = data.get("step", {})

    if not flow_key or not isinstance(step, dict):
        return jsonify({"error": "Flow and step required"}), 400

    step_id = str(step.get("id", "")).strip()
    step_type = str(step.get("type", "")).strip()
    step_label = str(step.get("label", "")).strip()

    if not step_id or not step_label:
        return jsonify({"error": "Step ID and label required"}), 400
    if step_type not in {"text", "number", "message", "choice", "yes_no"}:
        return jsonify({"error": "Invalid step type"}), 400

    clean_step = {
        "id": step_id,
        "type": step_type,
        "label": step_label,
        "required": bool(step.get("required", True)),
    }

    if step_type == "choice":
        choices = step.get("choices", [])
        if not isinstance(choices, list) or not choices:
            return jsonify({"error": "Choice step requires choices"}), 400
        clean_step["choices"] = [str(c).strip() for c in choices if str(c).strip()]

    show_if = step.get("show_if")
    if isinstance(show_if, dict):
        show_field = str(show_if.get("field", "")).strip()
        show_equals = str(show_if.get("equals", "")).strip()
        if show_field and show_equals:
            clean_step["show_if"] = {"field": show_field, "equals": show_equals}

    config = load_config()
    for flow in config.get("custom_flows", []):
        if flow.get("key") == flow_key:
            flow.setdefault("steps", []).append(clean_step)
            save_config(config)
            return jsonify({"ok": True})

    return jsonify({"error": "Flow not found"}), 404


@app.route("/api/admin/custom-flow/step/delete", methods=["POST"])
@app.route("/data/admin/custom-flow/step/delete", methods=["POST"])
def api_admin_custom_flow_step_delete():
    data = request.get_json(force=True)
    flow_key = str(data.get("flow_key", "")).strip()
    step_id = str(data.get("step_id", "")).strip()

    config = load_config()
    for flow in config.get("custom_flows", []):
        if flow.get("key") == flow_key:
            flow["steps"] = [s for s in flow.get("steps", []) if s.get("id") != step_id]
            save_config(config)
            return jsonify({"ok": True})

    return jsonify({"error": "Flow not found"}), 404


# Run setup at import time as well as local run time.
# This is required for hosted deployments such as Render/Gunicorn,
# because gunicorn imports app:app and does not execute the __main__ block.
@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


ensure_config_file()
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
