"""C1.1 (revised): students may keep the shared default password; changing it is optional.

* detection uses the stored hash (not ``must_change_password``) and the configured
  default, never a hard-coded value;
* a student cannot CHOOSE the shared default (change-password or reset);
* a default-password student is NOT forced to change it: login (and ID-card scan) clear any
  old forced-change flag and the protected pages open; the Password page always works;
* personal-password students are unaffected;
* teacher and admin password changes are unchanged;
* missing or changed configuration is handled safely.
"""
from unittest import mock

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from feedback_system import db
from feedback_system.security import (hash_password, is_default_student_hash,
                                      is_default_student_password,
                                      student_default_passwords)
from helpers import login, token_from

ROLL = '25B11CS016'            # a student no other test depends on
PERSONAL = 'Own-pass-C11-1'
PERSONAL_2 = 'Own-pass-C11-2'


@pytest.fixture()
def key_override(app):
    saved = {}

    def set_(name, value):
        saved.setdefault(name, app.config.get(name))
        app.config[name] = value

    yield set_
    for name, value in saved.items():
        app.config[name] = value


@pytest.fixture()
def student(app):
    """Yield the student's id; restore the original password hash and flag afterwards."""
    with app.app_context():
        row = db.query("SELECT id, password_hash, must_change_password FROM users "
                       "WHERE role='student' AND username=?", (ROLL,), one=True)
        uid, original = row['id'], (row['password_hash'], row['must_change_password'])
    yield uid
    with app.app_context():
        db.execute('UPDATE users SET password_hash=?, must_change_password=? WHERE id=?',
                   (original[0], original[1], uid))


def _set(app, uid, password, must_change=0):
    with app.app_context():
        db.execute('UPDATE users SET password_hash=?, must_change_password=? WHERE id=?',
                   (hash_password(password), must_change, uid))


def _stored(app, uid):
    with app.app_context():
        row = db.query('SELECT password_hash, must_change_password FROM users WHERE id=?',
                       (uid,), one=True)
        return row['password_hash'], row['must_change_password']


def _flag(app, uid):
    return _stored(app, uid)[1]


# --------------------------------------------------------------------------- #
# 1-2. Detection
# --------------------------------------------------------------------------- #
def test_hash_of_the_configured_default_is_detected(app):
    default = app.config['DEFAULT_STUDENT_PASSWORD']
    with app.app_context():
        assert is_default_student_hash(generate_password_hash(default))   # import path
        assert is_default_student_hash(hash_password(default))            # admin-reset path
        assert is_default_student_password(default)


def test_personal_password_is_not_flagged(app):
    with app.app_context():
        assert not is_default_student_hash(hash_password(PERSONAL))
        assert not is_default_student_hash(generate_password_hash(PERSONAL))
        assert not is_default_student_password(PERSONAL)


def test_helper_is_safe_for_missing_or_garbage_hashes(app):
    with app.app_context():
        for bad in (None, '', 'not-a-password-hash', 12345):
            assert is_default_student_hash(bad) is False


# --------------------------------------------------------------------------- #
# 8. Configuration changes and gaps
# --------------------------------------------------------------------------- #
def test_detection_follows_configuration_and_previous_defaults(app, key_override):
    original = app.config['DEFAULT_STUDENT_PASSWORD']
    key_override('DEFAULT_STUDENT_PASSWORD', 'New-default-C11')
    key_override('PREVIOUS_DEFAULT_STUDENT_PASSWORDS', [original])
    with app.app_context():
        assert set(student_default_passwords()) == {'New-default-C11', original}
        assert is_default_student_hash(hash_password(original))        # still on the old default
        assert is_default_student_hash(hash_password('New-default-C11'))
        assert not is_default_student_hash(hash_password(PERSONAL))


