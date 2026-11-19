# Authenticator and Recovery — Design and Operations Guide

**Scope of this document:** the authenticator (TOTP) secret, backup codes, password recovery, lost-device recovery, admin-assisted recovery, security e-mails, role differences, and configuration.
**Status as of Checkpoint 3 (lost-device recovery):** the service layer (Checkpoint 1), enrollment for all five roles, one-time backup codes, the Account Security page and per-role enforcement (Checkpoint 2), and lost-device recovery for all five roles (Checkpoint 3) are implemented and tested. Normal password reset is unchanged (still the e-mail code flow; authenticator-based password reset is **not** part of Checkpoint 3). The extended admin reset of other users' authenticators is **[Planned: Checkpoint 4]**. Session invalidation and migration v26 are **pending** and not implemented.
**Source of truth:** `feedback_system/authenticator.py`, `feedback_system/mailer.py`, `feedback_system/security.py` and `tests/test_authenticator_recovery_cp1.py`. If this document and the code disagree, the code wins; fix this document.

This document contains no real passwords, secrets, keys or backup codes. Examples use placeholders only.

---

## 1. Authenticator secret

| Step | What happens | Code |
|---|---|---|
| Generate | A fresh random base32 secret (160 bits, `pyotp.random_base32()`) is created per enrollment. | `new_secret` |
| Encrypt | The secret is encrypted with Fernet (`MultiFernet`) using the key(s) in `TOTP_ENCRYPTION_KEY`. The key is never derived from `SECRET_KEY`. | `encrypt_secret` |
| Store | Only the encrypted token is stored: `student_authenticators.secret_enc`. The plaintext exists in memory only. | `begin_enrollment` |
| Show once | `begin_enrollment` returns the plaintext once so the setup page can show a QR code / setup key. It is never put in the session or logged. Implemented in Checkpoint 2: `/setup/authenticator` shows a QR code (inline SVG, standard `otpauth://` URI) plus the manual setup key, for Google or Microsoft Authenticator. | `begin_enrollment`, `pending_secret` |
| Confirm | The user enters a 6-digit code. If it matches, `status` changes from `pending` to `active` and the confirming time step is recorded as used. | `confirm_enrollment` |
| Verify | Each later code is checked against the decrypted secret. | `verify_totp`, `_check_code` |

Table names still say "student" for historical reasons, but both tables are keyed by `user_id`, so they hold a row for any role. No schema change is needed (schema stays at v25).

**Row states.** No row = not enrolled. `pending` = secret issued, not yet proven. `active` = proven and usable. Only an `active` authenticator can verify codes. A `pending` one is refused as `not_enrolled`.

**Verification rules** (RFC 6238; SHA-1, 6 digits, 30-second step):
- The code must be exactly six digits; anything else is rejected without touching the database.
- Time steps one before, the current, and one after are accepted (±30 s drift). Two steps away is rejected.
- Every candidate step is compared with a constant-time comparison, so timing does not reveal which step matched.
- **Replay protection:** a step can be used once. `consume_step` runs `UPDATE … WHERE last_step IS NULL OR last_step < ?` and checks `rowcount`, so two simultaneous requests with the same code cannot both succeed. `last_step` is shared by sign-in and recovery, so a code spent in one place is dead in the other.
- If a stored secret cannot be decrypted (wrong or lost key), `AuthenticatorConfigError` is raised. Callers must catch it and deny access without a stack trace. **[Caller handling: Checkpoint 3]**

**Replacing an active authenticator** is refused by `begin_enrollment` (`AuthenticatorStateError`). The only ways to remove one are lost-device recovery (§4) and admin reset (§5).

## 2. Backup codes

