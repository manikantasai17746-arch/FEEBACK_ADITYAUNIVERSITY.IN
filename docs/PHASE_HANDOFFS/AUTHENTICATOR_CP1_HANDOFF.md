# Student Authenticator (TOTP) — Checkpoint 1 Handoff

**Status:** Implemented in working copy. Awaiting user's local `pytest` run and approval before Checkpoint 2.
**Scope:** Schema and crypto core only. No routes, no templates, no student enrollment UI, no form gating.

## 1. Goal and scope
Provide the service layer for student authenticator (TOTP) 2FA: migration v25, encrypted secret storage, RFC 6238 verification with replay protection and drift window, single-use hashed backup codes, rate limiting, and the per-form `auth_required` flag with its creation and lock rules.

## 2. Source baseline
Uploaded project ZIP `FEEBACK_ADITYAUNIVERSITY_IN-main__26_.zip`. No `.git` directory was present, so no branch or commit can be recorded.

## 3. Decisions applied
- New dependencies approved: `pyotp`, `cryptography`. `segno` deferred to Checkpoint 2 (QR rendering only).
- Encryption key is `TOTP_ENCRYPTION_KEY`, never derived from `SECRET_KEY`.
- Recovery: backup codes plus audited Admin reset (Admin reset is Checkpoint 5). No email-only reset.
- Existing forms: `auth_required = 0`. New forms: `auth_required = 1` set explicitly in `services.save_form`, never by the column default. Explicit OFF must be chosen.
- `auth_required` locked once a form is non-draft.

## 4. Files
| File | Change |
|---|---|
| `feedback_system/schema.py` | Added migration v25 (additive). |
| `feedback_system/authenticator.py` | New service module (no routes). |
| `feedback_system/services.py` | `parse_auth_required`, `AuthSettingLocked`, rule inside `save_form`. |
| `feedback_system/__init__.py` | Config: `TOTP_ENCRYPTION_KEY`, `OTP_MAX_ATTEMPTS`, `OTP_LOCKOUT_SECONDS`. |
| `requirements.txt` | Added `cryptography>=42`, `pyotp>=2.9`. |
| `tests/conftest.py` | Test-only `TOTP_ENCRYPTION_KEY` in the shared app config. |
| `tests/test_authenticator_core.py` | New test module (see §8). |
| `docs/DEVELOPMENT_STATUS.md`, this file | Status and handoff. |

## 5. Migration v25
Additive only. Nothing dropped, renamed, or deleted.
- `student_authenticators(user_id PK → users, secret_enc TEXT, status 'pending'|'active', last_step INTEGER, enrolled_at, updated_at)`
- `student_backup_codes(id PK, user_id → users, code_hash TEXT, used_at, created_at)` + index `ix_backup_codes_user`
- `ALTER TABLE feedback_forms ADD COLUMN auth_required INTEGER NOT NULL DEFAULT 0`
- `UPDATE feedback_forms SET auth_required=0 WHERE auth_required IS NULL` (explicit backfill; existing forms OFF)

PostgreSQL: `pg.to_postgres` rewrites `ADD COLUMN` to `ADD COLUMN IF NOT EXISTS` and identity for `AUTOINCREMENT`. Covered by a test.

Migration 17: present in `schema.py` (“Improvement actions”). An earlier report claimed it was missing; that was a faulty text-parsing check and is withdrawn.

