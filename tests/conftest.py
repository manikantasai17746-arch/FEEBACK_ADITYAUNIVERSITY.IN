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
    r = login(client, '/teacher/login', {'emp_code': '6817', 'password': 'teacher@123'})
    assert r.status_code == 302
    # Logging in on the shared default password sets must_change_password; the
    # first-login flow itself is covered by test_account_setup.py and
    # test_first_login_phase2.py, so other tests start past it.
    from feedback_system import db
    with app.app_context():
        db.execute("UPDATE users SET must_change_password=0, first_login_complete=1 "
                   "WHERE emp_code='6817'")
    return client
