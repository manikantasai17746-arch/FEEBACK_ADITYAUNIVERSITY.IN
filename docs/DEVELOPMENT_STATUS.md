# Development Status

**Last updated:** 2026-11-13  
**Repository:** `manikantasai17746-arch/FEEBACK_ADITYAUNIVERSITY.IN`  
**Documentation branch:** `docs/ai-agent-knowledge-base`  
**Source baseline inspected:** GitHub `main` at `78c3cb9baa38b1e0168e9d4be2e63953e80216cd` (latest commit observed during preparation).

## 1. Current state

- Phases 3, 4, and 5B have handoff/verification documentation in the repository.
- Phase 6 is in planning / Checkpoint 0 review. No Phase 6 application implementation was started in the inspected baseline.
- Invalid login submissions from the landing-page role modal now re-render the landing page, reopen the selected modal, preserve non-secret identifiers, and show all flashed messages in that modal. No schema change.
- Focused verification: `pytest -q tests/test_app.py -k 'invalid_landing_modal_login or admin_login_rejects_bad_credentials or admin_login_success'` — 7 passed.
- Teacher Assignment Setup now includes a read-only, teacher-scoped eligible-student preview with search and pagination beside each editable room field. It reads active `session_students` records only and does not change room, assignment, student, or feedback data.
- Preview verification: focused assignment setup tests — 4 passed; JavaScript syntax check passed. The browser UI module was not runnable in this environment because its Playwright/Chromium dependency was unavailable.
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
