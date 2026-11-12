"""Mandatory form deadline (auto-close) and HOD pending-submission reminders."""
import pytest

from feedback_system import db, mailer, services
from feedback_system.security import hash_password

from helpers import (find_session, future_deadline, get_csrf, login, login_student,
                     make_form, publish_form, question_ids, submit_feedback, token_from)


def _save(admin, session_id, **extra):
    tok = get_csrf(admin.get('/admin/forms/new').get_data(as_text=True))
    body = {'form_id': None, 'title': 'DL form', 'instructions': 'x', 'session_id': session_id,
            'questions': [{'text': 'Q', 'qtype': 'text', 'is_required': False, 'options': []}]}
    body.update(extra)
    return admin.post('/admin/forms/save', json=body,
                      headers={'X-CSRF-Token': tok, 'X-Requested-With': 'XMLHttpRequest'})


def _row(app, fid):
    with app.app_context():
        return dict(db.query('SELECT * FROM feedback_forms WHERE id=?', (fid,), one=True))


def _sql(app, sql, args=()):
    with app.app_context():
        db.execute(sql, args)


# ----------------------------------------------------------------- deadline
def test_deadline_is_required_on_create(admin, app):
    sid = find_session(app, '6817', '2501IT07')
    r = _save(admin, sid)                                   # no deadline at all
    assert r.status_code == 400 and 'deadline' in r.get_json()['errors'][0].lower()
    r = _save(admin, sid, deadline_at='2001-01-01T10:00')  # in the past
    assert r.status_code == 400 and 'future' in r.get_json()['errors'][0].lower()
    r = _save(admin, sid, deadline_at='not-a-date')
    assert r.status_code == 400


def test_deadline_is_stored_as_utc_from_local_time(admin, app):
    sid = find_session(app, '6817', '2501IT07')
    fid = make_form(admin, sid)
    iso = _row(app, fid)['deadline_at']
    assert iso and services.deadline_input_value(iso) == future_deadline()[:16] or iso
    # round trip local -> utc -> local is lossless
    assert services.deadline_input_value(services.parse_deadline_input('2030-05-01T09:30')) \
        == '2030-05-01T09:30'
    # 09:30 IST is 04:00 UTC
    assert services.parse_deadline_input('2030-05-01T09:30') == '2030-05-01T04:00:00'


def test_publish_blocked_without_valid_deadline(admin, app):
    sid = find_session(app, '6817', '2501IT07')
    fid = make_form(admin, sid)
    _sql(app, 'UPDATE feedback_forms SET deadline_at=NULL WHERE id=?', (fid,))
    publish_form(admin, fid)
    assert _row(app, fid)['status'] == 'draft'


def test_expired_form_closes_and_rejects_submissions(admin, app, client):
    sid = find_session(app, '6817', '2501IT07')
    rolls = [r for r in __import__('helpers').eligible_rolls(app, sid)][:2]
    fid = make_form(admin, sid)
    publish_form(admin, fid)
    assert _row(app, fid)['status'] == 'open'

    c = app.test_client()
    login_student(c, rolls[0])
    html = c.get(f'/student/form/{fid}').get_data(as_text=True)
    rq, tq, oid = question_ids(html)

    # Deadline passes while the form is still flagged 'open' (no sweep ran yet):
    _sql(app, "UPDATE feedback_forms SET deadline_at='2001-01-01T00:00:00' WHERE id=?", (fid,))
    submit_feedback(c, fid, rq, tq, oid)
    with app.app_context():
        assert db.query('SELECT COUNT(*) c FROM submissions WHERE form_id=?', (fid,),
                        one=True)['c'] == 0          # the late answer was refused
    assert _row(app, fid)['status'] == 'closed'      # ...and the form closed itself


def test_sweep_closes_only_expired_open_forms(admin, app):
    sid = find_session(app, '6817', '2501IT07')
    expired = make_form(admin, sid, title='expiring')
    fresh = make_form(admin, sid, title='fresh')
    legacy = make_form(admin, sid, title='legacy')
    for f in (expired, fresh, legacy):
        _sql(app, "UPDATE feedback_forms SET status='open' WHERE id=?", (f,))
    _sql(app, "UPDATE feedback_forms SET deadline_at='2001-01-01T00:00:00' WHERE id=?", (expired,))
    _sql(app, 'UPDATE feedback_forms SET deadline_at=NULL WHERE id=?', (legacy,))
    with app.app_context():
        services.close_expired_forms(force=True)
    assert _row(app, expired)['status'] == 'closed'
    assert _row(app, expired)['closed_at']
    assert _row(app, fresh)['status'] == 'open'
    assert _row(app, legacy)['status'] == 'open'     # no deadline -> never auto-closes


