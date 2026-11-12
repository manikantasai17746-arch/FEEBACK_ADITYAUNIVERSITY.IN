"""Admin: register two OTP-verified recovery emails, then two-email forgot-password."""
import re

import pytest

from feedback_system import db, mailer
from feedback_system.security import hash_password, verify_password
from helpers import get_csrf, login


@pytest.fixture()
def box(app, monkeypatch):
    out = []

    def fake_send(to, subject, text, html=None):
        out.append({'to': to, 'text': text, 'code': (re.search(r'\b(\d{6})\b', text) or [None, None])[1]})
        return True
    monkeypatch.setattr(mailer, 'send_email', fake_send)
    app.config['REQUIRE_ACCOUNT_SETUP'] = True
    with app.app_context():
        db.execute("DELETE FROM email_codes")
        db.execute("UPDATE users SET recovery_email1=NULL, recovery_email2=NULL WHERE role='admin'")
    yield out
    app.config['REQUIRE_ACCOUNT_SETUP'] = False
    with app.app_context():
        db.execute("DELETE FROM email_codes")
        db.execute("UPDATE users SET recovery_email1=NULL, recovery_email2=NULL, password_hash=? "
                   "WHERE role='admin'", (hash_password('admin123'),))


def _post(client, path, **data):
    return client.post(path, data={**data, '_csrf': get_csrf(client.get(path).get_data(as_text=True))})


def _register(client, box, e1='one@gmail.com', e2='two@gmail.com'):
    assert login(client, '/admin/login', {'email': 'admin@college.edu', 'password': 'admin123'}).status_code == 302
    r = client.get('/admin/')
    assert r.status_code == 302 and '/admin/setup-emails' in r.headers['Location']
    r = _post(client, '/admin/setup-emails', email1=e1, confirm1=e1, email2=e2, confirm2=e2)
    assert r.status_code == 302 and 'verify' in r.headers['Location']
    codes = {m['to']: m['code'] for m in box}
    assert set(codes) == {e1, e2}
    r = _post(client, '/admin/setup-emails/verify', code1=codes[e1], code2=codes[e2])
    assert r.status_code == 302 and '/admin/setup-emails' not in r.headers['Location']


def test_admin_forced_to_register_two_emails_and_then_free(app, client, box):
    _register(client, box)
    assert client.get('/admin/').status_code == 200
    with app.app_context():
        u = db.query("SELECT recovery_email1 a, recovery_email2 b FROM users WHERE role='admin'", one=True)
    assert (u['a'], u['b']) == ('one@gmail.com', 'two@gmail.com')


def test_setup_rejects_same_or_unconfirmed_emails(client, box):
    login(client, '/admin/login', {'email': 'admin@college.edu', 'password': 'admin123'})
    r = _post(client, '/admin/setup-emails', email1='a@gmail.com', confirm1='a@gmail.com',
              email2='a@gmail.com', confirm2='a@gmail.com')
    assert r.status_code == 200 and not box
    r = _post(client, '/admin/setup-emails', email1='a@gmail.com', confirm1='x@gmail.com',
              email2='b@gmail.com', confirm2='b@gmail.com')
    assert r.status_code == 200 and not box


def test_wrong_code_does_not_save(app, client, box):
    login(client, '/admin/login', {'email': 'admin@college.edu', 'password': 'admin123'})
    _post(client, '/admin/setup-emails', email1='a@gmail.com', confirm1='a@gmail.com',
          email2='b@gmail.com', confirm2='b@gmail.com')
    _post(client, '/admin/setup-emails/verify', code1='000000', code2='000000')
    with app.app_context():
        u = db.query("SELECT recovery_email1 a FROM users WHERE role='admin'", one=True)
    assert not u['a']


def test_forgot_password_needs_both_emails_and_sends_to_one(app, client, box):
    _register(client, box)
    client.get('/logout')
    box.clear()
    # wrong second email -> nothing is sent
    r = _post(client, '/admin/forgot-password', email1='one@gmail.com', email2='evil@gmail.com')
    assert r.status_code == 302 and not box
    # both right (any order) -> exactly one mail, to one of the two
    c2 = client.application.test_client()
    r = _post(c2, '/admin/forgot-password', email1='two@gmail.com', email2='ONE@gmail.com')
    assert r.status_code == 302 and len(box) == 1
    assert box[0]['to'] in ('one@gmail.com', 'two@gmail.com')
    r = _post(c2, '/forgot-password/reset', code=box[0]['code'], new_password='NewAdminPass#1',
              confirm_password='NewAdminPass#1')
    assert r.status_code == 302
    with app.app_context():
        u = db.query("SELECT password_hash FROM users WHERE role='admin'", one=True)
    assert verify_password(u['password_hash'], 'NewAdminPass#1')


def test_both_addresses_get_used_over_time(app, client, box, monkeypatch):
    _register(client, box)
    client.get('/logout')
    targets = set()
    for _ in range(30):
        box.clear()
        with app.app_context():
            db.execute("DELETE FROM email_codes")
        _post(client.application.test_client(), '/admin/forgot-password',
              email1='one@gmail.com', email2='two@gmail.com')
        targets.add(box[0]['to'])
    assert targets == {'one@gmail.com', 'two@gmail.com'}


def test_generic_forgot_route_no_longer_serves_admin(client, box):
    r = client.get('/forgot-password?role=admin')
    assert r.status_code == 302 and '/admin/forgot-password' in r.headers['Location']
