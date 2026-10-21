"""One-time e-mail codes (verify an address / reset a password)."""
import hashlib
import hmac
import secrets
import time

from flask import current_app

from . import db

CODE_TTL_SECONDS = 10 * 60
RESEND_COOLDOWN_SECONDS = 60
MAX_ATTEMPTS = 5


class Cooldown(Exception):
    def __init__(self, seconds):
        super().__init__(seconds)
        self.seconds = seconds


def _hash(user_id, purpose, code):
    key = str(current_app.config['SECRET_KEY']).encode()
    return hmac.new(key, f'{user_id}:{purpose}:{code}'.encode(), hashlib.sha256).hexdigest()


def issue(user_id, purpose, email):
    """Create a fresh 6-digit code (replacing older ones) and return it.

    Raises :class:`Cooldown` if a code was issued less than a minute ago.
    """
    now = int(time.time())
    last = db.query('SELECT created_ts FROM email_codes WHERE user_id=? AND purpose=? '
                    'ORDER BY created_ts DESC', (user_id, purpose), one=True)
    if last and now - int(last['created_ts']) < RESEND_COOLDOWN_SECONDS:
        raise Cooldown(RESEND_COOLDOWN_SECONDS - (now - int(last['created_ts'])))
    code = f'{secrets.randbelow(1_000_000):06d}'
    db.execute('DELETE FROM email_codes WHERE user_id=? AND purpose=?', (user_id, purpose))
    db.execute('INSERT INTO email_codes(user_id, purpose, email, code_hash, attempts, '
               'created_ts, expires_ts) VALUES(?,?,?,?,0,?,?)',
               (user_id, purpose, email, _hash(user_id, purpose, code), now,
                now + CODE_TTL_SECONDS))
    return code


def discard(user_id, purpose):
    db.execute('DELETE FROM email_codes WHERE user_id=? AND purpose=?', (user_id, purpose))


def pending(user_id, purpose):
    """The live (unexpired) code row for this user/purpose, or None."""
    row = db.query('SELECT * FROM email_codes WHERE user_id=? AND purpose=?',
                   (user_id, purpose), one=True)
    if row and int(row['expires_ts']) < int(time.time()):
        discard(user_id, purpose)
        return None
    return row


def check(user_id, purpose, code):
    """Return the e-mail address the code was sent to if ``code`` is right, else None.

    Wrong guesses are counted; after MAX_ATTEMPTS the code is destroyed.
    A correct code is consumed.
    """
    row = pending(user_id, purpose)
    code = (code or '').strip().replace(' ', '')
    if not row:
        return None
    if int(row['attempts']) >= MAX_ATTEMPTS:
        discard(user_id, purpose)
        return None
    if hmac.compare_digest(row['code_hash'], _hash(user_id, purpose, code)):
        discard(user_id, purpose)
        return row['email']
    db.execute('UPDATE email_codes SET attempts=attempts+1 WHERE id=?', (row['id'],))
    if int(row['attempts']) + 1 >= MAX_ATTEMPTS:
        discard(user_id, purpose)
    return None
