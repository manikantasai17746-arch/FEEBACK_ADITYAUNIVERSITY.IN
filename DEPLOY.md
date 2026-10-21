# Deploying on Vercel + Supabase

The application runs unchanged on Vercel as a **serverless Flask function** and
stores its data in **Supabase (PostgreSQL)** instead of the local SQLite file.
SQLite is still used automatically for local development and the test suite, so
nothing about your current setup breaks.

```
Browser ──▶ Vercel (serverless WSGI: app.py) ──▶ Supabase (PostgreSQL)
```

| Concern | How it is handled |
|---|---|
| Serverless entry point | Root `app.py` exposes the Flask `app`; Vercel detects it automatically (no rewrites needed). |
| Database | `DATABASE_URL` (a `postgres://` string) switches the app from SQLite to Postgres via `feedback_system/pg.py`. Without it, SQLite is used. |
| Read-only filesystem | `SECRET_KEY` is taken from the environment; the app no longer needs to write `instance/secret_key`. |
| Cold starts / migrations | Migrations are idempotent and serialised with a Postgres advisory lock. |
| Supabase transaction pooler | `prepare_threshold=None` disables server-side prepared statements (required by the port‑6543 pooler). |
| Data | Seed once with `scripts/init_supabase.py` (imports the timetable workbook). |

---

## 1. Create the Supabase project

1. Create a project at <https://supabase.com> and note the database password.
2. Open **Project Settings → Database → Connection string** and copy **two**
   strings:
   * **Direct connection** (host ends `db.<ref>.supabase.co`, port **5432**) —
     used only for the one-off seeding step.
   * **Connection pooling → Transaction** (host ends
     `...pooler.supabase.com`, port **6543**) — used by the running app.

   Both look like:
   ```
   postgresql://postgres.<ref>:<password>@<host>:<port>/postgres?sslmode=require
   ```

---

## 2. Create the schema and seed the data (one-off, run locally)

From the project root on your machine (the workbook `.xlsx` must be present):

Set `ADMIN_EMAIL` and `ADMIN_PASSWORD` in your local `.env` before seeding; they
are required to create the initial admin account.

```powershell
# use the DIRECT connection here (port 5432) — it is much faster
$env:DATABASE_URL = "postgresql://postgres.<ref>:<password>@db.<ref>.supabase.co:5432/postgres?sslmode=require"

python -m pip install -r requirements.txt
python scripts/init_supabase.py
```

This applies migrations **v1..v5**, creates the admin account and imports every
branch, timetable, teacher, student, class session and eligibility row. It is
safe to re-run (existing rows are preserved).

Expected output ends with something like:

```
Supabase seed complete: {'users': 5265, 'branches': 16, 'timetables': 43,
                         'class_sessions': 567, 'enrollments': 5022,
                         'session_students': 38341}
```

---

## 3. Deploy to Vercel

### Option A — Git (recommended)

1. Push this project to GitHub/GitLab/Bitbucket.
2. In Vercel: **Add New… → Project → Import** the repository.
3. **Important:** if the Flask project lives in a sub-folder (e.g.
   `teacher-feedback-system/`), set **Root Directory** to that folder.
4. Add the environment variables (below) before the first deploy, then **Deploy**.

### Option B — Vercel CLI

```powershell
npm i -g vercel
vercel                                   # link the project (Root Directory = this folder)
vercel env add DATABASE_URL production   # paste the POOLER (6543) string
vercel env add SECRET_KEY production
vercel env add AUTO_IMPORT production    # 0
vercel --prod
```

### Environment variables (Vercel → Settings → Environment Variables)

| Variable | Value |
|---|---|
| `DATABASE_URL` | Supabase **pooler** string (port **6543**, `sslmode=require`) |
| `SECRET_KEY` | a long random string (e.g. `python -c "import secrets;print(secrets.token_hex(32))"`) |
| `AUTO_IMPORT` | `0` (data is already seeded) |
| `SESSION_COOKIE_SECURE` | `1` |
| `ADMIN_EMAIL` | your admin login (required when creating the first admin) |
| `ADMIN_PASSWORD` | strong admin password (required when creating the first admin) |
| `DEFAULT_TEACHER_PASSWORD` / `DEFAULT_STUDENT_PASSWORD` | optional overrides |
| `MAIL_USER` | the Gmail address that sends the verification / reset codes |
| `MAIL_APP_PASSWORD` | a Google **App Password** for that Gmail (Google Account → Security → 2-Step Verification → App passwords). Not the normal password. |
| `MAIL_FROM_NAME` | optional sender name, e.g. `Aditya University Feedback` |

Admin credentials from `.env` are only used when a database has no admin
account. To explicitly change an existing admin login, first verify that
`DATABASE_URL` / `FEEDBACK_DB` points to the intended database, then run:

