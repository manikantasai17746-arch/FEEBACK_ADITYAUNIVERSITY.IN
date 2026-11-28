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


# ------------------------------------------------------- authenticator setup after sign-in
def _sign_in(client):
    state = _start(client)['state'][0]
    r = _callback(client, f'{ROLL.lower()}@{DOMAIN}', state)
    assert r.status_code == 302
    return r


@pytest.fixture()
def no_authenticator(app, ms_env):
    with app.app_context():
        uid = db.query("SELECT id FROM users WHERE username=?", (ROLL,), one=True)['id']
        for table in ('student_backup_codes', 'student_authenticators'):
            db.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
    yield uid


def test_setup_page_has_no_password_or_email_code_step(client, no_authenticator):
    _sign_in(client)
    html = client.get('/setup/authenticator').get_data(as_text=True)
    assert 'Current password' not in html
    assert 'Email me a code' not in html
    assert 'Setup key' in html and 'Finish setup' in html


def test_a_student_can_finish_authenticator_setup_with_only_the_microsoft_sign_in(
        app, client, no_authenticator):
    import pyotp
    from feedback_system import authenticator as auth
    _sign_in(client)
    client.get('/setup/authenticator')
    with app.app_context():
        secret = auth.pending_secret(no_authenticator)
    code = pyotp.TOTP(secret, interval=auth.TOTP_STEP_SECONDS).now()
    with client.session_transaction() as s:
        tok = s['token']
    r = client.post('/setup/authenticator/confirm', data={'code': code, '_csrf': tok})
    assert r.status_code == 200
    with app.app_context():
        assert auth.is_active(no_authenticator)


def test_an_expired_proof_asks_for_microsoft_again_never_a_password(client, no_authenticator):
    _sign_in(client)
    with client.session_transaction() as s:
        s['auth_pw_until'] = 0
        s['auth_setup_until'] = 0
    r = client.get('/setup/authenticator')
    assert r.status_code == 302
    assert '/student/sso/start' in r.headers['Location'] and 'reauth=1' in r.headers['Location']


def test_reauth_start_works_while_already_signed_in(client, ms_env):
    _sign_in(client)
    r = client.get('/student/sso/start', query_string={'reauth': 1,
                                                       'next': '/setup/authenticator'})
    assert r.status_code == 302 and 'login.microsoftonline.com' in r.headers['Location']
    state = parse_qs(urlparse(r.headers['Location']).query)['state'][0]
    r = _callback(client, f'{ROLL.lower()}@{DOMAIN}', state)
    assert r.headers['Location'].endswith('/setup/authenticator')


# ------------------------------------- with the first-login gate ON (as in production)
@pytest.fixture()
def gate_on(app, no_authenticator):
    app.config['REQUIRE_ACCOUNT_SETUP'] = True
    yield
    app.config['REQUIRE_ACCOUNT_SETUP'] = False


def test_first_login_goes_straight_to_the_authenticator_qr_page(client, gate_on):
    _sign_in(client)
    r = client.get('/student/')
    assert r.status_code == 302 and r.headers['Location'].endswith('/setup/authenticator')
    html = client.get('/setup/authenticator').get_data(as_text=True)
    assert 'Setup key' in html and 'Current password' not in html


def test_an_old_session_without_proof_signs_in_again_instead_of_looping(client, gate_on):
    _sign_in(client)
    with client.session_transaction() as s:
        s.pop('auth_pw_until', None)
        s.pop('auth_setup_until', None)
    r = client.get('/setup/authenticator')
    assert '/student/sso/start' in r.headers['Location']
    r = client.get(r.headers['Location'])               # must NOT bounce back to the setup page
    assert 'login.microsoftonline.com' in r.headers['Location']
    state = parse_qs(urlparse(r.headers['Location']).query)['state'][0]
    r = _callback(client, f'{ROLL.lower()}@{DOMAIN}', state)    # while still logged in
    assert r.headers['Location'].endswith('/setup/authenticator')
    assert 'Setup key' in client.get('/setup/authenticator').get_data(as_text=True)