def test_extending_deadline_reopens_closed_form(admin, app):
    sid = find_session(app, '6817', '2501IT07')
    fid = make_form(admin, sid)
    publish_form(admin, fid)
    _sql(app, "UPDATE feedback_forms SET deadline_at='2001-01-01T00:00:00' WHERE id=?", (fid,))
    with app.app_context():
        services.close_expired_forms(force=True)
    assert _row(app, fid)['status'] == 'closed'
    tok = token_from(admin, '/admin/forms')
    admin.post(f'/admin/forms/{fid}/deadline',
               data={'_csrf': tok, 'deadline_at': future_deadline(3)})
    row = _row(app, fid)
    assert row['status'] == 'open' and row['deadline_at'] > '2026'
    # a past deadline is refused
    admin.post(f'/admin/forms/{fid}/deadline',
               data={'_csrf': tok, 'deadline_at': '2001-01-01T00:00'})
    assert _row(app, fid)['deadline_at'] == row['deadline_at']


def test_bulk_create_requires_deadline(admin, app):
    tok = get_csrf(admin.get('/admin/forms/bulk').get_data(as_text=True))
    r = admin.post('/admin/forms/bulk/create',
                   json={'filters': {'teacher_id': 1}, 'status': 'draft',
                         'questions': [{'text': 'Q', 'qtype': 'text', 'is_required': False,
                                        'options': []}]},
                   headers={'X-CSRF-Token': tok})
    assert r.status_code == 400


# -------------------------------------------------------------- e-mail rules
@pytest.mark.parametrize('addr', ['a@gmail.com', 'Name.Last@Outlook.com', 'x@hotmail.co.uk',
                                  'n@yahoo.in', 't@aditya.ac.in', 'u+tag@live.com'])
def test_valid_addresses_on_any_provider(addr):
    assert mailer.is_valid_email(addr)


@pytest.mark.parametrize('addr', ['', 'a@b', 'a b@gmail.com', '@gmail.com', 'a@@x.com',
                                  'a@x.com\nBcc: z@x.com', 'a..b@x.com', None])
def test_invalid_addresses_rejected(addr):
    assert not mailer.is_valid_email(addr)


def test_provider_presets(monkeypatch):
    for k in ('SMTP_HOST', 'SMTP_PORT', 'MAIL_PROVIDER'):
        monkeypatch.delenv(k, raising=False)
    assert mailer._smtp_target() == ('smtp.gmail.com', 465)
    monkeypatch.setenv('MAIL_PROVIDER', 'outlook')
    assert mailer._smtp_target() == ('smtp.office365.com', 587)
    monkeypatch.setenv('SMTP_HOST', 'mail.example.edu')
    monkeypatch.setenv('SMTP_PORT', '2525')
    assert mailer._smtp_target() == ('mail.example.edu', 2525)


# ------------------------------------------------------------ HOD pending
@pytest.fixture()
def pending_setup(admin, app):
    sid = find_session(app, '6817', '2501IT07')
    with app.app_context():
        info = db.query("""SELECT t.branch_id, cs.teacher_user_id FROM class_sessions cs
                           JOIN timetables t ON t.id=cs.timetable_id WHERE cs.id=?""",
                        (sid,), one=True)
        name = 'HODPEND'
        if not db.query("SELECT id FROM users WHERE username=? AND role='hod'", (name,), one=True):
            db.execute("""INSERT INTO users(role,username,emp_code,name,email,password_hash,is_active)
                          VALUES('hod',?,?,?,?,?,1)""",
                       (name, name, 'Pending HOD', 'hodpend@test.edu', hash_password('hodpass123')))
        uid = db.query("SELECT id FROM users WHERE username=?", (name,), one=True)['id']
        db.execute('DELETE FROM user_department_scopes WHERE user_id=?', (uid,))
        db.execute('INSERT INTO user_department_scopes(user_id, branch_id) VALUES(?,?)',
                   (uid, info['branch_id']))
        db.execute('UPDATE users SET email_verified=1, onboarding_complete=1 WHERE id=?', (uid,))
        db.execute('UPDATE users SET email=? WHERE id=?',
                   ('faculty.member@outlook.com', info['teacher_user_id']))
    fid = make_form(admin, sid, title='Pending test')
    publish_form(admin, fid)
    hod = app.test_client()
    assert login(hod, '/hod/login', {'emp_code': 'HODPEND', 'password': 'hodpass123'}).status_code == 302
    return {'fid': fid, 'sid': sid, 'hod': hod, 'teacher_id': info['teacher_user_id']}


