"""Lost-device recovery (all-roles upgrade, Checkpoint 3).

Student/Teacher/HOD/Dean: ``/lost-phone`` (ID + password + one backup code).
Admin: ``/admin/lost-authenticator`` (both registered recovery e-mails, a code to each).
Normal password reset is not changed and is covered by one regression test here.
"""
import re
import threading
import time

import pyotp
import pytest

from feedback_system import authenticator as auth
from feedback_system import db, mailer, verification
from feedback_system.security import clear_login_failures, hash_password
from helpers import login

from test_authenticator_enroll_cp2 import (LOGIN, PW, ROLES, _codes_in, _enroll,  # noqa: F401
                                           _post, _sign_in, acct, sent)

NON_ADMIN = ['student', 'teacher', 'hod', 'dean']
E1, E2 = 'recovery.one@test.edu', 'recovery.two@test.edu'
GENERIC = 'We could not verify those details'


@pytest.fixture(autouse=True)
def _reset_limits(app):
    """Counters live in the shared test DB; start every test clean."""
    with app.app_context():
        db.execute("DELETE FROM login_attempts")
        db.execute("DELETE FROM audit_log WHERE action LIKE '%lost%'")
    yield


def _recover(client, a, code, password=None, ident=None, role=None):
    return _post(client, '/lost-phone', role=role or a['role'], ident=ident or a['ident'],
                 password=a['pw'] if password is None else password, code=code)


def _enrolled(client, app, a, sent):
    """Enrol and return (secret, codes). The client ends signed in."""
    _sign_in(client, a)
    secret, r = _enroll(client, app, a, sent)
    codes = _codes_in(r.get_data(as_text=True))
    assert len(codes) == 10
    return secret, codes


def _state(app, uid):
    with app.app_context():
        row = db.query('SELECT status, enrolled_at FROM student_authenticators WHERE user_id=?',
                       (uid,), one=True)
        return (dict(row) if row else None, auth.backup_codes_remaining(uid),
                db.query('SELECT COUNT(*) AS n FROM student_backup_codes WHERE user_id=?',
                         (uid,), one=True)['n'])


def _audit(app, action, uid=None):
    with app.app_context():
        q, p = 'SELECT * FROM audit_log WHERE action=?', [action]
        if uid is not None:
            q, p = q + ' AND entity_id=?', p + [uid]
        return db.query(q, p)


# =========================================================================== #
# Student / Teacher / HOD / Dean
# =========================================================================== #
@pytest.mark.parametrize('acct', NON_ADMIN, indirect=True)
def test_recovery_removes_the_authenticator_and_every_code_and_requires_a_new_one(
        app, client, acct, sent):
    secret, codes = _enrolled(client, app, acct, sent)
    sent.clear()
    anon = app.test_client()
    r = _recover(anon, acct, codes[0])
    assert r.status_code == 302 and 'login' in r.headers['Location']
    row, remaining, total = _state(app, acct['uid'])
    assert row['status'] == 'pending' and row['enrolled_at'] and remaining == 0 and total == 0
    with app.app_context():
        assert not auth.is_active(acct['uid']) and auth.needs_reenrollment(acct['uid'])
        # The old phone and the nine unused codes are dead everywhere.
        code, now = pyotp.TOTP(secret).at(time.time() + 60), time.time() + 60
        assert auth.verify_second_factor(acct['uid'], code, now=now) == (False, 'not_enrolled')
        assert auth.verify_recovery_code(acct['uid'], codes[1]) == (False, 'not_enrolled')
        assert not auth.consume_backup_code(acct['uid'], codes[1])
        # The pending secret is brand new, never the old one.
        assert auth.pending_secret(acct['uid']) != secret
    assert _audit(app, 'user.lost_device_recovery', acct['uid'])
    notice = [m for m in sent if 'lost-device' in m['subject'].lower()]
    assert len(notice) == 1 and notice[0]['code'] is None and 'removed' in notice[0]['text']
    assert not any(c in m['text'] for m in sent for c in codes) and secret not in notice[0]['text']


