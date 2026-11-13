# PHASE 3 HANDOFF — Teacher Assignment Setup (rooms + lab availability)

## 0. Source-of-truth note
`PHASE_0/1/2_HANDOFF.md` were **not in the zip**. The equivalents that exist are `tests/PHASE1_NOTES.md` and
`tests/PHASE2_NOTES.md` (nothing for Phase 0). Everything below was verified against the actual source, not the notes.

## 1. Implementation summary
`/teacher/assignment-setup` (the Phase 2 placeholder) is now a 4-step wizard:

1. **Subjects** — read-only, one expandable card per *subject code*, one row per section (branch, semester, academic year, period, student count, current room, current lab).
2. **Room Configuration** — optional room per subject + section.
3. **Lab Availability** — *Lab Available* / *No Lab* per subject + section (+ "all sections" shortcuts).
4. **Review and Save** — counts (total / configured / pending / Lab Available / No Lab / without room), full table, confirmation dialog, one atomic save, success/error banners, completion badge. Everything stays editable afterwards.

Unsaved values live in the form controls, so Previous/Next never loses them. Reload shows what is saved in the DB.

### Key design findings (what the real data looks like)
* The unit that is configured is a **teaching session** = `class_sessions` row = *section (timetable) + subject + teacher*. It is `UNIQUE(timetable_id, subject_code, teacher_user_id)`, so a subject repeated over several periods is already **one** row (`period_no` = earliest period). Hence "repeated periods ≠ duplicate configurations" holds by construction; different sections are different rows and stay independent. Verified on the real 38k-row import (242 teachers).
* **The timetable import contains no room data and no lab/batch data.** So there is no "original timetable room" to distinguish from the teacher's room, and no lab-batch relationships to preserve; `class_sessions`/`timetables` are never written to. If a future import adds rooms, store them in a new column and show both.
* Phase 1 already created `teacher_subject_settings` (per session, `UNIQUE(session_id)`, `room` nullable, `lab_available` 0/1). It is **reused**; no new table.
* `lab_available` is `NOT NULL DEFAULT 0`, so it cannot express "never configured" vs "No Lab" → migration v21 (below).

## 2. Database changes — migration **v21** (auto-applied on start)
```sql
ALTER TABLE teacher_subject_settings ADD COLUMN lab_status TEXT CHECK(lab_status IN ('available','none'));
UPDATE teacher_subject_settings SET lab_status='available' WHERE lab_available=1 AND lab_status IS NULL;
CREATE INDEX IF NOT EXISTS ix_tss_lab_status ON teacher_subject_settings(lab_status);
```
* `lab_status`: `NULL` = not configured, `'available'`, `'none'`. App keeps legacy `lab_available` in sync (1 only for `'available'`).
* Additive, non-destructive, idempotent (re-running the back-fill is a no-op; the runner already tolerates duplicate columns). Postgres: `to_postgres` rewrites to `ADD COLUMN IF NOT EXISTS` (tested; `pglast` is not installed here so real-Postgres execution was **not** run).
* Back-fill is conservative: only rows already `lab_available=1` become `'available'`; `0` rows stay NULL because 0 was a default, not a decision.
* Existing protections used: `UNIQUE(session_id)` (no duplicates), FKs to `class_sessions`/`users`, indexes `ix_tss_teacher`, `ix_tss_lab`, new `ix_tss_lab_status`. Room stays nullable.

## 3. Backend routes (all `@role_required('teacher')`, teacher taken from the session)
| Method | Path | Purpose |
|---|---|---|
| GET | `/teacher/assignment-setup` | Wizard page (server-rendered) |
| GET | `/teacher/assignment-setup/api/assignments` | Subjects→sections JSON + summary |
| GET | `/teacher/assignment-setup/api/summary` | Counts + `complete` |
| POST | `/teacher/assignment-setup/api/save` | Step 4: many items, one transaction (used by the UI) |
| POST | `/teacher/assignment-setup/api/room` | Save/clear one room |
| POST | `/teacher/assignment-setup/api/lab` | Set one lab status |

JSON POSTs need the `X-CSRF-Token` header (existing mechanism). Errors: 400 malformed, 403 not your assignment (same answer for foreign and non-existent ids), 422 per-item validation (`errors[]` with `session_id`/`field`/`message`, nothing saved), 500 safe generic message after rollback (no DB internals).

## 4. Services — new `feedback_system/assignment_setup.py`
`teacher_assignments`, `group_by_subject`, `get_assignment`, `summarize`, `teacher_summary`, `save_configuration` (validate everything first, then one transaction, rollback on any error), `save_room`, `save_lab_status`, `normalize_room`, `normalize_lab_status`, and **`lab_eligible_sessions(teacher_user_id)`** (the Phase 4 hook: only `'available'`).

Rules: room trimmed/collapsed, ≤20 chars, `[A-Za-z0-9 -/_.,()#&]`, empty → `NULL`; max 500 items per request; duplicate session ids rejected; absent key = field unchanged; a settings row owned by another teacher (session re-assigned) is reset, not inherited. Audit entry `teacher_setup_save` written after commit.

**Timetable/lab inconsistency rule:** if a *confirmed* `lab_faculty_assignments` row exists for a session, setting *No Lab* (or clearing) is **refused** (422, whole request rolled back) with a message to ask HOD/admin to cancel it; the UI also disables the *No Lab* radio with an explanation. Nothing is changed silently. Lab Available **never** creates a lab assignment or lab form.

