"""Checkpoint B3: lost-device recovery proved with a Microsoft / Google sign-in.

Student/Teacher/HOD/Dean (Admin excluded). Pre-login, free (no e-mail code), college tenant
required for Microsoft, the provider e-mail must match the address on file, and the 2-hour
recovery lock from B1 applies. The provider is mocked (``sso.fetch_claims``).
"""
import time
from urllib.parse import parse_qs, urlparse

import pytest

from feedback_system import authenticator as auth
from feedback_system import db, recovery_lock as rl, security, sso
from feedback_system.security import hash_password, verify_password

from test_authenticator_enroll_cp2 import (PW, _codes_in, _enroll, _post, _sign_in,  # noqa: F401
                                           acct, sent)
from test_authenticator_lost_device_cp3 import _enrolled, _state

ROLES4 = ['student', 'teacher', 'hod', 'dean']
NEW_PW = 'Brand-new-pass-9'
GID, MID = 'google-client-id', 'microsoft-client-id'
TENANT = '11111111-2222-3333-4444-555555555555'
OTHER_TENANT = '99999999-9999-9999-9999-999999999999'
GENERIC = 'We could not confirm that sign-in matches your account'


@pytest.fixture(autouse=True)
def _env(app, monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', GID)
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'g-secret')
    monkeypatch.setenv('MICROSOFT_CLIENT_ID', MID)
    monkeypatch.setenv('MICROSOFT_CLIENT_SECRET', 'm-secret')
    monkeypatch.setenv('MICROSOFT_TENANT_ID', TENANT)
    monkeypatch.delenv('GOOGLE_HOSTED_DOMAIN', raising=False)

    def wipe():
        with app.app_context():
            db.execute('DELETE FROM login_attempts')
            db.execute("DELETE FROM audit_log WHERE action LIKE '%lost_device%' "
                       "OR action LIKE 'user.recovery_%'")
    wipe()
    yield
    wipe()
    with app.app_context():
        for ident in ('CP2HOD', 'CP2DEAN'):
            db.execute('UPDATE users SET password_hash=?, must_change_password=0 WHERE username=?',
                       (hash_password(PW), ident))
        db.execute("UPDATE users SET email='student@test.edu' WHERE username=? AND role='student'",
                   (_student_ident(),))


def _student_ident():
    from test_authenticator_enroll_cp2 import ROLL
    return ROLL


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


def _email_on_file(app, a):
    """The address the provider must return. Students must use roll@college."""
    with app.app_context():
        if a['role'] == 'student':
            addr = security.student_college_email(a['ident'])
            db.execute('UPDATE users SET email=? WHERE id=?', (addr, a['uid']))
            return addr
        return db.query('SELECT email FROM users WHERE id=?', (a['uid'],), one=True)['email'].lower()


def _claims(provider, email, nonce, tid=TENANT):
    if provider == 'google':
        return {'iss': 'https://accounts.google.com', 'aud': GID, 'nonce': nonce,
                'email': email, 'email_verified': True}
    return {'iss': 'x', 'aud': MID, 'nonce': nonce, 'tid': tid, 'email': email}


def _provider_for(a):
    return 'microsoft'


def _begin(app, a, provider='microsoft', ident=None, role=None, client=None):
    c = client or app.test_client()
    r = _post(c, f'/lost-phone/sso/{provider}/start', role=role or a['role'],
              ident=ident or a['ident'])
    return c, r


def _callback(c, monkeypatch, provider, email, tid=TENANT, **extra):
    """Pretend the provider sent the user back with a good code for ``email``."""
    with c.session_transaction() as s:
        pend = s.get('recov_sso') or {}
    nonce, state = pend.get('nonce', 'x'), pend.get('state', 'x')
    monkeypatch.setattr(sso, 'fetch_claims',
                        lambda p, code, uri: _claims(p, email, nonce, tid))
    q = {'state': state, 'code': 'abc'}
    q.update(extra)
    return c.get(f'/lost-phone/sso/{provider}/callback', query_string=q)


def _finish(c, pw=NEW_PW, confirm=None):
    return _post(c, '/lost-phone/sso/finish', new_password=pw,
                 confirm_password=pw if confirm is None else confirm)


def _pw_ok(app, a, pw):
    with app.app_context():
        row = db.query('SELECT password_hash FROM users WHERE id=?', (a['uid'],), one=True)
        return verify_password(row['password_hash'], pw)


def _restore_pw(app, a):
    with app.app_context():
        db.execute('UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?',
                   (hash_password(a['pw']), a['uid']))


