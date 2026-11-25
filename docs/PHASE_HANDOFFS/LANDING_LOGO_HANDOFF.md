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

- `feedback_system/templates/landing.html` (`footer#contact`) and the footer block in `feedback_system/static/css/landing.css` were rebuilt. The footer logo is the supplied blue and orange animation (`feedback_system/templates/partials/footer_logo_anim.svg`, from `Aditya_University_stacked_rotation.html`) on a transparent background on the navy footer (blue lightened for contrast). The sun and orbits rotate continuously; the wordmark fades in when the footer scrolls into view (a small nonce script adds `is-animated` / `is-in`). Without Intersection Observer, or with reduced motion, the complete static logo shows.
- Desktop: brand column (logo, title, tagline, social icon buttons), Quick links (How it works, Privacy, Sign in) and Contact (phone, email, address with icons). Phone and email are `tel:` and `mailto:` links. Phones: a single column without Quick links (the menu drawer already has them), with larger type.
- A 3px orange-to-blue bar runs along the top edge of the footer. Social links still point to the generic network home pages as before; replace them with the university's real profile URLs when available.

### Verification

- Rendered the real Flask page and checked it in Chrome at 1280px, 820px and 390px: no horizontal overflow, layout matches the approved preview.
- `pytest tests/test_app.py -k 'landing or admin_login'` could not run in this environment: `pyotp` is not installed and package installs are blocked, so the app fails at import. The page was rendered with throwaway stand-in modules kept outside the repository. Run the targeted tests in a full environment before deploying.

## Hero headline animation

Implemented on 2026-11-23. Template and CSS only.

- The hero `<h1>` in `landing.html` is split into word spans. On load the words rise in one by one, "name out of it." turns orange, then the lead paragraph and the sign-in buttons fade up. The styles are in `landing.css` next to `.hero h1`.
- A small nonce script right after the `<h1>` adds `hero-anim` to `.hero-copy` before first paint. Without it, or with reduced motion on, the text simply shows. The headline wording is unchanged.
- Verified in Chrome at 1280px and 390px: sequence plays, final colors are correct, no horizontal overflow, no console errors. `pytest` could not run here (`pyotp` is missing), so run `pytest tests/test_app.py -k 'landing or admin_login'` before deploying.

## Compact mobile landing layout

On 2026-11-25 the public landing header, hero and interactive sample were condensed at widths up to 560px. The sign-in and menu controls retain a minimum height of 44px. Deploy `static/css/landing.css` and `static/css/answer-card.css` together with the standard build.

- Headless Chrome loaded the rendered Flask landing page at 320, 390, 512, 560, 561 and 1280px. At 390px the headline measured 26px and the sample card began 357px from the top. The phone checks confirmed a 14px introduction, a 17px sample question, room for all five rating options, and 44px or taller menu and sign-in controls.
- `python -X utf8 -m pytest -q tests/test_app.py -k landing --tb=line -o addopts=` passed five tests with 49 deselected.
- `python -X utf8 -m pytest -q tests --maxfail=5 --tb=line -o addopts=` stopped after five account-setup failures with one skip. Four tests could not retrieve their fixture user, and one received the teacher login redirect instead of the photo setup redirect.

The follow-up reduces type and spacing in the submission steps, privacy rules, role picker and footer. Deploy `templates/landing.html` with `static/css/landing.css` for the shorter animation and revised phone layout. Chrome checked the rendered sections at 320, 390, 512, 560, 561 and 1280px. At phone widths all four fit horizontally, social buttons and role rows were at least 44px tall, the animation occupied 134px of height and the footer logo measured 128px wide. Both pytest commands above reproduced the same results after this change.

The sample card and statistics banner received a further phone sizing adjustment in the two stylesheets. Chrome selected Average on the rendered landing page at all six widths, using synthetic counts and a completion percentage to display the full banner. At 390px the selected card measured 216px high and the banner measured 204px. At all four phone widths, each rating button measured at least 44px in both dimensions and the stamp cleared the receipt text. The landing and broad pytest commands above again gave five passes with 49 deselected, and five account-setup failures with one skip, respectively.
