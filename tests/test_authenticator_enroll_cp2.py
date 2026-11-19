"""Authenticator enrollment for all five roles (all-roles upgrade, Checkpoint 2).

Covers: enrollment for student/teacher/HOD/Dean/admin, password re-entry, one-time backup
codes, the Account Security page, per-role enforcement and grace periods, notices, and
that nothing here can remove an existing authenticator.
"""
import re
import time
from datetime import date, timedelta

import pyotp
import pytest

from feedback_system import authenticator as auth
from feedback_system import db, mailer
from feedback_system.security import clear_login_failures, hash_password
from helpers import STUDENT_PERSONAL_PASSWORD, ensure_personal_student_password, login

ROLL = '25B11CS018'
PW = 'Personal-pass-1'
ROLES = ['student', 'teacher', 'hod', 'dean', 'admin']
LOGIN = {
    'student': ('/student/login', lambda u, p: {'roll': u, 'password': p}),
    'teacher': ('/teacher/login', lambda u, p: {'emp_code': u, 'password': p}),
    'hod': ('/hod/login', lambda u, p: {'emp_code': u, 'password': p}),
    'dean': ('/dean/login', lambda u, p: {'emp_code': u, 'password': p}),
    'admin': ('/admin/login', lambda u, p: {'email': u, 'password': p}),
}


def _csrf(client):
    import secrets as s
    with client.session_transaction() as sess:
        tok = sess.get('token')
        if not tok:
            tok = sess['token'] = s.token_hex(16)
        return tok


def _post(client, path, **data):
    return client.post(path, data={'_csrf': _csrf(client), **data})


def _wipe(app, uid):
    with app.app_context():
        for t in ('student_backup_codes', 'student_authenticators', 'email_codes'):
            db.execute(f'DELETE FROM {t} WHERE user_id=?', (uid,))
        auth.clear_otp_failures(uid)
        clear_login_failures(f'authsetup:{uid}')


@pytest.fixture()
def acct(app, request):
    role = request.param
    with app.app_context():
        if role == 'student':
            ensure_personal_student_password(app, ROLL)
            ident, pw = ROLL, STUDENT_PERSONAL_PASSWORD
            where = ("role='student' AND username=?", (ROLL,))
        elif role == 'teacher':
            db.execute("UPDATE users SET password_hash=?, must_change_password=0, "
                       "first_login_complete=1 WHERE emp_code='6817'", (hash_password(PW),))
            ident, pw, where = '6817', PW, ("emp_code='6817'", ())
        elif role in ('hod', 'dean'):
            ident = f'CP2{role.upper()}'
            if not db.query('SELECT id FROM users WHERE username=?', (ident,), one=True):
                db.execute("INSERT INTO users(role, username, emp_code, name, email, password_hash,"
                           " is_active, onboarding_complete) VALUES(?,?,?,?,?,?,1,1)",
                           (role, ident, ident, f'CP2 {role}', f'{role}@test.edu', hash_password(PW)))
            pw, where = PW, ("username=?", (ident,))
        else:
            ident, pw, where = 'admin@college.edu', 'admin123', ("role='admin' ORDER BY id LIMIT 1", ())
        row = db.query(f'SELECT id, email, name FROM users WHERE {where[0]}', where[1], one=True)
        uid = row['id']
        if not (row['email'] or '').strip():
            db.execute('UPDATE users SET email=? WHERE id=?', (f'{role}@test.edu', uid))
    _wipe(app, uid)
    yield {'role': role, 'uid': uid, 'ident': ident, 'pw': pw}
    _wipe(app, uid)


def _sign_in(client, a):
    path, mk = LOGIN[a['role']]
    assert login(client, path, mk(a['ident'], a['pw'])).status_code == 302


@pytest.fixture()
def sent(monkeypatch):
    box = []

    def fake(to, subject, text, html=None, attachments=None):
        box.append({'to': to, 'subject': subject, 'text': text,
                    'code': (re.search(r'\b(\d{6})\b', text) or [None, None])[1]})
        return True
    monkeypatch.setattr(mailer, 'send_email', fake)
    return box


