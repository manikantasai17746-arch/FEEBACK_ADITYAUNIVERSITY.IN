"""Checkpoint 1.1 (M1): enrollment confirmation is rate limited.

``confirm_enrollment`` uses the same DB-backed lockout as ``verify_second_factor``:
a locked account is refused before any code is checked, each wrong code is
counted, and a successful confirmation clears the count.
"""
import pyotp
import pytest

from feedback_system import authenticator as auth
from feedback_system import db
from feedback_system.security import _db_key

STUDENT_ROLL = '25B11CS015'


def _wipe(app, uid):
    with app.app_context():
        db.execute('DELETE FROM student_backup_codes WHERE user_id=?', (uid,))
        db.execute('DELETE FROM student_authenticators WHERE user_id=?', (uid,))
        auth.clear_otp_failures(uid)


@pytest.fixture()
def student_uid(app):
    with app.app_context():
        uid = db.query("SELECT id FROM users WHERE role='student' AND username=?",
                       (STUDENT_ROLL,), one=True)['id']
    _wipe(app, uid)
    yield uid
    _wipe(app, uid)


@pytest.fixture()
def max_attempts(app):
    """Set the OTP limit to 3 for a test, and restore the original afterwards."""
    saved = app.config.get('OTP_MAX_ATTEMPTS')
    app.config['OTP_MAX_ATTEMPTS'] = 3
    yield 3
    app.config['OTP_MAX_ATTEMPTS'] = saved


def _failure_row(uid):
    return db.query('SELECT fail_count FROM login_attempts WHERE attempt_key=?',
                    (_db_key(f'otp:{uid}'),), one=True)


def test_wrong_confirmations_lock_the_enrollment(app, student_uid, max_attempts):
    with app.app_context():
        secret = auth.begin_enrollment(student_uid)
        for _ in range(max_attempts):
            assert not auth.confirm_enrollment(student_uid, '000000')
        assert auth.otp_locked_seconds(student_uid) > 0
        # The lock is checked before the code: a correct code is refused while locked.
        assert not auth.confirm_enrollment(student_uid, pyotp.TOTP(secret).now())
        assert not auth.is_active(student_uid)


def test_each_wrong_confirmation_is_counted(app, student_uid, max_attempts):
    with app.app_context():
        auth.begin_enrollment(student_uid)
        auth.confirm_enrollment(student_uid, '000000')
        auth.confirm_enrollment(student_uid, '111111')
        assert _failure_row(student_uid)['fail_count'] == 2


def test_successful_confirmation_clears_earlier_failures(app, student_uid, max_attempts):
    with app.app_context():
        secret = auth.begin_enrollment(student_uid)
        auth.confirm_enrollment(student_uid, '000000')
        auth.confirm_enrollment(student_uid, '000000')
        assert auth.confirm_enrollment(student_uid, pyotp.TOTP(secret).now())
        assert auth.is_active(student_uid)
        assert auth.otp_locked_seconds(student_uid) == 0
        assert _failure_row(student_uid) is None


def test_confirmation_with_no_pending_secret_checks_nothing_and_counts_nothing(
        app, student_uid, max_attempts):
    with app.app_context():
        assert not auth.confirm_enrollment(student_uid, '123456')
        assert _failure_row(student_uid) is None
