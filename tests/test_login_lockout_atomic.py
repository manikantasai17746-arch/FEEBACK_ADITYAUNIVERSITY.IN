"""Checkpoint 1.2 (M2): atomic lockout counter.

The DB-backed counter increments with single statements, and the OTP path reserves an
attempt before checking its code. These tests run real threads, each with its own app
context and database connection, released together by a barrier.
"""
import threading
import time
import uuid

import pyotp
import pytest

from feedback_system import authenticator as auth
from feedback_system import db
from feedback_system import pg
from feedback_system import security
from feedback_system.security import (_db_key, clear_login_failures, login_locked,
                                      register_login_failure, reserve_login_attempt)

STUDENT_ROLL = '25B11CS015'


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _key():
    return f'cp12-lockout-{uuid.uuid4().hex}'


def _fail_count(key):
    row = db.query('SELECT fail_count FROM login_attempts WHERE attempt_key=?',
                   (_db_key(key),), one=True)
    return None if row is None else row['fail_count']


def _run_concurrently(app, n, work):
    """Run ``work(i)`` in ``n`` threads released together. Returns (results, errors)."""
    barrier = threading.Barrier(n)
    results, errors = [None] * n, []

    def runner(i):
        try:
            barrier.wait(timeout=30)
            with app.app_context():
                results[i] = work(i)
        except Exception as exc:  # surfaced to the test, never swallowed
            errors.append(exc)

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return results, errors


@pytest.fixture()
def key(app):
    k = _key()
    yield k
    with app.app_context():
        clear_login_failures(k)


@pytest.fixture()
def student_uid(app):
    with app.app_context():
        uid = db.query("SELECT id FROM users WHERE role='student' AND username=?",
                       (STUDENT_ROLL,), one=True)['id']

    def wipe():
        with app.app_context():
            db.execute('DELETE FROM student_backup_codes WHERE user_id=?', (uid,))
            db.execute('DELETE FROM student_authenticators WHERE user_id=?', (uid,))
            auth.clear_otp_failures(uid)

    wipe()
    yield uid
    wipe()


@pytest.fixture()
def otp_max(app):
    saved = app.config.get('OTP_MAX_ATTEMPTS')
    app.config['OTP_MAX_ATTEMPTS'] = 5
    yield 5
    app.config['OTP_MAX_ATTEMPTS'] = saved


# --------------------------------------------------------------------------- #
# Lost increments
# --------------------------------------------------------------------------- #
def test_concurrent_failures_are_never_lost(app):
    """Three rounds of 16 simultaneous failures, each on its own key: no increment is lost."""
    keys = [_key() for _ in range(3)]
    try:
        for k in keys:
            _, errors = _run_concurrently(
                app, 16, lambda i, k=k: register_login_failure(k, 10_000, 300))
            assert not errors, errors
        with app.app_context():
            for k in keys:
                assert _fail_count(k) == 16
                assert login_locked(k) == 0
    finally:
        with app.app_context():
            for k in keys:
                clear_login_failures(k)


def test_one_row_per_key_under_concurrent_first_failures(app, key):
    _, errors = _run_concurrently(app, 12, lambda i: register_login_failure(key, 10_000, 300))
    assert not errors, errors
    with app.app_context():
        rows = db.query('SELECT COUNT(*) c FROM login_attempts WHERE attempt_key=?',
                        (_db_key(key),), one=True)['c']
        assert rows == 1


# --------------------------------------------------------------------------- #
# Threshold, reservation and reset
# --------------------------------------------------------------------------- #
def test_lock_engages_at_exactly_the_threshold(app, key):
    with app.app_context():
        for _ in range(4):
            register_login_failure(key, 5, 300)
        assert login_locked(key) == 0          # four failures: not yet locked
        register_login_failure(key, 5, 300)
        assert login_locked(key) > 0           # the fifth locks


def test_sequential_reservations_allow_exactly_the_limit(app, key):
    with app.app_context():
        allowed = [reserve_login_attempt(key, 5, 300) for _ in range(6)]
        assert allowed == [True, True, True, True, True, False]
        assert login_locked(key) > 0


def test_concurrent_reservations_never_exceed_the_limit(app):
    """Three rounds of 24 simultaneous attempts against a limit of 5."""
    keys = [_key() for _ in range(3)]
    try:
        for k in keys:
            results, errors = _run_concurrently(
                app, 24, lambda i, k=k: reserve_login_attempt(k, 5, 300))
            assert not errors, errors
            allowed = results.count(True)
            assert 1 <= allowed <= 5, f'{allowed} attempts allowed against a limit of 5'
            with app.app_context():
                assert login_locked(k) > 0
    finally:
        with app.app_context():
            for k in keys:
                clear_login_failures(k)