def _enroll(client, app, a, sent):
    """Run the whole enrollment. Returns (secret, response of the confirm step)."""
    _post(client, '/setup/authenticator', action='password', password=a['pw'])
    if a['role'] == 'student':
        _post(client, '/setup/authenticator', action='send')
        _post(client, '/setup/authenticator/verify', code=sent[-1]['code'])
    client.get('/setup/authenticator')
    with app.app_context():
        secret = auth.pending_secret(a['uid'])
    assert secret, 'the secret should be shown after the proofs'
    r = _post(client, '/setup/authenticator/confirm', code=pyotp.TOTP(secret).now())
    return secret, r


def _codes_in(html):
    return re.findall(r'\b[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}\b', html)


# --------------------------------------------------------------------------- #
# Enforcement policy (pure function)
# --------------------------------------------------------------------------- #
def test_policy_default_is_students_only_and_unchanged():
    cfg = {'TOTP_ENCRYPTION_KEY': 'k'}
    assert auth.enforcement_state('student', cfg)[0] == 'required'
    for r in ('teacher', 'hod', 'dean', 'admin'):
        assert auth.enforcement_state(r, cfg)[0] == 'off'


def test_policy_roles_grace_and_guards():
    today = date(2026, 12, 1)
    cfg = {'TOTP_ENCRYPTION_KEY': 'k', 'AUTHENTICATOR_ENFORCE_ROLES': 'student, teacher,hod,admin',
           'AUTHENTICATOR_GRACE_UNTIL': 'teacher=2026-12-31,hod=2026-12-01,dean=2027-01-01'}
    assert auth.enforcement_state('teacher', cfg, today) == ('grace', date(2026, 12, 31))
    assert auth.enforcement_state('hod', cfg, today)[0] == 'required'     # from that date
    assert auth.enforcement_state('dean', cfg, today)[0] == 'off'         # not listed
    assert auth.enforcement_state('admin', cfg, today)[0] == 'off'        # recovery not built
    del cfg['TOTP_ENCRYPTION_KEY']
    assert auth.enforcement_state('teacher', cfg, today)[0] == 'off'      # no key: never lock out
    assert auth.enforcement_state('student', cfg, today)[0] == 'required'  # unchanged behaviour
    bad = {'TOTP_ENCRYPTION_KEY': 'k', 'AUTHENTICATOR_ENFORCE_ROLES': 'teacher',
           'AUTHENTICATOR_GRACE_UNTIL': 'teacher=not-a-date'}
    assert auth.enforcement_state('teacher', bad, today)[0] == 'required'


# --------------------------------------------------------------------------- #
# Enrollment for each of the five roles
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('acct', ROLES, indirect=True)
def test_enrollment_works_for_every_role_and_shows_ten_codes_once(app, client, acct, sent):
    _sign_in(client, acct)
    secret, r = _enroll(client, app, acct, sent)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    codes = _codes_in(html)
    assert len(codes) == auth.BACKUP_CODE_COUNT == len(set(codes))
    assert r.headers['Cache-Control'] == 'no-store'
    with app.app_context():
        assert auth.is_active(acct['uid'])
        assert auth.backup_codes_remaining(acct['uid']) == 10
        stored = ' '.join(x['code_hash'] for x in db.query(
            'SELECT code_hash FROM student_backup_codes WHERE user_id=?', (acct['uid'],)))
        enc = db.query('SELECT secret_enc FROM student_authenticators WHERE user_id=?',
                       (acct['uid'],), one=True)['secret_enc']
    assert all(c not in stored and c.replace('-', '') not in stored for c in codes)
    assert secret not in enc
    # Nothing secret is kept in the session, and the codes never come back.
    with client.session_transaction() as sess:
        blob = str(dict(sess))
    assert secret not in blob and not any(c in blob for c in codes)
    for path in ('/account/security', '/setup/authenticator'):
        page = client.get(path, follow_redirects=True).get_data(as_text=True)
        assert not any(c in page for c in codes) and secret not in page
    # Re-submitting the same confirm (browser refresh) cannot show codes again.
    again = _post(client, '/setup/authenticator/confirm', code=pyotp.TOTP(secret).now())
    assert again.status_code == 302 and not _codes_in(again.get_data(as_text=True))


