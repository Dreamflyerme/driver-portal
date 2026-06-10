# Driver Dispatch Portal

A small Flask web app for drivers to send non-urgent dispatch requests and for Dispatch/Admin users to manage them.

## Local setup

Install Python 3.12 or newer, then run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

First run admin login:

- Username: `admin`
- Password: `admin123`

Change the admin password once the app is running.

Demo/testing logins:

- Driver: `demo_driver` / `driver123`
- Dispatch: `demo_dispatch` / `dispatch123`

## Roles

- `driver`: enters shift details, submits requests, and receives live status updates.
- `dispatch`: views incoming requests, saves default dispatcher regions, comments, acknowledges, and completes requests.
- `admin`: manages users, request buttons, dispatcher groups, and depots.

## Baseline driver request options

- CIPs
- Milk Left Behind
- Already Started Milking
- Still Milking
- I'm Complete - Have Room
- Call Me Back
- Split has been cleared
- Free Text

## Data

Local development uses SQLite at `data/driver_portal.sqlite3`.

For the free Render showcase deploy, `data/driver_portal.sqlite3` is intentionally included in Git as the starter database. The app will boot from that committed database on Render.

Render free services do not include persistent disks. Any changes written by the deployed app can be lost when Render restarts or redeploys the service. The committed starter database will still provide the showcase setup each time the app deploys.

## Render deployment

Render settings for this app:

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`
- Environment variable: set `SECRET_KEY` to a long random value.

The included `render.yaml` defines a free-tier-compatible web service. It does not attach a disk.

For durable production-style data on Render, upgrade to a service that supports persistent disks and set `DATABASE_PATH=/var/data/driver_portal.sqlite3`, or move the app to an external Postgres provider.

If Render shows a 500 error immediately after deploy, check the service environment:

- On the free Render plan, remove any `DATABASE_PATH=/var/data/driver_portal.sqlite3` environment variable.
- Redeploy so the app uses the committed showcase database at `data/driver_portal.sqlite3`.

Do not upload `.venv`, `__pycache__`, `.env`, or extra local database backups.

## Git upload checklist

Upload these files and folders:

- `app.py`
- `requirements.txt`
- `Procfile`
- `render.yaml`
- `README.md`
- `.gitignore`
- `test_smoke.py`
- `data/driver_portal.sqlite3`

Do not upload:

- `.venv/`
- `__pycache__/`
- `.env`
- Any extra `*.sqlite`, `*.sqlite3`, or `*.db` files other than `data/driver_portal.sqlite3`

If using Git from a terminal:

```powershell
git init
git add app.py requirements.txt Procfile render.yaml README.md .gitignore test_smoke.py data/driver_portal.sqlite3
git commit -m "Deploy driver dispatch portal"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPO.git
git push -u origin main
```
