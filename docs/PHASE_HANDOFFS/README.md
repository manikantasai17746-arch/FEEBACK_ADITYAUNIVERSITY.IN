# Phase Handoffs and Verification Reports

This directory indexes phase documentation. Existing handoff files remain in their historical locations; do not move, rename, or delete them without approval.

## Existing repository documents

| Phase | Document | Coverage |
|---|---|---|
| Earlier HOD/Dean foundation | `IMPLEMENTATION_REPORT.md` | Historical implementation inventory, role/scope scaffolding, HOD/Dean/Second Faculty integration. Migration and test counts are historical. |
| Phase 3 | `PHASE_3_HANDOFF.md` | Teacher Assignment Setup, rooms, lab status, v21 migration, routes/service behavior, tests, limitations. |
| Phase 4 | `feedback_system/PHASE_4_HANDOFF.md` | Second Faculty assignment model, No-Lab guard, soft removal, tests, limitations. |
| Phase 5B | `feedback_system/PHASE_5B_VERIFICATION_REPORT.md` | Category ownership, eligibility, anonymity verification, safety fixes, test gaps. |

## Missing / not located during inspection

- `PHASE_0/1/2_HANDOFF.md` was reported missing by Phase 3; it points to `tests/PHASE1_NOTES.md` and `tests/PHASE2_NOTES.md` as partial equivalents.
- A standalone Phase 5A handoff was not found at `feedback_system/PHASE_5A_HANDOFF.md`. Check the current tree before assuming no related notes exist.
- `PHASE_5_VERIFICATION_PENDING.md` was reported absent in the Phase 5B verification report.

## Phase handoff template

For each new approved phase/checkpoint, document:
1. Goal and scope.
2. Source baseline (branch + commit).
3. Requirements and decisions/approvals.
4. Files added/modified.
5. Routes/APIs and behavior.
6. Database/schema/migration changes (or explicitly none).
7. Security, authorization, privacy, and anonymity checks.
8. Tests run: exact commands, environment, passed/failed/skipped/errors.
9. Manual/browser checks and viewport sizes.
10. Known limitations, unresolved issues, and deployment instructions.
11. Next checkpoint and approval status.

Keep old reports as historical evidence. Add a dated follow-up rather than silently rewriting prior reports when later work changes the facts.
