# Category headings on student feedback forms

Implemented on 2026-11-20 from `c0fe054`.

The faculty card on `/student/form/<id>` displays the heading assigned by the existing student form service for the stored category. The same template renders validation errors. Older forms without a recognized category use “Feedback”. The pending list still presents a single queue. Deploy with the standard application build.

## Verification

- `pytest tests/test_student_portal_theme.py -k feedback_categories_on_forms --tb=short` passed. The integration test uses three stored forms with the same title, verifies each heading on the corresponding form URL, and submits all three in the required order.
- Chrome checked the heading at 1280 px and 390 px in both themes.
- `pytest tests/test_student_portal_theme.py tests/test_hod_feedback_main.py tests/test_hod_feedback_lab.py tests/test_hod_feedback_second_and_results.py tests/test_photos_phase1.py --tb=short -rs` passed all 59 tests with Python UTF-8 mode and a workspace SQLite temporary directory.

## Mobile typography (2026-11-25)

At widths up to 560px, the signed-in student stylesheet reduces headings, dashboard counters, introductory text and notification copy. Sizes use rem units to respect the larger-text control. Deploy the updated `feedback_system/static/css/student-theme.css` with the standard build. The change is CSS-only.

- A headless Chrome fixture loaded the application and student stylesheets. At 360, 390 and 560px the main heading measured 22.4px, the dashboard introduction 14px, counters 22px, question headings 16px and the textarea 16px. All five increased with larger text enabled. At 561 and 1280px the desktop rules applied. These were computed-style checks on representative markup.
- `python -X utf8 -m pytest -q tests/test_student_portal_theme.py --tb=line` on temporary SQLite gave 10 passes, one failure and five setup errors. The student fixture found no student, and the category test found no second-faculty assignment.
- `python -X utf8 -m pytest -q tests --maxfail=5 --tb=line -o addopts=` stopped with five account-setup failures and one skip. Four tests could not retrieve their user and one received the teacher login redirect instead of the photo setup redirect. The remainder of the suite was not exercised.

### Dashboard counters in one row

The phone breakpoint now narrows the gap between the three dashboard counters to 0.5rem and keeps the existing equal-width columns. Chrome rendered the counter markup from the dashboard template at 320, 390, 560 and 1280px. All three labels aligned on one row and fit their columns in both default and larger-text modes.

After this CSS change, `python -X utf8 -m pytest -q tests/test_student_portal_theme.py --tb=line -o addopts=` gave 10 passes, one failure and five setup errors. The broader command above again stopped after five account-setup failures and one skip.

### Compact dashboard header

The phone header uses less vertical padding, a 1.25rem title, a .8125rem introduction and .75rem detail chips. The department chip in `student_dashboard.html` spans both columns. Deploy the template and student stylesheet together.

Chrome checked the header markup with sample values at 320, 390, 465 and 560px. All three rows aligned and each chip fit its column with default and larger text. At 561 and 1280px the header used its desktop flex layout. Both pytest commands from the counter check reproduced the same results after the header change.

### Pending form titles on phones

At widths up to 560px, Waiting for your feedback shows the stored title on each row. Available forms use the existing form link and locked forms use a disabled label. Deploy `templates/student_dashboard.html` and `static/css/student-theme.css` together.

Chrome rendered the dashboard with an available Phase 1 form and a locked custom form. At 320, 390, 512 and 560px the pending list showed only the two titles. At 561 and 1280px it showed subject details, the deadline and the original action labels. Link and lock checks passed at every width. Both pytest commands documented below reproduced the same results.

### Compact Change password page

On 2026-11-25 the student Change password header, form and safety tips received tighter phone spacing. Deploy `templates/change_password.html` and `static/css/student-theme.css` together.

Chrome rendered the template with a synthetic student at 320, 390, 512, 560, 561 and 1280px in light and dark modes. The new-password visibility toggle worked at each width. On phones, fields and buttons stayed at least 44px tall and fit the viewport. Inputs used 16px type, requirements 12px and safety tips 13px.

`python -X utf8 -m pytest -q tests/test_student_portal_theme.py --tb=line -o addopts=` gave 10 passes, one failure and five setup errors due to missing fixture records. `python -X utf8 -m pytest -q tests --maxfail=5 --tb=line -o addopts=` stopped at the same five account-setup failures noted above, with one skip.

### Pending feedback grouped by title on phones

On 2026-11-25, the pending list on `GET /student/` switched to one disclosure per form title at widths up to 560px. The summary shows the earliest available deadline. Expanding it reveals each teacher, subject, feedback category, deadline and available action. A group without deadlines displays “No deadline”. Desktop retains individual cards. Each form keeps its original ID, eligibility and lock state.

The dashboard groups the current student's pending records from `services.student_forms`. Grouping is for display only and does not query answers. Deploy `feedback_system/student.py`, `templates/student_dashboard.html` and `static/css/student-theme.css` together with the normal build. No migration is required.

- Headless Chrome checked the rendered template with synthetic forms at 320, 390, 560, 561 and 1280px. Phone checks passed for initial collapse, expanding and closing groups, the earliest deadline, categories, locked and available actions, missing deadlines and horizontal fit. The two wider viewports displayed the individual cards.
- `uv run --offline --no-project --with-requirements requirements-dev.txt python -X utf8 -m pytest -q tests/test_student_portal_theme.py --tb=line -o addopts=` yielded 11 passes, one failure and five setup errors. The new grouped-dashboard regression passed. Other fixtures lacked a student or second-faculty assignment.
- `uv run --offline --no-project --with-requirements requirements-dev.txt python -X utf8 -m pytest -q tests --maxfail=5 --tb=line -o addopts=` stopped after five account-setup failures and one skip. Four tests could not retrieve a user and one received the teacher login redirect. The rest of the suite was not run.

