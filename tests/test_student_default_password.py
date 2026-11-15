"""C1.1 - students must not stay on (or choose) the shared default password.

The shared default (config ``DEFAULT_STUDENT_PASSWORD``) is read from the app config
here; it is never written as a literal in these tests except where a test
deliberately swaps the configuration.

Covered:
  1  detection of a student on the configured default
  2  detection of a student on a personal password (and non-students / bad input)
  3  imported student: default hash + must_change_password=0 is still forced
  4  student cannot choose the shared default (change-password AND forgot-password)
  5  student can change to a valid personal password
  6  admin reset to the default -> student must change it
  7  personal-password students keep normal login and access
  8  scan login keeps working (and is not treated as a personal-password login)
  9  teacher password-change behaviour unchanged
  10 admin password-change behaviour unchanged
  11  e-mail / SSO endpoints stay reachable (the SSO modules are in test_sso_email.py)
  12  missing / blank / changed default configuration is handled safely
"""
import os
import re
import tempfile

import pytest

from feedback_system import create_app, db, mailer, security
from feedback_system.import_data import reset_student_teacher_passwords
from feedback_system.security import hash_password
from helpers import (TEST_STUDENT_PASSWORD, get_csrf, login, login_student,
                     set_student_password, token_from)


# --------------------------------------------------------------------------- setup
def _default(app):
    """The configured shared password (read from config, never hard-coded)."""
    return app.config['DEFAULT_STUDENT_PASSWORD']


def _roll(app, n):
    """The n-th student's roll. High offsets keep clear of rows other modules touch."""
    with app.app_context():
        rows = db.query("SELECT username FROM users WHERE role='student' ORDER BY id")
    return rows[60 + n]['username']


def _uid(app, roll):
    with app.app_context():
        return db.query("SELECT id FROM users WHERE username=? AND role='student'",
                        (roll,), one=True)['id']


def _row(app, roll):
    with app.app_context():
        return db.query('SELECT * FROM users WHERE username=?', (roll,), one=True)


def _put_on_default(app, roll, flag=0):
    """Reproduce an imported account: shared-default hash, flag as given."""
    with app.app_context():
        db.execute("UPDATE users SET password_hash=?, must_change_password=? "
                   "WHERE role='student' AND username=?",
                   (hash_password(_default(app)), flag, roll))


def _loc(resp):
    return resp.headers.get('Location', '')


def _post(client, path, **data):
    tok = get_csrf(client.get(path).get_data(as_text=True))
    return client.post(path, data={**data, '_csrf': tok})


@pytest.fixture(autouse=True)
def _restore(app):
    """Leave the shared session database as other modules expect it."""
    yield
    with app.app_context():
        for n in range(0, 8):
            roll = _roll(app, n)
            db.execute("UPDATE users SET email=NULL, email_verified=0 WHERE username=?", (roll,))
    for n in range(0, 8):
        set_student_password(app, _roll(app, n))
    with app.app_context():
        db.execute("DELETE FROM email_codes")
        db.execute("UPDATE users SET password_hash=?, must_change_password=0, "
                   "first_login_complete=1 WHERE emp_code='6817'",
                   (hash_password('teacher@123'),))
        db.execute("UPDATE users SET password_hash=? WHERE role='admin'",
                   (hash_password('admin123'),))
    security._DEFAULT_MATCH_CACHE.clear()


# --------------------------------------------------------------------------- 1, 2: detection
def test_student_on_configured_default_is_detected(app):
    roll = _roll(app, 0)
    _put_on_default(app, roll)
    with app.app_context():
        assert security.student_has_default_password(_uid(app, roll)) is True
        assert security.password_hash_matches_student_default(
            _row(app, roll)['password_hash']) is True


def test_student_on_personal_password_is_not_detected(app):
    roll = _roll(app, 0)
    set_student_password(app, roll)
    with app.app_context():
        assert security.student_has_default_password(_uid(app, roll)) is False


