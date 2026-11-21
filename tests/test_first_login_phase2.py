"""Phase 2: first-login flow for Teacher / HOD / Dean.

Order: photo -> replace the temporary password -> confirm e-mail (HOD/Dean: their
onboarding page) -> authenticator app (roles listed in AUTHENTICATOR_ENFORCE_ROLES) ->
Teacher Assignment Setup page (teachers only).
"""
import io
import re
import uuid

import pytest
from PIL import Image

from feedback_system import db, mailer, schema
from feedback_system.security import hash_password
from helpers import get_csrf, login, login_student


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

    # 1. the photo comes first, whatever page is asked for
    r = client.get('/teacher/')
    assert r.status_code == 302 and '/setup/photo' in _loc(r)
    assert '/setup/photo' in _loc(client.get('/teacher/assignment-setup'))
    assert '/setup/photo' in _loc(client.get('/change-password'))     # cannot skip ahead
    assert '/setup/photo' in _loc(client.get('/setup/email'))
    page = client.get('/setup/photo').get_data(as_text=True)
    assert 'Step 1 of 4' in page and 'Continue' in page
    assert 'Choose a photo' not in page and 'photoInput' not in page   # admin adds photos
    r = _post(client, '/setup/photo', action='continue')
    assert r.status_code == 302
    with app.app_context():
        assert db.query('SELECT first_login_complete f FROM users WHERE emp_code=?',
                        (emp,), one=True)['f'] == 1

    # 2. then the temporary password (before the e-mail)
    r = client.get('/teacher/')
    assert r.status_code == 302 and '/change-password' in _loc(r)
    assert '/change-password' in _loc(client.get('/setup/email'))     # e-mail must wait
    assert 'Step 2 of 4' in client.get('/change-password').get_data(as_text=True)
    assert _change_password(client).status_code == 302

    # 3. then the e-mail confirmation
    r = client.get('/teacher/')
    assert r.status_code == 302 and '/setup/email' in _loc(r)
    assert 'Step 3 of 4' in client.get('/setup/email').get_data(as_text=True)
    ok = _verify_email(client, box)
    assert ok.status_code == 302
    with app.app_context():
        row = db.query('SELECT email, email_verified FROM users WHERE emp_code=?', (emp,), one=True)
        assert row['email'] == 'prof@gmail.com' and row['email_verified'] == 1

    # 4. no authenticator required for teachers by default -> Teacher Assignment Setup
    r = client.get('/teacher/')
    assert r.status_code == 302 and '/teacher/assignment-setup' in _loc(r)
    assert client.get('/teacher/assignment-setup').status_code == 200
    assert client.get('/teacher/').status_code == 200            # the landing happens once
    # the one-time steps never repeat
    assert '/teacher/' in _loc(client.get('/setup/photo'))


def test_authenticator_comes_after_email_then_assignment_setup(app, client, box):
    """Teacher listed in AUTHENTICATOR_ENFORCE_ROLES: photo, password, e-mail, app, setup."""
    import pyotp
    from feedback_system import authenticator
    saved = app.config.get('AUTHENTICATOR_ENFORCE_ROLES')
    app.config['AUTHENTICATOR_ENFORCE_ROLES'] = 'student,teacher'
    emp = _no_photo_teacher(app)
    try:
        _login(client, emp)
        assert '/setup/photo' in _loc(client.get('/teacher/'))
        assert '/setup/photo' in _loc(client.get('/setup/authenticator'))
        _post(client, '/setup/photo', action='continue')
        assert '/change-password' in _loc(client.get('/teacher/'))
        assert '/change-password' in _loc(client.get('/setup/authenticator'))
        _change_password(client)
        assert '/setup/email' in _loc(client.get('/teacher/'))
        assert '/setup/email' in _loc(client.get('/setup/authenticator'))
        _verify_email(client, box)

        # 4. the authenticator app, with its step label
        r = client.get('/teacher/')
        assert r.status_code == 302 and '/setup/authenticator' in _loc(r)
        page = client.get('/setup/authenticator').get_data(as_text=True)
        assert 'Step 4 of 5' in page
        _post(client, '/setup/authenticator', action='password', password='MyNewPass#9')
        setup_page = client.get('/setup/authenticator').get_data(as_text=True)
        with app.app_context():
            uid = db.query('SELECT id FROM users WHERE emp_code=?', (emp,), one=True)['id']
            secret = authenticator.pending_secret(uid)
        assert secret
        r = client.post('/setup/authenticator/confirm',
                        data={'code': pyotp.TOTP(secret).now(), '_csrf': get_csrf(setup_page)})
        assert r.status_code == 200
        # 5. "I have saved my codes" leads to Teacher Assignment Setup
        assert '/teacher/assignment-setup' in r.get_data(as_text=True)
        assert client.get('/teacher/assignment-setup').status_code == 200
        assert client.get('/teacher/').status_code == 200
    finally:
        app.config['AUTHENTICATOR_ENFORCE_ROLES'] = saved
        with app.app_context():
            for t in ('student_backup_codes', 'student_authenticators'):
                db.execute(f'DELETE FROM {t}')


