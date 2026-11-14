"""Phase 3 — Teacher Assignment Setup (rooms + lab availability).

Runs on its own throw-away database with a small, fully known world (two
teachers, several sections) so every assertion is exact.  Two tests also use the
real timetable import to prove that real data never yields duplicates.

Numbering in the docstrings (T1..T32) follows the Phase 3 brief.
"""
import os
import re
import sqlite3
import tempfile

import pytest

from feedback_system import assignment_setup as setup
from feedback_system import create_app, db, pg, schema
from feedback_system.security import hash_password
from helpers import get_csrf, login, make_form, publish_form

PW = 'pass12345'


def login_student(client, roll):
    return login(client, '/student/login', {'roll': roll, 'password': PW})
SETUP = '/teacher/assignment-setup'
API = SETUP + '/api'


# --------------------------------------------------------------------------- #
# World
# --------------------------------------------------------------------------- #
def _user(role, code, **extra):
    db.execute(
        """INSERT INTO users(role, username, emp_code, roll, name, email, password_hash,
                             is_active, onboarding_complete, first_login_complete,
                             email_verified, must_change_password)
           VALUES(?,?,?,?,?,?,?,1,1,1,1,0)""",
        (role, code, code if role != 'student' else None,
         code if role == 'student' else None, extra.get('name', code),
         f'{code.lower()}@t.edu', hash_password(PW)))
    return db.query('SELECT id FROM users WHERE username=?', (code,), one=True)['id']


def _timetable(branch, sem, year, code):
    return db.insert_id(
        'INSERT INTO timetables(branch_id, semester_id, academic_year_id, code, name) '
        'VALUES(?,?,?,?,?)', (branch, sem, year, code, f'{code} name'))


def _session(tt, code, name, teacher, period):
    sid = db.insert_id(
        """INSERT INTO class_sessions(timetable_id, subject_code, subject_name,
               teacher_user_id, period_no) VALUES(?,?,?,?,?)""",
        (tt, code, name, teacher, period))
    return sid


@pytest.fixture(scope='module')
def app():
    tmp = tempfile.mkdtemp(prefix='ffs-phase3-')
    return create_app({
        'TESTING': True, 'DATABASE': os.path.join(tmp, 'test.db'), 'DATABASE_URL': None,
        'SECRET_KEY': 'test-secret-phase3', 'AUTO_IMPORT': False,
        'ADMIN_EMAIL': 'admin@college.edu', 'ADMIN_PASSWORD': 'admin123',
        'LOGIN_MAX_ATTEMPTS': 100, 'REQUIRE_ACCOUNT_SETUP': False,
    })


@pytest.fixture(scope='module')
def world(app):
    """T1 teaches ADS in sections A and B, DBMS in A, AC in A.  T2 teaches OTHER."""
    with app.app_context():
        db.execute("INSERT INTO branches(code, name) VALUES('CSE','Computer Science')")
        branch = db.query("SELECT id FROM branches WHERE code='CSE'", one=True)['id']
        db.execute("INSERT INTO semesters(number, label) VALUES(5, 'Semester V')")
        sem = db.query('SELECT id FROM semesters WHERE number=5', one=True)['id']
        db.execute("INSERT INTO academic_years(name, is_active) VALUES('2025-26', 1)")
        year = db.query("SELECT id FROM academic_years WHERE name='2025-26'", one=True)['id']

        t1, t2 = _user('teacher', 'T1', name='Teacher One'), _user('teacher', 'T2', name='Teacher Two')
        hod = _user('hod', 'HOD1')
        db.execute("""INSERT INTO users(role, username, email, name, password_hash, is_active)
                      VALUES('admin','admin','admin@college.edu','Admin',?,1)""",
                   (hash_password('admin123'),))
        stu = _user('student', 'S1')
        tt_a, tt_b = _timetable(branch, sem, year, 'A'), _timetable(branch, sem, year, 'B')
        w = {
            't1': t1, 't2': t2, 'hod': hod, 'student': stu, 'tt_a': tt_a, 'tt_b': tt_b,
            'ads_a': _session(tt_a, 'ADSI', 'Advanced Data Structures', t1, 2),
            'ads_b': _session(tt_b, 'ADSI', 'Advanced Data Structures', t1, 4),
            'dbms_a': _session(tt_a, 'DBMS', 'Database Management Systems', t1, 3),
            'ac_a': _session(tt_a, 'AC', 'Advanced Calculus', t1, 1),
            'other_a': _session(tt_a, 'OTH1', 'Other Teachers Subject', t2, 5),
        }
        db.execute('INSERT INTO session_students(session_id, student_user_id) VALUES(?,?)',
                   (w['ads_a'], stu))
        w['mine'] = [w['ads_a'], w['ads_b'], w['dbms_a'], w['ac_a']]
        return w


