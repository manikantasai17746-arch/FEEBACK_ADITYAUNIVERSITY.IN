"""Student portal: themed pages render, and the Notifications page works (it used to extend a missing template)."""
import os
import tempfile
from datetime import datetime, timedelta

import pytest

from feedback_system import create_app, db
from feedback_system.security import hash_password
from helpers import login


@pytest.fixture(scope='module')
def app():
    tmp = tempfile.mkdtemp(prefix='ffs-student-theme-')
    return create_app({
        'TESTING': True, 'DATABASE': os.path.join(tmp, 't.db'), 'DATABASE_URL': None,
        'SECRET_KEY': 'k', 'AUTO_IMPORT': True, 'ADMIN_EMAIL': 'a@b.c', 'ADMIN_PASSWORD': 'admin123',
        'DEFAULT_STUDENT_PASSWORD': 'student@123', 'DEFAULT_TEACHER_PASSWORD': 'teacher@123',
        'LOGIN_MAX_ATTEMPTS': 50, 'REQUIRE_ACCOUNT_SETUP': False})


@pytest.fixture()
def student(app):
    with app.app_context():
        u = db.query("SELECT id, roll FROM users WHERE role='student' ORDER BY id", one=True)
        db.execute("UPDATE users SET password_hash=?, must_change_password=0, onboarding_complete=1, "
                   "email='s@adityauniversity.in', email_verified=1 WHERE id=?", (hash_password('student@123'), u['id']))
        db.execute("DELETE FROM notifications WHERE user_id=?", (u['id'],))
        db.execute("INSERT INTO notifications(user_id,kind,title,body,is_read) VALUES(?,?,?,?,0)",
                   (u['id'], 'deadline', 'Closes soon', 'Phase 2 closes this week.'))
        return dict(u)


def _client(app, student):
    c = app.test_client()
    login(c, '/student/login', {'roll': student['roll'], 'password': 'student@123'})
    return c


def test_student_pages_use_student_theme_only(app, student):
    c = _client(app, student)
    for path in ('/student/', '/student/notifications', '/change-password'):
        html = c.get(path).get_data(as_text=True)
        assert 'css/student-theme.css' in html, path
        assert 'css/teacher-theme.css' not in html, path
        assert 'role-student' in html


def test_notifications_page_renders_and_lists_items(app, student):
    c = _client(app, student)
    r = c.get('/student/notifications')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Closes soon' in html and 'Mark read' in html and 'unread' in html


def test_other_roles_do_not_load_student_theme(app):
    html = app.test_client().get('/teacher/login').get_data(as_text=True)
    assert 'student-theme.css' not in html


def test_student_change_password_page_has_new_layout(app, student):
    c = _client(app, student)
    html = c.get('/change-password').get_data(as_text=True)
    for needle in ('id="pw-meter"', 'Keep your account safe', 'js/password-form.js',
                   'name="current_password"', 'name="new_password"', 'name="confirm_password"'):
        assert needle in html, needle


def test_current_menu_item_is_highlighted(app, student):
    import re
    c = _client(app, student)
    for path, label in (('/student/', 'My Feedback'), ('/student/notifications', 'Notifications'),
                        ('/change-password', 'Password'), ('/account/security', 'Security')):
        html = c.get(path).get_data(as_text=True)
        active = re.findall(r'<a[^>]*class="active"[^>]*>([^<]+)</a>', html)
        assert [a.strip() for a in active] == [label], (path, active)


def test_mobile_pending_groups_show_each_form_and_earliest_deadline(app, monkeypatch):
    from flask import g
    from feedback_system import services, student

    common = dict(title='Phase 1', subject_name='Sample course', session_id=1,
                  status='open', submission_id=None, category_key='main',
                  category_label=services.CATEGORY_LABELS['main'], locked=False)
    forms = [
        dict(common, id=1, teacher_name='First teacher', deadline_at='2099-03-12T10:00:00'),
        dict(common, id=2, teacher_name='Second teacher', deadline_at='2099-03-10T10:00:00',
             category_key='second', category_label=services.CATEGORY_LABELS['second'], locked=True),
        dict(common, id=3, title='Practical review', teacher_name='Lab teacher', deadline_at=None,
             category_key='lab', category_label=services.CATEGORY_LABELS['lab']),
        dict(common, id=4, teacher_name='Completed teacher', deadline_at=None, submission_id=42),
    ]
    monkeypatch.setattr(services, 'student_forms', lambda uid: forms)
    with app.test_request_context('/student/?skip_issue=1'):
        g.user = dict(id=123456, role='student', name='Sample student')
        html = student.dashboard()
    groups = html.split('class="pending-groups">', 1)[1].split('class="grid grid-2 pending-forms"', 1)[0]
    assert groups.count('<details class="pending-group">') == 2
    phase, practical, _ = groups.split('</details>')
    header, rows = phase.split('</summary>')
    assert 'Phase 1' in header and services.format_deadline(forms[1]['deadline_at']) in header
    assert 'First teacher' in rows and 'Second teacher' in rows
    assert services.format_deadline(forms[0]['deadline_at']) in rows
    assert services.format_deadline(forms[1]['deadline_at']) in rows
    assert 'Main Faculty Feedback' in rows and 'Second Faculty Feedback' in rows
    assert 'href="/student/form/1"' in rows and 'href="/student/form/2"' not in rows
    assert 'Locked' in rows and 'Completed teacher' not in groups
    assert 'Practical review' in practical and 'Lab Feedback' in practical
    assert 'No deadline' in practical.split('</summary>')[0]


