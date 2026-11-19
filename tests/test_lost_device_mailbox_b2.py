"""Checkpoint B2: lost-device recovery with NO phone and NO backup codes (e-mailed code).

Student/Teacher/HOD/Dean. Admin is excluded and unchanged. Also: the existing backup-code
page (/lost-phone) now honours the 2-hour recovery lock.
"""
import time

import pytest

from feedback_system import authenticator as auth
from feedback_system import db, recovery_lock as rl, security
from feedback_system.security import hash_password, verify_password
from helpers import login

from test_authenticator_enroll_cp2 import (LOGIN, PW, _codes_in, _enroll, _post,  # noqa: F401
                                           _sign_in, acct, sent)
from test_authenticator_lost_device_cp3 import _enrolled, _recover, _state

ROLES4 = ['student', 'teacher', 'hod', 'dean']
NEW_PW = 'Brand-new-pass-9'
CODE_FAIL = 'That code is not correct or has expired.'
GENERIC = 'We could not verify those details'


@pytest.fixture(autouse=True)
def _clean(app):
    def wipe():
        with app.app_context():
            db.execute('DELETE FROM login_attempts')
            db.execute("DELETE FROM audit_log WHERE action LIKE '%lost_device%' "
                       "OR action LIKE 'user.recovery_%'")
    wipe()
    yield
    wipe()
    with app.app_context():       # whatever a test changed, the shared HOD/Dean fixtures keep PW
        for ident in ('CP2HOD', 'CP2DEAN'):
            db.execute('UPDATE users SET password_hash=?, must_change_password=0 WHERE username=?',
                       (hash_password(PW), ident))


@pytest.fixture()
def clock(monkeypatch):
    class Clock:
        offset = 0

        def advance(self, seconds):
            self.offset += seconds
    c = Clock()
    real = time.time
    monkeypatch.setattr(security, '_now', lambda: real() + c.offset)
    return c


def _restore_pw(app, a):
    with app.app_context():
        db.execute('UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?',
                   (hash_password(a['pw']), a['uid']))


def _start(app, a, ident=None, role=None, client=None):
    c = client or app.test_client()
    r = _post(c, '/lost-phone/no-codes', role=role or a['role'], ident=ident or a['ident'])
    return c, r


def _finish(c, code, pw=NEW_PW, confirm=None):
    return _post(c, '/lost-phone/no-codes/verify', code=code, new_password=pw,
                 confirm_password=pw if confirm is None else confirm)


def _pw_ok(app, a, pw):
    with app.app_context():
        row = db.query('SELECT password_hash FROM users WHERE id=?', (a['uid'],), one=True)
        return verify_password(row['password_hash'], pw)


def _audit(app, uid, like):
    with app.app_context():
        return [r['action'] for r in db.query(
            "SELECT action FROM audit_log WHERE entity_id=? AND action LIKE ? ORDER BY id",
            (uid, like))]


def _wrong(code):
    return '000000' if code != '000000' else '111111'


# =========================================================================== #
# The whole journey, for each of the four roles
# =========================================================================== #
@pytest.mark.parametrize('acct', ROLES4, indirect=True)
def test_full_recovery_then_new_setup_with_confirmation_code(app, client, acct, sent):
    _enrolled(client, app, acct, sent)
    client.get('/logout')
    sent.clear()
    c, r = _start(app, acct)
    assert r.status_code == 302 and '/lost-phone/no-codes/verify' in r.headers['Location']
    assert len(sent) == 1 and sent[0]['to'] == f"{acct['role']}@test.edu" and sent[0]['code']
    r = _finish(c, sent[0]['code'])
    assert r.status_code == 302 and 'login' in r.headers['Location']

    row, remaining, total = _state(app, acct['uid'])
    assert row['status'] == 'pending' and row['enrolled_at'] and total == 0   # old one is gone
    with app.app_context():
        assert auth.needs_reenrollment(acct['uid'])
    assert _pw_ok(app, acct, NEW_PW) and not _pw_ok(app, acct, acct['pw'])
    assert any('removed using an emailed code' in m['text'] or 'removed' in m['text'].lower()
               for m in sent[1:])                                  # the notice e-mail
    assert 'user.lost_device_recovery' in _audit(app, acct['uid'], 'user.lost_device_recovery')

    # next sign-in: forced into setup (as in production), and the new app must be CONFIRMED
    acct2 = {**acct, 'pw': NEW_PW}
    c2 = app.test_client()
    saved = {k: app.config.get(k) for k in ('REQUIRE_ACCOUNT_SETUP', 'AUTHENTICATOR_ENFORCE_ROLES')}
    app.config['REQUIRE_ACCOUNT_SETUP'] = True
    app.config['AUTHENTICATOR_ENFORCE_ROLES'] = ''
    try:
        _sign_in(c2, acct2)
        path = {'student': '/student/', 'teacher': '/teacher/', 'hod': '/hod/',
                'dean': '/dean/'}[acct['role']]
        r = c2.get(path)
        assert r.status_code == 302 and '/setup/authenticator' in r.headers['Location']
        assert 'Not now' not in c2.get('/setup/authenticator').get_data(as_text=True)
        app.config['REQUIRE_ACCOUNT_SETUP'] = False
        secret, resp = _enroll(c2, app, acct2, sent)
    finally:
        app.config.update(saved)
    codes = _codes_in(resp.get_data(as_text=True))
    assert len(codes) == 10                                        # new backup codes
    row, remaining, _ = _state(app, acct['uid'])
    assert row['status'] == 'active' and remaining == 10
    _restore_pw(app, acct)