def _audit(app, uid, like):
    with app.app_context():
        return [r['action'] for r in db.query(
            "SELECT action FROM audit_log WHERE entity_id=? AND action LIKE ? ORDER BY id",
            (uid, like))]


def _untouched(app, a):
    row, remaining, _ = _state(app, a['uid'])
    return row['status'] == 'active' and remaining == 10 and _pw_ok(app, a, a['pw'])


# =========================================================================== #
# Which options are offered
# =========================================================================== #
def test_provider_rules_per_role(app):
    assert sso.recovery_providers_for('student') == ['microsoft']
    for role in ('teacher', 'hod', 'dean'):
        assert sso.recovery_providers_for(role) == ['microsoft', 'google']
    assert sso.recovery_providers_for('admin') == []
    assert sso.recovery_providers_for('') == []


@pytest.mark.parametrize('tenant', ['', 'common', 'organizations', 'adityauniversity.in', '"1111"'])
def test_microsoft_needs_the_college_tenant_guid_for_every_role(app, monkeypatch, tenant):
    monkeypatch.setenv('MICROSOFT_TENANT_ID', tenant)
    assert sso.recovery_providers_for('student') == []
    for role in ('teacher', 'hod', 'dean'):
        assert sso.recovery_providers_for(role) == ['google']


def test_nothing_offered_when_nothing_is_configured(app, monkeypatch):
    for k in ('GOOGLE_CLIENT_ID', 'MICROSOFT_CLIENT_ID'):
        monkeypatch.delenv(k)
    assert all(sso.recovery_providers_for(r) == [] for r in ROLES4)


def test_start_page_shows_buttons_only_when_available(app, client, monkeypatch):
    page = client.get('/lost-phone/no-codes').get_data(as_text=True)
    assert 'Continue with Microsoft' in page and 'Email me a code' in page
    assert page.index('Continue with Microsoft') < page.index('Email me a code')
    monkeypatch.delenv('GOOGLE_CLIENT_ID')
    monkeypatch.delenv('MICROSOFT_CLIENT_ID')
    page = client.get('/lost-phone/no-codes').get_data(as_text=True)
    assert 'Continue with' not in page and 'Email me a code' in page


def test_an_unavailable_provider_cannot_be_started(app, client, monkeypatch):
    monkeypatch.setenv('MICROSOFT_TENANT_ID', 'common')
    r = _post(client, '/lost-phone/sso/microsoft/start', role='teacher', ident='6817')
    assert r.status_code == 302 and r.headers['Location'].endswith('/lost-phone/no-codes')
    r = _post(client, '/lost-phone/sso/google/start', role='student', ident='X')   # not for students
    assert r.headers['Location'].endswith('/lost-phone/no-codes')
    assert client.get('/lost-phone/sso/microsoft/start').status_code == 405        # POST only


# =========================================================================== #
# The whole journey
# =========================================================================== #
@pytest.mark.parametrize('acct', ROLES4, indirect=True)
def test_full_recovery_with_microsoft_then_new_setup_with_confirmation(app, client, acct, sent,
                                                                      monkeypatch):
    _enrolled(client, app, acct, sent)
    client.get('/logout')
    email = _email_on_file(app, acct)
    sent.clear()
    c, r = _begin(app, acct)
    assert r.status_code == 302 and 'login.microsoftonline.com' in r.headers['Location']
    assert TENANT in r.headers['Location']                       # college tenant only
    r = _callback(c, monkeypatch, 'microsoft', email)
    assert r.status_code == 302 and r.headers['Location'].endswith('/lost-phone/sso/finish')
    assert sent == []                                             # NO email on the sign-in path
    assert _state(app, acct['uid'])[0]['status'] == 'active'      # nothing removed yet
    assert b'Choose a new password' in c.get('/lost-phone/sso/finish').data

    r = _finish(c)
    assert r.status_code == 302 and 'login' in r.headers['Location']
    row, _rem, total = _state(app, acct['uid'])
    assert row['status'] == 'pending' and total == 0
    with app.app_context():
        assert auth.needs_reenrollment(acct['uid'])
    assert _pw_ok(app, acct, NEW_PW)
    assert len(sent) == 1 and 'Microsoft' in sent[0]['text']      # one notice, no code mail
    assert 'user.lost_device_recovery' in _audit(app, acct['uid'], 'user.lost_device_recovery')
    assert 'user.lost_device_sso_verified' in _audit(app, acct['uid'], 'user.lost_device_sso%')

    acct2 = {**acct, 'pw': NEW_PW}
    c2 = app.test_client()
    saved = {k: app.config.get(k) for k in ('REQUIRE_ACCOUNT_SETUP', 'AUTHENTICATOR_ENFORCE_ROLES')}
    app.config['REQUIRE_ACCOUNT_SETUP'] = True
    app.config['AUTHENTICATOR_ENFORCE_ROLES'] = ''
    try:
        _sign_in(c2, acct2)
        home = {'student': '/student/', 'teacher': '/teacher/', 'hod': '/hod/', 'dean': '/dean/'}
        r = c2.get(home[acct['role']])
        assert r.status_code == 302 and '/setup/authenticator' in r.headers['Location']
        app.config['REQUIRE_ACCOUNT_SETUP'] = False
        _secret, resp = _enroll(c2, app, acct2, sent)             # confirmation code step
    finally:
        app.config.update(saved)
    assert len(_codes_in(resp.get_data(as_text=True))) == 10
    row, remaining, _ = _state(app, acct['uid'])
    assert row['status'] == 'active' and remaining == 10
    _restore_pw(app, acct)


