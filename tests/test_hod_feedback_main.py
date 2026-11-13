"""Module A — HOD Main Faculty Feedback."""
import os
import re
import tempfile

import pytest

from feedback_system import category_forms, create_app, db, services
from feedback_system.feedback_categories import MAIN, eligible_count, resolve_scope
from feedback_system.security import hash_password
from helpers import future_deadline, login

PW = 'hodpass123'
TEACHER_PW = 'teacher@123'


@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-hod-main-')
    application = create_app({
        'TESTING': True,
        'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None,
        'SECRET_KEY': 'test-hod-feedback-main',
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


def _seed_hod(app, branch_code='CSE'):
    import json as _json
    with app.app_context():
        branch = db.query('SELECT id, code FROM branches WHERE code=?', (branch_code,), one=True)
        if not branch:
            db.execute("INSERT INTO branches(code, name) VALUES(?,?)",
                       (branch_code, branch_code + ' Dept'))
            branch = db.query('SELECT id, code FROM branches WHERE code=?', (branch_code,), one=True)
        existing = db.query("SELECT id FROM users WHERE username='HODMAIN1' AND role='hod'", one=True)
        if not existing:
            db.execute(
                """INSERT INTO users(role, username, emp_code, name, email, password_hash,
                                     is_active, onboarding_complete)
                   VALUES('hod','HODMAIN1','HODMAIN1','Main HOD','hodmain@test.edu',?,1,1)""",
                (hash_password(PW),))
            hod = db.query("SELECT id FROM users WHERE username='HODMAIN1'", one=True)
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
                (hod['id'], 'Main test set'))
            opts = _json.dumps([{'label': 'Excellent', 'value': 5}, {'label': 'Poor', 'value': 1}])
            db.execute(
                """INSERT INTO question_set_items
                   (set_id, position, text, qtype, is_required, options_json)
                   VALUES(?,?,?,?,1,?)""",
                (sid, 0, 'How clear was the teaching?', 'rating', opts))
            db.get_db().commit()
        return dict(hod_id=hod['id'], branch_id=branch['id'], branch_code=branch['code'])


def _login_hod(client):
    r = login(client, '/hod/login', {'emp_code': 'HODMAIN1', 'password': PW})
    assert r.status_code == 302
    return client


def _a_cse_session(app):
    with app.app_context():
        row = db.query(
            """SELECT cs.id, cs.subject_code, t.branch_id, b.code AS branch_code
               FROM class_sessions cs
               JOIN timetables t ON t.id=cs.timetable_id
               JOIN branches b ON b.id=t.branch_id
               WHERE cs.is_deleted=0 AND cs.teacher_user_id IS NOT NULL AND b.code='CSE'
               LIMIT 1""", one=True)
        if not row:
            row = db.query(
                """SELECT cs.id, cs.subject_code, t.branch_id, b.code AS branch_code
                   FROM class_sessions cs
                   JOIN timetables t ON t.id=cs.timetable_id
                   JOIN branches b ON b.id=t.branch_id
                   WHERE cs.is_deleted=0 AND cs.teacher_user_id IS NOT NULL LIMIT 1""", one=True)
        return dict(row) if row else None


def _hod_qset_id(app, hod_id):
    with app.app_context():
        row = db.query(
            'SELECT id FROM question_sets WHERE owner_user_id=? AND is_deleted=0 LIMIT 1',
            (hod_id,), one=True)
        return row['id'] if row else None


def test_non_hod_blocked(app, client):
    _seed_hod(app)
    assert client.get('/hod/feedback/main').status_code in (302, 401, 403)
    login(client, '/teacher/login', {'emp_code': '6817', 'password': TEACHER_PW})
    assert client.get('/hod/feedback/main').status_code in (302, 403)


def test_hod_list_and_api_in_scope(app, client):
    _seed_hod(app)
    c = _login_hod(client)
    assert c.get('/hod/feedback/main').status_code == 200
    data = c.get('/hod/feedback/main/api/sessions').get_json()
    assert data['ok'] is True and 'subjects' in data


def test_hod_cannot_preview_out_of_scope_session(app, client):
    seed = _seed_hod(app, 'CSE')
    c = _login_hod(client)
    with app.app_context():
        other = db.query(
            """SELECT cs.id FROM class_sessions cs
               JOIN timetables t ON t.id=cs.timetable_id
               WHERE cs.is_deleted=0 AND t.branch_id <> ? LIMIT 1""",
            (seed['branch_id'],), one=True)
    if not other:
        pytest.skip('No out-of-scope session')
    assert c.get(f"/hod/feedback/main/api/preview?session_id={other['id']}").status_code == 403


def test_preview_eligible_count_matches_scope(app, client):
    seed = _seed_hod(app)
    sess = _a_cse_session(app)
    if not sess:
        pytest.skip('No session')
    with app.app_context():
        db.execute('DELETE FROM user_department_scopes WHERE user_id=?', (seed['hod_id'],))
        db.execute(
            'INSERT INTO user_department_scopes(user_id, branch_id, is_primary) VALUES(?,?,1)',
            (seed['hod_id'], sess['branch_id']))
    c = _login_hod(client)
    r = c.get(f"/hod/feedback/main/api/preview?session_id={sess['id']}")
    assert r.status_code == 200
    prev = r.get_json()['previews'][0]
    with app.app_context():
        expected = eligible_count(resolve_scope(MAIN, sess['id']))
    assert prev['eligible_count'] == expected
    assert len(prev['students']) == expected


def test_create_main_form_category_and_duplicate(app, client):
    seed = _seed_hod(app)
    sess = _a_cse_session(app)
    if not sess:
        pytest.skip('No session')
    with app.app_context():
        db.execute('DELETE FROM user_department_scopes WHERE user_id=?', (seed['hod_id'],))
        db.execute(
            'INSERT INTO user_department_scopes(user_id, branch_id, is_primary) VALUES(?,?,1)',
            (seed['hod_id'], sess['branch_id']))
        db.execute(
            "UPDATE feedback_forms SET is_deleted=1 WHERE session_id=? AND category='main'",
            (sess['id'],))
    qsid = _hod_qset_id(app, seed['hod_id'])
    c = _login_hod(client)
    html = c.get('/hod/feedback/main/create').get_data(as_text=True)
    m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert m
    csrf = m.group(1)
    r = c.post('/hod/feedback/main/api/create',
               json={'session_ids': [sess['id']], 'question_set_id': qsid, 'publish': False},
               headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    data = r.get_json()
    assert data['ok'] and data['created']
    form_id = data['created'][0]['form_id']
    with app.app_context():
        form = services.get_form(form_id)
        assert form['category'] == 'main'
        assert form['status'] == 'draft'
    r2 = c.post('/hod/feedback/main/api/create',
                json={'session_ids': [sess['id']], 'question_set_id': qsid, 'publish': False},
                headers={'X-CSRF-Token': csrf})
    body = r2.get_json()
    assert not body.get('ok') or body.get('errors')
    assert r2.status_code in (409, 207)


def test_main_submission_does_not_complete_second_or_lab(app, client):
    seed = _seed_hod(app)
    sess = _a_cse_session(app)
    if not sess:
        pytest.skip('No session')
    with app.app_context():
        db.execute('DELETE FROM user_department_scopes WHERE user_id=?', (seed['hod_id'],))
        db.execute(
            'INSERT INTO user_department_scopes(user_id, branch_id, is_primary) VALUES(?,?,1)',
            (seed['hod_id'], sess['branch_id']))
        db.execute(
            "UPDATE feedback_forms SET is_deleted=1 WHERE session_id=? AND category='main'",
            (sess['id'],))
        hod = db.query('SELECT * FROM users WHERE id=?', (seed['hod_id'],), one=True)
        questions = [{
            'text': 'Clarity', 'qtype': 'rating', 'is_required': True,
            'options': [{'label': 'Good', 'value': 5}, {'label': 'Bad', 'value': 1}],
        }]
        dl = services.parse_deadline_input(future_deadline(7))
        fid = category_forms.create_form(
            dict(hod), MAIN, sess['id'], 'Main only test', questions, deadline_at=dl)
        category_forms.publish(dict(hod), fid)
        students = services.eligible_students(sess['id'])
        assert students
        student_id = students[0]['id']
        q = db.query('SELECT id FROM form_questions WHERE form_id=?', (fid,), one=True)
        opt = db.query('SELECT id FROM question_options WHERE question_id=?', (q['id'],), one=True)
        ok, msg = services.submit_feedback(student_id, fid, {f'q_{q["id"]}': str(opt['id'])})
        assert ok, msg
        other = db.query(
            """SELECT COUNT(*) c FROM submissions s
               JOIN feedback_forms f ON f.id=s.form_id
               WHERE s.student_user_id=? AND f.session_id=? AND f.category IN ('second','lab')""",
            (student_id, sess['id']), one=True)['c']
        assert other == 0


def test_legacy_hod_form_new_still_works(app, client):
    _seed_hod(app)
    c = _login_hod(client)
    r = c.get('/hod/forms/new')
    assert r.status_code == 200