@pytest.fixture(autouse=True)
def _clean(app, world):
    yield
    with app.app_context():
        c = db.get_db()
        c.execute('DROP TRIGGER IF EXISTS boom')
        c.execute('DELETE FROM lab_faculty_assignments')
        c.execute('DELETE FROM teacher_subject_settings')
        c.commit()


def _login_as(client, code, path='/teacher/login'):
    r = login(client, path, {'emp_code': code, 'password': PW})
    assert r.status_code == 302
    return client


@pytest.fixture()
def t1(app, client_factory):
    return _login_as(client_factory(), 'T1')


@pytest.fixture()
def t2(app, client_factory):
    return _login_as(client_factory(), 'T2')


@pytest.fixture()
def client_factory(app):
    return app.test_client


def _tok(c):
    return get_csrf(c.get(SETUP).get_data(as_text=True))


def post(c, path, payload, token=None):
    return c.post(API + path, json=payload,
                  headers={'X-CSRF-Token': token or _tok(c), 'X-Requested-With': 'XMLHttpRequest'})


def save(c, items):
    return post(c, '/save', {'items': items})


def settings(app, sid):
    with app.app_context():
        r = db.query('SELECT * FROM teacher_subject_settings WHERE session_id=?', (sid,), one=True)
        return dict(r) if r else None


def count_settings(app):
    with app.app_context():
        return db.query('SELECT COUNT(*) c FROM teacher_subject_settings', one=True)['c']


# =========================================================================== #
# Subject retrieval (T1-T5)
# =========================================================================== #
def test_t1_teacher_sees_assigned_subjects(t1, world):
    html = t1.get(SETUP).get_data(as_text=True)
    for text in ('Advanced Data Structures', 'ADSI', 'Database Management Systems', 'DBMS',
                 'Advanced Calculus', 'Section A', 'Section B', 'Semester V', '2025-26', 'CSE',
                 'Employee ID T1'):
        assert text in html, text


def test_t2_teacher_cannot_see_another_teachers_subjects(t1, t2, world):
    html = t1.get(SETUP).get_data(as_text=True)
    assert 'OTH1' not in html and 'Other Teachers Subject' not in html
    j = t1.get(API + '/assignments').get_json()
    assert 'OTH1' not in {s['subject_code'] for s in j['subjects']}
    # ...and the other teacher sees only theirs.
    j2 = t2.get(API + '/assignments').get_json()
    assert [s['subject_code'] for s in j2['subjects']] == ['OTH1']


def test_t3_multiple_subjects_display_one_card_each(t1, world):
    j = t1.get(API + '/assignments').get_json()
    codes = [s['subject_code'] for s in j['subjects']]
    assert sorted(codes) == ['AC', 'ADSI', 'DBMS']
    adsi = next(s for s in j['subjects'] if s['subject_code'] == 'ADSI')
    assert sorted(x['section'] for x in adsi['sections']) == ['A', 'B']
    assert j['summary']['total'] == 4


