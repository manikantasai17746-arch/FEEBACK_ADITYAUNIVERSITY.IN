"""Phase 2: teacher first-login flow.

Order: confirm e-mail (code) -> replace default password -> photo step ->
Teacher Assignment Setup page.
"""
import io
import re

import pytest
from PIL import Image

from feedback_system import db, mailer, schema
from feedback_system.security import hash_password
from helpers import get_csrf, login


def _png(size=(200, 240)):
    buf = io.BytesIO()
    Image.new('RGB', size, (30, 90, 160)).save(buf, 'PNG')
    return buf.getvalue()


@pytest.fixture()
def box(app, monkeypatch):
    """Gate on, mail captured, and the teachers used here reset afterwards."""
    mails = []

    def fake_send(to, subject, text, html=None):
        mails.append({'to': to, 'code': (re.search(r'\b(\d{6})\b', text) or [None, None])[1]})
        return True

    monkeypatch.setattr(mailer, 'send_email', fake_send)
    app.config['REQUIRE_ACCOUNT_SETUP'] = True
    yield mails
    app.config['REQUIRE_ACCOUNT_SETUP'] = False
    with app.app_context():
        db.execute("DELETE FROM email_codes")
        db.execute("DELETE FROM employee_photos")
        db.execute("UPDATE users SET email=NULL, email_verified=0, must_change_password=0, "
                   "first_login_complete=0, password_hash=? WHERE role='teacher'",
                   (hash_password('teacher@123'),))
        db.execute("UPDATE users SET first_login_complete=0 WHERE role='teacher'")


def _no_photo_teacher(app):
    """An employee code with neither a stored nor a legacy static photo."""
    with app.app_context():
        for r in db.query("SELECT emp_code FROM users WHERE role='teacher' AND emp_code IS NOT NULL "
                          "ORDER BY id"):
            if r['emp_code'] not in ('6791', '6817'):
                return r['emp_code']


def _post(c, path, files=None, **data):
    html = c.get(path).get_data(as_text=True)
    payload = {**data, '_csrf': get_csrf(html)}
    if files:
        payload.update(files)
        return c.post(path, data=payload, content_type='multipart/form-data')
    return c.post(path, data=payload)


def _loc(r):
    return r.headers.get('Location', '')


def _login(c, emp):
    r = login(c, '/teacher/login', {'emp_code': emp, 'password': 'teacher@123'})
    assert r.status_code == 302
    return c


def _verify_email(c, mails, email='prof@gmail.com'):
    assert _post(c, '/setup/email', email=email).status_code == 302
    return _post(c, '/setup/email/verify', code=mails[-1]['code'])


def _change_password(c, new='MyNewPass#9'):
    tok = get_csrf(c.get('/change-password').get_data(as_text=True))
    return c.post('/change-password', data={'current_password': 'teacher@123', 'new_password': new,
                                            'confirm_password': new, '_csrf': tok})


# ------------------------------------------------------------------ migration
def test_migration_v20_adds_flag_and_grandfathers_existing_teachers(app):
    assert schema.MIGRATIONS[-1][0] >= 20
    stmts = [s for v, _d, ss in schema.MIGRATIONS if v == 20 for s in ss]
    assert any('first_login_complete' in s and 'ALTER TABLE users' in s for s in stmts)
    with app.app_context():
        cols = {r['name'] for r in db.query('PRAGMA table_info(users)')}
        assert 'first_login_complete' in cols
        # The back-fill rule: a teacher with a confirmed email + own password is grandfathered.
        backfill = [s for s in stmts if s.lstrip().startswith('UPDATE users SET first_login_complete')][0]
        assert "role='teacher'" in backfill and 'must_change_password=0' in backfill


def test_v20_translates_to_postgres_cleanly():
    from feedback_system import pg
    for v, _d, ss in schema.MIGRATIONS:
        if v == 20:
            for s in ss:
                assert pg.to_postgres(s)


# ------------------------------------------------------------------ the full order
def test_full_first_login_order(app, client, box):
    emp = _no_photo_teacher(app)
    _login(client, emp)

    # 1. e-mail comes first, even though the default password also needs changing
    r = client.get('/teacher/')
    assert r.status_code == 302 and '/setup/email' in _loc(r)
    assert '/setup/email' in _loc(client.get('/teacher/assignment-setup'))
    assert '/setup/email' in _loc(client.get('/setup/photo'))      # cannot skip ahead

    ok = _verify_email(client, box)
    assert ok.status_code == 302
    with app.app_context():
        row = db.query('SELECT email, email_verified FROM users WHERE emp_code=?', (emp,), one=True)
        assert row['email'] == 'prof@gmail.com' and row['email_verified'] == 1

    # 2. then the temporary password
    r = client.get('/teacher/')
    assert r.status_code == 302 and '/change-password' in _loc(r)
    assert '/change-password' in _loc(client.get('/setup/photo'))
    assert _change_password(client).status_code == 302

    # 3. then the photo step
    r = client.get('/teacher/')
    assert r.status_code == 302 and '/setup/photo' in _loc(r)
    page = client.get('/setup/photo').get_data(as_text=True)
    assert 'Step 3 of 4' in page and 'Choose a photo' in page and 'Skip for now' in page
    with app.app_context():
        assert db.query('SELECT first_login_complete f FROM users WHERE emp_code=?',
                        (emp,), one=True)['f'] == 0

    # 4. finishing lands on the Teacher Assignment Setup page
    r = _post(client, '/setup/photo', action='skip')
    assert r.status_code == 302 and '/teacher/assignment-setup' in _loc(r)
    assert client.get('/teacher/assignment-setup').status_code == 200
    assert client.get('/teacher/').status_code == 200
    with app.app_context():
        assert db.query('SELECT first_login_complete f FROM users WHERE emp_code=?',
                        (emp,), one=True)['f'] == 1
    # the flow never repeats
    assert '/teacher/assignment-setup' in _loc(client.get('/setup/photo'))


