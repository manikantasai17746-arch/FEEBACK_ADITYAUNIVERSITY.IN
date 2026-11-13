# Project Context — Faculty Feedback System

**Last verified against GitHub source:** 2026-11-13  
**Repository:** `manikantasai17746-arch/FEEBACK_ADITYAUNIVERSITY.IN`  
**Documentation branch:** `docs/ai-agent-knowledge-base`

## 1. Purpose

A faculty feedback platform for Aditya University. Students submit feedback for the teaching sessions and categories for which they are eligible. Authorized staff manage forms and monitor completion. Faculty-facing results must remain anonymous.

## 2. Technology and deployment

- Python / Flask application with an app factory.
- Jinja2 server-rendered templates, CSS, and JavaScript.
- SQLite for local development and tests.
- PostgreSQL / Supabase for hosted production through `feedback_system/pg.py`.
- Excel timetable import using `openpyxl`.
- ReportLab is used for PDF report generation.
- Vercel deployment is documented in `DEPLOY.md`.

The README includes historical feature and setup descriptions; some schema/test descriptions predate later phases. Verify current code.

## 3. Important source map

| Path | Responsibility |
|---|---|
| `app.py` | Local application entry point |
| `api/index.py` | Serverless entry point; verify deployment config before changing |
| `feedback_system/__init__.py` | Flask app factory, config, blueprint registration, hooks, headers |
| `feedback_system/db.py` | Database connection lifecycle and migration runner |
| `feedback_system/pg.py` | PostgreSQL adapter and SQL translation |
| `feedback_system/schema.py` | Versioned database migrations |
| `feedback_system/security.py` | CSRF, role checks, branch-scope helpers, audit/security utilities |
| `feedback_system/auth.py` | Authentication and current-user helpers |
| `feedback_system/services.py` | Shared business logic, forms, tracking, results |
| `feedback_system/feedback_categories.py` | Category scope resolution and shared eligibility |
| `feedback_system/category_forms.py` | Category form draft/publish/close lifecycle |
| `feedback_system/scoring.py` | Faculty attribution and scoring |
| `feedback_system/assignment_setup.py` | Phase 3 teacher room/lab setup service |
| `feedback_system/second_faculty_service.py` | Second Faculty assignment service |
| `feedback_system/hod.py` | HOD routes and portal |
| `feedback_system/admin.py` | Admin routes and portal |
| `feedback_system/dean.py` | Dean routes and portal |
| `feedback_system/teacher.py` | Teacher routes and results |
| `feedback_system/student.py` | Student routes and submission |
| `feedback_system/second_faculty.py` | Second Faculty portal |
| `feedback_system/templates/` | Jinja templates |
| `feedback_system/static/` | CSS, JavaScript, images |
| `tests/` | Unit, integration, and browser/UI tests |

Blueprints are registered in `feedback_system/__init__.py`. Verify registrations and route decorators before adding routes.

## 4. Data model and relationships

The Phase 5B verification work reports schema v24. Confirm the actual latest migration in `feedback_system/schema.py` before creating any migration.

- `branches` → `timetables` → `class_sessions`.
- `class_sessions` identifies a teaching assignment/session: section/timetable + subject + Main Faculty. Phase 3 documents uniqueness on timetable, subject code, and teacher.
- `session_students` associates eligible students with a specific session.
- `teacher_subject_settings` stores room/lab setup per session. `lab_status`: `available`, `none`, or NULL/not configured. Legacy `lab_available` is retained and mirrors availability.
- `second_faculty_assignments` links a Second Faculty assignment to a session. Its row `id` is the identity for Second Faculty feedback.
- `feedback_forms` stores category forms and references the appropriate session or Second Faculty assignment.
- `form_questions` and `question_options` store questions/options.
- `submissions` records completion and contains student identity.
- Category answer storage is separate; inspect current schema and views before querying it.
- Legacy `assignments` and `feedback` structures remain for backward compatibility.

This is not a substitute for inspecting actual migration definitions and foreign keys.

## 5. Roles and authorization

The application has role-specific flows for Admin, HOD, Dean, Teacher, Student, and Second Faculty. Shared auth/security helpers implement role gates.

Important HOD/Dean branch-scope helpers:
- `scoped_branches(user)`
- `scoped_branch_ids_or_none(user)`
- `require_branch_access(user, branch_id)`

The security module documents Admin as all-branch scope and HOD/Dean scope derived from allocations/assignments with fallback department scopes. New routes must validate scope server-side for every relevant object/action. Never rely on hidden fields or client-side filtering.

State-changing requests use the CSRF mechanism initialized in the app factory. JSON POSTs commonly use `X-CSRF-Token`; form POSTs use the existing token field. Inspect exact route behavior.

