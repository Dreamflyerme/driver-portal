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

On Render, the app is configured to use SQLite at `/var/data/driver_portal.sqlite3`. That path must be backed by a Render persistent disk, otherwise any database created on Render can be lost on restart or redeploy.

## Render deployment

Render settings for this app:

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`
- Environment variable: set `SECRET_KEY` to a long random value.
- Persistent disk mount path: `/var/data`
- Database path: `/var/data/driver_portal.sqlite3`

The included `render.yaml` defines the web service, a persistent disk, and the `DATABASE_PATH` setting. If you replace an existing Render service instead of creating from the Blueprint, manually add a persistent disk mounted at `/var/data` and set `DATABASE_PATH=/var/data/driver_portal.sqlite3`.

For this showcase build, `data/driver_portal.sqlite3` is intentionally allowed in Git as the starter database. On first Render boot, if `/var/data/driver_portal.sqlite3` does not exist yet, the app copies the committed starter database to the persistent disk. After that, Render keeps using the persistent disk database and later deploys do not overwrite it.

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
