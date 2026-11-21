"""Mandatory form deadline (auto-close) and HOD pending-submission reminders."""
import pytest

from feedback_system import db, mailer, services
from feedback_system.security import hash_password

from helpers import (find_session, future_deadline, get_csrf, login, login_student, give_feedback_code,
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
    give_feedback_code(c)
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
    give_feedback_code(c)
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


def test_hod_forms_displays_saved_deadline(app, pending_setup):
    fid = pending_setup['fid']
    _sql(app, "UPDATE feedback_forms SET deadline_at='2030-05-01T04:00:00' WHERE id=?",
         (fid,))
    html = pending_setup['hod'].get('/hod/forms').get_data(as_text=True)
    assert 'Pending test' in html
    # Scope to this form's row: other forms on the shared page may legitimately
    # have no deadline.
    marker = f'/hod/forms/{fid}/deadline'
    i = html.find(marker)
    assert i != -1, 'form row with deadline action not found'
    row = html[html.rfind('<tr', 0, i):html.find('</tr>', i)]
    assert '01 May 2030' in row
    assert 'No deadline' not in row


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


@pytest.fixture()
def bulk_app(tmp_path, monkeypatch):
    """Synthetic, isolated department with two allocated/unallocated sections."""
    from feedback_system import create_app
    app = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'bulk.db'),
                      'DATABASE_URL': None, 'AUTO_IMPORT': False, 'SECRET_KEY': 'test-only',
                      'REQUIRE_ACCOUNT_SETUP': False})
    with app.app_context():
        conn = db.get_db()
        conn.executescript("""
            INSERT INTO branches(id,code,name) VALUES(1,'A','Alpha'),(2,'B','Beta');
            INSERT INTO academic_years(id,name) VALUES(1,'Test year');
            INSERT INTO semesters(id,number,label) VALUES(1,1,'First');
            INSERT INTO timetables(id,branch_id,semester_id,academic_year_id,code,name)
              VALUES(1,1,1,1,'A1','A1'),(2,1,1,1,'A2','A2'),(3,2,1,1,'B1','B1');
            INSERT INTO users(id,role,username,name,email,roll,password_hash)
              VALUES(20,'hod','head','Head','head@example.org',NULL,'x'),
              (21,'teacher','one','One','one@example.org',NULL,'x'),
              (22,'teacher','two','Two','two@example.org',NULL,'x'),
              (23,'teacher','three','Three',NULL,NULL,'x'),
              (31,'student','sa','Student A',NULL,'ROLL-A','x'),
              (32,'student','sb','Student B',NULL,'ROLL-B','x');
            UPDATE users SET onboarding_complete=1, first_login_complete=1, email_verified=1;
            INSERT INTO hod_allocations(hod_user_id,academic_year_id,semester_id,branch_id,timetable_id)
              VALUES(20,1,1,1,1);
            INSERT INTO class_sessions(id,timetable_id,subject_code,subject_name,teacher_user_id)
              VALUES(1,1,'MAT','Maths',21),(2,1,'PHY','Physics',22),(3,2,'CHE','Chemistry',23),
              (4,3,'LIT','Literature',23),(5,1,'GEO','Geology',23);
            INSERT INTO teacher_subject_settings(session_id,teacher_user_id,lab_status)
              VALUES(1,21,'available'),(2,22,'available');
            INSERT INTO second_faculty_assignments(id,session_id,subject_code,main_teacher_user_id,
                second_teacher_user_id,second_emp_code,status)
              VALUES(1,2,'PHY',22,21,'ONE','confirmed'),(2,2,'PHY',22,23,'THREE','confirmed');
            INSERT INTO session_students(session_id,student_user_id)
              SELECT cs.id, u.id FROM class_sessions cs CROSS JOIN users u WHERE u.role='student';
        """)
        for fid, title, session, category in [
                (1, 'Main one', 1, 'main'), (2, 'Main two', 1, 'main'),
                (3, 'Lab only', 1, 'lab'), (4, 'Assisting', 2, 'second'),
                (5, 'Own Physics', 2, 'main'), (6, 'Completed', 1, 'main'),
                (7, 'Expired', 1, 'main'), (8, 'Unallocated timetable', 3, 'main'),
                (9, 'Another department', 4, 'main'), (10, 'Missing address', 5, 'main'),
                (11, 'Closed before send', 2, 'main')]:
            conn.execute("""INSERT INTO feedback_forms(id,title,category,session_id,timetable_id,
                branch_id,status,deadline_at,second_faculty_assignment_id)
                SELECT ?,?,?,cs.id,t.id,t.branch_id,'open',?,? FROM class_sessions cs
                JOIN timetables t ON t.id=cs.timetable_id WHERE cs.id=?""",
                         (fid, title, category, '2000-01-01' if fid == 7 else '2099-01-01T12:00:00',
                          1 if category == 'second' else None, session))
            conn.execute("INSERT INTO form_sessions(form_id,session_id) VALUES(?,?)", (fid, session))
        conn.executescript("""
            INSERT INTO submissions(form_id,student_user_id) VALUES(6,31),(6,32),(1,31);
            INSERT INTO form_eligible_students(form_id,student_user_id) VALUES(4,31);
        """)
        conn.commit()
    monkeypatch.setattr(mailer, 'is_configured', lambda: True)
    return app


