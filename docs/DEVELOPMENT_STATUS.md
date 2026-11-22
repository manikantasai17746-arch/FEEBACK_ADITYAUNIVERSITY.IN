# Development Status

**Last updated:** 2026-11-22  
**Repository:** `manikantasai17746-arch/FEEBACK_ADITYAUNIVERSITY.IN`  
**Documentation branch:** `docs/ai-agent-knowledge-base`  
**Source baseline inspected:** GitHub `main` at `78c3cb9baa38b1e0168e9d4be2e63953e80216cd` (latest commit observed during preparation).

## 1. Current state

- Dean Performance now filters faculty cards, totals, score bands and follow-ups by feedback category (All, Main Faculty, Lab Faculty or Second Faculty).

- Phases 3, 4, and 5B have handoff/verification documentation in the repository.
- Phase 6 is in planning / Checkpoint 0 review. No Phase 6 application implementation was started in the inspected baseline.
- Invalid login submissions from the landing-page role modal now re-render the landing page, reopen the selected modal, preserve non-secret identifiers, and show all flashed messages in that modal. No schema change.
- Focused verification: `pytest -q tests/test_app.py -k 'invalid_landing_modal_login or admin_login_rejects_bad_credentials or admin_login_success'` — 7 passed.
- Teacher Assignment Setup now includes a read-only, teacher-scoped eligible-student preview with search and pagination beside each editable room field. It reads active `session_students` records only and does not change room, assignment, student, or feedback data.
- Preview verification: focused assignment setup tests — 4 passed; JavaScript syntax check passed. The browser UI module was not runnable in this environment because its Playwright/Chromium dependency was unavailable.
- HOD Pending submissions now has a client-side search box (emp ID, name, subject); see `docs/PHASE_HANDOFFS/PENDING_SEARCH_HANDOFF.md`. No schema change. Verified: `tests/test_pending_search.py` (2 passed) plus `test_pending_all_categories.py` and `test_recent_forms_search_close.py` pass; `test_deadline_and_pending.py::test_hod_forms_displays_saved_deadline` fails identically on untouched HEAD (pre-existing). Run with a stub for `pyotp`, which was not installable in the build sandbox; browser UI not run.
- HOD Form Management rows now have a "Change deadline" dialog with "This form only" (existing `/hod/forms/<id>/deadline` route) and "All Phase N forms" (new `/hod/forms/deadline-phase` route; draft/published only, closed untouched) and show the deadline as formatted local time; see `docs/PHASE_HANDOFFS/FORM_DEADLINE_CHANGE_HANDOFF.md`. No schema change. Verified: `tests/test_form_deadline_change.py` (6 passed); the previously failing `test_hod_forms_displays_saved_deadline` now passes; full run (excluding authenticator/lost-device/recovery tests, which need the real `pyotp`) shows no failure that is not also present on the previous HEAD. Browser UI not run.
- HOD My Actions rebuilt: stat cards, "Assigned to me by Dean" / "Raised by me for Dean" tabs, status chips, discussion thread, and a new "+ New action for Dean" form (`/hod/actions/new`; teacher/form optional and re-checked against the HOD's own departments, 403 otherwise). Buttons: Send reply, Start working (Open to In progress, Dean-assigned only), Mark as resolved (the Dean closes). Migration v29 is additive: `improvement_actions.raised_by_role`, `improvement_actions.closed_at` (`completed` with no `closed_at` = resolved, awaiting confirmation; existing completed rows are back-filled as closed), `improvement_action_log.kind`. Dean side: saving an action as `completed` closes it, a typed note is logged as a reply, `dean.action_update` is audited, and the Dean list marks HOD-raised actions. Every HOD reply/status change/raise is audited. No e-mail is sent to the Dean for HOD-raised actions (open decision). Verified: `tests/test_hod_raise_action.py` (14 passed); full suite 907 passed, 9 failed, same 9 failures as the previous HEAD (authenticator QR/lost-device/auto-publish tests; `pyotp` was stubbed because it could not be installed). Browser-checked at 1280px and 375px.
- Dean > Actions > Send feedback review requests: each row's "View result" now opens the existing Dean result popup (`dean.performance_result`) over the page instead of navigating to the full-page report. The link carries both `teacher_id` and `form_id` (`data-result-url`), so a Main/Lab/Second Faculty form of the same subject opens its own form; the `href` to `dean.performance_detail` stays as the no-JavaScript fallback. The dialog host moved into a shared partial (`_dean_result_dialog_host.html`, placed outside the review `<form>`), included by both Faculty Performance and this page, and both load `static/js/dean-performance.js`. Shared JS tweaks: focus returns to the clicked link with `preventScroll` (scroll position is kept), and only the latest click may open the popup. No schema, scoring or route change. Verified: `tests/test_dean_bulk_result_popup.py` (7 passed), `tests/test_dean_performance_interactions.py` (32 passed), plus a real-Chrome run at 1280x560 and 390x844 (84 checks: correct form per row incl. an ACTION OPEN row, draft letter/priority/due date/tick/scroll preserved, no POST or navigation, x/Close/Escape/Print, focus return, no-JS fallback, Faculty Performance popup unchanged). Full suite in this sandbox: 803 passed, 156 failed, 14 errors; all failures/errors are authenticator/login tests that need the real `pyotp`/`segno` (not installable here; `pyotp` was stubbed outside the repo) and the same set fails on the untouched code.
- The documentation branch was created from the observed `main` commit. Before merging, check whether `main` has advanced.

## 2. Completed phase evidence

### Phase 3 — Teacher Assignment Setup
Source: `PHASE_3_HANDOFF.md`.
- Teacher setup wizard for subjects/sections, room configuration, lab availability, review/save.
- Reuses `teacher_subject_settings`; migration v21 adds `lab_status` and index.
- No-Lab guard prevents setting No Lab while a confirmed lab-faculty assignment exists.
- Handoff reports 51 service tests and 5 browser/UI tests passed in that phase.
- Historical full-suite comparison: untouched ZIP 187 passed, 11 failed, 12 errors; modified tree 243 passed, 11 failed, 12 errors, with same failing/erroring set.

### Phase 4 — Second Faculty Assignment
Source: `feedback_system/PHASE_4_HANDOFF.md`.
- Audited and extended existing Second Faculty module.
- Assignment is per session and identified by assignment row ID.
- Assignment/replacement requires Lab Available; No Lab or unconfigured lab blocks assignment.
- Soft removal preserves assignment history.
- Handoff reports 52 unittest tests passed and a Chromium smoke check at 1280px and 375px.
- Handoff says pytest/full suite and several HOD/Admin/UI checks were not run in that phase.

### Phase 5B — Feedback categories and safety verification
Source: `feedback_system/PHASE_5B_VERIFICATION_REPORT.md`.
- Verifies Main, Second, and Lab ownership and availability matrix.
- Uses shared category scope/eligibility and publication snapshot.
- Reports 12 Phase 5B verification tests and 52 Phase 4 tests passed.
- Follow-up safety fixes add 9 regression tests, fix comment-order correlation, and prevent legacy status routes from moving submitted category forms back to draft.
- No migration was added in the Phase 5B safety follow-up; report says schema v23/v24 were untouched.
- Full pytest suite, PostgreSQL concurrency, and some browser checks remained pending in that report.

## 3. Verified test baseline + Module A (2026-11-13)

| Command | Result |
|---|---|
| Full pytest after baseline test fixes | **334 passed, 1 skipped, 0 failed, 0 errors** |
| `tests/test_hod_feedback_main.py` | **7 passed** |

Baseline fixes were tests-only (password-change gate, HOD onboarding seed, shared-DB pollution, v21 assertion, tile text). Module A adds HOD Main Faculty Feedback (`hod_feedback` blueprint). Schema remains **v24**.

Handoffs: `docs/PHASE_HANDOFFS/TEST_BASELINE_HANDOFF.md`, `docs/PHASE_HANDOFFS/MODULE_A_MAIN_FEEDBACK_HANDOFF.md`.

## 3c. Module C — Lab Feedback + HOD nav (2026-11-13)

- Lab module on `hod_feedback` blueprint; only Lab Available sessions.
- HOD nav redesign + Overview category counts.
- Tests: `test_hod_feedback_lab.py` 8 passed. Schema v24.
- Handoff: `docs/PHASE_HANDOFFS/MODULE_C_LAB_AND_NAV_HANDOFF.md`.

## 3d. Module B + Results tabs (2026-11-13)

- Second Faculty HOD module; results tabs Main/Second/Lab; completion & scoring via scoring.py.
- Tests: test_hod_feedback_second_and_results.py 8 passed.
- Handoff: docs/PHASE_HANDOFFS/MODULE_B_RESULTS_HANDOFF.md

## 3e. Category PDF reports (2026-11-13)

- Per-category HOD PDF downloads at `/hod/results/{main,second,lab}.pdf`.
- Reuses reports.py; filters match results UI; HOD scope only.
- Tests: test_hod_category_pdf.py 5 passed.
 
## 3f. Latest reported test baseline from earlier Phase 6 planning notes

The latest Checkpoint 0 inspection report supplied during planning reports:

| Test command | Reported result |
|---|---|
| Phase 5B verification + Phase 5B safety fixes + Second Faculty Phase 4 unittest modules | 73 tests, OK |
| Full pytest suite | 315 passed, 12 failed, 12 errors |

The Checkpoint 0 report says 12 failures/errors appear around login requests returning 302 where tests expect 200/403, including `test_app.py`, `test_multi_round.py`, `test_teacher_scoping.py`, `test_hod_dean_second_faculty.py`, and `test_photos_phase1.py`. It suspects first-login/onboarding redirects but explicitly has **not confirmed the root cause**.

It also reports a stale assertion in `test_assignment_setup_phase3.py::test_migration_v21…`: the test expects schema version 21 while current schema is reported as v24.

These results are reported, not independently rerun as part of this documentation task. Do not call the failures harmless. Re-run and investigate before claiming a current baseline.

## 3f. Final verification (2026-11-13)

- Full suite re-run after Overview assertion fix (accepts "Overview" on HOD home).
- Behavioral checks: same person main+second, multi-assignment second employee,
  main submission does not touch second, duplicate prevention, role gates, anonymity view.
- Migration on populated DB copy: v24 → v24, **no data loss**.
- Browser: HOD login rendered at mobile (~375) and desktop (~1280); module routes
  verified HTTP 200 under HOD session in earlier run (server-side).
- Deployment: docs/DEPLOYMENT.md

## 3g. Student Authenticator (TOTP) — Checkpoint 1 (schema and crypto core)

- Status: implemented in working copy; awaiting user's local `pytest` run and approval for Checkpoint 2.
- Schema: migration **v25** (additive): `student_authenticators`, `student_backup_codes`, `feedback_forms.auth_required` (default 0; existing forms backfilled to 0).
- Code: `feedback_system/authenticator.py` (service only), `services.save_form` rule (new forms ON, explicit OFF allowed, lock once non-draft).
- Tests: `tests/test_authenticator_core.py`, 47 cases. Sandbox harness result: 47 passed (stand-in pytest/pyotp; see handoff §8). Full suite and PostgreSQL not run here.
- Migration 17 is present; an earlier "missing v17" report was incorrect.
- Handoff: `docs/PHASE_HANDOFFS/AUTHENTICATOR_CP1_HANDOFF.md`.

## 3h. Authenticator recovery — all-roles upgrade, Checkpoint 1 (service layer) — 2026-11-17

- Status: service layer done and tested; **documentation complete; Checkpoint 2 not started and awaiting explicit approval.**
- Scope: `feedback_system/authenticator.py` and `feedback_system/mailer.py` only, plus `tests/test_authenticator_recovery_cp1.py`. No routes, templates, schema (still v25) or data changes. Nothing is user-visible yet.
- Added: backup-code count and regeneration, recovery verifier under its own `otpreset:` rate-limit key, lost-device reset, `security_notice` e-mails (5 events, no secrets).
- Rule verified and tested: a normal password reset (TOTP or backup code) never removes or disables the authenticator; only lost-device recovery and the admin reset do.
- Tests (2026-11-17, review sandbox, real pyotp, SQLite): `pytest tests/test_authenticator_recovery_cp1.py tests/test_authenticator_core.py tests/test_authenticator_notices_stepe.py tests/test_authenticator_setup_stepb.py` — 119 passed. Earlier session reported full suite 633 passed excluding `tests/test_student_default_password.py` (cannot import: `TEST_STUDENT_PASSWORD` missing from `tests/helpers.py`; pre-existing, unfixed); not re-run for this documentation step. PostgreSQL not run.
- Pending / not implemented: session invalidation after reset and its v26 migration (not written, not approved; schema is v25); the Admin lost-authenticator recovery script (procedure documented only).
- Open decisions: email fallback for users with no authenticator, rollout/enforcement timing, fixing the admin reset-password route that does not restrict target role (outside this scope).
- Guide: `docs/AUTHENTICATOR_RECOVERY.md`. Handoff: `docs/PHASE_HANDOFFS/AUTHENTICATOR_CP1_HANDOFF.md` §15.

## 3i. Authenticator all-roles upgrade, Checkpoint 2 (enrollment) — 2026-11-18

- Status: implemented and tested; **Checkpoint 3 not started, awaiting explicit approval.** No migration (schema v25).
- Added: enrollment for Student/Teacher/HOD/Dean/Admin with password re-entry (students keep the e-mailed code), QR setup, one-time backup codes, `/account/security`, per-role enforcement with grace dates (`AUTHENTICATOR_ENFORCE_ROLES`, `AUTHENTICATOR_GRACE_UNTIL`). Default enforcement is students only (unchanged). Admin enforcement is disabled until Admin recovery exists.
- Tests (2026-11-18, SQLite, real pyotp/segno): full suite excluding `tests/test_student_default_password.py` — 675 passed (633 + 42 new); that module still cannot be imported (pre-existing). Two Step B/E test files were updated for the new password step. PostgreSQL not run.
- Still pending / not implemented: session invalidation and migration v26 (not written, not approved); Admin lost-authenticator recovery script; password recovery and lost-device recovery (Checkpoint 3); admin reset for teacher/HOD/Dean (Checkpoint 4).
- Handoff: `docs/PHASE_HANDOFFS/AUTHENTICATOR_CP1_HANDOFF.md` §16.

## 3j. Authenticator all-roles upgrade, Checkpoint 3 (lost-device recovery) — 2026-11-18

- Status: implemented and tested; **Checkpoint 4 not started, awaiting explicit approval.** No migration (schema v25). Normal password reset unchanged.
- Added: `/lost-phone` for Student/Teacher/HOD/Dean (ID + current password + one backup code); Admin recovery by verifying both registered recovery e-mails (`/admin/lost-authenticator`); atomic once-only removal of the authenticator and all backup codes; forced re-enrollment at next sign-in for every role (marker = `pending` row with `enrolled_at` set); per-account and per-IP limits; audit entries; `lost_device_reset` e-mails.
- Tests (2026-11-18, temporary SQLite database, real pyotp/segno): `pytest tests --ignore=tests/test_student_default_password.py` — **714 passed** (675 + 39 new), exit code 0, in a second full run. The first full run had one failure, `tests/test_assignment_setup_ui.py::test_t32_loading_error_and_success_states`: a timing-dependent browser test that also fails about 1 run in 14 on the untouched original zip, and passed on re-run (alone and with its whole file). Unrelated to this work, not fixed. `tests/test_student_default_password.py` still cannot be imported (`TEST_STUDENT_PASSWORD` missing; pre-existing).
- Pending / not implemented: session invalidation and migration v26 (not written, not approved; schema is v25); Admin enforcement (`ADMIN_ENFORCEMENT_ALLOWED=False`, owner decision); authenticator-based password reset; admin reset of teacher/HOD/Dean authenticators (Checkpoint 4). PostgreSQL and a live mail server not tested.
- Handoff: `docs/PHASE_HANDOFFS/AUTHENTICATOR_CP1_HANDOFF.md` §17.

## 3k. Forgot password with authenticator, Checkpoint A (2026-11-19)

Owner decisions: authenticator code first, then backup code, then (Checkpoint B, not built yet) a college-mailbox proof to reset a lost authenticator; roles = Student, Teacher, HOD, Dean (not admin); mailbox proof = Microsoft sign-in first, emailed code as fallback.

**Done (Checkpoint A):** `/forgot-password` and `/forgot-password/reset` (`auth.py`, `_check_reset_code`). An account with an ACTIVE authenticator resets its password with an app code or an unused backup code; no email is sent and the authenticator and other codes stay (uses `verify_recovery_code`, key `otpreset:<id>`, replay-safe). Accounts without one keep the emailed 6-digit code. Same page and message for unknown, enrolled and not-enrolled IDs; one error message for every failure including lockout. HOD and Dean added to the page and "Forgot password?" links added to their login pages. Admin unchanged.
**Tests:** `tests/test_forgot_password_authenticator.py` (new); `test_normal_password_reset_keeps_the_authenticator` in `test_authenticator_lost_device_cp3.py` rewritten (it expected an emailed code for an enrolled student). Full suite: 742 passed.
**Not done (Checkpoint B):** no phone and no backup codes, using Microsoft sign-in or emailed code to remove the authenticator, then new password, new QR, confirm code, new backup codes. This reverses the Checkpoint 1 rule "never email alone as an authenticator reset" for non-admin roles; needs the owner's approval of the design first.
**Deploy:** no migration, no new environment variable.

## 3l. Lost-device recovery, Checkpoint B1: recovery lock engine (2026-11-19)

Engine only; no route uses it yet (B2 wires the new e-mail-code flow and the existing `/lost-phone` page, B3 adds Microsoft/Google, B4 docs and regression).
**Done:** `feedback_system/recovery_lock.py`. 5 wrong recovery codes within 15 minutes lock lost-device recovery for 2 hours (`reclock:<id>` in `login_attempts`), auto-unlock by time. Student/Teacher/HOD/Dean only; every function is a no-op for Admin. Separate keys, so sign-in, the sign-in authenticator check and forgot-password (Checkpoint A) are never blocked. Audit: `user.recovery_locked` (at the 5th wrong code), `user.recovery_blocked` (first refused attempt of a lock), `user.recovery_unlocked` (written lazily on the next attempt after expiry, with the expiry time; there is no background job). IP caps `recstart-ip` (10/15 min) and `reccode-ip` (15/h); per-account e-mail cap `recmail` (3/h).
**Settings (all optional):** `RECOVERY_MAX_ATTEMPTS=5`, `RECOVERY_LOCK_SECONDS=7200`, `RECOVERY_FAILURE_WINDOW_SECONDS=900`, `RECOVERY_IP_START_MAX=10`, `RECOVERY_IP_CODE_MAX=15`, `RECOVERY_MAIL_MAX=3`.
**Known trade-off:** someone who knows an ID can lock that account's lost-device recovery for 2 hours (never its login); per-IP caps limit this.
**Tests:** `tests/test_recovery_lock_b1.py`. **Deploy:** no migration; settings optional.

## 3m. Lost-device recovery, Checkpoint B2: no phone and no backup codes, e-mailed code (2026-11-19)

**Done:** `/lost-phone/no-codes` and `/lost-phone/no-codes/verify` in `lost_device.py` for Student, Teacher, HOD, Dean (Admin excluded and unchanged). An e-mailed 6-digit code goes to the confirmed address only if the account exists, has an ACTIVE authenticator, is not recovery-locked and is under the 3-per-hour mail cap; the browser sees the same page either way. Code + new password in one submit; on the right code the authenticator and all backup codes are removed atomically (`apply_lost_device_reset`), the password is changed, a notice e-mail is sent (`lost_device_reset_mailbox`), and the next sign-in is forced into authenticator setup, which asks for the confirming code and issues 10 new backup codes. Wrong e-mailed codes count toward the 2-hour recovery lock (B1). The existing `/lost-phone` backup-code page now also uses the lock and the per-IP code limit (wrong passwords there do not count toward the 2-hour lock). Checkpoint A files are unchanged. Admin recovery is unchanged.
**Tests:** `tests/test_lost_device_mailbox_b2.py` (new); `test_recovery_lock_b1.py` updated (only `lost_device.py` may use the lock). **Deploy:** no migration, no new environment variable. **Next:** B3 Microsoft/Google sign-in recovery.

## 3n. Dean UI redesign (2026-11-22) — status: implemented through checkpoint 4, review pending

Dean pages use the new theme (`static/css/dean-theme.css`) and nav (`base.html`, Dean branch). Student Complaints is in the nav on all eight pages (desktop and the mobile menu); the dashboard shortcut, text-size controls, theme toggle, Profile, Security and Logout are kept. No schema change, no new dependency. Details, tests and limits: `docs/PHASE_HANDOFFS/DEAN_UI_HANDOFF.md`.

- HOD scoping is now timetable-aware (`dean._hod_owns`): two HODs in one department and semester see only their own teachers on Performance, the score bands and the HOD name on each card; the Dean's bulk "review request" assigns the action to the owning HOD (it previously chose the first HOD of the department). The scoring rows now also carry `timetable_id` (additive).
- Dashboard and Publication list one row per (department, HOD); View opens `dean.performance?branch_id=&hod_id=#teachers`. Performance has the `#teachers` anchor and a filtered-view banner.
- Secondary pages: sub-navigation and tables no longer widen the page at 390px.
- Result popup, Mail teacher dialog, "Email HOD about ticked teachers", optional improvement actions from both mail flows and the print/PDF fix are implemented on the Performance band page (`dean.performance_result`, `dean.performance_bands_send`, `dean.performance_bands_email_hod`, `static/js/dean-performance.js`, `_dean_result_dialog.html`). Teacher mails, HOD assignment notices and HOD review mails are counted separately.
- Checkpoint 4 (2026-11-22, rebuilt from the previous agent's notes because its checkpoint-4 source was never attached): when an action is created but its HOD's assignment e-mail fails, `performance_bands_send` now returns `hod_notify_failed` and a warning (level `warning`); the action is kept. New `scripts/dean_ui_harness.py` (Chromium + recording mail transport + A4 PDF inspection) and pytest test `test_hod_assignment_notice_failure_is_warned_not_hidden`.
- Tests (2026-11-22, Python 3.12.3, **real pytest 9.2.0**, real pyotp/segno/psycopg/pglast, Flask 3.1.3; command `python -m pytest -p no:cacheprovider -rfE --junitxml=...` from the repository root, untouched baseline and working tree run separately):
  - `tests/test_dean_hod_scope.py` 18 passed; `tests/test_dean_performance_interactions.py` 31 passed (both new, synthetic data, no workbook).
  - Browser/print harness: 52/52 (26 at 1280 px, 26 at 390 px). Negative controls: on the checkpoint-3 `dean.py` it scores 50/52 (only the HOD-notice warning check fails at both widths); on a print CSS that hides `<main>` (my reconstruction of the old rule, not the original) the PDF has 0 characters and the PDF checks fail.
  - Full suite, untouched baseline: 165 failed, 456 passed, 34 skipped, 261 errors (916 tests). Working tree: 165 failed, 505 passed, 34 skipped, 261 errors (965 tests). The failing/erroring test IDs are **identical** (426, same failure/error type per test); the 49 additional tests exist only in the working tree and all pass. Cause of the shared failures: the supplied source has no operational timetable workbook, so fixtures that need imported students/class sessions find nothing (`TypeError: 'NoneType' object is not subscriptable`, `RuntimeError: No class_sessions in test DB; AUTO_IMPORT required`, etc.). Those tests are **unverified**, not passing.
  - Correction: the earlier note "427 failing (166 F + 261 E)" was not reproduced; this environment gives 426 (165 F + 261 E) on both trees. The one-test difference is unexplained and was not investigated; the baseline and working-tree sets match each other either way.
- Not verified: anything needing real imported timetables (`test_dean_action_assignment` - 6 failures that also fail on the untouched baseline, `test_hod_timetable_scope`, `test_student_portal_theme`, authenticator/photo suites), PostgreSQL, real e-mail delivery (mailer replaced by a recording transport), a real OS print dialog (`window.print` is stubbed; the PDF is produced with Chromium `page.pdf()` after the real button adds `rd-printing`).
- Open decision, not applied: dark mode, the Send button and the active comment-filter pill (white on `#3B82D6`) measure 3.93:1, below WCAG AA 4.5:1. Candidate CSS is in the Dean UI handoff.

## 4. Phase 6 — planned, not implemented

Proposed modules:
1. Main Faculty Feedback.
2. Second Faculty Feedback.
3. Lab Feedback.
4. Overview/statistics and HOD navigation.
5. Regression tests and handoff.

Proposed checkpoints:
- Checkpoint 0: inspection and plan only.
- Checkpoint 1: Main Faculty module.
- Checkpoint 2: Second Faculty module.
- Checkpoint 3: Lab module.
- Checkpoint 4: overview and navigation.
- Checkpoint 5: regression tests and handoff.

The Phase 6 Checkpoint 0 report says no source files were changed. It proposed a new `feedback_system/hod_feedback.py` blueprint, templates, scoped CSS, CSP-safe JS, and tests. These are proposals, not implemented functionality.

### Decisions recorded in the Phase 6 planning discussion

- Make Main form conflict behavior consistent across new and legacy creation paths; inspect compatibility before changing.
- Backward-compatible optional `phase=None` support in `category_forms.create_form` was recommended so Phase 1/2 are stored and validated; inspect and verify before implementation.
- Reuse the existing HOD question-set system; do not silently bypass the rule that questions come from question sets.
- Deadline field is optional.
- New Phase 6 pages are HOD-only; preserve existing Admin functionality.
- HOD eligibility preview may show eligible student names/rolls only in the authorized preview; never associate identities with anonymous answers.
- Test SQLite concurrency; document PostgreSQL concurrency if untested.
- Treat 315/12/12 as a temporary reported baseline; preserve 315 passes, add no failures/errors, and investigate login redirects rather than assuming they are test-harness-only.
- Read Phase 3 and Phase 4 handoffs fully and relevant Phase 5 docs before coding.

**Approval gate:** Do not begin Checkpoint 1 until outstanding handoff review and baseline verification are complete and the user explicitly approves Checkpoint 1. Do not start later checkpoints without separate approval.

## 5. Known unresolved items

- Root cause of login/onboarding-related pytest failures/errors is unconfirmed.
- Stale migration-v21 test assertion needs a deliberate test-maintenance decision.
- PostgreSQL concurrency behavior has not been established by the cited Phase 5B report.
- Some historical phase reports record tests that could not be run in their original environments.
- A standalone Phase 5A handoff was not found at the expected path during repository inspection; Phase 5A details are also described in Phase 5B verification and current source.

## 6. How to update this file

After each approved checkpoint/phase, update actual commit/branch, implemented files/routes/schema changes, exact tests and results, known failures and confirmed/unconfirmed causes, migration/deployment notes, approvals, and next checkpoint. Never replace an old test result without recording command, environment, and date.

## Authenticator and feedback code (C1.1 to Final)

Completed, each as a separate patch with focused tests:

- C1 / C1.1 / C1.2: encrypted TOTP secrets, replay protection, backup codes, atomic lockout counter, H1/H2/M1/M3 fixes.
- C1.1 default password: detection from the stored hash; students cannot choose the shared default (change or reset). **Revised:** students are no longer forced to change the shared default (see "Student sign-in flow" below); the Password page stays available.
- Step A: a confirmed student e-mail is locked; only an administrator can clear it.
- Step B: authenticator setup after the e-mail step, proved by a code e-mailed to the registered address; secret shown only with that proof, never stored in the session.
- Step C: the authenticator code is required before a form marked "Authentication Required" can be opened or submitted; verified for the login session only.
- Step D: the choice is on the admin builder, the admin bulk screen, the HOD builder and the HOD category screens; default ON; locked once published.
- Step E: administrator reset of a student's authenticator; e-mail notices when an app is set up or reset (no secret or code in any notice).
- Final: decode errors fail closed; digest-keyed cipher cache; concurrent first-time enrollment; missing-key warning; default-on tests for bulk creation, `create_forms_for_session`, and the legacy HOD save.

Known limits (not yet closed):

- A student still on the shared default can be taken over by anyone who knows it (the change is now optional, by decision). The college-email rule, the Microsoft tenant check and the authenticator reduce this; they do not remove it.
- Pending setups never expire (needs a migration).
- Admins can clear a student's e-mail but not a teacher's.
- Verified state lasts for the login session (logout, a new login, 30 minutes idle, or 8 hours).

Verification status: each patch was applied to a clean repository with `git apply` and checked with `git diff --check`. The sandbox runner does not run pytest, so the full suite must be run on the developer's machine. PostgreSQL migration execution is not verified.

## Student sign-in flow (current)

1. Student logs in with roll number + the shared default password. No forced password change; the Password menu always allows an optional change. A student still cannot choose the shared default as the new password.
2. First login, no e-mail on file: **Continue with Microsoft** (students never see Google). The Microsoft account must equal `<roll in lower case>@STUDENT_EMAIL_DOMAIN` (default `adityauniversity.in`), e.g. `26b21cs073@adityauniversity.in`. Microsoft for students only works when `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET` and a tenant GUID in `MICROSOFT_TENANT_ID` are set.
3. **Try another way**: a box for the college e-mail only (same `<roll>@domain` rule); a 6-digit code is sent to it.
4. After either proof, the authenticator QR code is shown straight away (no second code, no password prompt). A student who already has the college e-mail on file can use Continue with Microsoft instead of the e-mailed code.
5. Admin keeps two recovery e-mails; student, teacher, HOD and Dean have one e-mail. Teachers keep Google and Microsoft.

Set `STUDENT_EMAIL_DOMAIN=` (empty) to switch the college-email rule off.

## Student portal restyle and ordering (2026-11-20)

Verified changes (branch `teacher-ui-landing-style`):
- `static/css/student-theme.css` (new, loaded only for role `student` in `base.html`, every rule scoped to `body.role-student`). Same fonts, sizes, weights and tokens as `teacher-theme.css`; light and dark via the existing `html.dark-mode` switch. Covers My Feedback, the feedback form, Notifications, Change password and the email / authenticator / backup-code setup pages.
- `base.html`: student nav gets `active` states and the sun/moon theme toggle.
- `student_dashboard.html`: page head with eyebrow and info chips. Stat-tile markup is unchanged.
- `student_notifications.html`: rewritten to extend `base.html`. It previously extended a template (`student_base.html`) that does not exist in the repo, so the page could not render.
- Students see one neutral "Waiting for your feedback" list (no Main/Lab/Second labels). By the owner's decision, the "Your feedback history" section below it does have Main / Lab / Second tabs (`?cat=`), so history shows the category. Lab and Second forms stay locked until the earlier form of the same session is submitted (`services.feedback_locked`, enforced in `student.view_form` and `services.submit_feedback`). A missed form that has closed does not block later ones.
- Tests: `tests/test_student_portal_theme.py` (new) and `tests/test_hod_feedback_lab.py::test_lab_submission_does_not_complete_main` updated for the order rule.

Limitations: the full suite was run with stand-in `pyotp`/`segno` modules because package installs were unavailable; 3 authenticator tests fail identically with and without these changes. The authenticator QR and backup-code pages were not screenshotted (they reuse the same scoped classes).

## Recovery pages, setup redirect and loading screen (2026-11-20)

- `static/css/recovery-theme.css` (new): restyles the signed-out forgot-password, reset-password, lost-phone and admin recovery pages (body class `recovery`, same fonts/sizes/tokens as the teacher and student themes). Templates only add the stylesheet link and body class; forms and routes are unchanged.
- `auth.render_backup_codes`: after a student finishes authenticator setup (`reason == 'enrolled'`) the "I have saved my codes" button goes to My Feedback; other roles and later regenerations still go to Account security. The codes are still shown exactly once.
- `static/js/loader.js` + `static/css/loader.css` (new): a spinner screen shown while a student or signed-out page is loading after a link click or form submit. It waits 120 ms (no flash on fast pages), never stays more than 12 s, and is hidden on back/forward. Loaded only for students and signed-out pages.
- Tests: `tests/test_student_portal_theme.py`.

## Star rating on student comments
- A student who writes a comment (text answer) must also give 1-5 stars for it; blank comments need no stars.
- Stored as `comment_stars` on `feedback_responses` and legacy `answers` (migration 26; view `anonymous_answers` now also exposes it). No student id is stored with it.
- Not done yet: showing stars / grouping comments (Positive, Neutral, Needs improvement) for HOD and Dean.

## Landing-page logo animation (2026-11-20)

The public feedback statistics banner displays the approved gold Aditya University logo to the left of the live counts on desktop and above them on phones. The entrance starts when the logo scrolls into view. The [landing-page logo handoff](PHASE_HANDOFFS/LANDING_LOGO_HANDOFF.md) records verification and deployment details.

## Shared sign-in and rotating portal loader (2026-11-20)

The role sign-in URLs render the landing-page form for the requested role. The new loader uses the rotating university logo in the Student, Teacher, HOD, and Dean portals and on signed-out pages. See the [sign-in and loader handoff](PHASE_HANDOFFS/LANDING_LOGO_HANDOFF.md#shared-sign-in-and-rotating-loader).

## Faculty photos on student feedback forms (2026-11-20)

The teacher verification card uses the shared employee photo service for uploaded photos and bundled portraits. Photos display in a square frame, with an initials badge when a portrait is unavailable. See the [faculty photo handoff](PHASE_HANDOFFS/STUDENT_FORM_PHOTO_HANDOFF.md).

## Category headings on student feedback forms (2026-11-20)

An open student form identifies itself as Main Faculty Feedback, Lab Feedback, or Second Faculty Feedback above the faculty name. See the [category heading handoff](PHASE_HANDOFFS/STUDENT_FORM_CATEGORY_HANDOFF.md).

## Loading between student feedback forms (2026-11-20)

Submitting feedback keeps the university loader active through the navigation to the next form. The arriving form displays the loader until it finishes loading. See the [loader handoff](PHASE_HANDOFFS/LANDING_LOGO_HANDOFF.md#loading-between-student-feedback-forms).

## Student feedback notifications (2026-11-20)

Publishing Main Faculty, Lab, or Second Faculty feedback records notices for eligible students. My Feedback and Notifications check for outstanding forms during their final 24 hours and record one reminder per form. See the [notification handoff](PHASE_HANDOFFS/STUDENT_FEEDBACK_NOTIFICATIONS_HANDOFF.md).

## Student Complaints (2026-11-21)

My Feedback offers an optional college issue report after the student completes their eligible published forms. HODs review their departments' complaints, and Deans review complaints across departments. Both can update statuses. See the [Student Complaints handoff](PHASE_HANDOFFS/STUDENT_COMPLAINTS_HANDOFF.md).

## Review and delete imported timetables (2026-11-21)

Admin → Import now lists every imported workbook (file, semester, academic year, import time and what that semester currently holds). **Delete…** removes the imported timetables, class sessions, enrollments, eligibility rows and legacy assignments for that semester + academic year, forgets the file record so a corrected workbook can be imported again, and removes student/teacher accounts that nothing else references. It reuses the HOD/Dean deletion safeguard: a 6-digit code is e-mailed to `DELETE_OTP_EMAIL` (10-minute expiry, 5 attempts) and must be entered on a confirmation page.

Decisions and limits:
- Imports are not tagged per file, so deletion is scoped to semester + academic year; if several files were imported for the same pair, all are removed together (the list warns about this).
- An import is **locked** (no code is sent, deletion is refused) while feedback forms (including archived), submissions, responses, published results, second-faculty/lab assignments, teacher room settings, HOD allocations or complaints depend on it. The check is repeated when the code is submitted.
- No schema change. Accounts are only deleted if absent from `import_data.USER_REFERENCES`; `tests/test_import_delete.py` fails if a new table references `users.id` and is not listed.
- Verified: `tests/test_import_delete.py` (8 tests, pass). Full suite compared against the original zip in an environment lacking `pyotp`, `segno` and `psycopg`: no new failures (authenticator/QR tests fail identically before and after; one browser UI test is flaky in both). PostgreSQL was not exercised; the SQL is portable but untested there.

## First-login order for Teacher / HOD / Dean (2026-11-21)

One-time setup now runs **photo → new password → confirm e-mail → authenticator app → Teacher Assignment Setup (teachers only)**; HOD/Dean use their existing onboarding page as the e-mail step and have no Assignment Setup. `users.first_login_complete` now means "photo step done" for all three roles; HOD/Dean who already finished onboarding are treated as photo-done. Every step page refuses to be opened ahead of an earlier pending step. The authenticator step appears only for roles in `AUTHENTICATOR_ENFORCE_ROLES` (default: students only). No schema change. See the [First-login order handoff](PHASE_HANDOFFS/FIRST_LOGIN_ORDER_HANDOFF.md).

- Tests (SQLite, real pyotp/segno, branch `feature/first-login-order`): `tests/test_first_login_phase2.py` 20 passed, `tests/test_account_setup.py` 10 passed. Full run `pytest tests --ignore=tests/test_student_default_password.py`: 855 passed, 3 failed, identical on untouched `main` (`test_authenticator_lost_device_cp3.py::test_every_failure_looks_the_same_to_the_browser[teacher]`, `test_deadline_and_pending.py::test_hod_forms_displays_saved_deadline`, `test_lost_device_sso_b3.py::test_start_page_shows_buttons_only_when_available`); not caused by this change and not fixed. PostgreSQL and the browser (Playwright) UI tests were not exercised.


## HOD timetable-level scope fix (2026-11-21)

An HOD who claimed only some timetables (`hod_allocations.timetable_id`) was shown department-wide data: the Main Faculty card counted every timetable of the branch (e.g. 111 instead of 19 for CSE Sem 3 T1-General) and the Main/Lab/Second Faculty lists showed every section (2501AI06: 17 sections / 1135 students instead of 3 / 212). Scope was resolved by branch only.

Fix: `security.scoped_timetable_ids()` / `timetable_scope_sql()` return the HOD's claimed timetables (`None` = no restriction for admin/dean and for HODs without allocation rows, so the legacy department-scope fallback is unchanged). Applied to the HOD dashboard counters and category overview (`hod.py`), the Main/Lab/Second list queries, and the per-session/assignment scope checks used by preview and create (`hod_feedback.py`). No schema change.

Not covered (still branch-scoped, follow-up needed): score bands / performance pages, results, comparison and complaint views for HODs.

Tests: `tests/test_hod_timetable_scope.py` (3 pass; fail on the original code). HOD/Dean related suites pass. Full run: same 3 failures as on untouched `main` (listed above); PostgreSQL not exercised.

## HOD Main Faculty create flow (2026-11-21)

New create page `/hod/feedback/main/create` (`main_create.html`, `static/js/hod_feedback_main.js`, `hfc-*` classes in `hod_feedback.css`, routes in `hod_feedback.py`): Select (list "Select" opens it with only that teacher/section, "Show all teachers" lists the subject with nothing selected) -> Preview (eligible students, first 8 per section, "Go to bottom") -> Questions (shared/own question sets, per-form trash, extra questions, Phase 1/Phase 2/Custom name, deadline, instructions, authenticator checkbox, "Edit sets" popup with autosave; editing a shared phase set creates the HOD's own copy via `/hod/api/question-sets/customise-shared`) -> Confirm (Save as draft -> `/hod/forms`, Publish now -> `/hod/` overview). Timetable scope (`security.scoped_timetable_ids`) applies to the list, preview and create. No schema change.

Verified in Chromium (Playwright, seeded HOD with one allocation, CSE Sem 3 T1-General: 19 sections, 7 subjects, 2501AI06 = 3 teachers / 212 students): Select, Show all teachers, Select/Unselect all, Preview, Go to bottom, trash/extras, Edit sets (customise, autosave, Reset, Duplicate, Delete, New set + rename), Phase 2 blocked while Phase 1 is draft/open, Back buttons, draft and publish redirects, out-of-scope session rejected (preview 403, create 409). Only console errors: Google Fonts blocked in the sandbox.

Bugs found by that walk-through and fixed:
- Question sets/presets were embedded in a single-quoted `data-` attribute with `|safe`; an apostrophe ("teacher's", Phase 2) truncated the JSON so the page had no question sets. Now autoescaped in double quotes.
- `app.js` turns every `[data-back]` into `history.back()`; the wizard Back buttons left the page. Renamed to `data-hf-back`.
- `body.role-hod .btn` out-ranked `.hfc-orange/.hfc-outline/.hfc-danger`; selectors raised. `.hfc-banner[hidden]` was overridden by `display:flex`.
- The Edit sets dialog sat under the site header's stacking context; it is moved to `<body>`.
- Preview read `name` that `eligible_students()` does not return; Main preview now looks names up (imported students have name == roll, shown once). Selection order now follows the table.

Tests: `tests/test_hod_timetable_scope.py` (+3: JSON attribute, no `data-back`, preview names), `tests/test_hod_teacher_picker.py` (Main list link assertion updated to the new create page).

Known / not done:
- `lab_create.html` and `second_create.html` also use `[data-back]` (same history.back clash); `api_lab_preview` / `api_second_preview` also return blank names. Out of scope, not changed.
- Closing "Edit sets" clears the per-form trash exclusions (indices can shift).
- Score bands, performance, results, comparison and complaint pages for HODs remain branch-scoped, not timetable-scoped.
- Test environment: `pyotp` and `segno` were not installable here; a local RFC 6238 stand-in for `pyotp` was used outside the repo.

## HOD Recent Forms: search and close-before-deadline (2026-11-21)

- **Search:** the shared Recent Forms table (`templates/_hod_recent_forms.html`, used by the HOD overview and `/hod/forms`) has a live search box. It matches employee ID, subject code, subject name and faculty name, combines with the All/Draft/Open/Closed chips, updates the chip counts, highlights the match, and "Select all drafts" / "Publish selected" only act on visible drafts (checkboxes of hidden rows are cleared). Client-side only; the dashboard still loads its latest 50 forms.
- **Close only before the deadline:** an open form past its deadline shows a disabled "Close feedback" with "Deadline passed — closes automatically". `hod.py` passes `deadline_passed` per form; the page also re-checks every 30 s so a page left open flips the button. `category_forms.close()` and `change_status()` now refuse a manual close after the deadline (`ScopeError('deadline_passed', ..., 409)`), via `services.deadline_passed`. This applies to every caller of those functions, including the Admin status route. The existing lazy sweep (`services.close_expired_forms`) still closes expired forms by itself.
- **Unchanged:** no schema change, no new dependency, Reopen/Publish behaviour.
- **Tests:** new `tests/test_recent_forms_search_close.py` (4 tests). `pytest` could not be installed in the build sandbox (no network), so the new tests and 12 related existing modules were run with a small stand-in runner, identical results before and after the change. Run `pytest -q` on your machine for the real suite. A headless-Chromium check of the page behaviour was run separately.
- **Files:** `feedback_system/category_forms.py`, `feedback_system/hod.py`, `feedback_system/templates/_hod_recent_forms.html`, `feedback_system/static/js/hod_overview.js`, `feedback_system/static/css/hod-overview.css`, `tests/test_recent_forms_search_close.py`.

## HOD Pending submissions: Main, Second Faculty and Lab Faculty forms (2026-11-21)

- The "Feedback form" dropdown on `/hod/pending` (also embedded in the HOD overview "Pending submissions" tab) is now grouped into **Main / Second Faculty / Lab Faculty Feedback**, and each entry shows the faculty name when the form title does not already contain it. The selected form shows its category badge. Previously all categories were listed but looked identical (for example several "Phase 1 · 2501CS10" entries).
- Fix: `services.pending_by_teacher()` for a **second-faculty form** now counts only that form's own `second_faculty_assignment_id`. Before, a session with several confirmed second faculty listed all of them under every second form.
- Files: `feedback_system/hod.py`, `feedback_system/services.py`, `feedback_system/templates/hod_pending.html`, `tests/test_pending_all_categories.py`. No schema change.
- Not changed: the pending-roll PDF and reminder e-mail still use the form title only. (Superseded by the section below.)

## HOD Pending submissions: one e-mail per teacher (2026-11-21)

- Problem: reminders were sent per form, so a teacher who is Main faculty, Lab faculty and/or Second faculty got a separate e-mail (and PDF) for each form.
- Now `/hod/pending/<fid>/email` sends each teacher **one** e-mail with **one** PDF covering every OPEN form (Main / Second / Lab) of the HOD's department where that teacher still has pending students. "Email all teachers" is therefore department-wide and de-duplicated per teacher; the per-row "Email PDF" button sends that teacher's combined e-mail too. A teacher with a single pending form gets the previous single-form wording and PDF.
- Combined PDF: `reports.build_pending_rolls_pdf_combined` (one section per form, roll numbers only). The row "PDF" link previews exactly what the e-mail attaches (open forms); for a closed form it still shows that form only. Reminders remain open-form-only, and the 100-teacher limit now counts teachers, not forms.
- UI (`hod_pending.html`): the button count is the number of distinct teachers, a hint explains the combined e-mail, and each row lists the other forms the e-mail also covers. Existing markup the older tests match is unchanged.
- Closed forms and forms past their deadline are no longer listed on `/hod/pending` (only open, unexpired forms are), are never included in a reminder e-mail, and the e-mail route refuses them (`services.deadline_passed`). A direct `?form_id=` link to a hidden form falls back to an open form; another department's form still returns 403/404. Test: `test_expired_and_closed_forms_are_hidden_and_not_e_mailed`.
- Files: `feedback_system/hod.py` (`_teacher_pending_sections`, `_pending_pdf`, `_email_pending`, `pending`, `pending_pdf`, `pending_email`), `feedback_system/reports.py`, `feedback_system/templates/hod_pending.html`, `tests/test_pending_all_categories.py` (+4 tests). No schema change, no new dependency.
- Tests (2026-11-21, SQLite, temporary stand-ins for `pyotp`/`segno` kept outside the repo because installs were unavailable): `pytest tests --ignore=tests/test_student_default_password.py` gave 885 passed, 3 failed; the 3 failures are the same ones that fail on untouched `main` (`test_authenticator_lost_device_cp3.py::test_every_failure_looks_the_same_to_the_browser[teacher]`, `test_deadline_and_pending.py::test_hod_forms_displays_saved_deadline`, `test_lost_device_sso_b3.py::test_start_page_shows_buttons_only_when_available`); the deadline one was re-run on the original code and fails there too. The final pytest summary line was lost when the run was interrupted, so the counts come from the saved progress marks. PostgreSQL and browser UI tests were not run.
- Limitations: the page now evaluates pending counts for every open form of the department on each render (one `pending_by_teacher` call per open form) to show the combined count; not measured on a large production database. Clicking "Email all" twice still sends a second e-mail per teacher (no sent-history is stored; adding one needs a migration). Timetable-level HOD scope is still not applied on this page (unchanged).
