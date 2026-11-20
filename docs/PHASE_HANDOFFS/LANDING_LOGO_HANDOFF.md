# Landing-page logo animation

Preview approved on 2026-11-20. Repository baseline `9f02436` on `main`.

- The public homepage at `/` uses the supplied SVG in `feedback_system/templates/partials/landing_numbers_logo.svg`. The banner layout and keyframes live in `feedback_system/static/css/landing.css`. The existing nonce-protected script block in `feedback_system/templates/landing.html` starts the entrance when the logo enters the viewport.
- The logo appears to the left of the live counts on desktop and above them through 640px. Reduced-motion settings and browsers without Intersection Observer receive the complete static logo.
- Banner visibility and the optional completion line depend on the public aggregate stats. Deploy with the standard application build.

## Verification

- `python -m pytest tests/test_app.py -k 'landing or admin_login'` passed all 7 matching tests with SQLite temporary files redirected to the writable workspace. The original temporary directory gave 7 setup errors because SQLite could not open its database file there.
- Chrome checked the Flask page at 1280px and 390px under enforced Content Security Policy. The checks covered logo placement, activation on scroll, reduced motion, the static fallback, and the optional completion line. Flask also rendered the page with zero students and omitted the banner.
- The baseline already references an untracked ZXing scanner asset that returned HTTP 404 in the local checkout. The logo uses only its local SVG paths, styles, and animation.
