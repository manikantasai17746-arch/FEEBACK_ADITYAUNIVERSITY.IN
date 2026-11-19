"""Authenticator recovery - Checkpoint 1 (service layer only).

Covers: backup-code count and regeneration, the recovery verifier (TOTP or backup code
under its own ``otpreset:`` rate-limit key), replay protection, lost-device reset, the
security notice e-mails, and that every function works for a non-student role.
No routes or templates are involved.
"""
import time

import pyotp
import pytest

from feedback_system import authenticator as auth
from feedback_system import db, mailer
from feedback_system.security import _db_key

STUDENT_ROLL = '25B11CS017'


def _uid(app, role, username=None):
    with app.app_context():
        if username:
            row = db.query('SELECT id FROM users WHERE role=? AND username=?',
                           (role, username), one=True)
        else:
            row = db.query('SELECT id FROM users WHERE role=? ORDER BY id LIMIT 1',
                           (role,), one=True)
        return row['id']


def _wipe(app, uid):
    with app.app_context():
        db.execute('DELETE FROM student_backup_codes WHERE user_id=?', (uid,))
        db.execute('DELETE FROM student_authenticators WHERE user_id=?', (uid,))
        auth.clear_otp_failures(uid)
        auth.clear_reset_failures(uid)


@pytest.fixture(params=['student', 'teacher'])
def uid(app, request):
    """The same tests run for a student and a teacher: the service is role-agnostic."""
    user_id = _uid(app, request.param, STUDENT_ROLL if request.param == 'student' else None)
    _wipe(app, user_id)
    yield user_id
    _wipe(app, user_id)


@pytest.fixture()
def key_override(app):
    saved = {}

    def set_(name, value):
        saved.setdefault(name, app.config.get(name))
        app.config[name] = value

    yield set_
    for name, value in saved.items():
        app.config[name] = value


def _enroll(app, user_id):
    with app.app_context():
        secret = auth.begin_enrollment(user_id)
        assert auth.confirm_enrollment(user_id, pyotp.TOTP(secret).now())
    return secret


def _fresh(secret, offset):
    """A valid code for a step ``offset`` seconds ahead, and the ``now`` to verify it at."""
    now = time.time() + offset
    return pyotp.TOTP(secret).at(now), now


# --------------------------------------------------------------------------- #
# Backup codes: count and regeneration
# --------------------------------------------------------------------------- #
def test_remaining_count_starts_at_zero_and_drops_when_a_code_is_used(app, uid):
    _enroll(app, uid)
    with app.app_context():
        assert auth.backup_codes_remaining(uid) == 0
        codes = auth.regenerate_backup_codes(uid)
        assert len(codes) == auth.BACKUP_CODE_COUNT
        assert auth.backup_codes_remaining(uid) == auth.BACKUP_CODE_COUNT
        assert auth.consume_backup_code(uid, codes[0])
        assert auth.backup_codes_remaining(uid) == auth.BACKUP_CODE_COUNT - 1


def test_regenerating_invalidates_every_old_code(app, uid):
    _enroll(app, uid)
    with app.app_context():
        old = auth.regenerate_backup_codes(uid)
        new = auth.regenerate_backup_codes(uid)
        assert set(old).isdisjoint(new)
        assert not auth.consume_backup_code(uid, old[0])
        assert auth.consume_backup_code(uid, new[0])


def test_regenerate_needs_an_active_authenticator(app, uid):
    with app.app_context():
        with pytest.raises(auth.AuthenticatorStateError):
            auth.regenerate_backup_codes(uid)          # nothing enrolled
        auth.begin_enrollment(uid)
        with pytest.raises(auth.AuthenticatorStateError):
            auth.regenerate_backup_codes(uid)          # still pending
        assert auth.backup_codes_remaining(uid) == 0


def test_plaintext_backup_codes_are_never_stored(app, uid):
    _enroll(app, uid)
    with app.app_context():
        codes = auth.regenerate_backup_codes(uid)
        stored = ' '.join(r['code_hash'] for r in
                          db.query('SELECT code_hash FROM student_backup_codes WHERE user_id=?',
                                   (uid,)))
        for c in codes:
            assert c not in stored and c.replace('-', '') not in stored