## 6. Security decisions implemented
- **Encryption at rest:** Fernet `MultiFernet`. `TOTP_ENCRYPTION_KEY` may list several keys, newest first, for rotation. Fails closed (`AuthenticatorConfigError`) when the key is missing or malformed. Error text never echoes key material. Decryption failures never return partial data.
- **Pending secret:** stored encrypted; plaintext is returned only from `begin_enrollment` for one-time display.
- **Active authenticator replacement:** refused by `begin_enrollment` (`AuthenticatorStateError`). Requires a verified current code (Checkpoint 2).
- **Verification:** RFC 6238, SHA-1, 6 digits, 30 s step. Window ±1 step. All candidates evaluated (constant-time comparison per candidate).
- **Replay:** `consume_step` is a compare-and-set `UPDATE … WHERE last_step < ?`. Under concurrency only one request succeeds. Enrollment also records its confirming step.
- **Backup codes:** 10 per student, 12 characters (~59 bits), shown once in Checkpoint 2. Stored as scrypt hashes via `hash_password`, so they do not depend on the encryption key and survive key rotation. Single use via `used_at IS NULL` compare-and-set.
- **Rate limiting:** `otp:<user_id>` key on the shared DB-backed `login_attempts` table (hashed key). Default 5 failures per 900 s window, lock 900 s (configurable). Lock is checked before any code verification.
- **Failure reasons** are internal (`locked`, `not_enrolled`, `invalid`, `totp`, `backup`); nothing is logged.
- **No secret or code is logged.** No audit calls in Checkpoint 1 (no request context).

## 7. Form rules (`services.save_form`)
- Create without a value → `auth_required = 1`. Explicit `0`/`false`/`off`/`no` → `0`. Explicit `1`/`true`/`on`/`yes` → `1`.
- Any other value → `ValueError` (prevents a typo from silently disabling protection).
- Update not mentioning the key → column untouched.
- Update changing the value on a non-draft form → `AuthSettingLocked`. Re-submitting the same value is allowed.
- Covers every creation path, because all of them call `save_form` (admin save, bulk, legacy HOD save, `category_forms.create_form`, `create_forms_for_session`, HOD category modules). Checkpoint 4 must send an explicit value from each UI checkbox.

## 8. Tests
New module `tests/test_authenticator_core.py`, 47 test cases:
- Encryption: round-trip, tamper, missing/malformed/wrong key, key rotation, `SECRET_KEY` independence.
- TOTP: RFC 6238 vectors (T=59 → 287082; T=1111111109 → 081804), drift ±1 accepted, ±2 rejected, replay rejected, malformed input rejected.
- Enrollment: ciphertext-only storage, wrong-code keeps pending, activation blocks same-code replay, active authenticator not replaceable, not-enrolled path.
- Second factor: accept-once, replay, atomic monotonic consume.
- Backup codes: shape, hashed at rest, single use, normalisation, replacement, via second-factor check.
- Rate limiting: lock blocks correct code, clear unlocks, counted in shared table.
- Migration v25: additive, PostgreSQL translation, populated legacy database keeps every form and sets it OFF.
- Form rule: default ON on create, explicit OFF, strict parsing, draft edit, published lock, legacy edit stays OFF.

### Results actually obtained
This sandbox has no `pytest` and no `pyotp`, and has no network access to install them.
- Test module executed through a sandbox-only harness (outside the project, not committed) against the real `create_app`, real SQLite, real `services`/`db`/`authenticator` code. The harness uses a stand-in `pytest` and a stand-in `pyotp`. The `pyotp` stand-in was checked against both RFC 6238 vectors before use.
  - Result: **PASSED 47, FAILED 0.**
- Mutation check: defaulting new forms to OFF made 2 tests fail, so the default and lock tests are real guards.
- Existing app-only `save_form` tests (`tests/test_phase_rules.py`) under the same harness: **6 passed, 0 failed.**
- **Not run:** the full existing suite (requires pytest and client fixtures this harness does not provide), and any PostgreSQL test. The user's local `pytest` run is required.