# =========================================================================== #
# Wrong codes, password rules, the lock
# =========================================================================== #
@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_wrong_code_changes_nothing(app, client, acct, sent):
    _enrolled(client, app, acct, sent)
    sent.clear()
    c, _ = _start(app, acct)
    r = _finish(c, _wrong(sent[0]['code']))
    assert r.status_code == 200 and CODE_FAIL.encode() in r.data
    row, remaining, _ = _state(app, acct['uid'])
    assert row['status'] == 'active' and remaining == 10
    assert _pw_ok(app, acct, acct['pw'])


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_password_rule_failures_do_not_use_up_the_code_or_count_as_wrong(app, client, acct, sent):
    _enrolled(client, app, acct, sent)
    sent.clear()
    c, _ = _start(app, acct)
    code = sent[0]['code']
    assert b'at least 8' in _finish(c, code, pw='short').data
    assert b'do not match' in _finish(c, code, pw=NEW_PW, confirm='Different-pass-1').data
    assert b'not the default' in _finish(c, code, pw='teacher@123').data
    with app.app_context():
        assert rl.locked_seconds({'id': acct['uid'], 'role': 'teacher'}) == 0
        row = db.query('SELECT fail_count FROM login_attempts WHERE attempt_key=?',
                       (security._db_key(f"reclock:{acct['uid']}"),), one=True)
        assert row is None or int(row['fail_count']) == 0     # no wrong code was counted
    assert _finish(c, code).status_code == 302             # the same code still works
    _restore_pw(app, acct)


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_five_wrong_codes_lock_recovery_for_two_hours_but_not_login(app, client, acct, sent, clock):
    _enrolled(client, app, acct, sent)
    client.get('/logout')
    sent.clear()
    c, _ = _start(app, acct)
    real = sent[0]['code']
    wrong = _wrong(real)
    for _i in range(5):
        _finish(c, wrong)
    acts = _audit(app, acct['uid'], 'user.recovery_%')
    assert acts == ['user.recovery_locked']
    # even the right code is now refused with the same message; nothing is removed
    r = _finish(c, real)
    assert r.status_code == 200 and CODE_FAIL.encode() in r.data
    assert _state(app, acct['uid'])[0]['status'] == 'active'
    # a new request sends no e-mail while locked
    sent.clear()
    time.sleep(0)  # (cooldown is separate; the lock is what stops the mail)
    _start(app, acct, client=app.test_client())
    assert sent == []
    # normal login is not affected
    _sign_in(app.test_client(), acct)
    # after 2 hours recovery works again (new code)
    clock.advance(7200 + 10)
    with app.app_context():
        db.execute('DELETE FROM email_codes WHERE user_id=?', (acct['uid'],))
    c3, _ = _start(app, acct)
    assert sent and _finish(c3, sent[-1]['code']).status_code == 302
    assert 'user.recovery_unlocked' in _audit(app, acct['uid'], 'user.recovery_%')
    _restore_pw(app, acct)


