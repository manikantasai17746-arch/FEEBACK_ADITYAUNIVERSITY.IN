# Upgrade notes — PDF reports, subject-scoped forms, toggle & visibility

This document summarises the upgrade applied to the **Faculty Feedback System**.
The stack (Flask + SQLite + Jinja2 + openpyxl), the existing architecture, auth,
database relationships, routes and all previously-working features are preserved.
Nothing was rebuilt from scratch and **no existing records were deleted**.

---

## 1. What was delivered

### Feature 1 — Admin Dashboard PDF reports
* Two separate, server-generated PDF documents, built from **live** database
  records (ReportLab is the one new dependency, `reportlab>=4.0`):
  * **Students who submitted feedback**
  * **Eligible students who have not submitted feedback**
* They respect the dashboard/report filters: academic year, semester, branch,
  timetable/section and feedback form. Filters are validated server-side.
* A student never appears twice within one report, and report rows contain
  **completion data only** — never answer content — so anonymity is preserved.
* Students/teachers cannot access them; anonymous requests are redirected to the
  admin login. Internal details are never exposed.
* Available from a **Download PDF reports** card on the admin dashboard and from
  buttons on the **Reports** page.

### Feature 2 — Toggle mode & text-visibility fixes
* The create-form screen has a clear **Normal / Subject only** toggle with strong
  active/inactive states (`.mode-toggle`, `.mode-btn`, `.is-active`).
* Readability safeguards: dropdown `option` text is legible once opened, disabled
  controls keep readable text instead of fading out, keyboard focus rings are
  consistent, and the muted text colour was darkened slightly for WCAG contrast.

### Feature 3 — Simplified Subject-scoped “Create Feedback Form”
* New **Subject only** mode creates **one** form for a subject across **all
  timetables** (or one specific timetable) — not one form per period.
* The timetable selector includes **“No Preference (All Timetables)”**.
* A live preview shows the matching session/timetable count and the **eligible
  student count** before publishing; that count uses the *same rule* as
  publication, so preview always equals the published eligible count.
* Duplicate forms for the same subject + configuration are blocked, and
  publishing is blocked when a subject has 0 eligible students.
* The original **Normal** mode (Subject + Teacher + Period) still works unchanged.

### Feature 4 — Consistency, tests and packaging
* Shared eligibility logic is reused by preview, publication, tracking and PDFs.
* Focused tests added (see §7) and the whole suite executed.
* This ZIP, the README and `.env.example` are the deliverables.

---

## 2. Files added / changed

| File | Change |
|---|---|
| `feedback_system/schema.py` | **New migration v5**: `form_sessions` table + `scope`, `subject_code`, `subject_name` on `feedback_forms`; back-fills existing forms. |
| `feedback_system/services.py` | Form-session/eligibility helpers, subject-scope preview & duplicate check, rewritten `save_form`, report queries (`report_forms`, `report_rows`, submitted/not-submitted student lists), teacher-session scoping. |
| `feedback_system/reports.py` | **New** — ReportLab PDF builder (landscape A4, repeating header, page numbers, empty-state). |
| `feedback_system/admin.py` | `form_save` scope/duplicate handling, `/api/subjects`, `/api/subject/preview`, two PDF routes, dashboard/report filter data. |
| `feedback_system/teacher.py` | Teacher results scoped to the teacher's own sessions. |
| `feedback_system/templates/admin_form_builder.html` | Mode toggle, subject selector, timetable hint, preview panel. |
| `feedback_system/templates/admin_dashboard.html` | **Download PDF reports** card. |
| `feedback_system/templates/admin_reports.html` | Timetable/form filters + two PDF buttons. |
| `feedback_system/templates/admin_forms.html`, `admin_result_detail.html` | Show subject scope / “All timetables”. |
| `feedback_system/static/js/app.js` | Toggle, subject cascade, preview, scope-aware publish validation. |
| `feedback_system/static/css/app.css` | Toggle, preview-panel and readability/contrast rules. |
| `requirements.txt` | Added `reportlab>=4.0`. |
| `tests/test_upgrade.py` | **New** — focused tests for the upgrade. |
| `README.md`, `.env.example` | Updated/new documentation. |

---

## 3. Setup & run

```bash
pip install -r requirements.txt          # includes reportlab
python app.py                            # -> http://localhost:5000
```

