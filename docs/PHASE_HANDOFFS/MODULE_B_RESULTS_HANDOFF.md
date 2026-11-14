# Module B + Results Tabs + Completion Stats Handoff

**Date:** 2026-11-13  
**Schema:** v24 (no migration)

## Module B — Second Faculty Feedback
- Routes: `/hod/feedback/second`, `/second/create`, `/second/api/list`, `/second/api/preview`, `/second/api/create`
- Cards keyed by `second_faculty_assignments.id`; confirmed + non-removed only
- Forms via `category_forms.create_form(..., category='second', assignment_id=SFA id)`
- Same employee can appear on multiple assignment cards

## Results tabs (Prompt 5) + completion (Prompt 6)
- `/hod/results?tab=main|second|lab` — categories never mixed
- Cards: photo, name, emp ID, subject, section, room, eligible/submitted/pending
- Counts: "N answers from M students" via `scoring.form_marks` (M from submissions)
- Completion % = submitted/eligible (0 when eligible=0)
- Detail: `/hod/results/<fid>/detail` uses `services.question_stats` + scoring
- Filters: year, semester, branch, section, subject, faculty

## Tests
`python -m pytest tests/test_hod_feedback_second_and_results.py` → 8 passed