@pytest.mark.parametrize('acct', NON_ADMIN, indirect=True)
def test_the_next_sign_in_forces_new_enrollment_whatever_the_policy(app, client, acct, sent):
    secret, codes = _enrolled(client, app, acct, sent)
    _recover(app.test_client(), acct, codes[0])
    saved = {k: app.config.get(k) for k in ('REQUIRE_ACCOUNT_SETUP', 'AUTHENTICATOR_ENFORCE_ROLES')}
    app.config['REQUIRE_ACCOUNT_SETUP'] = True
    app.config['AUTHENTICATOR_ENFORCE_ROLES'] = ''            # no role is enforced by config
    try:
        fresh = app.test_client()
        _sign_in(fresh, acct)
        home = {'student': '/student/', 'teacher': '/teacher/', 'hod': '/hod/', 'dean': '/dean/'}
        r = fresh.get(home[acct['role']])
        assert r.status_code == 302 and 'setup/authenticator' in r.headers['Location']
        # ...and it is mandatory (no "Not now" way out), then it works.
        page = fresh.get('/setup/authenticator').get_data(as_text=True)
        assert 'Not now' not in page
        app.config['REQUIRE_ACCOUNT_SETUP'] = False
        new_secret, r2 = _enroll(fresh, app, acct, sent)
        assert new_secret != secret and r2.status_code == 200
        assert len(_codes_in(r2.get_data(as_text=True))) == 10
        with app.app_context():
            assert auth.is_active(acct['uid']) and not auth.needs_reenrollment(acct['uid'])
        app.config['REQUIRE_ACCOUNT_SETUP'] = True
        assert 'setup/authenticator' not in (fresh.get(home[acct['role']]).headers.get('Location') or '')
    finally:
        app.config.update(saved)


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_a_voluntary_unfinished_setup_is_not_mistaken_for_a_recovery(app, client, acct):
    _sign_in(client, acct)
    with app.app_context():
        auth.begin_enrollment(acct['uid'])                    # pending, no marker
        assert not auth.needs_reenrollment(acct['uid'])
    saved = {k: app.config.get(k) for k in ('REQUIRE_ACCOUNT_SETUP', 'AUTHENTICATOR_ENFORCE_ROLES')}
    app.config['REQUIRE_ACCOUNT_SETUP'] = True
    app.config['AUTHENTICATOR_ENFORCE_ROLES'] = 'student'
    try:
        assert 'setup/authenticator' not in (client.get('/teacher/').headers.get('Location') or '')
    finally:
        app.config.update(saved)


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_wrong_password_with_a_valid_code_changes_nothing(app, client, acct, sent):
    _, codes = _enrolled(client, app, acct, sent)
    r = _recover(app.test_client(), acct, codes[0], password='not-my-password')
    assert r.status_code == 200 and GENERIC.encode() in r.data
    row, remaining, _ = _state(app, acct['uid'])
    assert row['status'] == 'active' and remaining == 10        # code NOT spent
    assert _audit(app, 'user.lost_device_recovery_failed', acct['uid'])


