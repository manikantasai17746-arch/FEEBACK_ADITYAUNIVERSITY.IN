"""Student authenticator (TOTP) core service - Checkpoint 1.

Service layer only: no routes, no templates, no form gating.  It provides:

* encrypted storage of TOTP secrets (Fernet / MultiFernet, key from the
  ``TOTP_ENCRYPTION_KEY`` setting, never derived from ``SECRET_KEY``);
* RFC 6238 verification with a +/-1 time-step drift window (30 s);
* replay protection: a time step can be consumed exactly once, atomically;
* single-use backup codes, stored only as scrypt hashes;
* rate limiting on the shared ``login_attempts`` table.

The tables (``student_authenticators`` / ``student_backup_codes``) keep their original
names but are keyed by ``user_id``, so the service works for every role.

Secrets and codes are never logged.  Plaintext secrets exist only in memory and
are returned from ``begin_enrollment`` so the enrollment step (Checkpoint 2) can
display them once.
"""
import logging
import re
import hashlib
import secrets
import threading
import time
from datetime import date, datetime

import pyotp
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from flask import current_app

from . import db
from .security import (clear_login_failures, hash_password, login_locked,
                       reserve_login_attempt, verify_password)

logger = logging.getLogger(__name__)

TOTP_STEP_SECONDS = 30
TOTP_DRIFT_STEPS = 1                 # accept previous, current and next step
CODE_RE = re.compile(r'^\d{6}$')
BACKUP_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'   # no 0/1/i/l/o look-alikes
BACKUP_CODE_COUNT = 10
BACKUP_CODE_LENGTH = 12              # ~59 bits of entropy per code
FAILURE_WINDOW_SECONDS = 900
ISSUER = 'Aditya University Feedback'


class AuthenticatorError(Exception):
    """Base class for authenticator service errors."""


class AuthenticatorConfigError(AuthenticatorError):
    """The encryption key is missing, malformed, or cannot decrypt a stored secret."""


class AuthenticatorStateError(AuthenticatorError):
    """The requested change is not allowed in the account's current state."""


# --------------------------------------------------------------------------- #
# Encryption at rest
# --------------------------------------------------------------------------- #
_CIPHER_CACHE = {}
_CIPHER_CACHE_LOCK = threading.Lock()
_CIPHER_CACHE_MAX = 4


def _cipher_for(raw):
    """Return the MultiFernet for a key string.

    Cached by the SHA-256 of the key string, so the raw key is never a cache key. The
    Fernet objects still hold derived key material for the life of the process; that
    cannot be avoided for encryption. The cache is bounded and cleared when it fills.
    """
    if not raw:
        raise AuthenticatorConfigError('TOTP_ENCRYPTION_KEY is not set.')
    digest = hashlib.sha256(str(raw).encode('utf-8')).hexdigest()
    with _CIPHER_CACHE_LOCK:
        cached = _CIPHER_CACHE.get(digest)
    if cached is not None:
        return cached
    keys = [k.strip() for k in str(raw).split(',') if k.strip()]
    try:
        cipher = MultiFernet([Fernet(k) for k in keys])
    except (ValueError, TypeError):
        # Never include the key material in the message.
        raise AuthenticatorConfigError('TOTP_ENCRYPTION_KEY is not a valid Fernet key.') from None
    with _CIPHER_CACHE_LOCK:
        if len(_CIPHER_CACHE) >= _CIPHER_CACHE_MAX:
            _CIPHER_CACHE.clear()
        _CIPHER_CACHE[digest] = cipher
    return cipher


def _cipher():
    return _cipher_for(current_app.config.get('TOTP_ENCRYPTION_KEY') or '')


def encrypt_secret(secret):
    """Encrypt a base32 TOTP secret for storage. Output is a Fernet token (text)."""
    return _cipher().encrypt(secret.encode('ascii')).decode('ascii')


def decrypt_secret(token):
    """Decrypt a stored secret. Fails closed: never returns a guess or a partial value."""
    try:
        return _cipher().decrypt(token.encode('ascii')).decode('ascii')
    except (InvalidToken, UnicodeError):
        raise AuthenticatorConfigError(
            'Stored authenticator secret cannot be decrypted with the configured key.') from None