@pytest.mark.parametrize('acct', ['teacher', 'hod', 'dean'], indirect=True)
def test_staff_can_recover_with_google(app, client, acct, sent, monkeypatch):
    _enrolled(client, app, acct, sent)
    client.get('/logout')
    email = _email_on_file(app, acct)
    c, r = _begin(app, acct, provider='google')
    assert 'accounts.google.com' in r.headers['Location']
    r = _callback(c, monkeypatch, 'google', email)
    assert r.headers['Location'].endswith('/lost-phone/sso/finish')
    assert _finish(c).status_code == 302
    assert _state(app, acct['uid'])[0]['status'] == 'pending'
    _restore_pw(app, acct)


# =========================================================================== #
# What must be refused (generic message, nothing changed)
# =========================================================================== #
@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_wrong_tenant_is_refused(app, client, acct, sent, monkeypatch):
    _enrolled(client, app, acct, sent)
    client.get('/logout')
    c, _ = _begin(app, acct)
    r = _callback(c, monkeypatch, 'microsoft', _email_on_file(app, acct), tid=OTHER_TENANT)
    assert r.headers['Location'].endswith('/lost-phone/no-codes')
    assert b'college Microsoft account' in c.get('/lost-phone/no-codes').data
    assert c.get('/lost-phone/sso/finish').headers['Location'].endswith('/lost-phone/no-codes')
    assert _untouched(app, acct)


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_a_different_email_is_refused_and_counted(app, client, acct, sent, monkeypatch):
    _enrolled(client, app, acct, sent)
    client.get('/logout')
    c, _ = _begin(app, acct)
    r = _callback(c, monkeypatch, 'microsoft', 'someone.else@test.edu')
    assert r.headers['Location'].endswith('/lost-phone/no-codes')
    assert GENERIC.encode() in c.get('/lost-phone/no-codes').data
    assert _untouched(app, acct)
    assert 'user.lost_device_recovery_failed' in _audit(app, acct['uid'], 'user.lost_device%')
    with app.app_context():
        row = db.query('SELECT fail_count FROM login_attempts WHERE attempt_key=?',
                       (security._db_key(f"reclock:{acct['uid']}"),), one=True)
        assert row and int(row['fail_count']) == 1


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_student_must_use_the_college_address(app, client, acct, sent, monkeypatch):
    _enrolled(client, app, acct, sent)
    client.get('/logout')
    college = _email_on_file(app, acct)
    # stored address is not the college one: even a matching sign-in is refused
    with app.app_context():
        db.execute('UPDATE users SET email=? WHERE id=?', ('student@test.edu', acct['uid']))
    c, _ = _begin(app, acct)
    r = _callback(c, monkeypatch, 'microsoft', 'student@test.edu')
    assert r.headers['Location'].endswith('/lost-phone/no-codes')
    assert _untouched(app, acct)
    # the proper college address on file and in the token works
    _email_on_file(app, acct)
    c, _ = _begin(app, acct)
    r = _callback(c, monkeypatch, 'microsoft', college)
    assert r.headers['Location'].endswith('/lost-phone/sso/finish')
    _restore_pw(app, acct)


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_unknown_id_not_enrolled_and_locked_all_give_the_same_answer(app, client, acct, sent,
                                                                      monkeypatch):
    email = _email_on_file(app, acct)
    outcomes = []
    # 1) never enrolled  2) unknown ID
    for ident in (acct['ident'], 'NOPE-0000'):
        c, r = _begin(app, acct, ident=ident)
        assert 'login.microsoftonline.com' in r.headers['Location']      # same start for all
        r = _callback(c, monkeypatch, 'microsoft', email)
        outcomes.append((r.status_code, r.headers['Location'],
                         GENERIC.encode() in c.get('/lost-phone/no-codes').data))
    assert outcomes[0] == outcomes[1] == (302, outcomes[0][1], True)
    # 3) enrolled but locked
    _enrolled(client, app, acct, sent)
    client.get('/logout')
    u = {'id': acct['uid'], 'role': 'student'}
    with app.app_context():
        for _i in range(5):
            rl.begin_attempt(u)
            rl.record_failure(u)
    c, _ = _begin(app, acct)
    r = _callback(c, monkeypatch, 'microsoft', email)
    assert (r.status_code, r.headers['Location']) == outcomes[0][:2]
    assert GENERIC.encode() in c.get('/lost-phone/no-codes').data
    assert _untouched(app, acct)


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_five_wrong_sign_ins_lock_recovery_for_two_hours_not_login(app, client, acct, sent,
                                                                  monkeypatch, clock):
    _enrolled(client, app, acct, sent)
    client.get('/logout')
    email = _email_on_file(app, acct)
    for _i in range(5):
        c, _ = _begin(app, acct)
        _callback(c, monkeypatch, 'microsoft', 'wrong@adityauniversity.in')
    assert _audit(app, acct['uid'], 'user.recovery_%') == ['user.recovery_locked']
    c, _ = _begin(app, acct)
    r = _callback(c, monkeypatch, 'microsoft', email)                  # the right one: refused
    assert r.headers['Location'].endswith('/lost-phone/no-codes')
    assert _untouched(app, acct)
    _sign_in(app.test_client(), acct)                                  # normal login unaffected
    clock.advance(7200 + 10)
    with app.app_context():
        db.execute('DELETE FROM login_attempts')                       # IP counters only
    c, _ = _begin(app, acct)
    r = _callback(c, monkeypatch, 'microsoft', email)
    assert r.headers['Location'].endswith('/lost-phone/sso/finish')
    assert 'user.recovery_unlocked' in _audit(app, acct['uid'], 'user.recovery_%')
    _restore_pw(app, acct)


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_bad_state_missing_start_and_wrong_provider_are_refused(app, client, acct, sent, monkeypatch):
    _enrolled(client, app, acct, sent)
    client.get('/logout')
    email = _email_on_file(app, acct)
    c, _ = _begin(app, acct)
    r = _callback(c, monkeypatch, 'microsoft', email, state='forged')
    assert r.headers['Location'].endswith('/lost-phone/no-codes')
    assert b'expired' in c.get('/lost-phone/no-codes').data
    # no start at all
    fresh = app.test_client()
    monkeypatch.setattr(sso, 'fetch_claims', lambda *a: _claims('microsoft', email, 'x'))
    r = fresh.get('/lost-phone/sso/microsoft/callback', query_string={'state': 'x', 'code': 'abc'})
    assert r.headers['Location'].endswith('/lost-phone/no-codes')
    # started with Microsoft, returned on the Google callback
    c, _ = _begin(app, acct)
    r = _callback(c, monkeypatch, 'google', email)
    assert r.headers['Location'].endswith('/lost-phone/no-codes')
    # state is one use only
    c, _ = _begin(app, acct)
    with c.session_transaction() as s:
        st = s['recov_sso']['state']
    _callback(c, monkeypatch, 'microsoft', email)
    r = c.get('/lost-phone/sso/microsoft/callback', query_string={'state': st, 'code': 'abc'})
    assert r.headers['Location'].endswith('/lost-phone/no-codes')
    assert _untouched(app, acct) or _state(app, acct['uid'])[0]['status'] == 'active'


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_cancelled_sign_in_and_expired_state(app, client, acct, sent, monkeypatch):
    _enrolled(client, app, acct, sent)
    client.get('/logout')
    c, _ = _begin(app, acct)
    r = _callback(c, monkeypatch, 'microsoft', _email_on_file(app, acct), error='access_denied')
    assert r.headers['Location'].endswith('/lost-phone/no-codes')
    assert b'cancelled' in c.get('/lost-phone/no-codes').data
    c, _ = _begin(app, acct)
    with c.session_transaction() as s:
        s['recov_sso'] = {**s['recov_sso'], 'exp': 1}      # reassign: nested edits are not saved
    r = _callback(c, monkeypatch, 'microsoft', _email_on_file(app, acct))
    assert r.headers['Location'].endswith('/lost-phone/no-codes')
    assert _untouched(app, acct)