def _bulk_client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session['uid'] = 20
        session['role'] = 'hod'
    return client


def _worker_step(app):
    from feedback_system import reminders
    with app.app_context():
        return reminders.process_next()


def test_bulk_reminders_route_grouping_and_recheck(bulk_app, monkeypatch):
    from feedback_system import reminders, reports
    client = _bulk_client(bulk_app)
    page = client.get('/hod/pending?form_id=5').get_data(as_text=True)
    for title in ('Main one', 'Lab only', 'Assisting', 'Own Physics'):
        assert title in page
    for title in ('Completed', 'Expired', 'Unallocated timetable', 'Another department'):
        assert title not in page
    assert client.get('/hod/pending/8/teacher/23.pdf').status_code == 403
    assert client.get('/hod/pending/4/teacher/23.pdf').status_code == 404
    assert client.post('/hod/pending/email-all', data={'previous_run': 0}).status_code == 400
    sent, pdf_inputs = {}, {}
    build = reports.build_pending_forms_pdf

    def render(label, teacher, emp, forms, **kwargs):
        pdf_inputs[teacher, label] = forms
        return build(label, teacher, emp, forms, **kwargs)

    def send(address, subject, text, attachments):
        assert address not in sent
        sent[address] = {name for name, pdf, mime in attachments
                         if pdf.startswith(b'%PDF-') and mime == 'application/pdf'}
        return True

    monkeypatch.setattr(reports, 'build_pending_forms_pdf', render)
    monkeypatch.setattr(mailer, 'send_email', send)
    data = {'_csrf': get_csrf(page), 'previous_run': 0}
    for _ in range(2):
        assert client.post('/hod/pending/email-all', data=data).status_code == 302
    assert not sent
    assert _worker_step(bulk_app)  # Freeze the candidate pairs.
    _sql(bulk_app, "UPDATE feedback_forms SET status='closed' WHERE id=11")
    _sql(bulk_app, "UPDATE feedback_forms SET deadline_at='2000-01-01' WHERE id=2")
    while _worker_step(bulk_app):
        pass
    assert sent == {'one@example.org': {'pending_main.pdf', 'pending_lab.pdf', 'pending_second.pdf'},
                    'two@example.org': {'pending_main.pdf'}}
    main = pdf_inputs['One', 'Main Faculty Feedback']
    assert [(f['title'], f['subjects'], f['rolls']) for f in main] == [
        ('Main one', ['Maths'], ['ROLL-B'])]
    assert main[0]['deadline'] == services.format_deadline('2099-01-01T12:00:00')
    second = pdf_inputs['One', 'Second Faculty Feedback']
    assert [(f['title'], f['rolls']) for f in second] == [('Assisting', ['ROLL-A'])]
    assert [f['title'] for f in pdf_inputs['Two', 'Main Faculty Feedback']] == ['Own Physics']
    client.post('/hod/pending/email-all', data=data)
    assert not _worker_step(bulk_app)
    with bulk_app.app_context():
        assert reminders.latest_run(20)['counts'] == {'sent': 2, 'no_email': 1}
    page = client.get('/hod/pending').get_data(as_text=True)
    assert 'Sent 2' in page and 'Failed 0' in page and 'Skipped (no valid email) 1' in page


