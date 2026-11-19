"""Checkpoint B1: the lost-device recovery lock (2 hours) and its rate limits.

Student/Teacher/HOD/Dean only; Admin is excluded. The lock must never stop normal login,
the sign-in authenticator check or forgot-password (Checkpoint A).
"""
import time

import pytest

from feedback_system import authenticator as auth
from feedback_system import db, recovery_lock as rl, security
from helpers import login

from test_authenticator_enroll_cp2 import LOGIN, ROLES, _sign_in, acct  # noqa: F401

ROLES4 = ['student', 'teacher', 'hod', 'dean']
TWO_HOURS = 7200


@pytest.fixture(autouse=True)
def _clean(app):
    with app.app_context():
        db.execute('DELETE FROM login_attempts')
        db.execute("DELETE FROM audit_log WHERE action LIKE 'user.recovery_%'")
    yield
    with app.app_context():
        db.execute('DELETE FROM login_attempts')
        db.execute("DELETE FROM audit_log WHERE action LIKE 'user.recovery_%'")


@pytest.fixture()
def clock(monkeypatch):
    """Move the lockout clock without sleeping: clock.advance(seconds)."""
    class Clock:
        offset = 0

        def advance(self, seconds):
            self.offset += seconds
    c = Clock()
    real = time.time
    monkeypatch.setattr(security, '_now', lambda: real() + c.offset)
    return c


def _user(app, a):
    return {'id': a['uid'], 'role': a['role']}


def _events(app, uid):
    with app.app_context():
        rows = db.query("SELECT action, detail FROM audit_log WHERE entity='users' AND entity_id=? "
                        "AND action LIKE 'user.recovery_%' ORDER BY id", (uid,))
        return [(r['action'], r['detail']) for r in rows]


def _five_wrong(app, u):
    with app.app_context():
        for _ in range(5):
            assert rl.begin_attempt(u) == 'ok'        # the 5th is still checked
            rl.record_failure(u)


@pytest.mark.parametrize('acct', ROLES4, indirect=True)
def test_five_wrong_codes_lock_recovery_for_two_hours(app, acct, clock):
    u = _user(app, acct)
    _five_wrong(app, u)
    with app.app_context():
        assert rl.begin_attempt(u) == 'locked'
        assert TWO_HOURS - 5 <= rl.locked_seconds(u) <= TWO_HOURS
    clock.advance(TWO_HOURS - 60)
    with app.app_context():
        assert rl.begin_attempt(u) == 'locked'        # still locked just before 2 hours
    clock.advance(120)
    with app.app_context():
        assert rl.locked_seconds(u) == 0              # unlocks by itself
        assert rl.begin_attempt(u) == 'ok'


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_a_correct_code_before_the_fifth_wrong_one_clears_the_count(app, acct):
    u = _user(app, acct)
    with app.app_context():
        for _ in range(4):
            assert rl.begin_attempt(u) == 'ok'
            rl.record_failure(u)
        assert rl.begin_attempt(u) == 'ok'
        rl.record_success(u)                          # right code on the 5th try
        assert rl.locked_seconds(u) == 0
        for _ in range(4):                            # a fresh count of 5 is needed again
            assert rl.begin_attempt(u) == 'ok'
            rl.record_failure(u)
        assert rl.locked_seconds(u) == 0
    assert _events(app, acct['uid']) == []


@pytest.mark.parametrize('acct', ['teacher'], indirect=True)
def test_old_failures_stop_counting_after_the_window(app, acct, clock):
    u = _user(app, acct)
    with app.app_context():
        for _ in range(3):
            rl.begin_attempt(u)
            rl.record_failure(u)
    clock.advance(901)
    with app.app_context():
        for _ in range(3):
            assert rl.begin_attempt(u) == 'ok'
            rl.record_failure(u)
        assert rl.locked_seconds(u) == 0              # 3 + 3 across windows is not 5 in one


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_lock_unlock_and_blocked_are_audited_once_each(app, acct, clock):
    u = _user(app, acct)
    _five_wrong(app, u)
    with app.app_context():
        for _ in range(6):                            # many refused tries: one blocked entry
            assert rl.begin_attempt(u) == 'locked'
    acts = [a for a, _ in _events(app, acct['uid'])]
    assert acts == ['user.recovery_locked', 'user.recovery_blocked']
    assert '120 minutes' in _events(app, acct['uid'])[0][1]
    clock.advance(TWO_HOURS + 5)
    with app.app_context():
        assert rl.begin_attempt(u) == 'ok'
        assert rl.begin_attempt(u) == 'ok'            # unlock is logged only once
    ev = _events(app, acct['uid'])
    assert [a for a, _ in ev] == ['user.recovery_locked', 'user.recovery_blocked',
                                  'user.recovery_unlocked']
    assert 'expired' in ev[2][1]
    # a second lock is logged again as a new lock (the two calls above already used 2 tries)
    with app.app_context():
        for _ in range(3):
            assert rl.begin_attempt(u) == 'ok'
            rl.record_failure(u)
        assert rl.locked_seconds(u) > 0
    acts = [a for a, _ in _events(app, acct['uid'])]
    assert acts.count('user.recovery_locked') == 2 and acts[-1] == 'user.recovery_locked'


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_the_audit_log_holds_no_code_or_password(app, acct):
    u = _user(app, acct)
    _five_wrong(app, u)
    for _, detail in _events(app, acct['uid']):
        assert 'code' not in detail.lower().replace('wrong codes', '')