def test_detection_handles_missing_users_bad_hashes_and_other_roles(app):
    with app.app_context():
        assert security.student_has_default_password(None) is False
        assert security.student_has_default_password(0) is False
        assert security.student_has_default_password(99999999) is False       # no such user
        for bad in (None, '', '   ', 'not-a-hash', 'scrypt:garbage$x$y', 12345, b'bytes'):
            assert security.password_hash_matches_student_default(bad) is False
        # Teachers and admins are never judged against the STUDENT default, even if
        # their hash happens to match it.
        shared = hash_password(_default(app))
        db.execute("UPDATE users SET password_hash=? WHERE emp_code='6817'", (shared,))
        db.execute("UPDATE users SET password_hash=? WHERE role='admin'", (shared,))
        teacher = db.query("SELECT id FROM users WHERE emp_code='6817'", one=True)['id']
        admin = db.query("SELECT id FROM users WHERE role='admin'", one=True)['id']
        assert security.student_has_default_password(teacher) is False
        assert security.student_has_default_password(admin) is False
        assert security.student_password_change_required({'id': teacher, 'role': 'teacher',
                                                          'must_change_password': 0}) is False
        assert security.student_password_change_required(None) is False


def test_default_check_is_cached_and_cache_follows_configuration(app, monkeypatch):
    roll = _roll(app, 0)
    _put_on_default(app, roll)
    stored = _row(app, roll)['password_hash']
    calls = []
    real = security.verify_password
    monkeypatch.setattr(security, 'verify_password',
                        lambda h, p: calls.append(1) or real(h, p))
    original = _default(app)
    with app.app_context():
        security._DEFAULT_MATCH_CACHE.clear()
        assert security.password_hash_matches_student_default(stored) is True
        assert security.password_hash_matches_student_default(stored) is True
        assert len(calls) == 1                                  # second answer came from cache
        # Same stored hash, different configured default: must NOT reuse the old answer.
        monkeypatch.setitem(app.config, 'DEFAULT_STUDENT_PASSWORD', 'Another#Shared-9')
        assert security.password_hash_matches_student_default(stored) is False
        monkeypatch.setitem(app.config, 'DEFAULT_STUDENT_PASSWORD', original)
        assert security.password_hash_matches_student_default(stored) is True


# --------------------------------------------------------------------------- 3: imported account forced
def test_imported_default_account_with_flag_zero_is_forced_to_change(app, client):
    """The core bug: default hash + must_change_password=0 used to reach the portal."""
    roll = _roll(app, 1)
    _put_on_default(app, roll, flag=0)
    assert _row(app, roll)['must_change_password'] == 0
    r = login(client, '/student/login', {'roll': roll, 'password': _default(app)})
    assert r.status_code == 302                       # ordinary login still succeeds ...
    for path in ('/student/', '/student/notifications', '/student/form/1'):
        got = client.get(path)                        # ... but nothing else is reachable
        assert got.status_code == 302 and '/change-password' in _loc(got), path
    page = client.get('/change-password')
    assert page.status_code == 200 and b'shared default password' in page.data
    assert _default(app).encode() not in page.data    # the notice never prints the password
    # The decision is saved, so the account stays forced if the configured default changes.
    assert _row(app, roll)['must_change_password'] == 1


def test_flag_zero_cannot_be_used_to_bypass_the_gate_mid_session(app, client):
    roll = _roll(app, 1)
    set_student_password(app, roll)
    assert login_student(client, roll).status_code == 302
    assert client.get('/student/').status_code == 200
    _put_on_default(app, roll, flag=0)                # password replaced while signed in
    got = client.get('/student/')
    assert got.status_code == 302 and '/change-password' in _loc(got)


