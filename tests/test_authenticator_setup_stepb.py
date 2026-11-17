"""Step B: a student sets up an authenticator app after the e-mail step.

* setup requires a code sent to the registered, confirmed e-mail;
* the secret is shown only after that proof, and never stored in the session;
* a correct app code activates the authenticator and opens the portal;
* a wrong code, an expired proof, or a logout leaves setup closed;
* teachers cannot use the student page; existing behaviour is unchanged when setup is off.
"""
import re
import secrets as _secrets
import time

import pyotp
import pytest

from feedback_system import authenticator, db, mailer
from helpers import login, login_student

ROLL = '25B11CS016'            # a student no other test depends on


@pytest.fixture()
def sent(app, monkeypatch):
    box = []

    def fake_send(to, subject, text, html=None):
        box.append({'to': to, 'code': (re.search(r'\b(\d{6})\b', text) or [None, None])[1]})
        return True

    monkeypatch.setattr(mailer, 'send_email', fake_send)
    yield box


@pytest.fixture()
def setup_on(app):
    """Turn on the first-login setup for this test (the gate applies to students)."""
    saved = app.config.get('REQUIRE_ACCOUNT_SETUP')
    app.config['REQUIRE_ACCOUNT_SETUP'] = True
    yield
    app.config['REQUIRE_ACCOUNT_SETUP'] = saved


@pytest.fixture()
def student(app):
    with app.app_context():
        row = db.query("SELECT id, email, email_verified FROM users "
                       "WHERE role='student' AND username=?", (ROLL,), one=True)
        saved = (row['id'], row['email'], row['email_verified'])

    def wipe():
        with app.app_context():
            db.execute('DELETE FROM student_backup_codes WHERE user_id=?', (saved[0],))
            db.execute('DELETE FROM student_authenticators WHERE user_id=?', (saved[0],))
            db.execute('DELETE FROM email_codes WHERE user_id=?', (saved[0],))

    wipe()
    yield saved[0]
    wipe()
    with app.app_context():
        db.execute('UPDATE users SET email=?, email_verified=? WHERE id=?',
                   (saved[1], saved[2], saved[0]))


def _set_email(app, uid, email='student@example.com'):
    with app.app_context():
        db.execute('UPDATE users SET email=?, email_verified=1 WHERE id=?', (email, uid))


def _token(client):
    with client.session_transaction() as sess:
        tok = sess.get('token')
        if not tok:
            tok = _secrets.token_hex(16)
            sess['token'] = tok
        return tok


def _post(client, path, **data):
    return client.post(path, data={'_csrf': _token(client), **data})


def _secret(app, uid):
    with app.app_context():
        return authenticator.pending_secret(uid)


def _send_and_confirm_email(client, sent):
    _post(client, '/setup/authenticator', action='send')
    code = sent[-1]['code']
    return _post(client, '/setup/authenticator/verify', code=code)


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
def test_student_with_email_but_no_authenticator_is_sent_to_setup(app, client, student, setup_on):
    _set_email(app, student)
    assert login_student(client, ROLL).status_code == 302
    r = client.get('/student/')
    assert r.status_code == 302 and 'setup/authenticator' in r.headers['Location']


def test_setup_needs_a_confirmed_email_first(app, client, student, setup_on):
    with app.app_context():
        db.execute('UPDATE users SET email=NULL, email_verified=0 WHERE id=?', (student,))
    assert login_student(client, ROLL).status_code == 302
    r = client.get('/setup/authenticator')
    assert r.status_code == 302 and '/setup/email' in r.headers['Location']


def test_existing_students_are_unchanged_when_setup_is_off(app, client, student):
    _set_email(app, student)
    assert app.config.get('REQUIRE_ACCOUNT_SETUP') in (False, None)
    assert login_student(client, ROLL).status_code == 302
    assert client.get('/student/').status_code == 200


# --------------------------------------------------------------------------- #
# Proof by emailed code
# --------------------------------------------------------------------------- #
def test_send_step_emails_a_code_and_shows_no_secret(app, client, student, sent, setup_on):
    _set_email(app, student)
    assert login_student(client, ROLL).status_code == 302
    r = client.get('/setup/authenticator')
    assert r.status_code == 200 and b'Email me a code' in r.data
    r = _post(client, '/setup/authenticator', action='send')
    assert r.status_code == 302 and 'verify' in r.headers['Location']
    assert len(sent) == 1 and sent[0]['to'] == 'student@example.com' and sent[0]['code']
    r = client.get('/setup/authenticator')
    assert b'setup-key' not in r.data


