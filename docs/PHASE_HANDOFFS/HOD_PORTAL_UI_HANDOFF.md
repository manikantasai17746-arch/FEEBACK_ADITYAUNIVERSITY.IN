# HOD portal UI handoff — Checkpoint 2

Branch hod-dashboard-ui starts at 38ccd84 and includes the staged Checkpoint 1 Overview and shared header. The remaining HOD pages were compared with the rendered HOD preview and styled through an explicit role and template opt-in. The application uses the existing local fonts and image assets.

## Pages and workflow

| Preview pages | Live implementation |
| --- | --- |
| Main, Second, and Lab lists and creation (six pages) | The existing category blueprints still own listing, filters, authorized eligibility previews, and form creation. Main creation now presents subjects, matching sections, and the selection summary in three panels. Lab creation now initializes the shared wizard from the Lab API response. |
| Department Forms, Create feedback form, Select teachers, and My question sets | The existing form-management actions, builder, picker round trip, shared question sets, and editing page carry through the preview styles. |
| Feedback Results | The existing category cards, filters, real PDF link, publication actions, and selection count remain available. The confirmation handler runs from the HOD script under enforced CSP. |
| My Actions | Shows a summary and cards using the Dean-assigned actions already returned by the live route. Reply and update actions use the existing forms. |
| Performance, Historical Comparison, and Multi-round Comparison | The report bands, filters, score colors, and tables use existing data. The Multi-round Comparison page links to the live score-band report as a companion page. |
| Onboarding and Profile | The onboarding preview shows a copy of Overview. The live first-login wizard received the portal styling and keeps its verification and scope-claim steps. The shared profile template opts in for HOD. |

The preview contains mock actions and overlays for parts of form management, question editing, and results, plus mock action creation. This checkpoint uses the live pages and existing access-controlled controls for those workflows. The patch introduces no Python or schema changes. Tests exercise the existing CSRF, authorization, department scoping, and anonymous-results boundaries.

## Verification

Browser checks used Chrome DevTools with a synthetic SQLite fixture and CSP enforcement enabled. All 17 scoped pages and the existing score-band companion rendered at 375, 768, and 1280 px in light and dark mode, a total of 108 page and viewport combinations. The report tables use keyboard-focusable horizontal scrolling where needed.

The browser exercised Main subject filtering, selection and clearing, authorized preview, question-set display, review, and a successful draft save. It exercised the Lab and Second Faculty eligibility previews, Department Forms filtering, the teacher picker round trip, result selection and canceling publication, action selection, historical comparison and the live score-band link, and onboarding scope selection. The teacher and Dean profile outputs did not load the HOD content theme.

The initial browser checks found a mobile overflow in the rating summary table and a baseline Lab wizard that stayed on “Loading sessions.” The table received a scroll region and the shared wizard now starts on the Lab markup and consumes the existing Lab session groups. The final browser run passed both checks.

The pytest runs used UTF-8 mode on Windows and temporary SQLite databases with TEMP, TMP, and TMPDIR pointed to writable workspace scratch storage.

| Command | Result |
| --- | --- |
| python -X utf8 -B -m pytest -ra --tb=short tests/test_hod_dashboard_theme.py | 30 passed |
| python -X utf8 -B -m pytest -ra --tb=short tests/test_hod_dashboard_theme.py tests/test_hod_feedback_lab.py | 38 passed on the final Lab wizard change |
| python -X utf8 -B -m pytest -ra --tb=short | 848 passed, 1 failed, 1 skipped in 20 min 24 sec |
| node --check feedback_system/static/js/hod-theme.js | Passed |
| node --check feedback_system/static/js/hod_feedback_main.js | Passed |

The focused HOD and adjacent workflow run passed all 168 tests. It used the following PowerShell commands.

```powershell
$hodTests = @(Get-ChildItem -Name -Path tests -Filter 'test_hod_*.py' | ForEach-Object { "tests/$_" })
python -X utf8 -B -m pytest -ra --tb=short @hodTests tests/test_comparison.py tests/test_dean_action_assignment.py tests/test_deadline_and_pending.py tests/test_photos_phase1.py tests/test_profile_and_second_views.py tests/test_question_sets.py tests/test_student_portal_theme.py
```