def test_t3b_preview_returns_only_active_eligible_students_and_supports_search(app, t1, world):
    with app.app_context():
        second = _user('student', 'S2', name='Second Student')
        db.execute('INSERT INTO session_students(session_id, student_user_id) VALUES(?,?)',
                   (world['ads_a'], second))
        inactive = _user('student', 'INACTIVE', name='Inactive Student')
        db.execute('UPDATE users SET is_active=0 WHERE id=?', (inactive,))
        db.execute('INSERT INTO session_students(session_id, student_user_id) VALUES(?,?)',
                   (world['ads_a'], inactive))

    response = t1.get(API + '/students', query_string={'session_id': world['ads_a']})
    assert response.status_code == 200
    body = response.get_json()
    assert body['ok'] and body['total'] == 2
    assert body['assignment']['subject_code'] == 'ADSI'
    assert body['assignment']['section'] == 'A'
    assert {row['roll'] for row in body['rows']} == {'S1', 'S2'}

    response = t1.get(API + '/students', query_string={
        'session_id': world['ads_a'], 'search': 'second'})
    assert response.status_code == 200
    body = response.get_json()
    assert body['total'] == 1 and body['rows'][0]['roll'] == 'S2'

    response = t1.get(API + '/students', query_string={
        'session_id': world['ads_a'], 'page': 2, 'page_size': 1})
    assert response.status_code == 200
    body = response.get_json()
    assert body['page'] == 2 and body['pages'] == 2
    assert len(body['rows']) == 1


def test_t3c_preview_rejects_another_teachers_session(t1, world):
    response = t1.get(API + '/students', query_string={'session_id': world['other_a']})
    assert response.status_code == 404


def test_t4_repeated_timetable_sessions_never_duplicate(app, world):
    """The (timetable, subject, teacher) key is unique, so periods collapse into one
    assignment; the settings table is additionally unique per session."""
    with app.app_context():
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('INSERT INTO class_sessions(timetable_id, subject_code, subject_name, '
                       'teacher_user_id, period_no) VALUES(?,?,?,?,?)',
                       (world['tt_a'], 'ADSI', 'Advanced Data Structures', world['t1'], 6))
        db.execute('INSERT INTO teacher_subject_settings(session_id, teacher_user_id) VALUES(?,?)',
                   (world['ads_a'], world['t1']))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('INSERT INTO teacher_subject_settings(session_id, teacher_user_id) '
                       'VALUES(?,?)', (world['ads_a'], world['t1']))
        rows = setup.teacher_assignments(world['t1'])
        assert len({(r['subject_code'], r['timetable_id']) for r in rows}) == len(rows) == 4


def test_t4b_real_import_has_no_duplicate_assignments():
    """Real timetable (38k rows): still exactly one assignment per subject+section."""
    tmp = tempfile.mkdtemp(prefix='ffs-phase3-real-')
    real = create_app({'TESTING': True, 'DATABASE': os.path.join(tmp, 'r.db'),
                       'DATABASE_URL': None, 'SECRET_KEY': 'k', 'AUTO_IMPORT': True,
                       'ADMIN_EMAIL': 'a@college.edu', 'ADMIN_PASSWORD': 'admin123',
                       'REQUIRE_ACCOUNT_SETUP': False})
    with real.app_context():
        tids = [r['id'] for r in db.query("SELECT id FROM users WHERE role='teacher'")]
        assert len(tids) > 100
        with_rows = 0
        for tid in tids:
            rows = setup.teacher_assignments(tid)
            keys = [(r['subject_code'], r['timetable_id']) for r in rows]
            assert len(keys) == len(set(keys))
            assert len({r['session_id'] for r in rows}) == len(rows)
            with_rows += bool(rows)
        assert with_rows > 100
        # a subject taught in several sections stays several independent assignments
        multi = [g for tid in tids for g in setup.group_by_subject(setup.teacher_assignments(tid))
                 if len(g['sections']) > 1]
        assert multi


def test_t5_sections_are_independent(app, t1, world):
    assert save(t1, [{'session_id': world['ads_a'], 'room': '101', 'lab_status': 'available'}]).status_code == 200
    a, b = settings(app, world['ads_a']), settings(app, world['ads_b'])
    assert a['room'] == '101' and a['lab_status'] == 'available'
    assert b is None
    assert save(t1, [{'session_id': world['ads_b'], 'room': '202', 'lab_status': 'none'}]).status_code == 200
    assert settings(app, world['ads_a'])['room'] == '101'
    assert settings(app, world['ads_b'])['lab_status'] == 'none'


