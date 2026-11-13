"""Module C — HOD Lab Faculty Feedback + nav checks."""
import os
import re
import tempfile

import pytest

from feedback_system import category_forms, create_app, db, services
from feedback_system.feedback_categories import LAB, MAIN, eligible_count, resolve_scope
from feedback_system.security import hash_password
from helpers import future_deadline, login

PW = 'hodpass123'
TEACHER_PW = 'teacher@123'


@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-hod-lab-')
    application = create_app({
        'TESTING': True,
        'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None,
        'SECRET_KEY': 'test-hod-lab',
        'AUTO_IMPORT': True,
        'ADMIN_EMAIL': 'admin@college.edu',
        'ADMIN_PASSWORD': 'admin123',
        'DEFAULT_TEACHER_PASSWORD': TEACHER_PW,
        'DEFAULT_STUDENT_PASSWORD': 'student@123',
        'LOGIN_MAX_ATTEMPTS': 50,
        'REQUIRE_ACCOUNT_SETUP': False,
    })
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


def _seed_hod(app):
    import json as _json
    with app.app_context():
        branch = db.query("SELECT id, code FROM branches WHERE code='CSE'", one=True)
        if not branch:
            db.execute("INSERT INTO branches(code, name) VALUES('CSE','CSE')")
            branch = db.query("SELECT id, code FROM branches WHERE code='CSE'", one=True)
        existing = db.query("SELECT id FROM users WHERE username='HODLAB1'", one=True)
        if not existing:
            db.execute(
                """INSERT INTO users(role, username, emp_code, name, email, password_hash,
                                     is_active, onboarding_complete)
                   VALUES('hod','HODLAB1','HODLAB1','Lab HOD','hodlab@test.edu',?,1,1)""",
                (hash_password(PW),))
            hod = db.query("SELECT id FROM users WHERE username='HODLAB1'", one=True)
            db.execute(
                "INSERT INTO user_department_scopes(user_id, branch_id, is_primary) VALUES(?,?,1)",
                (hod['id'], branch['id']))
        else:
            db.execute('UPDATE users SET onboarding_complete=1, password_hash=? WHERE id=?',
                       (hash_password(PW), existing['id']))
            hod = existing
        qs = db.query('SELECT id FROM question_sets WHERE owner_user_id=? AND is_deleted=0 LIMIT 1',
                      (hod['id'],), one=True)
        if not qs:
            sid = db.insert_id(
                'INSERT INTO question_sets(owner_user_id, name, is_common) VALUES(?,?,0)',
                (hod['id'], 'Lab test set'))
            opts = _json.dumps([{'label': 'Excellent', 'value': 5}, {'label': 'Poor', 'value': 1}])
            db.execute(
                """INSERT INTO question_set_items
                   (set_id, position, text, qtype, is_required, options_json)
                   VALUES(?,?,?,?,1,?)""",
                (sid, 0, 'How was the lab session?', 'rating', opts))
            db.get_db().commit()
        return dict(hod_id=hod['id'], branch_id=branch['id'])


def _login_hod(client):
    r = login(client, '/hod/login', {'emp_code': 'HODLAB1', 'password': PW})
    assert r.status_code == 302
    return client


def _session_with_lab(app, branch_id, lab_status='available'):
    """Pick a CSE session and set lab_status."""
    with app.app_context():
        row = db.query(
            """SELECT cs.id, cs.teacher_user_id, t.branch_id FROM class_sessions cs
               JOIN timetables t ON t.id=cs.timetable_id
               WHERE cs.is_deleted=0 AND cs.teacher_user_id IS NOT NULL AND t.branch_id=?
               LIMIT 1""",
            (branch_id,), one=True)
        if not row:
            return None
        sid, tid = row['id'], row['teacher_user_id']
        exists = db.query('SELECT 1 FROM teacher_subject_settings WHERE session_id=?', (sid,), one=True)
        if exists:
            db.execute(
                "UPDATE teacher_subject_settings SET lab_status=?, lab_available=? WHERE session_id=?",
                (lab_status, 1 if lab_status == 'available' else 0, sid))
        else:
            db.execute(
                """INSERT INTO teacher_subject_settings
                   (session_id, teacher_user_id, lab_status, lab_available, room)
                   VALUES(?,?,?,?,?)""",
                (sid, tid, lab_status, 1 if lab_status == 'available' else 0, 'L-101'))
        db.get_db().commit()
        return dict(id=sid, branch_id=row['branch_id'], teacher_user_id=tid)


