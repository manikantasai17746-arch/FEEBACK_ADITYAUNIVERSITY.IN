# Faculty Feedback System

A secure, per-class-period faculty feedback platform. Students rate the specific
teacher **for each subject/period they actually attend**; teachers only ever see
anonymous, admin-published results for their own sessions; administrators build
forms, track submissions and publish results.

This is an **upgrade of the existing single-file Flask app** — the technology
stack (Flask + Jinja2 + openpyxl + ReportLab) and the original data model are
preserved. It runs on **SQLite locally** (dev + tests) and on
**PostgreSQL / Supabase in production**, and deploys to **Vercel** as a
serverless function — see **[DEPLOY.md](DEPLOY.md)**. No existing records are
deleted, and the original `assignments` / `feedback` tables and routes still work.

---

## 1. Quick start

```bash
pip install -r requirements.txt
python app.py                     # -> http://localhost:5000
```

On the very first run the app:

1. creates/upgrades the SQLite database `feedback.db` (versioned migrations), and
2. imports `III_semester_Student_timetable_detailed_report.xlsx` into the real
   academic structure (branches, timetables, class periods, teachers, students,
   enrollments, per-session eligibility).

> **Deploying to Vercel + Supabase?** Set `DATABASE_URL` to a `postgres://`
> connection string and the app uses PostgreSQL automatically — see
> **[DEPLOY.md](DEPLOY.md)** for the step-by-step guide.

### Default logins

| Role    | URL              | Identifier             | Password (default)       |
|---------|------------------|------------------------|--------------------------|
| Student | `/`              | Roll number, e.g. `25B11CS015` | `student@123`    |
| Teacher | `/teacher/login` | Employee code, e.g. `6817`     | `teacher@123`    |
| Admin   | `/admin/login`   | `admin@college.edu`            | `admin123`       |

**Change every default password in production.** Students/teachers can change
their own password at `/change-password`; admins can reset any account from
*Teachers* / *Students*.

### Try the sample scenario

Teacher **CHIKKALA LOVA LAKSHMI (emp 6817)** teaches **ADSA (`2501CS10`)** at
period 3 and **ASE (`2501IT07`)** at period 5 in timetable `T1-General`. Create a
form per period and confirm the two sets of results never mix — this is the
core business requirement.

---

## 2. Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | auto-generated & persisted in `instance/secret_key` | Session signing (required in production) |
| `DATABASE_URL` | *(unset)* | **Supabase/PostgreSQL** connection string; when set, Postgres is used instead of SQLite |
| `FEEDBACK_DB` | `./feedback.db` | SQLite database path (used when `DATABASE_URL` is unset) |
| `FEEDBACK_XLSX` | `./III_semester_...xlsx` | Timetable workbook |
| `ADMIN_EMAIL` | `admin@college.edu` | Bootstrap admin login |
| `ADMIN_PASSWORD` | `admin123` | Bootstrap admin password |
| `DEFAULT_TEACHER_PASSWORD` | `teacher@123` | Seeded teacher password |
| `DEFAULT_STUDENT_PASSWORD` | `student@123` | Seeded student password |
| `ACADEMIC_YEAR` | `2025-26` | Academic year label for the import |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` behind HTTPS |
| `AUTO_IMPORT` | `1` | Import the workbook when the DB is empty (`0` in production) |
| `LOGIN_MAX_ATTEMPTS` | `8` | Failed logins before lockout |
| `LOGIN_LOCKOUT_SECONDS` | `300` | Lockout duration |

See `.env.example`. A production launch uses a WSGI server, e.g.
`waitress-serve --host 0.0.0.0 --port 8000 app:app`.

---

## 3. Architecture

```
app.py                       entry point (python app.py)
api/index.py                 Vercel serverless entry point (WSGI)
vercel.json                  Vercel rewrites -> api/index
scripts/init_supabase.py     one-off Supabase schema + workbook seed
feedback_system/
  __init__.py                app factory, config, Jinja globals, error handlers
  db.py                      connection helpers + migration runner (SQLite/Postgres)
  pg.py                      PostgreSQL/Supabase adapter + SQL dialect translation
  schema.py                  versioned SQL migrations (v1..v5)
  import_data.py             Excel -> academic structure (idempotent)
  security.py                hashing, CSRF, RBAC decorators, rate limit, audit
  services.py                all business logic (forms, eligibility, stats,
                             tracking, publish, report rows)
  reports.py                 ReportLab PDF builder for the student reports
  auth.py                    student/teacher/admin login, logout, password change
  admin.py                   admin dashboard, academics, builder, tracking,
                             results, PDF reports
  student.py                 student dashboard + submission
  teacher.py                 teacher dashboard + anonymous results
  templates/                 26 responsive Jinja templates
  static/css/app.css         design system (preserved navy identity, responsive)
  static/js/app.js           form builder, mode toggle, cascading selects,
                             subject preview, table filter, roll lists
