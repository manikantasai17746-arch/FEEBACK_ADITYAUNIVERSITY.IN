"""Student portal: themed pages render, and the Notifications page works (it used to extend a missing template)."""
import os
import tempfile

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


def test_dashboard_history_tabs_use_cat_param(app):
    """With a Main and a Lab form the history shows both tabs and ?cat= picks one."""
    import test_hod_feedback_lab as T
    from feedback_system import category_forms, services
    from feedback_system.feedback_categories import LAB, MAIN
    from helpers import future_deadline
    seed = T._seed_hod(app)
    sess = T._session_with_lab(app, seed['branch_id'], 'available')
    if not sess:
        pytest.skip('no session with a lab')
    with app.app_context():
        hod = dict(db.query('SELECT * FROM users WHERE id=?', (seed['hod_id'],), one=True))
        db.execute("UPDATE feedback_forms SET is_deleted=1 WHERE session_id=?", (sess['id'],))
        qs = [{'text': 'Q', 'qtype': 'rating', 'is_required': True,
               'options': [{'label': 'Good', 'value': 5}, {'label': 'Bad', 'value': 1}]}]
        dl = services.parse_deadline_input(future_deadline(7))
        for cat in (MAIN, LAB):
            category_forms.publish(hod, category_forms.create_form(hod, cat, sess['id'], 'P', qs, deadline_at=dl))
        st = services.eligible_students(sess['id'])[0]
        db.execute("UPDATE users SET password_hash=?, must_change_password=0, onboarding_complete=1, "
                   "email='t@adityauniversity.in', email_verified=1 WHERE id=?",
                   (hash_password('student@123'), st['id']))
        student = {'roll': st['roll']}
    c = _client(app, student)
    html = c.get('/student/?cat=lab').get_data(as_text=True)
    assert 'Waiting for your feedback' in html and 'Your feedback history' in html
    assert 'Main Faculty' in html and 'Lab Feedback' in html
    assert 'cat=main' in html and 'cat=lab' in html
    assert c.get('/student/?cat=bogus').status_code == 200
    # the pending cards above the tabs never name the kind of feedback
    pending_part = html.split('Your feedback history')[0]
    assert 'Lab Feedback' not in pending_part and 'Main Faculty Feedback' not in pending_part


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