# =========================================================================== #
# Room configuration (T6-T11)
# =========================================================================== #
def test_t6_save_a_room_number(app, t1, world):
    r = post(t1, '/room', {'session_id': world['ads_a'], 'room': ' A-205 '})
    assert r.status_code == 200 and r.get_json()['ok']
    row = settings(app, world['ads_a'])
    assert row['room'] == 'A-205' and row['teacher_user_id'] == world['t1']
    assert row['lab_status'] is None and row['lab_available'] == 0


def test_t7_empty_room_is_allowed_and_stored_as_null(app, t1, world):
    for empty in ('', '   ', None):
        r = post(t1, '/room', {'session_id': world['ac_a'], 'room': empty})
        assert r.status_code == 200, empty
        assert settings(app, world['ac_a'])['room'] is None
    j = t1.get(API + '/assignments').get_json()
    assert j['summary']['without_room'] == 4


def test_t8_update_an_existing_room(app, t1, world):
    post(t1, '/room', {'session_id': world['dbms_a'], 'room': '301'})
    post(t1, '/room', {'session_id': world['dbms_a'], 'room': '302'})
    assert settings(app, world['dbms_a'])['room'] == '302'
    assert count_settings(app) == 1                      # updated, not duplicated
    post(t1, '/room', {'session_id': world['dbms_a'], 'room': ''})   # and can be cleared again
    assert settings(app, world['dbms_a'])['room'] is None


def test_t9_different_rooms_for_different_subjects(app, t1, world):
    save(t1, [{'session_id': world['ads_a'], 'room': '205'},
              {'session_id': world['dbms_a'], 'room': '301'},
              {'session_id': world['ac_a'], 'room': '104'}])
    assert [settings(app, world[k])['room'] for k in ('ads_a', 'dbms_a', 'ac_a')] == ['205', '301', '104']


def test_t10_different_rooms_for_different_sections(app, t1, world):
    save(t1, [{'session_id': world['ads_a'], 'room': '205'},
              {'session_id': world['ads_b'], 'room': '206'}])
    assert settings(app, world['ads_a'])['room'] == '205'
    assert settings(app, world['ads_b'])['room'] == '206'


def test_t11_unauthorized_room_updates_are_rejected(app, t1, t2, world, client_factory):
    # teacher 1 -> teacher 2's session
    r = post(t1, '/room', {'session_id': world['other_a'], 'room': 'HACK'})
    assert r.status_code == 403 and not r.get_json()['ok']
    assert settings(app, world['other_a']) is None
    # a mixed request is rejected as a whole: nothing of it is saved
    r = save(t1, [{'session_id': world['ads_a'], 'room': '777'},
                  {'session_id': world['other_a'], 'room': '888'}])
    assert r.status_code == 403 and count_settings(app) == 0
    # unknown ids look identical to foreign ids
    assert post(t1, '/room', {'session_id': 999999, 'room': '1'}).status_code == 403
    # the legitimate owner is unaffected
    assert post(t2, '/room', {'session_id': world['other_a'], 'room': '12'}).status_code == 200
    # other roles / anonymous cannot use the API at all
    anon = client_factory()
    assert anon.get(API + '/assignments').status_code == 302
    stu = client_factory(); login_student(stu, 'S1')
    assert stu.get(SETUP).status_code in (302, 403)
    assert stu.get(API + '/assignments').status_code in (302, 403)
    hod = client_factory(); login(hod, '/hod/login', {'emp_code': 'HOD1', 'password': PW})
    assert hod.get(API + '/assignments').status_code in (302, 403)
    assert hod.get(SETUP).status_code in (302, 403)


# =========================================================================== #
# Lab availability (T12-T17)
# =========================================================================== #
def test_t12_select_lab_available(app, t1, world):
    r = post(t1, '/lab', {'session_id': world['ads_a'], 'lab_status': 'available'})
    assert r.status_code == 200
    row = settings(app, world['ads_a'])
    assert row['lab_status'] == 'available' and row['lab_available'] == 1


def test_t13_select_no_lab(app, t1, world):
    post(t1, '/lab', {'session_id': world['ac_a'], 'lab_status': 'none'})
    row = settings(app, world['ac_a'])
    assert row['lab_status'] == 'none' and row['lab_available'] == 0