def test_imported_students_start_flagged_and_reset_script_flags_them():
    tmp = tempfile.mkdtemp(prefix='c11-import-')
    fresh = create_app({
        'TESTING': True, 'DATABASE': os.path.join(tmp, 'fresh.db'), 'DATABASE_URL': None,
        'SECRET_KEY': 'k', 'AUTO_IMPORT': True, 'ADMIN_EMAIL': 'a@college.edu',
        'ADMIN_PASSWORD': 'admin123', 'DEFAULT_TEACHER_PASSWORD': 'teacher@123',
        'DEFAULT_STUDENT_PASSWORD': 'Shared#Start-1', 'REQUIRE_ACCOUNT_SETUP': False,
        'TOTP_ENCRYPTION_KEY': 'slFqyvDkoDYXvVZAqiTyPsKcY1ekJ684A0nVc5i4JME=',
    })
    with fresh.app_context():
        total = db.query("SELECT COUNT(*) c FROM users WHERE role='student'", one=True)['c']
        unflagged = db.query("SELECT COUNT(*) c FROM users WHERE role='student' "
                             "AND must_change_password=0", one=True)['c']
        assert total > 0 and unflagged == 0           # every imported student is flagged
        # ... and they really are on the shared default.
        one = db.query("SELECT id FROM users WHERE role='student' LIMIT 1", one=True)['id']
        assert security.student_has_default_password(one) is True

        db.execute("UPDATE users SET must_change_password=0 WHERE role='student'")
        _, count = reset_student_teacher_passwords(db.get_db(), 'teacher@123', 'Shared#Start-1')
        assert count == total
        assert db.query("SELECT COUNT(*) c FROM users WHERE role='student' "
                        "AND must_change_password=0", one=True)['c'] == 0
        with pytest.raises(RuntimeError):             # blank default is refused, as before
            reset_student_teacher_passwords(db.get_db(), 'teacher@123', '')


# --------------------------------------------------------------------------- 4, 5: choosing a password
def test_student_cannot_choose_the_shared_default(app, client):
    roll = _roll(app, 2)
    _put_on_default(app, roll, flag=1)
    login(client, '/student/login', {'roll': roll, 'password': _default(app)})
    before = _row(app, roll)['password_hash']
    r = _post(client, '/change-password', current_password=_default(app),
              new_password=_default(app), confirm_password=_default(app))
    assert r.status_code == 200
    assert b'shared default password' in r.data and b'personal password' in r.data
    row = _row(app, roll)
    assert row['password_hash'] == before and row['must_change_password'] == 1   # nothing changed
    assert client.get('/student/').status_code == 302                          # still held


def test_normal_password_validation_is_preserved(app, client):
    roll = _roll(app, 2)
    _put_on_default(app, roll, flag=1)
    login(client, '/student/login', {'roll': roll, 'password': _default(app)})
    wrong = _post(client, '/change-password', current_password='nope-nope',
                  new_password='Fine#Pass-123', confirm_password='Fine#Pass-123')
    assert b'current password is incorrect' in wrong.data
    short = _post(client, '/change-password', current_password=_default(app),
                  new_password='short', confirm_password='short')
    assert b'at least 8' in short.data
    mismatch = _post(client, '/change-password', current_password=_default(app),
                     new_password='Fine#Pass-123', confirm_password='Different#1234')
    assert b'do not match' in mismatch.data
    assert _row(app, roll)['must_change_password'] == 1


def test_student_changes_to_a_personal_password_and_is_released(app, client):
    roll = _roll(app, 3)
    _put_on_default(app, roll, flag=0)
    login(client, '/student/login', {'roll': roll, 'password': _default(app)})
    assert '/change-password' in _loc(client.get('/student/'))
    r = _post(client, '/change-password', current_password=_default(app),
              new_password='My#Own-Pass-77', confirm_password='My#Own-Pass-77')
    assert r.status_code == 302 and '/student/' in _loc(r)
    row = _row(app, roll)
    assert row['must_change_password'] == 0
    with app.app_context():
        assert security.student_has_default_password(row['id']) is False
    assert client.get('/student/').status_code == 200
    # The old shared password is dead; the new one logs in.
    other = app.test_client()
    assert login(other, '/student/login', {'roll': roll, 'password': _default(app)}).status_code == 200
    assert login(other, '/student/login',
                 {'roll': roll, 'password': 'My#Own-Pass-77'}).status_code == 302


@pytest.fixture()
def mailbox(app, monkeypatch):
    box = []

    def fake_send(to, subject, text, html=None):
        box.append({'to': to, 'code': (re.search(r'\b(\d{6})\b', text) or [None, None])[1]})
        return True

    monkeypatch.setattr(mailer, 'send_email', fake_send)
    return box


