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
