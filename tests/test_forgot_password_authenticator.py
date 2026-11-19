"""Forgot password: authenticator code or backup code first (no email when enrolled).

* Account with an ACTIVE authenticator: app code or unused backup code, no email sent, the
  authenticator and the other backup codes are untouched.
* Account without one: the emailed 6-digit code, exactly as before.
* Student, Teacher, HOD and Dean. Admin keeps its separate two-email recovery.
"""
import time

import pyotp
import pytest

from feedback_system import authenticator as auth
from feedback_system import db
from feedback_system.security import hash_password

from test_authenticator_enroll_cp2 import _post, acct, sent  # noqa: F401
from test_authenticator_lost_device_cp3 import _enrolled, _state

def _next(secret):
    """Next 30-second code: enrolment already spent the current one (replay protection)."""
    return pyotp.TOTP(secret).at(time.time() + 30)


NEW_PW = 'Brand-new-pass-9'
BAD = 'That code is not correct or has expired.'
ROLES4 = ['student', 'teacher', 'hod', 'dean']


@pytest.fixture(autouse=True)
def _clean(app):
    with app.app_context():
        db.execute('DELETE FROM login_attempts')
    yield


def _restore_password(app, a):
    with app.app_context():
        db.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(a['pw']), a['uid']))


def _start(app, a):
    anon = app.test_client()
    r = _post(anon, '/forgot-password', role=a['role'], ident=a['ident'])
    assert r.status_code == 302 and '/forgot-password/reset' in r.headers['Location']
    return anon


def _reset(anon, code, pw=NEW_PW):
    return _post(anon, '/forgot-password/reset', code=code, new_password=pw, confirm_password=pw)


def _pw_is(app, a, pw):
    from feedback_system.security import verify_password
    with app.app_context():
        row = db.query('SELECT password_hash FROM users WHERE id=?', (a['uid'],), one=True)
        return verify_password(row['password_hash'], pw)


@pytest.mark.parametrize('acct', ROLES4, indirect=True)
def test_app_code_resets_password_without_sending_email(app, client, acct, sent):
    secret, _ = _enrolled(client, app, acct, sent)
    sent.clear()
    anon = _start(app, acct)
    assert sent == []                                   # no mail cost
    r = _reset(anon, _next(secret))
    assert r.status_code == 302
    row, remaining, _ = _state(app, acct['uid'])
    assert row['status'] == 'active' and remaining == 10   # authenticator and codes untouched
    assert _pw_is(app, acct, NEW_PW)
    _restore_password(app, acct)


@pytest.mark.parametrize('acct', ['student', 'teacher'], indirect=True)
def test_backup_code_resets_password_and_is_spent(app, client, acct, sent):
    _, codes = _enrolled(client, app, acct, sent)
    sent.clear()
    anon = _start(app, acct)
    assert _reset(anon, codes[0]).status_code == 302
    row, remaining, _ = _state(app, acct['uid'])
    assert row['status'] == 'active' and remaining == 9
    assert sent == []
    anon2 = _start(app, acct)                           # the same code never works twice
    r = _reset(anon2, codes[0], 'Another-pass-77')
    assert r.status_code == 200 and BAD.encode() in r.data
    assert _pw_is(app, acct, NEW_PW)
    _restore_password(app, acct)


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_wrong_code_changes_nothing_and_the_emailed_route_is_closed(app, client, acct, sent):
    secret, _ = _enrolled(client, app, acct, sent)
    sent.clear()
    anon = _start(app, acct)
    wrong = '000000' if _next(secret) != '000000' else '111111'
    r = _reset(anon, wrong)
    assert r.status_code == 200 and BAD.encode() in r.data
    assert _pw_is(app, acct, acct['pw'])
    # resend must not email an enrolled user either
    _post(anon, '/forgot-password/reset', action='resend')
    assert sent == []


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_a_used_app_code_cannot_be_replayed(app, client, acct, sent):
    secret, _ = _enrolled(client, app, acct, sent)
    code = _next(secret)
    anon = _start(app, acct)
    assert _reset(anon, code).status_code == 302
    anon2 = _start(app, acct)
    r = _reset(anon2, code, 'Another-pass-77')
    assert r.status_code == 200 and BAD.encode() in r.data
    _restore_password(app, acct)


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_five_wrong_codes_lock_out_even_the_right_code(app, client, acct, sent):
    secret, _ = _enrolled(client, app, acct, sent)
    anon = _start(app, acct)
    wrong = '000000' if _next(secret) != '000000' else '111111'
    for _i in range(5):
        _reset(anon, wrong)
    r = _reset(anon, _next(secret))
    assert r.status_code == 200 and BAD.encode() in r.data      # same message, not "locked"
    assert _pw_is(app, acct, acct['pw'])
    with app.app_context():
        auth.clear_reset_failures(acct['uid'])


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_sign_in_is_not_locked_by_reset_guessing(app, client, acct, sent):
    secret, _ = _enrolled(client, app, acct, sent)
    anon = _start(app, acct)
    for _i in range(6):
        _reset(anon, '000000')
    with app.app_context():
        assert auth.verify_second_factor(acct['uid'], _next(secret))[0] is True
        auth.clear_reset_failures(acct['uid'])


@pytest.mark.parametrize('acct', ['hod', 'dean'], indirect=True)
def test_hod_and_dean_without_authenticator_use_the_emailed_code(app, acct, sent):
    anon = _start(app, acct)
    assert sent and sent[-1]['to'] == f"{acct['role']}@test.edu" and sent[-1]['code']
    assert _reset(anon, sent[-1]['code']).status_code == 302
    assert _pw_is(app, acct, NEW_PW)
    _restore_password(app, acct)


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_page_does_not_reveal_whether_an_account_is_enrolled_or_exists(app, client, acct, sent):
    _enrolled(client, app, acct, sent)
    a = app.test_client()
    known = _post(a, '/forgot-password', role='student', ident=acct['ident'])
    b = app.test_client()
    unknown = _post(b, '/forgot-password', role='student', ident='NOPE-0000')
    assert known.status_code == unknown.status_code == 302
    assert known.headers['Location'] == unknown.headers['Location']
    page_a = a.get('/forgot-password/reset').get_data(as_text=True)
    page_b = b.get('/forgot-password/reset').get_data(as_text=True)
    assert 'authenticator app' in page_a and 'authenticator app' in page_b
    r = _reset(b, '123456')
    assert BAD.encode() in r.data


def test_hod_and_dean_login_pages_link_to_forgot_password(client):
    for path, role in (('/hod/login', 'hod'), ('/dean/login', 'dean')):
        assert f'/forgot-password?role={role}'.encode() in client.get(path).data
    page = client.get('/forgot-password').get_data(as_text=True)
    assert 'value="hod"' in page and 'value="dean"' in page