def test_t14_update_existing_lab_availability(app, t1, world):
    post(t1, '/lab', {'session_id': world['ads_a'], 'lab_status': 'available'})
    post(t1, '/lab', {'session_id': world['ads_a'], 'lab_status': 'none'})
    row = settings(app, world['ads_a'])
    assert row['lab_status'] == 'none' and row['lab_available'] == 0 and count_settings(app) == 1
    # changing lab never wipes the room that was saved before
    post(t1, '/room', {'session_id': world['ads_a'], 'room': '205'})
    post(t1, '/lab', {'session_id': world['ads_a'], 'lab_status': 'available'})
    assert settings(app, world['ads_a'])['room'] == '205'


def test_t15_no_lab_and_unconfigured_are_excluded_from_lab_eligibility(app, t1, world):
    save(t1, [{'session_id': world['ads_a'], 'lab_status': 'available'},
              {'session_id': world['dbms_a'], 'lab_status': 'none'}])      # ADS_B, AC untouched
    with app.app_context():
        eligible = {r['session_id'] for r in setup.lab_eligible_sessions(world['t1'])}
    assert eligible == {world['ads_a']}


def test_t16_lab_available_does_not_create_lab_faculty_assignments(app, t1, world):
    save(t1, [{'session_id': s, 'lab_status': 'available'} for s in world['mine']])
    with app.app_context():
        assert db.query('SELECT COUNT(*) c FROM lab_faculty_assignments', one=True)['c'] == 0
        assert db.query("SELECT COUNT(*) c FROM feedback_forms WHERE category='lab'", one=True)['c'] == 0


def test_t17_timetable_inconsistency_is_reported_not_silently_changed(app, t1, world):
    """Lab faculty already confirmed for a session => 'No Lab' is refused, loudly."""
    save(t1, [{'session_id': world['ads_a'], 'lab_status': 'available', 'room': '205'}])
    with app.app_context():
        db.execute("""INSERT INTO lab_faculty_assignments
            (session_id, main_teacher_user_id, lab_teacher_user_id, lab_emp_code, status)
            VALUES(?,?,?,?, 'confirmed')""", (world['ads_a'], world['t1'], world['t2'], 'T2'))
    r = save(t1, [{'session_id': world['dbms_a'], 'room': '301'},        # valid, must NOT be saved
                  {'session_id': world['ads_a'], 'lab_status': 'none'}])
    j = r.get_json()
    assert r.status_code == 422 and not j['ok']
    assert j['errors'][0]['session_id'] == world['ads_a'] and 'already assigned' in j['errors'][0]['message']
    assert settings(app, world['ads_a'])['lab_status'] == 'available'     # unchanged
    assert settings(app, world['dbms_a']) is None                         # all-or-nothing
    with app.app_context():                                               # assignment untouched
        assert db.query("SELECT status FROM lab_faculty_assignments", one=True)['status'] == 'confirmed'
    # keeping Lab Available is fine, and so is a cancelled assignment
    assert post(t1, '/lab', {'session_id': world['ads_a'], 'lab_status': 'available'}).status_code == 200
    with app.app_context():
        db.execute("UPDATE lab_faculty_assignments SET status='cancelled'")
    assert post(t1, '/lab', {'session_id': world['ads_a'], 'lab_status': 'none'}).status_code == 200
    # the page itself locks the radio so the teacher sees why
    with app.app_context():
        db.execute("UPDATE lab_faculty_assignments SET status='confirmed'")
    html = t1.get(SETUP).get_data(as_text=True)
    assert 'Lab faculty is already assigned here' in html


# =========================================================================== #
# Workflow (T18-T24)
# =========================================================================== #
def test_t18_all_four_steps_are_present_and_navigable(t1):
    html = t1.get(SETUP).get_data(as_text=True)
    for n in (1, 2, 3, 4):
        assert f'data-panel="{n}"' in html and f'data-go="{n}"' in html
    for label in ('Subjects', 'Room Configuration', 'Lab Availability', 'Review and Save',
                  'Previous Step', 'Next Step', 'Save Configuration', 'Cancel changes',
                  'Yes, save'):
        assert label in html, label
    assert html.count('Room Not Assigned') == 4
    assert 'js/assignment_setup.js' in html and 'css/assignment_setup.css' in html
    # actual click-through navigation is exercised in test_assignment_setup_ui.py