## 9. Known limitations and risks
1. `pyotp` and `cryptography` are required; they are not installed in the sandbox. Real `pyotp` behaviour is assumed to match the stand-in for the methods used (`TOTP.at`, `random_base32`, `provisioning_uri`).
2. `TOTP_ENCRYPTION_KEY` must be set in every environment before Checkpoint 2 (enrollment). Missing key → feature fails closed.
3. Key rotation: prepend the new key, then run a re-encryption pass (`reencrypt_secret`) over `student_authenticators`. The re-encryption job is not yet written (Checkpoint 5).
4. `verify_second_factor` can raise `AuthenticatorConfigError` if a stored secret cannot be decrypted. Checkpoint 3 must catch this and deny access without a stack trace.
5. Backup-code verification scans every unused code (≈10 scrypt checks per attempt). Acceptable behind the rate limit; monitor under load.
6. Rate-limit fallback: the shared DB table is used; if the DB call fails, the existing code falls back to per-instance memory, which is weaker on serverless. This is existing behaviour.
7. Pre-existing and out of scope: `scan_login` can log a student in from an ID-card code while they still use the default password. Scheduled as a separate improvement; it does not bypass the form gate (Checkpoint 3).
8. Creation default is now ON for every new form. When Checkpoint 3 gates forms, existing test helpers that create forms and submit as students will need deliberate updates (set `auth_required=0` or enroll the student). This is expected, not a regression, but must be handled explicitly.

## 10. Deployment
Before Checkpoint 2: set `TOTP_ENCRYPTION_KEY` (comma-separated Fernet keys, newest first) in the production environment. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Store it as a secret; never commit it. Install `pyotp` and `cryptography` from `requirements.txt`. `DEPLOY.md` will be updated in Checkpoint 5.

## 11. Next checkpoint and approval
Checkpoint 2 (student setup: QR via `segno`, manual key, confirmation, backup-code display) requires explicit user approval. Not started.

## 12. Checkpoint 1.1 (security fixes, partial)

Scope: H1, H2, M1, M3 only. Verified by focused tests (59 passed in the sandbox harness: 47 Checkpoint 1 + 8 form-lock + 4 enrollment-limiter).

- **H1 (sticky lock):** `services.save_form` locks Authentication Required once a form has
  been published. The lock is `published_at IS NOT NULL` or any non-draft status. Revert to
  draft is still allowed (an existing approved test requires it), but the setting stays
  locked after a revert.
- **M3 (publish race):** the setting change is one conditional `UPDATE ... WHERE status='draft'
  AND published_at IS NULL`, checked by `rowcount`. A publish that commits first makes the
  change fail and roll back before any other field is written.
- **H2 (split script):** `scripts/split_forms_by_teacher.py` never copies the source
  `auth_required`. Each copy is written with 1 (`copied_form_columns`).
- **M1 (enrollment confirmation):** `confirm_enrollment` uses the same DB-backed lockout as
  second-factor verification. Locked accounts are refused before the code is checked, wrong
  codes are counted, success clears the count.

Deferred to later sub-checkpoint (not implemented): M2 atomic lockout counter (a concurrency
test showed the original read-then-write counter loses increments; to be fixed separately),
the audit fixes (UnicodeError handling, IntegrityError handling on concurrent enrollment,
digest-keyed cipher cache), the startup warning for a missing `TOTP_ENCRYPTION_KEY`, and
per-path default-ON tests for bulk, `create_forms_for_session`, legacy HOD save, and the
admin route.

## 13. Mandatory Checkpoint 2 requirement (C1, recorded, not implemented)

Enrollment (Checkpoint 2) must:
1. Require re-entry of the student's personal password before any enrollment step.
2. Refuse enrollment for accounts still using the shared default password.
3. Refuse enrollment from ID-card scan-login sessions.
4. Send a notification to the student's registered email after successful enrollment.
5. Never use email alone as an authenticator reset mechanism. Recovery is backup codes or an audited Admin reset.

## 14. Checkpoint 1.2 (M2: atomic lockout counter)

- **Root cause:** `security.register_login_failure` read the row, computed the next count in
  Python, then wrote it back. Concurrent failures overwrote each other, so attempts could
  exceed the limit. Separately, the OTP path checked the lock, verified the code, and only then
  recorded the failure, so requests that arrived together could all pass the check.
- **Fix:** the counter is now a few single-statement operations (`security._SQL_*`): ensure the
  row, reset an expired window, `fail_count = fail_count + 1`, lock at the threshold. Increments
  are evaluated by the database on the current row. The OTP path calls
  `security.reserve_login_attempt` before any code is checked. Each attempt is counted when it is
  made while the account is unlocked; attempts refused during a lockout are not counted, so they
  cannot extend it. At most `OTP_MAX_ATTEMPTS` attempts are allowed per window.
