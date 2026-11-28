# Deployment instructions — HOD Faculty Feedback modules

**Schema:** v24 (this release adds **no** new migration)  
**Zip:** `ALL_SESSION_CHANGES.zip`

## 1. Pre-flight

- Working backup of the production database (SQLite file or PostgreSQL dump).
- Python 3.11+ with project venv; `pip install -r requirements.txt` (includes `reportlab`).
- Confirm current app already runs at schema **v24** before deploying UI-only changes.

## 2. Deploy code

```bash
cd /path/to/FEEBACK_ADITYAUNIVERSITY.IN
# optional safety branch
git checkout -b deploy/hod-feedback-modules
unzip -o ALL_SESSION_CHANGES.zip
git add tests/ docs/ feedback_system/
git status   # review
git commit -m "HOD Main/Second/Lab feedback, results tabs, category PDFs, test baseline"
```

Paths are relative to the repo root. Do not nest the zip under another folder.

## 3. Database

- **No migration required** for this feature set (still v24).
- On first app start, existing migrate runner is idempotent:  
  copy DB → start app → confirm `feedback_system_schema_migrations` max version is still 24 and row counts unchanged.
- Never delete historical forms, submissions, or soft-removed second-faculty rows.

## 4. Restart

```bash
# example — adjust to your process manager
sudo systemctl restart faculty-feedback
# or
pkill -f gunicorn; gunicorn -w 4 -b 0.0.0.0:8000 'feedback_system:create_app()'
```

## 5. Smoke checks (production)

1. HOD login → **Overview** loads with category cards.
2. Nav: Main / Second / Lab Faculty Feedback, Results, Reports (existing links).
3. Results tabs `?tab=main|second|lab` and **Download PDF** for the active tab.
4. Teacher login: cannot open `/hod/feedback/*` or `/hod/results/*.pdf`.
5. Optional: `python -m pytest tests/test_hod_feedback_main.py tests/test_hod_feedback_lab.py tests/test_hod_feedback_second_and_results.py tests/test_hod_category_pdf.py -q`

## 6. Rollback

```bash
git revert HEAD   # or checkout previous commit
# restart app — no DB rollback needed (no schema change)
```

## 7. Notes

- Main, Second, and Lab forms stay separate (category column + separate eligibility).
- Student answers remain anonymous; eligible names only in HOD preview.
- PDF reports reuse `reports.build_category_results_pdf` and HOD scope filters.

## Student authenticator (TOTP) and feedback code

New environment variables (set them in the production environment, never in the repository):

| Variable | Required | Purpose |
|---|---|---|
| `TOTP_ENCRYPTION_KEY` | Yes, before any student is asked to set up an app | Fernet key(s) that encrypt TOTP secrets at rest. Comma-separated, newest first, to allow rotation. Not derived from `SECRET_KEY`. Without it, setup fails closed and a warning is logged at start-up. |
| `DEFAULT_STUDENT_PASSWORD` | Yes | The shared default that student accounts were created with. Set it to the value your accounts actually use; the built-in fallback is in `feedback_system/__init__.py` and is deliberately not repeated here. Accounts still on it must change it before any protected page. Empty disables this detection; a warning is logged. |
| `PREVIOUS_DEFAULT_STUDENT_PASSWORDS` | If the default was ever changed | Comma-separated older defaults, still detected. Values must not contain commas. |
| `STUDENT_EMAIL_DOMAIN` | No (default `adityauniversity.in`) | Students confirm their e-mail as `<roll>@<this domain>` only. Empty switches the rule off. |
| `MICROSOFT_CLIENT_ID` / `MICROSOFT_CLIENT_SECRET` / `MICROSOFT_TENANT_ID` | For "Continue with Microsoft" | Azure app registration; redirect URI `https://<site>/sso/microsoft/callback`. Students get Microsoft only when `MICROSOFT_TENANT_ID` is the college tenant GUID (not `common`). |
| `AUTHENTICATOR_ENFORCE_ROLES` | No (default `student`) | Comma list of roles that must set up an authenticator, e.g. `student,teacher,hod,dean`. `student` is always enforced, even if this is blank or omits it. `admin` is ignored (with a start-up warning) until the Admin recovery procedure is implemented. Non-student roles are never forced while `TOTP_ENCRYPTION_KEY` is unset. |
| `LOST_DEVICE_IP_MAX_ATTEMPTS` | No (default 10) | Lost-device recovery requests allowed per address per 15 minutes before a lockout. Raise it if many users share one address. |
| `AUTHENTICATOR_GRACE_UNTIL` | No | `role=YYYY-MM-DD` pairs, e.g. `teacher=2026-12-31,hod=2026-12-31`. A `student=` entry is ignored: students have no grace period. Before the date the role only sees a reminder; from the date it must enrol at sign-in. |
| `OTP_MAX_ATTEMPTS` | No (default 5) | Wrong codes allowed in a 15-minute window before the account is locked. |
| `OTP_LOCKOUT_SECONDS` | No (default 900) | Length of the lock. |

Generate a key with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Install the new dependencies from `requirements.txt` (`cryptography`, `pyotp`, `segno`). `segno` draws the QR code; without it the page still shows the setup key.

Database: migration v25 runs automatically at start-up. It only adds tables and a column; existing forms are set to "Authentication Required" OFF.

Rollout checklist:

1. Set `TOTP_ENCRYPTION_KEY` and confirm `DEFAULT_STUDENT_PASSWORD` matches the accounts' default.
2. Start the app and confirm no "TOTP_ENCRYPTION_KEY is not set" warning in the log.
3. Tell students that after their next login they will confirm their e-mail and set up an authenticator app.
4. New forms require the code by default. Admins and HODs can untick the box while creating a form; a form cannot be changed once it is published.
5. Admins can reset a student's authenticator (for example, a lost phone) from the student list. The student is e-mailed and sets up again.
6. Sessions open before the update keep working until they log out or expire. Changing `SECRET_KEY` ends all sessions at once if that is wanted.

### Recovery precautions (authenticator all-roles upgrade, Checkpoint 1)

Checkpoints 1 and 2 need **no migration** (schema stays v25). Checkpoint 2 adds the two optional variables above; with their defaults only students are forced to enrol, as before. To roll out to staff, list the roles in `AUTHENTICATOR_ENFORCE_ROLES` with a grace date, announce it, and confirm `TOTP_ENCRYPTION_KEY` is set. Do not enforce Admin (it is refused until Admin recovery exists). See `docs/AUTHENTICATOR_RECOVERY.md` §7–8. In short:

- Keep `TOTP_ENCRYPTION_KEY` in the secret manager with a backup. Losing it makes stored authenticator secrets unreadable; affected users then need an administrator reset and re-enrollment.
- Password recovery attempts have their own lockout (`otpreset:`), separate from sign-in. `OTP_MAX_ATTEMPTS` and `OTP_LOCKOUT_SECONDS` apply to both.
- **Pending (not implemented):** session invalidation. Resetting a password or authenticator does not sign out sessions already open elsewhere (idle limit 30 minutes, maximum 8 hours). Rotating `SECRET_KEY` ends all sessions at once. Fixing this needs migration **v26** (session epoch), which is **not written and not approved**; the schema remains v25.
- **Pending (not implemented):** a supported way to recover an Admin who has lost the authenticator. Do not enforce authenticators for the Admin role until it exists. See `docs/AUTHENTICATOR_RECOVERY.md` §5.
- Test on PostgreSQL before relying on the new code in production; Checkpoint 1 tests ran on SQLite only.