def test_t19_t20_save_then_reload_persists(app, t1, world, client_factory):
    r = save(t1, [{'session_id': world['ads_a'], 'room': '205', 'lab_status': 'available'},
                  {'session_id': world['ads_b'], 'room': '', 'lab_status': 'none'},
                  {'session_id': world['dbms_a'], 'room': '301', 'lab_status': 'available'},
                  {'session_id': world['ac_a'], 'room': '104', 'lab_status': 'none'}])
    j = r.get_json()
    assert r.status_code == 200 and j['ok'] and j['summary']['complete']
    # fresh browser, fresh login = real reload
    fresh = _login_as(client_factory(), 'T1')
    got = {a['session_id']: a for a in fresh.get(API + '/assignments').get_json()['subjects'][0]['sections']}
    html = fresh.get(SETUP).get_data(as_text=True)
    assert 'Setup complete' in html
    assert 'value="205"' in html and 'value="301"' in html and 'value="104"' in html
    assert 'Assigned Room:</b> 205' in html
    assert 'Assigned Room:</b> 301' in html
    assert 'Assigned Room:</b> 104' in html
    assert re.search(r'name="lab-%d" value="available" checked' % world['ads_a'], html)
    assert re.search(r'name="lab-%d" value="none" checked' % world['ac_a'], html)
    assert got  # structure sanity


def test_t21_edit_a_saved_configuration(app, t1, world):
    save(t1, [{'session_id': world['ads_a'], 'room': '205', 'lab_status': 'available'}])
    r = save(t1, [{'session_id': world['ads_a'], 'room': '210', 'lab_status': 'none'}])
    assert r.status_code == 200
    row = settings(app, world['ads_a'])
    assert (row['room'], row['lab_status'], row['lab_available']) == ('210', 'none', 0)
    assert count_settings(app) == 1


def test_t22_configuration_summary_counts(t1, world):
    s0 = t1.get(API + '/summary').get_json()['summary']
    assert s0 == {'total': 4, 'configured': 0, 'pending': 4, 'lab_available': 0, 'no_lab': 0,
                  'without_room': 4, 'complete': False}
    save(t1, [{'session_id': world['ads_a'], 'room': '205', 'lab_status': 'available'},
              {'session_id': world['dbms_a'], 'room': '301', 'lab_status': 'available'},
              {'session_id': world['ac_a'], 'room': '104', 'lab_status': 'none'}])
    s = t1.get(API + '/summary').get_json()['summary']
    assert s == {'total': 4, 'configured': 3, 'pending': 1, 'lab_available': 2, 'no_lab': 1,
                 'without_room': 1, 'complete': False}
    s = save(t1, [{'session_id': world['ads_b'], 'lab_status': 'none'}]).get_json()['summary']
    assert s['complete'] and s['pending'] == 0


@pytest.mark.parametrize('payload', [
    {'items': []}, {'items': 'x'}, {}, {'items': [1]}, {'items': [{}]},
    {'items': [{'session_id': 'abc', 'room': '1'}]},
    {'items': [{'session_id': True, 'room': '1'}]},
    {'items': [{'session_id': 1.5, 'room': '1'}]},
    {'items': [{'session_id': None, 'room': '1'}]},
])
def test_t23_invalid_payloads_are_rejected(app, t1, payload):
    r = post(t1, '/save', payload)
    assert r.status_code == 400 and not r.get_json()['ok']
    assert count_settings(app) == 0


@pytest.mark.parametrize('item,field', [
    ({'room': 'x' * 21}, 'room'),
    ({'room': '<script>alert(1)</script>'}, 'room'),
    ({'room': "1'; DROP TABLE users;--"}, 'room'),
    ({'room': 205}, 'room'),
    ({'room': ['1']}, 'room'),
    ({'lab_status': 'maybe'}, 'lab_status'),
    ({'lab_status': 1}, 'lab_status'),
    ({'lab_status': 'AVAILABLE'}, 'lab_status'),
])
def test_t23_invalid_values_are_rejected_and_nothing_saved(app, t1, world, item, field):
    r = save(t1, [{'session_id': world['ads_a'], **item}])
    j = r.get_json()
    assert r.status_code == 422 and j['errors'][0]['field'] == field
    assert count_settings(app) == 0
    with app.app_context():
        assert db.query('SELECT COUNT(*) c FROM users', one=True)['c'] >= 4     # injection inert