- **Policy unchanged:** 5 failures in 15 minutes lock for 15 minutes; success clears the count.
  Password-login behaviour is unchanged apart from the atomic counting.
- **SQLite and PostgreSQL:** the SQL uses only `ON CONFLICT ... DO NOTHING`, conditional single
  updates, and `?` placeholders, which the project's wrapper converts to `%s`. A test translates
  every counter statement with `pg.to_postgres`. On PostgreSQL, the `UPDATE ... fail_count+1`
  re-reads the latest row version after waiting for the row lock. PostgreSQL execution was not run.
- **Limitation:** under heavy concurrency an attempt can be refused although it could have been
  allowed (conservative), but never allowed beyond the limit. When the database is unreachable, the
  existing per-instance in-memory fallback applies, as before.
- **Tests:** `tests/test_login_lockout_atomic.py` (16 tests, real threads). Mutation checks: a
  non-atomic increment fails 2 concurrency tests; a reservation that counts nothing fails 6 tests.

## 15. Recovery service layer (all-roles upgrade, Checkpoint 1) — 2026-11-17

Scope: service layer only, in `feedback_system/authenticator.py` and `feedback_system/mailer.py`, plus one new test file. No routes, templates, schema or migration changes. Schema stays v25. Full design and operations guide: `docs/AUTHENTICATOR_RECOVERY.md`.

**Added to `authenticator.py`:** `backup_codes_remaining`, `regenerate_backup_codes` (active authenticator only; caller must have just verified a current TOTP), `verify_recovery_code` (TOTP or backup code under its own `otpreset:<id>` rate-limit key), `reset_locked_seconds` / `reserve_reset_attempt` / `clear_reset_failures`, `complete_lost_device_reset` (removes authenticator and all codes; verifies nothing itself). `verify_second_factor` and `verify_recovery_code` now share the helper `_check_code`; sign-in behaviour is unchanged.

**Added to `mailer.py`:** `security_notice(to, name, event)` for `password_reset`, `backup_code_used`, `lost_device_reset`, `backup_codes_regenerated`, `authenticator_reset`. Notification only; no secret, code, link or password.

**Rule verified (owner request):** a backup code or TOTP used for a normal password reset does not remove or disable the authenticator. Only `complete_lost_device_reset` (and the existing admin reset) remove it. The `backup_code_used` notice says the authenticator is still set up; `lost_device_reset` says it was removed.

**Tests:** `tests/test_authenticator_recovery_cp1.py`, run for a student and a teacher account. Results actually obtained on 2026-11-17 in the review sandbox, with real `pyotp`, on SQLite: `pytest tests/test_authenticator_recovery_cp1.py tests/test_authenticator_core.py tests/test_authenticator_notices_stepe.py tests/test_authenticator_setup_stepb.py` — **119 passed**. The previous session reported the full suite at 633 passed (581 existing + 52 new), excluding `tests/test_student_default_password.py`, which cannot be imported because `TEST_STUDENT_PASSWORD` is missing from `tests/helpers.py` (existing failure, not fixed). That full-suite figure was not re-run during the documentation task. PostgreSQL not run.

**Limitations / open decisions:**
1. Nothing calls the new functions yet; no user-visible change.
2. `complete_lost_device_reset` trusts its caller; the lost-device route must require `verify_recovery_code(...) == (True, 'backup')` first. A combined verify-and-remove function is offered, not approved.
3. HOD, Dean and Admin are not separately covered by the CP1 tests (student and teacher only); add in Checkpoint 2.
4. **PENDING:** no session invalidation after a reset (stateless cookies, 30 min idle / 8 h max). The v26 session-epoch migration is not written and not approved; schema is v25.
5. A known username lets someone lock that user's recovery (not sign-in) for the lockout period; per-IP limits planned in Checkpoint 3.
6. **NOT IMPLEMENTED:** the Admin lost-authenticator recovery script. A procedure is documented in `docs/AUTHENTICATOR_RECOVERY.md` §5; enforcement for Admin must wait for it.

