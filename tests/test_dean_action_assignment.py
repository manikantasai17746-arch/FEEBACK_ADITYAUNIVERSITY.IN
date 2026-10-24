"""Dean assigns improvement actions to HODs; HODs see and update only their own."""
import os
import tempfile

import pytest

from feedback_system import create_app
from feedback_system import db as db_module
from feedback_system.security import hash_password
from helpers import get_csrf, login


@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-actions-')
    application = create_app({
        'TESTING': True,
        'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None,
        'SECRET_KEY': 'test-secret-key-actions',
        'AUTO_IMPORT': True,
        'ADMIN_EMAIL': 'admin@college.edu',
        'ADMIN_PASSWORD': 'admin123',
        'DEFAULT_TEACHER_PASSWORD': 'teacher@123',
        'DEFAULT_STUDENT_PASSWORD': 'student@123',
        'LOGIN_MAX_ATTEMPTS': 50,
        'REQUIRE_ACCOUNT_SETUP': False,
    })
    yield application


def _user(role, code, name):
    db_module.execute(
        """INSERT INTO users(role, username, emp_code, name, email, password_hash,
                             is_active, onboarding_complete)
           VALUES(?,?,?,?,?,?,1,1)""",
        (role, code, code, name, f'{code.lower()}@t.edu', hash_password('pass12345')))
    return db_module.query('SELECT id FROM users WHERE username=?', (code,), one=True)['id']


@pytest.fixture(scope='module')
def seeded(app):
    """Dean D over branches A and B; HOD1 owns A, HOD2 owns B, HOD3 is unassigned."""
    with app.app_context():
        tts = db_module.query(
            'SELECT id, branch_id, semester_id, academic_year_id FROM timetables '
            'WHERE is_deleted=0 ORDER BY branch_id')
        picks, seen = [], set()
        for t in tts:
            if t['branch_id'] not in seen and len(seen) < 3:
                seen.add(t['branch_id'])
                picks.append(t)
        dean = _user('dean', 'ADEAN', 'Action Dean')
        hods = []
        for i, t in enumerate(picks[:3], 1):
            hid = _user('hod', f'AHOD{i}', f'Action HOD {i}')
            db_module.execute(
                """INSERT INTO hod_allocations(hod_user_id, academic_year_id, branch_id,
                       semester_id, timetable_id) VALUES(?,?,?,?,?)""",
                (hid, t['academic_year_id'], t['branch_id'], t['semester_id'], t['id']))
            if i <= 2:  # HOD3 is NOT assigned to this Dean
                db_module.execute(
                    'INSERT INTO dean_hod_assignments(dean_user_id, hod_user_id) VALUES(?,?)',
                    (dean, hid))
            hods.append((hid, t['branch_id']))
        return {'dean': dean, 'hods': hods}


def _login(app, path, code):
    c = app.test_client()
    r = login(c, path, {'emp_code': code, 'password': 'pass12345'})
    assert r.status_code == 302
    return c


def _new_action(c, branch_id, owner_user_id, title='Improve labs'):
    tok = get_csrf(c.get('/dean/actions/new').get_data(as_text=True))
    return c.post('/dean/actions/new', data={
        '_csrf': tok, 'title': title, 'branch_id': branch_id,
        'owner_user_id': owner_user_id or '', 'priority': 'high', 'status': 'planned',
        'due_date': '2030-01-01'}, follow_redirects=False)


def _action_id(title):
    return db_module.query('SELECT id FROM improvement_actions WHERE title=?',
                           (title,), one=True)['id']


def test_dean_form_lists_only_assignable_hods(app, seeded):
    dean = _login(app, '/dean/login', 'ADEAN')
    html = dean.get('/dean/actions/new').get_data(as_text=True)
    assert 'Action HOD 1' in html and 'Action HOD 2' in html
    assert 'Action HOD 3' not in html


