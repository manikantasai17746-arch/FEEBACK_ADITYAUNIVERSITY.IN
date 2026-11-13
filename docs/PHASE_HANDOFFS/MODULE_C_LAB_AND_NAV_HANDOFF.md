# Module C + HOD Navigation Handoff

**Date:** 2026-11-13  
**Schema:** v24 (no migration)

## Module C — Lab Faculty Feedback
- Routes: `/hod/feedback/lab`, `/lab/create`, `/lab/api/sessions`, `/lab/api/preview`, `/lab/api/create`
- Only `teacher_subject_settings.lab_status='available'` (same rule as `assignment_setup.lab_eligible_sessions`)
- Forms: `category_forms.create_form(..., category='lab', assignment_id=session_id)` per Phase 5B
- Lab faculty from `lab_faculty_assignments` shown for display/photo when present; form identity remains session-based
- Templates: `lab_list.html`, `lab_create.html`

## Navigation redesign
- HOD nav: Overview, Main Faculty Feedback, Second Faculty Feedback, Lab Faculty Feedback, Faculty Assignments (`/hod/pending`), Form Management, Feedback Results, Reports
- Overview (`/hod/`) category cards: assignments, published, eligible, submitted, pending, drafts
- Existing page URLs unchanged
- Second Faculty Feedback is a 200 placeholder until Module B

## Tests
`python -m pytest tests/test_hod_feedback_lab.py` → 8 passed  
Also `tests/test_hod_feedback_main.py` still passes.
