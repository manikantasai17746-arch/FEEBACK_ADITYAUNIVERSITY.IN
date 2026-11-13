# Critical Rules — Do Not Violate

**Last verified:** 2026-11-13. Confirm against current code before implementation.

## Data and migrations
1. Never reset, truncate, delete, or replace existing production data.
2. Preserve legacy tables and records unless the user explicitly approves a safe migration plan.
3. Inspect the current schema version before any migration. New migrations must be additive and use the next available version; do not edit historical migrations to retrofit production.
4. Test migrations on isolated copies/temporary databases. Never point test imports or destructive fixtures at production.
5. Keep SQLite and PostgreSQL behavior compatible. Do not assume SQLite-only SQL works through the Postgres adapter.
6. Do not add indexes or dependencies speculatively; justify them with a real need.

## Privacy and anonymity
1. Never connect anonymous answers to student identity in results, comments, exports, analytics, or faculty-facing pages.
2. Keep completion tracking separate from anonymous answer storage.
3. Do not select/render/log respondent names, roll numbers, student IDs, or submission timestamps alongside answer content.
4. Preserve the Phase 5B comment-order safety fix. Do not order comments by answer/response ID or submission order.
5. Student identity may be shown only in an authorized eligibility/completion workflow where role and scope explicitly permit it. Never imply a student wrote a particular answer.

## Category ownership and scope
1. Main Faculty Feedback is keyed by `class_sessions.id` and belongs to Main Faculty.
2. Second Faculty Feedback is keyed by `second_faculty_assignments.id` and belongs to the assigned Second Faculty.
3. Never merge Second Faculty assignments by employee code; assignment row ID is the identity.
4. Only confirmed, resolved, active Second Faculty assignments are eligible.
5. Second Faculty must differ from Main Faculty and match the session's Main Faculty relationship.
6. Second Faculty feedback requires `lab_status='available'`.
7. Lab Feedback is keyed by `class_sessions.id`, evaluates the same Main Faculty's practical/lab teaching, and requires `lab_status='available'`.
8. Never create or reuse a separate Lab Faculty assignment for Phase 5 Lab feedback.
9. Expected availability: No Lab → Main only; Lab Available → Main + Lab; Lab Available with confirmed Second Faculty → Main + Lab + one Second category per confirmed assignment.

## Authorization and request safety
1. Enforce role and branch authorization server-side on every page, API, preview, and state-changing action.
2. Re-resolve session, form, and assignment scope from the database. Never trust browser-supplied faculty, branch, student, category, or ownership values.
3. Use existing role/security helpers and CSRF for all state-changing routes.
4. Keep errors safe; do not leak SQL, credentials, stack traces, or private records.
5. Preserve audit behavior for privileged state changes where existing architecture requires it.

## Lifecycle and compatibility
1. Use `category_forms.create_form/publish/close/change_status` as appropriate; inspect signatures and routes before calling.
2. Do not bypass publication snapshots, validation, duplicate/conflict checks, deadlines, or submitted-form transition protections.
3. Do not change existing Admin/HOD workflows as a side effect of a new module without approval.
4. Do not change unrelated UI/shared CSS. Prefer scoped styles and established CSP-safe script patterns.
5. Keep SQL/API behavior compatible with both database backends.

## Honesty and workflow
1. Distinguish verified facts, historical report claims, assumptions, and unresolved issues.
2. Report exact tests run and results. Never claim unrun tests passed.
3. Do not dismiss existing failures as harmless without confirming root cause.
4. For checkpointed work, stop after the approved checkpoint and wait for explicit approval.
5. Update project status and handoff documentation after each completed phase.