def test_student_form_shortens_default_copy_and_keeps_custom_instructions(app, monkeypatch):
    from flask import g
    from feedback_system import services, student

    form = dict(id=1, title='Survey', status='open', teacher_name='Sample teacher',
                subject_code='CS10', subject_name='Algorithms', timetable='A',
                deadline_at='2099-03-12T10:00:00', instructions=services.DEFAULT_INSTRUCTIONS)
    monkeypatch.setattr(services, 'student_form', lambda uid, fid: form)
    monkeypatch.setattr(services, 'form_questions', lambda fid: [])
    monkeypatch.setattr(student, '_feedback_gate', lambda user, fid: None)
    monkeypatch.setattr(services, 'submit_feedback', lambda uid, fid, data: (False, 'Try again'))
    with app.test_request_context('/student/form/1'):
        g.user = dict(id=123456, role='student', name='Sample student')
        html = student.view_form(1)
        assert services.DEFAULT_INSTRUCTIONS not in html
        assert 'Your feedback is anonymous.' in html and 'You can submit once.' in html
        assert 'CS10 — Algorithms' in html and '<summary>Class details</summary>' in html
        assert services.format_deadline(form['deadline_at']) in html
        form['instructions'] = 'Consider the lab sessions.'
        assert form['instructions'] in student.view_form(1)
        assert form['instructions'] in student.submit(1)


