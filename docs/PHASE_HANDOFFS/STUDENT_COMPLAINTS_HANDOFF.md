# Student Complaints

Implemented on 2026-11-21 from `44818ef`.

- My Feedback shows **Report a College Issue** after a student has submitted every feedback form returned by the existing eligible-forms service. Drafts are excluded. A closed or archived assigned form with no submission still counts as incomplete. **Skip for now** hides the report for the current dashboard view.
- `POST /student/complaints` rechecks completion and loads department and semester from the latest active enrollment. The form uses a retry key so reposting it creates one complaint. Staff screens display the report without the student's name or roll number.
- `/hod/student-complaints` uses the existing HOD department scope and offers a semester filter. `/dean/student-complaints` covers all departments and offers department and semester filters. The Dean page looks up the active HOD for the report's timetable allocation, then checks legacy department scopes when applicable. Reports without a matching assignment show **Unassigned**.
- Each portal has a CSRF-protected status update for Pending, In Progress, and Resolved. The server checks staff scope and detects stale changes. The audit entry records the status change without copying the complaint description.
- Migration 28 creates the separate `student_complaints` table. The usual application startup applies the migration. Existing faculty feedback storage and submission routes keep their existing behavior.

## Verification

- SQLite regression run with Python UTF-8 mode and temporary databases in the workspace. `pytest tests/test_student_complaints.py tests/test_student_portal_theme.py tests/test_app.py tests/test_feedback_gate_stepc.py tests/test_deadline_and_pending.py tests/test_hod_dean_second_faculty.py tests/test_dean_dashboard_counts.py tests/test_dean_action_assignment.py --tb=short -rs` passed 134 tests.
- Chrome at 390 px and 1440 px exercised skip and submit, the HOD list and a status update, and the Dean list and combined filters. It also verified mobile table scrolling under enforced CSP.
- Embedded PostgreSQL applied migration 28 and exercised enrollment selection, HOD resolution, duplicate submission handling, and conditional status updates.

## Status updates in the student inbox

Both staff portals use the existing complaint status handler. When its conditional update succeeds, it reads the filing student's ID from the updated row and inserts an unread notice in the same transaction. The notice identifies the complaint number, category, staff role and status transition. The description stays in the complaint record. If the filing account was removed, staff can still change the status. Deploy `feedback_system/complaints.py` and the student notifications template with the normal build. The change uses the existing schema.

- `uv run --offline --no-project --with-requirements requirements-dev.txt python -X utf8 -m pytest -q tests/test_student_complaints.py tests/test_student_portal_theme.py::test_mark_as_read_removes_only_the_selected_notification --tb=short -o addopts=` passed all four tests on SQLite after correcting the new test's missing timetable ID. Coverage includes HOD and Dean changes, student-only delivery, duplicate and stale saves, HOD scope, a forged recipient and marking an update as read.
- `uv run --offline --no-project --with-requirements requirements-dev.txt python -X utf8 -m pytest -q tests --maxfail=5 --tb=line -o addopts=` stopped after five account-setup failures and one skip. Four tests could not retrieve their fixture user and one received the teacher login redirect instead of photo setup. PostgreSQL was not exercised for this change.