tests/                       pytest suite
```

### Database

Two backends are supported transparently:

* **SQLite** (default) — the local file `feedback.db`; used for development and
  the test suite.
* **PostgreSQL / Supabase** — selected automatically when `DATABASE_URL` (or
  `SUPABASE_DB_URL`) is a `postgres://` connection string. The adapter
  `feedback_system/pg.py` keeps the `sqlite3` ergonomics (`?` placeholders, rows
  addressable by index *and* name, `lastrowid` via `RETURNING id`) and rewrites
  the few SQLite-only constructs into PostgreSQL, so no application code is
  duplicated. The generated SQL is validated against the real PostgreSQL grammar
  in the test suite.

Versioned migrations (`feedback_system/schema.py`) applied by `db.run_migrations`:

* **v1** reproduces the *original* `assignments` and `feedback` tables verbatim.
* **v2** adds `timetable`, `period_no`, `semester`, `academic_year` to `assignments`.
* **v3** adds the academic/auth/feedback domain tables.
* **v4** adds `session_students` (the eligible population per teaching session).
* **v5** adds `form_sessions` (the sessions a form is distributed to) plus
  `scope`, `subject_code` and `subject_name` on `feedback_forms`, so a single
  form can cover a subject across **all timetables**. Existing forms are
  back-filled (one `form_sessions` row each) — **no data is dropped**.

Key relationships:

```
branches ──< timetables ──< class_sessions ──< session_students >── users(student)
                │                 │                                     │
        feedback_forms ──< form_sessions >── class_sessions             │
                │                                                      │
                ├──< form_questions ──< question_options               │
                │                                                      │
                └──< submissions (student link, for completion only) ──┘
                          └──< answers   (NO student link  → anonymous)
        result_publications (form+session publish state)
```

`form_sessions` is the **single source of truth for eligibility**: a student is
eligible for a form when one of the form's covered sessions lists them in
`session_students`. The same rule powers the create-form preview, publication,
submission tracking and both PDF reports — so the preview count always equals the
published eligible count.

**Anonymity** is enforced structurally: `answers` link only to `submissions`,
and the `anonymous_answers` view exposes answers with **no** student column.
Every teacher-facing query reads through that view, and no student identifier is
ever selected, rendered, exported or logged for teachers.

---

## 4. Feature summary

* **Admin auth** – email + password, hashed (scrypt), sessions, logout, CSRF,
  rate-limiting/lockout, RBAC, audit log.
* **Admin dashboard** – stat tiles + **Quick Actions** in the upper-middle area
  (academics, timetables, subjects, teachers, students, form builder, tracking,
  preview/publish, reports, audit, re-import).
* **Form builder with two modes** – a **Normal** mode (Subject + Teacher + Period,
  the original behaviour) and a **Subject only** mode that creates **one** form
  for every eligible student of a subject, optionally across **No Preference (All
  Timetables)**. The builder cascades year → semester → branch → timetable →
  subject and shows a live **eligible-student preview** before publishing.
  Dynamic questions with a **+** button, per-question type (rating / single /
  multiple choice / written), required flags, configurable options (e.g.
  Excellent/Good/Bad/Very poor — editable/extendable), reorder/delete,
  **Save Draft** and **Publish Form** as distinct actions.
* **Subject-scoped forms** – one form distributes to every teaching session of a
  subject in the selected configuration (all timetables, or a single timetable).
  Duplicate forms for the same subject+configuration are blocked; publishing is
  blocked when a subject has **0 eligible students**.
* **Individual class-period feedback** – in Normal mode every form targets one
  teaching session `(academic year, semester, branch, timetable, subject, teacher,
  period)`; answers are bound to that session and can never appear under another
  subject of the same teacher.
* **Student dashboard** – shows only the sessions the student is eligible for,
  with year/semester (Year 1 = Sem 1–2 … Year 4 = Sem 7–8) derived from
  enrollment data; validation, confirmation, pending/submitted statuses,
  duplicate + closed-form protection, answers preserved on recoverable errors.
