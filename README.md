# APID — Academic Publishing and Information Database

Django 5 backend for [apid.journalslibrary.com](https://apid.journalslibrary.com) — a public registry of academic researchers operated by Consortium e-Learning Network Pvt Ltd (STM Journals imprint).

## Stack

- Python 3.12, Django 5.x, gunicorn
- PostgreSQL 16 (native on host, not containerised)
- Apache 2.4 (`ProxyPass` to `127.0.0.1:8120`), WHM/cPanel vhost
- AWS SES for transactional email (`mail.celnet.in`)
- Optional Google OAuth sign-in

## Layout

```
apps/
  identity/     accounts, Google OAuth, password reset, member model
  editorial/    reviewer/board workflows, apply, approved directory, mng_client
  content/     static marketing + policy pages (slug → HTML template)
  profiles/    public profile, verification, QR, PDF certificate/letter
  migration/   WP → Django importers (reader.py reads apid WP MySQL)
config/
  settings.py  env-driven (APID_* namespace)
  urls.py      slug routing + API
  wsgi.py / asgi.py
```

## Environment

Service reads `/etc/apid/env` (mode `640`, group `apid`). Required keys:

| Key | Purpose |
|---|---|
| `APID_SECRET_KEY` | Django signing (sessions, CSRF, password reset) |
| `APID_DB_URL` | `postgresql://user:pass@host/db` (or `.env.db` file next to repo) |
| `APID_GOOGLE_CLIENT_ID` / `APID_GOOGLE_CLIENT_SECRET` | Google OAuth |
| `APID_LOOKUP_SECRET` | Inter-service shared HMAC with Manuscript Engine |
| `APID_AWS_ACCESS_KEY_ID` / `APID_AWS_SECRET_ACCESS_KEY` / `APID_AWS_SES_REGION_NAME` | SES |
| `APID_DEFAULT_FROM_EMAIL` | e.g. `APID <no-reply@mail.celnet.in>` |

Never commit any of these. `/etc/apid/env` is outside the repo on purpose.

## Running locally

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export APID_SECRET_KEY=dev-only-change-in-prod
export APID_DB_URL=postgresql://apid:apid@localhost/apid
python manage.py migrate
python manage.py runserver
```

## Production deploy (celnet host)

```bash
cd /opt/apid/backend
git pull
sudo -u apid /opt/apid/.venv/bin/pip install -r requirements.txt
sudo -u apid /opt/apid/.venv/bin/python manage.py migrate
sudo -u apid /opt/apid/.venv/bin/python manage.py collectstatic --noinput
systemctl restart apid-web
```

Service: `apid-web.service` (gunicorn bound to `127.0.0.1:8120`, three workers).

## Collaborators

Two Claude agents work on this repo:

- **Wisp** on `itb09` (celnet-side)
- **Agent 2** on `conwizamit` VPS (manuscript-engine side)

Branch per feature → PR → review → merge → `git pull` on celnet to deploy.
