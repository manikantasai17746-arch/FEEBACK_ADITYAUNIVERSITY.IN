"""Step A: a confirmed student email is locked. Only an administrator can clear it.

* the setup page refuses to send a code once an email is confirmed;
* a code sent before the lock cannot replace the confirmed email;
* an administrator can clear a student's email; the student can then set one again;
* only administrators can clear it, and only student accounts are affected.
"""
import re

import pytest

from feedback_system import db, mailer
from helpers import login_student


@pytest.fixture()
def sent(app, monkeypatch):
    box = []

    def fake_send(to, subject, text, html=None):
        box.append({'to': to, 'code': (re.search(r'\b(\d{6})\b', text) or [None, None])[1]})
        return True

    # Not turning on the first-login gate here: it would also redirect the admin session
    # (the gate applies to every role). The setup POST routes are tested directly.
    monkeypatch.setattr(mailer, 'send_email', fake_send)
    yield box


ROLL = '25B11CS016'            # a student no other test depends on


@pytest.fixture()
def student(app):
    with app.app_context():
        row = db.query("SELECT id, email, email_verified FROM users "
                       "WHERE role='student' AND username=?", (ROLL,), one=True)
        saved = (row['id'], row['email'], row['email_verified'])
    yield saved[0]
    with app.app_context():
        db.execute('UPDATE users SET email=?, email_verified=? WHERE id=?',
                   (saved[1], saved[2], saved[0]))
        db.execute('DELETE FROM email_codes WHERE user_id=?', (saved[0],))


def _college(app):
    return f"{ROLL.lower()}@{app.config['STUDENT_EMAIL_DOMAIN']}"


def _set_email(app, uid, email, verified=1):
    with app.app_context():
        db.execute('UPDATE users SET email=?, email_verified=? WHERE id=?', (email, verified, uid))


def _email_of(app, uid):
    with app.app_context():
        return db.query('SELECT email FROM users WHERE id=?', (uid,), one=True)['email']


def _session_token(client):
    with client.session_transaction() as sess:
        tok = sess.get('token')
        if not tok:
            import secrets
            tok = secrets.token_hex(16)
            sess['token'] = tok
        return tok


def _post(client, path, **data):
    return client.post(path, data={'_csrf': _session_token(client), **data})


def _admin_post(admin, path):
    return admin.post(path, data={'_csrf': _session_token(admin)})


# --------------------------------------------------------------------------- #
# Setup refuses to change a confirmed email
# --------------------------------------------------------------------------- #
def test_confirmed_email_cannot_be_replaced_through_setup(app, client, sent, student):
    _set_email(app, student, 'first@gmail.com')
    assert login_student(client, ROLL).status_code == 302
    r = _post(client, '/setup/email', email='second@gmail.com')
    assert b'already confirmed' in r.data
    assert sent == []                                  # no code was sent
    assert _email_of(app, student) == 'first@gmail.com'


def test_code_sent_before_the_lock_cannot_replace_the_confirmed_email(
        app, client, sent, student):
    assert login_student(client, ROLL).status_code == 302
    _post(client, '/setup/email', email=_college(app))
    code = sent[-1]['code']
    assert code                                        # a real code was sent
    _set_email(app, student, 'locked@gmail.com')       # confirmed in the meantime
    r = _post(client, '/setup/email/verify', code=code)
    assert r.status_code == 302 and '/setup/email' in r.headers['Location']
    assert _email_of(app, student) == 'locked@gmail.com'


# --------------------------------------------------------------------------- #
# Administrator clears the email
# --------------------------------------------------------------------------- #
def test_admin_clears_the_email_and_the_student_can_set_a_new_one_once(
        app, client, admin, sent, student):
    _set_email(app, student, 'first@gmail.com')
    r = _admin_post(admin, f'/admin/students/{student}/clear-email')
    assert r.status_code == 302
    assert _email_of(app, student) is None

    assert login_student(client, ROLL).status_code == 302
    _post(client, '/setup/email', email=_college(app))
    assert len(sent) == 1 and sent[-1]['to'] == _college(app)
    _post(client, '/setup/email/verify', code=sent[-1]['code'])
    assert _email_of(app, student) == _college(app)
    # and it is locked again
    _post(client, '/setup/email', email='third@gmail.com')
    assert len(sent) == 1


def test_only_an_administrator_can_clear_the_email(app, client, student):
    _set_email(app, student, 'first@gmail.com')
    assert login_student(client, ROLL).status_code == 302          # a student session
    r = _admin_post(client, f'/admin/students/{student}/clear-email')
    assert r.status_code in (302, 403)
    assert _email_of(app, student) == 'first@gmail.com'


def test_clearing_applies_to_students_only(app, admin):
    with app.app_context():
        teacher = db.query("SELECT id, email FROM users WHERE role='teacher' LIMIT 1", one=True)
    r = _admin_post(admin, f"/admin/students/{teacher['id']}/clear-email")
    assert r.status_code == 404
    assert _email_of(app, teacher['id']) == teacher['email']
