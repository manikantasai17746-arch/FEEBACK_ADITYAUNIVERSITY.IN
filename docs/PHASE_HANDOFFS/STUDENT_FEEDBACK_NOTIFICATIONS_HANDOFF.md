# Student feedback notifications

Implemented on 2026-11-20 from `890f08a`.

- Publishing Main Faculty, Lab, or Second Faculty feedback records an inbox entry for each eligible student in the publication transaction. Session-scoped forms use their frozen eligible students. Subject-scoped forms use their linked sessions. Drafts stay private.
- Opening My Feedback or Notifications during the final 24 hours records one reminder per outstanding form. The check reuses student form eligibility and skips expired or submitted forms. The inbox displays the current deadline in the app's local time and links to the published form.
- The existing inbox and its read actions retain student-scoped access. Migration 27 adds a nullable form reference and a unique index for the student, form, and notice type. Existing inbox entries work with a null form reference. Deploy with the standard build, which applies the additive migration at startup.

## Verification

- `pytest tests/test_student_portal_theme.py -k 'notifications or feedback_categories_on_forms' --tb=short` passed 2 tests. The category flow covers draft suppression, all three publications, the 24-hour boundary, completed exclusions, and read status after repeated visits.
- Embedded PostgreSQL verified migration 27 against existing inbox rows and exercised the publication and reminder inserts for subject and snapshot recipients, deduplication, the deadline boundary, and completed forms.
