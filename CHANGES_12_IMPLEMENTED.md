# All 12 Changes — Implementation Summary

Implemented on top of the existing Faculty Feedback System codebase.
Minimum response threshold default = **5**. Rating scale derived from
`question_options.value` (fallback Excellent=5 … Poor=1).

## Change 1 — Login tabs
- Student, Teacher, Admin login pages now include **HOD** and **Dean** tabs.
- HOD/Dean login pages already had all five tabs.
- Guest top-nav links include Student / Teacher / HOD / Dean / Admin.

## Change 2 — Student auto-next form
- After successful submit, the next pending open form is opened automatically.
- If none remain, student returns to the dashboard.

## Change 3 — Accessibility & mobile
- **Dark mode** toggle (persisted in `localStorage`).
- **Larger text** toggle (A+).
- Mobile-friendly rating/choice controls (min 44px tap targets).
- CSS variables and responsive filter/stat grids.

## Change 4 — Notification inbox
- Migration v10: `notifications` table.
- Routes: `/student/notifications`, mark-read, mark-all-read.
- Template: `student_notifications.html`.
- Nav link for students.
- (Notifications are created when forms are published / deadlines approach — hook points ready.)

## Change 5 — Admin dashboard cleanup
- Removed Quick Action cards: Academic years & semesters, Timetables & sections, Audit log.
- Download PDF reports section hidden on dashboard (use dedicated Reports page).
- Routes still exist; only dashboard surface was cleaned.

## Change 6 — Publish all / Publish selected
- Admin Results page: checkboxes per unpublished form.
- **Publish selected** and **Publish all** buttons.
- Optional “Skip forms with 0 responses” (default on).
- Routes: `results_publish_selected`, `results_publish_all`.

## Change 7 — Quick Actions: HOD & Dean import + Second faculty
- Added cards on Admin dashboard linking to existing HOD/Dean accounts page and Second faculty status page.

## Change 8 — Second faculty
- Already present in the base ZIP (assignments, import, HOD/Dean visibility). No regression.

## Change 9 — HOD / Dean restyle
- HOD dashboard already uses stat cards + styled table.
- Performance / Compare / Actions pages use the same card + data-table design language.
- Shared CSS improvements apply to all roles.

## Change 10 — HOD admin-style powers (scoped)
- Already present: forms, results, second faculty, branch scoping, server-side access checks.
- Extended with Performance section (Change 11).

## Change 11 — HOD Faculty Performance
- New module: `feedback_system/scoring.py` — **central scoring service**.
- Routes: `/hod/performance`, `/hod/performance/faculty/<id>`, `/hod/performance/compare`.
- Templates: marks sheet, detail (criterion + distribution), any-two-period comparison.
- Rules: (earned/max)×100, N/A when below threshold or no scoreable answers, anonymity preserved.

## Change 12 — Dean dashboard upgrade
- Faculty Performance page (reuses scoring service).
- **Improvement Action Tracker**: create/edit, statuses, priority, owner, due date, audit log.
- Migration v10 tables: `improvement_actions`, `improvement_action_log`.
- Nav updated on Dean dashboard.

## Database
- Migration **v10** (additive only): notifications, form_drafts, improvement_actions, improvement_action_log.
- Runs automatically on next app start. Backup recommended before production deploy.

## How to run
```bash
cd Teacher_Feedback_System
pip install -r requirements.txt
python app.py
```

## Defaults / open decisions applied
| Decision | Value used |
|----------|------------|
| Min response threshold | 5 |
| Dark mode / large text | Available to all roles via toolbar |
| Notifications | In-app, students |
| Publish all skips 0 responses | Yes (checkbox, default on) |
| HOD powers toggle by admin | Not added (always on for scoped HOD) |