```powershell
python scripts/sync_admin_credentials.py
```

This updates the first admin account's username, email, and password using
`ADMIN_EMAIL` and `ADMIN_PASSWORD`.

To reset every student and teacher password to `DEFAULT_STUDENT_PASSWORD` and
`DEFAULT_TEACHER_PASSWORD` in `.env`, verify the database target and run:

```powershell
python scripts\reset_student_teacher_passwords.py
```

This replaces existing personal passwords. Teachers will be required to change
their password after logging in; student passwords are set to the configured
default.

> Without `MAIL_USER` / `MAIL_APP_PASSWORD`, **no email can be sent on Vercel**, so students and
> teachers could not confirm their email or reset a password. Locally (no Vercel) the code is
> printed in the terminal instead, so you can test.

> `AUTO_IMPORT=0` keeps cold starts fast and avoids re-importing the workbook on
> every deploy. The data already lives in Supabase.

---

## 4. Verify

* Open the deployment URL → the student login page loads.
* Log in at `/admin/login` and open:
  * **Dashboard** → the *Download PDF reports* card,
  * **Reports**, **Forms**, **Tracking**, **Results**.
* Download a PDF report to confirm `reportlab` works serverlessly.

---

## 5. How it works (and why)

* **Backend selection** — `feedback_system/db.py` inspects the target: a
  `postgres://` `DATABASE_URL` uses `feedback_system/pg.py`; anything else is a
  SQLite file. The rest of the code is unchanged because the Postgres adapter
  mimics `sqlite3` (qmark `?` placeholders are translated to `%s`, rows are
  addressable by index *and* name, `lastrowid` is emulated with `RETURNING id`).
* **SQL translation** — the few SQLite-only constructs (`AUTOINCREMENT`,
  `INSERT OR IGNORE`, `CREATE VIEW IF NOT EXISTS`, `ADD COLUMN`,
  `DEFAULT CURRENT_TIMESTAMP`, case-insensitive `LIKE`) are rewritten to
  PostgreSQL, and the result is validated against the real PostgreSQL grammar in
  the test suite.
* **Migrations** — the same migrations run on Postgres, serialised by
  `pg_advisory_lock` so concurrent cold starts cannot race.
* **Anonymity** — unchanged: the `anonymous_answers` view and every
  teacher/report query still never join answers to a student.

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: psycopg` | `pip install -r requirements.txt` (adds `psycopg[binary]`). |
| `prepared statement ... does not exist` | Make sure `DATABASE_URL` is the **pooler** (6543) string; the app disables prepared statements for it. |
| Students get logged out constantly | `SECRET_KEY` is missing/empty on Vercel — set a fixed value. |
| `could not connect` during seeding | Check the password and that you used the **direct** (5432) string with `?sslmode=require`. |
| 500 on every page | Check the Vercel function logs; usually a wrong `DATABASE_URL`. |
| Want to go back to SQLite locally | Just unset `DATABASE_URL`; the app uses `feedback.db`. |

## 7. Cost / limits

* Vercel **Hobby** is free for personal projects and supports Python functions.
* Supabase **Free** gives a Postgres database (with a small project-pause after
  inactivity). For production workloads consider a paid tier.
* Every request opens one pooled Postgres connection and closes it at the end of
  the request, which is the recommended pattern for serverless.

---

## 8. Secrets (`.env`) and the Excel semester import

**Secrets.** Copy `.env.example` to `.env` for local work and fill it in. `.env` is
git-ignored and excluded from Vercel uploads. On Vercel, set the same names under
*Settings → Environment Variables* (never upload the file). In production
(`VERCEL` or `APP_ENV=production`) the app refuses to start without a `SECRET_KEY`
of 32+ characters, and cookies are `Secure` by default.

**New semester.** Sign in as admin → **Import** (top menu):

1. Pick the semester and academic year, choose the `.xlsx`, press **Check file** to preview counts.
2. Choose the file again and press **Import**. The page displays an in-progress indicator
   and disables submission while the import runs. The server also blocks the same workbook
   from being imported twice into the same semester and academic year. Existing forms and
   feedback are untouched.

Required columns (any order, one sheet per branch): `Time Table, Course Code, Course Name,
Emp.Code, Emp.Name, Roll.No`.

Vercel limits web uploads to about 4.5 MB and short function run-times. For a big file,
run it from your computer with the direct (5432) `DATABASE_URL` in `.env`:

```powershell
python scripts/import_excel.py "Semester_IV.xlsx" --semester 4 --year 2025-26 --check
python scripts/import_excel.py "Semester_IV.xlsx" --semester 4 --year 2025-26
```
