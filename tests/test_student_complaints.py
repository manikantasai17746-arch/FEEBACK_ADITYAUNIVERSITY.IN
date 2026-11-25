"""Exercise report gating, enrollment, and staff access through the real routes."""
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from feedback_system import category_forms, create_app, db, services
from feedback_system.security import hash_password
from helpers import get_csrf, login


@pytest.fixture()
def complaint_app(tmp_path):
    app = create_app({
        'TESTING': True, 'DATABASE': str(tmp_path / 'complaints.sqlite'),
        'DATABASE_URL': None, 'SECRET_KEY': 'complaint-test',
        'AUTO_IMPORT': False, 'REQUIRE_ACCOUNT_SETUP': False,
    })
    with app.app_context():
        conn = db.get_db()
        conn.execute("INSERT INTO academic_years(id, name, is_active) VALUES(1, '2026-27', 1)")
        conn.executemany('INSERT INTO branches(id, code, name) VALUES(?, ?, ?)',
                         [(1, 'CSE', 'Computer Science'), (2, 'ECE', 'Electronics')])
        conn.executemany('INSERT INTO semesters(id, number, label) VALUES(?, ?, ?)',
                         [(1, 3, 'Semester 3'), (2, 5, 'Semester 5')])
        password = hash_password('Test-account-password')
        conn.executemany("""
            INSERT INTO users(id, role, username, name, roll, emp_code, password_hash,
                              onboarding_complete, first_login_complete)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)
        """, [
            (10, 'student', 'STUDENT-A', 'Student A', 'STUDENT-A', None, password),
            (11, 'student', 'STUDENT-B', 'Student B', 'STUDENT-B', None, password),
            (12, 'student', 'STUDENT-C', 'Student C', 'STUDENT-C', None, password),
            (20, 'teacher', 'TEACHER-A', 'Teacher A', None, 'TEACHER-A', password),
            (30, 'hod', 'HOD-A', 'Allocated HOD', None, 'HOD-A', password),
            (31, 'hod', 'HOD-B', 'Department HOD', None, 'HOD-B', password),
            (32, 'hod', 'HOD-C', 'Scoped HOD', None, 'HOD-C', password),
            (40, 'dean', 'DEAN-A', 'Dean A', None, 'DEAN-A', password),
        ])
        conn.executemany("""
            INSERT INTO timetables(id, branch_id, semester_id, academic_year_id, code, name)
            VALUES (?, ?, ?, 1, ?, ?)
        """, [(1, 1, 1, 'CSE-A', 'CSE A'), (2, 1, 2, 'CSE-B', 'CSE B'), (3, 2, 2, 'ECE-A', 'ECE A')])
        conn.executemany("""
            INSERT INTO enrollments(student_user_id, timetable_id, semester_id, academic_year_id)
            VALUES (?, ?, ?, 1)
        """, [(10, 1, 1), (11, 2, 2), (12, 3, 2)])
        conn.executemany("""
            INSERT INTO class_sessions(id, timetable_id, subject_code, subject_name, teacher_user_id)
            VALUES (?, ?, ?, 'Test subject', 20)
        """, [(1, 1, 'SUBJECT-A'), (2, 2, 'SUBJECT-B'), (3, 3, 'SUBJECT-C'), (4, 1, 'SUBJECT-D')])
        conn.executemany('INSERT INTO session_students(session_id, student_user_id) VALUES (?, ?)',
                         [(1, 10), (2, 11), (3, 12), (4, 10)])
        conn.execute("""
            INSERT INTO hod_allocations(hod_user_id, academic_year_id, branch_id, semester_id, timetable_id)
            VALUES (30, 1, 1, 1, 1)
        """)
        conn.executemany('INSERT INTO user_department_scopes(user_id, branch_id, is_primary) VALUES (?, ?, 1)',
                         [(31, 2), (32, 1)])
        conn.execute('INSERT INTO dean_hod_assignments(dean_user_id, hod_user_id) VALUES (40, 30)')
        conn.commit()
    return app


def _portal(app, role, username):
    client = app.test_client()
    field = 'roll' if role == 'student' else 'emp_code'
    response = login(client, f'/{role}/login', {field: username, 'password': 'Test-account-password'})
    assert response.status_code == 302
    return client


