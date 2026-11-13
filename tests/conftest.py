"""Shared pytest fixtures.

A single throwaway database is built for the session (which triggers the real
timetable import), then reused by the tests. Each test creates its own feedback
forms so tests do not interfere.
"""
import os
import tempfile

import pytest

from feedback_system import create_app

from helpers import login


@pytest.fixture(scope='session')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-test-')
    application = create_app({
        'TESTING': True,
        'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None,  # force SQLite even if the shell exports one
        'SECRET_KEY': 'test-secret-key',
        'AUTO_IMPORT': True,
        'ADMIN_EMAIL': 'admin@college.edu',
        'ADMIN_PASSWORD': 'admin123',
        'DEFAULT_TEACHER_PASSWORD': 'teacher@123',
        'DEFAULT_STUDENT_PASSWORD': 'student@123',
        'LOGIN_MAX_ATTEMPTS': 50,
        # The first-login gate is exercised in tests/test_account_setup.py; the
        # rest of the suite logs in as many users and skips it.
        'REQUIRE_ACCOUNT_SETUP': False,
    })
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin(client):
    r = login(client, '/admin/login', {'email': 'admin@college.edu', 'password': 'admin123'})
    assert r.status_code == 302
    return client


@pytest.fixture()
def teacher(app, client):
    # Some tests change 6817's password hash on the shared session DB. Always
    # restore the default hash before login so this fixture stays reliable.
    from feedback_system import db
    from feedback_system.security import hash_password
    with app.app_context():
        db.execute(
            "UPDATE users SET password_hash=?, must_change_password=0, "
            "first_login_complete=1 WHERE emp_code='6817'",
            (hash_password('teacher@123'),))
    r = login(client, '/teacher/login', {'emp_code': '6817', 'password': 'teacher@123'})
    assert r.status_code == 302
    # Login with the default password sets must_change_password again; clear it.
    # First-login flow is covered by dedicated tests.
    with app.app_context():
        db.execute("UPDATE users SET must_change_password=0, first_login_complete=1 "
                   "WHERE emp_code='6817'")
    return client