**Next:** Checkpoint 2 (enrollment for all five roles, one-time backup-code display, `/account/security`). Not started; requires explicit approval.

## 16. All-roles upgrade, Checkpoint 2 (enrollment) — 2026-11-18

Scope: authenticator enrollment for Student, Teacher, HOD, Dean and Admin; QR setup; one-time backup codes; Account Security page; password re-entry; per-role enforcement and grace periods; notices. **No database migration** (schema stays v25). Design: `docs/AUTHENTICATOR_RECOVERY.md`.

**Files modified:** `feedback_system/authenticator.py` (enforcement policy: `ROLES`, `ADMIN_ENFORCEMENT_ALLOWED`, `enforcement_state`; no change to the Checkpoint 1 functions), `feedback_system/auth.py` (enrollment routes for all roles, password proof, backup-code page, `_authenticator_gate` wired into `_enforce_account_setup`), `feedback_system/__init__.py` (config `AUTHENTICATOR_ENFORCE_ROLES`, `AUTHENTICATOR_GRACE_UNTIL`, blueprint, admin warning), `feedback_system/templates/setup_authenticator.html`, `feedback_system/templates/base.html` ("Security" menu link), `tests/test_authenticator_setup_stepb.py` and `tests/test_authenticator_notices_stepe.py` (updated deliberately for the new password step and the backup-codes response), and the documentation files.
**Files created:** `feedback_system/account_security.py`, `feedback_system/templates/account_security.html`, `feedback_system/templates/backup_codes.html`, `tests/test_authenticator_enroll_cp2.py` (42 tests).
**Documentation also changed:** `README.md` and `UPGRADE_NOTES.md` (default passwords removed), `docs/DEPLOYMENT.md`, `docs/DEVELOPMENT_STATUS.md`, `docs/AUTHENTICATOR_RECOVERY.md`, this file, `docs/PHASE_HANDOFFS/README.md`.

**Routes:** `/setup/authenticator` (+ `/verify`, `/confirm`) now serve all five roles; new `GET /account/security`, `POST /account/security/backup-codes`.

**Behaviour:** password re-entry for every role (students also keep the e-mailed code); refused on a temporary/default password; backup codes rendered once in the confirming response (`no-store`), never in session, redirect or log; regeneration needs a current 6-digit app code (a backup code is refused and not spent); enrollment notice via the existing `authenticator_notice('added')`, regeneration via `security_notice('backup_codes_regenerated')`; a failed e-mail is audited and never blocks. Enforcement default is students only (unchanged); other roles are opt-in by config with a grace date; Admin enforcement cannot be enabled while Admin recovery is unimplemented; non-student enforcement is skipped without `TOTP_ENCRYPTION_KEY`.

**Rules kept:** nothing in this checkpoint removes or replaces an active authenticator (a test asserts no such route exists); changing a password leaves it in place. Password recovery, lost-device recovery and the admin-reset extension are still **Checkpoints 3 and 4**.

**Tests (2026-11-18, SQLite, real pyotp/segno):** `pytest tests --ignore=tests/test_student_default_password.py` — **675 tests, all passed** (633 before + 42 new; progress dots only, no failures or errors; pytest printed no summary line). `tests/test_student_default_password.py` still cannot be imported (missing `TEST_STUDENT_PASSWORD` in `tests/helpers.py`; pre-existing, unfixed). Mutation checks: removing the password proof, disabling enforcement, and accepting a backup code at regeneration each made the new tests fail. PostgreSQL and browser/phone-scan checks not run.