def test_t23_duplicates_oversize_and_non_json(app, t1, world):
    dup = [{'session_id': world['ads_a'], 'room': '1'}, {'session_id': world['ads_a'], 'room': '2'}]
    assert save(t1, dup).status_code == 400
    assert save(t1, [{'session_id': i, 'room': '1'} for i in range(1, setup.MAX_ITEMS + 2)]).status_code == 400
    r = t1.post(API + '/save', data='not json', headers={'X-CSRF-Token': _tok(t1),
                                                          'Content-Type': 'application/json'})
    assert r.status_code == 400
    assert post(t1, '/room', {'session_id': world['ads_a']}).status_code == 400      # room key missing
    assert post(t1, '/lab', {'session_id': world['ads_a']}).status_code == 400       # lab key missing
    assert count_settings(app) == 0


def test_t23_csrf_is_required(app, t1, world):
    r = t1.post(API + '/save', json={'items': [{'session_id': world['ads_a'], 'room': '1'}]},
                headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code == 400 and count_settings(app) == 0
    r = t1.post(API + '/room', json={'session_id': world['ads_a'], 'room': '1'},
                headers={'X-CSRF-Token': 'wrong', 'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code == 400 and count_settings(app) == 0


def test_t24_database_failure_rolls_the_whole_save_back(app, t1, world):
    save(t1, [{'session_id': world['ads_a'], 'room': 'OLD'}])                # existing row
    with app.app_context():
        c = db.get_db()
        c.execute("""CREATE TRIGGER boom BEFORE INSERT ON teacher_subject_settings
                     WHEN NEW.session_id = %d BEGIN SELECT RAISE(ABORT, 'simulated failure'); END"""
                  % world['dbms_a'])
        c.commit()
    r = save(t1, [{'session_id': world['ads_a'], 'room': 'NEW'},            # would succeed
                  {'session_id': world['ads_b'], 'room': 'B-NEW'},          # would succeed
                  {'session_id': world['dbms_a'], 'room': 'X'}])            # blows up
    j = r.get_json()
    assert r.status_code == 500 and not j['ok'] and 'Nothing was changed' in j['error']
    assert 'simulated' not in r.get_data(as_text=True)                      # no DB internals leaked
    assert settings(app, world['ads_a'])['room'] == 'OLD'
    assert settings(app, world['ads_b']) is None
    # and the app still works afterwards
    with app.app_context():
        db.get_db().execute('DROP TRIGGER boom'); db.get_db().commit()
    assert save(t1, [{'session_id': world['ads_a'], 'room': 'NEW'}]).status_code == 200


def test_audit_entry_is_written_on_save(app, t1, world):
    save(t1, [{'session_id': world['ads_a'], 'room': '5'}])
    with app.app_context():
        row = db.query("SELECT actor_user_id, detail FROM audit_log WHERE action='teacher_setup_save' "
                       "ORDER BY id DESC LIMIT 1", one=True)
    assert row['actor_user_id'] == world['t1'] and '1 assignment' in row['detail']


# =========================================================================== #
# Migration
# =========================================================================== #
def test_migration_v21_shape_backfill_and_postgres(app, world):
    stmts = [s for v, _d, ss in schema.MIGRATIONS if v == 21 for s in ss]
    # v21 must exist and be applied; later migrations (v22+) are expected.
    assert stmts and schema.MIGRATIONS[-1][0] >= 21
    assert any(v == 21 for v, _d, _ss in schema.MIGRATIONS)
    assert any('ADD COLUMN lab_status' in s for s in stmts)
    assert not any(re.search(r'\b(DROP|DELETE)\b', s, re.I) for s in stmts)      # non-destructive
    for s in stmts:
        assert pg.to_postgres(s)
    pgsql = ' '.join(pg.to_postgres(s) for s in stmts)
    assert 'ADD COLUMN IF NOT EXISTS lab_status' in pgsql
    with app.app_context():
        cols = {r['name'] for r in db.query('PRAGMA table_info(teacher_subject_settings)')}
        assert {'lab_status', 'room', 'lab_available'} <= cols
        # back-fill rule: legacy lab_available=1 -> 'available'; the default 0 stays "not configured"
        db.execute('INSERT INTO teacher_subject_settings(session_id, teacher_user_id, lab_available) '
                   'VALUES(?,?,1)', (world['ads_a'], world['t1']))
        db.execute('INSERT INTO teacher_subject_settings(session_id, teacher_user_id, lab_available) '
                   'VALUES(?,?,0)', (world['ads_b'], world['t1']))
        backfill = [s for s in stmts if s.startswith('UPDATE')][0]
        db.execute(backfill)
        assert db.query('SELECT lab_status FROM teacher_subject_settings WHERE session_id=?',
                        (world['ads_a'],), one=True)['lab_status'] == 'available'
        assert db.query('SELECT lab_status FROM teacher_subject_settings WHERE session_id=?',
                        (world['ads_b'],), one=True)['lab_status'] is None
        db.execute(backfill)                                                    # idempotent
        with pytest.raises(sqlite3.IntegrityError):                             # CHECK constraint
            db.execute("UPDATE teacher_subject_settings SET lab_status='bogus' WHERE session_id=?",
                       (world['ads_a'],))


# =========================================================================== #
# Regression (T25-T29)
# =========================================================================== #
def test_t25_teacher_login_still_works(client_factory):
    c = client_factory()
    r = login(c, '/teacher/login', {'emp_code': 'T1', 'password': PW})
    assert r.status_code == 302
    assert c.get('/teacher/').status_code == 200                    # dashboard untouched
    assert 'Assignment Setup' in c.get('/teacher/').get_data(as_text=True)   # new nav link


def test_t26_student_login_still_works(client_factory):
    c = client_factory()
    assert login_student(c, 'S1').status_code == 302
    assert c.get('/student/').status_code == 200


def test_t27_hod_login_still_works(client_factory):
    c = client_factory()
    assert login(c, '/hod/login', {'emp_code': 'HOD1', 'password': PW}).status_code == 302


def test_t28_existing_feedback_forms_remain_accessible(app, world, client_factory):
    admin = client_factory()
    assert login(admin, '/admin/login', {'email': 'admin@college.edu', 'password': 'admin123'}).status_code == 302
    fid = make_form(admin, world['ads_a'], title='Phase3 regression form')
    publish_form(admin, fid)
    stu = client_factory(); login_student(stu, 'S1')
    r = stu.get(f'/student/form/{fid}')
    assert r.status_code == 200 and b'Phase3 regression form' in r.data


def test_t29_timetable_and_feedback_data_are_never_modified(app, t1, world):
    def snapshot():
        with app.app_context():
            return {t: [tuple(r) for r in db.query(f'SELECT * FROM {t} ORDER BY 1')]
                    for t in ('class_sessions', 'timetables', 'session_students', 'enrollments',
                              'feedback_forms', 'submissions', 'answers', 'form_sessions',
                              'lab_faculty_assignments', 'second_faculty_assignments', 'users')}
    before = snapshot()
    save(t1, [{'session_id': s, 'room': f'R{s}', 'lab_status': 'available'} for s in world['mine']])
    save(t1, [{'session_id': s, 'room': '', 'lab_status': 'none'} for s in world['mine']])
    assert snapshot() == before


def test_phase2_first_login_flow_still_lands_on_the_setup_page(app, client_factory):
    """Phase 2 redirects here after the photo step; the page must render for that teacher."""
    c = _login_as(client_factory(), 'T2')
    r = c.get(SETUP)
    assert r.status_code == 200 and b'Teacher Assignment Setup' in r.data


def test_empty_state_for_teacher_without_subjects(app, client_factory):
    with app.app_context():
        _user('teacher', 'NOSUBJ')
    c = _login_as(client_factory(), 'NOSUBJ')
    html = c.get(SETUP).get_data(as_text=True)
    assert 'No subjects assigned yet' in html and 'data-panel="1"' not in html
    assert c.get(API + '/assignments').get_json()['summary']['total'] == 0
    assert post(c, '/save', {'items': [{'session_id': 1, 'room': '1'}]}).status_code == 403