@pytest.mark.parametrize('acct', ['hod'], indirect=True)
def test_only_a_backup_code_is_accepted_and_an_app_code_is_not_spent(app, client, acct, sent):
    secret, codes = _enrolled(client, app, acct, sent)
    nxt = pyotp.TOTP(secret).at(time.time() + 30)
    for bad in (nxt, '', 'abcd-efgh-jkmn', codes[0][:-1], codes[0] + 'x', 'ZZZZ-ZZZZ-ZZZZ'):
        r = _recover(app.test_client(), acct, bad)
        assert r.status_code == 200 and GENERIC.encode() in r.data, bad
    row, remaining, _ = _state(app, acct['uid'])
    assert row['status'] == 'active' and remaining == 10
    with app.app_context():                                       # the app code is still unspent
        assert auth.verify_second_factor(acct['uid'], nxt, now=time.time() + 30) == (True, 'totp')


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_a_used_code_cannot_be_replayed_against_the_new_authenticator(app, client, acct, sent):
    _, codes = _enrolled(client, app, acct, sent)
    assert _recover(app.test_client(), acct, codes[0]).status_code == 302
    fresh = app.test_client()
    _sign_in(fresh, acct)
    _, r = _enroll(fresh, app, acct, sent)
    new_codes = _codes_in(r.get_data(as_text=True))
    # Old codes (used and unused) never work again; the new authenticator is untouched.
    for old in codes:
        assert GENERIC.encode() in _recover(app.test_client(), acct, old).data
    row, remaining, _ = _state(app, acct['uid'])
    assert row['status'] == 'active' and remaining == 10 and set(new_codes).isdisjoint(codes)


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_every_failure_looks_the_same_to_the_browser(app, client, acct, sent):
    _, codes = _enrolled(client, app, acct, sent)

    def view(resp):
        text = resp.get_data(as_text=True)
        text = re.sub(r'name="_csrf" value="[^"]*"', '', text)
        text = re.sub(r'name="csrf-token" content="[^"]*"', '', text)
        text = re.sub(r'name="ident" value="[^"]*"', '', text)
        return resp.status_code, text
    cases = {
        'unknown id': _recover(app.test_client(), acct, codes[0], ident='NOSUCH999'),
        'wrong password': _recover(app.test_client(), acct, codes[0], password='x' * 12),
        'wrong code': _recover(app.test_client(), acct, 'abcd-efgh-jkmn'),
        'bad shape': _recover(app.test_client(), acct, '123456'),
    }
    views = {k: view(v) for k, v in cases.items()}
    assert len({v for v in views.values()}) == 1, 'responses differ: {}'.format(list(views))
    assert views['unknown id'][0] == 200 and GENERIC in views['unknown id'][1]
    # An account with no authenticator at all answers the same way.
    with app.app_context():
        db.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(PW), acct['uid']))
        auth.remove_authenticator(acct['uid'])
    none = view(_recover(app.test_client(), acct, codes[0]))
    assert none == views['unknown id']


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_brute_force_locks_the_account_but_not_sign_in_or_password_reset(app, client, acct, sent):
    secret, codes = _enrolled(client, app, acct, sent)
    app.config['OTP_MAX_ATTEMPTS'] = 3
    try:
        for _ in range(3):
            _recover(app.test_client(), acct, 'abcd-efgh-jkmn')
        with app.app_context():
            assert auth.lost_locked_seconds(acct['uid']) > 0
            assert auth.otp_locked_seconds(acct['uid']) == 0        # sign-in gate untouched
            assert auth.reset_locked_seconds(acct['uid']) == 0      # recovery key untouched
        # Even the right password and a right code are refused while locked.
        assert GENERIC.encode() in _recover(app.test_client(), acct, codes[0]).data
        row, remaining, _ = _state(app, acct['uid'])
        assert row['status'] == 'active' and remaining == 10
        with app.app_context():
            auth.clear_lost_failures(acct['uid'])
        assert _recover(app.test_client(), acct, codes[0]).status_code == 302
    finally:
        app.config['OTP_MAX_ATTEMPTS'] = 5


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_sign_in_failures_do_not_lock_lost_device_recovery(app, client, acct, sent):
    secret, codes = _enrolled(client, app, acct, sent)
    app.config['OTP_MAX_ATTEMPTS'] = 3
    try:
        with app.app_context():
            for _ in range(4):
                auth.verify_second_factor(acct['uid'], '000000', now=time.time() + 90)
            assert auth.otp_locked_seconds(acct['uid']) > 0
            assert auth.lost_locked_seconds(acct['uid']) == 0
        assert _recover(app.test_client(), acct, codes[0]).status_code == 302
    finally:
        app.config['OTP_MAX_ATTEMPTS'] = 5


