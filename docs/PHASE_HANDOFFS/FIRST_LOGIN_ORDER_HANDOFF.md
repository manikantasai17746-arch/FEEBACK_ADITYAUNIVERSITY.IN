# First-login order for Teacher / HOD / Dean — handoff

## What changed
The one-time setup now runs in this order for **Teacher, HOD and Dean** (students are unchanged):

| Step | Teacher | HOD / Dean |
|---|---|---|
| 1 | Photo (`/setup/photo`) | Photo (`/setup/photo`) |
| 2 | Replace the temporary password (`/change-password`) | same |
| 3 | Confirm e-mail with a code (`/setup/email`) | e-mail onboarding page (`/hod/onboarding`, `/dean/onboarding`: e-mail code, then claim scope) |
| 4 | Authenticator app — only if the role is in `AUTHENTICATOR_ENFORCE_ROLES` | same |
| 5 | Teacher Assignment Setup (`/teacher/assignment-setup`) | — |

Previously a teacher did e-mail → password → authenticator → photo, HOD/Dean did password → onboarding → authenticator with no photo step.

## How it works (`feedback_system/auth.py`)
- `_pending_first_login_step(user)` returns the first unfinished step among photo / password / email. The sign-in gate (`_enforce_account_setup`) now handles all three roles with it, then runs `_authenticator_gate`.
- Every step page also refuses to be reached early (typing the URL): `setup_email`, `verify_email`, `change_password`, `setup_authenticator`, and `hod.onboarding` / `dean.onboarding` redirect to the earlier pending step.
- `users.first_login_complete` now means **"photo step done"** for all three roles (teachers were already grandfathered to 1 by migration v20). HOD/Dean accounts that already finished onboarding (`onboarding_complete=1`) count as photo-done, so existing HOD/Dean are not sent back. **No schema change.**
- Step 5: the gate sets `session['first_login_flow']` whenever it sends a teacher/HOD/Dean to a setup step. When nothing is pending any more, the next GET by a teacher lands once on Teacher Assignment Setup; after authenticator enrolment the backup-codes "continue" button goes there too. The flag lives in the session only.
- Step labels ("Step 2 of 5 · One-time setup") count the authenticator step only when it applies to the role.
- `REQUIRE_ACCOUNT_SETUP=false` still skips the photo and (teacher) e-mail steps, as before.

## Deployment note
The authenticator step is **not** part of first login for Teacher/HOD/Dean unless enforcement is switched on (default is students only). To include it, set `AUTHENTICATOR_ENFORCE_ROLES=student,teacher,hod,dean` (optionally `AUTHENTICATOR_GRACE_UNTIL`) and `TOTP_ENCRYPTION_KEY`.

## Limits / decisions
- A teacher who logs out part-way through and signs in again completes the steps in order, but the one-time landing on Assignment Setup is only guaranteed within the session that started the flow; the page stays reachable from the dashboard.
- A HOD/Dean whose onboarding an admin later resets (`onboarding_complete=0`) and who never had the flag set will see the photo step once more.
- Teachers whose password an admin resets (still grandfathered) go password → landing on Assignment Setup once.

## Tests
`tests/test_first_login_phase2.py` (rewritten for the new order; new HOD/Dean, authenticator-order and no-skipping-ahead tests) and `tests/test_account_setup.py` (two teacher tests updated).