# --------------------------------------------------------------------------- #
# Recovery verifier
# --------------------------------------------------------------------------- #
def test_recovery_accepts_a_current_totp_code(app, uid):
    secret = _enroll(app, uid)
    code, now = _fresh(secret, 60)
    with app.app_context():
        assert auth.verify_recovery_code(uid, code, now=now) == (True, 'totp')


def test_recovery_totp_cannot_be_replayed_here_or_at_sign_in(app, uid):
    secret = _enroll(app, uid)
    code, now = _fresh(secret, 60)
    with app.app_context():
        assert auth.verify_recovery_code(uid, code, now=now) == (True, 'totp')
        assert auth.verify_recovery_code(uid, code, now=now) == (False, 'invalid')
        # The same code is also dead for the sign-in gate (shared last_step).
        assert auth.verify_second_factor(uid, code, now=now) == (False, 'invalid')


def test_recovery_accepts_a_backup_code_once(app, uid):
    _enroll(app, uid)
    with app.app_context():
        codes = auth.regenerate_backup_codes(uid)
        assert auth.verify_recovery_code(uid, codes[3]) == (True, 'backup')
        assert auth.verify_recovery_code(uid, codes[3]) == (False, 'invalid')
        assert auth.backup_codes_remaining(uid) == auth.BACKUP_CODE_COUNT - 1


def test_recovery_refuses_malformed_input_without_error(app, uid, key_override):
    key_override('OTP_MAX_ATTEMPTS', 50)      # this test checks input handling, not the limit
    _enroll(app, uid)
    with app.app_context():
        for bad in (None, '', '12345', '1234567', 'abcdef', ' ', 'x' * 500):
            assert auth.verify_recovery_code(uid, bad) == (False, 'invalid')


def test_recovery_for_an_account_without_authenticator_is_not_counted(app, uid):
    with app.app_context():
        for _ in range(10):
            assert auth.verify_recovery_code(uid, '000000') == (False, 'not_enrolled')
        assert auth.reset_locked_seconds(uid) == 0


def test_recovery_for_a_pending_enrollment_is_refused(app, uid):
    with app.app_context():
        secret = auth.begin_enrollment(uid)
        assert auth.verify_recovery_code(uid, pyotp.TOTP(secret).now()) == (False, 'not_enrolled')


# --------------------------------------------------------------------------- #
# Rate limiting under its own key
# --------------------------------------------------------------------------- #
def test_failed_recovery_attempts_lock_recovery_even_for_the_right_code(app, uid, key_override):
    key_override('OTP_MAX_ATTEMPTS', 3)
    secret = _enroll(app, uid)
    code, now = _fresh(secret, 120)
    with app.app_context():
        for _ in range(3):
            assert auth.verify_recovery_code(uid, '000000', now=now) == (False, 'invalid')
        assert auth.reset_locked_seconds(uid) > 0
        assert auth.verify_recovery_code(uid, code, now=now) == (False, 'locked')
        auth.clear_reset_failures(uid)
        assert auth.verify_recovery_code(uid, code, now=now) == (True, 'totp')


def test_recovery_failures_do_not_lock_the_sign_in_gate(app, uid, key_override):
    """Someone guessing at /forgot-password must not lock the real user out of sign-in."""
    key_override('OTP_MAX_ATTEMPTS', 3)
    secret = _enroll(app, uid)
    code, now = _fresh(secret, 120)
    with app.app_context():
        for _ in range(5):
            auth.verify_recovery_code(uid, '000000', now=now)
        assert auth.reset_locked_seconds(uid) > 0
        assert auth.otp_locked_seconds(uid) == 0
        assert auth.verify_second_factor(uid, code, now=now) == (True, 'totp')


