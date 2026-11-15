"""Student authenticator (TOTP) core service - Checkpoint 1.

Service layer only: no routes, no templates, no form gating.  It provides:

* encrypted storage of TOTP secrets (Fernet / MultiFernet, key from the
  ``TOTP_ENCRYPTION_KEY`` setting, never derived from ``SECRET_KEY``);
* RFC 6238 verification with a +/-1 time-step drift window (30 s);
* replay protection: a time step can be consumed exactly once, atomically;
* single-use backup codes, stored only as scrypt hashes;
* rate limiting on the shared ``login_attempts`` table.

Secrets and codes are never logged.  Plaintext secrets exist only in memory and
are returned from ``begin_enrollment`` so the enrollment step (Checkpoint 2) can
display them once.
"""
import re
import secrets
import time
from datetime import datetime
from functools import lru_cache

import pyotp
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from flask import current_app

from . import db
from .security import (clear_login_failures, hash_password, login_locked,
                       register_login_failure, verify_password)

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
@lru_cache(maxsize=4)
def _cipher_for(raw):
    if not raw:
        raise AuthenticatorConfigError('TOTP_ENCRYPTION_KEY is not set.')
    keys = [k.strip() for k in str(raw).split(',') if k.strip()]
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except (ValueError, TypeError):
        # Never include the key material in the message.
        raise AuthenticatorConfigError('TOTP_ENCRYPTION_KEY is not a valid Fernet key.') from None


def _cipher():
    return _cipher_for(current_app.config.get('TOTP_ENCRYPTION_KEY') or '')


def encrypt_secret(secret):
    """Encrypt a base32 TOTP secret for storage. Output is a Fernet token (text)."""
    return _cipher().encrypt(secret.encode('ascii')).decode('ascii')


def decrypt_secret(token):
    """Decrypt a stored secret. Fails closed: never returns a guess or a partial value."""
    try:
        return _cipher().decrypt(token.encode('ascii')).decode('ascii')
    except InvalidToken:
        raise AuthenticatorConfigError(
            'Stored authenticator secret cannot be decrypted with the configured key.') from None


def reencrypt_secret(token):
    """Re-encrypt a stored token under the newest key (used during key rotation)."""
    try:
        return _cipher().rotate(token.encode('ascii')).decode('ascii')
    except InvalidToken:
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


def begin_enrollment(user_id):
    """Create or replace a PENDING secret for an account with no active authenticator.

    Returns the plaintext secret once, for display during enrollment. Only the
    encrypted form is stored. Refuses to replace an active authenticator, because
    that requires proof of the current device (handled in Checkpoint 2).
    """
    secret = new_secret()
    token = encrypt_secret(secret)
    now = _now_iso()
    row = _row(user_id)
    if row is None:
        db.execute("INSERT INTO student_authenticators(user_id, secret_enc, status, updated_at) "
                   "VALUES(?,?,'pending',?)", (user_id, token, now))
    else:
        cur = db.execute(
            "UPDATE student_authenticators SET secret_enc=?, status='pending', last_step=NULL, "
            "enrolled_at=NULL, updated_at=? WHERE user_id=? AND status<>'active'",
            (token, now, user_id))
        if cur.rowcount != 1:
            raise AuthenticatorStateError('An authenticator is already active for this account.')
    return secret


def confirm_enrollment(user_id, code, now=None):
    """Activate a pending authenticator once the student proves it works.

    The confirming time step is recorded as used, so the same code cannot be
    replayed at login straight after setup. Rate limited (see body).
    """
    # M1: same DB-backed lockout as verify_second_factor. A locked account is refused
    # before any code is checked; each wrong code is counted; success clears the count.
    if otp_locked_seconds(user_id):
        return False
    row = _row(user_id)
    if row is None or row['status'] != 'pending':
        return False
    step = verify_totp(decrypt_secret(row['secret_enc']), code, last_step=None, now=now)
    if step is None:
        record_otp_failure(user_id)
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


def record_otp_failure(user_id):
    register_login_failure(_otp_key(user_id),
                           int(current_app.config.get('OTP_MAX_ATTEMPTS', 5)),
                           int(current_app.config.get('OTP_LOCKOUT_SECONDS', 900)),
                           window_seconds=FAILURE_WINDOW_SECONDS)


def clear_otp_failures(user_id):
    clear_login_failures(_otp_key(user_id))


# --------------------------------------------------------------------------- #
# Full second-factor check (used by the Checkpoint 3 gate)
# --------------------------------------------------------------------------- #
def verify_second_factor(user_id, code, now=None):
    """Check a TOTP or backup code for an enrolled student. Returns (ok, reason).

    Reasons: ``totp`` / ``backup`` (success), ``locked``, ``not_enrolled``,
    ``invalid``.  Order: rate limit, then TOTP with replay protection, then
    backup code.  Every failure counts toward the lockout.
    """
    if otp_locked_seconds(user_id):
        return False, 'locked'
    row = _row(user_id)
    if row is None or row['status'] != 'active':
        return False, 'not_enrolled'
    text = str(code or '').strip()
    step = verify_totp(decrypt_secret(row['secret_enc']), text,
                       last_step=row['last_step'], now=now)
    if step is not None:
        if consume_step(user_id, step):
            clear_otp_failures(user_id)
            return True, 'totp'
    elif len(_normalise_code(text)) == BACKUP_CODE_LENGTH and consume_backup_code(user_id, text):
        clear_otp_failures(user_id)
        return True, 'backup'
    record_otp_failure(user_id)
    return False, 'invalid'
