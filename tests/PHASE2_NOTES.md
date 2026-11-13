# Phase 2 — Teacher first-login flow

## Flow (enforced by `auth._enforce_account_setup`)
1. **Confirm email** (`/setup/email` → 6-digit code → `/setup/email/verify`); sets `users.email` + `email_verified=1`.
2. **Replace the default password** (`/change-password`). Email now comes *before* the password for teachers.
3. **Photo step** (`/setup/photo`, new): existing photo → preview card + "Keep this photo & continue" (or replace);
   no photo → upload prompt with live preview; "Skip for now" is allowed (initials avatar is the fallback).
   Uses `photos.save_photo` (JPG/PNG, size limit, Pillow re-encode, stored as `employee_photos/<EMP>.<ext>`).
4. Redirect to **`/teacher/assignment-setup`** (placeholder page; Phase 3 builds the real form).
Steps cannot be skipped: `/setup/photo` and the setup page redirect back to whichever earlier step is open.

## Migration v20
`users.first_login_complete INTEGER NOT NULL DEFAULT 0`. Back-fill: teachers who already have an email and
`must_change_password=0` get `1` (not pushed through onboarding again) and `email_verified=1`.

## Files
Modified: `schema.py`, `security.py` (current_user), `auth.py` (gate, verify_email, `setup_photo`), `teacher.py`
(`assignment_setup`), `photos.py` (fixed `/photos/me` → `auth.landing`), templates `setup_email`, `verify_email`,
`change_password`; tests `conftest.py`, `test_account_setup.py`.
New: `templates/setup_photo.html`, `templates/teacher_assignment_setup.html`, `tests/test_first_login_phase2.py`.

## Behaviour notes
* Photo step only enforced while `REQUIRE_ACCOUNT_SETUP` is on (same switch as the email step).
* HOD/Dean keep their own onboarding unchanged; they can add a photo at `/photos/me`.
* Students are unaffected.

## Tests
`test_first_login_phase2.py` (12): migration + Postgres translation, full order, no skipping, upload stored by emp id,
bad file rejected, continue-needs-photo, legacy photo counts, CSRF, student blocked, grandfathered teacher, gate off, `/photos/me`.
Existing `test_account_setup.py` updated for the new order (and its stale `/` student-login path fixed): 10/10.

## Pre-existing failures (also fail on the untouched zip, unrelated to Phase 2)
`test_app.py` ×4, `test_hod_dean_second_faculty.py` ×2 (HOD/Dean onboarding gate), `test_multi_round.py` ×1,
`test_teacher_scoping.py` ×1 (dashboard "Eligible Students" 72). Not touched; to be handled before Phase 9.