@pytest.mark.parametrize('acct', ROLES, indirect=True)
def test_qr_and_key_are_shown_and_compatible_with_authenticator_apps(app, client, acct, sent):
    _sign_in(client, acct)
    _post(client, '/setup/authenticator', action='password', password=acct['pw'])
    if acct['role'] == 'student':
        _post(client, '/setup/authenticator', action='send')
        _post(client, '/setup/authenticator/verify', code=sent[-1]['code'])
    html = client.get('/setup/authenticator').get_data(as_text=True)
    assert 'setup-key' in html and 'otpauth://totp/' in html
    assert 'issuer=Aditya' in html and 'secret=' in html            # standard otpauth URI
    assert '<svg' in html                                            # the QR code


# --------------------------------------------------------------------------- #
# Password re-entry
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('acct', ['teacher', 'hod'], indirect=True)
def test_no_secret_without_the_password(app, client, acct):
    _sign_in(client, acct)
    html = client.get('/setup/authenticator').get_data(as_text=True)
    assert 'Current password' in html and 'setup-key' not in html
    r = _post(client, '/setup/authenticator', action='password', password='wrong-password')
    assert b'setup-key' not in r.data and b'not correct' in r.data
    with app.app_context():
        assert auth.pending_secret(acct['uid']) is None


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_wrong_passwords_are_rate_limited(app, client, acct):
    _sign_in(client, acct)
    app.config['OTP_MAX_ATTEMPTS'] = 3
    try:
        for _ in range(4):
            _post(client, '/setup/authenticator', action='password', password='nope')
        r = _post(client, '/setup/authenticator', action='password', password=acct['pw'])
        assert b'Too many' in r.data and b'setup-key' not in r.data
    finally:
        app.config['OTP_MAX_ATTEMPTS'] = 5


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_correct_code_is_refused_without_a_password_proof(app, client, acct):
    _sign_in(client, acct)
    with app.app_context():
        secret = auth.begin_enrollment(acct['uid'])
    r = _post(client, '/setup/authenticator/confirm', code=pyotp.TOTP(secret).now())
    assert r.status_code == 302
    with app.app_context():
        assert not auth.is_active(acct['uid'])


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_an_expired_password_proof_is_refused(app, client, acct):
    _sign_in(client, acct)
    _post(client, '/setup/authenticator', action='password', password=acct['pw'])
    client.get('/setup/authenticator')
    with app.app_context():
        secret = auth.pending_secret(acct['uid'])
    with client.session_transaction() as sess:
        sess['auth_pw_until'] = int(time.time()) - 1
    _post(client, '/setup/authenticator/confirm', code=pyotp.TOTP(secret).now())
    with app.app_context():
        assert not auth.is_active(acct['uid'])


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_students_also_need_the_emailed_code(app, client, acct, sent):
    _sign_in(client, acct)
    _post(client, '/setup/authenticator', action='password', password=acct['pw'])
    html = client.get('/setup/authenticator').get_data(as_text=True)
    assert 'Email me a code' in html and 'setup-key' not in html


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_a_user_on_a_temporary_password_cannot_enrol(app, client, acct):
    _sign_in(client, acct)
    with app.app_context():
        db.execute('UPDATE users SET must_change_password=1 WHERE id=?', (acct['uid'],))
    try:
        r = client.get('/setup/authenticator')
        assert r.status_code == 302 and 'change-password' in r.headers['Location']
    finally:
        with app.app_context():
            db.execute('UPDATE users SET must_change_password=0 WHERE id=?', (acct['uid'],))


def test_a_guest_is_sent_to_sign_in(client):
    for path in ('/setup/authenticator', '/account/security'):
        r = client.get(path)
        assert r.status_code == 302 and 'login' in r.headers['Location']
    r = client.post('/account/security/backup-codes', data={})
    assert r.status_code in (302, 400, 403)


# --------------------------------------------------------------------------- #
# Account Security page and backup-code regeneration
# --------------------------------------------------------------------------- #
def _enrolled(client, app, acct, sent):
    _sign_in(client, acct)
    secret, r = _enroll(client, app, acct, sent)
    return secret, _codes_in(r.get_data(as_text=True))