The full suite reproduced the known failure in tests/test_lost_device_sso_b3.py::test_start_page_shows_buttons_only_when_available. The baseline recovery page lacks the expected Microsoft button, as confirmed against 38ccd84 during Checkpoint 1. The suite skipped tests/test_assignment_setup_ui.py because Playwright is unavailable. The separate Chrome DevTools browser run completed using the local browser.

## Control follow-up

The form list now opens the selected form's authorized detail page. The pending form picker, bulk reminder confirmation, and faculty report print control use the existing HOD script under enforced CSP. Lab and Second Faculty step labels are progress indicators. The existing controls advance the steps.

Chrome reached all 12 HOD navigation destinations and Security. The browser checks covered all three results PDF categories, form details, publish and unpublish, two performance print controls, the pending picker and individual and bulk reminders, score-band reset and send, action status updates, question set create, rename and delete, profile photo upload and password update, logout, and allocation preview and confirmation for a previously email-verified account. Email calls were stubbed and used synthetic accounts.

| Command | Result |
| --- | --- |
| python -X utf8 -B -m pytest -ra --tb=short tests/test_hod_dashboard_theme.py tests/test_hod_category_pdf.py tests/test_deadline_and_pending.py tests/test_dean_action_assignment.py tests/test_question_sets.py tests/test_profile_and_second_views.py | 89 passed |
| python -X utf8 -B -m pytest -ra --tb=short tests/test_hod_dashboard_theme.py tests/test_hod_feedback_lab.py tests/test_hod_feedback_second_and_results.py | 46 passed |

Live PostgreSQL, email delivery, and a deployed site were outside this verification. Checkpoint 2 completes the planned HOD visual rollout.

## Visual corrections from the supplied preview

The Overview uses the attachment's gold SVG artwork, seal illustration and stamp shapes. The logo draws in on load, replays from the button, and displays the completed artwork when reduced motion is enabled. The exact vector paths and the seal PNG are bundled in the repository. The status chips and table layout use scoped Recent Forms data, and View details opens the existing authorized detail route.

Multi-round Comparison renders the six score bands and active teacher cards using the existing department-scoped score helper. The original closed-round filters and report link are available. The preview's fake notifications, simulated publishing state, and mock action creation were excluded. The top navigation retains all 12 existing destinations. Onboarding continues to render the real setup wizard.

The scoped tests ran in a temporary SQLite database. The final command below returned 74 passed.

    python -X utf8 -B -m pytest -ra --tb=short tests/test_hod_dashboard_theme.py tests/test_comparison.py tests/test_score_marks_consistency.py tests/test_hod_feedback_main.py tests/test_hod_feedback_lab.py tests/test_hod_feedback_second_and_results.py

Chrome under enforced CSP verified the Overview and Comparison at 375, 768, and 1280 px, including the logo reveal and keyboard replay, reduced-motion fallback, the seal asset, the Recent Forms draft filter, and score-band selection. The 17 remaining HOD pages also passed the 375 px route smoke check. This corrective pass did not rerun the full suite or use live PostgreSQL or production data.


## Screenshot review follow-up

The screenshot review uses four section cards within `/hod/`. The active section, pending form type and selection, score filters, and result category and filters have bookmarkable URLs. The shared header displays nine primary links and a bell backed by the signed-in account's notifications. Second Faculty assignments are reached from Second Faculty Feedback.

Recent Forms uses the existing form lifecycle for publishing, closing, and reopening, including its deadline checks and publishing validation. The grouped Pending selector resolves forms within both the HOD's branch allocation and the selected category. Grouped PDFs label roll-number blocks by form scope. Reminder emails include open forms only, and all outcomes are reported inside the active panel. Score-band mailing rechecks the chosen band and filters on the server. The Overview's results panel requires opting in to publish results from an open form. Answers remain anonymous.

The focused test command returned 95 passed.

    python -X utf8 -B -m pytest -ra --tb=short tests/test_hod_dashboard_theme.py tests/test_deadline_and_pending.py tests/test_hod_feedback_second_and_results.py tests/test_hod_category_pdf.py tests/test_hod_feedback_main.py tests/test_hod_feedback_lab.py

Chrome at 375, 768, and 1317 px verified the section links, grouped form picker, score-band selection, composer reset, results category and open-form selection controls, and notification menu. Browser verification used synthetic SQLite records and stubbed mail. The HOD uses local fonts. The legacy Google Fonts import in the shared stylesheet was blocked under enforced CSP. Production database and SMTP delivery require environment-level verification.