* On first run the app applies migrations **v1..v5** and, if the database is empty,
  imports the timetable workbook.
* The bundled `feedback.db` is **already migrated to v5** and contains the
  existing records, so it can be used immediately.
* Configuration is via environment variables; see `.env.example`.
* Production: `waitress-serve --host 0.0.0.0 --port 8000 app:app` with
  `SESSION_COOKIE_SECURE=1` behind HTTPS.

Admin credentials must be set with `ADMIN_EMAIL` and `ADMIN_PASSWORD` in the
environment (for local development, in `.env`) before initializing a fresh
database; there is no built-in admin login. Teacher login uses emp-code / `teacher@123`;
student roll number / `student@123`.

---

## 4. PDF routes

| Method | Route | Report |
|---|---|---|
| GET | `/admin/reports/submitted-students.pdf` | Students who submitted |
| GET | `/admin/reports/not-submitted-students.pdf` | Eligible students who did **not** submit |

**Filters** (all optional, combined): `academic_year_id`, `semester_id`,
`branch_id`, `timetable_id`, `subject_code`, `status`, `form_id`.
Supplying `form_id` restricts the report to that single form (most precise).
When no form matches the filters the request is redirected with a message instead
of producing a misleading document. Reports are admin-only.

---

## 5. Eligibility & submission tracking (shared logic)

`form_sessions(form_id, session_id)` records **exactly which teaching sessions a
form covers** and is the single source of truth.

A student is **eligible** for a form when one of its covered sessions lists them
in `session_students`:

```sql
SELECT COUNT(DISTINCT student_user_id)
FROM session_students
WHERE session_id IN (SELECT session_id FROM form_sessions WHERE form_id = ?)
```

* **Preview** (create screen) and **publication** use this identical rule, so the
  previewed count equals the published eligible count.
* **Tracking** derives *submitted* from `submissions` (completion only) and
  *not-submitted* as `eligible − submitted`.
* **PDFs** use the same lists (`submitted_students_for_form`,
  `not_submitted_students_for_form`).
* A subject-scoped form stores one representative session (`feedback_forms.session_id`)
  for display/publication, while `form_sessions` holds the full population.
* When a student submits, the recorded session is **their own** covered session,
  not the representative one, so per-teacher result scoping stays correct.

### Anonymity
Completion tracking links a student to a *submission row* only — never to the
anonymous answer content. Report queries never join answers, and the
`anonymous_answers` view exposes no student column. Teacher results are scoped to
the teacher's own sessions via `teacher_form_session_ids`, so a shared
subject-form never leaks one teacher's results into another's.

---

## 6. Toggle & visibility summary

* **Modes**: Normal (Subject + Teacher + Period) and Subject only, switched by the
  toggle; the default on the create screen is **Subject only**.
* The subject field is shown only in Subject-only mode; the session field only in
  Normal mode; both validate on save and on the publish button.
* Visual fixes: distinct active toggle styling, legible dropdown options, readable
  disabled controls, uniform focus ring, improved text contrast. The design
  identity (navy palette, layout) is unchanged.

---

## 7. Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

* New tests cover: all-timetable subject creation, specific-timetable
  restriction, preview == published eligibility, duplicate prevention, per-form
  tracking, other-subject/configuration exclusion, invalid-subject rejection, PDF
  auth/content/filters/empty/multi-page, anonymity (no answer content in reports)
  and a Normal-mode regression.
* **Result: 63 passed** (48 pre-existing + 15 new), executed locally.
* JavaScript syntax checked with `node --check`.

---

## 8. Record-preservation confirmation

Migration v5 is additive and backward-compatible. Applying it to the real
`feedback.db` produced a byte-for-byte row-count match on every pre-existing
table (a backup `feedback.db.pre-v5.bak` was kept locally, not shipped):

| Table | Before | After |
|---|---|---|
| users | 5265 | 5265 |
| class_sessions | 567 | 567 |
| session_students | 38341 | 38341 |
| enrollments | 5022 | 5022 |
| feedback_forms | 3 | 3 |
| form_questions | 7 | 7 |
| question_options | 25 | 25 |
| submissions | 10 | 10 |
| branches / timetables / academic_years | 16 / 43 / 1 | 16 / 43 / 1 |
| schema version | 4 | **5** |
| form_sessions | — (new) | 3 (one per existing form) |

All admin accounts, students, forms, feedback and submission history are intact.
