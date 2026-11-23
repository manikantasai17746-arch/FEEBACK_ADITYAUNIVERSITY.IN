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

## Loading between student feedback forms

Implemented on 2026-11-20 from `6800fbf`.

The feedback submission overlay stays active until the browser leaves the form. When another form is available, the redirect marks the arrival so that the destination shows the overlay before rendering its content and dismisses it on `pageshow`. Returning with Back clears the previous overlay. The submission response, category ordering, and form validation still use the normal student routes. Deploy with the standard application build.

- `pytest tests/test_student_portal_theme.py tests/test_app.py tests/test_feedback_gate_stepc.py --tb=short -rs` passed 84 tests with Python UTF-8 mode and a workspace SQLite temporary directory.
- Chrome exercised a 14-second feedback submission and a slow asset on the next form under enforced CSP. It verified rotation past the old timeout, an active loader at page departure and during arrival, and dismissal after load and on Back.

## Landing-page footer upgrade

Implemented on 2026-11-23 from `24425a2`. Template and CSS only; no routes, schema or dependencies changed.

- `feedback_system/templates/landing.html` (`footer#contact`) and the footer block in `feedback_system/static/css/landing.css` were rebuilt. The footer now shows the shared `university_logo` macro (same SVG as the loading screen, light palette) on a white rounded card. The sun and orbits rotate continuously, and reduced-motion settings show the static logo.
- Desktop: brand column (logo, title, tagline, social icon buttons), Quick links (How it works, Privacy, Sign in) and Contact (phone, email, address with icons). Phone and email are `tel:` and `mailto:` links. Phones: a single column without Quick links (the menu drawer already has them), with larger type.
- A 3px orange-to-blue bar runs along the top edge of the footer. Social links still point to the generic network home pages as before; replace them with the university's real profile URLs when available.

### Verification

- Rendered the real Flask page and checked it in Chrome at 1280px, 820px and 390px: no horizontal overflow, layout matches the approved preview.
- `pytest tests/test_app.py -k 'landing or admin_login'` could not run in this environment: `pyotp` is not installed and package installs are blocked, so the app fails at import. The page was rendered with throwaway stand-in modules kept outside the repository. Run the targeted tests in a full environment before deploying.