def test_per_address_limit_stops_guessing_across_many_ids(app):
    app.config['LOST_DEVICE_IP_MAX_ATTEMPTS'] = 3
    try:
        c = app.test_client()
        for i in range(3):
            assert b'Too many attempts' not in _post(c, '/lost-phone', role='student', ident=f'X{i}',
                                                     password='p', code='c').data
        r = _post(c, '/lost-phone', role='student', ident='X9', password='p', code='c')
        assert b'Too many attempts' in r.data
    finally:
        app.config['LOST_DEVICE_IP_MAX_ATTEMPTS'] = 10


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_concurrent_recoveries_remove_exactly_once(app, client, acct, sent):
    _, codes = _enrolled(client, app, acct, sent)
    results, errors = [], []
    gate = threading.Barrier(5)

    def worker(code):
        try:
            with app.app_context():
                gate.wait(timeout=10)
                results.append(auth.recover_lost_device(acct['uid'], code, reserve=False))
        except Exception as exc:                                  # pragma: no cover
            errors.append(repr(exc))
    threads = [threading.Thread(target=worker, args=(c,)) for c in codes[:5]]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert not errors, errors
    assert sum(1 for ok, _ in results if ok) == 1, results
    row, remaining, total = _state(app, acct['uid'])
    assert row['status'] == 'pending' and row['enrolled_at'] and total == 0
    with app.app_context():
        assert db.query('SELECT COUNT(*) AS n FROM student_authenticators WHERE user_id=?',
                        (acct['uid'],), one=True)['n'] == 1


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_the_same_code_submitted_twice_succeeds_once(app, client, acct, sent):
    _, codes = _enrolled(client, app, acct, sent)
    with app.app_context():
        assert auth.recover_lost_device(acct['uid'], codes[0]) == (True, 'backup')
        assert auth.recover_lost_device(acct['uid'], codes[0]) == (False, 'not_enrolled')
        assert auth.apply_lost_device_reset(acct['uid']) is False


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_a_bad_encryption_key_fails_closed_and_spends_nothing(app, client, acct, sent, monkeypatch):
    _, codes = _enrolled(client, app, acct, sent)
    monkeypatch.setitem(app.config, 'TOTP_ENCRYPTION_KEY', 'not-a-key')
    with app.app_context():
        with pytest.raises(auth.AuthenticatorConfigError):
            auth.recover_lost_device(acct['uid'], codes[0])
    monkeypatch.undo()
    row, remaining, _ = _state(app, acct['uid'])
    assert row['status'] == 'active' and remaining == 10


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_a_failure_during_removal_rolls_everything_back(app, client, acct, sent, monkeypatch):
    _, codes = _enrolled(client, app, acct, sent)
    with app.app_context():
        conn = db.get_db()
        real = conn.execute
        calls = []

        class Boom(Exception):
            pass

        class Wrapper:
            def __init__(self, inner):
                self.inner = inner

            def execute(self, sql, *a, **k):
                if sql.lstrip().startswith('INSERT INTO student_authenticators'):
                    raise Boom()
                return self.inner.execute(sql, *a, **k)

            def __getattr__(self, name):
                return getattr(self.inner, name)
        monkeypatch.setattr(db, 'get_db', lambda: Wrapper(conn))
        with pytest.raises(Boom):
            auth.apply_lost_device_reset(acct['uid'])
        monkeypatch.undo()
    row, remaining, total = _state(app, acct['uid'])
    assert row['status'] == 'active' and total == 10          # nothing was removed


def test_service_reasons_and_not_enrolled_is_not_counted(app):
    with app.app_context():
        uid = db.query("SELECT id FROM users WHERE role='teacher' ORDER BY id LIMIT 1", one=True)['id']
        db.execute('DELETE FROM student_backup_codes WHERE user_id=?', (uid,))
        db.execute('DELETE FROM student_authenticators WHERE user_id=?', (uid,))
        for _ in range(12):
            assert auth.recover_lost_device(uid, 'abcd-efgh-jkmn') == (False, 'not_enrolled')
        assert auth.lost_locked_seconds(uid) == 0


