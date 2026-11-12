"""Teacher dashboard: every section of a subject is its own row (never combined)."""
import os
import re
import tempfile

import pytest

from feedback_system import create_app, services
from feedback_system import db as db_module
from feedback_system.security import hash_password
from helpers import login


@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-sections-')
    return create_app({
        'TESTING': True, 'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None, 'SECRET_KEY': 'test-secret-sections',
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


@pytest.fixture(scope='module')
def world(app):
    """One teacher, subject ASE in 3 sections, with 2 + 2 + 1 students."""
    with app.app_context():
        db_module.execute("INSERT INTO branches(code, name) VALUES('CSE','CSE')")
        branch_id = db_module.query("SELECT id FROM branches WHERE code='CSE'", one=True)['id']
        db_module.execute("INSERT INTO semesters(number, label) VALUES(3, 'Sem 3')")
        sem = db_module.query('SELECT id FROM semesters WHERE number=3', one=True)['id']
        teacher = _user('teacher', 'TSEC')
        sections = {}
        sessions = {}
        for code, periods, n_students in (('T3-General', [3], 2), ('T1-General', [5], 2),
                                          ('T6-T-HUB', [5], 1)):
            tt = db_module.insert_id(
                "INSERT INTO timetables(branch_id, semester_id, code, name) VALUES(?,?,?,?)",
                (branch_id, sem, code, code))
            students = [_user('student', f'{code}-S{i}') for i in range(n_students)]
            sections[code] = students
            sessions[code] = []
            for period in periods:
                sid = db_module.insert_id(
                    """INSERT INTO class_sessions(timetable_id, subject_code, subject_name,
                           teacher_user_id, period_no) VALUES(?,?,?,?,?)""",
                    (tt, '2501IT07', 'Agile Software Engineering', teacher, period))
                sessions[code].append(sid)
                for stu in students:
                    db_module.execute(
                        'INSERT INTO session_students(session_id, student_user_id) VALUES(?,?)',
                        (sid, stu))
        return {'teacher': teacher, 'branch': branch_id, 'sections': sections,
                'sessions': sessions}


def _rows(html):
    """Table rows of the performance table that mention the subject code."""
    return [r for r in re.findall(r'<tr>.*?</tr>', html, re.S) if '2501IT07' in r]


def test_sessions_by_section_keeps_sections_apart(app, world):
    with app.app_context():
        all_ids = [s for ids in world['sessions'].values() for s in ids]
        groups = services.sessions_by_section(all_ids)
    assert sorted(g['timetable'] for g in groups) == ['T1-General', 'T3-General', 'T6-T-HUB']
    assert all(len(g['session_ids']) == 1 for g in groups)


def test_no_form_yet_shows_one_row_per_section(app, world):
    c = app.test_client()
    assert login(c, '/teacher/login', {'emp_code': 'TSEC', 'password': 'pass12345'}).status_code == 302
    rows = _rows(c.get('/teacher/').get_data(as_text=True))
    assert len(rows) == 3
    text = ' '.join(rows)
    for section in ('T3-General', 'T1-General', 'T6-T-HUB'):
        assert section in text
    eligible = sorted(int(re.findall(r'<td>(\d+)</td>', r)[1]) for r in rows)
    # S.No is the 1st numeric cell, Eligible the 2nd; 2 + 2 + 1, not one row of 5.
    assert eligible == [1, 2, 2]


def test_published_form_is_split_by_section(app, world):
    with app.app_context():
        all_ids = [s for ids in world['sessions'].values() for s in ids]
        fid = db_module.insert_id(
            """INSERT INTO feedback_forms(title, branch_id, status, session_id,
                   subject_code, subject_name) VALUES(?,?, 'closed', ?, ?, ?)""",
            ('ASE Phase 1', world['branch'], all_ids[0], '2501IT07',
             'Agile Software Engineering'))
        for sid in all_ids:
            db_module.execute('INSERT INTO form_sessions(form_id, session_id) VALUES(?,?)',
                              (fid, sid))
            db_module.execute(
                'INSERT INTO result_publications(form_id, session_id, is_published) '
                'VALUES(?,?,1)', (fid, sid))
        # One student from T3-General responds.
        db_module.execute(
            'INSERT INTO submissions(form_id, session_id, student_user_id) VALUES(?,?,?)',
            (fid, world['sessions']['T3-General'][0], world['sections']['T3-General'][0]))
        world['form_id'] = fid

    c = app.test_client()
    assert login(c, '/teacher/login', {'emp_code': 'TSEC', 'password': 'pass12345'}).status_code == 302
    rows = _rows(c.get('/teacher/').get_data(as_text=True))
    assert len(rows) == 3
    by_section = {s: r for r in rows for s in ('T3-General', 'T1-General', 'T6-T-HUB') if s in r}
    assert set(by_section) == {'T3-General', 'T1-General', 'T6-T-HUB'}
    # Only the T3 section has a response; the others are not credited with it.
    assert re.findall(r'<td>(\d+)</td>', by_section['T3-General'])[1:3] == ['2', '1']
    assert re.findall(r'<td>(\d+)</td>', by_section['T1-General'])[1:3] == ['2', '0']
    assert re.findall(r'<td>(\d+)</td>', by_section['T6-T-HUB'])[1:3] == ['1', '0']


def test_results_page_can_show_a_single_section(app, world):
    c = app.test_client()
    assert login(c, '/teacher/login', {'emp_code': 'TSEC', 'password': 'pass12345'}).status_code == 302
    fid = world['form_id']
    with app.app_context():
        t6 = db_module.query("SELECT id FROM timetables WHERE code='T6-T-HUB'", one=True)['id']
    page = c.get(f'/teacher/results/{fid}?section={t6}')
    assert page.status_code == 200
    assert 'Section T6-T-HUB' in page.get_data(as_text=True)
    assert c.get(f'/teacher/results/{fid}?section=999999').status_code == 404
    assert c.get(f'/teacher/results/{fid}').status_code == 200   # whole-form view still works
