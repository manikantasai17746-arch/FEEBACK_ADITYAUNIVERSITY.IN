"""Google / Microsoft sign-in verifies the first-login e-mail without sending a code."""
import re
from urllib.parse import parse_qs, urlparse

import pytest

from feedback_system import db, mailer, sso
from helpers import get_csrf, login, login_student

GID = 'google-client-id'
MID = 'microsoft-client-id'
TENANT = '11111111-2222-3333-4444-555555555555'


@pytest.fixture()
def sso_env(app, monkeypatch):
    """Turn the first-login gate and both providers on; capture any outgoing mail."""
    sent = []
    monkeypatch.setattr(mailer, 'send_email',
                        lambda to, subject, text, html=None: sent.append(to) or True)
    monkeypatch.setenv('GOOGLE_CLIENT_ID', GID)
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'g-secret')
    monkeypatch.setenv('MICROSOFT_CLIENT_ID', MID)
    monkeypatch.setenv('MICROSOFT_CLIENT_SECRET', 'm-secret')
    monkeypatch.setenv('MICROSOFT_TENANT_ID', TENANT)
    monkeypatch.delenv('GOOGLE_HOSTED_DOMAIN', raising=False)
    app.config['REQUIRE_ACCOUNT_SETUP'] = True
    yield sent
    app.config['REQUIRE_ACCOUNT_SETUP'] = False
    with app.app_context():
        for table in ('student_backup_codes', 'student_authenticators'):
            db.execute(f"DELETE FROM {table} WHERE user_id IN (SELECT id FROM users "
                       f"WHERE role='student' AND email LIKE '%@' || ?)",
                       (app.config['STUDENT_EMAIL_DOMAIN'],))
        db.execute("UPDATE users SET email=NULL, email_verified=0, must_change_password=0, "
                   "first_login_complete=0 WHERE role IN ('student','teacher')")
        db.execute("DELETE FROM email_codes")


def _student(app, offset=0):
    with app.app_context():
        rows = db.query("SELECT roll FROM users WHERE role='student' ORDER BY id", )
        return rows[offset]['roll']


def _login_student(client, roll):
    assert login_student(client, roll).status_code == 302


def _start(client, provider):
    r = client.get(f'/sso/{provider}/start')
    assert r.status_code == 302
    return r.headers['Location']


def _session_nonce(client):
    with client.session_transaction() as s:
        return s['sso']['nonce']


def _state_from(location):
    return parse_qs(urlparse(location).query)['state'][0]


def _stored_email(app, roll):
    with app.app_context():
        return db.query("SELECT email, email_verified FROM users WHERE roll=?", (roll,), one=True)


# ---------------------------------------------------------------- provider checks
def _claims(**kw):
    base = {'iss': 'https://accounts.google.com', 'aud': GID, 'nonce': 'n1',
            'email': 'Stu@Gmail.com', 'email_verified': True}
    base.update(kw)
    return base


def test_google_verified_email_is_returned_lowercased(sso_env):
    assert sso.verified_email('google', _claims(), 'n1', GID) == 'stu@gmail.com'


def test_google_unverified_email_is_refused(sso_env):
    with pytest.raises(sso.SSOError, match='not verified'):
        sso.verified_email('google', _claims(email_verified=False), 'n1', GID)


def test_nonce_or_audience_mismatch_is_refused(sso_env):
    with pytest.raises(sso.SSOError, match='expired'):
        sso.verified_email('google', _claims(), 'other-nonce', GID)
    with pytest.raises(sso.SSOError, match='different application'):
        sso.verified_email('google', _claims(aud='someone-else'), 'n1', GID)


def test_google_domain_restriction(sso_env, monkeypatch):
    monkeypatch.setenv('GOOGLE_HOSTED_DOMAIN', 'aditya.ac.in')
    with pytest.raises(sso.SSOError, match='aditya.ac.in'):
        sso.verified_email('google', _claims(hd='gmail.com'), 'n1', GID)
    ok = _claims(hd='aditya.ac.in', email='a@aditya.ac.in')
    assert sso.verified_email('google', ok, 'n1', GID) == 'a@aditya.ac.in'