# =========================================================================== #
# The proof: single use, short-lived, password rules
# =========================================================================== #
@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_proof_is_single_use_expires_and_password_rules_keep_it(app, client, acct, sent, monkeypatch):
    _enrolled(client, app, acct, sent)
    client.get('/logout')
    email = _email_on_file(app, acct)
    c, _ = _begin(app, acct)
    _callback(c, monkeypatch, 'microsoft', email)
    assert b'at least 8' in _finish(c, pw='short').data
    assert b'do not match' in _finish(c, pw=NEW_PW, confirm='Different-pass-1').data
    assert b'not the default' in _finish(c, pw='teacher@123').data
    assert _untouched(app, acct)                                   # nothing changed yet
    assert _finish(c).status_code == 302                           # the same proof still works once
    r = c.get('/lost-phone/sso/finish')                            # ...and only once
    assert r.status_code == 302 and r.headers['Location'].endswith('/lost-phone/no-codes')
    _restore_pw(app, acct)
    # expiry
    _enrolled(app.test_client(), app, acct, sent)
    c, _ = _begin(app, acct)
    _callback(c, monkeypatch, 'microsoft', email)
    with c.session_transaction() as s:
        s['recov_proof'] = {**s['recov_proof'], 'exp': 1}
    r = _finish(c)
    assert r.status_code == 302 and r.headers['Location'].endswith('/lost-phone/no-codes')
    assert _state(app, acct['uid'])[0]['status'] == 'active'


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_a_proof_cannot_finish_after_the_account_became_locked(app, client, acct, sent, monkeypatch):
    _enrolled(client, app, acct, sent)
    client.get('/logout')
    c, _ = _begin(app, acct)
    _callback(c, monkeypatch, 'microsoft', _email_on_file(app, acct))
    u = {'id': acct['uid'], 'role': 'teacher'}
    with app.app_context():
        for _i in range(5):
            rl.begin_attempt(u)
            rl.record_failure(u)
    r = _finish(c)
    assert r.headers['Location'].endswith('/lost-phone/no-codes')
    assert _untouched(app, acct)


