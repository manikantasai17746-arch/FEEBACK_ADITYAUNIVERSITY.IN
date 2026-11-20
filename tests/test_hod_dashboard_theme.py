"""HOD Overview data, navigation, theme isolation, and existing security gates."""
import re

import pytest

from feedback_system import create_app, db


HOD_PAGES = (
    '/hod/',
    '/hod/feedback/main', '/hod/feedback/main/create',
    '/hod/feedback/second', '/hod/feedback/second/create',
    '/hod/feedback/lab', '/hod/feedback/lab/create',
    '/hod/forms', '/hod/forms/new', '/hod/forms/pick-teachers', '/hod/question-sets',
    '/hod/pending', '/hod/performance/bands', '/hod/results',
    '/hod/actions', '/hod/performance', '/hod/performance/compare', '/hod/comparison',
    '/hod/second-faculty', '/profile', '/account/security',
)


UNSTYLED_HOD_CONTENT = {'/hod/', '/hod/pending', '/hod/second-faculty', '/account/security'}


def _new_user(role, name, onboarding_complete=True):
    username = name.lower().replace(' ', '-')
    return db.insert_id(
        """INSERT INTO users(
               role, username, name, emp_code, roll, password_hash,
               onboarding_complete, first_login_complete, email_verified)
           VALUES(?, ?, ?, ?, ?, 'unused', ?, 1, 1)""",
        (role, username, name, username, username, int(onboarding_complete)),
    )


def _new_form(title, category, status, branch, session=None, assignment=None):
    form = db.insert_id(
        """INSERT INTO feedback_forms(
               title, category, status, branch_id, session_id, second_faculty_assignment_id,
               snapshot_subject_code, snapshot_branch)
           VALUES(?, ?, ?, ?, ?, ?, 'TEST101', (SELECT code FROM branches WHERE id=?))""",
        (title, category, status, branch, session, assignment, branch),
    )
    if session:
        db.execute('INSERT INTO form_sessions(form_id, session_id) VALUES(?, ?)', (form, session))
    return form


def _seed(app):
    with app.app_context():
        ids = {
            'hod': _new_user('hod', 'Overview HOD'),
            'empty_hod': _new_user('hod', 'Empty HOD'),
            'no_forms_hod': _new_user('hod', 'No Forms HOD'),
            'onboarding_hod': _new_user('hod', 'Onboarding HOD', onboarding_complete=False),
            'teacher': _new_user('teacher', 'Overview Teacher'),
            'dean': _new_user('dean', 'Overview Dean'),
            'student': _new_user('student', 'Overview Student'),
            'admin': _new_user('admin', 'Overview Admin'),
        }
        own = db.insert_id("INSERT INTO branches(code, name) VALUES('TEST', 'Test Department')")
        other = db.insert_id("INSERT INTO branches(code, name) VALUES('OTHER', 'Other Department')")
        empty = db.insert_id("INSERT INTO branches(code, name) VALUES('EMPTY', 'Empty Department')")
        for key, branch in [('hod', own), ('no_forms_hod', empty), ('onboarding_hod', own)]:
            db.execute('INSERT INTO user_department_scopes(user_id, branch_id) VALUES(?, ?)',
                       (ids[key], branch))

        semester = db.insert_id("INSERT INTO semesters(number, label) VALUES(91, 'Test Semester')")
        timetable = db.insert_id(
            "INSERT INTO timetables(branch_id, semester_id, code, name) VALUES(?, ?, 'T1', 'Test Section')",
            (own, semester),
        )
        session = db.insert_id(
            """INSERT INTO class_sessions(timetable_id, subject_code, subject_name, teacher_user_id)
               VALUES(?, 'TEST101', 'Test Subject', ?)""",
            (timetable, ids['teacher']),
        )
        second_teacher = _new_user('teacher', 'Second Test Teacher')
        second_student = _new_user('student', 'Pending Test Student')
        for student in (ids['student'], second_student):
            db.execute('INSERT INTO enrollments(student_user_id, timetable_id, semester_id) VALUES(?, ?, ?)',
                       (student, timetable, semester))
            db.execute('INSERT INTO session_students(session_id, student_user_id) VALUES(?, ?)',
                       (session, student))

        db.execute(
            """INSERT INTO teacher_subject_settings(session_id, teacher_user_id, lab_status, lab_available)
               VALUES(?, ?, 'available', 1)""", (session, ids['teacher']),
        )
        assignment = db.insert_id(
            """INSERT INTO second_faculty_assignments(
                   branch_id, session_id, timetable_id, subject_code, second_emp_code,
                   main_teacher_user_id, second_teacher_user_id, status)
               VALUES(?, ?, ?, 'TEST101', 'second-test-teacher', ?, ?, 'confirmed')""",
            (own, session, timetable, ids['teacher'], second_teacher),
        )
        db.execute("INSERT INTO second_faculty_assignments(branch_id, subject_code) VALUES(?, 'TEST102')",
                   (own,))

        main = _new_form('Main Test Form', 'main', 'open', own, session)
        second = _new_form('Second Test Form', 'second', 'open', own, session, assignment=assignment)
        ids['main_form'] = main
        _new_form('Lab Test Draft', 'lab', 'draft', own, session)
        _new_form('Lab Test Closed', 'lab', 'closed', own, session)
        _new_form('Archived Test Form', 'main', 'archived', own)
        deleted = _new_form('Deleted Test Form', 'main', 'draft', own)
        db.execute('UPDATE feedback_forms SET is_deleted=1 WHERE id=?', (deleted,))
        ids['other_form'] = _new_form('Other Department Form', 'main', 'open', other)

        for form in (main, second):
            for student in (ids['student'], second_student):
                db.execute('INSERT INTO form_eligible_students(form_id, student_user_id) VALUES(?, ?)',
                           (form, student))
        db.execute('INSERT INTO submissions(form_id, session_id, student_user_id) VALUES(?, ?, ?)',
                   (main, session, ids['student']))
    return ids


