"""Student sign-in flow changes.

* the default password is optional to change (admin reset does not force it for students);
* the college e-mail rule: <roll>@<STUDENT_EMAIL_DOMAIN>, and how to switch it off;
* the e-mail-code route ends in the authenticator QR without a second code.
"""
import re

import pytest

from feedback_system import db, mailer
from feedback_system.security import student_college_email
from helpers import get_csrf, login_student

ROLL = '25B11CS017'            # a student no other test depends on


@pytest.fixture()
def sent(app, monkeypatch):
    box = []

    def fake_send(to, subject, text, html=None):
        box.append({'to': to, 'code': (re.search(r'\b(\d{6})\b', text) or [None, None])[1]})
        return True

    monkeypatch.setattr(mailer, 'send_email', fake_send)
    app.config['REQUIRE_ACCOUNT_SETUP'] = True
    saved = app.config['STUDENT_EMAIL_DOMAIN']
    yield box
    app.config['REQUIRE_ACCOUNT_SETUP'] = False
    app.config['STUDENT_EMAIL_DOMAIN'] = saved
    with app.app_context():
        for table in ('student_backup_codes', 'student_authenticators'):
            db.execute(f"DELETE FROM {table} WHERE user_id IN "
                       f"(SELECT id FROM users WHERE roll=?)", (ROLL,))
        db.execute('DELETE FROM email_codes')
        db.execute("UPDATE users SET email=NULL, email_verified=0 WHERE roll=?", (ROLL,))


def _post(client, path, **data):
    return client.post(path, data={**data, '_csrf': get_csrf(client.get(path).get_data(as_text=True))})


def _uid(app, role='student', where="roll=?", args=(ROLL,)):
    with app.app_context():
        return db.query(f"SELECT id FROM users WHERE role=? AND {where}", (role, *args), one=True)['id']


def _flag(app, uid):
    with app.app_context():
        return db.query('SELECT must_change_password FROM users WHERE id=?', (uid,), one=True)[
            'must_change_password']


# ------------------------------------------------------------------ college e-mail helper
def test_college_email_is_roll_lowercased_at_the_college_domain(app):
    with app.app_context():
        assert student_college_email('26B21CS073') == '26b21cs073@adityauniversity.in'
        assert student_college_email(' 26b21cs073 ') == '26b21cs073@adityauniversity.in'
        assert student_college_email('') is None and student_college_email(None) is None
        app.config['STUDENT_EMAIL_DOMAIN'] = '@Example.EDU'
        try:
            assert student_college_email('A1') == 'a1@example.edu'
            app.config['STUDENT_EMAIL_DOMAIN'] = ''
            assert student_college_email('A1') is None          # rule switched off
        finally:
            app.config['STUDENT_EMAIL_DOMAIN'] = 'adityauniversity.in'


# ------------------------------------------------------------------ e-mail code route
def test_student_must_enter_their_own_college_email(app, client, sent):
    assert login_student(client, ROLL).status_code == 302
    for bad in ('someone@gmail.com', '25b11cs016@adityauniversity.in',        # another student's
                f'{ROLL.lower()}@adityauniversity.com'):
        r = _post(client, '/setup/email', email=bad)
        assert r.status_code == 200 and b'college email' in r.data, bad
    assert sent == []
    r = _post(client, '/setup/email', email=f'{ROLL.lower()}@adityauniversity.in')
    assert r.status_code == 302 and '/setup/email/verify' in r.headers['Location']
    assert sent[-1]['to'] == f'{ROLL.lower()}@adityauniversity.in'


def test_email_code_then_the_qr_straight_away_for_a_student(app, client, sent):
    assert login_student(client, ROLL).status_code == 302
    _post(client, '/setup/email', email=f'{ROLL.lower()}@adityauniversity.in')
    _post(client, '/setup/email/verify', code=sent[-1]['code'])
    page = client.get('/setup/authenticator').get_data(as_text=True)
    assert 'setup-key' in page and 'otpauth://totp/' in page
    assert len(sent) == 1                       # one code only; no second e-mail for the QR


def test_the_rule_can_be_switched_off_with_an_empty_domain(app, client, sent):
    app.config['STUDENT_EMAIL_DOMAIN'] = ''
    assert login_student(client, ROLL).status_code == 302
    r = _post(client, '/setup/email', email='kid@gmail.com')
    assert r.status_code == 302 and sent[-1]['to'] == 'kid@gmail.com'


def test_setup_page_shows_the_college_email_to_type(app, client, sent):
    assert login_student(client, ROLL).status_code == 302
    html = client.get('/setup/email').get_data(as_text=True)
    assert f'{ROLL.lower()}@adityauniversity.in' in html
    assert 'Continue with Google' not in html


# ------------------------------------------------------------------ admin password reset
def test_admin_reset_does_not_force_a_student_but_still_forces_a_teacher(app, admin):
    stu = _uid(app)
    tea = _uid(app, 'teacher', "emp_code=?", ('6817',))
    tok = get_csrf(admin.get('/admin/students').get_data(as_text=True))
    r = admin.post(f'/admin/users/{stu}/reset-password', data={'_csrf': tok, 'role': 'student'})
    assert r.status_code == 302 and _flag(app, stu) == 0
    r = admin.post(f'/admin/users/{tea}/reset-password', data={'_csrf': tok, 'role': 'teacher'})
    assert r.status_code == 302 and _flag(app, tea) == 1
    with app.app_context():                       # leave the shared teacher account tidy
        db.execute("UPDATE users SET must_change_password=0 WHERE emp_code='6817'")