def test_non_hod_blocked_lab(app, client):
    _seed_hod(app)
    assert client.get('/hod/feedback/lab').status_code in (302, 401, 403)
    login(client, '/teacher/login', {'emp_code': '6817', 'password': TEACHER_PW})
    assert client.get('/hod/feedback/lab').status_code in (302, 403)


def test_no_lab_and_unconfigured_never_listed(app, client):
    seed = _seed_hod(app)
    with app.app_context():
        rows = list(db.query(
            """SELECT cs.id, cs.teacher_user_id FROM class_sessions cs
               JOIN timetables t ON t.id=cs.timetable_id
               WHERE cs.is_deleted=0 AND cs.teacher_user_id IS NOT NULL AND t.branch_id=?
               ORDER BY cs.id LIMIT 3""",
            (seed['branch_id'],)))
        if len(rows) < 3:
            pytest.skip('need at least 3 sessions in branch')
        none_id, unconf_id, avail_id = rows[0]['id'], rows[1]['id'], rows[2]['id']

        def _set(sid, tid, status):
            exists = db.query(
                'SELECT 1 FROM teacher_subject_settings WHERE session_id=?', (sid,), one=True)
            if status is None:
                db.execute('DELETE FROM teacher_subject_settings WHERE session_id=?', (sid,))
                return
            lab_av = 1 if status == 'available' else 0
            if exists:
                db.execute(
                    "UPDATE teacher_subject_settings SET lab_status=?, lab_available=? WHERE session_id=?",
                    (status, lab_av, sid))
            else:
                db.execute(
                    """INSERT INTO teacher_subject_settings
                       (session_id, teacher_user_id, lab_status, lab_available)
                       VALUES(?,?,?,?)""",
                    (sid, tid, status, lab_av))

        _set(none_id, rows[0]['teacher_user_id'], 'none')
        _set(unconf_id, rows[1]['teacher_user_id'], None)
        _set(avail_id, rows[2]['teacher_user_id'], 'available')
        db.get_db().commit()

    c = _login_hod(client)
    data = c.get('/hod/feedback/lab/api/sessions').get_json()
    assert data['ok']
    ids = []
    for sub in data['subjects']:
        for s in sub['sessions']:
            ids.append(s['session_id'])
    assert none_id not in ids
    assert unconf_id not in ids
    assert avail_id in ids