def test_photo_upload_completes_the_flow_and_is_stored_by_emp_id(app, client, box):
    emp = _no_photo_teacher(app)
    _login(client, emp)
    _verify_email(client, box)
    _change_password(client)
    r = _post(client, '/setup/photo', action='upload',
              files={'photo': (io.BytesIO(_png()), 'me.png', 'image/png')})
    assert r.status_code == 302 and '/teacher/assignment-setup' in _loc(r)
    with app.app_context():
        row = db.query('SELECT filename FROM employee_photos WHERE emp_code=?', (emp.upper(),), one=True)
        assert row and row['filename'] == f'employee_photos/{emp.upper()}.png'
        assert db.query('SELECT first_login_complete f FROM users WHERE emp_code=?',
                        (emp,), one=True)['f'] == 1


def test_bad_upload_is_rejected_and_does_not_finish_setup(app, client, box):
    emp = _no_photo_teacher(app)
    _login(client, emp)
    _verify_email(client, box)
    _change_password(client)
    r = _post(client, '/setup/photo', action='upload',
              files={'photo': (io.BytesIO(b'GIF89a not really'), 'x.png', 'image/png')})
    assert r.status_code == 200 and b'valid JPG or PNG' in r.data
    assert '/setup/photo' in _loc(client.get('/teacher/'))
    with app.app_context():
        assert not db.query('SELECT 1 FROM employee_photos WHERE emp_code=?', (emp.upper(),), one=True)


def test_continue_needs_a_photo_but_existing_photo_shows_a_preview(app, client, box):
    emp = _no_photo_teacher(app)
    _login(client, emp)
    _verify_email(client, box)
    _change_password(client)
    r = _post(client, '/setup/photo', action='continue')           # nothing on file yet
    assert r.status_code == 200 and b'upload a photo' in r.data
    with app.app_context():
        from feedback_system import photos
        photos.save_photo(emp, _png(), None)
    page = client.get('/setup/photo').get_data(as_text=True)
    assert 'Keep this photo' in page and f'/employee-photo/{emp.upper()}' in page
    assert client.get(f'/employee-photo/{emp}').status_code == 200   # image is reachable mid-setup
    r = _post(client, '/setup/photo', action='continue')
    assert r.status_code == 302 and '/teacher/assignment-setup' in _loc(r)


def test_legacy_static_photo_counts_as_existing(app, client, box):
    _login(client, '6817')                                          # has static/img/teachers/6817.jpg
    _verify_email(client, box)
    _change_password(client)
    page = client.get('/setup/photo').get_data(as_text=True)
    assert 'Keep this photo' in page


# ------------------------------------------------------------------ safety / regression
def test_photo_step_requires_csrf(app, client, box):
    emp = _no_photo_teacher(app)
    _login(client, emp)
    _verify_email(client, box)
    _change_password(client)
    assert client.post('/setup/photo', data={'action': 'skip'}).status_code in (400, 403)
    assert '/setup/photo' in _loc(client.get('/teacher/'))


def test_other_roles_cannot_use_the_teacher_photo_step(app, client):
    r = login(client, '/student/login', {'roll': '25B11CS015', 'password': 'student@123'})
    assert r.status_code == 302
    r = client.get('/setup/photo')
    assert r.status_code in (302, 403) and '/setup/photo' not in _loc(r)
    assert client.get('/teacher/assignment-setup').status_code in (302, 403)


def test_teacher_who_finished_old_setup_is_not_sent_back(app, client, box):
    """Grandfathered account: email + own password + flag set -> straight in."""
    emp = _no_photo_teacher(app)
    with app.app_context():
        db.execute("UPDATE users SET email='old@gmail.com', email_verified=1, password_hash=?, "
                   "first_login_complete=1 WHERE emp_code=?", (hash_password('Personal#Pass1'), emp))
    r = login(client, '/teacher/login', {'emp_code': emp, 'password': 'Personal#Pass1'})
    assert r.status_code == 302
    assert client.get('/teacher/').status_code == 200


def test_photo_step_not_enforced_when_account_setup_is_off(app, client):
    app.config['REQUIRE_ACCOUNT_SETUP'] = False
    emp = _no_photo_teacher(app)
    with app.app_context():
        db.execute("UPDATE users SET password_hash=?, first_login_complete=0, must_change_password=0 "
                   "WHERE emp_code=?", (hash_password('Personal#Pass1'), emp))
    login(client, '/teacher/login', {'emp_code': emp, 'password': 'Personal#Pass1'})
    assert client.get('/teacher/').status_code == 200


def test_photos_me_redirect_works(app, client):
    emp = _no_photo_teacher(app)
    with app.app_context():
        db.execute("UPDATE users SET first_login_complete=1, must_change_password=0 WHERE emp_code=?",
                   (emp,))
        db.execute("UPDATE users SET password_hash=? WHERE emp_code=?",
                   (hash_password('Personal#Pass1'), emp))
    login(client, '/teacher/login', {'emp_code': emp, 'password': 'Personal#Pass1'})
    r = client.get('/photos/me')
    assert r.status_code == 302 and f'/photos/manage/{emp.upper()}' in _loc(r)