def test_microsoft_tenant_restriction_and_upn_fallback(sso_env):
    ms = {'iss': 'x', 'aud': MID, 'nonce': 'n2', 'tid': TENANT,
          'preferred_username': 'student@aditya.ac.in'}
    assert sso.verified_email('microsoft', ms, 'n2', MID) == 'student@aditya.ac.in'
    with pytest.raises(sso.SSOError, match='college Microsoft'):
        sso.verified_email('microsoft', {**ms, 'tid': '99999999-9999-9999-9999-999999999999'},
                           'n2', MID)


def test_provider_off_without_credentials(app, monkeypatch):
    monkeypatch.delenv('GOOGLE_CLIENT_ID', raising=False)
    monkeypatch.delenv('GOOGLE_CLIENT_SECRET', raising=False)
    assert not sso.enabled('google')


# ---------------------------------------------------------------- routes
def _college(app, roll):
    return f"{roll.lower()}@{app.config['STUDENT_EMAIL_DOMAIN']}"


def _ms_claims(nonce, email):
    return {'iss': 'x', 'aud': MID, 'nonce': nonce, 'tid': TENANT, 'email': email}


def _ms_signin(client, monkeypatch, email):
    """Start a Microsoft sign-in and finish it as if Microsoft returned ``email``."""
    loc = _start(client, 'microsoft')
    nonce = _session_nonce(client)
    monkeypatch.setattr(sso, 'fetch_claims', lambda p, code, uri: _ms_claims(nonce, email))
    return client.get(f'/sso/microsoft/callback?code=abc&state={_state_from(loc)}')


def test_student_setup_page_offers_microsoft_only(app, client, sso_env):
    roll = _student(app)
    _login_student(client, roll)
    html = client.get('/setup/email').get_data(as_text=True)
    assert 'Continue with Microsoft' in html
    assert 'Continue with Google' not in html
    assert 'Try another way' in html and _college(app, roll) in html     # the college e-mail box


def test_teacher_setup_page_still_offers_both_providers(app, client, teacher, sso_env):
    html = client.get('/setup/email').get_data(as_text=True)
    assert 'Continue with Google' in html and 'Continue with Microsoft' in html


def test_google_is_refused_for_students(app, client, sso_env):
    _login_student(client, _student(app))
    r = client.get('/sso/google/start')
    assert r.status_code == 302 and r.headers['Location'].endswith('/setup/email')


def test_microsoft_needs_the_college_tenant_for_students(app, client, sso_env, monkeypatch):
    monkeypatch.setenv('MICROSOFT_TENANT_ID', 'common')          # any Microsoft account: not safe
    _login_student(client, _student(app))
    html = client.get('/setup/email').get_data(as_text=True)
    assert 'Continue with Microsoft' not in html and 'Try another way' in html
    r = client.get('/sso/microsoft/start')
    assert r.status_code == 302 and r.headers['Location'].endswith('/setup/email')


def test_microsoft_start_redirects_with_state_and_tenant(app, client, sso_env):
    _login_student(client, _student(app))
    loc = _start(client, 'microsoft')
    assert loc.startswith(f'https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?')
    assert f'client_id={MID}' in loc and _state_from(loc)


def test_google_start_redirects_to_google_with_state_for_a_teacher(app, client, teacher, sso_env):
    loc = _start(client, 'google')
    assert loc.startswith('https://accounts.google.com/o/oauth2/v2/auth?')
    assert 'client_id=google-client-id' in loc and _state_from(loc)


def test_teacher_google_sign_in_saves_verified_email_without_mail(
        app, client, teacher, sso_env, monkeypatch):
    loc = _start(client, 'google')
    nonce = _session_nonce(client)
    monkeypatch.setattr(sso, 'fetch_claims',
                        lambda p, code, uri: _claims(nonce=nonce, email='Prof@Gmail.com'))
    r = client.get(f'/sso/google/callback?code=abc&state={_state_from(loc)}')
    assert r.status_code == 302 and 'setup' not in r.headers['Location']
    with app.app_context():
        row = db.query("SELECT email, email_verified FROM users WHERE emp_code='6817'", one=True)
    assert row['email'] == 'prof@gmail.com' and row['email_verified'] == 1
    assert sso_env == []                       # no code e-mail was sent


