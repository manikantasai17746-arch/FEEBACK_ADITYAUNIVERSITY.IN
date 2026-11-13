# Phase 5B Verification Report (quick final check)

Scope: critical requirements only (A–E). The original verification (sections 0–7) changed no application
code; the follow-up **"Safety fixes" (section 8)** then fixed Important #1 and #2. No migrations were added;
v23 and v24 are untouched. All testing used an isolated temporary database
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
1. **[FIXED in section 8] Comment-order side channel (anonymity).** `suggestions_paged` orders by `answer_id`; for 5B rows
   that is `feedback_responses.id`, which follows submission order (rows of one submission are contiguous).
   Admin pages show per-student `submitted_at` to the second (`report_rows`, `tracking_rolls`, tracking CSV).
   An admin cross-referencing both could link comments to students. Teachers/HODs/Deans do not see
   per-student timestamps, so requirement D holds for them. Smallest fix: order comments by a hash of
   `response_group` (or randomly) instead of `answer_id`, and/or store day-only `submitted_at` for category submissions.
2. **[FIXED in section 8] Legacy status route bypasses the Phase 5 lifecycle.** `POST /hod/forms/<id>/status` (and the admin
   equivalent) let an open category form with submissions go back to `draft` (probed: 200). Questions stay
   locked by the existing "already has responses" check, so no answers are orphaned, but the form disappears from
   students. Also `services._freeze_category_eligibility` swallows `ScopeError` and runs after the status
   update (non-atomic), so a form can briefly be open with no snapshot (falls back to the live session rule), and
   an already-existing snapshot is never refreshed on re-open. Behaviour fails closed, not open.
3. **Main-form conflict rule inconsistent (NOT changed, deliberately out of scope):** `services._category_publish_errors` allows several Main forms per
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

---

## 8. Safety fixes (Important #1 and #2) - follow-up to this report

### 8.1 Exact files changed
| File | Change |
|---|---|
| `feedback_system/services.py` | Fix 1: new `_COMMENT_ORDER`; `suggestions_paged` and the text list in `question_stats` use it instead of `ORDER BY answer_id`; `suggestions_paged` no longer selects `submitted_at`. Fix 2: `set_form_status` refuses `draft` when the form has submissions (defence in depth). |
| `feedback_system/category_forms.py` | Fix 2: the transactional body of `publish()` moved unchanged into `_open_with_snapshot()`; new `change_status()` entry point for the legacy routes (delegates to `publish` / `close` / `_open_with_snapshot`, blocks the invalid transition). |
| `feedback_system/hod.py` | `form_status` calls `category_forms.change_status` and turns `ScopeError` into a JSON/flash error. Two import lines added. |
| `feedback_system/admin.py` | Same change for the admin `form_status` route. Two import lines added. |
| `tests/test_phase5b_safety_fixes.py` | **New**: 9 regression tests. |
| `feedback_system/PHASE_5B_VERIFICATION_REPORT.md` | This report. |

Not touched: `schema.py` (migrations v23/v24), `feedback_categories.py`, `student.py`, templates, JS, existing tests, and all
stored feedback data (`feedback_responses`, `answers`, `submissions` are read exactly as before; nothing is rewritten).

### 8.2 Fix 1 - anonymous comment privacy
* **Inspection.** Every comment listing goes through two queries in `services.py`: `suggestions_paged` (admin result page,
  teacher results page, dean `_all_comments`, all paginated) and `question_stats` (inline text answers; the templates only show
  its count). No other page, export or report reads `text_value`. Both used `ORDER BY answer_id`, which follows submission order.
* **Change.** Comments are now ordered by their own text (`LOWER(text_value), text_value`). The order is therefore a function of
  the *set* of comments only: submission order, `answer_id` and `submitted_at` have no influence. The ordering is applied in SQL
  before `LIMIT/OFFSET`, so every page uses the same order and pagination cannot restore the linkage. Identical texts are
  interchangeable, so ties leak nothing. No student identifier was ever returned with comments and none is now; comment text is unchanged.
* **Storage not modified.** No schema change, no data rewrite. (Existing submit-time shuffling inside a submission is kept.)

### 8.3 Fix 2 - legacy form status route
* **Callers inspected.** `POST /hod/forms/<id>/status` and `POST /admin/forms/<id>/status`; their only UI callers are
  `hod_forms.html` (publish / close buttons), `admin_forms.html` (open / close / archive buttons), `hod_form_new.html` and
  `admin_form_builder.html` via `static/js/app.js` (always `status='open'`). Nothing in the UI sends `draft`. Other users of
  `services.set_form_status`: the HOD/admin deadline-reopen paths (closed -> open) and `bulk_create_forms`; they are unchanged.