def test_forgot_password_reset_cannot_choose_the_shared_default(app, client, mailbox):
    roll = _roll(app, 4)
    set_student_password(app, roll)
    with app.app_context():
        db.execute("UPDATE users SET email='kid@gmail.com' WHERE username=?", (roll,))
    assert _post(client, '/forgot-password', role='student', ident=roll).status_code == 302
    code = mailbox[-1]['code']
    before = _row(app, roll)['password_hash']
    bad = _post(client, '/forgot-password/reset', code=code, new_password=_default(app),
                confirm_password=_default(app))
    assert bad.status_code == 200 and b'shared default password' in bad.data
    assert _row(app, roll)['password_hash'] == before
    ok = _post(client, '/forgot-password/reset', code=code, new_password='Reset#Pass-4321',
               confirm_password='Reset#Pass-4321')
    assert ok.status_code == 302 and '/student/login' in _loc(ok)
    assert login(app.test_client(), '/student/login',
                 {'roll': roll, 'password': 'Reset#Pass-4321'}).status_code == 302


# --------------------------------------------------------------------------- 6: admin reset
def test_admin_reset_to_default_forces_the_student_to_change_it(app, admin):
    roll = _roll(app, 5)
    uid = _uid(app, roll)
    set_student_password(app, roll)                   # personal password, flag 0
    tok = token_from(admin, '/admin/students')
    r = admin.post(f'/admin/users/{uid}/reset-password', data={'_csrf': tok, 'role': 'student'})
    assert r.status_code == 302
    row = _row(app, roll)
    assert row['must_change_password'] == 1
    with app.app_context():
        assert security.student_has_default_password(uid) is True
        entry = db.query("SELECT actor_label, detail FROM audit_log WHERE action='user.reset_password' "
                         "AND entity_id=? ORDER BY id DESC LIMIT 1", (uid,), one=True)
    assert entry is not None and 'student' in entry['detail']        # audit behaviour kept
    # The student signs in with the default and is held at change-password.
    stu = app.test_client()
    assert login(stu, '/student/login', {'roll': roll, 'password': _default(app)}).status_code == 302
    got = stu.get('/student/')
    assert got.status_code == 302 and '/change-password' in _loc(got)


def test_gate_still_holds_if_a_reset_account_loses_its_flag(app):
    """Defence in depth: even with the flag cleared, the stored default still forces a change."""
    roll = _roll(app, 5)
    _put_on_default(app, roll, flag=0)
    stu = app.test_client()
    login(stu, '/student/login', {'roll': roll, 'password': _default(app)})
    assert '/change-password' in _loc(stu.get('/student/'))


def test_admin_reset_refuses_when_default_is_not_configured(app, admin, monkeypatch):
    roll = _roll(app, 5)
    uid = _uid(app, roll)
    set_student_password(app, roll)
    before = _row(app, roll)['password_hash']
    for blank in (None, '', '   '):
        monkeypatch.setitem(app.config, 'DEFAULT_STUDENT_PASSWORD', blank)
        tok = token_from(admin, '/admin/students')
        r = admin.post(f'/admin/users/{uid}/reset-password', data={'_csrf': tok})
        assert r.status_code == 302
        row = _row(app, roll)
        assert row['password_hash'] == before and row['must_change_password'] == 0


# --------------------------------------------------------------------------- 7: personal passwords
def test_personal_password_student_keeps_normal_login_and_access(app, client):
    roll = _roll(app, 6)
    assert login_student(client, roll).status_code == 302
    for path in ('/student/', '/student/notifications'):
        assert client.get(path).status_code == 200, path
    assert _row(app, roll)['must_change_password'] == 0       # nothing was written/forced
    # ... and the shared default does not work for this account.
    assert login(app.test_client(), '/student/login',
                 {'roll': roll, 'password': _default(app)}).status_code == 200


