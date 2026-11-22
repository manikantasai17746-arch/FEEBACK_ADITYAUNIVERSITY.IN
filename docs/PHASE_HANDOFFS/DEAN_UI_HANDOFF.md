# Dean UI handoff (2026-11-22)

Status: implemented through checkpoint 4 (2026-11-22); **needs review and a run against the real timetable data before merge**. No schema change, no new dependency, no push or deploy. Checkpoint 4's source was never attached by the previous agent: the HOD-notification warning and the browser harness were **rebuilt from its notes**, then verified against the actual source.

## Scope delivered
| Area | Change |
|---|---|
| Theme / nav | `static/css/dean-theme.css` (preview theme), `templates/base.html` Dean nav: Overview, Performance, Comparison, Actions, Publication, **Student Complaints**, My HODs, Profile, theme toggle + A-/A/A+, Security, Logout. The nav is shared by all eight pages and the mobile hamburger menu. Authorization on `dean.student_complaints` unchanged. |
| Dashboard | Rebuilt to the preview with real data; hero "Student Complaints" shortcut kept; one row per (department, HOD); rows of the same department and semester are told apart by timetable code. |
| Performance | `id="teachers"` anchor (View links land on the list), filtered-view banner with "Show all departments", band card HOD name fixed, link "Send review requests to HODs" (existing bulk-action service) beside the existing e-mail composer (existing `mailer`). |
| Publication | One row per (department, HOD) with that HOD's own form counts; View links carry `branch_id`, `hod_id` and `#teachers`. |
| Comparison, Actions, My HODs, Analytics, Historical | Their markup already matched the preview; added scoped CSS: sub-navigation chips, tables scroll inside their card at <=760px (no page-wide horizontal scroll at 390px). |
| Result popup, mail dialogs, actions | `dean.performance_result` popup (scores, category bars, question table, anonymous comments with rating-based filters, no Flagged filter, print only the comments shown), per-card Mail teacher dialog, Email HOD about ticked teachers, optional improvement action per delivered teacher (owner = the teacher's HOD or the HOD picked for one teacher). **Checkpoint 4:** if the HOD's assignment e-mail fails the action is kept and the result carries `hod_notify_failed` and a warning. |
| HOD scoping | `dean._band_hods` now returns `timetable_ids` and `whole_dept`; `dean._hod_owns` is the single ownership rule used by Performance, bands, band cards and `_row_hod` (bulk review requests). `scoring.faculty_scores_for_branches` rows gain `timetable_id`. |

## Verified
- `pytest tests/test_dean_hod_scope.py` : 18 passed. Two HODs in one department and semester with different timetables: each Dashboard/Publication View link opens exactly that HOD's teachers (2 vs 3), band cards name the right HOD, a department or HOD of another Dean is ignored, teachers of another Dean are not reachable, non-Dean roles are refused, bulk review requests reach the owning HOD, Student Complaints is in the nav of all eight pages. Mutation checks: restoring the old triple-only matching makes 3 (nav/bands) and 1 (review request) of these tests fail.
- Together with `test_dean_dashboard_counts`, `test_student_complaints`, `test_hod_dean_second_faculty`: 34 passed.
- (Earlier checkpoint, superseded by the checkpoint 4 run below) Full suite baseline vs tree was reported as 427 identical failures (166 F, 261 E); that count was not reproduced on 2026-11-22.
- Browser (Chromium via Playwright, synthetic data): all eight pages plus Student Complaints at 1280 and 390px, no horizontal page scroll at 390px; hamburger opens and lists Student Complaints on every page; theme toggle and text size toggle work on every page.
- Checkpoint 4 (2026-11-22; Python 3.12.3, real pytest 9.2.0, real pyotp/segno/psycopg/pglast, Chromium via Playwright 1.63, recording mail transport, synthetic data):
  - `tests/test_dean_performance_interactions.py` 31 passed, including `test_hod_assignment_notice_failure_is_warned_not_hidden` (fails on the checkpoint-3 `dean.py`, passes now).
  - `python scripts/dean_ui_harness.py` : 52/52 (1280 px and 390 px). Mixed case, 5 ticked teachers (1 send returns False, 1 raises): teacher mail 3 delivered / 2 failed; 3 actions, only for delivered teachers; 3 HOD assignment notices (Alpha 1, Beta 2). Alpha's notice failing: teachers 3, actions 3, Beta notices 2, Dean sees a warning. Also actions off, repeat send skips open actions, identical re-submit replayed, dialogs fit the viewport, Escape/Close, and the open popup printed to a real A4 PDF (text, size, ink, only filtered comments, no page chrome). `--root PATH` runs it against another checkout.
  - Negative controls: checkpoint-3 `dean.py` -> 50/52 (only the HOD-notice warning check); print CSS hiding `<main>` (**my reconstruction**, the checkpoint-1 CSS was not available) -> 0-character PDF, PDF checks fail.
  - Full suite, untouched baseline vs working tree: 165 F + 261 E on both (426 identical test IDs, same type each); baseline 456 passed / 34 skipped, working tree 505 passed / 34 skipped (49 new tests, all pass). Cause: no timetable workbook in the supplied source, so those tests are unverified. The earlier "427 (166 F)" figure was **not reproduced** (426 here; difference not investigated).

## Not verified / open
- Real timetable data, PostgreSQL, real e-mail delivery (mailer not configured here).
- Not exercised: a real OS print dialog (`window.print` is stubbed in the harness), a real SMTP server (recording transport only), PostgreSQL, real timetable data. The harness covers the Performance band page and result popup only.
- Dean Actions list shows rows with `branch_id IS NULL` to every Dean (pre-existing, not changed).
- `console error` for `fonts.googleapis.com` comes from the stylesheet and is blocked in the offline sandbox; it is not new.
- Dark mode: Actions page checked once (readable); other pages only confirmed that the toggle works. The previous agent reported (unverified by me) that on the popup and mail dialog the Send button and the active filter pill are white on `#3B82D6`, 3.93:1, below AA. **Not changed - needs a decision.** Candidate (not applied): `html.dark-mode body.role-dean .rd-btn.p, html.dark-mode body.role-dean .rd-filters button.on { background: var(--tt-blue-deep); border-color: var(--tt-blue-deep); }`. Also reported, untouched: shared Dean `btn-accent` 2.9-3.4:1 and `btn-ok` 2.12:1 (dark) on the inline composer.

## Run the harness
`pip install playwright pdfplumber pypdfium2 && playwright install chromium`, then `python scripts/dean_ui_harness.py` from the repository root. It creates its own temporary SQLite database, never contacts SMTP, and exits non-zero on any failed check.

## Deploy
Static CSS/templates/Python only. No migration.
