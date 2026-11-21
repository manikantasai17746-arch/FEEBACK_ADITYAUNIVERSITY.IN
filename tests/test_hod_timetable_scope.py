"""HOD timetable-level scope: an HOD who claimed only some timetables of a
department must not see assignments, sections or counts of the others."""
import os
import tempfile

import pytest

from feedback_system import create_app, db
from feedback_system.security import hash_password
from helpers import login

PW = 'hodpass123'


@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-hod-ttscope-')
    yield create_app({
        'TESTING': True,
        'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None,
        'SECRET_KEY': 'test-hod-tt-scope',
        'AUTO_IMPORT': True,
        'ADMIN_EMAIL': 'admin@college.edu',
        'ADMIN_PASSWORD': 'admin123',
        'DEFAULT_TEACHER_PASSWORD': 'teacher@123',
        'DEFAULT_STUDENT_PASSWORD': 'student@123',
        'LOGIN_MAX_ATTEMPTS': 50,
        'REQUIRE_ACCOUNT_SETUP': False,
    })


def _seed(app, claimed_codes):
    """HOD for CSE Sem 3 that owns only the timetables in *claimed_codes*."""
    with app.app_context():
        cse = db.query("SELECT id FROM branches WHERE code='CSE'", one=True)
        rows = db.query(
            """SELECT t.id, t.code, t.semester_id, t.academic_year_id
               FROM timetables t WHERE t.branch_id=? AND t.code IN ({})""".format(
                ','.join('?' * len(claimed_codes))),
            (cse['id'], *claimed_codes))
        assert rows, 'expected imported CSE timetables'
        existing = db.query("SELECT id FROM users WHERE username='HODTT1'", one=True)
        if existing:
            uid = existing['id']
            db.execute('DELETE FROM hod_allocations WHERE hod_user_id=?', (uid,))
        else:
            uid = db.insert_id(
                """INSERT INTO users(role, username, emp_code, name, email, password_hash,
                                     is_active, onboarding_complete)
                   VALUES('hod','HODTT1','HODTT1','TT HOD','hodtt@test.edu',?,1,1)""",
                (hash_password(PW),))
        for r in rows:
            db.execute(
                """INSERT INTO hod_allocations(hod_user_id, academic_year_id, branch_id,
                                               semester_id, timetable_id)
                   VALUES(?,?,?,?,?)""",
                (uid, r['academic_year_id'], cse['id'], r['semester_id'], r['id']))
        db.get_db().commit()
        return uid, [r['id'] for r in rows]


def test_main_list_and_dashboard_only_claimed_timetable(app):
    _uid, tt_ids = _seed(app, ['T1-General'])
    client = app.test_client()
    r = login(client, '/hod/login', {'emp_code': 'HODTT1', 'password': PW})
    assert r.status_code == 302

    data = client.get('/hod/feedback/main/api/sessions').get_json()
    assert data['ok']
    sections = {s['section_code']
                for sub in data['subjects']
                for t in sub['teachers'] for s in t['sections']}
    assert sections == {'T1-General'}

    ai06 = [s for s in data['subjects'] if s['subject_code'] == '2501AI06']
    assert ai06, 'Data Analysis Essentials should be listed for T1'
    assert len(ai06[0]['teachers']) == 3
    assert ai06[0]['session_count'] == 3
    assert ai06[0]['eligible_total'] == 212

    from feedback_system.hod import _category_overview
    with app.app_context():
        ov = {o['key']: o for o in _category_overview(
            [db.query("SELECT id FROM branches WHERE code='CSE'", one=True)['id']], tt_ids)}
    assert ov['main']['assignments'] == 19


def test_session_in_other_timetable_is_rejected(app):
    _seed(app, ['T1-General'])
    with app.app_context():
        other = db.query(
            """SELECT cs.id FROM class_sessions cs JOIN timetables t ON t.id=cs.timetable_id
               WHERE t.code='T2-General' AND cs.is_deleted=0
                 AND cs.teacher_user_id IS NOT NULL LIMIT 1""", one=True)
    client = app.test_client()
    login(client, '/hod/login', {'emp_code': 'HODTT1', 'password': PW})
    r = client.get(f"/hod/feedback/main/api/preview?session_id={other['id']}")
    assert r.status_code == 403


def test_hod_without_allocations_keeps_branch_scope(app):
    from feedback_system.security import scoped_timetable_ids
    with app.app_context():
        db.execute("DELETE FROM hod_allocations WHERE hod_user_id=(SELECT id FROM users WHERE username='HODTT1')")
        db.get_db().commit()
        uid = db.query("SELECT id FROM users WHERE username='HODTT1'", one=True)['id']
        from flask import g
        with app.test_request_context():
            assert scoped_timetable_ids({'id': uid, 'role': 'hod'}) is None
