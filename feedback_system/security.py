"""Security utilities: password hashing, CSRF, RBAC, rate limiting, audit log."""
import functools
import secrets
import time

from flask import g, jsonify, redirect, request, session, url_for, abort
from werkzeug.security import check_password_hash, generate_password_hash

from . import db

# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(plain):
    return generate_password_hash(plain)


def verify_password(stored_hash, plain):
    if not stored_hash:
        return False
    try:
        return check_password_hash(stored_hash, plain)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Sessions / current user
# --------------------------------------------------------------------------- #
def login_user(user_row):
    session.clear()
    session['uid'] = user_row['id']
    session['role'] = user_row['role']
    session['name'] = user_row['name'] or user_row['username']
    session['token'] = secrets.token_hex(16)      # CSRF token
    session.permanent = False


def logout_user():
    session.clear()


def current_user():
    """Return the logged-in user as a dict, cached per request. None if guest."""
    if 'user' in g:
        return g.user
    uid = session.get('uid')
    user = None
    if uid:
        try:
            row = db.query(
                'SELECT id, role, username, email, name, roll, emp_code, is_active, '
                'must_change_password, email_verified, onboarding_complete, designation '
                'FROM users WHERE id=?', (uid,), one=True)
        except Exception:
            row = db.query(
                'SELECT id, role, username, email, name, roll, emp_code, is_active, '
                'must_change_password FROM users WHERE id=?', (uid,), one=True)
        if row and row['is_active']:
            user = dict(row)
            user.setdefault('email_verified', 0)
            user.setdefault('onboarding_complete', 0)
            user.setdefault('designation', None)
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
    return bool(sent) and secrets.compare_digest(sent, session.get('token', ''))


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


# --------------------------------------------------------------------------- #
# Simple in-process login rate limiting / lockout
# --------------------------------------------------------------------------- #
_ATTEMPTS = {}


def _now():
    return time.time()


def login_locked(key):
    rec = _ATTEMPTS.get(key)
    if not rec:
        return 0
    if rec.get('until', 0) > _now():
        return int(rec['until'] - _now())
    return 0


def register_login_failure(key, max_attempts, lock_seconds):
    rec = _ATTEMPTS.setdefault(key, {'count': 0, 'until': 0})
    rec['count'] += 1
    if rec['count'] >= max_attempts:
        rec['until'] = _now() + lock_seconds
        rec['count'] = 0


def clear_login_failures(key):
    _ATTEMPTS.pop(key, None)


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #
def audit(action, entity=None, entity_id=None, detail=None, actor=None):
    actor = actor or current_user()
    try:
        db.execute(
            'INSERT INTO audit_log(actor_user_id, actor_label, action, entity, '
            'entity_id, detail) VALUES(?,?,?,?,?,?)',
            (actor['id'] if actor else None,
             (actor['name'] or actor['username']) if actor else 'system',
             action, entity, entity_id, detail))
    except Exception:
        # Auditing must never break the request flow.
        pass


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
