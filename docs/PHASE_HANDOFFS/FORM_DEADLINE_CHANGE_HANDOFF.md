# Form Management: change a form's deadline (2026-11-21)

**Goal:** let the HOD change the deadline of a form (shown as "Phase 1 ..." in the Form column) straight from the Recent Forms table on Form Management (`/hod/forms`) and on the Overview tab.

**Behaviour**
- Every non-archived row has a "Change deadline" button (Draft, Published and Closed forms). It opens a dialog pre-filled with the current deadline in college time (+05:30 by default).
- Save posts to the existing `POST /hod/forms/<id>/deadline` route. No route, service or schema change: HOD branch scope, CSRF, the "must be in the future" rule and audit logging are unchanged.
- Existing route behaviour kept: saving a future deadline on a **closed** form reopens it (the dialog says so); a past deadline is refused with a flash message.
- The Status column now shows the deadline for every status as formatted local time ("closes 01 May 2030, 09:30 AM" / "deadline ...") instead of raw stored UTC text, or "No deadline".

**Files:** `templates/_hod_recent_forms.html` (button, status text, dialog), `static/js/hod_overview.js` (dialog open/close), `tests/test_form_deadline_change.py` (new).

**Schema / migrations:** none. **Dependencies:** none.

**Tests:** `tests/test_form_deadline_change.py` (3). It also fixes `tests/test_deadline_and_pending.py::test_hod_forms_displays_saved_deadline`, which failed on the previous HEAD because the row had no deadline action or formatted date.

**Limitations**
- Changes one form at a time (each subject/section form has its own deadline). There is no "change all forms of this phase" bulk action.
- Admin's own Forms page is unchanged (it already had a Change control).
