# Pending submissions: search box (2026-11-21)

**Goal:** let the HOD find a teacher quickly in the Pending submissions table (dashboard panel and `/hod/pending`).

**Behaviour**
- Client-side live search over the rows already on the page. It matches employee name, emp ID and subject name only (`data-q` on each `tr.pd-row`, lower-cased). Roll numbers and email are deliberately NOT searched.
- Shows "x of y teacher(s)", an empty-state message and a clear (x) button; Esc clears; Enter never submits.
- Teacher name and subject text are highlighted.
- View-only: no route, query, email or PDF behaviour changed. "Email all teachers with pending" still covers every teacher, whatever the search shows.

**Files**
- `templates/hod_pending.html`: search box, `data-q`, empty-state `<p>`, stylesheet + script links.
- `templates/hod_dashboard.html`: links the new CSS and JS (the panel is loaded by `hod_overview.js` via fetch + `innerHTML`, which strips inline `<script>`/`<style>`, so the logic lives in separate files and listens on `document`).
- `static/js/hod_pending_search.js`, `static/css/hod_pending_search.css` (new).
- `tests/test_pending_search.py` (new).

**Schema / migrations:** none. **Dependencies:** none. **Security:** no authorization change; the no-match message is set with `textContent`, highlights are HTML-escaped.

**Limitations**
- Searches the rows of the currently selected form only (change the form dropdown to search another form).
- The existing markup of the teacher cell was left unchanged because `tests/test_pending_all_categories.py` matches it exactly.

## Pending roll number dialog

On 2026-11-28, from `main` at `751a365`, the pending table gained a same-page dialog with the selected teacher, subject, count and scrollable roll list. The standalone page and the dashboard panel use the same template and delegated click handler. Browsers without native modal support can expand the inline list. The dialog uses the existing HOD-scoped completion data. Deploy `hod_pending.html`, `hod_pending_search.js` and `hod_pending_search.css` together. No migration is needed.

- A temporary rendered-template fixture with 150 synthetic rolls in one row and one in another passed in headless Chrome with 400 × 650 and 1440 × 900 window settings. The checks covered row isolation, scrollable content, viewport fit, both close buttons, focus return and the underlying scroll position. Command: `uv run --offline --no-project --with-requirements requirements-dev.txt python -X utf8 .check_roll_dialog.py`.
- `node --check feedback_system/static/js/hod_pending_search.js` passed.
- `uv run --offline --no-project --with-requirements requirements-dev.txt python -X utf8 -m pytest -q tests/test_pending_search.py tests/test_pending_all_categories.py tests/test_deadline_and_pending.py --tb=line -rs -o addopts=` gave 17 passed, 9 skipped, 5 failed and 5 setup errors. The skips required a second-faculty assignment. Five form creations lacked a subject, teacher or period, and five fixture lookups returned no record.
- `uv run --offline --no-project --with-requirements requirements-dev.txt python -X utf8 -m pytest -q tests --maxfail=5 --tb=line -rs -o addopts=` stopped after five account-setup failures. Four lookups returned no user and one page lacked the expected CSRF token. One browser module skipped because Python Playwright was unavailable.
