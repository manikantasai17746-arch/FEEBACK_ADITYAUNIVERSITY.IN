"""First-login setup (email + code, teacher default password) and forgot-password reset."""
import re
import time

import pytest

from feedback_system import db, mailer
from feedback_system.security import hash_password
from helpers import get_csrf, login


@pytest.fixture()
def sent(app, monkeypatch):
    """Turn the first-login gate on and capture outgoing mail instead of sending it."""
    box = []

    def fake_send(to, subject, text, html=None):
        box.append({'to': to, 'subject': subject, 'text': text,
                    'code': (re.search(r'\b(\d{6})\b', text) or [None, None])[1]})
        return True

    monkeypatch.setattr(mailer, 'send_email', fake_send)
    app.config['REQUIRE_ACCOUNT_SETUP'] = True
    yield box
    app.config['REQUIRE_ACCOUNT_SETUP'] = False
    with app.app_context():
        db.execute("DELETE FROM email_codes")
        db.execute("UPDATE users SET email=NULL, must_change_password=0 "
                   "WHERE role IN ('student','teacher')")
        db.execute("UPDATE users SET password_hash=? WHERE emp_code='6817'",
                   (hash_password('teacher@123'),))


def _student(app):
    with app.app_context():
        return db.query("SELECT roll FROM users WHERE role='student' LIMIT 1", one=True)['roll']


def _post(client, path, **data):
    return client.post(path, data={**data, '_csrf': get_csrf(client.get(path).get_data(as_text=True))})


def _set_teacher_password(app, plain):
    with app.app_context():
        db.execute("UPDATE users SET password_hash=? WHERE emp_code='6817'", (hash_password(plain),))


def _confirm_email(client, sent, email):
    assert _post(client, '/setup/email', email=email).status_code == 302
    return _post(client, '/setup/email/verify', code=sent[-1]['code'])


# ----------------------------------------------------------------------------- email + code
def test_student_confirms_email_with_code(app, client, sent):
    roll = _student(app)
    assert login(client, '/', {'roll': roll, 'password': 'student@123'}).status_code == 302
    r = client.get('/student/')
    assert r.status_code == 302 and '/setup/email' in r.headers['Location']

    assert _post(client, '/setup/email', email='nope').status_code == 200      # invalid format
    assert sent == []
    r = _post(client, '/setup/email', email='Kid@Gmail.com')
    assert r.status_code == 302 and '/setup/email/verify' in r.headers['Location']
    assert sent[-1]['to'] == 'kid@gmail.com' and sent[-1]['code']
    with app.app_context():   # not saved until confirmed
        assert not db.query('SELECT email FROM users WHERE roll=?', (roll,), one=True)['email']
    assert client.get('/student/').status_code == 302

    wrong = _post(client, '/setup/email/verify', code='000000' if sent[-1]['code'] != '000000' else '111111')
    assert wrong.status_code == 200 and b'not correct' in wrong.data
    ok = _post(client, '/setup/email/verify', code=sent[-1]['code'])
    assert ok.status_code == 302
    assert client.get('/student/').status_code == 200
    with app.app_context():
        assert db.query('SELECT email FROM users WHERE roll=?', (roll,), one=True)['email'] == 'kid@gmail.com'


def test_code_expires_and_locks_after_five_wrong_guesses(app, client, sent):
    login(client, '/', {'roll': _student(app), 'password': 'student@123'})
    _post(client, '/setup/email', email='a@gmail.com')
    real = sent[-1]['code']
    bad = '000000' if real != '000000' else '111111'
    for _ in range(5):
        _post(client, '/setup/email/verify', code=bad)
    r = client.get('/setup/email/verify')                    # code was destroyed
    assert r.status_code == 302 and '/setup/email' in r.headers['Location']
    with app.app_context():
        db.execute('DELETE FROM email_codes')
    _post(client, '/setup/email', email='a@gmail.com')
    with app.app_context():
        db.execute('UPDATE email_codes SET expires_ts=?', (int(time.time()) - 1,))
    assert client.get('/setup/email/verify').status_code == 302   # expired


def test_resend_has_a_cooldown(app, client, sent):
    login(client, '/', {'roll': _student(app), 'password': 'student@123'})
    _post(client, '/setup/email', email='a@gmail.com')
    r = _post(client, '/setup/email/verify', action='resend')
    assert b'wait' in r.data and len(sent) == 1
    with app.app_context():
        db.execute('UPDATE email_codes SET created_ts=created_ts-120')
    _post(client, '/setup/email/verify', action='resend')
    assert len(sent) == 2


def test_mail_failure_does_not_save_email(app, client, sent, monkeypatch):
    monkeypatch.setattr(mailer, 'send_email', lambda *a, **k: False)
    login(client, '/', {'roll': _student(app), 'password': 'student@123'})
    r = _post(client, '/setup/email', email='a@gmail.com')
    assert r.status_code == 200 and b'could not send' in r.data


