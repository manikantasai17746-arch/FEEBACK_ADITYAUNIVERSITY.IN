"""Students sign in with their college Microsoft account only (no roll number / password)."""
from urllib.parse import parse_qs, urlparse
from unittest import mock

import pytest

from feedback_system import db, sso
from feedback_system.security import hash_password
from helpers import token_from

TENANT = '11111111-2222-3333-4444-555555555555'
MID = 'microsoft-client-id'
ROLL = 'ZZMS26CS001'
DOMAIN = 'adityauniversity.in'


@pytest.fixture()
def ms_env(app, monkeypatch):
    monkeypatch.setenv('MICROSOFT_CLIENT_ID', MID)
    monkeypatch.setenv('MICROSOFT_CLIENT_SECRET', 'm-secret')
    monkeypatch.setenv('MICROSOFT_TENANT_ID', TENANT)
    with app.app_context():
        if not db.query("SELECT id FROM users WHERE username=?", (ROLL,), one=True):
            db.execute("INSERT INTO users(role, username, name, roll, password_hash, is_active) "
                       "VALUES('student', ?, 'Test Student', ?, ?, 1)",
                       (ROLL, ROLL, hash_password('unused-pass-1')))
        db.execute("UPDATE users SET email=NULL, email_verified=0, is_active=1 WHERE username=?",
                   (ROLL,))
    yield


def _start(client):
    r = client.get('/student/sso/start')
    assert r.status_code == 302
    loc = r.headers['Location']
    assert loc.startswith(f'https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize')
    return parse_qs(urlparse(loc).query)


def _callback(client, email, state, *, extra=None):
    with client.session_transaction() as s:
        nonce = s['student_sso']['nonce']
    claims = {'iss': 'x', 'aud': MID, 'nonce': nonce, 'email': email, 'tid': TENANT}
    with mock.patch.object(sso, 'fetch_claims', return_value=claims):
        return client.get('/student/sso/callback',
                          query_string={'state': state, 'code': 'abc', **(extra or {})})


def test_student_login_page_offers_only_microsoft(client, ms_env):
    html = client.get('/student/login').get_data(as_text=True)
    assert 'Continue with Microsoft' in html
    assert 'name="roll"' not in html
    assert 'lp_stu_pw' not in html
    assert 'Scan ID Card' not in html


def test_posting_a_roll_and_password_no_longer_logs_in(client, ms_env):
    r = client.post('/student/login', data={'roll': ROLL, 'password': 'unused-pass-1',
                                        '_csrf': token_from(client, '/student/login')})
    assert r.status_code == 302 and r.headers['Location'].endswith('/student/login')
    assert client.get('/student/').status_code == 302
    assert '/student/login' in client.get('/student/').headers['Location']


def test_scan_login_is_retired(client, ms_env):
    r = client.post('/scan-login', data={'code': ROLL, '_csrf': token_from(client, '/student/login')})
    assert r.get_json()['ok'] is False


def test_start_sends_the_student_to_the_college_tenant(client, ms_env):
    q = _start(client)
    assert q['client_id'] == [MID]
    assert q['redirect_uri'][0].endswith('/student/sso/callback')


def test_start_refuses_when_the_tenant_is_not_configured(client, ms_env, monkeypatch):
    monkeypatch.delenv('MICROSOFT_TENANT_ID')
    r = client.get('/student/sso/start')
    assert r.status_code == 302 and r.headers['Location'].endswith('/student/login')


def test_college_email_signs_the_existing_student_in(app, client, ms_env):
    state = _start(client)['state'][0]
    r = _callback(client, f'{ROLL.lower()}@{DOMAIN}', state)
    assert r.status_code == 302 and r.headers['Location'].endswith('/student/')
    with client.session_transaction() as s:
        assert s['role'] == 'student'
    with app.app_context():
        row = db.query("SELECT email, email_verified FROM users WHERE username=?", (ROLL,), one=True)
    assert row['email'] == f'{ROLL.lower()}@{DOMAIN}' and row['email_verified'] == 1


def test_signing_in_again_after_logout_works(client, ms_env):
    state = _start(client)['state'][0]
    assert _callback(client, f'{ROLL.lower()}@{DOMAIN}', state).status_code == 302
    client.get('/logout')
    with client.session_transaction() as s:
        assert 'uid' not in s
    state = _start(client)['state'][0]
    assert _callback(client, f'{ROLL.lower()}@{DOMAIN}', state).status_code == 302
    with client.session_transaction() as s:
        assert s['role'] == 'student'


def test_an_unknown_roll_number_is_refused_and_no_account_is_created(app, client, ms_env):
    state = _start(client)['state'][0]
    r = _callback(client, f'nobody999@{DOMAIN}', state)
    assert r.headers['Location'].endswith('/student/login')
    with client.session_transaction() as s:
        assert 'uid' not in s
    with app.app_context():
        assert db.query("SELECT id FROM users WHERE UPPER(username)='NOBODY999'", one=True) is None


def test_an_inactive_student_is_refused(app, client, ms_env):
    with app.app_context():
        db.execute("UPDATE users SET is_active=0 WHERE username=?", (ROLL,))
    state = _start(client)['state'][0]
    r = _callback(client, f'{ROLL.lower()}@{DOMAIN}', state)
    assert r.headers['Location'].endswith('/student/login')
    with client.session_transaction() as s:
        assert 'uid' not in s


def test_a_non_college_email_is_refused(client, ms_env):
    state = _start(client)['state'][0]
    r = _callback(client, f'{ROLL.lower()}@gmail.com', state)
    assert r.headers['Location'].endswith('/student/login')
    with client.session_transaction() as s:
        assert 'uid' not in s


def test_a_wrong_state_is_refused(client, ms_env):
    _start(client)
    r = _callback(client, f'{ROLL.lower()}@{DOMAIN}', 'forged-state')
    assert r.headers['Location'].endswith('/student/login')
    with client.session_transaction() as s:
        assert 'uid' not in s


def test_a_token_from_another_tenant_is_refused(client, ms_env):
    state = _start(client)['state'][0]
    with client.session_transaction() as s:
        nonce = s['student_sso']['nonce']
    claims = {'aud': MID, 'nonce': nonce, 'email': f'{ROLL.lower()}@{DOMAIN}',
              'tid': '99999999-2222-3333-4444-555555555555'}
    with mock.patch.object(sso, 'fetch_claims', return_value=claims):
        r = client.get('/student/sso/callback', query_string={'state': state, 'code': 'abc'})
    assert r.headers['Location'].endswith('/student/login')
    with client.session_transaction() as s:
        assert 'uid' not in s


def test_the_callback_without_a_started_sign_in_is_refused(client, ms_env):
    r = client.get('/student/sso/callback', query_string={'state': 'x', 'code': 'abc'})
    assert r.headers['Location'].endswith('/student/login')


def test_email_already_on_another_account_is_refused(app, client, ms_env):
    with app.app_context():
        admin = db.query("SELECT id FROM users WHERE role='admin' LIMIT 1", one=True)
        db.execute("UPDATE users SET email=? WHERE id=?", (f'{ROLL.lower()}@{DOMAIN}', admin['id']))
    try:
        state = _start(client)['state'][0]
        r = _callback(client, f'{ROLL.lower()}@{DOMAIN}', state)
        assert r.headers['Location'].endswith('/student/login')
        with client.session_transaction() as s:
            assert 'uid' not in s
    finally:
        with app.app_context():
            db.execute("UPDATE users SET email=NULL WHERE id=?", (admin['id'],))