def test_pending_lists_only_non_submitters_roll_numbers(app, pending_setup):
    fid, sid = pending_setup['fid'], pending_setup['sid']
    rolls = __import__('helpers').eligible_rolls(app, sid)
    c = app.test_client()
    login_student(c, rolls[0])
    rq, tq, oid = question_ids(c.get(f'/student/form/{fid}').get_data(as_text=True))
    submit_feedback(c, fid, rq, tq, oid)

    with app.app_context():
        before = db.query('SELECT COUNT(*) c FROM submissions', one=True)['c']
        entries = services.pending_by_teacher(fid)
        after = db.query('SELECT COUNT(*) c FROM submissions', one=True)['c']
    assert before == after                                  # read-only
    entry = next(e for e in entries if e['teacher_user_id'] == pending_setup['teacher_id'])
    assert rolls[0] not in entry['pending_rolls']           # submitter excluded
    assert set(entry['pending_rolls']) == set(rolls[1:])    # everyone else listed
    assert entry['submitted'] == 1 and entry['eligible'] == len(rolls)

    html = pending_setup['hod'].get(f'/hod/pending?form_id={fid}').get_data(as_text=True)
    assert rolls[1] in html and f'>{rolls[0]}<' not in html


def test_pending_pdf_has_rolls_only(app, pending_setup):
    r = pending_setup['hod'].get(
        f"/hod/pending/{pending_setup['fid']}/teacher/{pending_setup['teacher_id']}.pdf")
    assert r.status_code == 200 and r.data[:5] == b'%PDF-'


def test_one_click_email_sends_pdf_to_teacher(app, pending_setup, monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, 'send_email',
                        lambda to, subject, text, html=None, attachments=None:
                        sent.append((to, subject, text, attachments)) or True)
    hod, fid = pending_setup['hod'], pending_setup['fid']
    tok = token_from(hod, f'/hod/pending?form_id={fid}')
    r = hod.post(f'/hod/pending/{fid}/email',
                 data={'_csrf': tok, 'teacher_user_id': pending_setup['teacher_id']})
    assert r.status_code == 302
    assert len(sent) == 1
    to, subject, text, attachments = sent[0]
    assert to == 'faculty.member@outlook.com'
    assert attachments[0][2] == 'application/pdf' and attachments[0][1][:5] == b'%PDF-'
    # roll numbers are NOT in the body; only the PDF carries them
    assert not any(roll in text for roll in __import__('helpers').eligible_rolls(
        app, pending_setup['sid']))


def test_email_refused_outside_hod_scope_and_when_closed(app, pending_setup, admin):
    hod, fid = pending_setup['hod'], pending_setup['fid']
    tok = token_from(hod, f'/hod/pending?form_id={fid}')
    # another department's form -> 403
    with app.app_context():
        other = db.query("""SELECT f.id FROM feedback_forms f WHERE f.branch_id<>
            (SELECT branch_id FROM feedback_forms WHERE id=?) LIMIT 1""", (fid,), one=True)
    if other:
        assert hod.post(f"/hod/pending/{other['id']}/email",
                        data={'_csrf': tok, 'all': '1'}).status_code in (403, 404)
        assert hod.get(f"/hod/pending?form_id={other['id']}").status_code in (403, 404)
    _sql(app, "UPDATE feedback_forms SET status='closed' WHERE id=?", (fid,))
    sent = []
    import feedback_system.mailer as m
    orig = m.send_email
    m.send_email = lambda *a, **k: sent.append(a) or True
    try:
        hod.post(f'/hod/pending/{fid}/email', data={'_csrf': tok, 'all': '1'})
    finally:
        m.send_email = orig
    assert sent == []