def test_teacher_cannot_upload_a_photo_during_setup(app, client, box):
    emp = _no_photo_teacher(app)
    _login(client, emp)
    r = _post(client, '/setup/photo', action='upload',
              files={'photo': (io.BytesIO(_png()), 'me.png', 'image/png')})
    assert r.status_code == 200 and b'administrator' in r.data
    with app.app_context():
        assert not db.query('SELECT 1 FROM employee_photos WHERE emp_code=?',
                            (emp.upper(),), one=True)
        assert db.query('SELECT first_login_complete f FROM users WHERE emp_code=?',
                        (emp,), one=True)['f'] == 0
    assert '/setup/photo' in _loc(client.get('/teacher/'))      # setup still not finished


def test_continue_works_without_a_photo_and_shows_the_admin_photo_when_present(app, client, box):
    emp = _no_photo_teacher(app)
    _login(client, emp)
    with app.app_context():
        from feedback_system import photos
        photos.save_photo(emp, _png(), None)                    # what the admin import does
    page = client.get('/setup/photo').get_data(as_text=True)
    assert f'/employee-photo/{emp.upper()}' in page and 'photoInput' not in page
    assert client.get(f'/employee-photo/{emp}').status_code == 200   # image is reachable mid-setup
    r = _post(client, '/setup/photo', action='continue')
    assert r.status_code == 302 and '/teacher/' in _loc(r)


def test_continue_is_allowed_when_no_photo_is_on_file(app, client, box):
    emp = _no_photo_teacher(app)
    _login(client, emp)
    r = _post(client, '/setup/photo', action='continue')
    assert r.status_code == 302
    with app.app_context():
        assert db.query('SELECT first_login_complete f FROM users WHERE emp_code=?',
                        (emp,), one=True)['f'] == 1
        assert db.query("SELECT 1 FROM audit_log WHERE action='teacher.first_login_complete' "
                        "AND detail='Photo step skipped'", one=True)


def test_legacy_static_photo_is_shown_on_the_setup_step(app, client, box):
    _login(client, '6817')                                          # has static/img/teachers/6817.jpg
    page = client.get('/setup/photo').get_data(as_text=True)
    assert '/employee-photo/6817' in page and 'photoInput' not in page


# ------------------------------------------------------------------ safety / regression
def test_photo_step_requires_csrf(app, client, box):
    emp = _no_photo_teacher(app)
    _login(client, emp)
    assert client.post('/setup/photo', data={'action': 'skip'}).status_code in (400, 403)
    assert '/setup/photo' in _loc(client.get('/teacher/'))


def test_other_roles_cannot_use_the_teacher_photo_step(app, client):
    r = login_student(client, '25B11CS015')
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
    # teachers have no photo page of their own any more: the admin adds the photo
    assert r.status_code == 302 and '/profile' in _loc(r)


# ------------------------------------------------------------------ HOD / Dean
TEMP_PW = 'Temp#Pass123'


@pytest.fixture()
def fresh_account(app):
    """A newly created HOD/Dean: temporary password, e-mail not verified, nothing done."""
    made = []

    def make(role, onboarding_complete=0, first_login_complete=0):
        ident = f'FL{role.upper()}{uuid.uuid4().hex[:6].upper()}'
        with app.app_context():
            db.execute("INSERT INTO users(role, username, emp_code, name, email, password_hash, "
                       "is_active, must_change_password, email_verified, onboarding_complete, "
                       "first_login_complete) VALUES(?,?,?,?,?,?,1,?,0,?,?)",
                       (role, ident, ident, f'First {role}', f'{ident.lower()}@test.edu',
                        hash_password(TEMP_PW), 0 if onboarding_complete else 1,
                        onboarding_complete, first_login_complete))
        made.append(ident)
        return ident
    app.config['REQUIRE_ACCOUNT_SETUP'] = True
    yield make
    app.config['REQUIRE_ACCOUNT_SETUP'] = False
    with app.app_context():
        for ident in made:
            row = db.query("SELECT id FROM users WHERE username=?", (ident,), one=True)
            if not row:
                continue
            for table, col in (('email_codes', 'user_id'), ('audit_log', 'actor_user_id'),
                               ('student_authenticators', 'user_id'),
                               ('student_backup_codes', 'user_id')):
                db.execute(f"DELETE FROM {table} WHERE {col}=?", (row['id'],))
            try:
                db.execute("DELETE FROM users WHERE id=?", (row['id'],))
            except Exception:                      # still referenced: just retire the account
                db.execute("UPDATE users SET is_active=0 WHERE id=?", (row['id'],))