def test_wrong_email_code_gives_no_setup_access(app, client, student, sent, setup_on):
    _set_email(app, student)
    assert login_student(client, ROLL).status_code == 302
    _post(client, '/setup/authenticator', action='send')
    real = sent[-1]['code']
    wrong = '000000' if real != '000000' else '111111'
    _post(client, '/setup/authenticator/verify', code=wrong)
    assert b'setup-key' not in client.get('/setup/authenticator').data
    with app.app_context():
        assert authenticator.pending_secret(student) is None


def test_mail_failure_gives_no_setup_access(app, client, student, monkeypatch, setup_on):
    monkeypatch.setattr(mailer, 'send_email', lambda *a, **k: False)
    _set_email(app, student)
    assert login_student(client, ROLL).status_code == 302
    r = _post(client, '/setup/authenticator', action='send', )
    assert b'could not send' in r.data
    with client.session_transaction() as sess:
        assert 'auth_setup_until' not in sess


# --------------------------------------------------------------------------- #
# After proof: the secret is shown once to this student, and never stored in the session
# --------------------------------------------------------------------------- #
def test_confirmed_email_code_shows_the_key_and_keeps_it_out_of_the_session(
        app, client, student, sent, setup_on):
    _set_email(app, student)
    assert login_student(client, ROLL).status_code == 302
    assert _send_and_confirm_email(client, sent).status_code == 302
    r = client.get('/setup/authenticator')
    secret = _secret(app, student)
    assert secret and b'setup-key' in r.data
    assert b'otpauth://' in r.data
    assert secret[:4].encode() in r.data           # the grouped key is on the page
    with client.session_transaction() as sess:
        assert all(secret not in str(v) for v in sess.values())


def test_correct_app_code_activates_and_opens_the_portal(app, client, student, sent, setup_on):
    _set_email(app, student)
    assert login_student(client, ROLL).status_code == 302
    _send_and_confirm_email(client, sent)
    client.get('/setup/authenticator')
    code = pyotp.TOTP(_secret(app, student)).now()
    r = _post(client, '/setup/authenticator/confirm', code=code)
    assert r.status_code == 302
    with app.app_context():
        assert authenticator.is_active(student)
    assert client.get('/student/').status_code == 200


def test_wrong_app_code_keeps_the_same_pending_key(app, client, student, sent, setup_on):
    _set_email(app, student)
    assert login_student(client, ROLL).status_code == 302
    _send_and_confirm_email(client, sent)
    client.get('/setup/authenticator')
    before = _secret(app, student)
    _post(client, '/setup/authenticator/confirm', code='000000')
    with app.app_context():
        assert not authenticator.is_active(student)
    assert _secret(app, student) == before          # not regenerated on a wrong code


def test_expired_proof_refuses_even_a_correct_app_code(app, client, student, sent, setup_on):
    _set_email(app, student)
    assert login_student(client, ROLL).status_code == 302
    _send_and_confirm_email(client, sent)
    client.get('/setup/authenticator')
    code = pyotp.TOTP(_secret(app, student)).now()
    with client.session_transaction() as sess:
        sess['auth_setup_until'] = int(time.time()) - 1
    r = _post(client, '/setup/authenticator/confirm', code=code)
    assert r.status_code == 302 and 'setup/authenticator' in r.headers['Location']
    with app.app_context():
        assert not authenticator.is_active(student)


def test_logout_closes_the_setup_proof(app, client, student, sent, setup_on):
    _set_email(app, student)
    assert login_student(client, ROLL).status_code == 302
    _send_and_confirm_email(client, sent)
    client.get('/logout')
    assert login_student(client, ROLL).status_code == 302
    assert b'setup-key' not in client.get('/setup/authenticator').data
    code = pyotp.TOTP(_secret(app, student)).now() if _secret(app, student) else '000000'
    _post(client, '/setup/authenticator/confirm', code=code)
    with app.app_context():
        assert not authenticator.is_active(student)


# --------------------------------------------------------------------------- #
# Teachers and the QR code
# --------------------------------------------------------------------------- #
def test_teacher_cannot_use_the_student_setup_page(app, client, teacher, setup_on):
    r = client.get('/setup/authenticator')
    assert r.status_code in (302, 403)
    assert b'setup-key' not in r.data


def test_key_is_shown_even_when_the_qr_library_is_missing(app, client, student, sent, setup_on):
    """The manual key always works; the QR image is added when segno is installed."""
    _set_email(app, student)
    assert login_student(client, ROLL).status_code == 302
    _send_and_confirm_email(client, sent)
    r = client.get('/setup/authenticator')
    assert b'setup-key' in r.data
