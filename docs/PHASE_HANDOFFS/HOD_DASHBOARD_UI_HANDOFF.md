# HOD Overview UI handoff

**Checkpoint 1 · 2026-11-20**

Branch hod-dashboard-ui starts at 38ccd84. Schema remains v26. Deployment uses the existing assets and configuration.

## Scope and decisions

The Overview at /hod/ renders the three category cards, department totals, and Recent Forms from the existing HOD dashboard context. The section cards for Pending submissions, Teachers by Score Band, and Feedback Results link to their existing pages. The notification bell was omitted. The Overview logo replays a short animation on the bundled Aditya University PNG and respects the browser's reduced-motion setting.

The full-width welcome and totals bands, folded cards, stamps, and section cards follow the rendered HOD preview. The signed-in HOD header is shared across HOD pages. The rest of the theme targets the Overview wrapper inside body.role-hod. The 12 portal destinations, Security, and Logout use their existing endpoints. The shared theme also uses the bundled Instrument Sans and Bricolage Grotesque fonts.

## Files

- feedback_system/templates/base.html loads the HOD assets, renders the shared navigation and active states, and provides the CSP nonce to the existing accessibility controls for HOD.
- feedback_system/templates/hod_dashboard.html renders the new Overview and its department-scope and no-forms states.
- feedback_system/static/css/hod-theme.css styles the header and Overview for light and dark modes and responsive widths.
- feedback_system/static/js/hod-theme.js controls mobile navigation and logo replay.
- tests/test_hod_dashboard_theme.py seeds a temporary SQLite database and checks scoped counts, both empty states, theme isolation, page statuses, and role, branch, and CSRF gates.
- docs/DEVELOPMENT_STATUS.md summarizes the checkpoint.

## Route and data checks

The Flask dashboard, its existing category aggregates, and the existing pending, score band, results, form management, and category routes supply all data and actions. Pending roll numbers remain in the existing pending workflow. The dashboard checks ensure those rolls and other departments' forms stay out of Recent Forms.

The change is confined to templates, static assets, tests, and documentation. Existing role and branch checks, CSRF handling, audit events, and form lifecycle operations continue to run through their existing handlers.

## Verification

The pytest runs used Python 3.12 and temporary SQLite databases. On this Windows sandbox, setting TEMP, TMP, and TMPDIR to a writable workspace scratch directory allowed SQLite to open the fixture databases.

| Command | Result |
| --- | --- |
| python -B -m pytest -ra --tb=short tests/test_hod_dashboard_theme.py | 30 passed |
| python -B -m pytest -ra --tb=short tests/test_hod_dashboard_theme.py tests/test_hod_feedback_main.py tests/test_hod_feedback_second_and_results.py tests/test_hod_feedback_lab.py tests/test_hod_category_pdf.py tests/test_hod_teacher_picker.py tests/test_hod_dean_second_faculty.py tests/test_hod_dean_mail_delete.py tests/test_student_portal_theme.py | 87 passed |
| python -B -m pytest -ra --tb=short | 847 passed, 2 failed, 1 skipped (22 min 28 sec) |
| node --check feedback_system/static/js/hod-theme.js | Passed |

The initial existing-test run returned 1 pass and 56 setup errors before the writable temp path was configured. The first new-test run then returned 30 setup errors because the test seed referenced a nonexistent table. The fixture now uses the existing form-session and Second Faculty relationships. Both recovered in the passing runs above.

The targeted command python -B -m pytest -ra --tb=short tests/test_lost_device_sso_b3.py::test_start_page_shows_buttons_only_when_available returned 1 failure on both this branch and a clean archive of 38ccd84. The baseline recovery template renders the email-code form and has no Microsoft button markup. The test expects Continue with Microsoft and fails at that assertion.

The command python -B -m pytest -ra --tb=short tests/test_recovery_lock_b1.py::test_only_the_recovery_pages_use_the_lock tests/test_requirements.py::test_admin_adds_requirement_and_publish_links_it returned 1 failure and 1 pass on both checkouts. The recovery-lock check calls Path.read_text with no encoding and the Windows cp1252 default cannot decode a byte in the UTF-8 source. The command python -X utf8 -B -m pytest -ra --tb=short tests/test_recovery_lock_b1.py::test_only_the_recovery_pages_use_the_lock passed on this branch. The full suite skipped tests/test_assignment_setup_ui.py because Playwright was unavailable. The independent Chromium check below used Chrome DevTools.

Chromium rendered Overview at 375, 768, and 1280 px in light and dark mode. The desktop capture was compared against the supplied HOD preview. The header showed all 12 portal links, Security, Logout, and the two accessibility controls at each width. At 1280 px, the links fit in two rows. At 375 px, the expanded menu wraps the links in six rows. Keyboard menu controls, Escape focus return, visible focus, A+, logo replay, and reduced motion passed with CSP enforcement enabled. At 375 px, Recent Forms stayed below the sticky header and its table scrolled horizontally from the keyboard.

A browser smoke run in the default CSP mode visited 22 HOD pages, including all 18 preview counterparts. It also opened the existing pending-roll list, checked the PDF link, opened and closed the teacher's Second Faculty modal, and advanced Teacher Assignment Setup to room configuration.

## Limitations and next checkpoint

PostgreSQL integration was outside this UI verification. The existing app stylesheet still declares a remote font import. The new HOD theme uses bundled fonts, and the browser smoke ran against local assets. The remaining HOD page designs can be implemented in a later checkpoint after explicit approval.
