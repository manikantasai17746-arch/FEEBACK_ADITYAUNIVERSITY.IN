"""Security utilities: password hashing, CSRF, RBAC, rate limiting, audit log."""
import functools
import hashlib
import logging
import os
import secrets
import threading
import time

from flask import abort, current_app, g, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import db

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(plain):
    # scrypt is memory-hard; fall back to PBKDF2 only if this Python lacks it.
    try:
        return generate_password_hash(plain, method='scrypt')
    except Exception:
        return generate_password_hash(plain, method='pbkdf2:sha256:600000')


_DUMMY_HASH = hash_password('not-a-real-password')


def verify_password(stored_hash, plain):
    if plain is None or len(plain) > 1024:
        return False
    if not stored_hash:
        # Do the same amount of work as a real check so the response time does
        # not reveal whether an account exists.
        try:
            check_password_hash(_DUMMY_HASH, plain)
        except Exception:
            pass
        return False
    try:
        return check_password_hash(stored_hash, plain)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Shared default student password (C1.1)
#
# Every imported student starts on one shared, publicly guessable password
# (config ``DEFAULT_STUDENT_PASSWORD``).  A student still on it has NOT proved
# they own the account, so they must replace it before using the portal.  The
# value is never hard-coded here and never logged; detection works on the stored
# hash, so it does not depend on the ``must_change_password`` column being right.
# --------------------------------------------------------------------------- #
_DEFAULT_MATCH_CACHE = {}
_DEFAULT_MATCH_CACHE_MAX = 20000
_DEFAULT_MATCH_LOCK = threading.Lock()


def configured_student_default():
    """The configured shared student password, or None when it is not usable.

    "Not usable" = no app context, missing, not a string, or blank. Callers treat
    None as "no default can be recognised" rather than guessing one.
    """
    try:
        value = current_app.config.get('DEFAULT_STUDENT_PASSWORD')
    except RuntimeError:              # called outside an application context
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def is_student_default_password(plain):
    """True when the plaintext ``plain`` is the configured shared student password.

    Used on a password a student is about to choose. Constant-time comparison.
    """
    default = configured_student_default()
    if default is None or not isinstance(plain, str):
        return False
    return secrets.compare_digest(plain.encode('utf-8'), default.encode('utf-8'))


def password_hash_matches_student_default(stored_hash):
    """True when ``stored_hash`` verifies against the configured shared password.

    Uses the normal password verifier (no plaintext comparison). Missing/blank/
    malformed hashes and an unusable configuration all give False.

    Verifying scrypt costs ~80 ms, so the answer is cached. The cache key is a
    digest of BOTH the stored hash and the configured default, which makes the
    cached value a pure function of its inputs: changing the configuration, or a
    student changing their password, simply produces a different key. Neither the
    hash nor the password is stored in the clear or logged.
    """
    default = configured_student_default()
    if default is None or not stored_hash or not isinstance(stored_hash, str):
        return False
    key = hashlib.sha256(
        stored_hash.encode('utf-8') + b'\x00' + default.encode('utf-8')).hexdigest()
    cached = _DEFAULT_MATCH_CACHE.get(key)
    if cached is not None:
        return cached
    result = bool(verify_password(stored_hash, default))
    with _DEFAULT_MATCH_LOCK:
        if len(_DEFAULT_MATCH_CACHE) >= _DEFAULT_MATCH_CACHE_MAX:
            _DEFAULT_MATCH_CACHE.clear()          # bounded memory; just recomputes
        _DEFAULT_MATCH_CACHE[key] = result
    return result


def student_has_default_password(user_id):
    """True when ``user_id`` is a STUDENT whose stored hash is the shared default.

    False for unknown users, non-students (teachers/HOD/Dean/admin are never
    judged against the student default), users without a hash, and when the
    default is not configured.
    """
    if not user_id:
        return False
    row = db.query('SELECT role, password_hash FROM users WHERE id=?', (user_id,), one=True)
    if not row or row['role'] != 'student':
        return False
    return password_hash_matches_student_default(row['password_hash'])