# =========================================================================== #
# Admin excluded; limits; existing paths untouched
# =========================================================================== #
@pytest.mark.parametrize('acct', ['admin'], indirect=True)
def test_admin_is_excluded_and_unchanged(app, client, acct, sent, monkeypatch):
    _enrolled(client, app, acct, sent)
    client.get('/logout')
    assert sso.recovery_providers_for('admin') == []
    c, r = _begin(app, acct, role='admin', ident=acct['ident'])    # treated as a student ID
    r = _callback(c, monkeypatch, 'microsoft', acct['ident'])
    assert r.headers['Location'].endswith('/lost-phone/no-codes')
    assert GENERIC.encode() in c.get('/lost-phone/no-codes').data
    row, remaining, _ = _state(app, acct['uid'])
    assert row['status'] == 'active' and remaining == 10 and _pw_ok(app, acct, acct['pw'])
    assert client.get('/admin/lost-authenticator').status_code == 200


def test_ip_limit_on_starting_a_sign_in_recovery(app, client):
    for _i in range(10):
        _post(client, '/lost-phone/sso/microsoft/start', role='student', ident='NOPE-0001')
    r = _post(client, '/lost-phone/sso/microsoft/start', role='student', ident='NOPE-0001')
    assert r.headers['Location'].endswith('/lost-phone/no-codes')
    assert b'Too many attempts' in client.get('/lost-phone/no-codes').data


def test_csrf_is_required_on_start(app, client):
    r = client.post('/lost-phone/sso/microsoft/start', data={'role': 'student', 'ident': 'X'})
    assert r.status_code in (400, 403)


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_emailed_code_fallback_still_works_when_sso_is_available(app, client, acct, sent):
    _enrolled(client, app, acct, sent)
    client.get('/logout')
    sent.clear()
    c = app.test_client()
    _post(c, '/lost-phone/no-codes', role='teacher', ident=acct['ident'])
    assert len(sent) == 1 and sent[0]['code']
    r = _post(c, '/lost-phone/no-codes/verify', code=sent[0]['code'], new_password=NEW_PW,
              confirm_password=NEW_PW)
    assert r.status_code == 302
    assert _state(app, acct['uid'])[0]['status'] == 'pending'
    _restore_pw(app, acct)


def test_existing_in_app_sso_still_needs_a_login(app, client):
    r = client.get('/sso/microsoft/start')
    assert r.status_code == 302 and '/login' in r.headers['Location']