def test_empty_or_missing_default_configuration_is_safe(app, key_override):
    for value in (None, ''):
        key_override('DEFAULT_STUDENT_PASSWORD', value)
        key_override('PREVIOUS_DEFAULT_STUDENT_PASSWORDS', [])
        with app.app_context():
            assert student_default_passwords() == []
            assert is_default_student_password('') is False        # an empty value is never a default
            assert is_default_student_password(None) is False
            assert is_default_student_hash(hash_password(PERSONAL)) is False


def test_empty_default_still_refuses_an_empty_password_at_login(app, client, student, key_override):
    key_override('DEFAULT_STUDENT_PASSWORD', None)
    r = login(client, '/student/login', {'roll': ROLL, 'password': ''})
    assert r.status_code == 200                       # refused: the password is required


# --------------------------------------------------------------------------- #
# 3. Students cannot choose the shared default
# --------------------------------------------------------------------------- #
def _change(client, current, new):
    tok = token_from(client, '/change-password')
    return client.post('/change-password', data={
        '_csrf': tok, 'current_password': current,
        'new_password': new, 'confirm_password': new})


def test_student_cannot_change_password_to_the_shared_default(app, client, student):
    _set(app, student, PERSONAL)
    assert login(client, '/student/login', {'roll': ROLL, 'password': PERSONAL}).status_code == 302
    r = _change(client, PERSONAL, app.config['DEFAULT_STUDENT_PASSWORD'])
    assert b'not the default one' in r.data
    stored, _ = _stored(app, student)
    assert check_password_hash(stored, PERSONAL)               # unchanged
    with app.app_context():
        assert not is_default_student_hash(stored)


def test_student_cannot_change_to_a_previous_default_either(app, client, student, key_override):
    _set(app, student, PERSONAL)
    key_override('PREVIOUS_DEFAULT_STUDENT_PASSWORDS', ['old-default-C11'])
    assert login(client, '/student/login', {'roll': ROLL, 'password': PERSONAL}).status_code == 302
    r = _change(client, PERSONAL, 'old-default-C11')
    assert b'not the default one' in r.data
    assert check_password_hash(_stored(app, student)[0], PERSONAL)


def test_student_can_still_choose_a_personal_password(app, client, student):
    _set(app, student, PERSONAL)
    assert login(client, '/student/login', {'roll': ROLL, 'password': PERSONAL}).status_code == 302
    _change(client, PERSONAL, PERSONAL_2)
    assert check_password_hash(_stored(app, student)[0], PERSONAL_2)


def test_student_reset_by_email_code_cannot_set_the_shared_default(app, client, student):
    _set(app, student, PERSONAL)
    with client.session_transaction() as sess:
        sess['reset_who'] = ['student', ROLL]
    tok = token_from(client, '/forgot-password/reset')
    default = app.config['DEFAULT_STUDENT_PASSWORD']
    with mock.patch('feedback_system.verification.check', return_value=True):
        r = client.post('/forgot-password/reset', data={
            '_csrf': tok, 'new_password': default, 'confirm_password': default, 'code': '123456'})
    assert b'not the default one' in r.data
    assert check_password_hash(_stored(app, student)[0], PERSONAL)


# --------------------------------------------------------------------------- #
# 4. Default-password students are forced to change before protected pages
# --------------------------------------------------------------------------- #
def test_default_password_student_is_not_forced_to_change(app, client, student):
    default = app.config['DEFAULT_STUDENT_PASSWORD']
    _set(app, student, default, must_change=0)
    assert login(client, '/student/login', {'roll': ROLL, 'password': default}).status_code == 302
    assert _flag(app, student) == 0
    assert client.get('/student/').status_code == 200      # opens straight away


def test_password_page_is_always_available_and_works_for_a_default_student(app, client, student):
    default = app.config['DEFAULT_STUDENT_PASSWORD']
    _set(app, student, default, must_change=0)
    assert login(client, '/student/login', {'roll': ROLL, 'password': default}).status_code == 302
    assert client.get('/change-password').status_code == 200
    _change(client, default, PERSONAL)                    # optional change still works
    assert check_password_hash(_stored(app, student)[0], PERSONAL)
    assert client.get('/student/').status_code == 200