@pytest.mark.parametrize('acct', ['admin'], indirect=True)
def test_the_administrator_cannot_use_the_staff_page(app, client, acct, sent):
    _, codes = _enrolled(client, app, acct, sent)
    r = _recover(app.test_client(), acct, codes[0], role='admin')
    assert r.status_code == 200 and GENERIC.encode() in r.data
    row, remaining, _ = _state(app, acct['uid'])
    assert row['status'] == 'active' and remaining == 10


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_csrf_is_required(app, client, acct, sent):
    _, codes = _enrolled(client, app, acct, sent)
    r = app.test_client().post('/lost-phone', data={'role': 'teacher', 'ident': acct['ident'],
                                                    'password': acct['pw'], 'code': codes[0]})
    assert r.status_code in (400, 403)
    assert _state(app, acct['uid'])[0]['status'] == 'active'


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_normal_password_reset_keeps_the_authenticator(app, client, acct, sent):
    """Forgot-password now takes an app code (no e-mail) and still keeps the authenticator."""
    import pyotp
    secret, _ = _enrolled(client, app, acct, sent)
    sent.clear()
    anon = app.test_client()
    _post(anon, '/forgot-password', role='student', ident=acct['ident'])
    assert sent == []                                   # no e-mail for an enrolled account
    r = _post(anon, '/forgot-password/reset', code=pyotp.TOTP(secret).at(time.time() + 30),
              new_password='Brand-new-pass-9', confirm_password='Brand-new-pass-9')
    assert r.status_code == 302
    row, remaining, _ = _state(app, acct['uid'])
    assert row['status'] == 'active' and remaining == 10
    with app.app_context():
        assert not auth.needs_reenrollment(acct['uid'])
        db.execute('UPDATE users SET password_hash=? WHERE id=?',
                   (hash_password(acct['pw']), acct['uid']))


# =========================================================================== #
# Administrator: both registered recovery e-mails
# =========================================================================== #
@pytest.fixture()
def adm(app, sent):
    """The admin account with two registered recovery e-mails and an active authenticator."""
    with app.app_context():
        row = db.query("SELECT id, recovery_email1, recovery_email2 FROM users WHERE role='admin' "
                       "ORDER BY id LIMIT 1", one=True)
        uid = row['id']
        saved = (row['recovery_email1'], row['recovery_email2'])
        db.execute('UPDATE users SET recovery_email1=?, recovery_email2=? WHERE id=?', (E1, E2, uid))
        for t in ('student_backup_codes', 'student_authenticators', 'email_codes'):
            db.execute(f'DELETE FROM {t} WHERE user_id=?', (uid,))
        for k in (f'otp:{uid}', f'otpreset:{uid}', f'otplost:{uid}', f'adminlostv:{uid}'):
            clear_login_failures(k)
    a = {'role': 'admin', 'uid': uid, 'ident': 'admin@college.edu', 'pw': 'admin123'}
    c = app.test_client()
    _sign_in(c, a)
    secret, r = _enroll(c, app, a, sent)
    codes = _codes_in(r.get_data(as_text=True))
    sent.clear()
    a.update(secret=secret, codes=codes)
    yield a
    with app.app_context():
        db.execute('UPDATE users SET recovery_email1=?, recovery_email2=? WHERE id=?', (*saved, uid))
        for t in ('student_backup_codes', 'student_authenticators', 'email_codes'):
            db.execute(f'DELETE FROM {t} WHERE user_id=?', (uid,))


def _start(client, e1=E1, e2=E2):
    return _post(client, '/admin/lost-authenticator', email1=e1, email2=e2)


def _codes_sent(sent):
    got = {m['to']: m['code'] for m in sent if m['code'] and 'recovery' in m['subject'].lower()}
    return got


def _finish(client, c1, c2):
    return _post(client, '/admin/lost-authenticator/verify', code1=c1, code2=c2)


def test_admin_recovery_needs_both_codes_and_removes_everything(app, adm, sent):
    c = app.test_client()
    r = _start(c)
    assert r.status_code == 302 and 'verify' in r.headers['Location']
    got = _codes_sent(sent)
    assert set(got) == {E1, E2} and got[E1] != got[E2]            # two addresses, two codes
    r = _finish(c, got[E1], got[E2])
    assert r.status_code == 302 and 'admin/login' in r.headers['Location']
    row, remaining, total = _state(app, adm['uid'])
    assert row['status'] == 'pending' and row['enrolled_at'] and total == 0
    with app.app_context():
        assert not auth.is_active(adm['uid']) and auth.needs_reenrollment(adm['uid'])
        assert auth.pending_secret(adm['uid']) != adm['secret']
    assert _audit(app, 'admin.lost_authenticator_requested', adm['uid'])
    assert _audit(app, 'admin.lost_authenticator_recovery', adm['uid'])
    notices = [m for m in sent if 'lost-device' in m['subject'].lower()]
    assert {m['to'] for m in notices} >= {E1, E2}
    assert not any(adm['secret'] in m['text'] or any(x in m['text'] for x in adm['codes'])
                   for m in sent)