| Property | Value |
|---|---|
| Count | `BACKUP_CODE_COUNT` = 10 per account |
| Format | `xxxx-xxxx-xxxx`: 12 characters from a 31-character alphabet with no `0/1/i/l/o` look-alikes (about 59 bits each) |
| Generation | `secrets.choice` (cryptographically secure) in `generate_backup_codes` |
| Storage | Only scrypt hashes (`security.hash_password`) in `student_backup_codes.code_hash`. Plaintext is never stored. Hashes do not depend on `TOTP_ENCRYPTION_KEY`, so key rotation does not affect them. |
| Display | `regenerate_backup_codes` returns the plaintext list once to the caller. It is rendered in that same response (`Cache-Control: no-store`) and never put in the session, a redirect or a log. Implemented in Checkpoint 2 (shown right after the first app code is confirmed, and after regeneration). |
| Verification | `consume_backup_code` ignores dashes, spaces and case, rejects anything that is not 12 alphabet characters, then checks every unused hash (so timing does not reveal which one matched). |
| Single use | A match sets `used_at` with `UPDATE … WHERE id=? AND used_at IS NULL`; only the first caller gets `rowcount == 1`. A used code never verifies again. |
| Regeneration | `regenerate_backup_codes` deletes all old rows and inserts a new set in one transaction, so every old code stops working. Allowed only for an `active` authenticator; otherwise `AuthenticatorStateError`. The caller must first have verified a current TOTP code. Implemented: `POST /account/security/backup-codes` accepts only a 6-digit app code (a backup code is refused and not spent). |
| Remaining count | `backup_codes_remaining` counts rows with `used_at IS NULL`. |

A backup code is accepted at sign-in as well as in recovery. Using one in either place spends it.

## 3. Normal password reset (forgotten password)

**Flow [Implemented 2026-11-19 for accounts with an active authenticator; others keep the e-mail code]:** the user gives their registered username or e-mail plus a TOTP code **or** an unused backup code. No e-mail code and no reset link are sent. After the check succeeds, the route sets a short-lived, single-use "verified" marker and only then accepts a new password under the existing password rules.

**What exists now:** `verify_recovery_code(user_id, code)` returns `(ok, reason)`.

| Reason | Meaning |
|---|---|
| `totp` / `backup` | Success. A TOTP step or one backup code is now spent. |
| `locked` | Recovery is locked for this account (§8). |
| `not_enrolled` | No active authenticator. Not counted as a failed attempt. |
| `invalid` | Wrong, malformed, reused or replayed. Counted. |

The route must show the browser the same message for every failure reason (unknown account, no authenticator, locked, wrong code) so it cannot be used to probe accounts.

### Normal reset never removes or disables the authenticator

This is a firm rule and it is tested:

- `verify_recovery_code` only spends the one TOTP step or the one backup code it was given. It never calls any removal function, whichever kind of code was used.
- After a backup-code reset: the authenticator is still active with the same secret, exactly one code is spent (nine remain), the old phone still works at sign-in, and another backup code still works.
- After spending all ten backup codes through recovery, the authenticator is still active; the user then has zero codes and can regenerate them while signed in with a TOTP code.
- Tests: `test_backup_code_in_a_normal_reset_keeps_the_authenticator_and_other_codes`, `test_spending_every_backup_code_never_removes_the_authenticator`, `test_a_totp_reset_also_keeps_the_authenticator_and_codes`, `test_only_the_lost_device_function_removes_the_authenticator`.
- Only `complete_lost_device_reset` (§4) and the admin reset (§5) remove an authenticator.

## 4. Lost-device recovery (separate path) — implemented, Checkpoint 3

A Student, Teacher, HOD or Dean who has lost the phone but still has an unused backup code uses **`/lost-phone`** ("I lost my phone"). It is separate from the forgotten-password page and is never reached from it. It is not a password reset: the user must still know the current password (if the password is forgotten, reset it first through the normal flow, then come back).

**One request, no session state.** The form takes role, ID, **current password** and **one backup code**. In order:
1. Per-IP limit (every request counts, `LOST_DEVICE_IP_MAX_ATTEMPTS`, default 10 per window).
2. Account lookup. A password hash is always compared (a dummy hash if the account is unknown) so timing is the same.
3. Unknown account or no active authenticator: generic failure (not counted per account).
4. One attempt is **reserved** under the account's own `otplost:<id>` key. Locked: generic failure.
5. Wrong password: generic failure; the backup code is **not** spent.
6. `authenticator.recover_lost_device(user_id, code, reserve=False)` — the single verify-then-remove step:
   - accepts a backup code only (a 6-digit app code is rejected before anything is spent);
   - checks the encryption key first, so a bad key fails closed and spends nothing;
   - spends the code atomically (`UPDATE … WHERE used_at IS NULL`);
   - calls `apply_lost_device_reset` (below).