@pytest.fixture(scope='module')
def portal(tmp_path_factory):
    app = create_app({
        'TESTING': True, 'DATABASE': str(tmp_path_factory.mktemp('hod-overview') / 'test.db'),
        'DATABASE_URL': None, 'SECRET_KEY': 'test-hod-overview', 'AUTO_IMPORT': False,
        'REQUIRE_ACCOUNT_SETUP': False, 'AUTHENTICATOR_ENFORCE_ROLES': '',
    })
    return app, _seed(app)


def _signed_in(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session['uid'] = user_id
        session['token'] = 'hod-test-csrf'
    return client


def test_overview_renders_scoped_counts_and_live_links(portal):
    app, ids = portal
    response = _signed_in(app, ids['hod']).get('/hod/')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'css/hod-theme.css' in html and 'js/hod-theme.js' in html
    assert 'class="active" aria-current="page" href="/hod/"' in html
    for category, expected in {
        'main': ['1', '1', '2', '1', '1', '0'],
        'second': ['1', '1', '2', '0', '2', '0'],
        'lab': ['1', '0', '0', '0', '0', '1'],
    }.items():
        card = html.split(f'<article class="nb-sheet nb-{category}"', 1)[1].split('</article>', 1)[0]
        assert re.findall(r'<dd>([^<]+)</dd>', card) == expected
        assert f'href="/hod/feedback/{category}"' in card

    totals = html.split('aria-label="Department totals">', 1)[1].split('</dl>', 1)[0]
    assert re.findall(r'<dd>([^<]+)</dd>', totals) == ['1', '2', '1', '1 / 2 / 1', '1', '1']
    for path in ('/hod/pending', '/hod/performance/bands', '/hod/results'):
        assert f'href="{path}"' in html.split('aria-label="Overview sections">', 1)[1].split('</nav>', 1)[0]
    for text in ('Main Test Form', 'Second Test Form', 'Lab Test Draft', 'Lab Test Closed',
                 'Overview Teacher', 'Second Test Teacher', 'OPEN', 'DRAFT', 'CLOSED', 'archived'):
        assert text in html
    for text in ('Other Department Form', 'Deleted Test Form', 'Pending Test Student', 'pending-test-student'):
        assert text not in html


@pytest.mark.parametrize(('role', 'message'), (
    ('empty_hod', 'No department scope assigned.'),
    ('no_forms_hod', 'No forms yet for your department(s).'),
))
def test_overview_empty_states(portal, role, message):
    app, ids = portal
    response = _signed_in(app, ids[role]).get('/hod/')
    assert response.status_code == 200
    assert message in response.get_data(as_text=True)


@pytest.mark.parametrize('role', ('student', 'teacher', 'dean', 'admin'))
def test_other_roles_do_not_load_hod_theme(portal, role):
    app, ids = portal
    response = _signed_in(app, ids[role]).get('/account/security')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'css/hod-theme.css' not in html
    assert 'css/hod-pages.css' not in html
    assert 'js/hod-theme.js' not in html
    assert 'hod-topnav' not in html


@pytest.mark.parametrize('path', HOD_PAGES)
def test_hod_pages_keep_working_with_shared_header(portal, path):
    app, ids = portal
    response = _signed_in(app, ids['hod']).get(path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'css/hod-theme.css' in html
    nav = html.split('class="hod-nav-links">', 1)[1].split('</div>', 1)[0]
    assert len(re.findall(r'<a\b', nav)) == 12
    assert ('class="hod-overview"' in html) == (path == '/hod/')
    assert ('class="role-hod hod-content"' in html) == (path not in UNSTYLED_HOD_CONTENT)


def test_onboarding_keeps_expected_statuses(portal):
    app, ids = portal
    response = _signed_in(app, ids['onboarding_hod']).get('/hod/onboarding')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'css/hod-pages.css' in html
    assert 'class="role-hod hod-content"' in html
    assert 'HOD first-login setup' in html
    response = _signed_in(app, ids['hod']).get('/hod/onboarding')
    assert response.status_code == 302
    assert response.location == '/hod/'


def test_dashboard_and_existing_actions_keep_access_gates(portal):
    app, ids = portal
    guest = app.test_client().get('/hod/')
    assert guest.status_code == 302
    assert guest.location.startswith('/hod/login?')
    assert _signed_in(app, ids['teacher']).get('/hod/').status_code == 403

    hod = _signed_in(app, ids['hod'])
    assert hod.get(f"/hod/pending?form_id={ids['other_form']}").status_code == 403
    path = f"/hod/forms/{ids['main_form']}/status"
    assert hod.post(path, data={'status': 'closed'}).status_code == 400
    path = f"/hod/forms/{ids['other_form']}/status"
    assert hod.post(path, data={'status': 'closed', '_csrf': 'hod-test-csrf'}).status_code == 403
