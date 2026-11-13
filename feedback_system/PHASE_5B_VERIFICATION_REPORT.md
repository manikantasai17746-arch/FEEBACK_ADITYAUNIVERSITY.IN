# Phase 5B Verification Report (quick final check)

Scope: critical requirements only (A–E). No application code was changed.
No migrations were added; v23 and v24 are untouched. All testing used an isolated temporary database
(bundled timetable import). Phase 6 was **not** started.

## 0. Findings about the supplied ZIP
- `PHASE_5_VERIFICATION_PENDING.md` is **not present** in the ZIP (only `PHASE_3_HANDOFF.md` and
  `feedback_system/PHASE_4_HANDOFF.md`). I could not read the previous pending-verification list.
- `tests/test_phase5b_verification.py` **did not exist**. I created it (12 tests, `unittest`-style,
  also collectable by pytest). This is the only file added besides this report.
- pytest is **not installed** and there is no network, so pytest could not be used.

## 1. Files read
`feedback_categories.py`, `category_forms.py`, `student.py`, `services.py` (submission, student forms,
teacher/results ownership, publish/freeze, tracking reports), `scoring.py` (faculty attribution),
`schema.py` (v23/v24), `security.py` (scope, CSRF, role gate), `hod.py` (status route),
`teacher.py` (results route), `dean.py`/`admin.py` (tracking use of `submitted_at`).

## 2. Requirements checked and result
| # | Requirement | Result | Evidence |
|---|---|---|---|
| A | Main → Main Faculty; Lab → same Main Faculty (no separate Lab Faculty); Second → assigned Second Faculty | PASS | `test_ownership`: `resolve_scope` faculty ids; teacher form listings and `scoring.faculty_scores_for_branches` attribute main+lab forms to the Main Faculty and the second form only to the Second Faculty; `lab_faculty_assignment_id` is None |
| B | No Lab → Main only; Lab → Main+Lab; Lab + confirmed Second → all three; rejected/unresolved Second excluded | PASS | `test_expected_categories_matrix`, `test_unavailable_categories_are_refused` |
| B | Ineligible students cannot submit | PASS | outsider, student added after publish (not in snapshot), lab switched to No Lab after publish |
| C | Categories submit independently; duplicates blocked; draft/closed/expired rejected; snapshot respected | PASS | `test_independent_submission_duplicate_and_anonymity`, `test_ineligible_snapshot_draft_closed_expired`, `test_lab_made_unavailable_after_publish_blocks_submission` |
| D | Answers carry no identity; completion tracking separate; teacher results page does not show respondent roll/name | PASS (see Important #1) | schema probes on `feedback_responses`, `anonymous_answers`, `submissions`; 5B answers absent from `answers`; view `submitted_at` is day-only; `test_teacher_results_page_does_not_identify_respondents` (200 for owner, 403 for the Second Faculty) |
| E | Snapshot equals eligible set; double publish, conflicts, zero-eligible and past-deadline blocked | PASS | `test_snapshot_matches_eligibility_and_double_publish`, `test_conflicts_prevented`, `test_unpublishable_cases` |
| E | Unauthorized users cannot publish/close or touch another department | PASS | `test_authorization` (other-dept HOD, teacher, student → 403 on create/publish/close); `test_hod_routes_scope_and_role_gating` (HTTP: other-dept HOD 403, own HOD 200 + snapshot created, teacher/anonymous blocked, missing CSRF → 400) |

## 3. Tests actually executed
| Command | Result |
|---|---|
| `python3 -m unittest tests.test_phase5b_verification -v` | **Ran 12 tests – OK** |
| `python3 -m unittest tests.test_second_faculty_phase4` | **Ran 52 tests – OK** |
| `python3 -m unittest tests.test_hod_dean_second_faculty` | **Not run** – module imports pytest (ModuleNotFoundError) |
| Full suite / other pytest-based files | **Not run** (pytest unavailable) |

During development two of my own tests failed because of test-harness mistakes (flask `g` cache shared
by the test client inside a pushed app context; freshly seeded HODs not past onboarding; a wrong
expectation about two live drafts). All were fixed in the test file. No application defect was behind them.

## 4. Confirmed defects and fixes
No critical defects were confirmed, and **no application code was changed**.

## 5. Remaining issues
### Critical (must fix before proceeding)
None found.

### Important (fix before production)
1. **Comment-order side channel (anonymity).** `suggestions_paged` orders by `answer_id`; for 5B rows
   that is `feedback_responses.id`, which follows submission order (rows of one submission are contiguous).
   Admin pages show per-student `submitted_at` to the second (`report_rows`, `tracking_rolls`, tracking CSV).
   An admin cross-referencing both could link comments to students. Teachers/HODs/Deans do not see
   per-student timestamps, so requirement D holds for them. Smallest fix: order comments by a hash of
   `response_group` (or randomly) instead of `answer_id`, and/or store day-only `submitted_at` for category submissions.
2. **Legacy status route bypasses the Phase 5 lifecycle.** `POST /hod/forms/<id>/status` (and the admin
   equivalent) let an open category form with submissions go back to `draft` (probed: 200). Questions stay
   locked by the existing "already has responses" check, so no answers are orphaned, but the form disappears from
   students. Also `services._freeze_category_eligibility` swallows `ScopeError` and runs after the status
   update (non-atomic), so a form can briefly be open with no snapshot (falls back to the live session rule), and
   an already-existing snapshot is never refreshed on re-open. Behaviour fails closed, not open.
3. **Main-form conflict rule inconsistent:** `services._category_publish_errors` allows several Main forms per
   session, while `category_forms.create_form`/`validate_for_publish` block a second live Main form of the same phase.
4. **Test coverage gaps:** `tests/test_hod_dean_second_faculty.py` and all other pytest-only files were not run;
   `PHASE_5_VERIFICATION_PENDING.md` was not available to compare against.

### Non-critical (postpone)
- `datetime.utcnow()` deprecation warnings (Python 3.12).
- Admin completion tracking lists respondents/non-respondents by roll. This is by design (completion is kept
  separate from answers) and admin-only.
- Submission requires both the live `session_students` check and the frozen snapshot, so a student removed from a
  session after publishing can no longer submit (stricter than the snapshot alone).

## 6. Is Phase 5B safe as the foundation for Phase 6?
**Yes, with the Important items tracked.** Every critical requirement verified above passed on an isolated
database, and no critical defect was found. Phase 6 should use `category_forms.create_form/publish/close`
(not the legacy status route) for the new HOD pages. Important #1 and #2 are worth scheduling before production.

## 7. Tests still pending
- Full pytest suite (pytest not installable here) and `test_hod_dean_second_faculty.py`.
- Concurrent-submission race under PostgreSQL (only SQLite exercised).
- Browser/UI-level checks of the student and teacher pages.