def student_password_change_required(user):
    """Decide whether ``user`` (a current_user() dict) must change their password.

    Students: yes when the ``must_change_password`` flag is set OR their stored
    password is still the shared default. The flag alone is not trusted because
    imported accounts can carry 0 with the default hash. When the default is
    detected the flag is persisted, so the account stays forced even if the
    configured default is later changed. Any other role: always False here (their
    own rules live elsewhere).
    """
    if not user or user.get('role') != 'student':
        return False
    if user.get('must_change_password'):
        return True
    if not student_has_default_password(user.get('id')):
        return False
    db.execute("UPDATE users SET must_change_password=1 "
               "WHERE id=? AND role='student' AND must_change_password=0", (user['id'],))
    user['must_change_password'] = 1
    return True


# --------------------------------------------------------------------------- #
# Sessions / current user
# --------------------------------------------------------------------------- #
SESSION_IDLE_SECONDS = 30 * 60        # log out after 30 minutes of inactivity
SESSION_MAX_SECONDS = 8 * 60 * 60     # and always after 8 hours


def login_user(user_row):
    session.clear()
    session['uid'] = user_row['id']
    session['role'] = user_row['role']
    session['name'] = user_row['name'] or user_row['username']
    session['token'] = secrets.token_hex(16)      # CSRF token
    now = time.time()
    session['started'] = now
    session['last'] = now
    session.permanent = False


def logout_user():
    session.clear()


def _session_expired():
    """True when the session is idle too long or older than the maximum age."""
    now = time.time()
    started = session.get('started')
    last = session.get('last')
    if started is None or last is None:       # session created before this change
        session['started'] = session['last'] = now
        return False
    if now - last > SESSION_IDLE_SECONDS or now - started > SESSION_MAX_SECONDS:
        return True
    if now - last > 60:
        session['last'] = now
    return False


def current_user():
    """Return the logged-in user as a dict, cached per request. None if guest."""
    if 'user' in g:
        return g.user
    uid = session.get('uid')
    if uid and _session_expired():
        session.clear()
        uid = None
    user = None
    if uid:
        try:
            row = db.query(
                'SELECT id, role, username, email, name, roll, emp_code, is_active, '
                'must_change_password, email_verified, onboarding_complete, designation, '
                'first_login_complete FROM users WHERE id=?', (uid,), one=True)
        except Exception:
            row = db.query(
                'SELECT id, role, username, email, name, roll, emp_code, is_active, '
                'must_change_password FROM users WHERE id=?', (uid,), one=True)
        if row and row['is_active']:
            user = dict(row)
            user.setdefault('email_verified', 0)
            user.setdefault('onboarding_complete', 0)
            user.setdefault('designation', None)
            user.setdefault('first_login_complete', 1)
        else:
            session.clear()
    g.user = user
    return user


# --------------------------------------------------------------------------- #
# Decorators
# --------------------------------------------------------------------------- #
def login_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for('auth.student_login', next=request.path))
        return view(*args, **kwargs)
    return wrapper


