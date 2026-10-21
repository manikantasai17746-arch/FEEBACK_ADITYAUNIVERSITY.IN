"""Regression: first-admin bootstrap, credential sync, Admin/HOD/Dean login."""
import csv
import io
import os
import re
import tempfile

import pytest

from feedback_system import create_app
from feedback_system import db as db_module
from feedback_system.import_data import ensure_admin, set_admin_credentials
from feedback_system.security import hash_password, verify_password
from helpers import get_csrf, login


@pytest.fixture()
def app_factory(tmp_path):
    def _make(**extra):
        cfg = {
            'TESTING': True,
            'DATABASE': str(tmp_path / 'admin-test.db'),
            'DATABASE_URL': None,
            'SECRET_KEY': 'test-secret-admin-init',
            'AUTO_IMPORT': False,  # control bootstrap ourselves
            'ADMIN_EMAIL': 'admin@college.edu',
            'ADMIN_PASSWORD': 'admin123',
            'DEFAULT_TEACHER_PASSWORD': 'teacher@123',
            'DEFAULT_STUDENT_PASSWORD': 'student@123',
            'LOGIN_MAX_ATTEMPTS': 50,
            'REQUIRE_ACCOUNT_SETUP': False,
        }
        cfg.update(extra)
        return create_app(cfg)
    return _make


def test_ensure_admin_creates_first_account(app_factory):
    app = app_factory()
    with app.app_context():
        conn = db_module.get_db()
        assert db_module.query(
            "SELECT id FROM users WHERE role='admin'", one=True) is None
        aid = ensure_admin(conn, 'admin@college.edu', 'admin123')
        conn.commit()
        assert aid is not None
        row = db_module.query(
            "SELECT * FROM users WHERE role='admin'", one=True)
        assert row['username'] == 'admin@college.edu'
        assert row['email'] == 'admin@college.edu'
        assert row['is_active'] == 1
        assert verify_password(row['password_hash'], 'admin123')
        # Second call does not overwrite password
        ensure_admin(conn, 'other@college.edu', 'otherpass99')
        conn.commit()
        row2 = db_module.query(
            "SELECT * FROM users WHERE role='admin'", one=True)
        assert row2['username'] == 'admin@college.edu'
        assert verify_password(row2['password_hash'], 'admin123')


def test_ensure_admin_without_credentials_returns_none(app_factory):
    app = app_factory(ADMIN_EMAIL=None, ADMIN_PASSWORD=None)
    with app.app_context():
        conn = db_module.get_db()
        assert ensure_admin(conn, None, None) is None
        assert db_module.query(
            "SELECT id FROM users WHERE role='admin'", one=True) is None


def test_set_admin_credentials_creates_and_resets(app_factory):
    app = app_factory()
    with app.app_context():
        conn = db_module.get_db()
        aid = set_admin_credentials(conn, 'admin@college.edu', 'FirstPass12')
        row = db_module.query("SELECT * FROM users WHERE id=?", (aid,), one=True)
        assert verify_password(row['password_hash'], 'FirstPass12')
        set_admin_credentials(conn, 'admin@college.edu', 'SecondPass34')
        row = db_module.query("SELECT * FROM users WHERE id=?", (aid,), one=True)
        assert verify_password(row['password_hash'], 'SecondPass34')
        assert not verify_password(row['password_hash'], 'FirstPass12')


def test_admin_login_after_bootstrap(app_factory):
    app = app_factory(AUTO_IMPORT=True)
    client = app.test_client()
    with app.app_context():
        # bootstrap_if_empty runs on create_app when AUTO_IMPORT=True
        row = db_module.query("SELECT * FROM users WHERE role='admin'", one=True)
        assert row is not None, 'bootstrap should create admin when credentials set'
        assert verify_password(row['password_hash'], 'admin123')

    r = login(client, '/admin/login',
              {'email': 'admin@college.edu', 'password': 'admin123'})
    assert r.status_code == 302
    assert 'admin' in (r.headers.get('Location') or '')


def test_admin_login_wrong_password(app_factory):
    app = app_factory(AUTO_IMPORT=True)
    client = app.test_client()
    page = client.get('/admin/login')
    csrf = get_csrf(page.get_data(as_text=True))
    r = client.post('/admin/login', data={
        'email': 'admin@college.edu', 'password': 'wrong-password',
        '_csrf': csrf,
    }, follow_redirects=True)
    assert r.status_code == 200
    assert b'Invalid credentials' in r.data or b'invalid' in r.data.lower()