* **Teacher dashboard** – subject selector (auto-selected when only one subject),
  question-wise counts and percentages over the real valid-response denominator,
  anonymous paginated suggestions, and **published-only** visibility scoped and
  authorised on the backend.
* **Submission tracking** – filters (year, semester, branch, timetable, teacher,
  subject, form status, include archived/deleted), summary stats, **Submitted** /
  **Not Submitted** roll lists, historical timetable snapshots, CSV export.
* **Preview & publish** – separate preview and a distinct *Publish Results*
  action (plus unpublish with audit logging); form status and result publication
  status are independent.
* **Admin PDF reports** – two accurate ReportLab PDFs built from live records:
  **submitted students** and **eligible students who have not submitted**. They
  honour the dashboard/report filters (year, semester, branch, timetable,
  feedback form), never duplicate a student, and contain **completion data only**
  (no answer content), so anonymity is preserved. Available from the dashboard
  *Download PDF reports* card and the Reports page.
* **Clear mode toggle & readable UI** – strong active/inactive styling for the
  Normal/Subject-only toggle, legible dropdown options and disabled controls,
  consistent keyboard focus rings, and improved text contrast.
* **Search / responsive tables** – case-insensitive search, filter+pagination,
  scrollable table containers with sticky headers, empty states.
* **Security** – hashing, RBAC, backend validation, CSRF, SQL-injection safety
  (parameterised queries), XSS-safety (Jinja autoescaping + JS escaping), safe
  error pages, audit logging.

### Routes added / changed by this upgrade

| Method | Route | Purpose |
|---|---|---|
| GET | `/admin/reports/submitted-students.pdf` | PDF of students who submitted (respects filters) |
| GET | `/admin/reports/not-submitted-students.pdf` | PDF of eligible students who have **not** submitted |
| GET | `/admin/api/subjects` | Distinct subjects for the selected year/semester/branch/timetable |
| GET | `/admin/api/subject/preview` | Eligible-session/timetable/student preview + duplicate check |
| POST | `/admin/forms/save` | Now accepts `scope=subject` and creates one form for a subject |

---

## 5. Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite builds a throwaway database (running the real import) and exercises
auth/RBAC, the form builder, the full student→tracking→publish→teacher flow,
per-period isolation, duplicate/closed/ineligible protection, anonymity,
publication gating, history preservation, search and exports. It also covers the
subject-scoped (all-timetable) forms, specific-timetable restriction,
preview == published eligibility, duplicate prevention, per-form tracking, PDF
report auth/content/filters/empties, and the **PostgreSQL translation layer**
(every migration statement plus representative queries are parsed with the real
PostgreSQL grammar via `pglast`).

**Result: 181 passed.**

---

## 6. Known limitations / manual steps

* Default seeded passwords are shared by role and should be rotated
  (`DEFAULT_*_PASSWORD`, per-user reset, or `/change-password`).
* Passwords are not yet forced to change on first login (`must_change_password`
  is set on admin reset but not enforced as a redirect — a small follow-up).
* The app ships with Flask's development server; use a WSGI server in production
  and set `SESSION_COOKIE_SECURE=1` behind HTTPS.
* Editing a form's questions is intentionally blocked once it has responses
  (409) so historical answers are never orphaned; create a new form instead.
* The legacy `feedback` table is retained for backward compatibility but the new
  form/answer tables are the operational source of truth.
#   F E E D B A C K - A D I T Y A _ U N I V E R S I T Y  
 #   F E E D B A C K - A D I T Y A _ U N I V E R S I T Y  
 
---

## HOD / Dean / Second-faculty upgrade (v8)

See **[IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md)** for the full inventory, migration notes, test results, and list of college data still required.

New logins (after Admin creates accounts + department scopes):

| Role | URL | Identifier |
|------|-----|------------|
| HOD  | `/hod/login`  | Employee code |
| Dean | `/dean/login` | Employee code |

Admin tools added:

* `/admin/second-faculty` — mapping status & import batches
* `/admin/accounts/hod-dean` — HOD/Dean accounts + CSV template
* `/admin/requirements` — feedback requirements for Dean publication monitoring

**Important:** `Individual Workload.xlsx` and `CSE_III_Sem_Second_Faculty_Import.xlsx` were not included in the source ZIP. The import workflow is ready; it will not invent assignments.