def test_assigned_action_reaches_the_hod_only(app, seeded):
    (h1, b1), (h2, _b2) = seeded['hods'][0], seeded['hods'][1]
    dean = _login(app, '/dean/login', 'ADEAN')
    assert _new_action(dean, b1, h1, 'Fix lab A').status_code == 302
    with app.app_context():
        row = db_module.query(
            'SELECT owner_user_id, owner_name FROM improvement_actions WHERE title=?',
            ('Fix lab A',), one=True)
        assert row['owner_user_id'] == h1 and row['owner_name'] == 'Action HOD 1'
    hod1 = _login(app, '/hod/login', 'AHOD1')
    assert 'Fix lab A' in hod1.get('/hod/actions').get_data(as_text=True)
    hod2 = _login(app, '/hod/login', 'AHOD2')
    assert 'Fix lab A' not in hod2.get('/hod/actions').get_data(as_text=True)


def test_hod_updates_status_and_dean_sees_it(app, seeded):
    h1 = seeded['hods'][0][0]
    with app.app_context():
        aid = _action_id('Fix lab A')
    hod1 = _login(app, '/hod/login', 'AHOD1')
    tok = get_csrf(hod1.get('/hod/actions').get_data(as_text=True))
    r = hod1.post(f'/hod/actions/{aid}/status',
                  data={'_csrf': tok, 'status': 'completed', 'note': 'Lab repaired'})
    assert r.status_code == 302
    with app.app_context():
        row = db_module.query('SELECT status, completed_at FROM improvement_actions WHERE id=?',
                              (aid,), one=True)
        assert row['status'] == 'completed' and row['completed_at']
        log = db_module.query(
            'SELECT actor_user_id, note, new_status FROM improvement_action_log '
            'WHERE action_id=? ORDER BY id DESC', (aid,), one=True)
        assert log['actor_user_id'] == h1 and log['new_status'] == 'completed'
        assert log['note'] == 'Lab repaired'
    dean = _login(app, '/dean/login', 'ADEAN')
    assert 'completed' in dean.get('/dean/actions').get_data(as_text=True)
    assert 'Lab repaired' in dean.get(f'/dean/actions/{aid}/edit').get_data(as_text=True)


def test_other_hod_cannot_change_someone_elses_action(app, seeded):
    with app.app_context():
        aid = _action_id('Fix lab A')
    hod2 = _login(app, '/hod/login', 'AHOD2')
    tok = get_csrf(hod2.get('/hod/actions').get_data(as_text=True))
    r = hod2.post(f'/hod/actions/{aid}/status', data={'_csrf': tok, 'status': 'planned'})
    assert r.status_code == 403
    with app.app_context():
        assert db_module.query('SELECT status FROM improvement_actions WHERE id=?',
                               (aid,), one=True)['status'] == 'completed'


def test_hod_cannot_set_cancelled_or_garbage_status(app, seeded):
    (h1, b1) = seeded['hods'][0]
    dean = _login(app, '/dean/login', 'ADEAN')
    _new_action(dean, b1, h1, 'Second task')
    with app.app_context():
        aid = _action_id('Second task')
    hod1 = _login(app, '/hod/login', 'AHOD1')
    tok = get_csrf(hod1.get('/hod/actions').get_data(as_text=True))
    for bad in ('cancelled', 'nonsense'):
        hod1.post(f'/hod/actions/{aid}/status', data={'_csrf': tok, 'status': bad})
    with app.app_context():
        assert db_module.query('SELECT status FROM improvement_actions WHERE id=?',
                               (aid,), one=True)['status'] == 'planned'


def test_dean_cannot_assign_unassigned_or_wrong_department_hod(app, seeded):
    (h1, b1), (h2, b2), (h3, _b3) = seeded['hods']
    dean = _login(app, '/dean/login', 'ADEAN')
    _new_action(dean, b1, h3, 'Bad HOD')         # HOD3 is not this Dean's HOD
    _new_action(dean, b1, h2, 'Wrong dept')      # HOD2 belongs to department B
    with app.app_context():
        titles = [r['title'] for r in db_module.query('SELECT title FROM improvement_actions')]
    assert 'Bad HOD' not in titles and 'Wrong dept' not in titles


def test_hod_actions_requires_hod_login(app):
    c = app.test_client()
    assert c.get('/hod/actions').status_code == 302
    dean = _login(app, '/dean/login', 'ADEAN')
    assert dean.get('/hod/actions').status_code == 403