def test_lab_create_category_and_duplicate(app, client):
    seed = _seed_hod(app)
    sess = _session_with_lab(app, seed['branch_id'], 'available')
    if not sess:
        pytest.skip('no session')
    with app.app_context():
        db.execute(
            "UPDATE feedback_forms SET is_deleted=1 WHERE session_id=? AND category='lab'",
            (sess['id'],))
        qsid = db.query(
            'SELECT id FROM question_sets WHERE owner_user_id=? AND is_deleted=0 LIMIT 1',
            (seed['hod_id'],), one=True)['id']
    c = _login_hod(client)
    html = c.get('/hod/feedback/lab/create').get_data(as_text=True)
    m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    csrf = m.group(1)
    r = c.post('/hod/feedback/lab/api/create',
               json={'session_ids': [sess['id']], 'question_set_id': qsid, 'publish': False},
               headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    data = r.get_json()
    assert data['ok']
    fid = data['created'][0]['form_id']
    with app.app_context():
        form = services.get_form(fid)
        assert form['category'] == 'lab'
        assert form['session_id'] == sess['id']
    r2 = c.post('/hod/feedback/lab/api/create',
                json={'session_ids': [sess['id']], 'question_set_id': qsid, 'publish': False},
                headers={'X-CSRF-Token': csrf})
    assert r2.status_code in (409, 207)
    assert not r2.get_json().get('ok') or r2.get_json().get('errors')


def test_lab_submission_does_not_complete_main(app, client):
    seed = _seed_hod(app)
    sess = _session_with_lab(app, seed['branch_id'], 'available')
    if not sess:
        pytest.skip('no session')
    with app.app_context():
        db.execute(
            "UPDATE feedback_forms SET is_deleted=1 WHERE session_id=? AND category IN ('lab','main')",
            (sess['id'],))
        hod = db.query('SELECT * FROM users WHERE id=?', (seed['hod_id'],), one=True)
        qs = [{
            'text': 'Lab Q', 'qtype': 'rating', 'is_required': True,
            'options': [{'label': 'Good', 'value': 5}, {'label': 'Bad', 'value': 1}],
        }]
        dl = services.parse_deadline_input(future_deadline(7))
        lab_id = category_forms.create_form(dict(hod), LAB, sess['id'], 'Lab only', qs, deadline_at=dl)
        category_forms.publish(dict(hod), lab_id)
        main_id = category_forms.create_form(dict(hod), MAIN, sess['id'], 'Main only', qs, deadline_at=dl)
        category_forms.publish(dict(hod), main_id)
        students = services.eligible_students(sess['id'])
        assert students
        student_id = students[0]['id']
        q = db.query('SELECT id FROM form_questions WHERE form_id=?', (lab_id,), one=True)
        opt = db.query('SELECT id FROM question_options WHERE question_id=?', (q['id'],), one=True)
        ok, msg = services.submit_feedback(student_id, lab_id, {f'q_{q["id"]}': str(opt['id'])})
        assert ok, msg
        # Main form still has no submission from this student
        main_sub = db.query(
            "SELECT 1 FROM submissions WHERE form_id=? AND student_user_id=?",
            (main_id, student_id), one=True)
        assert main_sub is None


def test_lab_preview_eligibility(app, client):
    seed = _seed_hod(app)
    sess = _session_with_lab(app, seed['branch_id'], 'available')
    if not sess:
        pytest.skip('no session')
    c = _login_hod(client)
    r = c.get(f"/hod/feedback/lab/api/preview?session_id={sess['id']}")
    assert r.status_code == 200
    prev = r.get_json()['previews'][0]
    with app.app_context():
        expected = eligible_count(resolve_scope(LAB, sess['id']))
    assert prev['eligible_count'] == expected


def test_hod_nav_links_resolve(app, client):
    _seed_hod(app)
    c = _login_hod(client)
    paths = [
        '/hod/',
        '/hod/feedback/main',
        '/hod/feedback/second',
        '/hod/feedback/lab',
        '/hod/pending',
        '/hod/forms',
        '/hod/results',
        '/hod/performance',
    ]
    for path in paths:
        r = c.get(path)
        assert r.status_code == 200, f'{path} -> {r.status_code}'


def test_nav_blocked_for_teacher(app, client):
    _seed_hod(app)
    login(client, '/teacher/login', {'emp_code': '6817', 'password': TEACHER_PW})
    for path in ('/hod/', '/hod/feedback/lab', '/hod/forms'):
        assert client.get(path).status_code in (302, 403)


def test_overview_counts_structure(app, client):
    seed = _seed_hod(app)
    _session_with_lab(app, seed['branch_id'], 'available')
    c = _login_hod(client)
    html = c.get('/hod/').get_data(as_text=True)
    assert 'Overview' in html or 'Main Faculty' in html
    assert 'Lab Faculty' in html