def test_admin_login_via_email_case(app_factory):
    app = app_factory(AUTO_IMPORT=True)
    client = app.test_client()
    r = login(client, '/admin/login',
              {'email': 'Admin@College.Edu', 'password': 'admin123'})
    assert r.status_code == 302


def test_hod_login_after_csv_import_full(app_factory):
    app = app_factory(AUTO_IMPORT=True)
    client = app.test_client()
    # Admin login
    assert login(client, '/admin/login',
                 {'email': 'admin@college.edu', 'password': 'admin123'}).status_code == 302

    with app.app_context():
        branch = db_module.query("SELECT code FROM branches LIMIT 1", one=True)
        if not branch:
            db_module.execute(
                "INSERT INTO branches(code, name) VALUES('CSE','Computer Science')")
            code = 'CSE'
        else:
            code = branch['code']

    emp = 'HODADMTEST1'
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['role', 'employee_id', 'full_name', 'official_email',
                'designation', 'department_codes', 'active'])
    w.writerow(['hod', emp, 'HOD Admin Test', 'hodadm@college.edu', 'HOD', code, '1'])
    data = buf.getvalue().encode('utf-8')

    page = client.get('/admin/accounts/hod-dean')
    csrf = get_csrf(page.get_data(as_text=True))
    r = client.post('/admin/accounts/hod-dean/import', data={
        '_csrf': csrf,
        'csv_file': (io.BytesIO(data), 'h.csv'),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    m = re.search(r'<code>([^<]{8,40})</code>', body)
    assert m, 'temp password must be shown once after import'
    temp_pw = m.group(1)

    with app.app_context():
        u = db_module.query(
            "SELECT * FROM users WHERE emp_code=? AND role='hod'", (emp,), one=True)
        assert u is not None
        assert u['username'] == emp
        assert verify_password(u['password_hash'], temp_pw)
        assert u['must_change_password'] == 1

    client.get('/logout')
    csrf2 = get_csrf(client.get('/hod/login').get_data(as_text=True))
    r2 = client.post('/hod/login', data={
        'emp_code': emp, 'password': temp_pw, '_csrf': csrf2,
    }, follow_redirects=False)
    assert r2.status_code == 302, f'HOD login failed: {r2.status_code} {r2.data[:200]!r}'

    r3 = client.get('/hod/', follow_redirects=False)
    assert 'change-password' in (r3.headers.get('Location') or '')


def test_dean_login_after_csv_import_full(app_factory):
    app = app_factory(AUTO_IMPORT=True)
    client = app.test_client()
    assert login(client, '/admin/login',
                 {'email': 'admin@college.edu', 'password': 'admin123'}).status_code == 302

    with app.app_context():
        branch = db_module.query("SELECT code FROM branches LIMIT 1", one=True)
        code = branch['code'] if branch else 'CSE'
        if not branch:
            db_module.execute(
                "INSERT INTO branches(code, name) VALUES('CSE','Computer Science')")

    emp = 'DEANADMTEST1'
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['role', 'employee_id', 'full_name', 'official_email',
                'designation', 'department_codes', 'active'])
    w.writerow(['dean', emp, 'Dean Admin Test', 'deanadm@college.edu', 'Dean', code, '1'])
    data = buf.getvalue().encode('utf-8')

    page = client.get('/admin/accounts/hod-dean')
    csrf = get_csrf(page.get_data(as_text=True))
    r = client.post('/admin/accounts/hod-dean/import', data={
        '_csrf': csrf,
        'csv_file': (io.BytesIO(data), 'd.csv'),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert r.status_code == 200
    m = re.search(r'<code>([^<]{8,40})</code>', r.get_data(as_text=True))
    assert m
    temp_pw = m.group(1)

    client.get('/logout')
    csrf2 = get_csrf(client.get('/dean/login').get_data(as_text=True))
    r2 = client.post('/dean/login', data={
        'emp_code': emp.lower(), 'password': temp_pw, '_csrf': csrf2,
    }, follow_redirects=False)
    assert r2.status_code == 302

    r3 = client.get('/dean/', follow_redirects=False)
    assert 'change-password' in (r3.headers.get('Location') or '')