def test_admin_must_enrol_again_at_next_sign_in(app, adm, sent):
    c = app.test_client()
    _start(c)
    got = _codes_sent(sent)
    _finish(c, got[E1], got[E2])
    saved = app.config['REQUIRE_ACCOUNT_SETUP']
    app.config['REQUIRE_ACCOUNT_SETUP'] = True
    try:
        fresh = app.test_client()
        _sign_in(fresh, adm)
        r = fresh.get('/admin/')
        assert r.status_code == 302 and 'setup/authenticator' in r.headers['Location']
    finally:
        app.config['REQUIRE_ACCOUNT_SETUP'] = saved


def test_one_correct_code_is_not_enough_and_nothing_is_spent(app, adm, sent):
    c = app.test_client()
    _start(c)
    got = _codes_sent(sent)
    for pair in ((got[E1], '000000'), ('000000', got[E2]), (got[E1], got[E1]), (got[E2], got[E1])):
        r = _finish(c, *pair)
        assert r.status_code == 200 and GENERIC.encode() in r.data
    assert _state(app, adm['uid'])[0]['status'] == 'active'
    # The failed tries did not consume the right codes: the right pair still works.
    assert _finish(c, got[E1], got[E2]).status_code == 302
    assert _state(app, adm['uid'])[0]['status'] == 'pending'


def test_codes_cannot_be_replayed_after_success(app, adm, sent):
    c = app.test_client()
    _start(c)
    got = _codes_sent(sent)
    assert _finish(c, got[E1], got[E2]).status_code == 302
    r = _finish(c, got[E1], got[E2])                   # state is gone: back to the start
    assert r.status_code == 302 and r.headers['Location'].endswith('/admin/lost-authenticator')
    with app.app_context():
        assert verification.pending(adm['uid'], 'admin_lost_1') is None


def test_wrong_or_partial_emails_send_nothing_and_look_the_same(app, adm, sent):
    outcomes = []
    for pair in ((E1, 'other@test.edu'), ('x@test.edu', 'y@test.edu'), (E1, E2.upper() + 'x'),
                 (E1, E1)):
        c = app.test_client()
        r = _start(c, *pair)
        outcomes.append((r.status_code, r.headers.get('Location')))
    assert not sent
    # A request whose details DO match gets the same redirect as the ones that did not.
    ok = _start(app.test_client())
    assert (ok.status_code, ok.headers['Location']) == outcomes[0]
    sent.clear()
    # An unmatched request is indistinguishable at the next page too.
    c = app.test_client()
    _start(c, 'x@test.edu', 'y@test.edu')
    page = c.get('/admin/lost-authenticator/verify')
    assert page.status_code == 200
    r = _finish(c, '123456', '654321')
    assert GENERIC.encode() in r.data
    assert _state(app, adm['uid'])[0]['status'] == 'active'


def test_an_admin_with_no_authenticator_gets_no_mail(app, adm, sent):
    with app.app_context():
        auth.remove_authenticator(adm['uid'])
    c = app.test_client()
    r = _start(c)
    assert r.status_code == 302 and not sent


def test_forged_session_state_cannot_borrow_another_accounts_codes(app, adm, sent):
    c = app.test_client()
    _start(c)
    got = _codes_sent(sent)
    with app.app_context():
        teacher = db.query("SELECT id FROM users WHERE role='teacher' ORDER BY id LIMIT 1", one=True)['id']
    far = int(time.time()) + 600
    forged = [{'uid': teacher, 'exp': far},                       # a non-admin account
              {'uid': 999999, 'exp': far},                        # no such account
              {'uid': 0, 'exp': far}]
    for st in forged:
        with c.session_transaction() as s:
            s['adminlost'] = st
        r = _finish(c, got[E1], got[E2])
        assert r.status_code == 200 and GENERIC.encode() in r.data
    assert _state(app, adm['uid'])[0]['status'] == 'active'


