"""Database-backed HOD reminders. Run python -m feedback_system.reminders to drain."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import chain
import logging

from . import db, mailer, reports, services

log = logging.getLogger(__name__)


def pending_forms(user):
    sql, args = services.qualifying_reminder_sessions(user)
    return db.query("SELECT DISTINCT f.id, f.title, f.status, f.deadline_at, f.subject_code "
                    + sql + ' ORDER BY f.id DESC', args)


def latest_run(hod_id):
    row = db.query("SELECT id, status FROM reminder_runs WHERE hod_id=? ORDER BY id DESC LIMIT 1",
                   (hod_id,), one=True)
    if row is None:
        return None
    counts = {r['status']: r['total'] for r in db.query(
        "SELECT status, COUNT(*) AS total FROM reminder_deliveries WHERE run_id=? GROUP BY status",
        (row['id'],))}
    return {**dict(row), 'counts': counts}


def enqueue(hod_id, previous):
    # The same rendered button always refers to the same predecessor.
    return db.execute("""INSERT INTO reminder_runs(hod_id, previous_run)
        SELECT ?, ? WHERE ?=COALESCE((SELECT MAX(id) FROM reminder_runs WHERE hod_id=?),0)
        AND NOT EXISTS (SELECT 1 FROM reminder_runs WHERE hod_id=? AND status<>'done')
        ON CONFLICT(hod_id, previous_run) DO NOTHING""",
                      (hod_id, previous, previous, hod_id, hod_id)).rowcount == 1


def _active_hod(hod_id):
    return db.query("SELECT id, role, name FROM users WHERE id=? AND role='hod' AND is_active=1",
                    (hod_id,), one=True)


def _prepare(run):
    # Publish the entire recipient queue atomically. A crash rolls back this claim.
    conn = db.get_db()
    claimed = conn.execute("UPDATE reminder_runs SET status='ready' WHERE id=? AND status='queued'",
                           (run['id'],)).rowcount
    if claimed:
        user = _active_hod(run['hod_id'])
        if user:
            sql, args = services.qualifying_reminder_sessions(user)
            conn.execute("INSERT INTO reminder_forms(run_id, teacher_id, form_id) "
                         "SELECT DISTINCT ?, faculty.id, f.id " + sql + " AND faculty.id IS NOT NULL",
                         (run['id'], *args))
            conn.execute("INSERT INTO reminder_deliveries(run_id, teacher_id) "
                         "SELECT DISTINCT run_id, teacher_id FROM reminder_forms WHERE run_id=?",
                         (run['id'],))
    conn.commit()


def _pending_rows(key, user):
    form_ids = [r['form_id'] for r in db.query(
        "SELECT form_id FROM reminder_forms WHERE run_id=? AND teacher_id=? ORDER BY form_id", key)]
    for start in range(0, len(form_ids), 100):
        yield from services.reminder_rows(form_ids[start:start + 100], user, pending_teacher=key[1])


def _attachments(first, rows, hod_name):
    categories = defaultdict(dict)
    for r in chain((first,), rows):
        category = categories[r['category'] or 'main']
        if r['form_id'] not in category:
            category[r['form_id']] = {
                'title': r['title'], 'deadline': services.format_deadline(r['deadline_at']),
                'subjects': set(), 'sections': set(), 'rolls': set(),
            }
        form = category[r['form_id']]
        form['subjects'].add(r['subject_name'] or r['subject_code'] or '')
        form['sections'].add(r['timetable'] or '')
        if r['roll']:
            form['rolls'].add(r['roll'])
    attachments = []
    for category, forms in sorted(categories.items()):
        ordered = [forms[fid] for fid in sorted(forms)]
        for form in ordered:
            for key in ('subjects', 'sections', 'rolls'):
                form[key] = sorted(form[key])
        label = services.CATEGORY_LABELS.get(category, 'Feedback (' + category + ')')
        pdf = reports.build_pending_forms_pdf(label, first['teacher_name'], first['emp_code'],
                                              ordered, hod_name=hod_name)
        attachments.append((f'pending_{category}.pdf', pdf, 'application/pdf'))
    return attachments


def _delivery_status(key, before, after):
    db.execute("UPDATE reminder_deliveries SET status=? WHERE run_id=? AND teacher_id=? "
               "AND status=?", (after, *key, before))


def _deliver(item):
    key = (item['run_id'], item['teacher_id'])
    user = _active_hod(item['hod_id'])
    rows = _pending_rows(key, user) if user else iter(())
    first = next(rows, None)
    if first is None:
        _delivery_status(key, 'queued', 'cleared')
        return
    address = first['email']
    if not mailer.is_valid_email(address):
        _delivery_status(key, 'queued', 'no_email')
        return
    if not mailer.is_configured():
        _delivery_status(key, 'queued', 'failed')
        return
    try:
        attachments = _attachments(first, rows, user['name'] or 'HOD')
    except Exception:
        log.exception("Could not build reminder PDFs for run %s, faculty %s", *key)
        _delivery_status(key, 'queued', 'failed')
        return
    # Commit before SMTP. Only the worker that changes this row may send.
    claimed = db.execute("""UPDATE reminder_deliveries SET status='sending', sending_at=?
        WHERE run_id=? AND teacher_id=? AND status='queued'""", (services._utc_now_iso(), *key))
    if not claimed.rowcount:
        return
    try:
        accepted = mailer.send_email(address, 'Pending student feedback',
                                     'Please remind the students listed in the attached PDFs '
                                     'to complete their feedback before the indicated deadlines.',
                                     attachments=attachments)
    except Exception:
        log.exception("Unconfirmed SMTP outcome for run %s, faculty %s", *key)
        _delivery_status(key, 'sending', 'unconfirmed')
        return
    _delivery_status(key, 'sending', 'sent' if accepted else 'failed')


def process_next():
    """Process one durable unit. Use a fresh app context for current HOD permissions."""
    run = db.query("SELECT id, hod_id FROM reminder_runs WHERE status='queued' ORDER BY id LIMIT 1",
                   one=True)
    if run:
        _prepare(run)
        return True
    item = db.query("""SELECT d.run_id, d.teacher_id, r.hod_id FROM reminder_deliveries d
        JOIN reminder_runs r ON r.id=d.run_id
        WHERE d.status='queued' AND r.status='ready' ORDER BY d.run_id, d.teacher_id LIMIT 1""",
                    one=True)
    if item:
        _deliver(item)
        return True
    # Interrupted SMTP acceptance is ambiguous. Surface it without automatic replay.
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M:%S')
    db.execute("UPDATE reminder_deliveries SET status='unconfirmed' "
               "WHERE status='sending' AND sending_at<?", (cutoff,))
    db.execute("""UPDATE reminder_runs SET status='done' WHERE status='ready' AND NOT EXISTS
        (SELECT 1 FROM reminder_deliveries d WHERE d.run_id=reminder_runs.id
         AND d.status IN ('queued','sending'))""")
    return False


def main():
    from . import create_app
    app = create_app()
    while True:
        with app.app_context():
            if not process_next():
                return


if __name__ == '__main__':
    main()
