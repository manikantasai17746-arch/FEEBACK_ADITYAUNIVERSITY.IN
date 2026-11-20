# Landing-page logo animation

Preview approved on 2026-11-20. Repository baseline `9f02436` on `main`.

- The public homepage at `/` uses the supplied SVG in `feedback_system/templates/partials/landing_numbers_logo.svg`. The banner layout and keyframes live in `feedback_system/static/css/landing.css`. The existing nonce-protected script block in `feedback_system/templates/landing.html` starts the entrance when the logo enters the viewport.
- The logo appears to the left of the live counts on desktop and above them through 640px. Reduced-motion settings and browsers without Intersection Observer receive the complete static logo.
- Banner visibility and the optional completion line depend on the public aggregate stats. Deploy with the standard application build.

## Verification

- `python -m pytest tests/test_app.py -k 'landing or admin_login'` passed all 7 matching tests with SQLite temporary files redirected to the writable workspace. The original temporary directory gave 7 setup errors because SQLite could not open its database file there.
- Chrome checked the Flask page at 1280px and 390px under enforced Content Security Policy. The checks covered logo placement, activation on scroll, reduced motion, the static fallback, and the optional completion line. Flask also rendered the page with zero students and omitted the banner.
- The baseline already references an untracked ZXing scanner asset that returned HTTP 404 in the local checkout. The logo uses only its local SVG paths, styles, and animation.

## Shared sign-in and rotating loader

Implemented on 2026-11-20 from `d0815ea`.

- The five existing role login URLs render their role's form in the landing-page dialog, including when validation fails. They continue to accept the existing POST fields and CSRF token. Safe local return destinations carry through the active form, and HOD and Dean password recovery links are available there.
- The common base and public landing template share a loader. Light mode uses a white background with blue and yellow artwork. Dark mode uses gold artwork. The supplied emblem and orbits start rotating when the overlay appears, with the wordmark visible immediately. Reduced-motion settings show a static logo.
- Both placements share `feedback_system/templates/partials/university_logo.svg`. The landing banner uses its own entrance animation. The vendor directory includes the pinned ZXing browser bundle and its license, restoring the same-origin scanner dependency under CSP.
- Deploy with the standard application build. The updates use the existing authentication and database configuration.

### Verification

- The login, recovery-link, and loader selection across the targeted pytest files passed 17 tests. SQLite temporary files were redirected to the writable workspace and Python used UTF-8 mode.
- Chrome exercised successful sign-in and the return destination for Student, Teacher, HOD, and Dean, navigation in both palettes, and mobile reduced motion with CSP enforced. The scanner library loaded successfully in the browser.
