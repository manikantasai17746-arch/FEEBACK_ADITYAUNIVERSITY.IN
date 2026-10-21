# HOD & Dean Account Management Upgrade — Deliverable

## 1. Modified / added files

| File | Change |
|------|--------|
| `feedback_system/schema.py` | Migration **v9**: `email_verified`, `onboarding_complete`, `designation` on users; `hod_allocations` (exclusive key); `dean_hod_assignments`; `allocation_history` |
| `feedback_system/security.py` | Scope helpers prefer `hod_allocations`; `allocation_owner`, `hod_allocation_keys`, `dean_assigned_hod_ids`; resilient `current_user` |
| `feedback_system/admin.py` | Full CSV import, credential one-time display, reset-onboarding, toggle-active, STRING_AGG-compatible listing, allocation overview |
| `feedback_system/auth.py` | `must_change_password` for HOD/Dean; onboarding redirect; safe `_home_for` redirect after password change |
| `feedback_system/hod.py` | Onboarding wizard (email verify → multi-scope claim → preview → transactional exclusive claim) |
| `feedback_system/dean.py` | Onboarding wizard (email verify → select HODs → combined preview → save assignments) |
| `feedback_system/templates/admin_hod_dean.html` | Import UI, credential panel, status/actions, allocation & Dean–HOD tables |
| `feedback_system/templates/hod_onboarding.html` | Responsive HOD wizard |
| `feedback_system/templates/dean_onboarding.html` | Responsive Dean wizard |
| `tests/test_hod_dean_upgrade.py` | 9 automated tests (all passed) |

## 2. Database migrations & deployment

1. Deploy the updated code.
2. On next app start, migration **v9** runs automatically (additive; safe for existing data).
3. Existing HOD/Dean users keep `onboarding_complete=0` and must complete the wizard (or Admin can reset).
4. No historical feedback or assignment rows are deleted.
5. PostgreSQL: unique constraint on `hod_allocations(academic_year_id, branch_id, semester_id, timetable_id)` enforces exclusivity; `STRING_AGG` used in admin listing when `DATABASE_URL` is set.

## 3. CSV template & import

**Headers:**  
`role,employee_id,full_name,official_email,designation,department_codes,active`

- `role`: `hod` or `dean` only  
- `department_codes`: branch codes separated by `|` (must exist in `branches`)  
- `active`: `0` or `1`  
- Max 200 rows, 512 KB, UTF-8  
- Duplicates (file or DB) rejected; existing accounts never overwritten  
- All rows validated before any write; single transaction per import  

**Route:** `POST /admin/accounts/hod-dean/import` (admin + CSRF)

## 4. Initial credentials

- Per-account cryptographically secure temporary password (`secrets.token_urlsafe(12)`)  
- Stored **only** as Werkzeug hash; never in CSV or logs  
- Shown **once** on the admin page after successful import (session-scoped)  
- Admin delivers credentials out-of-band to the employee  
- `must_change_password=1` forces change on first login  

## 5. HOD first-login onboarding

1. Login at `/hod/login`  
2. Forced password change if flag set  
3. Wizard at `/hod/onboarding`: verify official email (existing mailer + one-time code) → add one or more (year, department, semester, timetable) combinations → live preview → confirm  
4. Claims written to `hod_allocations` under unique constraint; conflicts abort  
5. `onboarding_complete=1` only after successful save  

## 6. Dean first-login onboarding

1. Login at `/dean/login`  
2. Password change if required  
3. Wizard at `/dean/onboarding`: email verify → select eligible HODs (intersecting department scope) → combined de-duplicated preview → save `dean_hod_assignments`  
4. Dean visibility is **derived** from assigned HODs’ allocations (no second ownership claim)

## 7. Allocation key & exclusivity

**Canonical key:** `(academic_year_id, branch_id, semester_id, timetable_id)`  

- Matches real timetable uniqueness in the schema  
- One HOD owner per exact key (DB unique constraint + transactional check)  
- Partial overlaps detected at allocation level  
- Concurrent claims fail on unique violation  
- Dean assignment does not create a competing ownership row  

## 8. Admin conflict resolution

- View all allocations and Dean–HOD links on `/admin/accounts/hod-dean`  
- Reset onboarding / activate-deactivate accounts  
- Ownership changes recorded in `allocation_history` (claim path implemented; admin reassign UI can extend the same table)

## 9. Test results

```
tests/test_hod_dean_upgrade.py — 9 passed
```

Covered: admin page access, unauthorized import, CSV template, valid HOD import + hashing + scopes, duplicate rejection, inactive login, must-change gate, exclusive unique constraint, regression logins.

**Limitations:** Full concurrent stress and email rate-limit e2e depend on live SMTP/PostgreSQL; local suite uses SQLite. Existing `test_hod_dean_second_faculty.py` suite should be re-run in CI after deploy.

## 10. Preservation

Student, teacher, admin, feedback forms, submissions, anonymity, second-faculty, and reporting routes are unchanged except for shared scope helpers that prefer allocations when present and fall back to `user_department_scopes`.