def role_required(*roles):
    def decorator(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                # Route the guest to the most relevant login page.
                if 'hod' in roles:
                    target = 'auth.hod_login'
                elif 'dean' in roles:
                    target = 'auth.dean_login'
                elif 'teacher' in roles:
                    target = 'auth.teacher_login'
                elif 'admin' in roles:
                    target = 'auth.admin_login'
                else:
                    target = 'auth.student_login'
                return redirect(url_for(target, next=request.path))
            if user['role'] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return decorator


# --------------------------------------------------------------------------- #
# Safe redirects
# --------------------------------------------------------------------------- #
def is_safe_next(target):
    """True only for same-site relative paths. Use before redirecting to ?next=."""
    if not target or not isinstance(target, str):
        return False
    if not target.startswith('/') or target.startswith('//') or '\\' in target:
        return False
    return not any(ord(c) < 32 for c in target)


# --------------------------------------------------------------------------- #
# CSRF
# --------------------------------------------------------------------------- #
def csrf_token():
    token = session.get('token')
    if not token:
        token = secrets.token_hex(16)
        session['token'] = token
    return token


def _csrf_ok():
    sent = request.form.get('_csrf') or request.headers.get('X-CSRF-Token')
    expected = session.get('token', '')
    if not sent or not expected:
        return False
    return secrets.compare_digest(sent.encode('utf-8'), expected.encode('utf-8'))


# --------------------------------------------------------------------------- #
# Security headers, cookie flags and CSP nonce
# --------------------------------------------------------------------------- #
def init_security_headers(app):
    """Called from init_csrf(), so no change to the app factory is needed.

    Environment switches (both default off so nothing breaks on first deploy):
      FORCE_HTTPS=1   Secure session cookie + HSTS header. Set once the site is on HTTPS.
      CSP_ENFORCE=1   Enforce the Content-Security-Policy. Until then it only reports
                      problems in the browser console (Report-Only).
    """
    force_https = os.environ.get('FORCE_HTTPS', '0') == '1'
    enforce_csp = os.environ.get('CSP_ENFORCE', '0') == '1'

    app.config['SESSION_COOKIE_HTTPONLY'] = True
    if not app.config.get('SESSION_COOKIE_SAMESITE'):
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    if force_https:
        app.config['SESSION_COOKIE_SECURE'] = True

    @app.before_request
    def _csp_nonce():
        g.csp_nonce = secrets.token_urlsafe(16)

    @app.context_processor
    def _inject_csp_nonce():
        return {'csp_nonce': g.get('csp_nonce', '')}

    @app.after_request
    def _security_headers(resp):
        nonce = g.get('csp_nonce', '')
        csp = '; '.join([
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "media-src 'self' blob:",
            "font-src 'self' data:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'self'",
        ])
        name = ('Content-Security-Policy' if enforce_csp
                else 'Content-Security-Policy-Report-Only')
        resp.headers[name] = csp
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        resp.headers.setdefault('Permissions-Policy',
                                'camera=(self), microphone=(), geolocation=()')
        resp.headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
        resp.headers.setdefault('Cache-Control', 'no-store')
        if force_https:
            resp.headers.setdefault('Strict-Transport-Security',
                                    'max-age=31536000; includeSubDomains')
        return resp


def init_csrf(app):
    @app.before_request
    def _csrf_protect():
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            # Exports/downloads and JSON APIs still must carry the token.
            if not _csrf_ok():
                if request.accept_mimetypes.best == 'application/json' or \
                        request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify(error='CSRF token missing or invalid.'), 400
                abort(400, description='CSRF token missing or invalid.')

    @app.errorhandler(400)
    def _bad_request(err):
        from flask import render_template
        return render_template('error.html', code=400,
                               message=getattr(err, 'description', 'Bad request.'),
                               user=None), 400

    init_security_headers(app)


# --------------------------------------------------------------------------- #
# Simple in-process login rate limiting / lockout
# --------------------------------------------------------------------------- #
_ATTEMPTS = {}
_ATTEMPTS_LOCK = threading.Lock()
_MAX_TRACKED = 5000


def _now():
    return time.time()


def _prune(now):
    """Keep memory bounded: drop the oldest unlocked records when the table is big."""
    if len(_ATTEMPTS) <= _MAX_TRACKED:
        return
    stale = [k for k, r in _ATTEMPTS.items() if r['until'] <= now]
    stale.sort(key=lambda k: _ATTEMPTS[k]['first'])
    for k in stale[:max(1, len(stale) // 2)]:
        _ATTEMPTS.pop(k, None)


def _db_key(key):
    """Hash the key so raw roll numbers / e-mails are never stored and length is bounded."""
    return hashlib.sha256(str(key).encode('utf-8')).hexdigest()


def _mem_locked(key):
    with _ATTEMPTS_LOCK:
        rec = _ATTEMPTS.get(key)
        if not rec:
            return 0
        if rec.get('until', 0) > _now():
            return int(rec['until'] - _now())
        return 0


def _mem_register(key, max_attempts, lock_seconds, window_seconds):
    now = _now()
    with _ATTEMPTS_LOCK:
        _prune(now)
        rec = _ATTEMPTS.get(key)
        if rec is None or (rec['until'] <= now and now - rec['first'] > window_seconds):
            rec = _ATTEMPTS[key] = {'count': 0, 'until': 0, 'first': now}
        rec['count'] += 1
        if rec['count'] >= max_attempts:
            rec['until'] = now + lock_seconds
            rec['count'] = 0
            rec['first'] = now


def login_locked(key):
    """Seconds left on a lockout (0 = not locked). Shared across all server instances.

    Uses the login_attempts table; falls back to in-process memory if the
    database call fails, so a database hiccup can never block every login.
    """
    try:
        row = db.query('SELECT until_ts FROM login_attempts WHERE attempt_key=?',
                       (_db_key(key),), one=True)
        if not row:
            return 0
        left = int(row['until_ts']) - int(_now())
        return left if left > 0 else 0
    except Exception:
        logger.warning('login_attempts lookup failed; using in-memory lockout', exc_info=True)
        return _mem_locked(key)


# Atomic lockout counter (M2).  Every statement below is a single database
# operation, so concurrent requests cannot overwrite each other's increments:
# ``fail_count = fail_count + 1`` is evaluated by the database on the current row
# (SQLite serialises writers; PostgreSQL re-evaluates the UPDATE on the latest row
# after waiting for the row lock).  Only SQL that runs on both databases is used.
_SQL_ENSURE_ROW = ('INSERT INTO login_attempts(attempt_key, fail_count, first_ts, until_ts) '
                   'VALUES(?,0,?,0) ON CONFLICT(attempt_key) DO NOTHING')
_SQL_RESET_EXPIRED = ('UPDATE login_attempts SET fail_count=0, first_ts=?, until_ts=0 '
                      'WHERE attempt_key=? AND until_ts <= ? AND ? - first_ts > ?')
_SQL_INCREMENT = 'UPDATE login_attempts SET fail_count=fail_count+1 WHERE attempt_key=?'
# Reservation variant: counts only while the key is not locked, so attempts refused during
# a lockout are not counted and cannot extend it.
_SQL_INCREMENT_UNLOCKED = ('UPDATE login_attempts SET fail_count=fail_count+1 '
                           'WHERE attempt_key=? AND until_ts <= ?')
_SQL_READ = 'SELECT fail_count, until_ts FROM login_attempts WHERE attempt_key=?'
_SQL_LOCK = ('UPDATE login_attempts SET fail_count=0, first_ts=?, until_ts=? '
             'WHERE attempt_key=? AND fail_count >= ?')


def _count_attempt(key, max_attempts, lock_seconds, window_seconds, respect_lock=False):
    """Count one attempt atomically. Returns (count, until, now), or None.

    ``count`` and ``until`` are read just after this attempt's increment and before
    any lock is applied, so the caller can tell whether the attempt was within the
    limit.  If the count has reached ``max_attempts`` the key is locked for
    ``lock_seconds`` and the count is reset, as before.

    With ``respect_lock`` (reservations) an attempt made while the key is locked is
    not counted at all and None is returned.  Without it (password flows) every
    failure is counted, as it always was.
    """
    now = int(_now())
    k = _db_key(key)
    db.execute(_SQL_ENSURE_ROW, (k, now))
    db.execute(_SQL_RESET_EXPIRED, (now, k, now, now, window_seconds))
    if respect_lock:
        if db.execute(_SQL_INCREMENT_UNLOCKED, (k, now)).rowcount != 1:
            return None                 # locked: refused, and not counted
    else:
        db.execute(_SQL_INCREMENT, (k,))
    row = db.query(_SQL_READ, (k,), one=True)
    count, until = int(row['fail_count']), int(row['until_ts'])
    if count >= max_attempts:
        db.execute(_SQL_LOCK, (now, now + lock_seconds, k, max_attempts))
    if secrets.randbelow(50) == 0:      # occasional cleanup keeps the table small
        db.execute('DELETE FROM login_attempts WHERE until_ts < ? AND first_ts < ?',
                   (now, now - 4 * window_seconds))
    return count, until, now


def register_login_failure(key, max_attempts, lock_seconds, window_seconds=900):
    """Count a failed login (password flows). Behaviour is unchanged from before M2:
    failures older than ``window_seconds`` stop counting, and reaching
    ``max_attempts`` locks the key for ``lock_seconds``. Only the counting is atomic."""
    try:
        _count_attempt(key, max_attempts, lock_seconds, window_seconds)
    except Exception:
        logger.warning('login_attempts write failed; using in-memory lockout', exc_info=True)
        _mem_register(key, max_attempts, lock_seconds, window_seconds)


def reserve_login_attempt(key, max_attempts, lock_seconds, window_seconds=900):
    """Reserve one attempt BEFORE its code is checked. True if it may proceed.

    Used by the OTP path.  Requests that arrive together each reserve an attempt
    first, so no more than ``max_attempts`` attempts are allowed per window, however
    many arrive at once.  An attempt is refused if the key is already locked, or if
    its count is past the limit.  Under heavy concurrency an attempt can be refused
    when it could have been allowed (the count it reads may already include later
    attempts); it is never allowed beyond the limit.
    """
    try:
        result = _count_attempt(key, max_attempts, lock_seconds, window_seconds,
                                respect_lock=True)
        if result is None:
            return False
        count, until, now = result
        return until <= now and count <= max_attempts
    except Exception:
        logger.warning('login_attempts write failed; using in-memory lockout', exc_info=True)
        if _mem_locked(key):
            return False
        _mem_register(key, max_attempts, lock_seconds, window_seconds)
        return True


def clear_login_failures(key):
    try:
        db.execute('DELETE FROM login_attempts WHERE attempt_key=?', (_db_key(key),))
    except Exception:
        logger.warning('login_attempts clear failed', exc_info=True)
    with _ATTEMPTS_LOCK:
        _ATTEMPTS.pop(key, None)


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #
def audit(action, entity=None, entity_id=None, detail=None, actor=None):
    if actor is None:
        try:
            actor = current_user()
        except RuntimeError:          # service called outside a request (jobs, tests)
            actor = None
    if detail is not None:
        # One line per entry and bounded size, so logged input cannot forge entries.
        detail = str(detail).replace('\r', ' ').replace('\n', ' ')[:2000]
    try:
        db.execute(
            'INSERT INTO audit_log(actor_user_id, actor_label, action, entity, '
            'entity_id, detail) VALUES(?,?,?,?,?,?)',
            (actor['id'] if actor else None,
             (actor['name'] or actor['username']) if actor else 'system',
             action, entity, entity_id, detail))
    except Exception:
        # Auditing must never break the request flow, but a failure must not be silent.
        logger.exception('Audit log write failed for action=%s', action)


# --------------------------------------------------------------------------- #
# Department scope helpers (HOD / Dean)
# --------------------------------------------------------------------------- #
def user_branch_ids(user_id):
    """Return the list of branch_id values the user is authorised for."""
    rows = db.query(
        'SELECT branch_id FROM user_department_scopes WHERE user_id=?',
        (user_id,))
    return [r['branch_id'] for r in rows]


def user_has_branch(user_id, branch_id):
    if branch_id is None:
        return False
    row = db.query(
        'SELECT 1 FROM user_department_scopes WHERE user_id=? AND branch_id=?',
        (user_id, branch_id), one=True)
    return bool(row)


def require_branch_access(user, branch_id):
    """Abort 403 when the user has no scope for ``branch_id``.

    Admins always pass. HOD/Dean must have an explicit scope row.
    """
    if not user:
        abort(403)
    if user['role'] == 'admin':
        return True
    if user['role'] not in ('hod', 'dean'):
        abort(403)
    if not user_has_branch(user['id'], branch_id):
        abort(403)
    return True


def scoped_branch_ids_or_none(user):
    """For admin: None (meaning all). For hod/dean: list of branch ids.

    Prefers hod_allocations when present; falls back to user_department_scopes.
    Dean visibility is derived from assigned HODs' allocations.
    """
    if not user:
        return []
    if user['role'] == 'admin':
        return None
    if user['role'] in ('hod', 'dean'):
        # Same answer as the legacy multi-query logic below, but served from the
        # single cached per-request lookup (no extra database trips).
        return [b['id'] for b in scoped_branches(user)]
    if user['role'] == 'hod':
        rows = db.query(
            'SELECT DISTINCT branch_id FROM hod_allocations WHERE hod_user_id=?',
            (user['id'],))
        if rows:
            return [r['branch_id'] for r in rows]
        return user_branch_ids(user['id'])
    if user['role'] == 'dean':
        rows = db.query(
            """SELECT DISTINCT ha.branch_id
               FROM dean_hod_assignments dha
               JOIN hod_allocations ha ON ha.hod_user_id = dha.hod_user_id
               WHERE dha.dean_user_id=?""",
            (user['id'],))
        if rows:
            return [r['branch_id'] for r in rows]
        return user_branch_ids(user['id'])
    return user_branch_ids(user['id'])


def scoped_branches(user):
    """Branch rows (id, code, name) the HOD/Dean may see, in ONE query.

    Same rules as :func:`scoped_branch_ids_or_none` (allocations first, then the
    explicit department scopes) but it returns the branch rows directly, so a
    portal page no longer needs a separate "which ids" query followed by a
    "load those branches" query.  Admins get every branch.
    """
    if not user:
        return []
    uid = user['id']
    role = user['role']
    cache = g.setdefault('_scoped_branches', {})
    if uid in cache:
        return cache[uid]
    rows = _load_scoped_branches(uid, role)
    cache[uid] = rows
    return rows


def _load_scoped_branches(uid, role):
    if role == 'admin':
        return db.query('SELECT id, code, name FROM branches ORDER BY code')
    if role == 'dean':
        primary = ("SELECT ha.branch_id FROM dean_hod_assignments dha "
                   "JOIN hod_allocations ha ON ha.hod_user_id = dha.hod_user_id "
                   "WHERE dha.dean_user_id=?")
    else:
        primary = "SELECT branch_id FROM hod_allocations WHERE hod_user_id=?"
    # One round trip: use the primary source when it has rows, else the
    # department-scope table.
    return db.query(
        f"""SELECT id, code, name FROM branches
            WHERE id IN ({primary})
               OR (NOT EXISTS ({primary})
                   AND id IN (SELECT branch_id FROM user_department_scopes
                              WHERE user_id=?))
            ORDER BY code""",
        (uid, uid, uid))


def hod_allocation_keys(hod_user_id):
    """Return list of dicts for each exclusive allocation owned by the HOD."""
    return db.query(
        """SELECT ha.id, ha.academic_year_id, ha.branch_id, ha.semester_id,
                  ha.timetable_id, ay.name AS academic_year, b.code AS branch_code,
                  b.name AS branch_name, s.number AS semester_number,
                  s.label AS semester_label, t.code AS timetable_code,
                  t.name AS timetable_name
           FROM hod_allocations ha
           JOIN academic_years ay ON ay.id = ha.academic_year_id
           JOIN branches b ON b.id = ha.branch_id
           JOIN semesters s ON s.id = ha.semester_id
           JOIN timetables t ON t.id = ha.timetable_id
           WHERE ha.hod_user_id=?
           ORDER BY ay.name, b.code, s.number, t.code""",
        (hod_user_id,))


def allocation_owner(academic_year_id, branch_id, semester_id, timetable_id):
    """Return the HOD user_id that owns this exact key, or None."""
    row = db.query(
        """SELECT hod_user_id FROM hod_allocations
           WHERE academic_year_id=? AND branch_id=? AND semester_id=?
             AND timetable_id=?""",
        (academic_year_id, branch_id, semester_id, timetable_id), one=True)
    return row['hod_user_id'] if row else None


def dean_assigned_hod_ids(dean_user_id):
    rows = db.query(
        'SELECT hod_user_id FROM dean_hod_assignments WHERE dean_user_id=?',
        (dean_user_id,))
    return [r['hod_user_id'] for r in rows]