**`apply_lost_device_reset(user_id)` — atomic and once-only.** In one transaction it deletes the authenticator row **only while it is `active`**, deletes all backup codes (used and unused), and inserts a fresh `pending` authenticator row as the re-enrollment marker. Two simultaneous recoveries cannot both succeed: the loser's delete matches no row, rolls back and returns `False` (tested with threads). It also clears the sign-in, password-reset and lost-device lockout counters. Any failure rolls everything back, so nothing is removed unless the whole recovery succeeded.

**Re-enrollment required at next sign-in.** The marker is a `pending` row in `student_authenticators` whose `enrolled_at` is set (a voluntary, unfinished setup is also `pending` but has `enrolled_at` NULL, so they cannot be confused; confirming the new device activates the row and clears the marker). No schema change. `authenticator.needs_reenrollment(user_id)` reads it. The sign-in gate redirects such a user to `/setup/authenticator` for **every role and whatever the enforcement config says**, and the setup page then has no "Not now" option. The pending secret is newly generated; the old secret is never reused. Enrollment needs the password proof as usual (students also the e-mailed code), and ends by showing 10 new backup codes once.

**Browser response.** Every failure (unknown ID, no authenticator, wrong password, wrong, used or malformed code, locked) shows the same message and page, tested byte for byte apart from the CSRF token and the echoed ID. A success redirects to the role's sign-in page with a notice.

**Audit and notices.** `user.lost_device_recovery` (success) and `user.lost_device_recovery_failed` (with a short reason, never the code or password). The existing `lost_device_reset` e-mail goes to the user's registered address; it contains no code, link or secret. A failed e-mail is audited and never blocks or undoes the recovery.

## 5. Admin-assisted recovery (no phone and no backup codes)

E-mail alone is never a reset mechanism (decision recorded in the Checkpoint 1 handoff §13). A user with neither phone nor codes contacts an administrator.

**Exists today:** `POST /admin/students/<uid>/reset-authenticator` (admin only, students only). It calls `remove_authenticator`, writes an audit entry (`user.authenticator_reset`), and e-mails the student via `mailer.authenticator_notice(..., 'reset')`. If the e-mail fails, a second audit entry records that. The student sets up a new authenticator at next login.

**Planned [Checkpoint 4]:**
- Extend the same route to teacher, HOD and Dean accounts, keeping the audit entry and e-mail. The notice will use the `authenticator_reset` event in `mailer.security_notice`.
- An administrator cannot reset another administrator through the web route.
- A locked-out administrator is covered by the procedure below.

### Admin lost-authenticator recovery — implemented, Checkpoint 3

This replaces the earlier script-only design. An administrator who has lost the authenticator uses **`/admin/lost-authenticator`** (linked from the admin forgot-password page). It needs mail to be configured and both recovery e-mails to be registered (the same two addresses the admin set up after first login).

1. The admin enters **both** recovery e-mails. If they match one active admin account that has an authenticator, a **different** 6-digit code is e-mailed to **each** address (purposes `admin_lost_1` and `admin_lost_2`). The page answers the same way whether or not they matched, and no mail is sent otherwise.
2. The admin enters both codes. They are checked **without spending either** (`verification.check(..., consume=False)`); only if **both** are right, belong to the two registered addresses and are different, are both discarded and the reset applied. A wrong or mixed-up pair spends nothing, so a typo does not force a restart. Each code also destroys itself after 5 wrong guesses and expires after 10 minutes.
3. The reset is the same atomic `apply_lost_device_reset` as §4: authenticator and all backup codes removed, re-enrollment marker left, counters cleared. The admin signs in with the existing password and must set up a new authenticator before using the portal.
4. Notices (`lost_device_reset`) go to the admin's primary address and both recovery addresses. Audit: `admin.lost_authenticator_requested`, `admin.lost_authenticator_recovery`, `admin.lost_authenticator_failed`.

