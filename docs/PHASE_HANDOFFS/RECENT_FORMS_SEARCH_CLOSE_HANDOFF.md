# Recent Forms: search + close only before deadline (2026-11-21)

**Goal:** (1) find forms by employee ID or subject in the HOD Recent Forms table; (2) "Close feedback" only while the form is open and before its deadline.

**Decisions:** option A (disabled button + note after the deadline) and live search, approved by the user.

**Behaviour**
- Search: client-side, in `_hod_recent_forms.html` + `static/js/hod_overview.js`. Matches `data-q` (subject code, subject name, faculty name with emp ID). Works with the status chips; chip counts follow the search; matches are highlighted.
- Close rule: `category_forms._refuse_manual_close_after_deadline` is called from `close()` and `change_status()`; returns 409 `deadline_passed`. The HOD `/hod/forms/<id>/status` route already turns `ScopeError` into a JSON error or a flash message.
- Existing auto-close (`services.close_expired_forms`, run from `before_request`, at most every 30 s per process) is unchanged, so an expired form is normally already Closed when the page loads; the disabled state covers the gap between sweeps.

**Schema / migrations:** none. **Dependencies:** none.

**Security:** authorization is unchanged (HOD branch scope check in the route, `_require_actor` in `close`). The new rule only removes a transition.

**Tests:** `tests/test_recent_forms_search_close.py`. See `docs/DEVELOPMENT_STATUS.md` for how they were run in the build sandbox.

**Limitations / notes**
- The Admin status route also goes through `category_forms`, so Admin can no longer manually close a form after its deadline either.
- Search covers the rows already on the page (dashboard: latest 50 forms).
- "closes ..." times are still shown as raw stored UTC text (not changed).
