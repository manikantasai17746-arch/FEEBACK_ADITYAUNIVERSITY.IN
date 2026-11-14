# PHASE 4 HANDOFF — Second Faculty Assignment

## 0. Starting point (read this first)
* `PHASE_0/1/2_HANDOFF.md` are not in the zip; only `PHASE_3_HANDOFF.md` is. Everything below was checked against the source.
* **The zip already contained a working Second Faculty module** (`second_faculty.py`, `second_faculty_service.py`, `templates/teacher_second_faculty.html`, `static/js/second_faculty.js`, table `second_faculty_assignments`, migration columns `created_by`/`source`). It was **not rebuilt**. It had **no tests** and **no handoff**, and it ignored the Phase 3 lab decision.
* Phase 4 work in this session = audit + **the No-Lab rule (new requirement)** + tests + this document.

## 1. New rule (requested): no Second Faculty for a section with no lab
Second Faculty may only be assigned/replaced on a session whose Phase 3 `teacher_subject_settings.lab_status = 'available'`.
| lab_status | Assign / Replace | Remove existing |
|---|---|---|
| `available` | allowed | allowed |
| `none` (No Lab) | **409 `no_lab`** | allowed |
| NULL / no settings row (not configured) | **409 `no_lab`** ("set Lab Available in Assignment Setup first") | allowed |
Enforced in the backend (`second_faculty_service.require_lab`, called by search, preview, create, replace) **and** the UI (card shows an explanatory message instead of the button; Replace hidden). Never decided by the browser.
Consistency guard in Phase 3 (`assignment_setup._validate_items`): setting **No Lab** is refused (422, whole save rolled back) while a confirmed Second Faculty assignment exists, same as the existing lab-faculty guard. Remove the Second Faculty first.

## 2. Data model (no new table, no migration)
`second_faculty_assignments`, reused. A Phase 3 "teacher assignment" is a `class_sessions` row (section + subject + teacher); `session_id` is the link and carries year / semester / branch / timetable via its timetable (copied onto each row too).
* `id` = unique assignment id; `UNIQUE(session_id, second_emp_code)` = DB-level duplicate prevention; indexes on session, branch, status, second teacher, subject.
* `source='teacher'` rows are editable by the main faculty; `source='import'` (timetable import) rows are read-only for the teacher (admin/HOD manage them).
* Many-to-many: many second faculty per session; one employee can be second faculty on many sessions of many main teachers, and can be main elsewhere (no global role lock). Self-assignment is refused only within that assignment.
* **Soft removal**: `status='rejected'` + `removed_at/removed_by`; rows are never deleted. Re-adding the same employee revives that row (no second row). Feedback, forms, students, employees, timetables are never touched.

## 3. Routes (all `@role_required('teacher')`; main faculty always from the session)
Base `/teacher/second-faculty`: `GET /` cards · `GET /api/employees?session_id&q&page` · `GET /api/preview?session_id&emp_code&page&page_size[&replace_id]` · `GET /api/cards/<session_id>` · `POST /api/assign` · `POST /api/assignments/<id>/replace` · `POST /api/assignments/<id>/remove`.
JSON POSTs need `X-CSRF-Token`. Status codes: 400 bad id / self-assignment, 404 not yours or not found (same answer, no probing), 409 duplicate / no_lab / same employee, 422 no eligible students, 500 generic message after rollback (DB text never leaked). Audit rows written for assign/replace/remove.

## 4. Services (`second_faculty_service.py`)
`owned_session`, `lab_status_of`, `assign_block_reason`, `require_lab`, `search_employees`, `validate_employee`, `eligible_student_count(s)`, `eligible_students_page`, `list_cards`, `card_for_session`, `build_preview`, `create_assignment`, `replace_assignment`, `remove_assignment` (each write = one transaction via `_run_write`).

## 5. Search / photos / eligibility
* Search: Employee ID or name, partial, case-insensitive, LIKE wildcards escaped, ≥2 chars, 10 per page + `has_more`, debounced in the UI. Active teacher/HOD/dean only, de-duplicated by emp code; the logged-in main faculty never offered. Returns name, ID, designation, departments, initials, photo URL only (no e-mail/phone/login).
* Photos: shared Phase 1 route `/employee-photo/<emp_code>`; UI shows initials behind the image and hides a broken image.
* Eligible students = `session_students` of **exactly that session** (built from the timetable import) for **active students**; section/subject/teacher/year/semester/branch are implied by the session. Other sections, other subjects, other teachers' sessions and inactive students are excluded. Zero students ⇒ warning + `can_confirm=false` + 422 on save. Preview is computed live (no cache), paginated, only for the owning teacher.

## 6. UI
Existing EduVault-style page kept: cards → search → profile preview → student preview → confirm → success; replace/remove with confirmation; toasts, spinners, empty / no-results states; confirm button disabled while saving. Changed this session: card Lab fact now reads `lab_status`; Assign/Replace shown only when `can_assign`.

## 7. Files
Modified: `feedback_system/second_faculty_service.py`, `second_faculty.py`, `assignment_setup.py`, `static/js/second_faculty.js`. New: `tests/test_second_faculty_phase4.py`, `PHASE_4_HANDOFF.md`.

## 8. Tests — actual results
| Run | Result |
|---|---|
| `python -m unittest tests.test_second_faculty_phase4` (isolated seeded DB) | **52 run, 52 OK** |
| Mutation check: lab check disabled | 3 tests failed (they bite) |
| Mutation check: ownership check disabled | 4 tests failed |
| Real Chromium (Playwright) smoke, 1280px and 375px | login → cards (6) → 3 Assign buttons / 3 no-lab messages → search "anita" → photo request → profile preview OK; no horizontal scroll |

Covers brief tests 1–19 (search, relationships), 20–30 (preview scoping), 31–39 (save, persist, replace, soft remove, injected-failure rollback, repeated/duplicate submits, unique constraint), CSRF, tampered ids, SQL-injection strings, foreign-teacher access, anonymous/student access, and regressions 40–46 (teacher/student/admin login, Phase 3 room + lab save, photo route).

**NOT run (be aware):** `pytest` could not be installed (no network), so the **rest of the existing suite (incl. the 51 Phase 3 tests and Phase 3 UI tests) was not executed** after my change. Phase 3 reported 23 pre-existing failures/errors on its baseline; I could not re-compare. Tests 47 (feedback form regression), 48–54 only partly covered by the smoke check above (confirmation modal save, edit/remove clicks, loading/error states were not driven in a browser). HOD/Admin-side second-faculty tests (`test_hod_dean_second_faculty.py`) were not re-run.

## 9. Known limitations / decisions to confirm
1. Teachers only; HOD/Admin manage second faculty through their existing pages (`hod.py`/`admin.py`), unchanged.
2. Imported (`source='import'`) assignments on a No-Lab section are left as they are (read-only to the teacher); only HOD/admin can change them. Decide whether to deactivate them.
3. Assignments are per session (section+subject+teacher), not per period; there is no separate timetable-period key.
4. "Lab not set" is treated like No Lab (cannot assign) so nothing is guessed.
5. Postgres execution not tested (no migration was needed this phase).
6. Existing Phase 3 failing tests remain for Phase 9 clean-up.

## 10. For Phase 5
Do not reuse `second_faculty_assignments` as lab faculty: lab faculty live in `lab_faculty_assignments` (Phase 3 note). Second-faculty feedback forms should key on `second_faculty_assignments.id` and filter `status='confirmed'`; rejected rows are history. Run: `python -m unittest tests.test_second_faculty_phase4 -v`, then `python app.py` → log in as a teacher → *Second Faculty*.
