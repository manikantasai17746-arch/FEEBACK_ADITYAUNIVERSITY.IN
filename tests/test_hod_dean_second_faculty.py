"""Tests for HOD/Dean portals, role expansion, second-faculty eligibility, and isolation."""
import os
import tempfile

import pytest

from feedback_system import create_app
from feedback_system import db as db_module
from feedback_system.security import hash_password, user_branch_ids, user_has_branch
from helpers import login


@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-hod-')
    application = create_app({
        'TESTING': True,
        'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None,
        'SECRET_KEY': 'test-secret-key-hod-dean',
        'AUTO_IMPORT': True,
        'ADMIN_EMAIL': 'admin@college.edu',
        'ADMIN_PASSWORD': 'admin123',
        'DEFAULT_TEACHER_PASSWORD': 'teacher@123',
        'DEFAULT_STUDENT_PASSWORD': 'student@123',
        'LOGIN_MAX_ATTEMPTS': 50,
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


def _seed_hod_dean(app):
    """Create one HOD and one Dean with explicit CSE branch scope (if CSE exists)."""
    with app.app_context():
        branch = db_module.query("SELECT id, code FROM branches WHERE code='CSE'", one=True)
        if not branch:
            # Ensure a branch exists for scope tests
            db_module.execute("INSERT INTO branches(code, name) VALUES('CSE','Computer Science')")
            branch = db_module.query("SELECT id, code FROM branches WHERE code='CSE'", one=True)
        # HOD
        existing = db_module.query(
            "SELECT id FROM users WHERE username='HODCSE1' AND role='hod'", one=True)
        if not existing:
            db_module.execute(
                """INSERT INTO users(role, username, emp_code, name, email, password_hash, is_active)
                   VALUES('hod','HODCSE1','HODCSE1','Test HOD','hod@test.edu',?,1)""",
                (hash_password('hodpass123'),))
            hod = db_module.query("SELECT id FROM users WHERE username='HODCSE1'", one=True)
            db_module.execute(
                "INSERT INTO user_department_scopes(user_id, branch_id, is_primary) VALUES(?,?,1)",
                (hod['id'], branch['id']))
        # Dean
        existing = db_module.query(
            "SELECT id FROM users WHERE username='DEAN1' AND role='dean'", one=True)
        if not existing:
            db_module.execute(
                """INSERT INTO users(role, username, emp_code, name, email, password_hash, is_active)
                   VALUES('dean','DEAN1','DEAN1','Test Dean','dean@test.edu',?,1)""",
                (hash_password('deanpass123'),))
            dean = db_module.query("SELECT id FROM users WHERE username='DEAN1'", one=True)
            db_module.execute(
                "INSERT INTO user_department_scopes(user_id, branch_id, is_primary) VALUES(?,?,1)",
                (dean['id'], branch['id']))
        return branch['id']


def test_schema_v8_tables_exist(app):
    with app.app_context():
        ver = db_module.current_version(db_module.get_db())
        assert ver >= 8
        tables = [r[0] for r in db_module.query(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for t in ('user_department_scopes', 'second_faculty_assignments',
                  'second_faculty_import_batches', 'feedback_requirements'):
            assert t in tables
        cols = [r[1] for r in db_module.query('PRAGMA table_info(feedback_forms)')]
        assert 'category' in cols


def test_role_check_allows_hod_dean(app):
    with app.app_context():
        ddl = db_module.query(
            "SELECT sql FROM sqlite_master WHERE name='users'", one=True)[0]
        assert "'hod'" in ddl
        assert "'dean'" in ddl


def test_hod_login_and_dashboard(app, client):
    _seed_hod_dean(app)
    r = login(client, '/hod/login', {'emp_code': 'HODCSE1', 'password': 'hodpass123'})
    assert r.status_code == 302
    r = client.get('/hod/')
    assert r.status_code == 200
    assert b'HOD Dashboard' in r.data or b'Dashboard' in r.data


def test_dean_login_and_dashboard(app, client):
    _seed_hod_dean(app)
    r = login(client, '/dean/login', {'emp_code': 'DEAN1', 'password': 'deanpass123'})
    assert r.status_code == 302
    r = client.get('/dean/')
    assert r.status_code == 200
    # Requirements not configured message expected
    assert b'Requirements not configured' in r.data or b'Dean' in r.data


def test_hod_cannot_access_admin(app, client):
    _seed_hod_dean(app)
    login(client, '/hod/login', {'emp_code': 'HODCSE1', 'password': 'hodpass123'})
    r = client.get('/admin/')
    assert r.status_code in (302, 403)


def test_student_cannot_access_hod(app, client):
    r = client.get('/hod/')
    assert r.status_code in (302, 403)


def test_inactive_hod_cannot_login(app, client):
    _seed_hod_dean(app)
    with app.app_context():
        db_module.execute("UPDATE users SET is_active=0 WHERE username='HODCSE1'")
    r = login(client, '/hod/login', {'emp_code': 'HODCSE1', 'password': 'hodpass123'})
    # Should not redirect to dashboard
    assert r.status_code == 200 or (r.status_code == 302 and 'hod' not in (r.headers.get('Location') or ''))
    with app.app_context():
        db_module.execute("UPDATE users SET is_active=1 WHERE username='HODCSE1'")


def test_unresolved_mapping_does_not_grant_eligibility(app):
    with app.app_context():
        from feedback_system import services
        # No confirmed assignment → helper returns None
        assert services.confirmed_second_faculty_for_session(999999) is None
        # Unresolved row must not make student eligible
        branch = db_module.query('SELECT id FROM branches LIMIT 1', one=True)
        if branch:
            db_module.execute(
                """INSERT INTO second_faculty_assignments
                   (branch_id, subject_code, status, second_emp_code)
                   VALUES (?, 'TESTSUB', 'unresolved', NULL)""",
                (branch['id'],))
        assert services.student_eligible_for_second_faculty_form(1, 999999) is False


def test_department_scope_isolation(app):
    branch_id = _seed_hod_dean(app)
    with app.app_context():
        hod = db_module.query("SELECT id FROM users WHERE username='HODCSE1'", one=True)
        assert user_has_branch(hod['id'], branch_id)
        other = db_module.query(
            "SELECT id FROM branches WHERE id != ?", (branch_id,), one=True)
        if other:
            assert not user_has_branch(hod['id'], other['id'])
        scopes = user_branch_ids(hod['id'])
        assert branch_id in scopes


def test_admin_second_faculty_pages(admin):
    r = admin.get('/admin/second-faculty')
    assert r.status_code == 200
    r = admin.get('/admin/accounts/hod-dean')
    assert r.status_code == 200
    r = admin.get('/admin/accounts/hod-dean/template.csv')
    assert r.status_code == 200
    assert b'employee_id' in r.data
    r = admin.get('/admin/requirements')
    assert r.status_code == 200


def test_existing_main_faculty_workflow_intact(app, admin):
    """Smoke: admin dashboard and form list still work after upgrade."""
    r = admin.get('/admin/')
    assert r.status_code == 200
    r = admin.get('/admin/forms')
    assert r.status_code == 200
