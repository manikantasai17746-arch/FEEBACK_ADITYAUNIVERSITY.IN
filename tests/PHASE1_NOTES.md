# Phase 1 — Foundation (database + shared employee photo system)

Branch: `feature/three-feedback-categories-phase1` (additive only; no existing data touched).

## Migration v19 (auto-applied on next start)
| Object | Purpose |
|---|---|
| `employee_photos` | One photo per employee code (`filename` = `employee_photos/<emp>.<ext>`), stored in the DB so it survives Vercel's read-only disk |
| `teacher_subject_settings` | Room + `lab_available` (0/1) per **session** (section + subject + teacher), `UNIQUE(session_id)` |
| `lab_faculty_assignments` | Lab faculty per session; `UNIQUE(session_id, lab_teacher_user_id)`; independent of second-faculty rows |
| `second_faculty_assignments` + `created_by`, `source` | Distinguish teacher-made from imported assignments |
| `feedback_forms` + `lab_faculty_assignment_id` | Link a `category='lab'` form to its assignment (`category` is free text; no constraint change) |
| indexes | `ix_forms_category`, `ix_sfa_main`, lab/settings lookups |

`pg.to_postgres` now maps `BLOB` → `BYTEA`.

## Photo system (`feedback_system/photos.py`)
* `GET /employee-photo/<emp>` — login required; serves stored photo → legacy `static/img/teachers/<emp>.jpg` → generated initials SVG (never 404 for a valid code; no JS fallback, CSP-safe). ETag + private cache.
* `GET|POST /photos/manage/<emp>` — preview card if a photo exists, upload prompt otherwise; live preview before saving; replace anytime. `GET /photos/me` redirects to your own.
* Validation: JPG/PNG only, `PHOTO_MAX_KB` (default 2048), min 64px, decoded with Pillow, EXIF stripped, scaled to ≤512px, re-encoded, decompression-bomb guard. CSRF required.
* Permissions: self; admin for anyone; HOD only for teachers in the HOD's own departments; students never.
* Reusable macros: `templates/_photo_macros.html` (`avatar`, `photo_card`) for Phases 4/6/8.
* New dependency: `Pillow>=10.0`.

## Known follow-ups for Phase 2
* The first-login gate (`_enforce_account_setup`) redirects every non-setup endpoint. Phase 2 must add `photos.manage` and `photos.serve` to `_SETUP_ENDPOINTS` for the onboarding photo step.
* Existing templates still use `static/img/teachers/<emp>.jpg` directly; they will be switched to the shared avatar in later phases.

## Tests
`tests/test_photos_phase1.py` (20 cases): migration, Postgres translation, constraints, serving/fallbacks, upload/replace/downscale, bad-file rejection, role scoping, CSRF.
