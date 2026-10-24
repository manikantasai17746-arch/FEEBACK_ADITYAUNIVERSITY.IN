"""HOD/Dean credential e-mails and OTP-protected permanent deletion."""
import io
import re

from feedback_system import db, mailer

from helpers import login, token_from


def _admin(client):
    login(client, '/admin/login', {'email': 'admin@college.edu', 'password': 'admin123'})
    return client


def _csv(rows):
    head = 'role,employee_id,full_name,official_email,designation,department_codes,active\n'
    return io.BytesIO((head + rows).encode())


def _branch(app):
    with app.app_context():
        return db.query('SELECT code FROM branches ORDER BY id LIMIT 1', one=True)['code']


def test_import_emails_credentials(app, client, monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, 'send_email',
                        lambda to, subject, text, html=None: sent.append((to, text)) or True)
    c = _admin(client)
    tok = token_from(c, '/admin/accounts/hod-dean')
    row = f'hod,MAILT01,Mail Test,mailt01@example.com,Prof,{_branch(app)},yes\n'
    r = c.post('/admin/accounts/hod-dean/import',
               data={'_csrf': tok, 'csv_file': (_csv(row), 'a.csv')},
               content_type='multipart/form-data')
    assert r.status_code == 302
    assert len(sent) == 1 and sent[0][0] == 'mailt01@example.com'
    assert 'MAILT01' in sent[0][1] and 'Temporary password' in sent[0][1]
    page = c.get('/admin/accounts/hod-dean').get_data(as_text=True)
    assert 'not delivered' not in page          # all mailed -> nothing shown on screen


def test_delete_requires_otp_and_env_mail(app, client, monkeypatch):
    monkeypatch.setattr(mailer, 'send_email', lambda *a, **k: True)
    c = _admin(client)
    with app.app_context():
        from feedback_system.security import hash_password
        uid = db.execute_returning_id(
            "INSERT INTO users(role, username, emp_code, name, email, password_hash, is_active, "
            "must_change_password) VALUES('hod','DELT01','DELT01','Del Test','d@example.com',?,1,1)",
            (hash_password('x' * 12),))
    tok = token_from(c, '/admin/accounts/hod-dean')

    # No DELETE_OTP_EMAIL configured -> refused, account kept.
    monkeypatch.delenv('DELETE_OTP_EMAIL', raising=False)
    c.post(f'/admin/accounts/hod-dean/{uid}/delete/request', data={'_csrf': tok})
    with app.app_context():
        assert db.query('SELECT 1 FROM users WHERE id=?', (uid,), one=True)

    # Configured -> code mailed to the dedicated address(es).
    monkeypatch.setenv('DELETE_OTP_EMAIL', 'boss@example.com, second@example.com')
    captured = []
    monkeypatch.setattr(mailer, 'send_email',
                        lambda to, subject, text, html=None: captured.append((to, text)) or True)
    c.post(f'/admin/accounts/hod-dean/{uid}/delete/request', data={'_csrf': tok})
    assert [t for t, _ in captured] == ['boss@example.com', 'second@example.com']
    code = re.search(r'\n\s+(\d{6})\n', captured[0][1]).group(1)

    # Wrong code -> nothing deleted.
    tok = token_from(c, f'/admin/accounts/hod-dean/{uid}/delete')
    c.post(f'/admin/accounts/hod-dean/{uid}/delete', data={'_csrf': tok, 'code': '000000'})
    with app.app_context():
        assert db.query('SELECT 1 FROM users WHERE id=?', (uid,), one=True)

    # A wrong guess does not burn the real code; the right one now deletes.
    r = c.post(f'/admin/accounts/hod-dean/{uid}/delete', data={'_csrf': tok, 'code': code})
    assert r.status_code == 302
    with app.app_context():
        assert db.query('SELECT 1 FROM users WHERE id=?', (uid,), one=True) is None