@pytest.mark.parametrize('acct', ROLES, indirect=True)
def test_security_page_shows_status_and_the_count(app, client, acct, sent):
    _sign_in(client, acct)
    html = client.get('/account/security').get_data(as_text=True)
    assert 'not</strong> set up' in html
    assert 'Set up authenticator app' in html
    secret, codes = _enrolled_after_signin(client, app, acct, sent)
    html = client.get('/account/security').get_data(as_text=True)
    assert 'is <strong>set up' in html and '10 of 10' in html
    with app.app_context():
        assert auth.consume_backup_code(acct['uid'], codes[0])
    assert '9 of 10' in client.get('/account/security').get_data(as_text=True)
    assert ('Administrator note' in html) == (acct['role'] == 'admin')


def _enrolled_after_signin(client, app, acct, sent):
    secret, r = _enroll(client, app, acct, sent)
    return secret, _codes_in(r.get_data(as_text=True))


@pytest.mark.parametrize('acct', ['teacher', 'admin'], indirect=True)
def test_regenerating_needs_a_current_app_code_and_replaces_every_old_code(app, client, acct, sent):
    _sign_in(client, acct)
    secret, old = _enrolled_after_signin(client, app, acct, sent)
    # Wrong code, a backup code and junk are all refused; nothing is spent or replaced.
    for bad in ('000000', old[0], 'abc', ''):
        r = _post(client, '/account/security/backup-codes', code=bad)
        assert r.status_code == 302 and not _codes_in(r.get_data(as_text=True))
    with app.app_context():
        assert auth.backup_codes_remaining(acct['uid']) == 10     # the backup code was not spent
    nxt = pyotp.TOTP(secret).at(time.time() + 30)
    r = _post(client, '/account/security/backup-codes', code=nxt)
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    new = _codes_in(r.get_data(as_text=True))
    assert len(new) == 10 and set(new).isdisjoint(old)
    with app.app_context():
        assert not auth.consume_backup_code(acct['uid'], old[1])
        assert auth.consume_backup_code(acct['uid'], new[0])
    assert any('New backup codes' in m['subject'] for m in sent)
    assert not any(c in m['text'] for m in sent for c in new + old)


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_regeneration_without_an_authenticator_goes_to_setup(app, client, acct):
    _sign_in(client, acct)
    r = _post(client, '/account/security/backup-codes', code='123456')
    assert r.status_code == 302 and 'setup/authenticator' in r.headers['Location']


# --------------------------------------------------------------------------- #
# Notices
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('acct', ['hod'], indirect=True)
def test_enrollment_sends_a_notice_without_any_secret_or_code(app, client, acct, sent):
    _sign_in(client, acct)
    secret, codes = _enrolled_after_signin(client, app, acct, sent)
    notice = [m for m in sent if 'authenticator' in m['subject'].lower()]
    assert notice and notice[0]['to'] == 'hod@test.edu'
    for m in notice:
        assert secret not in m['text'] and not any(c in m['text'] for c in codes)
        assert 'otpauth' not in m['text'].lower()


@pytest.mark.parametrize('acct', ['dean'], indirect=True)
def test_a_failed_notice_never_blocks_enrollment(app, client, acct, monkeypatch):
    monkeypatch.setattr(mailer, 'send_email', lambda *a, **k: False)
    _sign_in(client, acct)
    secret, r = _enroll(client, app, acct, [])
    assert r.status_code == 200 and len(_codes_in(r.get_data(as_text=True))) == 10
    with app.app_context():
        assert auth.is_active(acct['uid'])
        assert db.query("SELECT 1 FROM audit_log WHERE action='user.authenticator_notice_not_sent'"
                        " AND entity_id=?", (acct['uid'],), one=True)


# --------------------------------------------------------------------------- #
# Enforcement in the sign-in gate
# --------------------------------------------------------------------------- #
@pytest.fixture()
def policy(app):
    saved = {k: app.config.get(k) for k in ('REQUIRE_ACCOUNT_SETUP', 'AUTHENTICATOR_ENFORCE_ROLES',
                                            'AUTHENTICATOR_GRACE_UNTIL')}
    app.config['REQUIRE_ACCOUNT_SETUP'] = True

    def set_(roles, grace=''):
        app.config['AUTHENTICATOR_ENFORCE_ROLES'] = roles
        app.config['AUTHENTICATOR_GRACE_UNTIL'] = grace
    yield set_
    app.config.update(saved)