## 6. Feedback categories — authoritative business meaning

Stored values are `main`, `second`, and `lab`; canonical API names may be `main_faculty`, `second_faculty`, and `lab_faculty`.

### Main Faculty
- Scope identity: `class_sessions.id`.
- Regular subject teaching by the Main Faculty.
- Results belong to the Main Faculty assigned to the session.

### Second Faculty
- Scope identity: `second_faculty_assignments.id`, not employee code.
- Only a confirmed assignment with a resolved, active Second Faculty member is valid.
- It must point to the matching session; Second Faculty must differ from Main Faculty.
- The session must be Lab Available.
- Results belong to the assigned Second Faculty.
- Multiple assignment rows remain separate. Never merge them because employee codes match.

### Lab
- Scope identity: `class_sessions.id`.
- Practical/lab teaching by the SAME Main Faculty.
- Requires `teacher_subject_settings.lab_status='available'`.
- Does not use or create a separate Lab Faculty assignment.

### Expected availability

| Session setup | Expected feedback categories |
|---|---|
| No Lab | Main |
| Lab Available, no confirmed Second Faculty | Main + Lab |
| Lab Available + confirmed Second Faculty | Main + Lab + Second (one per confirmed assignment) |

Authoritative implementation: `feedback_system/feedback_categories.py`, especially `resolve_scope`, `expected_categories`, and eligibility functions.

## 7. Eligibility and form lifecycle

- `feedback_categories.resolve_scope(category, assignment_id)` resolves scope from database records.
- Main and Lab use `class_sessions.id`; Second uses `second_faculty_assignments.id`.
- Shared eligibility is based on active students linked to the exact session through `session_students`.
- `eligible_students`, `eligible_count`, and `student_is_eligible` are shared eligibility APIs.
- `snapshot_eligibility(conn, form_id, scope)` stores the publication-time eligible set inside the caller's transaction.
- Phase 5B report signature: `category_forms.create_form(actor, category, assignment_id, title, questions, instructions='', deadline_at=None)` creates a draft.
- `category_forms.publish(actor, form_id)` validates, freezes eligibility, and opens the form transactionally.
- `category_forms.close(actor, form_id)` closes a form.
- `category_forms.change_status` is the lifecycle-aware entry point used to protect legacy HOD/Admin status routes. Inspect its current signature before use.

Do not create a parallel eligibility engine or bypass the category form lifecycle.

## 8. Anonymity and completion tracking

Critical architectural boundary:

- Completion tracking uses `submissions` and/or publication eligibility snapshots. These contain student identity for authorized administrative completion workflows.
- Anonymous answers must not be joined back to `submissions` or exposed with student identifiers in teacher-facing results, HOD/Dean results, comments, analytics, or exports.
- Phase 5B verification documents the `anonymous_answers` view and `feedback_responses` design. Read the current schema/view and services before writing answer queries.
- A Phase 5B safety fix removed answer-order correlation in comment listings. Preserve it. Do not reintroduce ordering by answer/response ID or submission sequence.

Eligible student names/rolls may appear only in an explicitly authorized eligibility preview/completion workflow where permitted by role and scope. Never associate a person with an answer.

## 9. Existing HOD functionality and Phase 6 direction

Existing HOD routes include dashboard/forms, form creation/status/deadline, timetable/session/subject APIs, pending/results/performance/comparison/actions/question sets, and Second Faculty management. Verify exact route names in `feedback_system/hod.py`.

Phase 6 proposal:
1. Main Faculty module.
2. Second Faculty module.
3. Lab module.
4. Overview/statistics and HOD navigation.
5. Regression tests and handoff.

The proposed separate `feedback_system/hod_feedback.py` blueprint, templates, scoped CSS, CSP-safe JS, and tests are not implemented in the inspected baseline. See DEVELOPMENT_STATUS.

## 10. Deployment and environment

Read `DEPLOY.md` and `.env.example` before deployment/configuration changes. Never commit `.env`, credentials, tokens, production database URLs, or private student information. Do not run destructive test imports against production.

## 11. Verified source documents

- Root `README.md`: overview and historical feature guide; some sections are older.
- `PHASE_3_HANDOFF.md`: teacher assignment setup, lab status, migration v21, routes, tests, limitations.
- `feedback_system/PHASE_4_HANDOFF.md`: Second Faculty assignment rules, no-lab guard, tests, limitations.
- `feedback_system/PHASE_5B_VERIFICATION_REPORT.md`: Phase 5B category verification and safety fixes.
- `IMPLEMENTATION_REPORT.md`: earlier HOD/Dean/Second Faculty implementation report; migration/test snapshots are historical.
- `DEPLOY.md`: deployment guide; verify against current app before relying on version-specific examples.