@pytest.mark.parametrize('role', ['hod', 'dean'])
def test_hod_and_dean_start_with_the_photo_then_password_then_email_onboarding(
        app, client, fresh_account, role):
    ident = fresh_account(role)
    assert login(client, f'/{role}/login', {'emp_code': ident, 'password': TEMP_PW}).status_code == 302

    # 1. photo first; nothing later can be reached by typing its address
    assert '/setup/photo' in _loc(client.get(f'/{role}/'))
    assert '/setup/photo' in _loc(client.get('/change-password'))
    assert '/setup/photo' in _loc(client.get(f'/{role}/onboarding'))
    assert '/setup/photo' in _loc(client.get('/setup/authenticator'))
    page = client.get('/setup/photo').get_data(as_text=True)
    assert 'Step 1 of 3' in page and 'photoInput' not in page
    r = _post(client, '/setup/photo', action='continue')
    assert r.status_code == 302

    # 2. then the temporary password, before the e-mail onboarding
    assert '/change-password' in _loc(client.get(f'/{role}/'))
    assert '/change-password' in _loc(client.get(f'/{role}/onboarding'))
    page = client.get('/change-password').get_data(as_text=True)
    assert 'Step 2 of 3' in page and 'temporary password' in page
    tok = get_csrf(page)
    r = client.post('/change-password', data={'current_password': TEMP_PW, '_csrf': tok,
                                              'new_password': 'MyNewPass#9',
                                              'confirm_password': 'MyNewPass#9'})
    assert r.status_code == 302

    # 3. then the e-mail onboarding page
    assert f'/{role}/onboarding' in _loc(client.get(f'/{role}/'))
    assert client.get(f'/{role}/onboarding').status_code == 200
    with app.app_context():
        assert db.query('SELECT first_login_complete f FROM users WHERE username=?',
                        (ident,), one=True)['f'] == 1
    assert '/setup/photo' not in _loc(client.get(f'/{role}/'))     # the photo step is not repeated


@pytest.mark.parametrize('role', ['hod', 'dean'])
def test_hod_and_dean_authenticator_comes_after_onboarding(app, client, fresh_account, role):
    ident = fresh_account(role)
    saved = app.config.get('AUTHENTICATOR_ENFORCE_ROLES')
    app.config['AUTHENTICATOR_ENFORCE_ROLES'] = f'student,{role}'
    try:
        login(client, f'/{role}/login', {'emp_code': ident, 'password': TEMP_PW})
        assert 'Step 1 of 4' in client.get('/setup/photo').get_data(as_text=True)
        _post(client, '/setup/photo', action='continue')
        # photo and password done, onboarding open: the app is not offered yet
        with app.app_context():
            db.execute("UPDATE users SET must_change_password=0 WHERE username=?", (ident,))
        assert f'/{role}/onboarding' in _loc(client.get(f'/{role}/'))
        assert f'/{role}/onboarding' in _loc(client.get('/setup/authenticator'))
        # onboarding finished -> now the authenticator app
        with app.app_context():
            db.execute("UPDATE users SET onboarding_complete=1, email_verified=1 WHERE username=?",
                       (ident,))
        assert '/setup/authenticator' in _loc(client.get(f'/{role}/'))
        assert 'Step 4 of 4' in client.get('/setup/authenticator').get_data(as_text=True)
    finally:
        app.config['AUTHENTICATOR_ENFORCE_ROLES'] = saved


@pytest.mark.parametrize('role', ['hod', 'dean'])
def test_existing_hod_and_dean_who_finished_onboarding_skip_the_photo_step(
        app, client, fresh_account, role):
    ident = fresh_account(role, onboarding_complete=1, first_login_complete=0)
    with app.app_context():
        db.execute("UPDATE users SET password_hash=? WHERE username=?",
                   (hash_password('Personal#Pass1'), ident))
    assert login(client, f'/{role}/login',
                 {'emp_code': ident, 'password': 'Personal#Pass1'}).status_code == 302
    assert client.get(f'/{role}/').status_code == 200
    assert '/setup/photo' not in _loc(client.get('/setup/photo'))


def test_students_and_admin_have_no_photo_step(app, client, fresh_account):
    assert login_student(client, '25B11CS015').status_code == 302
    assert client.get('/setup/photo').status_code in (302, 403)
    assert '/setup/photo' not in _loc(client.get('/student/'))
