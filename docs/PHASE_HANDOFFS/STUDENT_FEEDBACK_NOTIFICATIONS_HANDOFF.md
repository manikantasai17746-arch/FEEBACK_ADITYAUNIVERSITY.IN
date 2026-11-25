# Student feedback notifications

Implemented on 2026-11-20 from `890f08a`.

- Publishing Main Faculty, Lab, or Second Faculty feedback records an inbox entry for each eligible student in the publication transaction. Session-scoped forms use their frozen eligible students. Subject-scoped forms use their linked sessions. Drafts stay private.
- Opening My Feedback or Notifications during the final 24 hours records one reminder per outstanding form. The check reuses student form eligibility and skips expired or submitted forms. The inbox displays the current deadline in the app's local time and links to the published form.
- The existing inbox and its read actions retain student-scoped access. Migration 27 adds a nullable form reference and a unique index for the student, form, and notice type. Existing inbox entries work with a null form reference. Deploy with the standard build, which applies the additive migration at startup.

## Verification

- `pytest tests/test_student_portal_theme.py -k 'notifications or feedback_categories_on_forms' --tb=short` passed 2 tests. The category flow covers draft suppression, all three publications, the 24-hour boundary, completed exclusions, and read status after repeated visits.
- Embedded PostgreSQL verified migration 27 against existing inbox rows and exercised the publication and reminder inserts for subject and snapshot recipients, deduplication, the deadline boundary, and completed forms.

## Mobile inbox density

At widths up to 560px the student inbox starts with one title per row. A button expands its full title, kind, message, deadline, timestamp and applicable actions. Read state changes through the existing CSRF-protected forms. Wider screens and browsers with JavaScript disabled display the full notices. Deploy `student_notifications.html`, `student-theme.css` and `student-inbox.js` together.

- Chrome loaded the template with 15 synthetic notices, mixed read states and the bulk-read button. Before scrolling, it showed 11 complete rows at 320×568, 12 at 360×640, 13 at 390×844, 14 at 412×915 and 12 at 560×740. The checks used default text size. A row expanded to reveal the form link and read form, collapsed again, and fit horizontally. At 1280×850 the body remained visible in the desktop layout.
- `python -X utf8 -m pytest -q tests/test_student_portal_theme.py --tb=line -o addopts=` gave 10 passes, one failure and five setup errors. The test database lacked the student and assignment records required by those fixtures.
- `python -X utf8 -m pytest -q tests --maxfail=5 --tb=line -o addopts=` stopped after five account-setup failures with one skip. Four tests could not find their fixture user and one received the teacher login redirect.

## Unread-only inbox

On 2026-11-25 the inbox query began filtering by the current student and unread status before applying the 100-row limit. The existing CSRF-protected read actions save the flag, which also keeps reminder deduplication intact. After the redirect, a single read removes only that row, and “Mark all as read” shows “No unread notifications.” Deploy `feedback_system/student.py` and `feedback_system/templates/student_notifications.html` together with the normal build. No migration is needed.

- `uv run --offline --no-project --with-requirements requirements-dev.txt python -X utf8 -m pytest -q tests/test_student_portal_theme.py -k mark_as_read_removes --tb=short -o addopts=` gave 1 pass and 18 deselected. The Flask client checked the individual action, remaining row and count, bulk action, persistence after refresh and another student's read flag.
- `uv run --offline --no-project --with-requirements requirements-dev.txt python -X utf8 -m pytest -q tests --maxfail=5 --tb=line -o addopts=` stopped after the five account-setup failures described above with one skip.
