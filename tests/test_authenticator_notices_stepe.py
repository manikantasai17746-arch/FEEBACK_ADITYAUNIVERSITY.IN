"""Step E: administrator reset of a student's authenticator, and e-mail notices.

* the reset removes the authenticator and its backup codes, students only;
* the student must set up again at their next login;
* a notice goes to the registered address when an app is set up or reset;
* notices never contain a secret, a setup key, or a code;
* a mail failure never blocks the reset or the activation.
"""
import re
import secrets as _secrets
import time

import pyotp
import pytest

from feedback_system import authenticator, db, mailer
from helpers import login_student

ROLL = '25B11CS016'            # a student no other test depends on


@pytest.fixture()
def sent(app, monkeypatch):
    box = []

    def fake_send(to, subject, text, html=None, attachments=None):
        box.append({'to': to, 'subject': subject, 'text': text})
        return True

    monkeypatch.setattr(mailer, 'send_email', fake_send)
    yield box


@pytest.fixture()
def setup_on(app):
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


def _token(client):
    with client.session_transaction() as sess:
        tok = sess.get('token')
        if not tok:
            tok = _secrets.token_hex(16)
            sess['token'] = tok
        return tok


def _set_email(app, uid):
    with app.app_context():
        db.execute("UPDATE users SET email='student@example.com', email_verified=1 WHERE id=?",
                   (uid,))


def _enroll(app, uid):
    """An active authenticator, enrolled in the past so later codes are not replays."""
    past = time.time() - 120
    with app.app_context():
        secret = authenticator.begin_enrollment(uid)
        assert authenticator.confirm_enrollment(uid, pyotp.TOTP(secret).at(past), now=past)
        row = db.query('SELECT secret_enc FROM student_authenticators WHERE user_id=?',
                       (uid,), one=True)
        return authenticator.decrypt_secret(row['secret_enc'])


def _rows(app, uid):
    with app.app_context():
        auth = db.query('SELECT COUNT(*) c FROM student_authenticators WHERE user_id=?',
                        (uid,), one=True)['c']
        codes = db.query('SELECT COUNT(*) c FROM student_backup_codes WHERE user_id=?',
                         (uid,), one=True)['c']
        return auth, codes


def _reset(admin, uid):
    return admin.post(f'/admin/students/{uid}/reset-authenticator',
                      data={'_csrf': _token(admin)})


# --------------------------------------------------------------------------- #
# The reset
# --------------------------------------------------------------------------- #
def test_reset_removes_the_authenticator_and_backup_codes(app, admin, student):
    _enroll(app, student)
    with app.app_context():
        authenticator.store_backup_codes(student, authenticator.generate_backup_codes())
    assert _rows(app, student) == (1, authenticator.BACKUP_CODE_COUNT)
    assert _reset(admin, student).status_code == 302
    assert _rows(app, student) == (0, 0)


def test_reset_sends_a_notice_that_contains_no_secret_or_code(app, admin, student, sent):
    secret = _enroll(app, student)
    _set_email(app, student)
    _reset(admin, student)
    notices = [m for m in sent if 'reset' in m['subject'].lower()]
    assert len(notices) == 1 and notices[0]['to'] == 'student@example.com'
    text = notices[0]['text']
    assert secret not in text and secret[:8] not in text
    assert not re.search(r'\b\d{6}\b', text)
    assert 'otpauth' not in text.lower()


def test_after_a_reset_the_student_must_set_up_again(app, admin, student, sent):
    _enroll(app, student)
    _set_email(app, student)
    # Reset first: with the first-login setup on, the admin session would be sent to the
    # admin setup page instead of reaching the reset route.
    assert _reset(admin, student).status_code == 302
    saved = app.config.get('REQUIRE_ACCOUNT_SETUP')
    app.config['REQUIRE_ACCOUNT_SETUP'] = True
    try:
        client = app.test_client()      # a separate client: the admin fixture uses the shared one
        assert login_student(client, ROLL).status_code == 302
        r = client.get('/student/')
        assert r.status_code == 302 and 'setup/authenticator' in r.headers['Location']
    finally:
        app.config['REQUIRE_ACCOUNT_SETUP'] = saved


def test_reset_is_limited_to_students(app, admin):
    with app.app_context():
        teacher = db.query("SELECT id FROM users WHERE role='teacher' LIMIT 1", one=True)
    assert _reset(admin, teacher['id']).status_code == 404


def test_mail_failure_does_not_block_the_reset(app, admin, student, monkeypatch):
    _enroll(app, student)
    _set_email(app, student)
    monkeypatch.setattr(mailer, 'send_email', lambda *a, **k: False)
    assert _reset(admin, student).status_code == 302
    assert _rows(app, student) == (0, 0)


# --------------------------------------------------------------------------- #
# The notice when a student sets up an app
# --------------------------------------------------------------------------- #
def test_setting_up_an_app_sends_a_notice_without_the_secret(app, client, student, sent, setup_on):
    _set_email(app, student)
    assert login_student(client, ROLL).status_code == 302
    tok = _token(client)
    client.post('/setup/authenticator', data={'_csrf': tok, 'action': 'send'})
    code = [m for m in sent if 'set up' in m['subject'].lower() and m.get('to')][-1]
    emailed = re.search(r'\b(\d{6})\b', code['text']).group(1)
    client.post('/setup/authenticator/verify', data={'_csrf': tok, 'code': emailed})
    client.get('/setup/authenticator')
    with app.app_context():
        secret = authenticator.pending_secret(student)
    assert secret
    app_code = pyotp.TOTP(secret).now()
    r = client.post('/setup/authenticator/confirm', data={'_csrf': tok, 'code': app_code})
    assert r.status_code == 302
    with app.app_context():
        assert authenticator.is_active(student)
    added = [m for m in sent if 'set up on your feedback account' in m['subject']]
    assert len(added) == 1 and added[0]['to'] == 'student@example.com'
    assert secret not in added[0]['text'] and app_code not in added[0]['text']


def test_no_notice_is_sent_without_a_registered_address(sent):
    assert mailer.authenticator_notice(None, 'Student', 'added') is False
    assert sent == []
