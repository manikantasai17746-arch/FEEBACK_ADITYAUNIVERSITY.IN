# AI Agent Instructions — Faculty Feedback System

This is the entry point for every AI coding agent working in this repository.

## Read before acting

1. Read [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md).
2. Read [docs/CRITICAL_RULES.md](docs/CRITICAL_RULES.md).
3. Read [docs/DEVELOPMENT_STATUS.md](docs/DEVELOPMENT_STATUS.md).
4. Read relevant phase handoffs in [docs/PHASE_HANDOFFS/README.md](docs/PHASE_HANDOFFS/README.md).
5. Inspect current source files relevant to the requested task. Documentation is a map, not a substitute for source verification.

## Required workflow

### Before changes
- Check `git status`, current branch, and recent commits. Never overwrite or discard existing user changes.
- Identify the exact requested scope; separate required changes from optional improvements.
- Inspect relevant implementations, callers, schema/migrations, templates, tests, and authorization paths.
- Prepare a concise plan and identify risks. For checkpoint-based work, stop and wait for explicit approval at each checkpoint.
- Verify current function signatures and schema version; do not rely on old examples.

### During changes
- Preserve Flask + SQLite/PostgreSQL + Jinja architecture unless the user approves an architectural change.
- Reuse existing services, eligibility, authorization, CSRF, form lifecycle, and query helpers. Do not create parallel implementations.
- Keep changes minimal and scoped. Do not refactor unrelated modules or alter shared CSS without demonstrated need.
- Database changes must be additive and non-destructive. Inspect the current maximum migration version first; use the next version only when necessary.
- Do not add dependencies without explaining why.
- Never add secrets, credentials, real student data, or private personal data to source control or documentation.

### Before completion
- Run narrow relevant tests, then the broadest feasible regression suite.
- Report exact commands and actual results, including skipped tests, failures, errors, and tests that could not run.
- Do not call an unverified failure harmless or claim an unrun test passed.
- Update `docs/DEVELOPMENT_STATUS.md` and the relevant phase handoff with verified changes, tests, limitations, and decisions.
- Include deployment/migration instructions where applicable.

## Git safety
- Never force-push, reset, rebase, delete branches, or discard user changes unless explicitly requested and understood.
- Do not amend/rewrite existing commits as a shortcut.
- Prefer a dedicated branch and reviewable commit/PR for substantial work.
- Inspect the staged diff before committing; include only approved files.
- Do not push application changes during a documentation-only task.

## Phase/checkpoint discipline
Implement only the currently approved checkpoint. After each checkpoint report files, routes/APIs, behavior, tests, security/privacy/data-safety checks, limitations, and next checkpoint. Then stop for explicit approval. Never infer approval for the next checkpoint.

## Source-of-truth priority
When information conflicts, use: (1) current executable source and schema/migrations; (2) current tests and verified runtime behavior; (3) latest phase handoff/verification report; (4) these summaries; (5) older README/report text. Report discrepancies and update documentation instead of silently assuming.