**Protections.** Per-IP limit on both steps; a per-account attempt limit on step 2 (`adminlostv:<id>`, `OTP_MAX_ATTEMPTS`); the flow state is a small signed-session record (`uid`, expiry 10 minutes). A tampered cookie fails its signature and is ignored. A well-formed but forged record (another account's id, a non-admin id, an expired or malformed value) is discarded or fails, because every code is checked server-side against that account and the codes are only ever sent to that account's own addresses.

**Residual risk.** Anyone who controls **both** recovery mailboxes can remove the admin's authenticator. They still need the admin password to sign in, and every address is notified. Keep the two recovery addresses on different providers with their own strong protection.

**Admin enforcement is still off.** `authenticator.ADMIN_ENFORCEMENT_ALLOWED` stays `False`: enrolment is voluntary for admins, and being required to enrol is a separate decision for the owner. A recovered admin, however, is required to re-enrol at next sign-in. `scripts/sync_admin_credentials.py` remains the offline route for a forgotten password only; it does not touch authenticators.

**Admin precautions:** confirm the person's identity by a channel outside the application (in person, or a known phone number) before resetting; the reset removes protection, so treat the request as a possible social-engineering attempt.

## 6. Security notifications

`mailer.security_notice(to, name, event)` sends notification-only mail. It returns `True` only if the mail was accepted; an unknown event or empty address returns `False` and sends nothing. No message contains a password, secret, setup key, link or code (enforced by a test that scans every message).

| Event | When it should be sent | Says |
|---|---|---|
| `password_reset` | Password changed through authenticator recovery | Password was changed using the authenticator |
| `backup_code_used` | A backup code was spent (recovery or sign-in) | Authenticator is **still set up**; fewer codes left |
| `lost_device_reset` | Lost-device recovery completed | Old authenticator and all codes **removed**; set up a new one at next sign-in |
| `backup_codes_regenerated` | A new set of codes was created | Older codes no longer work |
| `authenticator_reset` | An administrator reset the authenticator | Set up a new one at next sign-in |

Existing, separate notices for students: `mailer.authenticator_notice` ("added" when an app is set up; "reset" for the current admin reset).

**Sent today (Checkpoints 2 and 3):** a successful lost-device or admin recovery sends `lost_device_reset`; enrollment sends the existing `authenticator_notice('added')` message, and creating new backup codes sends `backup_codes_regenerated`. `password_reset`, `backup_code_used` and `authenticator_reset` are provided but not yet triggered (they belong to authenticator-based password reset and the extended admin reset). E-mail failure must never block or undo the security action; callers record it in the audit log instead. Routine password-reset e-mail codes are not sent in the new flow; e-mail remains for these notices and other existing notifications.

## 7. The five roles

The service layer is role-agnostic: it takes a `user_id`. Checkpoint 2 enrollment and the Account Security page are tested end to end for **all five roles** (`tests/test_authenticator_enroll_cp2.py`).

**Enrollment (implemented, Checkpoint 2).** Every role must **re-enter the current password** (rate limited with the OTP limits; proof valid 15 minutes, held only as an expiry time in the signed session). Students must also confirm the e-mailed code, as before. Enrollment is refused while the account is on a temporary or shared default password. This also covers ID-card scan sessions: those only work on the shared default, so the user must first set a personal password and then type it to enrol.

| Role | Recovery identifier (authenticator-based reset, deferred) | Enrollment proof (implemented) | Admin reset | Enforcement default |
|---|---|---|---|---|
| Student | Roll number or registered e-mail | Password + e-mailed code | Yes (exists) | **Required** (unchanged from before) |
| Teacher | Employee ID or registered e-mail | Password | Planned (Checkpoint 4) | Off |
| HOD | Employee ID or registered e-mail | Password | Planned (Checkpoint 4) | Off |
| Dean | Employee ID or registered e-mail | Password | Planned (Checkpoint 4) | Off |
| Admin | Admin e-mail or username | Password | **Not through the web.** Lost authenticator: both-recovery-e-mail recovery (section 5) | Disabled by `ADMIN_ENFORCEMENT_ALLOWED=False`; owner decision |

HOD and Dean enrollment is reached after their existing onboarding step. Admins may enrol **voluntarily**; the Account Security page tells them recovery for a lost administrator authenticator is not available yet.

**Per-role enforcement and grace periods (implemented).** Configured by environment variables (no migration needed):
- `AUTHENTICATOR_ENFORCE_ROLES`: comma list of roles that must enrol. Default `student`.
- `AUTHENTICATOR_GRACE_UNTIL`: `role=YYYY-MM-DD` pairs, e.g. `teacher=2026-12-31,hod=2026-12-31`. Before that date the role is only reminded (one banner per login session); from that date it is redirected to setup at sign-in. An invalid date is ignored and the role is enforced at once.
- Non-student roles are never forced while `TOTP_ENCRYPTION_KEY` is unset.
- `admin` in the list is ignored (a start-up warning is logged) while `authenticator.ADMIN_ENFORCEMENT_ALLOWED` is `False`. Change that constant only together with the Admin recovery procedure.

**Account Security page (implemented).** `/account/security` (menu item "Security", all five roles): enrolled or not, unused backup codes (`n of 10`), enforcement status, and creation of new backup codes with a current app code. It has **no** remove or replace option: only lost-device recovery [Planned: Checkpoint 3] or an audited administrator reset removes an authenticator. A user who still has a working phone and wants a new device has no self-service path yet; this is deliberate and listed as a limitation.

## 8. Configuration and security

**`TOTP_ENCRYPTION_KEY`**
- One or more Fernet keys, comma-separated, **newest first**. Set it as a secret in the environment; never commit it, paste it into tickets or docs, or reuse `SECRET_KEY`.
- Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- Missing or malformed: setup and verification fail closed (`AuthenticatorConfigError`; the message never contains key material) and a warning is logged at start-up.
- Rotation: put the new key first, keep the old one after it, deploy, run a re-encryption pass (`reencrypt_secret`; the job script is not yet written), then remove the old key.
- **Losing the key makes every stored secret unreadable.** Back it up in the secret manager. Backup codes survive (they are hashed, not encrypted), and `remove_authenticator` does not need the key, so affected users can still be reset and re-enrolled.

**Rate limiting** (shared `login_attempts` table, keys hashed with SHA-256; works across server instances)
- `OTP_MAX_ATTEMPTS` (default 5) attempts per 900-second window, then a lock of `OTP_LOCKOUT_SECONDS` (default 900).
- An attempt is **reserved before** the code is checked, so simultaneous requests cannot exceed the limit; success clears the count.
- Independent keys: `otp:<id>` for sign-in, `otpreset:<id>` for authenticator-based password recovery (not yet in use), `otplost:<id>` for lost-device recovery, `adminlostv:<id>` for the admin step 2, plus per-IP keys (`lostphone-ip:`, `adminlost-ip:`, `adminlostv-ip:`, `LOST_DEVICE_IP_MAX_ATTEMPTS`; default 10). Per-IP limits count every request, so many users behind one shared address share the cap. Guessing at the forgot-password page cannot lock the real user out of sign-in, and mistyping at sign-in does not block recovery (both tested).
- `not_enrolled` is checked first and is not counted.
- Residual risk: someone who knows a username can lock that user's **recovery** (not sign-in) for 15 minutes. Per-IP limits are planned for Checkpoint 3.
- If the database call fails, the existing per-instance in-memory fallback applies, which is weaker on serverless hosts.

**Replay protection:** see §1. One time step, one use, across sign-in and recovery.

**Session invalidation — PENDING, NOT IMPLEMENTED. Migration v26 — PENDING, NOT WRITTEN, NOT APPROVED.** Sessions are signed stateless cookies (idle limit 30 minutes, maximum 8 hours). A password reset or lost-device reset **does not sign out sessions already open on other devices**; they end at those limits, or all at once if `SECRET_KEY` is changed. Closing this would need a small additive migration **v26** (a per-user session-epoch column, checked on every request and bumped on password or authenticator reset). The schema is **v25** today and no v26 exists in the code. This needs the owner's decision before any work. Until then, tell users who suspect compromise to expect up to 8 hours of residual access, and rotate `SECRET_KEY` in a real emergency.

**Recovery precautions**
- Never log, print, e-mail, or place in a URL, session, or redirect any secret, setup key or backup code.
- The browser must never learn why a recovery attempt failed.
- Tell users to store backup codes offline (printed or in a password manager), not on the same phone as the authenticator.
- After a lost-device reset or admin reset, the old authenticator is gone; confirm the user's next enrollment completed and new codes were saved.
- Test on PostgreSQL before production: Checkpoint 1 tests ran on SQLite only. The code uses the same SQL helpers and needs no migration.

## 9. Test coverage (Checkpoint 1)

File: `tests/test_authenticator_recovery_cp1.py`. Every case runs for a student and a teacher account. Covers: backup-code count and regeneration, plaintext never stored, TOTP/backup recovery, replay (here and at sign-in), malformed input, not-enrolled handling, lockout under its own key in both directions, counter clearing, lost-device reset and re-enrollment, the "normal reset keeps the authenticator" rules, and security e-mail content and wording. See `docs/DEVELOPMENT_STATUS.md` for exact results.
