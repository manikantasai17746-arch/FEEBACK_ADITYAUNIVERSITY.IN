# Faculty photos on student feedback forms

Implemented on 2026-11-20 from `2c56bc6`.

- The teacher card on `/student/form/<id>` checks the assigned faculty member's photo through the shared employee photo service. That service gives uploaded photos priority over bundled JPGs. The card renders its initials badge when neither exists.
- Both the initial form and the page shown after a submission error use the same photo check. Photo requests use the existing authenticated employee-photo route. The card uses the faculty identity already resolved for the feedback form.
- Deploy with the standard application build.

## Verification

- `pytest tests/test_photos_phase1.py -k 'serve or fetch_photo or initials' --tb=short` passed 4 tests with Python UTF-8 mode and a workspace SQLite temporary directory. The integration check uploaded a PNG, opened an eligible student's feedback form, and verified that the photo URL returned the uploaded bytes. It also exercised the initials state with both photo sources absent.
