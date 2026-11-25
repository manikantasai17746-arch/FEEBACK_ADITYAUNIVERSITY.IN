# HOD Overview score-band filters

On 2026-11-25, starting from `main` at `2149a76`, the category and semester links in `feedback_system/templates/hod_dashboard.html` gained a shared fragment target. Previously each click loaded a new document and the browser had no target for `#bands`.

`feedback_system/static/js/hod_overview.js` now fetches the linked dashboard and updates only the score-band panel. It transfers keyboard focus to the refreshed filter, records the selection in browser history, and cancels superseded requests. The panel reuses its existing score-band and email-selection controls. `feedback_system/static/css/hod-overview.css` reserves a viewport of space for the panel so an empty result can still support the current scroll position.

The request uses the existing HOD-authorized `GET /hod/` and its department-scoped scores. The browser stores filter choices in the URL. Deploy the template, script and stylesheet together through the normal application release. The release requires no migration.

## Verification

- A temporary local Flask fixture used SQLite and synthetic score rows. `uv run --offline --no-project --with-requirements requirements-dev.txt python -X utf8 .verify_hod.py` drove Chrome at 1440 × 850 and 390 × 844. Both retained their document and scroll offset across category and semester changes, including an empty result. The filter row varied by up to two CSS pixels on desktop and stayed at the same coordinate on mobile. Focus, Back/Forward, select-all after refresh and rapid successive semester selections passed.
- `node --check feedback_system/static/js/hod_overview.js` passed.
- `uv run --offline --no-project --with-requirements requirements-dev.txt python -X utf8 -m pytest -q tests/test_recent_forms_search_close.py tests/test_comparison.py --tb=line -rs -o addopts=` gave 10 passed and 4 skipped. The four skips required more class sessions in their fixture.
- `uv run --offline --no-project --with-requirements requirements-dev.txt python -X utf8 -m pytest -q tests --maxfail=5 --tb=line -rs -o addopts=` stopped at five failures in `tests/test_account_setup.py`. Four fixture lookups returned no user and one teacher request redirected to login instead of photo setup. One browser module skipped because Python Playwright was unavailable. The remaining tests did not run.
