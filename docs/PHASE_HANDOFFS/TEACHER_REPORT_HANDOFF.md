# Teacher report handoff (2026-11-28)

## Behaviour
- HOD: `/hod/teacher-reports` — per-teacher PDF and all-teachers PDF (Main, Second, Lab),
  closed/archived forms in the HOD's branches only.
- Teacher: public `/teacher-report` — Emp ID + access code. CSRF, rate limited, audited, no-store.
- Access code: HMAC of SECRET_KEY, teacher and covered branches. Nothing stored.
  Changing SECRET_KEY changes every code; one code cannot be revoked on its own.
- New forms use Excellent, Good, Average, Below average, Poor
  (100 / 75 / 50 / 37.5 / 25 %). Existing forms are unchanged. College supplied no formula.
- HOD publish buttons and pop-up removed. Publish routes and Admin publishing remain.

## Verification
- New and scoring tests: 40 passed (`tests/test_teacher_report.py`,
  `test_hod_ready_to_publish.py`, `test_form_marks.py`, `test_closed_form_marks.py`,
  `test_score_marks_consistency.py`).
- Broad run before/after (14 files ignored at collection: pyotp missing; pyotp/segno stubbed):
  identical set of 362 failing/erroring tests before and after; passes 282 -> 289 (before the
  last two new tests were added), 34 skipped. Those 362 were not root-caused one by one;
  messages include missing CSRF token on pages and empty timetable data.
- Not verified: PostgreSQL, deployed site, browser check of the HOD and public pages.

No migration and no new dependency (pdfplumber is used by tests only if installed).