**Limitations:** no self-service replacement of a working authenticator (needs lost-device recovery); **PENDING, NOT IMPLEMENTED:** session invalidation and migration v26 (not written, not approved); **NOT IMPLEMENTED:** the Admin lost-authenticator recovery script, so Admin enforcement stays off; grace dates are config-only (no per-user start date, which would need a migration); a user who closes the backup-codes page without saving must create new codes; the notice for enrollment still has student-style fallback wording when a user has no name; Admin enrollment is voluntary and, until Checkpoint 3, an authenticator does not yet gate sign-in or password reset for any role.

**Next:** Checkpoint 3 (TOTP password recovery and lost-device recovery). Not started; requires explicit approval.

## 17. All-roles upgrade, Checkpoint 3 (lost-device recovery) — 2026-11-18

Scope: "I lost my phone" recovery for Student/Teacher/HOD/Dean (backup code), Admin recovery by verifying **both** registered recovery e-mails, atomic once-only removal, forced re-enrollment, audit and notices. **No migration** (schema stays v25). **Normal password reset is unchanged** (`forgot_password`, `reset_password`, `admin_forgot_password` not edited; only a link was added to two pages). Design: `docs/AUTHENTICATOR_RECOVERY.md` §4–5.

**Files created:** `feedback_system/lost_device.py`, `feedback_system/templates/lost_phone.html`, `admin_lost_authenticator.html`, `admin_lost_authenticator_verify.html`, `tests/test_authenticator_lost_device_cp3.py` (39 tests).
**Files modified:** `feedback_system/authenticator.py` (adds `recover_lost_device`, `apply_lost_device_reset`, `needs_reenrollment`, `otplost:` limiter; earlier functions untouched), `feedback_system/auth.py` (gate redirects on the re-enrollment marker for every role; setup page treats it as mandatory), `feedback_system/verification.py` (`check(..., consume=True)`; default behaviour unchanged), `feedback_system/mailer.py` (code e-mail wording for the admin purposes), `feedback_system/__init__.py` (blueprint, `LOST_DEVICE_IP_MAX_ATTEMPTS`), templates `forgot_password.html`, `admin_forgot_password.html`, `account_security.html` (links), and the documentation files.

**Re-enrollment marker (no migration):** a `pending` `student_authenticators` row with `enrolled_at` set. Voluntary unfinished setups have it NULL.

**Security review summary:**
- Replay: backup code spent atomically; used/old codes dead; app codes rejected on this path and not spent; admin e-mail codes single use, 10-minute expiry, destroyed after 5 wrong guesses.
- Brute force: per-account (`otplost:`, `adminlostv:`) and per-IP limits, reserved before checking; isolated from sign-in and password-reset counters (tested both ways).
- Concurrency: removal is one transaction deleting the row only while `active`; threaded tests show exactly one winner and a consistent final state; failures roll back fully; a bad encryption key fails before any code is spent.
- Session tampering: `/lost-phone` keeps no session state; admin flow state is `{uid, exp}` in the signed session; tampered cookies, forged/expired/malformed records and another account's id are refused (tested).
- Probing: identical responses for every failure; password hash always compared.
- Rules kept: normal reset never touches the authenticator (regression test); only these recovery paths remove it.

**Tests (2026-11-18, temporary SQLite DB, real pyotp/segno):** see DEVELOPMENT_STATUS §3j for the full-suite result. Eight deliberate code breaks (no code consumption, ignored password, ignored marker, one-code admin, unconditional delete, early code spend, no state expiry, no account limit) each made the new tests fail.

**Limitations / pending:** **PENDING, NOT IMPLEMENTED:** session invalidation and migration v26 (other sessions stay valid until they expire; not written, not approved). Admin enforcement still disabled (`ADMIN_ENFORCEMENT_ALLOWED=False`, owner decision). Whoever controls both admin recovery mailboxes can remove the admin authenticator (password still needed to sign in). Per-IP limits are shared by users behind one address. Authenticator-based password reset and the admin reset of other users' authenticators (Checkpoint 4) are not done. PostgreSQL and a live mail server were not tested. The staff page needs the current password, so a user who forgot it must reset it first.

**Next:** Checkpoint 4. Not started; requires explicit approval.