def test_an_old_forced_change_flag_is_cleared_at_student_login(app, client, student):
    default = app.config['DEFAULT_STUDENT_PASSWORD']
    _set(app, student, default, must_change=1)            # e.g. flagged before this change
    assert login(client, '/student/login', {'roll': ROLL, 'password': default}).status_code == 302
    assert _flag(app, student) == 0
    assert client.get('/student/').status_code == 200


# --------------------------------------------------------------------------- #
# 5. Personal-password students are unaffected
# --------------------------------------------------------------------------- #
def test_personal_password_student_logs_in_and_reaches_the_dashboard(app, client, student):
    _set(app, student, PERSONAL, must_change=0)
    assert login(client, '/student/login', {'roll': ROLL, 'password': PERSONAL}).status_code == 302
    assert _flag(app, student) == 0
    assert client.get('/student/').status_code == 200


# --------------------------------------------------------------------------- #
# 6. Scan login behaves as before for ordinary access
# --------------------------------------------------------------------------- #
def _scan(client, roll):
    tok = token_from(client, '/student/login')
    return client.post('/scan-login', data={'code': roll, '_csrf': tok}).get_json()


def test_scan_login_for_a_default_student_logs_in_without_a_forced_change(
        app, client, student):
    default = app.config['DEFAULT_STUDENT_PASSWORD']
    _set(app, student, default, must_change=1)
    body = _scan(client, ROLL)
    assert body['ok'] is True and body.get('redirect')
    assert _flag(app, student) == 0
    assert client.get('/student/').status_code == 200


def test_scan_login_for_a_personal_student_still_asks_for_the_password(app, client, student):
    _set(app, student, PERSONAL, must_change=0)
    body = _scan(client, ROLL)
    assert body['ok'] is False and body['fallback'] is True and body['roll'] == ROLL
    assert _flag(app, student) == 0


def test_scan_login_follows_a_changed_default_with_previous_defaults(
        app, client, student, key_override):
    old = app.config['DEFAULT_STUDENT_PASSWORD']
    key_override('DEFAULT_STUDENT_PASSWORD', 'New-default-C11')
    key_override('PREVIOUS_DEFAULT_STUDENT_PASSWORDS', [old])
    _set(app, student, old, must_change=0)
    body = _scan(client, ROLL)
    assert body['ok'] is True and _flag(app, student) == 0


# --------------------------------------------------------------------------- #
# 7. Teacher and admin password changes are unchanged
# --------------------------------------------------------------------------- #
def test_teacher_password_rules_are_unchanged(app, client, teacher):
    default_teacher = app.config['DEFAULT_TEACHER_PASSWORD']
    r = _change(client, default_teacher, default_teacher)
    assert b'not the default one' in r.data                       # teacher rule still applies
    r = _change(client, default_teacher, 'Teacher-own-C11-1')
    with app.app_context():
        stored = db.query("SELECT password_hash FROM users WHERE emp_code='6817'", one=True)
    assert check_password_hash(stored['password_hash'], 'Teacher-own-C11-1')


def test_admin_may_still_use_the_student_default_string(app, client, admin):
    """The student rule is scoped to student accounts; admins are not restricted by it."""
    try:
        r = _change(client, 'admin123', app.config['DEFAULT_STUDENT_PASSWORD'])
        with app.app_context():
            stored = db.query("SELECT password_hash FROM users WHERE role='admin' LIMIT 1",
                              one=True)['password_hash']
        assert check_password_hash(stored, app.config['DEFAULT_STUDENT_PASSWORD'])
        assert r.status_code == 302
    finally:
        with app.app_context():
            db.execute("UPDATE users SET password_hash=?, must_change_password=0 "
                       "WHERE role='admin'", (hash_password('admin123'),))