def _home(role):
    return {'teacher': '/teacher/', 'hod': '/hod/', 'dean': '/dean/', 'admin': '/admin/'}[role]


@pytest.mark.parametrize('acct', ['teacher', 'hod', 'dean'], indirect=True)
def test_a_listed_role_must_enrol_before_using_the_portal(app, client, acct, policy):
    policy(acct['role'])
    _sign_in(client, acct)
    r = client.get(_home(acct['role']))
    assert r.status_code == 302 and 'setup/authenticator' in r.headers['Location']


@pytest.mark.parametrize('acct', ['teacher', 'hod', 'dean'], indirect=True)
def test_an_unlisted_role_is_not_forced(app, client, acct, policy):
    policy('student')
    _sign_in(client, acct)
    r = client.get(_home(acct['role']))
    assert 'setup/authenticator' not in (r.headers.get('Location') or '')


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_grace_period_reminds_once_and_does_not_block(app, client, acct, policy):
    until = (date.today() + timedelta(days=10)).isoformat()
    policy('teacher', f'teacher={until}')
    _sign_in(client, acct)
    r = client.get('/teacher/', follow_redirects=True)
    assert 'setup/authenticator' not in r.request.path and b'mandatory on' in r.data
    assert b'mandatory on' not in client.get('/teacher/', follow_redirects=True).data   # once
    policy('teacher', f'teacher={(date.today() - timedelta(days=1)).isoformat()}')
    r = client.get('/teacher/')
    assert r.status_code == 302 and 'setup/authenticator' in r.headers['Location']


@pytest.mark.parametrize('acct', ['admin'], indirect=True)
def test_admin_enforcement_stays_disabled_even_when_configured(app, client, acct, policy):
    policy('student,teacher,hod,dean,admin')
    _sign_in(client, acct)
    # The administrator's own setup step may still apply, but never the authenticator.
    r = client.get('/admin/')
    assert 'setup/authenticator' not in (r.headers.get('Location') or '')


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_enforcement_is_skipped_for_non_students_when_the_key_is_missing(app, client, acct, policy):
    policy('teacher')
    saved = app.config['TOTP_ENCRYPTION_KEY']
    app.config['TOTP_ENCRYPTION_KEY'] = None
    try:
        _sign_in(client, acct)
        r = client.get('/teacher/')
        assert 'setup/authenticator' not in (r.headers.get('Location') or '')
    finally:
        app.config['TOTP_ENCRYPTION_KEY'] = saved


# --------------------------------------------------------------------------- #
# Nothing here removes an authenticator
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_changing_the_password_keeps_the_authenticator(app, client, acct, sent):
    _sign_in(client, acct)
    secret, codes = _enrolled_after_signin(client, app, acct, sent)
    r = _post(client, '/change-password', current_password=acct['pw'],
              new_password='Another-pass-2', confirm_password='Another-pass-2')
    assert r.status_code == 302
    with app.app_context():
        db.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(PW), acct['uid']))
        assert auth.is_active(acct['uid']) and auth.backup_codes_remaining(acct['uid']) == 10


def test_no_route_in_this_checkpoint_removes_an_authenticator(app):
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith('account_security.') or rule.endpoint.startswith('auth.setup_authenticator'):
            assert not re.search(r'remove|delete|reset|replace|lost', rule.rule), rule.rule


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_an_enrolled_user_cannot_start_a_second_enrollment(app, client, acct, sent):
    _sign_in(client, acct)
    secret, _ = _enrolled_after_signin(client, app, acct, sent)
    r = client.get('/setup/authenticator')
    assert r.status_code == 302 and 'account/security' in r.headers['Location']
    with app.app_context():
        assert auth.decrypt_secret(db.query(
            'SELECT secret_enc FROM student_authenticators WHERE user_id=?',
            (acct['uid'],), one=True)['secret_enc']) == secret