# =========================================================================== #
# Privacy: unknown / not enrolled / locked all look the same; Admin excluded
# =========================================================================== #
@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_unknown_id_and_no_authenticator_get_the_same_answer_and_no_email(app, client, acct, sent):
    _enrolled(client, app, acct, sent)
    sent.clear()
    c1, known = _start(app, acct)
    n = len(sent)
    c2, unknown = _start(app, acct, ident='NOPE-0000')
    assert known.status_code == unknown.status_code == 302
    assert known.headers['Location'] == unknown.headers['Location']
    assert n == 1 and len(sent) == 1
    p1 = c1.get('/lost-phone/no-codes/verify').get_data(as_text=True)
    p2 = c2.get('/lost-phone/no-codes/verify').get_data(as_text=True)
    assert 'we sent a' in p1 and 'we sent a' in p2
    r = _finish(c2, '123456')
    assert CODE_FAIL.encode() in r.data


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_account_without_an_authenticator_gets_no_email(app, acct, sent):
    c, r = _start(app, acct)                 # never enrolled
    assert r.status_code == 302 and sent == []
    assert CODE_FAIL.encode() in _finish(c, '123456').data


@pytest.mark.parametrize('acct', ['admin'], indirect=True)
def test_admin_is_excluded_and_unchanged(app, client, acct, sent):
    _enrolled(client, app, acct, sent)
    sent.clear()
    c, r = _start(app, acct, role='admin', ident=acct['ident'])
    assert r.status_code == 302 and sent == []
    assert CODE_FAIL.encode() in _finish(c, '123456').data
    row, remaining, _ = _state(app, acct['uid'])
    assert row['status'] == 'active' and remaining == 10
    assert _pw_ok(app, acct, acct['pw'])
    assert client.get('/admin/lost-authenticator').status_code == 200     # admin path untouched


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_resend_cooldown_and_expired_page(app, client, acct, sent):
    _enrolled(client, app, acct, sent)
    sent.clear()
    c, _ = _start(app, acct)
    _post(c, '/lost-phone/no-codes/verify', action='resend')              # inside 60 s
    assert len(sent) == 1
    fresh = app.test_client()
    r = fresh.get('/lost-phone/no-codes/verify')
    assert r.status_code == 302 and r.headers['Location'].endswith('/lost-phone/no-codes')


def test_ip_limit_on_starting_recovery(app, client):
    for _i in range(10):
        _post(client, '/lost-phone/no-codes', role='student', ident='NOPE-0001')
    r = _post(client, '/lost-phone/no-codes', role='student', ident='NOPE-0001')
    assert r.status_code == 200 and b'Too many attempts' in r.data


def test_csrf_is_required(app, client):
    r = client.post('/lost-phone/no-codes', data={'role': 'student', 'ident': 'X'})
    assert r.status_code in (400, 403)


# =========================================================================== #
# The existing backup-code page now honours the 2-hour lock
# =========================================================================== #
@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_backup_code_page_locks_for_two_hours_after_five_wrong_codes(app, client, acct, sent, clock):
    _, codes = _enrolled(client, app, acct, sent)
    client.get('/logout')
    anon = app.test_client()
    for _i in range(5):
        r = _recover(anon, acct, 'abcd-efgh-jkmn')
        assert GENERIC.encode() in r.data
    assert 'user.recovery_locked' in _audit(app, acct['uid'], 'user.recovery_%')
    r = _recover(anon, acct, codes[0])                  # a right code is refused while locked
    assert r.status_code == 200 and GENERIC.encode() in r.data
    row, remaining, _ = _state(app, acct['uid'])
    assert row['status'] == 'active' and remaining == 10
    _sign_in(app.test_client(), acct)                    # login still works
    clock.advance(7200 + 10)
    with app.app_context():
        db.execute('DELETE FROM login_attempts')         # (IP counters aside) 15-min lock long gone
    r = _recover(anon, acct, codes[0])
    assert r.status_code == 302
    assert _state(app, acct['uid'])[0]['status'] == 'pending'


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_wrong_passwords_on_the_backup_code_page_do_not_count_toward_the_two_hour_lock(
        app, client, acct, sent):
    _, codes = _enrolled(client, app, acct, sent)
    client.get('/logout')
    anon = app.test_client()
    for _i in range(4):
        _recover(anon, acct, codes[0], password='wrong-password')
    with app.app_context():
        assert rl.locked_seconds({'id': acct['uid'], 'role': 'student'}) == 0
    assert 'user.recovery_locked' not in _audit(app, acct['uid'], 'user.recovery_%')
