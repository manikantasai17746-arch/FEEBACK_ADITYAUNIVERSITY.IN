# Category PDF Reports Handoff

**Date:** 2026-11-13  
**Schema:** v24 (no migration)

## Routes
- `GET /hod/results/main.pdf`
- `GET /hod/results/second.pdf`
- `GET /hod/results/lab.pdf`

Query params (same as results UI): `year_id`, `semester_id`, `branch_id`, `section`, `subject`, `faculty`.

## Implementation
- `reports.build_category_results_pdf(...)` — one generator, called per category
- Shared row builder `_hod_results_rows` used by UI and PDF
- HOD scope enforced; other roles blocked via `@role_required('hod')`
- Photo embedded when available; no student identities

## Tests
`python -m pytest tests/test_hod_category_pdf.py` → 5 passed