def _publish(app, session_id):
    with app.app_context():
        hod_id = 31 if session_id == 3 else 30
        hod = db.query('SELECT * FROM users WHERE id=?', (hod_id,), one=True)
        deadline = (datetime.now(timezone.utc) + timedelta(days=2)).replace(tzinfo=None).isoformat(timespec='seconds')
        form_id = category_forms.create_form(
            hod, 'main', session_id, 'Test feedback',
            [{'text': 'Question', 'qtype': 'rating', 'is_required': True,
              'options': [{'label': 'Good', 'value': 5}, {'label': 'Poor', 'value': 1}]}],
            deadline_at=deadline, auth_required=False,
        )
        category_forms.publish(hod, form_id)
    return form_id


def _complete(app, client, form_id):
    path = f'/student/form/{form_id}'
    page = client.get(path)
    assert page.status_code == 200
    questions = page.get_data(as_text=True)
    with app.app_context():
        question, = services.form_questions(form_id)
    response = client.post(path + '/submit', data={
        '_csrf': get_csrf(questions), f"q_{question['id']}": question['options'][0]['id'],
    })
    assert response.status_code == 302
    return response


def _report_payload(client, description):
    html = client.get('/student/').get_data(as_text=True)
    assert 'Report a College Issue' in html
    return {
        '_csrf': get_csrf(html), 'category': 'lighting', 'description': description,
        'submission_key': re.search(r'name="submission_key" value="([^"]+)"', html)[1],
    }


def test_report_requires_all_feedback_and_saves_separately(complaint_app):
    app = complaint_app
    student = _portal(app, 'student', 'STUDENT-A')
    html = student.get('/student/').get_data(as_text=True)
    assert 'Report a College Issue' not in html
    payload = {'_csrf': get_csrf(html), 'submission_key': uuid4().hex,
               'category': 'lighting', 'description': 'Lights flicker.'}
    assert student.post('/student/complaints', data=payload).status_code == 403
    first, second = _publish(app, 1), _publish(app, 4)
    _complete(app, student, first)
    assert b'Report a College Issue' not in student.get('/student/').data
    assert student.post('/student/complaints', data=payload).status_code == 403
    _complete(app, student, second)
    assert b'Report a College Issue' not in student.get('/student/?skip_issue=1').data
    with app.app_context():
        assert not db.query('SELECT 1 FROM student_complaints')
    payload = _report_payload(student, '  Lights flicker.  ')
    invalid = student.post('/student/complaints', data={**payload, 'category': 'invalid'})
    assert invalid.status_code == 422 and b'Lights flicker.' in invalid.data
    payload.update(branch_id=2, semester_id=2, status='resolved', student_user_id=12)
    assert student.post('/student/complaints', data=payload).status_code == 302
    assert student.post('/student/complaints', data=payload).status_code == 302
    with app.app_context():
        complaint, = db.query('SELECT * FROM student_complaints')
        assert (complaint['student_user_id'], complaint['branch_id'], complaint['semester_id']) == (10, 1, 1)
        assert complaint['status'] == 'pending' and complaint['description'] == 'Lights flicker.'
        assert len(db.query('SELECT 1 FROM submissions')) == 2
        assert len(db.query('SELECT 1 FROM anonymous_answers')) == 2