# --------------------------------------------------------------------------- 8: scan login
def _scan(client, code):
    tok = token_from(client, '/student/login')
    return client.post('/scan-login', data={'code': code, '_csrf': tok})


def test_scan_login_still_works_for_default_students_but_does_not_skip_the_change(app, client):
    roll = _roll(app, 7)
    _put_on_default(app, roll, flag=0)
    r = _scan(client, roll)
    assert r.status_code == 200 and r.get_json()['ok'] is True        # scan login not disabled
    got = client.get('/student/')                                      # a scan is not a personal login
    assert got.status_code == 302 and '/change-password' in _loc(got)
    assert _row(app, roll)['must_change_password'] == 1


def test_scan_login_for_personal_password_student_only_fills_the_roll(app, client):
    roll = _roll(app, 7)
    set_student_password(app, roll)
    body = _scan(client, roll).get_json()
    assert body['ok'] is False and body.get('roll') == roll            # unchanged behaviour
    assert client.get('/student/').status_code == 302                  # not signed in
    assert '/student/login' in _loc(client.get('/student/'))


def test_scan_login_with_blank_default_config_does_not_match_anyone(app, client, monkeypatch):
    roll = _roll(app, 7)
    with app.app_context():
        db.execute("UPDATE users SET password_hash=? WHERE username=?", (hash_password(''), roll))
    monkeypatch.setitem(app.config, 'DEFAULT_STUDENT_PASSWORD', '')
    body = _scan(client, roll).get_json()
    assert body['ok'] is False                  # an empty-password hash is not treated as "default"


def test_scan_only_default_switch_behaviour_is_unchanged(app, client, monkeypatch):
    """SCAN_LOGIN_ONLY_DEFAULT=0 is an operator choice and C1.1 does not alter it
    (see the report: this setting still lets a scan sign in a personal-password student)."""
    roll = _roll(app, 7)
    set_student_password(app, roll)
    monkeypatch.setitem(app.config, 'SCAN_LOGIN_ONLY_DEFAULT', False)
    assert _scan(client, roll).get_json()['ok'] is True
    assert client.get('/student/').status_code == 200


# --------------------------------------------------------------------------- 9, 10: other roles
def test_teacher_password_change_behaviour_is_unchanged(app, client):
    with app.app_context():
        db.execute("UPDATE users SET password_hash=?, must_change_password=0, "
                   "first_login_complete=1 WHERE emp_code='6817'", (hash_password('teacher@123'),))
    login(client, '/teacher/login', {'emp_code': '6817', 'password': 'teacher@123'})
    assert '/change-password' in _loc(client.get('/teacher/'))          # teacher default still forces
    same = _post(client, '/change-password', current_password='teacher@123',
                 new_password='teacher@123', confirm_password='teacher@123')
    assert b'not the default' in same.data                              # teacher message unchanged
    # A teacher MAY use the *student* default string: that rule is student-only.
    ok = _post(client, '/change-password', current_password='teacher@123',
               new_password=_default(app), confirm_password=_default(app))
    assert ok.status_code == 302
    assert client.get('/teacher/').status_code == 200                  # not forced by the student rule
    with app.app_context():
        assert db.query("SELECT must_change_password m FROM users WHERE emp_code='6817'",
                        one=True)['m'] == 0


def test_teacher_with_personal_password_is_never_forced(app, client):
    with app.app_context():
        db.execute("UPDATE users SET password_hash=?, must_change_password=0, "
                   "first_login_complete=1 WHERE emp_code='6817'", (hash_password('Teach#Own-55'),))
    assert login(client, '/teacher/login',
                 {'emp_code': '6817', 'password': 'Teach#Own-55'}).status_code == 302
    assert client.get('/teacher/').status_code == 200


def test_admin_password_change_behaviour_is_unchanged(app, admin):
    r = _post(admin, '/change-password', current_password='admin123',
              new_password='NewAdmin#Pass-9', confirm_password='NewAdmin#Pass-9')
    assert r.status_code == 302 and '/admin/' in _loc(r)
    assert admin.get('/admin/').status_code == 200
    # Admins are not subject to the student-default rule.
    r2 = _post(admin, '/change-password', current_password='NewAdmin#Pass-9',
               new_password=_default(app), confirm_password=_default(app))
    assert r2.status_code == 302
    assert admin.get('/admin/').status_code == 200                     # never forced
    bad = _post(admin, '/change-password', current_password='wrong-current',
                new_password='Whatever#1234', confirm_password='Whatever#1234')
    assert b'current password is incorrect' in bad.data