def reencrypt_secret(token):
    """Re-encrypt a stored token under the newest key (used during key rotation)."""
    try:
        return _cipher().rotate(token.encode('ascii')).decode('ascii')
    except (InvalidToken, UnicodeError):
        raise AuthenticatorConfigError(
            'Stored authenticator secret cannot be decrypted with the configured key.') from None


# --------------------------------------------------------------------------- #
# TOTP (RFC 6238)
# --------------------------------------------------------------------------- #
def new_secret():
    """A fresh 160-bit base32 secret (pyotp default)."""
    return pyotp.random_base32()


def provisioning_uri(secret, account):
    """otpauth:// URI for the QR code. It contains the secret: show it only to the student."""
    return pyotp.TOTP(secret, interval=TOTP_STEP_SECONDS).provisioning_uri(
        name=account, issuer_name=ISSUER)


def _now(now=None):
    return time.time() if now is None else now


def verify_totp(secret, code, last_step=None, now=None):
    """Check a 6-digit code. Return the matched time step, or None.

    Accepts the current step and TOTP_DRIFT_STEPS either side.  A step that is
    not later than ``last_step`` is rejected (replay).  If two steps in the
    window produce the same code, the later one is returned so that consuming
    it also blocks the earlier one.  Every candidate is evaluated, so the
    comparison time does not reveal which step matched.
    """
    if not isinstance(code, str) or not CODE_RE.match(code):
        return None
    base = int(_now(now)) // TOTP_STEP_SECONDS
    totp = pyotp.TOTP(secret, interval=TOTP_STEP_SECONDS)
    matched = None
    for step in range(base - TOTP_DRIFT_STEPS, base + TOTP_DRIFT_STEPS + 1):
        ok = secrets.compare_digest(totp.at(step * TOTP_STEP_SECONDS), code)
        if ok and (last_step is None or step > last_step):
            matched = step
    return matched


def _now_iso():
    return datetime.utcnow().isoformat(timespec='seconds')


def consume_step(user_id, step):
    """Atomically mark ``step`` as used. True only for the first caller for that step.

    The UPDATE only succeeds while ``last_step`` is still lower, so two concurrent
    requests with the same code cannot both succeed.
    """
    cur = db.execute(
        "UPDATE student_authenticators SET last_step=?, updated_at=? "
        "WHERE user_id=? AND status='active' AND (last_step IS NULL OR last_step < ?)",
        (step, _now_iso(), user_id, step))
    return cur.rowcount == 1


# --------------------------------------------------------------------------- #
# Enrollment state
# --------------------------------------------------------------------------- #
def _row(user_id):
    return db.query('SELECT secret_enc, status, last_step FROM student_authenticators '
                    'WHERE user_id=?', (user_id,), one=True)


def is_active(user_id):
    row = _row(user_id)
    return bool(row and row['status'] == 'active')


def _update_pending(user_id, token, now):
    cur = db.execute(
        "UPDATE student_authenticators SET secret_enc=?, status='pending', last_step=NULL, "
        "enrolled_at=NULL, updated_at=? WHERE user_id=? AND status<>'active'",
        (token, now, user_id))
    return cur.rowcount == 1


def begin_enrollment(user_id):
    """Create or replace a PENDING secret for an account with no active authenticator.

    Returns the plaintext secret once, for display during setup. Only the encrypted form
    is stored. Refuses to replace an active authenticator (AuthenticatorStateError).

    Two first-time requests can race to insert. The loser's insert fails, the transaction
    is rolled back, and the loser retries as an update. Any other insert failure is
    re-raised unchanged.
    """
    secret = new_secret()
    token = encrypt_secret(secret)
    now = _now_iso()
    if _update_pending(user_id, token, now):
        return secret
    try:
        db.execute("INSERT INTO student_authenticators(user_id, secret_enc, status, updated_at) "
                   "VALUES(?,?,'pending',?)", (user_id, token, now))
    except Exception:
        db.get_db().rollback()
        if _update_pending(user_id, token, now):
            return secret
        if is_active(user_id):
            raise AuthenticatorStateError('An authenticator is already active for this account.')
        raise
    return secret