def test_sign_in_failures_do_not_lock_recovery(app, uid, key_override):
    """The user who mistypes at sign-in can still recover with a backup code."""
    key_override('OTP_MAX_ATTEMPTS', 3)
    _enroll(app, uid)
    with app.app_context():
        codes = auth.regenerate_backup_codes(uid)
        later = time.time() + 120
        for _ in range(4):
            auth.verify_second_factor(uid, '000000', now=later)
        assert auth.otp_locked_seconds(uid) > 0
        assert auth.reset_locked_seconds(uid) == 0
        assert auth.verify_recovery_code(uid, codes[0]) == (True, 'backup')


def test_recovery_failures_are_counted_in_the_shared_table_under_otpreset(app, uid):
    _enroll(app, uid)
    with app.app_context():
        auth.verify_recovery_code(uid, '000000', now=time.time() + 120)
        row = db.query('SELECT fail_count FROM login_attempts WHERE attempt_key=?',
                       (_db_key(f'otpreset:{uid}'),), one=True)
        assert row is not None and row['fail_count'] >= 1


def test_a_successful_recovery_clears_the_recovery_counter(app, uid, key_override):
    key_override('OTP_MAX_ATTEMPTS', 5)
    secret = _enroll(app, uid)
    code, now = _fresh(secret, 120)
    with app.app_context():
        auth.verify_recovery_code(uid, '000000', now=now)
        assert auth.verify_recovery_code(uid, code, now=now) == (True, 'totp')
        row = db.query('SELECT fail_count FROM login_attempts WHERE attempt_key=?',
                       (_db_key(f'otpreset:{uid}'),), one=True)
        assert row is None


# --------------------------------------------------------------------------- #
# Lost-device reset
# --------------------------------------------------------------------------- #
def test_lost_device_reset_removes_authenticator_and_all_remaining_codes(app, uid):
    _enroll(app, uid)
    with app.app_context():
        codes = auth.regenerate_backup_codes(uid)
        assert auth.verify_recovery_code(uid, codes[0]) == (True, 'backup')
        assert auth.complete_lost_device_reset(uid) is True
        assert not auth.is_active(uid)
        assert auth.backup_codes_remaining(uid) == 0
        # The remaining nine codes are gone too, and nothing verifies any more.
        assert auth.verify_recovery_code(uid, codes[1]) == (False, 'not_enrolled')
        assert auth.verify_second_factor(uid, codes[1]) == (False, 'not_enrolled')


def test_lost_device_reset_lets_the_user_enrol_a_new_authenticator(app, uid):
    old = _enroll(app, uid)
    with app.app_context():
        auth.complete_lost_device_reset(uid)
        new = auth.begin_enrollment(uid)
        assert new != old
        assert auth.confirm_enrollment(uid, pyotp.TOTP(new).now())
        assert auth.is_active(uid)


def test_lost_device_reset_clears_a_recovery_lockout_and_is_safe_when_nothing_exists(
        app, uid, key_override):
    key_override('OTP_MAX_ATTEMPTS', 2)
    _enroll(app, uid)
    with app.app_context():
        for _ in range(3):
            auth.verify_recovery_code(uid, '000000', now=time.time() + 120)
        assert auth.reset_locked_seconds(uid) > 0
        assert auth.complete_lost_device_reset(uid) is True
        assert auth.reset_locked_seconds(uid) == 0
        assert auth.complete_lost_device_reset(uid) is False


# --------------------------------------------------------------------------- #
# Security notice e-mails
# --------------------------------------------------------------------------- #
@pytest.fixture()
def sent(monkeypatch):
    box = []
    monkeypatch.setattr(mailer, 'send_email',
                        lambda to, subject, text, html=None, attachments=None:
                        box.append({'to': to, 'subject': subject, 'text': text}) or True)
    return box


@pytest.mark.parametrize('event', ['password_reset', 'backup_code_used', 'lost_device_reset',
                                   'backup_codes_regenerated', 'authenticator_reset'])
def test_security_notice_is_sent_and_contains_no_secret_material(sent, event):
    assert mailer.security_notice('someone@example.com', 'Dr Rao', event) is True
    msg = sent[0]
    assert msg['to'] == 'someone@example.com'
    assert 'Dr Rao' in msg['text'] and 'contact the administrator' in msg['text']
    for banned in ('http', 'otpauth', 'secret', 'password:', 'code:'):
        assert banned not in msg['text'].lower()