* **Invalid transition blocked.** Any move to `draft` of a form that already has submissions is refused: HTTP 409 with a JSON
  error for HOD AJAX calls, a flashed error + redirect for the admin form post, `ScopeError('invalid_transition')` from the
  service. The low-level `set_form_status` also raises, so no future caller can bypass it. The form stays open/closed and its
  snapshot is untouched.
* **Lifecycle reuse (no second implementation).** `change_status` delegates: draft -> open for Second/Lab forms = `category_forms.publish`;
  open -> closed = `category_forms.close`; draft -> open for Main forms = the same single atomic `_open_with_snapshot` transaction that
  `publish` uses (snapshot + `status='open'` + counts in ONE commit, guarded by `WHERE status='draft'`). Remaining moves (re-open,
  archive, draft -> closed, open/closed -> draft without submissions) still use `set_form_status`. Requesting the state a form is already in is a no-op.
* **Snapshots.** Publishing through either route now creates the snapshot atomically with opening (the old "open with no snapshot"
  window and the swallowed `ScopeError` no longer occur on this route); re-publishing a draft that was sent back refreshes the snapshot
  (`snapshot_eligibility` replaces it); re-opening a closed form retains the frozen list and creates no duplicates.
* **Authorization.** Re-checked inside `change_status`: another department's HOD gets 403 and nothing changes.
* **Main-form conflict rules unchanged.** Main forms still validate through `services.validate_form_for_publish` (several Main forms per
  session remain publishable via this route); a test pins this.

### 8.4 Tests executed (actual results, SQLite, isolated temporary DB)
| Command | Result |
|---|---|
| `python3 -m unittest tests.test_phase5b_safety_fixes` | **Ran 9 tests - OK** |
| `python3 -m unittest tests.test_phase5b_verification` | **Ran 12 tests - OK** (unchanged, still 12/12) |
| `python3 -m unittest tests.test_second_faculty_phase4` | **Ran 52 tests - OK** (unchanged, still 52/52) |
| all three modules in one run | **Ran 73 tests - OK** |
| Mutation check (temporarily restored `ORDER BY answer_id` and removed the draft guard) | the 4 relevant regression tests **failed**; with the fixes restored all 9 pass |

New tests: comment order differs from both the submission order and its reverse (submission times taken from
`services.submitted_rolls`, the admin-visible data); opposite submission orders give an identical display; pages of size 1/2/3 equal the full list;
`question_stats` and the rendered admin result page follow the same order; legacy `answers` rows too; submissions -> draft refused via HOD route,
admin route, `change_status` and the service (form and snapshot unchanged); valid HOD transitions (publish creates a snapshot equal to the eligible set,
close, re-open without duplicates, same-state no-op); valid admin transitions; draft without submissions can still be closed / archived / re-published;
other-department HOD gets 403; Main-form conflict rule unchanged.

**Not run:** the pytest-only files (including `tests/test_hod_dean_second_faculty.py`, `test_app.py`, `test_requirements.py`, `test_multi_round.py`)
because pytest is not installed and there is no network; no custom runner was built. By inspection none of them sends `draft` through the status route
or depends on comment order beyond `test_app.py` asserting single-comment lists.

### 8.5 Remaining limitations
* Comments are now alphabetical, so a reader sees them in that order (no recency order). A person who views a form's comment list before and after a
  student submits can still tell which comment is new (set difference); only the position-based linkage to submission *order* is removed.
* Comments are linkable by content to anyone who already knows what a particular student wrote; that is inherent to free text.
* `submissions.submitted_at` is still stored to the second and shown on admin tracking pages (unchanged; it no longer pairs with comment order).
* Important #3 (Main-form conflict inconsistency) is untouched by instruction; the pytest-only tests and the PostgreSQL concurrency race remain unrun.
* `services._freeze_category_eligibility` (used by re-open and `bulk_create_forms`) is unchanged: it still swallows `ScopeError` and runs after the status
  update. It is no longer used for draft -> open on this route, and re-open keeps the existing snapshot.

### 8.6 Updated Phase 5B readiness
Important #1 and #2 are resolved and covered by regression tests; the earlier 12/12 and 52/52 results are preserved. Remaining open items are #3
(Main conflict rule, deferred by your instruction) and the unrun pytest-only suites. **Assessment: Phase 5B is ready as the foundation for Phase 6**,
provided Phase 6 HOD pages call `category_forms.create_form / publish / close / change_status` and the pytest-only suite is run in an environment that has pytest
before production. Stopped here; Phase 6 not started.