def pending_secret(user_id):
    """The plaintext secret of a PENDING enrollment, for display during setup only.

    Callers must check the setup proof first. Returns None if nothing is pending.
    """
    row = _row(user_id)
    if row is None or row['status'] != 'pending':
        return None
    return decrypt_secret(row['secret_enc'])


def confirm_enrollment(user_id, code, now=None):
    """Activate a pending authenticator once the student proves it works.

    The confirming time step is recorded as used, so the same code cannot be
    replayed at login straight after setup. Rate limited (see body).
    """
    # M1/M2: same reserve-before-verify lockout as verify_second_factor. A missing or
    # non-pending row is refused before anything is counted.
    row = _row(user_id)
    if row is None or row['status'] != 'pending':
        return False
    if not reserve_otp_attempt(user_id):
        return False
    step = verify_totp(decrypt_secret(row['secret_enc']), code, last_step=None, now=now)
    if step is None:
        return False
    stamp = _now_iso()
    cur = db.execute(
        "UPDATE student_authenticators SET status='active', last_step=?, enrolled_at=?, "
        "updated_at=? WHERE user_id=? AND status='pending'",
        (step, stamp, stamp, user_id))
    if cur.rowcount != 1:
        return False
    clear_otp_failures(user_id)
    return True


# --------------------------------------------------------------------------- #
# Backup codes (single use, hashed at rest)
# --------------------------------------------------------------------------- #
def _normalise_code(raw):
    return re.sub(r'[\s-]', '', str(raw or '')).lower()


def generate_backup_codes(count=BACKUP_CODE_COUNT):
    """Return ``count`` codes shaped ``xxxx-xxxx-xxxx`` for showing to the student once."""
    codes = []
    for _ in range(count):
        raw = ''.join(secrets.choice(BACKUP_ALPHABET) for _ in range(BACKUP_CODE_LENGTH))
        codes.append(f'{raw[:4]}-{raw[4:8]}-{raw[8:]}')
    return codes


