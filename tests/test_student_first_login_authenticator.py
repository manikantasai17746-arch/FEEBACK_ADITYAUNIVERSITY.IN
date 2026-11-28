"""A student's first sign-in must always lead to authenticator setup.

The deployment variables AUTHENTICATOR_ENFORCE_ROLES / AUTHENTICATOR_GRACE_UNTIL are meant
for rolling the requirement out to staff. A blank value, a list without ``student`` (e.g.
``teacher,hod,dean``) or a student grace date used to switch the student requirement off,
so a new student was never asked to set up the authenticator app.
"""
import os
import tempfile
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest

from feedback_system import authenticator as auth
from feedback_system import create_app, db, sso
from feedback_system.security import hash_password

TENANT = '11111111-2222-3333-4444-555555555555'
ROLL = 'ZZFL26CS001'


@pytest.mark.parametrize('raw, expected', [
    (None, 'student'),
    ('', 'student'),
    ('   ', 'student'),
    ('student', 'student'),
    ('teacher,hod,dean', 'student,teacher,hod,dean'),
    (' Teacher , STUDENT ,hod', 'student,teacher,hod'),
])
def test_env_roles_always_include_student(raw, expected):
    assert auth.enforce_roles_from_env(raw) == expected


def test_env_grace_never_delays_students():
    raw = 'student=2099-01-01,teacher=2026-12-31, hod=2026-12-31'
    assert auth.grace_from_env(raw) == 'teacher=2026-12-31,hod=2026-12-31'
    assert auth.grace_from_env('student=2099-01-01') == ''
    assert auth.grace_from_env(None) == ''


@pytest.mark.parametrize('roles, grace', [
    ('', ''),
    ('teacher,hod,dean', ''),
    ('student,teacher', 'student=2099-01-01'),
])
def test_new_student_is_sent_to_authenticator_setup(monkeypatch, roles, grace):
    monkeypatch.setenv('MICROSOFT_CLIENT_ID', 'mid')
    monkeypatch.setenv('MICROSOFT_CLIENT_SECRET', 'secret')
    monkeypatch.setenv('MICROSOFT_TENANT_ID', TENANT)
    monkeypatch.setenv('AUTHENTICATOR_ENFORCE_ROLES', roles)
    monkeypatch.setenv('AUTHENTICATOR_GRACE_UNTIL', grace)
    app = create_app({
        'TESTING': True, 'DATABASE': os.path.join(tempfile.mkdtemp(), 't.db'),
        'DATABASE_URL': None, 'SECRET_KEY': 'test-secret-key', 'AUTO_IMPORT': False,
        'ADMIN_EMAIL': 'admin@college.edu', 'ADMIN_PASSWORD': 'admin123',
        'REQUIRE_ACCOUNT_SETUP': True,
        'TOTP_ENCRYPTION_KEY': 'slFqyvDkoDYXvVZAqiTyPsKcY1ekJ684A0nVc5i4JME=',
    })
    with app.app_context():
        db.execute("INSERT INTO users(role, username, name, roll, password_hash, is_active) "
                   "VALUES('student', ?, 'First Login', ?, ?, 1)",
                   (ROLL, ROLL, hash_password('unused-pass-1')))
    client = app.test_client()
    state = parse_qs(urlparse(client.get('/student/sso/start').headers['Location']).query)['state'][0]
    with client.session_transaction() as s:
        nonce = s['student_sso']['nonce']
    claims = {'iss': 'x', 'aud': 'mid', 'nonce': nonce, 'tid': TENANT,
              'email': f'{ROLL.lower()}@adityauniversity.in'}
    with mock.patch.object(sso, 'fetch_claims', return_value=claims):
        r = client.get('/student/sso/callback', query_string={'state': state, 'code': 'abc'})
    assert r.status_code == 302
    r = client.get(r.headers['Location'])          # the student's landing page
    assert r.status_code == 302 and r.headers['Location'].endswith('/setup/authenticator')
    page = client.get(r.headers['Location'])
    assert page.status_code == 200 and b'6-digit code' in page.data