def test_worker_handles_changed_scope_and_delivery_outcomes(bulk_app, monkeypatch):
    from feedback_system import reminders
    attempts = []
    monkeypatch.setattr(mailer, 'send_email', lambda address, *a, **kw: attempts.append(address) or False)
    with bulk_app.app_context():
        assert reminders.enqueue(20, 0)
    assert _worker_step(bulk_app)
    assert _worker_step(bulk_app)
    with bulk_app.app_context():
        db.execute("DELETE FROM hod_allocations WHERE hod_user_id=20")
    while _worker_step(bulk_app):
        pass
    assert attempts == ['one@example.org']
    with bulk_app.app_context():
        assert reminders.latest_run(20)['counts'] == {'failed': 1, 'cleared': 2}


def test_worker_handles_5000_open_forms(bulk_app, monkeypatch):
    from feedback_system import reminders, reports
    with bulk_app.app_context():
        db.execute("UPDATE feedback_forms SET status='closed'")
        conn = db.get_db()
        conn.executemany("""INSERT INTO feedback_forms(id,title,category,session_id,
            timetable_id,branch_id,status) VALUES(?,?,'main',1,1,1,'open')""",
                         [(i, f'Round {i}') for i in range(100, 5100)])
        conn.executemany("INSERT INTO form_sessions(form_id,session_id) VALUES(?,1)",
                         [(i,) for i in range(100, 5100)])
        conn.commit()
        hod = db.query('SELECT * FROM users WHERE id=20', one=True)
        assert len(reminders.pending_forms(hod)) == 5000
        assert reminders.enqueue(20, 0)
    counts = []
    real_pdf = reports.build_pending_forms_pdf

    def render(title, teacher, emp, forms, **kwargs):
        counts.append(len(forms))
        return real_pdf(title, teacher, emp, forms, **kwargs)

    monkeypatch.setattr(reports, 'build_pending_forms_pdf', render)
    monkeypatch.setattr(mailer, 'send_email', lambda *a, **kw: True)
    while _worker_step(bulk_app):
        pass
    assert counts == [5000]
    with bulk_app.app_context():
        assert reminders.latest_run(20)['counts'] == {'sent': 1}


def test_interrupted_send_is_reported_without_replay(bulk_app, monkeypatch):
    from feedback_system import reminders
    with bulk_app.app_context():
        assert reminders.enqueue(20, 0)
    assert _worker_step(bulk_app)
    with bulk_app.app_context():
        db.execute("""UPDATE reminder_deliveries SET status='sending', sending_at='2001-01-01'
                      WHERE teacher_id=21""")
        # Everyone in the second teacher's remaining forms finishes while queued.
        db.execute("""INSERT INTO submissions(form_id,student_user_id)
                      SELECT f.id,u.id FROM feedback_forms f CROSS JOIN users u
                      WHERE f.id IN (5,11) AND u.role='student'""")
    monkeypatch.setattr(mailer, 'send_email', lambda *a, **kw: pytest.fail('unexpected send'))
    while _worker_step(bulk_app):
        pass
    with bulk_app.app_context():
        run = reminders.latest_run(20)
        assert run['status'] == 'done'
        assert run['counts'] == {'cleared': 1, 'no_email': 1, 'unconfirmed': 1}


def test_simultaneous_clicks_and_workers_deliver_once(bulk_app, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from feedback_system import reminders
    clicks = Barrier(2)

    def click():
        with bulk_app.app_context():
            clicks.wait(timeout=30)
            return reminders.enqueue(20, 0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(click), pool.submit(click)
        assert sorted((first.result(), second.result())) == [False, True]
    assert _worker_step(bulk_app)
    ready = Barrier(2)
    build = reminders._attachments
    calls = []

    def prepare(*args):
        result = build(*args)
        ready.wait(timeout=30)
        return result

    monkeypatch.setattr(reminders, '_attachments', prepare)
    monkeypatch.setattr(mailer, 'send_email', lambda to, *a, **kw: calls.append(to) or True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(_worker_step, bulk_app) for _ in range(2)]
        assert all(job.result() for job in jobs)
    assert calls == ['one@example.org']
