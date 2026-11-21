# Form Management: change a form's deadline (2026-11-21)

**Goal:** let the HOD change the deadline of a form (shown as "Phase 1 ..." in the Form column) straight from the Recent Forms table on Form Management (`/hod/forms`) and on the Overview tab.

**Behaviour**
- Every non-archived row has a "Change deadline" button (Draft, Published and Closed forms). It opens a dialog pre-filled with the current deadline in college time (+05:30 by default).
- The dialog has two options. **This form only (current)** is the default and posts to the existing `POST /hod/forms/<id>/deadline` route. **All Phase N forms in my department** (shown for forms whose `phase` is `phase1` / `phase2`; hidden for custom-named forms) posts to the new `POST /hod/forms/deadline-phase` route.
- Phase route: sets one new deadline on every **Draft and Published** form of that phase in the HOD's department(s) (`scoped_branch_ids_or_none`), via `services.set_form_deadline` (one audit entry per form plus `hod.form.deadline_phase`). **Closed forms are left unchanged and never reopened**; the flash message says how many were skipped. Other phase and other departments are untouched. Same CSRF, future-date rule and 400 for an unknown phase.
- Single-form save uses the existing route. No route, service or schema change: HOD branch scope, CSRF, the "must be in the future" rule and audit logging are unchanged.
- Existing route behaviour kept: saving a future deadline on a **closed** form reopens it (the dialog says so); a past deadline is refused with a flash message.
- The Status column now shows the deadline for every status as formatted local time ("closes 01 May 2030, 09:30 AM" / "deadline ...") instead of raw stored UTC text, or "No deadline".

**Files:** `hod.py` (`form_deadline_phase`; `phase` added to the two recent-forms queries), `templates/_hod_recent_forms.html` (button, status text, dialog with scope options), `static/js/hod_overview.js` (dialog), `tests/test_form_deadline_change.py` (new).

**Schema / migrations:** none. **Dependencies:** none.

**Tests:** `tests/test_form_deadline_change.py` (6: single form, past date refused, whole phase incl. closed/other-phase/other-department untouched, bad phase, dialog options). It also fixes `tests/test_deadline_and_pending.py::test_hod_forms_displays_saved_deadline`, which failed on the previous HEAD because the row had no deadline action or formatted date.

**Limitations**
- Only Phase 1 / Phase 2 (stored `phase`) can be changed in bulk. Custom-named forms are changed one at a time.
- A bulk change skips closed forms on purpose; reopen those individually.
- Admin's own Forms page is unchanged (it already had a Change control).
- The dialog is moved to `<body>` on load. `.container` is a stacking layer (`z-index:1`) below the sticky header (`z-index:50`), so a dialog left inside it had its top (title, first option) hidden behind the header. The same placement applies to the older confirm dialog `#rfOv` in this macro (not changed here).