# --------------------------------------------------------------------------- #
# A backup code used for a NORMAL reset must never remove the authenticator
# --------------------------------------------------------------------------- #
def test_backup_code_in_a_normal_reset_keeps_the_authenticator_and_other_codes(app, uid):
    secret = _enroll(app, uid)
    with app.app_context():
        codes = auth.regenerate_backup_codes(uid)
        assert auth.verify_recovery_code(uid, codes[0]) == (True, 'backup')
        # Same authenticator, still active, only the one code was spent.
        assert auth.is_active(uid)
        assert auth.backup_codes_remaining(uid) == auth.BACKUP_CODE_COUNT - 1
        assert auth.decrypt_secret(
            db.query('SELECT secret_enc FROM student_authenticators WHERE user_id=?',
                     (uid,), one=True)['secret_enc']) == secret
        # The old phone still works at sign-in, and another backup code still works.
        code, now = _fresh(secret, 90)
        assert auth.verify_second_factor(uid, code, now=now) == (True, 'totp')
        assert auth.verify_recovery_code(uid, codes[1]) == (True, 'backup')


def test_spending_every_backup_code_never_removes_the_authenticator(app, uid, key_override):
    key_override('OTP_MAX_ATTEMPTS', 50)
    _enroll(app, uid)
    with app.app_context():
        for c in auth.regenerate_backup_codes(uid):
            assert auth.verify_recovery_code(uid, c) == (True, 'backup')
        assert auth.is_active(uid)
        assert auth.backup_codes_remaining(uid) == 0


def test_a_totp_reset_also_keeps_the_authenticator_and_codes(app, uid):
    secret = _enroll(app, uid)
    with app.app_context():
        auth.regenerate_backup_codes(uid)
        code, now = _fresh(secret, 60)
        assert auth.verify_recovery_code(uid, code, now=now) == (True, 'totp')
        assert auth.is_active(uid)
        assert auth.backup_codes_remaining(uid) == auth.BACKUP_CODE_COUNT


def test_only_the_lost_device_function_removes_the_authenticator(app, uid, key_override):
    """Every other recovery-side call leaves it in place; complete_lost_device_reset removes it."""
    key_override('OTP_MAX_ATTEMPTS', 50)
    secret = _enroll(app, uid)
    with app.app_context():
        codes = auth.regenerate_backup_codes(uid)
        code, now = _fresh(secret, 60)
        auth.verify_recovery_code(uid, '000000', now=now)           # failed attempt
        auth.verify_recovery_code(uid, code, now=now)               # TOTP success
        auth.verify_recovery_code(uid, codes[0])                    # backup success
        auth.regenerate_backup_codes(uid)
        auth.clear_reset_failures(uid)
        assert auth.is_active(uid)
        assert auth.complete_lost_device_reset(uid) is True
        assert not auth.is_active(uid)


def test_notice_wording_matches_whether_the_authenticator_stays(sent):
    mailer.security_notice('a@example.com', 'X', 'backup_code_used')
    mailer.security_notice('a@example.com', 'X', 'lost_device_reset')
    normal, lost = sent[0]['text'].lower(), sent[1]['text'].lower()
    assert 'still set up' in normal and 'removed' not in normal
    assert 'removed' in lost and 'new authenticator' in lost



def test_security_notice_ignores_unknown_events_and_empty_addresses(sent):
    assert mailer.security_notice('someone@example.com', 'X', 'made-up') is False
    assert mailer.security_notice('', 'X', 'password_reset') is False
    assert mailer.security_notice(None, 'X', 'password_reset') is False
    assert sent == []


def test_security_notice_reports_false_when_the_mail_fails(monkeypatch):
    monkeypatch.setattr(mailer, 'send_email', lambda *a, **k: False)
    assert mailer.security_notice('someone@example.com', 'X', 'password_reset') is False
