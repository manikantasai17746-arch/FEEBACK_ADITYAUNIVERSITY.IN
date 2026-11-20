# Category headings on student feedback forms

Implemented on 2026-11-20 from `c0fe054`.

The faculty card on `/student/form/<id>` displays the heading assigned by the existing student form service for the stored category. The same template renders validation errors. Older forms without a recognized category use “Feedback”. The pending list still presents a single queue. Deploy with the standard application build.

## Verification

- `pytest tests/test_student_portal_theme.py -k feedback_categories_on_forms --tb=short` passed. The integration test uses three stored forms with the same title, verifies each heading on the corresponding form URL, and submits all three in the required order.
- Chrome checked the heading at 1280 px and 390 px in both themes.
- `pytest tests/test_student_portal_theme.py tests/test_hod_feedback_main.py tests/test_hod_feedback_lab.py tests/test_hod_feedback_second_and_results.py tests/test_photos_phase1.py --tb=short -rs` passed all 59 tests with Python UTF-8 mode and a workspace SQLite temporary directory.