def test_failures_outside_the_window_stop_counting(app, key):
    with app.app_context():
        register_login_failure(key, 3, 300, window_seconds=900)
        register_login_failure(key, 3, 300, window_seconds=900)
        db.execute('UPDATE login_attempts SET first_ts=? WHERE attempt_key=?',
                   (int(time.time()) - 5000, _db_key(key)))
        register_login_failure(key, 3, 300, window_seconds=900)
        assert _fail_count(key) == 1           # a fresh window starts at 1
        assert login_locked(key) == 0
        register_login_failure(key, 3, 300, window_seconds=900)
        assert login_locked(key) == 0          # two in the new window: still not locked


def test_attempts_refused_during_a_lockout_do_not_extend_it(app, key):
    """Refused reservations are not counted, so they cannot re-lock or extend the lock."""
    with app.app_context():
        for _ in range(3):
            assert reserve_login_attempt(key, 3, 300) in (True,)
        assert login_locked(key) > 0
        until = db.query('SELECT until_ts FROM login_attempts WHERE attempt_key=?',
                         (_db_key(key),), one=True)['until_ts']
        for _ in range(10):
            assert reserve_login_attempt(key, 3, 300) is False
        row = db.query('SELECT fail_count, until_ts FROM login_attempts WHERE attempt_key=?',
                       (_db_key(key),), one=True)
        assert row['until_ts'] == until
        assert row['fail_count'] == 0


def test_expired_lock_no_longer_blocks(app, key):
    with app.app_context():
        register_login_failure(key, 1, 300)
        assert login_locked(key) > 0
        db.execute('UPDATE login_attempts SET until_ts=? WHERE attempt_key=?',
                   (int(time.time()) - 1, _db_key(key)))
        assert login_locked(key) == 0


# --------------------------------------------------------------------------- #
# OTP path: real TOTP verification under concurrency
# --------------------------------------------------------------------------- #
def _enroll(app, uid):
    with app.app_context():
        secret = auth.begin_enrollment(uid)
        assert auth.confirm_enrollment(uid, pyotp.TOTP(secret).now())
    with app.app_context():
        row = db.query('SELECT secret_enc FROM student_authenticators WHERE user_id=?',
                       (uid,), one=True)
        return auth.decrypt_secret(row['secret_enc'])


def test_concurrent_wrong_codes_never_exceed_the_otp_limit(app, student_uid, otp_max):
    """20 simultaneous wrong codes against a limit of 5: at most 5 are checked."""
    secret = _enroll(app, student_uid)
    wrong = pyotp.TOTP(secret).at(0)           # a code from 1970: never in the window
    results, errors = _run_concurrently(
        app, 20, lambda i: auth.verify_second_factor(student_uid, wrong)[1])
    assert not errors, errors
    checked = results.count('invalid')
    assert 1 <= checked <= otp_max, f'{checked} codes checked against a limit of {otp_max}'
    assert results.count('locked') == 20 - checked
    with app.app_context():
        assert auth.otp_locked_seconds(student_uid) > 0
        good = pyotp.TOTP(secret).now()
        assert auth.verify_second_factor(student_uid, good) == (False, 'locked')


def test_otp_lock_engages_at_the_configured_threshold(app, student_uid, otp_max):
    secret = _enroll(app, student_uid)
    wrong = pyotp.TOTP(secret).at(0)
    with app.app_context():
        reasons = [auth.verify_second_factor(student_uid, wrong)[1] for _ in range(6)]
        assert reasons == ['invalid'] * 5 + ['locked']
        assert auth.otp_locked_seconds(student_uid) > 0


def test_successful_verification_resets_the_otp_counter(app, student_uid, otp_max):
    secret = _enroll(app, student_uid)
    wrong = pyotp.TOTP(secret).at(0)
    later = time.time() + 120                  # a step that has not been used yet
    with app.app_context():
        for _ in range(3):
            assert auth.verify_second_factor(student_uid, wrong)[1] == 'invalid'
        assert auth.verify_second_factor(student_uid, pyotp.TOTP(secret).at(later),
                                         now=later) == (True, 'totp')
        assert db.query('SELECT 1 FROM login_attempts WHERE attempt_key=?',
                        (_db_key(f'otp:{student_uid}'),), one=True) is None
        # After the reset, four more failures do not lock: the count starts again.
        reasons = [auth.verify_second_factor(student_uid, wrong)[1] for _ in range(4)]
        assert reasons == ['invalid'] * 4
        assert auth.otp_locked_seconds(student_uid) == 0


# --------------------------------------------------------------------------- #
# Portability of the counter SQL
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('stmt_name', ['_SQL_ENSURE_ROW', '_SQL_RESET_EXPIRED',
                                       '_SQL_INCREMENT', '_SQL_READ', '_SQL_LOCK'])
def test_counter_sql_translates_to_postgres(stmt_name):
    sqlite_sql = getattr(security, stmt_name)
    pg_sql = pg.to_postgres(sqlite_sql)
    assert '?' not in pg_sql                   # placeholders converted to %s
    assert pg_sql.count('%s') == sqlite_sql.count('?')
    assert 'OR IGNORE' not in pg_sql and 'AUTOINCREMENT' not in pg_sql
    if stmt_name == '_SQL_ENSURE_ROW':
        assert 'ON CONFLICT(attempt_key) DO NOTHING' in pg_sql
