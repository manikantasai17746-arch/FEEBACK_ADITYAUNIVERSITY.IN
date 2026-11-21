# HOD bulk reminders

Implemented against `3684707`. Both the dashboard panel and `GET /hod/pending` use the same filtered list. A form qualifies when an active, eligible student in the HOD's branch and timetable scope still owes a submission and the form is open. Older open forms without a deadline follow the application's existing no-expiry rule.

## Delivery

`POST /hod/pending/email-all` requires the HOD role and a CSRF token. The worker resolves current HOD scope when preparing the run and again when processing each teacher. It reads live membership, publication snapshots, and submission receipts. Each teacher gets one email with a PDF per pending feedback category. The Second Faculty recipient comes from the form's confirmed assignment. Individual form PDF and email actions remain available.

Additive migration 29 stores run IDs, candidate form/teacher pairs, and per-teacher delivery receipts. Roll numbers and PDFs are generated in worker memory. Candidate discovery uses a set-based insert. Roster reads use batches of 100 forms per teacher.

## Deployment

1. Deploy the web application and worker from the same revision. Apply the application's migrations through version 29 before serving requests. The standard app startup migration path handles this where enabled.
2. Configure the worker with the web application's shared persistent database and mail configuration. Use `DATABASE_URL` for PostgreSQL or the same persistent `FEEDBACK_DB` file for SQLite. Supply `MAIL_USER`, `MAIL_APP_PASSWORD`, the applicable SMTP host/provider settings, and the same application time offset.
3. From the repository root, schedule `python -m feedback_system.reminders` every minute on a host with the application dependencies and SMTP access. Each invocation drains available work and exits. For a Vercel web deployment, run this command on a separate scheduler or worker host connected to the same PostgreSQL database.
4. An HOD can open Pending submissions and use **Refresh status** to view the latest run. Sent counts mean the configured mail server accepted the email. Failed deliveries, invalid or missing addresses, and recipients who are no longer eligible have separate counts.

Duplicate clicks from the same page resolve to one run. Workers claim each delivery before the SMTP call. After a worker interruption during SMTP, a later drain flags a receipt older than 30 minutes as **Delivery unconfirmed**. Check the sending account's mail logs before starting another run for these recipients. SMTP cannot atomically commit a database receipt and accept an email.

## Verification

The following checks used synthetic SQLite data and mocked email delivery. The full packet was generated with ReportLab in the 5,000-form test. A separate content check decoded a two-form PDF and verified both form headings, subjects, deadlines, and roll numbers.

- `python -m pytest -o addopts='-p no:cacheprovider' -q tests/test_deadline_and_pending.py -k 'bulk_reminders or worker_handles or interrupted_send or simultaneous_clicks'` passed all 5 bulk-specific tests. Coverage includes a single teacher with all three attachments, exact Second Faculty assignment, snapshot membership, branch and timetable restrictions, send-time changes, missing email, SMTP failure, interrupted delivery, and concurrent clicks and workers.
- `python -m pytest -o addopts='-p no:cacheprovider' -q --disable-warnings tests/test_deadline_and_pending.py tests/test_hod_category_pdf.py` recorded 36 passed and 1 failed on the final implementation. The existing `test_hod_forms_displays_saved_deadline` also failed on the original checkout. It expects a form-specific deadline action on the separate HOD Forms page.
- The worker entry point `python -m feedback_system.reminders` applied migration 29 and exited successfully on an empty test database.
- Live SMTP delivery was mocked. Live PostgreSQL verification was not completed. An isolated PostgreSQL 17 cluster initialization stalled during the Windows shared-memory probe and was stopped.