def test_staff_filters_and_status_updates(complaint_app):
    app = complaint_app
    reports = [('STUDENT-A', 'Fan in Room A', 1), ('STUDENT-B', '<b>Room B</b>', 2),
               ('STUDENT-C', 'Broken projector', 3)]
    for username, description, session_id in reports:
        student = _portal(app, 'student', username)
        _complete(app, student, _publish(app, session_id))
        assert student.post('/student/complaints', data=_report_payload(student, description)).status_code == 302
    hod = _portal(app, 'hod', 'HOD-A')
    dean = _portal(app, 'dean', 'DEAN-A')
    hod_url = '/hod/student-complaints'
    dean_url = '/dean/student-complaints'
    assert b'class="btn btn-ghost" href="/hod/student-complaints"' in hod.get('/hod/').data
    assert b'class="btn btn-ghost mt" href="/dean/student-complaints"' in dean.get('/dean/').data
    page = hod.get(hod_url).get_data(as_text=True)
    assert 'Fan in Room A' in page and '&lt;b&gt;Room B&lt;/b&gt;' in page
    assert 'Broken projector' not in page
    filtered = hod.get(hod_url, query_string={'semester': 2, 'department': 2}).data
    assert b'Room B' in filtered and b'Fan in Room A' not in filtered and b'Broken projector' not in filtered
    page = dean.get(dean_url).get_data(as_text=True)
    assert all(value in page for value in ('Fan in Room A', 'Broken projector', 'Allocated HOD',
                                            'Department HOD', 'Scoped HOD', 'Computer Science', 'Electronics'))
    assert 'Student A' not in page and '<b>Room B</b>' not in page
    filtered = dean.get(dean_url, query_string={'department': 2, 'semester': 2}).data
    assert b'Broken projector' in filtered and b'Fan in Room A' not in filtered and b'Room B' not in filtered
    assert b'No complaints match' in dean.get(dean_url, query_string={'department': 2, 'semester': 1}).data
    with app.app_context():
        ids = {row['student_user_id']: row['id'] for row in db.query('SELECT id, student_user_id FROM student_complaints')}
    change = {'_csrf': get_csrf(hod.get(hod_url).get_data(as_text=True)),
              'prior_status': 'pending', 'status': 'in_progress'}
    assert hod.post(f'{hod_url}/{ids[12]}/status', data=change).status_code == 404
    assert hod.post(f'{hod_url}/{ids[10]}/status', data={'status': 'in_progress'}).status_code == 400
    assert hod.post(f'{hod_url}/{ids[10]}/status', data={**change, 'status': 'bad'}).status_code == 400
    assert hod.post(f'{hod_url}/{ids[10]}/status', data=change).status_code == 302
    assert b'value="in_progress" selected' in dean.get(dean_url).data
    dean_token = get_csrf(page)
    resolved = {'_csrf': dean_token, 'prior_status': 'in_progress', 'status': 'resolved'}
    assert dean.post(f'{dean_url}/{ids[10]}/status', data=resolved).status_code == 302
    assert b'value="resolved" selected' in hod.get(hod_url).data
    stale = hod.post(f'{hod_url}/{ids[10]}/status', data=change, follow_redirects=True)
    assert b'This complaint changed' in stale.data and b'value="resolved" selected' in stale.data
    assert _portal(app, 'student', 'STUDENT-A').get(hod_url).status_code == 403
    assert hod.get(dean_url).status_code == 403
    assert dean.post('/student/complaints', data={'_csrf': dean_token}).status_code == 403


def test_complaint_status_notifies_only_reporting_student(complaint_app):
    app = complaint_app
    with app.app_context():
        complaint_id = db.execute_returning_id("""
            INSERT INTO student_complaints
                (student_user_id, branch_id, semester_id, timetable_id, submission_key, category, description)
            VALUES (10, 1, 1, 1, ?, 'lighting', 'Private complaint details')
        """, (uuid4().hex,))

    def signed_in(user_id):
        client = app.test_client()
        with client.session_transaction() as session:
            session['uid'] = user_id
        return client

    reporter, other = signed_in(10), signed_in(11)
    inbox = '/student/notifications'
    title = f'Complaint #{complaint_id}'
    for role, staff_id, previous, status, expected in (
        ('hod', 31, 'pending', 'resolved', 404),
        ('hod', 30, 'pending', 'in_progress', 302),
        ('hod', 30, 'pending', 'in_progress', 302),  # repeated save
        ('dean', 40, 'pending', 'resolved', 302),    # stale page
        ('dean', 40, 'in_progress', 'resolved', 302),
    ):
        staff = signed_in(staff_id)
        path = f'/{role}/student-complaints'
        csrf = get_csrf(staff.get(path).get_data(as_text=True))
        response = staff.post(f'{path}/{complaint_id}/status', data={
            '_csrf': csrf, 'prior_status': previous, 'status': status, 'student_user_id': 11,
        })
        assert response.status_code == expected
    html = reporter.get(inbox).get_data(as_text=True)
    assert title in html and '2 unread' in html
    assert 'The HOD updated your Lighting complaint from Pending to In Progress.' in html
    assert 'The Dean updated your Lighting complaint from In Progress to Resolved.' in html
    assert 'Private complaint details' not in html
    assert title not in other.get(inbox).get_data(as_text=True)
    with app.app_context():
        notices = db.query('SELECT id, user_id, title FROM notifications ORDER BY id')
        assert [(n['user_id'], n['title']) for n in notices] == [
            (10, f'{title} · In Progress'), (10, f'{title} · Resolved')]
    html = reporter.post(f"{inbox}/{notices[0]['id']}/read", data={'_csrf': get_csrf(html)},
                         follow_redirects=True).get_data(as_text=True)
    assert f'{title} · In Progress' not in html and f'{title} · Resolved' in html