#### Blank mobile pending list

On 2026-11-25 the local Flask preview was still running the earlier application while serving the edited stylesheet. The mobile rule could hide individual cards even on a page without grouped cards. The dashboard template now emits the group container only for populated groups. The stylesheet hides individual cards only beside that container. The local preview was restarted to load the new dashboard.

Headless Chrome checked the full dashboard with two synthetic pending forms at 320, 390, 500, 560 and 561px. Both the grouped page and a render without grouping data passed at every width. Phone checks covered the count, a single visible Phase 1 summary, its deadline, expansion to both teachers, categories, individual deadlines and lock state. The fallback exposed both individual cards. The two pytest commands above were repeated with the same outcomes (11 passed, one failure and five errors in the targeted module, then five failures and one skip before the broader run stopped).

#### Answer-sheet styling for mobile phase cards

The fold and marks now use the student theme’s orange accents. A 390px Chrome fixture verified both computed colors and the Details interaction in light and dark modes. The two pytest commands under “Pending feedback grouped by title on phones” were rerun after this color change. The portal module reported 11 passes, one failure and five setup errors. The broader run stopped after five account-setup failures with one skip.

On 2026-11-25, the mobile grouped card gained a folded upper-right corner, left-edge marks and a subtle shadow in `feedback_system/static/css/student-theme.css`. The same styling applies to custom form titles. Deploy the stylesheet with the standard build.

Chrome checked the dashboard template at 320, 390, 500, 560 and 561px in default light mode and dark mode with larger text. All ten checks passed. At phone widths the deadline was visible, the fold cleared the Details control, and the card opened and closed without horizontal overflow. At 561px the grouped summary was hidden as expected. The two pytest commands recorded above yielded 11 passes, one failure and five setup errors in the portal module, followed by five account-setup failures and one skip before the broader run stopped.

#### Mobile feedback history row

On 2026-11-25, the feedback history navigation on `/student/` began fitting its available categories in one equal-width row up to 560px. Each count has its own line beneath the category name. Deploy `templates/student_dashboard.html` and `static/css/student-theme.css` together. Links reuse the existing category query, and counts come from the same student-scoped history.

Headless Chrome checked the dashboard navigation markup with one, two and three categories at 320, 390, 500, 560 and 561px in default and larger text. All 30 checks passed, including single-row alignment and horizontal fit on phones, link destinations and the selected category. The two pytest commands under “Pending feedback grouped by title on phones” were rerun. The portal module gave 11 passes, one failure and five setup errors due to absent fixture records. The broad run stopped after five account-setup failures with one skip.

## Shorter student form introduction

On 2026-11-25 the student form began showing the feedback category, teacher and subject above a native “Class details” disclosure. It contains the available designation, department, timetable, semester and academic year. Phones use a smaller portrait and tighter card spacing. A short notice explains anonymity and the one-submission limit. Open forms with a deadline display “Due” and the existing formatted time. The template recognizes the standard instructions in previously saved forms and displays custom instructions on initial load and after validation errors.

Deploy `feedback_system/student.py`, `templates/student_form.html` and `static/css/student-theme.css` together with the normal build (template and stylesheet paths are relative to `feedback_system/`). Schema v29 requires no migration for this change. The edit uses the existing student route, authorization, form metadata and submission controls.

- `uv run --offline --no-project --with-requirements requirements-dev.txt python -X utf8 -m pytest -q tests/test_student_portal_theme.py -k shortens_default_copy --tb=short -o addopts=` passed with 1 test and 17 deselected. This exercises rendered HTML with synthetic form data. Phone appearance was not browser-verified.
- `uv run --offline --no-project --with-requirements requirements-dev.txt python -X utf8 -m pytest -q tests --maxfail=5 --tb=line -o addopts=` stopped with five failures in `test_account_setup.py` and one skip. Four tests received no fixture user, and one expected photo setup but received a teacher login redirect. The remainder of the suite was not exercised.

## Square faculty photo and single-row ratings

On 2026-11-25 the student form stylesheet made both the faculty portrait and its initials fallback square. Rating options sit in one row with a selection circle above each label and score. Phone question headings use smaller type. Each option has a minimum 44px tap target. Longer custom scales can scroll within the row. Deploy `feedback_system/static/css/student-theme.css` with the normal build. This presentation change requires no migration and touches no form data, permissions or submission code.

Headless Chrome loaded the application and student stylesheets with synthetic form markup at 320, 386, 455, 560, 640, 768 and 1280px, in default and larger-text modes. All 14 cases displayed five choices in one row without scrolling, met the minimum tap target, rendered both avatars square and selected the last radio by clicking its label.

Both pytest commands in “Shorter student form introduction” were repeated. The focused check passed (1 passed, 17 deselected). The broader run stopped at the same five account-setup failures with one skip.

### Larger faculty photo on phones

On 2026-11-25 the phone portrait grew from 2.5rem to 4rem and its fallback initials from 1rem to 1.5rem. Deploy the student stylesheet with the normal build. Headless Chrome checked synthetic cards at 320, 386, 476 and 560px. Both the photo and initials square measured 64px in default mode and 68px with larger text. In all eight layouts the faculty details fit beside the square.

Both pytest commands under “Shorter student form introduction” were rerun. The focused test passed (1 passed, 17 deselected). The regression suite stopped after the five previously recorded account-setup failures, with one skip.
