"""HOD Overview: 'results ready to publish' pop-up + semester options from real forms."""
import os
import tempfile

import pytest

from feedback_system import create_app, hod
from feedback_system import db as db_module
from feedback_system.security import hash_password


@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-ready-')
    return create_app({
        'TESTING': True, 'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None, 'SECRET_KEY': 'test-secret-ready',
        'AUTO_IMPORT': False, 'ADMIN_EMAIL': 'admin@college.edu',
        'ADMIN_PASSWORD': 'admin123', 'REQUIRE_ACCOUNT_SETUP': False,
    })


def _session(bid, sem_id, n):
    """A real timetable + class session (the form/publication tables reference them)."""
    tt = db_module.query('SELECT id FROM timetables WHERE branch_id=? AND semester_id=? AND code=?',
                         (bid, sem_id, 'TT'), one=True)
    if not tt:
        db_module.execute('INSERT INTO timetables(branch_id, semester_id, code, name) VALUES(?,?,?,?)',
                          (bid, sem_id, 'TT', 'Timetable'))
        tt = db_module.query('SELECT id FROM timetables WHERE branch_id=? AND semester_id=? AND code=?',
                             (bid, sem_id, 'TT'), one=True)
    db_module.execute('INSERT INTO class_sessions(timetable_id, subject_code, subject_name) VALUES(?,?,?)',
                      (tt['id'], f'SUB{n}', f'Subject {n}'))
    return db_module.query('SELECT id FROM class_sessions WHERE subject_code=? AND timetable_id=?',
                           (f'SUB{n}', tt['id']), one=True)['id']


def _form(branch_id, status, semester_id, n, title):
    sid = _session(branch_id, semester_id, n) if n else None
    db_module.execute(
        """INSERT INTO feedback_forms(title, branch_id, semester_id, status, session_id)
           VALUES(?,?,?,?,?)""", (title, branch_id, semester_id, status, sid))
    return db_module.query('SELECT id, session_id FROM feedback_forms WHERE title=?', (title,), one=True)


def _submit(form):
    if not db_module.query("SELECT id FROM users WHERE username='rdy_stu'", one=True):
        db_module.execute(
            """INSERT INTO users(role, username, name, password_hash, is_active, onboarding_complete)
               VALUES('student','rdy_stu','S',?,1,1)""", (hash_password('pass12345'),))
    stu = db_module.query("SELECT id FROM users WHERE username='rdy_stu'", one=True)['id']
    db_module.execute('INSERT INTO submissions(form_id, session_id, student_user_id) VALUES(?,?,?)',
                      (form['id'], form['session_id'], stu))


@pytest.fixture()
def hod_client(app, monkeypatch):
    with app.app_context():
        branch = db_module.query('SELECT id FROM branches ORDER BY id LIMIT 1', one=True)
        bid = branch['id'] if branch else None
        if bid is None:
            db_module.execute("INSERT INTO branches(code, name) VALUES('CSE','Computer Science')")
            bid = db_module.query('SELECT id FROM branches ORDER BY id LIMIT 1', one=True)['id']
        monkeypatch.setattr(hod, '_hod_branches', lambda user: [{'id': bid, 'code': 'CSE', 'name': 'CSE'}])
        monkeypatch.setattr(hod, 'scoped_timetable_ids', lambda user: None)
        for n in range(1, 9):
            if not db_module.query('SELECT id FROM semesters WHERE number=?', (n,), one=True):
                db_module.execute('INSERT INTO semesters(number, label) VALUES(?,?)', (n, f'Semester {n}'))
        if not db_module.query("SELECT id FROM users WHERE username='rdy_hod'", one=True):
            db_module.execute(
                """INSERT INTO users(role, username, emp_code, name, email, password_hash,
                                     is_active, onboarding_complete)
                   VALUES('hod','rdy_hod','rdy_hod','H','h@t.edu',?,1,1)""", (hash_password('pass12345'),))
        uid = db_module.query("SELECT id FROM users WHERE username='rdy_hod'", one=True)['id']
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['uid'] = uid
    return client, bid


def test_popup_only_for_closed_unpublished_forms_with_responses(app, hod_client):
    client, bid = hod_client
    with app.app_context():
        s3 = db_module.query('SELECT id FROM semesters WHERE number=3', one=True)['id']
        ready = _form(bid, 'closed', s3, 1, 'Ready form')
        _submit(ready)
        published = _form(bid, 'closed', s3, 2, 'Published form')
        _submit(published)
        db_module.execute(
            'INSERT INTO result_publications(form_id, session_id, is_published) VALUES(?,?,1)',
            (published['id'], published['session_id']))
        _form(bid, 'closed', s3, 3, 'No responses')
        opened = _form(bid, 'open', s3, 4, 'Still open')
        _submit(opened)
    html = client.get('/hod/').get_data(as_text=True)
    assert 'id="rdyOv"' in html and 'data-n="1"' in html
    assert 'View score bands' in html and 'Publish results' in html
    assert '/hod/performance/bands' in html and '/hod/results' in html


def test_semester_options_list_only_semesters_with_forms(app, hod_client):
    client, bid = hod_client
    with app.app_context():
        s4 = db_module.query('SELECT id FROM semesters WHERE number=4', one=True)['id']
        _form(bid, 'draft', s4, 5, 'Sem4 draft')
        got = [s['number'] for s in hod._semesters_with_data([bid])]
        other = [s['number'] for s in hod._semesters_with_data([bid + 999])]
    assert 4 in got
    assert 1 not in got and 8 not in got       # no fixed Sem 1-8 list
    assert other == []                          # other departments' forms never count