def test_malformed_or_expired_session_state_is_discarded(app, adm, sent):
    c = app.test_client()
    for bad in ('text', {'uid': '1', 'exp': 9999999999}, {'uid': 1}, {'uid': 1, 'exp': 1},
                {'uid': adm['uid'], 'exp': int(time.time()) - 5}, ['uid', 1], None):
        with c.session_transaction() as s:
            s['adminlost'] = bad
        r = c.get('/admin/lost-authenticator/verify')
        assert r.status_code == 302 and r.headers['Location'].endswith('/admin/lost-authenticator'), bad
        r = _finish(c, '123456', '123456')
        assert r.status_code == 302
    assert _state(app, adm['uid'])[0]['status'] == 'active'


def test_a_tampered_session_cookie_is_ignored_entirely(app, adm, sent):
    c = app.test_client()
    _start(c)
    cookie = c.get_cookie('session')
    assert cookie is not None
    value = cookie.value
    flipped = value[:-3] + ('AAA' if not value.endswith('AAA') else 'BBB')
    c2 = app.test_client()
    c2.set_cookie('session', flipped)
    r = c2.get('/admin/lost-authenticator/verify')
    assert r.status_code == 302                                   # bad signature: no state at all


def test_expired_codes_are_refused(app, adm, sent):
    c = app.test_client()
    _start(c)
    got = _codes_sent(sent)
    with app.app_context():
        db.execute("UPDATE email_codes SET expires_ts=? WHERE user_id=?", (int(time.time()) - 1, adm['uid']))
    r = _finish(c, got[E1], got[E2])
    assert r.status_code == 200 and GENERIC.encode() in r.data
    assert _state(app, adm['uid'])[0]['status'] == 'active'


def test_admin_brute_force_is_limited_per_account_and_per_address(app, adm, sent):
    c = app.test_client()
    _start(c)
    got = _codes_sent(sent)
    app.config['OTP_MAX_ATTEMPTS'] = 3
    try:
        for _ in range(3):
            _finish(c, '000000', '000000')
        # Locked: even the right codes are refused until the lock clears.
        r = _finish(c, got[E1], got[E2])
        assert r.status_code == 200 and GENERIC.encode() in r.data
        assert _state(app, adm['uid'])[0]['status'] == 'active'
        assert _audit(app, 'admin.lost_authenticator_failed', adm['uid'])
    finally:
        app.config['OTP_MAX_ATTEMPTS'] = 5
    # The e-mail codes also destroy themselves after too many wrong guesses.
    with app.app_context():
        clear_login_failures(f'adminlostv:{adm["uid"]}')
        for _ in range(verification.MAX_ATTEMPTS):
            verification.check(adm['uid'], 'admin_lost_1', '000000', consume=False)
        assert verification.pending(adm['uid'], 'admin_lost_1') is None


def test_admin_start_is_rate_limited_per_address(app, adm, sent):
    app.config['LOST_DEVICE_IP_MAX_ATTEMPTS'] = 2
    try:
        c = app.test_client()
        for _ in range(2):
            _start(c, 'x@test.edu', 'y@test.edu')
        r = _start(c, 'x@test.edu', 'y@test.edu')
        assert b'Too many attempts' in r.data
    finally:
        app.config['LOST_DEVICE_IP_MAX_ATTEMPTS'] = 10


def test_admin_concurrent_completion_removes_once(app, adm, sent):
    results, errors = [], []
    gate = threading.Barrier(4)

    def worker():
        try:
            with app.app_context():
                gate.wait(timeout=10)
                results.append(auth.apply_lost_device_reset(adm['uid']))
        except Exception as exc:                                  # pragma: no cover
            errors.append(repr(exc))
    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join(timeout=30) for t in ts]
    assert not errors, errors
    assert results.count(True) == 1


def test_a_mail_failure_at_the_start_leaves_no_codes_behind(app, adm, monkeypatch):
    monkeypatch.setattr(mailer, 'send_email', lambda *a, **k: False)
    c = app.test_client()
    r = _start(c)
    assert r.status_code == 200
    with app.app_context():
        assert verification.pending(adm['uid'], 'admin_lost_1') is None
        assert verification.pending(adm['uid'], 'admin_lost_2') is None


def test_admin_normal_forgot_password_page_is_unchanged(app):
    c = app.test_client()
    assert c.get('/admin/forgot-password').status_code == 200
    assert b'Administrator recovery' in c.get('/admin/forgot-password').data   # new link only
    assert c.get('/forgot-password').status_code == 200