def test_student_microsoft_sign_in_with_matching_roll_saves_email_and_shows_the_qr(
        app, client, sso_env, monkeypatch):
    roll = _student(app)
    _login_student(client, roll)
    r = _ms_signin(client, monkeypatch, _college(app, roll).upper())      # letter case is ignored
    assert r.status_code == 302 and 'setup' not in r.headers['Location']
    row = _stored_email(app, roll)
    assert row['email'] == _college(app, roll) and row['email_verified'] == 1
    assert sso_env == []                       # no code e-mail was sent
    # the next step is the authenticator, and its QR code is shown straight away
    page = client.get('/setup/authenticator').get_data(as_text=True)
    assert 'setup-key' in page and 'otpauth://totp/' in page


def test_student_microsoft_account_must_match_the_roll_number(app, client, sso_env, monkeypatch):
    roll, other = _student(app, 5), _student(app, 6)
    _login_student(client, roll)
    for bad in (_college(app, other), 'someone@gmail.com', f"{roll.lower()}@other-college.edu"):
        r = _ms_signin(client, monkeypatch, bad)
        assert r.status_code == 302 and r.headers['Location'].endswith('/setup/email'), bad
        assert _stored_email(app, roll)['email'] is None, bad
    assert sso_env == []
    # a refused sign-in does not unlock the QR code either
    assert 'setup-key' not in client.get('/setup/authenticator', follow_redirects=True
                                         ).get_data(as_text=True)


def test_returning_student_can_use_microsoft_instead_of_an_e_mailed_code(
        app, client, sso_env, monkeypatch):
    roll = _student(app, 7)
    with app.app_context():
        db.execute("UPDATE users SET email=?, email_verified=1 WHERE roll=?",
                   (_college(app, roll), roll))
    _login_student(client, roll)
    with client.session_transaction() as sess:           # password re-entry already done
        sess['auth_pw_until'] = int(__import__('time').time()) + 600
    html = client.get('/setup/authenticator').get_data(as_text=True)
    assert 'Continue with Microsoft' in html and 'setup-key' not in html
    _ms_signin(client, monkeypatch, _college(app, roll))
    page = client.get('/setup/authenticator').get_data(as_text=True)
    assert 'setup-key' in page and sso_env == []


def test_wrong_state_is_rejected_and_nothing_saved(app, client, sso_env, monkeypatch):
    roll = _student(app, 1)
    _login_student(client, roll)
    _start(client, 'google')
    monkeypatch.setattr(sso, 'fetch_claims', lambda *a: pytest.fail('must not exchange code'))
    r = client.get('/sso/google/callback?code=abc&state=forged')
    assert r.status_code == 302
    assert _stored_email(app, roll)['email'] is None


def test_email_already_used_by_another_account_is_refused(app, client, sso_env, monkeypatch):
    roll_a, roll_b = _student(app, 2), _student(app, 3)
    with app.app_context():
        db.execute("UPDATE users SET email=?, email_verified=1 WHERE roll=?",
                   (_college(app, roll_b), roll_a))
    _login_student(client, roll_b)
    _ms_signin(client, monkeypatch, _college(app, roll_b))
    assert _stored_email(app, roll_b)['email'] is None


def test_different_email_cannot_replace_one_on_file(app, client, sso_env, monkeypatch):
    roll = _student(app, 4)
    with app.app_context():
        db.execute("UPDATE users SET email='first@gmail.com', email_verified=1 WHERE roll=?", (roll,))
    _login_student(client, roll)
    _ms_signin(client, monkeypatch, _college(app, roll))
    assert _stored_email(app, roll)['email'] == 'first@gmail.com'


def test_disabled_provider_falls_back_to_code_page(app, client, sso_env, monkeypatch):
    monkeypatch.delenv('MICROSOFT_CLIENT_ID', raising=False)
    _login_student(client, _student(app))
    r = client.get('/sso/microsoft/start')
    assert r.status_code == 302 and r.headers['Location'].endswith('/setup/email')
