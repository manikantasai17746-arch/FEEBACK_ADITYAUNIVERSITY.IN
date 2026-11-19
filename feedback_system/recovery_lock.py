"""Lost-device recovery lock (Checkpoint B1): rate limits and the 2-hour lock.

Applies to Student, Teacher, HOD and Dean only. Every function is a no-op for any other
role (Admin keeps its own two-e-mail recovery and is never counted or locked here).

* Account lock: ``RECOVERY_MAX_ATTEMPTS`` (5) wrong recovery codes within
  ``RECOVERY_FAILURE_WINDOW_SECONDS`` (15 min) lock lost-device recovery for
  ``RECOVERY_LOCK_SECONDS`` (2 hours). It unlocks by itself when the time is up.
* The lock lives under its own key (``reclock:<id>``). Normal sign-in (``role:user:ip``),
  the sign-in authenticator check (``otp:<id>``) and forgot-password (``otpreset:<id>``)
  use other keys, so a recovery lock can never stop anyone logging in or resetting a
  password the Checkpoint A way.
* IP limits (counted before any work): ``recstart-ip`` for starting recovery,
  ``reccode-ip`` for code guesses. Per account, ``recmail`` caps e-mailed codes per hour.
* Audit entries (entity ``users``): ``user.recovery_locked`` when the 5th wrong code
  locks the account; ``user.recovery_blocked`` for the first refused attempt of a lock;
  ``user.recovery_unlocked`` the next time the account attempts recovery after the lock
  expired (there is no background job, so it is written lazily and records the expiry time).

Typical use by a recovery route::

    if not recovery_lock.ip_start_allowed(): ...refuse...
    state = recovery_lock.begin_attempt(user)       # 'ok' or 'locked'
    ...check the code...
    recovery_lock.record_success(user) / recovery_lock.record_failure(user)

No schema change: counters use ``login_attempts``; the log uses ``audit_log``.
"""
import logging
import time
from datetime import datetime, timezone

from flask import current_app, request

from . import db
from .security import (audit, clear_login_failures, login_locked, reserve_login_attempt)

logger = logging.getLogger(__name__)

RECOVERY_ROLES = ('student', 'teacher', 'hod', 'dean')
LOCKED, UNLOCKED, BLOCKED = ('user.recovery_locked', 'user.recovery_unlocked',
                             'user.recovery_blocked')
_SYSTEM = {'id': None, 'name': 'system', 'username': 'system'}


def applies_to(role):
    return role in RECOVERY_ROLES


def _num(name, default):
    try:
        return int(current_app.config.get(name, default))
    except (TypeError, ValueError):
        return default


def _key(user_id):
    return f'reclock:{user_id}'


def _max_attempts():
    return max(1, _num('RECOVERY_MAX_ATTEMPTS', 5))


def _lock_seconds():
    return max(1, _num('RECOVERY_LOCK_SECONDS', 7200))


def _window():
    return max(1, _num('RECOVERY_FAILURE_WINDOW_SECONDS', 900))


# --------------------------------------------------------------------------- #
# Audit helpers
# --------------------------------------------------------------------------- #
def _last_event(user_id):
    row = db.query("SELECT action FROM audit_log WHERE entity='users' AND entity_id=? "
                   "AND action IN (?,?,?) ORDER BY id DESC LIMIT 1",
                   (user_id, LOCKED, UNLOCKED, BLOCKED), one=True)
    return row['action'] if row else None


def _log(action, user_id, detail):
    audit(action, 'users', user_id, detail, actor=_SYSTEM)


def _stamp(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def _lock_until(user_id):
    from .security import _db_key
    row = db.query('SELECT until_ts FROM login_attempts WHERE attempt_key=?',
                   (_db_key(_key(user_id)),), one=True)
    return int(row['until_ts']) if row else 0


# --------------------------------------------------------------------------- #
# Account lock
# --------------------------------------------------------------------------- #
def locked_seconds(user):
    """Seconds left on this account's recovery lock (0 = not locked or not applicable)."""
    if not applies_to(user['role']):
        return 0
    return login_locked(_key(user['id']))


def _note_unlock(user_id):
    """Write the unlock entry once, the first time recovery is tried after a lock expired."""
    if _last_event(user_id) in (LOCKED, BLOCKED):
        until = _lock_until(user_id)
        _log(UNLOCKED, user_id, 'Recovery lock expired; recovery attempts allowed again'
             + (f' (lock ended {_stamp(until)})' if until else ''))


def begin_attempt(user):
    """Reserve one recovery-code attempt BEFORE the code is checked.

    Returns ``'ok'`` (go ahead and check the code) or ``'locked'`` (refuse, same generic
    message as any other failure). Not applicable roles always get ``'ok'`` and are not
    counted. Attempts refused during a lock are not counted, so they cannot extend it.
    """
    if not applies_to(user['role']):
        return 'ok'
    uid = user['id']
    if login_locked(_key(uid)) == 0:
        _note_unlock(uid)
    allowed = reserve_login_attempt(_key(uid), _max_attempts(), _lock_seconds(),
                                    window_seconds=_window())
    if allowed:
        return 'ok'
    if _last_event(uid) != BLOCKED:
        _log(BLOCKED, uid, 'Recovery attempt refused: account recovery is locked')
    return 'locked'


def record_success(user):
    """The code was right: clear the wrong-code count."""
    if applies_to(user['role']):
        clear_login_failures(_key(user['id']))


def record_failure(user):
    """The code was wrong. If this was the 5th, the lock is now on: log it once."""
    if not applies_to(user['role']):
        return
    uid = user['id']
    if login_locked(_key(uid)) > 0 and _last_event(uid) not in (LOCKED, BLOCKED):
        _log(LOCKED, uid,
             f'Recovery locked for {_lock_seconds() // 60} minutes after '
             f'{_max_attempts()} wrong codes (until {_stamp(_lock_until(uid))})')


# --------------------------------------------------------------------------- #
# IP-level and mail limits (all configurable; each counts every request)
# --------------------------------------------------------------------------- #
def _ip(ip=None):
    return ip or request.remote_addr or 'unknown'


def ip_start_allowed(ip=None):
    """Per-IP cap on starting recovery / entering an ID (default 10 per 15 minutes)."""
    return reserve_login_attempt(f'recstart-ip:{_ip(ip)}', max(1, _num('RECOVERY_IP_START_MAX', 10)),
                                 900, window_seconds=900)


def ip_code_allowed(ip=None):
    """Per-IP cap on code guesses across all accounts (default 15 per hour)."""
    return reserve_login_attempt(f'reccode-ip:{_ip(ip)}', max(1, _num('RECOVERY_IP_CODE_MAX', 15)),
                                 3600, window_seconds=3600)


def mail_allowed(user):
    """Per-account cap on recovery e-mails (default 3 per hour), on top of the 60 s resend
    cooldown. Protects mail cost and a student's inbox from being flooded."""
    if not applies_to(user['role']):
        return True
    return reserve_login_attempt(f'recmail:{user["id"]}', max(1, _num('RECOVERY_MAIL_MAX', 3)),
                                 3600, window_seconds=3600)