## 5. Templates / frontend
Modified: `templates/teacher_assignment_setup.html` (rewritten), `templates/base.html` (+`{% block head %}`, +teacher nav link "Assignment Setup").
New: `static/js/assignment_setup.js`, `static/css/assignment_setup.css` (scoped to `.tas`, reuses app.css tokens/buttons; no inline scripts → CSP-safe). Mobile: cards not wide tables, ≥44 px touch targets, no horizontal overflow (tested at 375 px).

## 6. Files
**Modified:** `feedback_system/schema.py`, `feedback_system/teacher.py`, `feedback_system/templates/base.html`, `feedback_system/templates/teacher_assignment_setup.html`
**New:** `feedback_system/assignment_setup.py`, `feedback_system/static/js/assignment_setup.js`, `feedback_system/static/css/assignment_setup.css`, `tests/test_assignment_setup_phase3.py`, `tests/test_assignment_setup_ui.py`, `PHASE_3_HANDOFF.md`

## 7. Tests executed — actual results (SQLite, Python 3.x, run in this session)
| Run | Result |
|---|---|
| `tests/test_assignment_setup_phase3.py` | **51 passed** |
| `tests/test_assignment_setup_ui.py` (headless Chromium, live server) | **5 passed** |
| Full suite, **my tree** | 266 collected → **243 passed, 11 failed, 12 errors** |
| Full suite, **untouched zip** (baseline) | 210 collected → 187 passed, 11 failed, 12 errors |

The 23 failing/erroring tests are **the identical set** on the untouched zip and on my tree (diffed): `test_app.py`×4, `test_hod_dean_second_faculty.py`×2, `test_multi_round.py`×2, `test_teacher_scoping.py`×3, and 12 `test_photos_phase1.py` setup errors (the shared `teacher` fixture's login assertion fails once earlier tests have changed that teacher's password). Run alone with `test_first_login_phase2.py` and `test_account_setup.py`, only `test_full_first_login_order` fails — also on the untouched zip. **Phase 3 introduced no regressions and did not fix these.**

Coverage vs the brief: T1–T5 subject retrieval; T6–T11 rooms (incl. unauthorized); T12–T17 lab (incl. exclusion from eligibility, no auto lab assignment, inconsistency); T18–T24 workflow (navigation clicked in a real browser, save/reload/edit, summary, invalid inputs, injected failure via SQLite trigger proves rollback); T25–T29 regression (teacher/student/HOD login, a published form opens, timetable/feedback/user/lab tables unchanged (row-for-row) before/after saves); T30–T32 desktop, mobile, loading ("Saving…", disabled), error (server 500, network abort, client validation) and success states. Also: CSRF required, audit row, v21 shape/back-fill/idempotency/CHECK/Postgres translation. A mutation check (disabling the ownership test) made the authorization tests fail, confirming they bite.

## 8. Known limitations / decisions to confirm
1. **Only the teacher edits.** HOD/Admin/Dean/students get 403/redirect on these routes. The brief says HOD/Admin follow existing role design but defines no HOD→teacher-settings scope; add one (via `security.require_branch_access`) if wanted.
2. **Per-session, not per-period rooms.** "Different rooms for separate sessions" can't be modelled finer than section+subject because the timetable stores one row per section+subject+teacher. Needs a timetable-model change if per-period rooms are really required.
3. **No original-timetable room / lab batches exist** in the data (see §1), so nothing to display or preserve.
4. **All non-deleted sessions are listed** (no academic-year filter in the UI). `teacher_assignments(..., academic_year_id=)` supports one if multiple years are ever loaded.
5. **Not enforced as a gate**: after the Phase 2 photo step the teacher lands here, but can leave; the dashboard is not blocked if setup is incomplete. The status badge and nav link are the nudge.
6. Partial saves are allowed (pending subjects stay pending); the confirm dialog says so.
7. Real PostgreSQL execution of v21 was not run (no Postgres/`pglast` available); only the SQL translation was tested.
8. Pre-existing failures above remain for the Phase 9 clean-up.

## 9. Run / verify
```bash
pip install -r requirements-dev.txt          # Playwright+Chromium only needed for the UI tests
python -m pytest tests/test_assignment_setup_phase3.py tests/test_assignment_setup_ui.py
python app.py   # migration v21 applies on start; log in as a teacher -> Assignment Setup
```
Manual check: Step 2 type `205` + leave one blank → Step 3 pick options → Step 4 shows counts → Save → confirm → green banner; reload keeps values; Step 1 shows them.

## 10. Instructions for the Phase 4 agent
* Eligibility: use `assignment_setup.lab_eligible_sessions(teacher_id)` or `SELECT … FROM teacher_subject_settings WHERE lab_status='available'`. `'none'` and `NULL` are **not** eligible. Don't read `lab_available` alone (legacy; mirrors `'available'`).
* Create faculty rows only in `lab_faculty_assignments` (UNIQUE `session_id, lab_teacher_user_id`), keyed by the same `session_id`. Phase 3 already refuses *No Lab* while a `status='confirmed'` row exists; when Phase 4 **cancels** an assignment, set `status='cancelled'` (not delete) so that guard releases.
* A lab form links via `feedback_forms.category='lab'` + `lab_faculty_assignment_id`.
* Room for the lab can default from `teacher_subject_settings.room`.
* Reuse `_validate_items`' ownership pattern (session must belong to `current_user()`); never trust ids from the browser. JSON POSTs: `X-CSRF-Token` header. Don't start second-faculty work in the same module.
* Tests: copy the seeded-world fixture in `tests/test_assignment_setup_phase3.py` (isolated app, known data) rather than the shared session `app`, which is order-sensitive.
