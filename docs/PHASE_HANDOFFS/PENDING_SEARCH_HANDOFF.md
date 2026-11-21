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