def test_feedback_categories_on_forms_and_history_tabs(app, monkeypatch):
    from test_hod_feedback_second_and_results import _ensure_sfa, _seed_hod
    from feedback_system import category_forms, services
    from feedback_system.feedback_categories import LAB, MAIN, SECOND
    from helpers import future_deadline, get_csrf, question_ids, submit_feedback

    seed = _seed_hod(app)
    assignment_id, = _ensure_sfa(app, seed['branch_id'], n=1)
    with app.app_context():
        hod = dict(db.query('SELECT * FROM users WHERE id=?', (seed['hod_id'],), one=True))
        session_id = db.query('SELECT session_id FROM second_faculty_assignments WHERE id=?',
                              (assignment_id,), one=True)['session_id']
        qs = [{'text': 'Q', 'qtype': 'rating', 'is_required': True,
               'options': [{'label': 'Good', 'value': 5}, {'label': 'Bad', 'value': 1}]}]
        dl = services.parse_deadline_input(future_deadline(7))
        forms = {}
        for category, scope_id in ((MAIN, session_id), (LAB, session_id), (SECOND, assignment_id)):
            form_id = category_forms.create_form(hod, category, scope_id, 'Teaching survey', qs,
                                                 deadline_at=dl, auth_required=False)
            assert not db.query('SELECT 1 FROM notifications WHERE form_id=?', (form_id,))
            forms[category] = form_id
        published = category_forms.publish_many(hod, forms.values())
        assert {f['form_id'] for f in published['published']} == set(forms.values())
        for form_id in forms.values():
            notified = db.query("SELECT user_id FROM notifications WHERE form_id=? AND kind='published'",
                                (form_id,))
            eligible = db.query('SELECT student_user_id FROM form_eligible_students WHERE form_id=?',
                                (form_id,))
            assert {r['user_id'] for r in notified} == {r['student_user_id'] for r in eligible}
        st = services.eligible_students(session_id)[0]
        db.execute("UPDATE users SET password_hash=?, must_change_password=0, onboarding_complete=1, "
                   "email='t@adityauniversity.in', email_verified=1 WHERE id=?",
                   (hash_password('student@123'), st['id']))
        student = {'roll': st['roll']}
    c = _client(app, student)
    inbox = c.get('/student/notifications').get_data(as_text=True)
    for category, form_id in forms.items():
        assert f'New {services.CATEGORY_LABELS[category]}' in inbox
        assert f'href="/student/form/{form_id}"' in inbox
    assert 'due soon' not in inbox
    html = c.get('/student/?cat=lab').get_data(as_text=True)
    assert 'Waiting for your feedback' in html and 'Your feedback history' in html
    assert 'Main Faculty' in html and 'Lab Feedback' in html and 'Second Faculty' in html
    assert all(f'cat={category}' in html for category in forms)
    assert c.get('/student/?cat=bogus').status_code == 200
    mobile_groups = html.split('class="pending-groups">', 1)[1].split('class="grid grid-2 pending-forms"', 1)[0]
    assert mobile_groups.count('<strong>Teaching survey</strong>') == 1
    next_url = None
    for category, label in ((MAIN, 'Main Faculty Feedback'), (LAB, 'Lab Feedback'),
                            (SECOND, 'Second Faculty Feedback')):
        assert label in mobile_groups
        form_id = forms[category]
        form_url = f'/student/form/{form_id}'
        if next_url:
            assert next_url == f'{form_url}?from_feedback=1'
        response = c.get(next_url or form_url)
        assert response.status_code == 200
        form_html = response.get_data(as_text=True)
        assert f'<h1 class="form-category">{label}</h1>' in form_html
        assert 'data-feedback-submit' in form_html
        arrival = 'id="ffs-loader-template" data-feedback-arrival'
        assert (arrival in form_html) == (next_url is not None)
        if next_url:
            assert form_html.index(arrival) < form_html.index('class="teacher-card"')
        submitted = submit_feedback(c, form_id, *question_ids(form_html))
        assert submitted.status_code == 302
        next_url = submitted.headers['Location']
        if category == MAIN:
            now = datetime.fromisoformat(dl) - timedelta(days=1)
            monkeypatch.setattr(services, '_utc_now_iso', lambda: now.isoformat(timespec='seconds'))
            inbox = c.get('/student/notifications').get_data(as_text=True)
            assert 'Lab Feedback due soon' in inbox and 'Second Faculty Feedback due soon' in inbox
            assert 'Main Faculty Feedback due soon' not in inbox
            r = c.post('/student/notifications/read-all', data={'_csrf': get_csrf(inbox)})
            assert r.status_code == 302
            c.get('/student/')
            c.get('/student/notifications')
            with app.app_context():
                reminders = db.query("""
                    SELECT form_id, is_read FROM notifications WHERE user_id=? AND kind='deadline'
                """, (st['id'],))
                assert {r['form_id']: r['is_read'] for r in reminders} == {forms[LAB]: 1, forms[SECOND]: 1}
                assert len(reminders) == 2
    assert next_url == '/student/'


def test_backup_codes_page_continues_to_my_feedback_for_students(app):
    from feedback_system import auth
    with app.test_request_context('/'):
        html = auth.render_backup_codes(['aaaa-bbbb-cccc'], reason='enrolled',
                                        user={'role': 'student'}).get_data(as_text=True)
        assert 'href="/student/"' in html
        later = auth.render_backup_codes(['aaaa-bbbb-cccc'], reason='regenerated',
                                         user={'role': 'student'}).get_data(as_text=True)
        assert 'href="/account/security"' in later
        teacher = auth.render_backup_codes(['aaaa-bbbb-cccc'], reason='enrolled',
                                           user={'role': 'teacher'}).get_data(as_text=True)
        assert 'href="/account/security"' in teacher


@pytest.mark.parametrize('path', ['/forgot-password?role=student', '/lost-phone', '/lost-phone/no-codes'])
def test_recovery_pages_use_recovery_theme_and_loader(app, path):
    r = app.test_client().get(path)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'css/recovery-theme.css' in html and 'class="role-guest recovery"' in html.replace('  ', ' ')
    assert 'js/loader.js' in html and 'css/loader.css' in html


def test_loader_is_available_in_student_portal_and_guest_pages(app, student):
    assert 'js/loader.js' in _client(app, student).get('/student/').get_data(as_text=True)
    assert 'js/loader.js' in app.test_client().get('/lost-phone').get_data(as_text=True)
    assert 'id="ffs-loader-template"' in app.test_client().get('/').get_data(as_text=True)


@pytest.mark.parametrize('role', ['student', 'teacher', 'hod', 'dean', 'admin'])
def test_base_loader_includes_requested_portals(app, role):
    from flask import render_template

    with app.test_request_context('/'):
        html = render_template('base.html', current_user={'role': role})
    assert ('id="ffs-loader-template"' in html) == (role != 'admin')
    assert ('css/loader.css' in html) == (role != 'admin')