# ----------------------------------------------------------------------------- teacher default password
def test_teacher_on_default_password_must_change_it(app, client, sent):
    _set_teacher_password(app, 'teacher@123')
    login(client, '/teacher/login', {'emp_code': '6817', 'password': 'teacher@123'})
    _confirm_email(client, sent, 't@gmail.com')
    r = client.get('/teacher/')
    assert r.status_code == 302 and '/change-password' in r.headers['Location']
    html = client.get('/change-password').get_data(as_text=True)
    assert 'still using the default password' in html
    tok = get_csrf(html)
    same = client.post('/change-password', data={'current_password': 'teacher@123',
        'new_password': 'teacher@123', 'confirm_password': 'teacher@123', '_csrf': tok})
    assert same.status_code == 200 and b'not the default' in same.data
    done = client.post('/change-password', data={'current_password': 'teacher@123',
        'new_password': 'MyNewPass#9', 'confirm_password': 'MyNewPass#9', '_csrf': tok})
    assert done.status_code == 302
    assert client.get('/teacher/').status_code == 200


def test_teacher_with_own_password_is_not_forced(app, client, sent):
    _set_teacher_password(app, 'Personal#Pass1')
    login(client, '/teacher/login', {'emp_code': '6817', 'password': 'Personal#Pass1'})
    _confirm_email(client, sent, 't@gmail.com')
    assert client.get('/teacher/').status_code == 200
    assert client.get('/change-password').status_code == 200


# ----------------------------------------------------------------------------- forgot password
def _give_email(app, emp, email):
    with app.app_context():
        db.execute('UPDATE users SET email=? WHERE emp_code=?', (email, emp))


def test_forgot_password_full_flow(app, client, sent):
    app.config['REQUIRE_ACCOUNT_SETUP'] = False
    _set_teacher_password(app, 'Personal#Pass1')
    _give_email(app, '6817', 'teach@gmail.com')
    r = _post(client, '/forgot-password', role='teacher', ident='6817')
    assert r.status_code == 302 and '/forgot-password/reset' in r.headers['Location']
    assert sent[-1]['to'] == 'teach@gmail.com' and 'reset' in sent[-1]['subject'].lower()
    code = sent[-1]['code']

    bad = _post(client, '/forgot-password/reset', code='999999' if code != '999999' else '888888',
                new_password='BrandNew#123', confirm_password='BrandNew#123')
    assert b'not correct' in bad.data
    weak = _post(client, '/forgot-password/reset', code=code, new_password='short', confirm_password='short')
    assert b'at least 8' in weak.data
    dflt = _post(client, '/forgot-password/reset', code=code, new_password='teacher@123',
                 confirm_password='teacher@123')
    assert b'not the default' in dflt.data
    ok = _post(client, '/forgot-password/reset', code=code, new_password='BrandNew#123',
               confirm_password='BrandNew#123')
    assert ok.status_code == 302 and '/teacher/login' in ok.headers['Location']
    # old password dead, new one works; the code cannot be reused
    assert login(client, '/teacher/login', {'emp_code': '6817', 'password': 'Personal#Pass1'}).status_code == 200
    assert login(client, '/teacher/login', {'emp_code': '6817', 'password': 'BrandNew#123'}).status_code == 302
    client.get('/logout')
    _post(client, '/forgot-password', role='teacher', ident='6817')   # new request
    old = _post(client, '/forgot-password/reset', code=code, new_password='Another#1234',
                confirm_password='Another#1234')
    assert old.status_code == 200 and b'not correct' in old.data
    app.config['REQUIRE_ACCOUNT_SETUP'] = True


def test_forgot_password_does_not_reveal_accounts(app, client, sent):
    _give_email(app, '6817', 'teach@gmail.com')
    known = _post(client, '/forgot-password', role='teacher', ident='6817')
    n_sent = len(sent)
    unknown = _post(client, '/forgot-password', role='teacher', ident='NOPE-0000')
    no_email = _post(client, '/forgot-password', role='student', ident=_student(app))
    assert known.status_code == unknown.status_code == no_email.status_code == 302
    assert known.headers['Location'] == unknown.headers['Location'] == no_email.headers['Location']
    assert len(sent) == n_sent          # nothing e-mailed for unknown / email-less accounts


def test_admin_can_reset_by_email(app, client, sent):
    with app.app_context():
        db.execute("UPDATE users SET password_hash=? WHERE role='admin'", (hash_password('admin123'),))
    _post(client, '/forgot-password', role='admin', ident='Admin@College.edu')
    assert sent[-1]['to'] == 'admin@college.edu'
    ok = _post(client, '/forgot-password/reset', code=sent[-1]['code'], new_password='AdminNew#123',
               confirm_password='AdminNew#123')
    assert ok.status_code == 302
    assert login(client, '/admin/login', {'email': 'admin@college.edu', 'password': 'AdminNew#123'}).status_code == 302
    with app.app_context():
        db.execute("UPDATE users SET password_hash=? WHERE role='admin'", (hash_password('admin123'),))


def test_login_pages_link_to_forgot_password(client):
    for path in ('/', '/teacher/login', '/admin/login'):
        assert b'/forgot-password' in client.get(path).data