# --------------------------------------------------------------------------- 11: e-mail / SSO
def test_default_password_student_can_still_reach_email_and_sso_endpoints(app, client):
    roll = _roll(app, 3)
    _put_on_default(app, roll, flag=1)
    login(client, '/student/login', {'roll': roll, 'password': _default(app)})
    assert client.get('/setup/email').status_code == 200               # exempt, as before
    r = client.get('/sso/google/start')                                 # provider off in tests
    assert r.status_code == 302 and '/setup/email' in _loc(r)
    assert client.get('/logout').status_code == 302


# --------------------------------------------------------------------------- 12: configuration
@pytest.mark.parametrize('bad', [None, '', '   ', 12345, b'bytes'])
def test_unusable_default_configuration_is_handled_safely(app, client, monkeypatch, bad):
    roll = _roll(app, 3)
    _put_on_default(app, roll, flag=0)                # hash made from the real default
    monkeypatch.setitem(app.config, 'DEFAULT_STUDENT_PASSWORD', bad)
    with app.app_context():
        assert security.configured_student_default() is None
        assert security.is_student_default_password('anything-at-all') is False
        assert security.is_student_default_password(bad) is False
        assert security.student_has_default_password(_uid(app, roll)) is False
    # No crash, no lock-out of every student: a login simply is not treated as "default".
    real = hash_password('Personal#Pass-xyz')
    with app.app_context():
        db.execute("UPDATE users SET password_hash=? WHERE username=?", (real, roll))
    assert login(client, '/student/login',
                 {'roll': roll, 'password': 'Personal#Pass-xyz'}).status_code == 302
    assert client.get('/student/').status_code == 200


def test_missing_default_key_and_no_app_context_are_safe(app, monkeypatch):
    assert security.configured_student_default() is None               # no app context at all
    assert security.is_student_default_password('x') is False
    monkeypatch.delitem(app.config, 'DEFAULT_STUDENT_PASSWORD')
    with app.app_context():
        assert security.configured_student_default() is None
        assert security.student_has_default_password(1) is False


def test_changed_default_configuration_follows_the_new_value(app, client, monkeypatch):
    roll_old, roll_new = _roll(app, 0), _roll(app, 1)
    _default_before = _default(app)
    _put_on_default(app, roll_old, flag=0)                              # hashed with the OLD default
    monkeypatch.setitem(app.config, 'DEFAULT_STUDENT_PASSWORD', 'Rotated#Default-8')
    _put_on_default(app, roll_new, flag=0)                              # hashed with the NEW default
    with app.app_context():
        assert security.student_has_default_password(_uid(app, roll_new)) is True
        assert security.student_has_default_password(_uid(app, roll_old)) is False
    # The new default is the one students may not choose; the retired value is no
    # longer recognised as "the shared default".
    with app.app_context():
        assert security.is_student_default_password('Rotated#Default-8') is True
        assert security.is_student_default_password(_default_before) is False


def test_previously_forced_student_stays_forced_after_the_default_changes(app, client, monkeypatch):
    roll = _roll(app, 2)
    _put_on_default(app, roll, flag=0)
    login(client, '/student/login', {'roll': roll, 'password': _default(app)})
    assert '/change-password' in _loc(client.get('/student/'))          # detected -> flag saved
    assert _row(app, roll)['must_change_password'] == 1
    monkeypatch.setitem(app.config, 'DEFAULT_STUDENT_PASSWORD', 'Rotated#Default-8')
    security._DEFAULT_MATCH_CACHE.clear()
    got = client.get('/student/')                                       # hash no longer matches ...
    assert got.status_code == 302 and '/change-password' in _loc(got)   # ... but the saved flag holds