@pytest.mark.parametrize('acct', ROLES4, indirect=True)
def test_a_recovery_lock_never_blocks_normal_login_or_other_checks(app, client, acct):
    u = _user(app, acct)
    _five_wrong(app, u)
    with app.app_context():
        assert rl.locked_seconds(u) > 0
        assert auth.otp_locked_seconds(acct['uid']) == 0       # sign-in authenticator check
        assert auth.reset_locked_seconds(acct['uid']) == 0     # forgot-password (Checkpoint A)
        assert auth.lost_locked_seconds(acct['uid']) == 0      # separate existing key
    _sign_in(client, acct)                                     # real login still works


@pytest.mark.parametrize('acct', ['admin'], indirect=True)
def test_admin_is_excluded_and_never_counted_or_locked(app, acct):
    u = _user(app, acct)
    assert not rl.applies_to('admin')
    with app.app_context():
        for _ in range(30):
            assert rl.begin_attempt(u) == 'ok'
            rl.record_failure(u)
        assert rl.locked_seconds(u) == 0
        assert rl.mail_allowed(u) is True and rl.mail_allowed(u) is True
        assert rl.mail_allowed(u) is True and rl.mail_allowed(u) is True
        n = db.query('SELECT COUNT(*) AS n FROM login_attempts', one=True)['n']
    assert n == 0 and _events(app, acct['uid']) == []


def test_the_lock_is_per_account(app):
    with app.app_context():
        stu = db.query("SELECT id FROM users WHERE role='student' LIMIT 1", one=True)['id']
        tea = db.query("SELECT id FROM users WHERE role='teacher' LIMIT 1", one=True)['id']
    a, b = {'id': stu, 'role': 'student'}, {'id': tea, 'role': 'teacher'}
    _five_wrong(app, a)
    with app.app_context():
        assert rl.begin_attempt(a) == 'locked'
        assert rl.begin_attempt(b) == 'ok'


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_limits_are_configurable(app, acct):
    u = _user(app, acct)
    app.config.update(RECOVERY_MAX_ATTEMPTS=2, RECOVERY_LOCK_SECONDS=600)
    try:
        with app.app_context():
            for _ in range(2):
                assert rl.begin_attempt(u) == 'ok'
                rl.record_failure(u)
            assert rl.begin_attempt(u) == 'locked'
            assert 590 <= rl.locked_seconds(u) <= 600
    finally:
        app.config.update(RECOVERY_MAX_ATTEMPTS=5, RECOVERY_LOCK_SECONDS=7200)


def test_ip_limits_count_every_request_and_are_per_ip(app):
    with app.app_context():
        assert all(rl.ip_start_allowed('10.0.0.1') for _ in range(10))
        assert rl.ip_start_allowed('10.0.0.1') is False        # 11th in the window
        assert rl.ip_start_allowed('10.0.0.2') is True         # another address is unaffected
        assert all(rl.ip_code_allowed('10.0.0.3') for _ in range(15))
        assert rl.ip_code_allowed('10.0.0.3') is False         # 16th guess in the hour
        assert rl.ip_code_allowed('10.0.0.4') is True


def test_ip_limits_lift_after_their_time(app, clock):
    with app.app_context():
        for _ in range(10):
            rl.ip_start_allowed('10.0.0.9')
        assert rl.ip_start_allowed('10.0.0.9') is False
    clock.advance(901)
    with app.app_context():
        assert rl.ip_start_allowed('10.0.0.9') is True


@pytest.mark.parametrize('acct', ['student'], indirect=True)
def test_at_most_three_recovery_e_mails_per_hour_per_account(app, acct, clock):
    u = _user(app, acct)
    with app.app_context():
        assert [rl.mail_allowed(u) for _ in range(4)] == [True, True, True, False]
    clock.advance(3601)
    with app.app_context():
        assert rl.mail_allowed(u) is True


def test_nothing_is_wired_into_existing_pages_yet(app, client):
    """B1 is the engine only: no existing route imports it, so behaviour is unchanged."""
    import pathlib
    root = pathlib.Path(app.root_path)
    users = [p.name for p in root.glob('*.py')
             if p.name != 'recovery_lock.py' and 'recovery_lock' in p.read_text()]
    assert users == []