def store_backup_codes(user_id, codes):
    """Replace all of a student's backup codes. Only scrypt hashes are stored."""
    rows = [(user_id, hash_password(_normalise_code(c)), _now_iso()) for c in codes]
    conn = db.get_db()
    try:
        conn.execute('DELETE FROM student_backup_codes WHERE user_id=?', (user_id,))
        conn.executemany('INSERT INTO student_backup_codes(user_id, code_hash, created_at) '
                         'VALUES(?,?,?)', rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def consume_backup_code(user_id, raw_code):
    """Use one unused backup code. True once per code; a reused or wrong code returns False."""
    code = _normalise_code(raw_code)
    if len(code) != BACKUP_CODE_LENGTH or any(c not in BACKUP_ALPHABET for c in code):
        return False
    match = None
    for r in db.query('SELECT id, code_hash FROM student_backup_codes '
                      'WHERE user_id=? AND used_at IS NULL', (user_id,)):
        # Check every candidate so timing does not reveal which code matched.
        if verify_password(r['code_hash'], code) and match is None:
            match = r['id']
    if match is None:
        return False
    cur = db.execute('UPDATE student_backup_codes SET used_at=? WHERE id=? AND used_at IS NULL',
                     (_now_iso(), match))
    return cur.rowcount == 1


# --------------------------------------------------------------------------- #
# Rate limiting for second-factor attempts (shared, DB-backed)
# --------------------------------------------------------------------------- #
def _otp_key(user_id):
    return f'otp:{user_id}'


def otp_locked_seconds(user_id):
    """Seconds left on a lockout for this account (0 = not locked)."""
    return login_locked(_otp_key(user_id))


def reserve_otp_attempt(user_id):
    """Reserve one second-factor attempt before its code is checked (M2).

    Every attempt counts when it is made, so concurrent attempts cannot exceed the
    limit. A successful attempt clears the count (clear_otp_failures).
    """
    return reserve_login_attempt(_otp_key(user_id),
                                 int(current_app.config.get('OTP_MAX_ATTEMPTS', 5)),
                                 int(current_app.config.get('OTP_LOCKOUT_SECONDS', 900)),
                                 window_seconds=FAILURE_WINDOW_SECONDS)


def clear_otp_failures(user_id):
    clear_login_failures(_otp_key(user_id))


# --------------------------------------------------------------------------- #
# Full second-factor check (used by the Checkpoint 3 gate)
# --------------------------------------------------------------------------- #
def _check_code(user_id, code, reserve, clear, now=None):
    """Shared TOTP-or-backup-code check. ``reserve``/``clear`` choose the rate-limit key.

    Reasons: ``totp`` / ``backup`` (success), ``locked``, ``not_enrolled``, ``invalid``.
    Order (M2): not-enrolled is checked first and is not counted; then the attempt is
    RESERVED (refused if locked or the limit is used up); only then is the code checked
    (TOTP with replay protection, then backup code).  A success clears the count; every
    other attempt stays counted.
    """
    row = _row(user_id)
    if row is None or row['status'] != 'active':
        return False, 'not_enrolled'
    if not reserve(user_id):
        return False, 'locked'
    text = str(code or '').strip()
    step = verify_totp(decrypt_secret(row['secret_enc']), text,
                       last_step=row['last_step'], now=now)
    if step is not None:
        if consume_step(user_id, step):
            clear(user_id)
            return True, 'totp'
    elif len(_normalise_code(text)) == BACKUP_CODE_LENGTH and consume_backup_code(user_id, text):
        clear(user_id)
        return True, 'backup'
    return False, 'invalid'


def verify_second_factor(user_id, code, now=None):
    """Check a TOTP or backup code for an enrolled account. Returns (ok, reason).

    Used by the sign-in / feedback-form gate. See ``_check_code`` for the reasons.
    """
    return _check_code(user_id, code, reserve_otp_attempt, clear_otp_failures, now=now)


# --------------------------------------------------------------------------- #
# Recovery (forgotten password / lost phone) and backup-code management
# --------------------------------------------------------------------------- #
def _reset_key(user_id):
    # Separate from ``otp:<id>`` so failed password-reset guesses (by anyone who only
    # knows the username) can never lock the real user out of the sign-in gate.
    return f'otpreset:{user_id}'


def reset_locked_seconds(user_id):
    """Seconds left on a recovery lockout for this account (0 = not locked)."""
    return login_locked(_reset_key(user_id))


def reserve_reset_attempt(user_id):
    """Reserve one recovery attempt before its code is checked (same rules as M2)."""
    return reserve_login_attempt(_reset_key(user_id),
                                 int(current_app.config.get('OTP_MAX_ATTEMPTS', 5)),
                                 int(current_app.config.get('OTP_LOCKOUT_SECONDS', 900)),
                                 window_seconds=FAILURE_WINDOW_SECONDS)


def clear_reset_failures(user_id):
    clear_login_failures(_reset_key(user_id))


def verify_recovery_code(user_id, code, now=None):
    """Check a TOTP or single-use backup code for a NORMAL password reset.

    Returns (ok, reason). Reasons: ``totp`` / ``backup`` (success), ``locked``,
    ``not_enrolled``, ``invalid``.  Rate limited under its own key (``otpreset:``). Reuses
    ``consume_step`` and ``consume_backup_code``, so a TOTP code cannot be replayed (here
    or at sign-in) and a backup code works once.

    This function NEVER removes or disables the authenticator, whichever kind of code
    was used: a backup code used for an ordinary password reset only spends that one
    code, and the user keeps the same authenticator and their remaining codes.  Removing
    the authenticator is a separate, explicit lost-device recovery
    (``complete_lost_device_reset``).  The caller must not tell the browser which reason
    it was.
    """
    return _check_code(user_id, code, reserve_reset_attempt, clear_reset_failures, now=now)


def backup_codes_remaining(user_id):
    """How many unused backup codes the account has (0 if none)."""
    row = db.query('SELECT COUNT(*) AS n FROM student_backup_codes '
                   'WHERE user_id=? AND used_at IS NULL', (user_id,), one=True)
    return int(row['n']) if row else 0


def regenerate_backup_codes(user_id):
    """Issue a fresh set of backup codes, invalidating every older one.

    Returns the plaintext codes ONCE for display; only hashes are stored.  Allowed only
    for an ACTIVE authenticator.  The caller must have just verified a current TOTP
    code (``verify_second_factor``) before calling this.
    """
    if not is_active(user_id):
        raise AuthenticatorStateError('No active authenticator for this account.')
    codes = generate_backup_codes()
    store_backup_codes(user_id, codes)
    return codes


def complete_lost_device_reset(user_id):
    """Lost-device recovery ONLY: remove the authenticator and ALL remaining codes.

    This is the single recovery step that removes the old authenticator.  It must be
    reached only through the separate "I lost my phone" path, after the user proved
    themselves with a backup code (``verify_recovery_code``); it must NOT be run just
    because a backup code was used for an ordinary password reset.  The phone may be lost
    or stolen, so it stops being trusted: the account must enrol a new authenticator at
    its next sign-in.  This function does not verify anything itself.
    Returns True if an authenticator was removed.
    """
    removed = remove_authenticator(user_id)
    clear_reset_failures(user_id)
    return removed


def remove_authenticator(user_id):
    """Delete an account's authenticator (active or pending) and backup codes.

    Used by admin resets and lost-device recovery; the user sets up again at next sign-in.
    Returns True if one existed.
    """
    conn = db.get_db()
    try:
        conn.execute('DELETE FROM student_backup_codes WHERE user_id=?', (user_id,))
        cur = conn.execute('DELETE FROM student_authenticators WHERE user_id=?', (user_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    clear_otp_failures(user_id)
    clear_reset_failures(user_id)
    return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# Enrollment policy (Checkpoint 2): which roles must enrol, and when
# --------------------------------------------------------------------------- #
ROLES = ('student', 'teacher', 'hod', 'dean', 'admin')

# The Admin recovery procedure (docs/AUTHENTICATOR_RECOVERY.md section 5) is NOT yet
# implemented. Until it is, forcing the Admin role to enrol is refused whatever the
# configuration says. Admins may still enrol voluntarily. Flip this only together with
# that procedure.
ADMIN_ENFORCEMENT_ALLOWED = False


def _parse_roles(raw):
    return {r.strip().lower() for r in str(raw or '').split(',') if r.strip()}


def _parse_grace(raw):
    """``"teacher=2026-12-31,hod=2027-01-15"`` -> {role: date}. Bad entries are ignored
    (so the role is enforced at once, the safer reading) and logged without the value."""
    out = {}
    for part in str(raw or '').split(','):
        role, _, value = part.partition('=')
        role = role.strip().lower()
        if not role:
            continue
        try:
            out[role] = date.fromisoformat(value.strip())
        except ValueError:
            logger.warning('AUTHENTICATOR_GRACE_UNTIL: ignoring an invalid date for role %r', role)
    return out


def enforcement_state(role, config, today=None):
    """Return ``(state, grace_until)`` for a role.

    ``state`` is ``'off'`` (not forced to enrol), ``'grace'`` (listed, but before its
    grace date: remind only) or ``'required'`` (must enrol before using the portal).

    Config: ``AUTHENTICATOR_ENFORCE_ROLES`` (comma list, default ``student``, which is the
    behaviour before this checkpoint) and ``AUTHENTICATOR_GRACE_UNTIL`` (``role=YYYY-MM-DD``
    pairs; the role is required from that date). Non-student roles are never forced while
    ``TOTP_ENCRYPTION_KEY`` is unset (that would lock them out of setup), and the Admin role
    is never forced while ``ADMIN_ENFORCEMENT_ALLOWED`` is False.
    """
    if role not in ROLES:
        return 'off', None
    if role not in _parse_roles(config.get('AUTHENTICATOR_ENFORCE_ROLES', 'student')):
        return 'off', None
    if role == 'admin' and not ADMIN_ENFORCEMENT_ALLOWED:
        return 'off', None
    if role != 'student' and not config.get('TOTP_ENCRYPTION_KEY'):
        return 'off', None
    until = _parse_grace(config.get('AUTHENTICATOR_GRACE_UNTIL', '')).get(role)
    today = today or datetime.utcnow().date()
    if until is not None and today < until:
        return 'grace', until
    return 'required', until


# --------------------------------------------------------------------------- #
# Lost-device recovery (Checkpoint 3)
#
# A SEPARATE path from normal password reset. Only a successful recovery here (or the
# audited administrator reset) removes an authenticator. Recovery leaves a "re-enrollment
# required" marker so the user must set up a new authenticator at the next sign-in:
# a PENDING authenticator row whose ``enrolled_at`` is set. (A voluntary, half-finished
# setup is also ``pending`` but has ``enrolled_at`` NULL, so the two cannot be confused.)
# No schema change is needed.
# --------------------------------------------------------------------------- #
def _lost_key(user_id):
    # Its own key: failed lost-device attempts never lock sign-in or password reset.
    return f'otplost:{user_id}'


def lost_locked_seconds(user_id):
    """Seconds left on a lost-device recovery lockout (0 = not locked)."""
    return login_locked(_lost_key(user_id))


def reserve_lost_attempt(user_id):
    """Reserve one lost-device attempt before anything is checked (same rules as M2)."""
    return reserve_login_attempt(_lost_key(user_id),
                                 int(current_app.config.get('OTP_MAX_ATTEMPTS', 5)),
                                 int(current_app.config.get('OTP_LOCKOUT_SECONDS', 900)),
                                 window_seconds=FAILURE_WINDOW_SECONDS)


def clear_lost_failures(user_id):
    clear_login_failures(_lost_key(user_id))


def needs_reenrollment(user_id):
    """True if a lost-device or admin recovery removed this account's authenticator and
    the new one has not been confirmed yet."""
    row = _row(user_id)
    if row is None or row['status'] != 'pending':
        return False
    marker = db.query('SELECT enrolled_at FROM student_authenticators WHERE user_id=?',
                      (user_id,), one=True)
    return bool(marker and marker['enrolled_at'])


def apply_lost_device_reset(user_id):
    """Atomically remove an ACTIVE authenticator and every backup code, and leave the
    re-enrollment marker. Returns True only for the one caller that removed it.

    The first statement deletes the row only while it is still ``active``, so two
    simultaneous recoveries cannot both succeed: the loser sees no row, rolls back and gets
    False. All three changes commit together or not at all. The new pending secret is
    created (and encrypted) BEFORE anything is deleted, so a missing or bad encryption key
    fails closed with nothing removed.
    """
    token = encrypt_secret(new_secret())
    stamp = _now_iso()
    conn = db.get_db()
    try:
        cur = conn.execute("DELETE FROM student_authenticators WHERE user_id=? AND status='active'",
                           (user_id,))
        if cur.rowcount != 1:
            conn.rollback()
            return False
        conn.execute('DELETE FROM student_backup_codes WHERE user_id=?', (user_id,))
        conn.execute("INSERT INTO student_authenticators(user_id, secret_enc, status, enrolled_at, "
                     "updated_at) VALUES(?,?,'pending',?,?)", (user_id, token, stamp, stamp))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    clear_otp_failures(user_id)
    clear_reset_failures(user_id)
    clear_lost_failures(user_id)
    return True


def recover_lost_device(user_id, backup_code, reserve=True):
    """Verify ONE unused backup code and, only if it is right, remove the authenticator.

    Returns ``(ok, reason)``: ``(True, 'backup')``, or ``(False, 'locked' | 'not_enrolled' |
    'invalid')``. The caller must show the browser the same message for every failure.

    * Backup code only. A 6-digit app code is rejected before anything is spent.
    * Not-enrolled is checked first and not counted; then the attempt is reserved under
      ``otplost:`` (pass ``reserve=False`` if the caller already reserved it, for example
      before checking a password); only then is the code checked.
    * The code is single use (``consume_backup_code`` is atomic), and the removal is atomic
      and happens once (``apply_lost_device_reset``). Nothing is removed on any failure.
    * Does NOT check a password or identity: the route does that first.
    """
    row = _row(user_id)
    if row is None or row['status'] != 'active':
        return False, 'not_enrolled'
    _cipher()        # a missing or bad key fails closed HERE, before any code is spent
    if reserve and not reserve_lost_attempt(user_id):
        return False, 'locked'
    code = _normalise_code(backup_code)
    if len(code) != BACKUP_CODE_LENGTH or any(c not in BACKUP_ALPHABET for c in code):
        return False, 'invalid'
    if not consume_backup_code(user_id, backup_code):
        return False, 'invalid'
    if not apply_lost_device_reset(user_id):
        return False, 'not_enrolled'
    return True, 'backup'
