"""Dean dashboard counts each STUDENT once, not once per feedback form."""
import os
import tempfile

import pytest

from feedback_system import create_app
from feedback_system import db as db_module
from feedback_system import dean
from feedback_system.security import hash_password


@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-deancount-')
    yield create_app({
        'TESTING': True, 'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None, 'SECRET_KEY': 'test-secret-dean-counts',
        'AUTO_IMPORT': False, 'ADMIN_EMAIL': 'admin@college.edu',
        'ADMIN_PASSWORD': 'admin123', 'REQUIRE_ACCOUNT_SETUP': False,
    })


def _user(role, code):
    db_module.execute(
        """INSERT INTO users(role, username, emp_code, roll, name, email, password_hash,
                             is_active, onboarding_complete)
           VALUES(?,?,?,?,?,?,?,1,1)""",
        (role, code, code, code if role == 'student' else None, code,
         f'{code.lower()}@t.edu', hash_password('pass12345')))
    return db_module.query('SELECT id FROM users WHERE username=?', (code,), one=True)['id']


def test_students_not_multiplied_by_number_of_forms(app):
    with app.app_context():
        db_module.execute("INSERT INTO branches(code, name) VALUES('ZZ','Test Branch')")
        branch = db_module.query("SELECT id FROM branches WHERE code='ZZ'", one=True)
        branch_id = branch['id']
        branches = [dict(id=branch_id, code='ZZ', name='Test Branch')]
        sem = db_module.query('SELECT id FROM semesters LIMIT 1', one=True)
        if sem is None:
            db_module.execute("INSERT INTO semesters(number, label) VALUES(1, 'Sem 1')")
            sem = db_module.query('SELECT id FROM semesters LIMIT 1', one=True)
        tt = db_module.insert_id(
            "INSERT INTO timetables(branch_id, semester_id, code, name) VALUES(?,?,?,?)",
            (branch_id, sem['id'], 'ZZ-A', 'ZZ A'))
        teacher = _user('teacher', 'T1')
        students = [_user('student', f'S{i}') for i in range(3)]

        form_ids = []
        for k in range(4):                       # 4 forms (e.g. 4 subjects)
            sid = db_module.insert_id(
                """INSERT INTO class_sessions(timetable_id, subject_code, subject_name,
                       teacher_user_id, period_no) VALUES(?,?,?,?,?)""",
                (tt, f'SUB{k}', f'Subject {k}', teacher, k + 1))
            fid = db_module.insert_id(
                """INSERT INTO feedback_forms(title, branch_id, status, session_id)
                   VALUES(?,?, 'open', ?)""", (f'Form {k}', branch_id, sid))
            db_module.execute('INSERT INTO form_sessions(form_id, session_id) VALUES(?,?)',
                              (fid, sid))
            for stu in students:                 # every student is in every form
                db_module.execute(
                    'INSERT INTO session_students(session_id, student_user_id) VALUES(?,?)',
                    (sid, stu))
            form_ids.append(fid)

        # S0 submits 2 forms, S1 submits 1 form, S2 submits nothing.
        for fid, stu in [(form_ids[0], students[0]), (form_ids[1], students[0]),
                         (form_ids[0], students[1])]:
            db_module.execute(
                'INSERT INTO submissions(form_id, student_user_id) VALUES(?,?)', (fid, stu))

        data = dean._dashboard_data({'id': 0}, branches, None, None, None)
        assert data['eligible'] == 3        # 3 students, NOT 12 student-form pairs
        assert data['submitted'] == 2       # S0 and S1 (S0 counted once, not twice)
        assert data['pending'] == 1
        assert data['rows'][0]['eligible'] == 3
        assert data['rows'][0]['submitted'] == 2
