"""Authentication routes for all three roles."""
import re

from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, url_for, session)

from . import db, mailer, verification
from .security import (audit, clear_login_failures, current_user, hash_password,
                       login_locked, login_user, logout_user,
                       register_login_failure, role_required, verify_password)

bp = Blueprint('auth', __name__)


def _safe_next(default_endpoint):
    nxt = request.args.get('next') or request.form.get('next')
    if nxt and nxt.startswith('/') and not nxt.startswith('//'):
        return nxt
    return url_for(default_endpoint)


def _existing_user(ident, role):
    """Resolve an active user for login.

    * admin  — match lowercased username **or** email (bootstrap stores both
      as the admin email).
    * hod/dean — match uppercased emp_code (preferred) or username; CSV import
      stores the employee ID in both columns.
    * student/teacher — match username exactly (historical behaviour).
    """
    if not ident:
        return None
    if role == 'admin':
        key = ident.strip().lower()
        return db.query(
            """SELECT * FROM users
               WHERE role='admin' AND is_active=1
                 AND (LOWER(username) = ?
                      OR LOWER(COALESCE(email, '')) = ?)""",
            (key, key), one=True)
    if role in ('hod', 'dean'):
        key = ident.strip().upper()
        return db.query(
            """SELECT * FROM users
               WHERE role=? AND is_active=1
                 AND (UPPER(COALESCE(emp_code, '')) = ?
                      OR UPPER(username) = ?)""",
            (role, key, key), one=True)
    return db.query(
        'SELECT * FROM users WHERE username=? AND role=? AND is_active=1',
        (ident, role), one=True)


def _login_attempt(role, username, password, redirect_endpoint):
    key = f'{role}:{username}:{request.remote_addr}'
    remaining = login_locked(key)
    if remaining:
        flash(f'Too many failed attempts. Please try again in {remaining} seconds.', 'error')
        return None
    user = _existing_user(username, role)
    if user and verify_password(user['password_hash'], password):
        clear_login_failures(key)
        if role == 'teacher':
            # Force a password change only while the teacher is still on the
            # shared default password; a personal password is left alone.
            still_default = password == current_app.config['DEFAULT_TEACHER_PASSWORD']
            db.execute('UPDATE users SET must_change_password=? WHERE id=?',
                       (1 if still_default else 0, user['id']))
        login_user(user)
        return redirect(_safe_next(redirect_endpoint))
    register_login_failure(key, current_app.config['LOGIN_MAX_ATTEMPTS'],
                           current_app.config['LOGIN_LOCKOUT_SECONDS'])
    flash('Invalid credentials. Please check and try again.', 'error')
    return None


# --------------------------------------------------------------------------- #
# First-login account setup (email, then default-password change for teachers)
# --------------------------------------------------------------------------- #
_EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$')
_SETUP_ENDPOINTS = {'auth.logout', 'auth.setup_email', 'auth.verify_email',
                    'auth.change_password', 'static',
                    'hod.onboarding', 'hod.onboarding_verify',
                    'hod.onboarding_save', 'dean.onboarding',
                    'dean.onboarding_verify', 'dean.onboarding_save'}


def _home_for(role):
    return {
        'admin': 'admin.dashboard',
        'teacher': 'teacher.dashboard',
        'student': 'student.dashboard',
        'hod': 'hod.dashboard',
        'dean': 'dean.dashboard',
    }.get(role, 'auth.student_login')


@bp.before_app_request
def _enforce_account_setup():
    if request.endpoint in _SETUP_ENDPOINTS or request.endpoint is None:
        return None
    role = session.get('role')
    if not session.get('uid') or role not in ('student', 'teacher', 'hod', 'dean'):
        return None
    user = current_user()
    if not user or user['role'] not in ('student', 'teacher', 'hod', 'dean'):
        return None

    # Password change is always enforced for any role that has the flag.
    if user.get('must_change_password'):
        return redirect(url_for('auth.change_password'))

    # Student/teacher email setup (existing behaviour, gated by config).
    if current_app.config.get('REQUIRE_ACCOUNT_SETUP', True) and role in ('student', 'teacher'):
        if not (user.get('email') or '').strip():
            return redirect(url_for('auth.setup_email', next=request.full_path.rstrip('?')
                                    if request.method == 'GET' else None))

    # HOD / Dean first-login onboarding (email verify + scope claim).
    if role in ('hod', 'dean'):
        # Refresh flags from DB (current_user may not include new columns yet)
        row = db.query(
            'SELECT email_verified, onboarding_complete FROM users WHERE id=?',
            (user['id'],), one=True)
        if row and not row['onboarding_complete']:
            ep = 'hod.onboarding' if role == 'hod' else 'dean.onboarding'
            return redirect(url_for(ep))
    return None


def _send_code(user_id, purpose, email):
    """Issue + e-mail a code. Returns (ok, message_if_not_ok)."""
    try:
        code = verification.issue(user_id, purpose, email)
    except verification.Cooldown as c:
        return False, f'Please wait {c.seconds} seconds before asking for another code.'
    subject, text, html = mailer.code_email(code, purpose, verification.CODE_TTL_SECONDS // 60)
    if not mailer.send_email(email, subject, text, html):
        verification.discard(user_id, purpose)
        return False, ('We could not send the email right now. Please check the address '
                       'and try again, or contact the administrator.')
    return True, None


@bp.route('/setup/email', methods=['GET', 'POST'])
@role_required('student', 'teacher')
def setup_email():
    user = current_user()
    if (user.get('email') or '').strip() and request.method == 'GET':
        return redirect(url_for(_home_for(user['role'])))
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        if not _EMAIL_RE.match(email) or len(email) > 254:
            flash('Please enter a valid email address, e.g. name@gmail.com.', 'error')
        else:
            ok, problem = _send_code(user['id'], 'verify_email', email)
            if ok:
                flash(f'We sent a 6-digit code to {email}.', 'success')
                return redirect(url_for('auth.verify_email', next=request.form.get('next') or None))
            flash(problem, 'error')
    return render_template('setup_email.html')


@bp.route('/setup/email/verify', methods=['GET', 'POST'])
@role_required('student', 'teacher')
def verify_email():
    user = current_user()
    row = verification.pending(user['id'], 'verify_email')
    if not row:
        flash('That code has expired. Please enter your email again.', 'warning')
        return redirect(url_for('auth.setup_email'))
    if request.method == 'POST':
        if request.form.get('action') == 'resend':
            ok, problem = _send_code(user['id'], 'verify_email', row['email'])
            flash('A new code has been sent.' if ok else problem, 'success' if ok else 'error')
        else:
            email = verification.check(user['id'], 'verify_email', request.form.get('code'))
            if email:
                db.execute('UPDATE users SET email=? WHERE id=?', (email, user['id']))
                audit('user.verify_email', 'users', user['id'], 'Email verified at first login')
                flash('Email confirmed. Thank you!', 'success')
                return redirect(_safe_next(_home_for(user['role'])))
            flash('That code is not correct or has expired.', 'error')
            if not verification.pending(user['id'], 'verify_email'):
                return redirect(url_for('auth.setup_email'))
    return render_template('verify_email.html', email=row['email'])


# --------------------------------------------------------------------------- #
# Forgot password (code sent to the verified e-mail on file)
# --------------------------------------------------------------------------- #
_RESET_ROLES = {'student': 'Roll number', 'teacher': 'Employee code', 'admin': 'Email address'}


def _normalise_ident(role, ident):
    ident = (ident or '').strip()
    return ident.lower() if role == 'admin' else ident.upper() if role == 'student' else ident


@bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        role = request.form.get('role') or 'student'
        ident = _normalise_ident(role, request.form.get('ident'))
        if role not in _RESET_ROLES or not ident:
            flash('Please enter your ID.', 'error')
        else:
            user = _existing_user(ident, role)
            email = ((user['email'] if user else '') or '').strip()
            if user and email:
                ok, problem = _send_code(user['id'], 'reset_password', email)
                if not ok and 'wait' in problem:
                    flash(problem, 'error')
                    return render_template('forgot_password.html', roles=_RESET_ROLES,
                                           role=role, ident=ident)
            session['reset_who'] = [role, ident]
            # Same answer whether or not the account exists (no user enumeration).
            flash('If that account has a confirmed email, a 6-digit code has been sent to it.', 'info')
            return redirect(url_for('auth.reset_password'))
    return render_template('forgot_password.html', roles=_RESET_ROLES,
                           role=request.args.get('role', 'student'), ident='')


@bp.route('/forgot-password/reset', methods=['GET', 'POST'])
def reset_password():
    who = session.get('reset_who')
    if not who:
        return redirect(url_for('auth.forgot_password'))
    role, ident = who
    user = _existing_user(ident, role)
    if request.method == 'POST':
        if request.form.get('action') == 'resend':
            email = ((user['email'] if user else '') or '').strip()
            if user and email:
                ok, problem = _send_code(user['id'], 'reset_password', email)
                flash('A new code has been sent.' if ok else problem, 'success' if ok else 'error')
            else:
                flash('If that account has a confirmed email, a new code has been sent.', 'info')
        else:
            new = request.form.get('new_password') or ''
            confirm = request.form.get('confirm_password') or ''
            if len(new) < 8:
                flash('New password must be at least 8 characters.', 'error')
            elif new != confirm:
                flash('The new passwords do not match.', 'error')
            elif role == 'teacher' and new == current_app.config['DEFAULT_TEACHER_PASSWORD']:
                flash('Please choose a new password — not the default one.', 'error')
            elif not user or not verification.check(user['id'], 'reset_password',
                                                    request.form.get('code')):
                flash('That code is not correct or has expired.', 'error')
            else:
                db.execute('UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?',
                           (hash_password(new), user['id']))
                audit('user.forgot_password_reset', 'users', user['id'], 'Password reset via email code')
                session.pop('reset_who', None)
                flash('Your password has been reset. Please log in.', 'success')
                return redirect(url_for({'admin': 'auth.admin_login',
                                         'teacher': 'auth.teacher_login'}.get(role, 'auth.student_login')))
    return render_template('reset_password.html', role=role, ident=ident)


# --------------------------------------------------------------------------- #
# Public landing page
# --------------------------------------------------------------------------- #
@bp.route('/')
def landing():
    """Marketing / portal entry page with live institution stats."""
    user = current_user()
    if user:
        role = user.get('role')
        dest = {
            'student': 'student.dashboard',
            'teacher': 'teacher.dashboard',
            'hod': 'hod.dashboard',
            'dean': 'dean.dashboard',
            'admin': 'admin.dashboard',
        }.get(role)
        if dest:
            return redirect(url_for(dest))

    def _count(sql, args=()):
        try:
            row = db.query(sql, args, one=True)
            return int(row['c']) if row and row['c'] is not None else 0
        except Exception:
            return 0

    students = _count(
        "SELECT COUNT(*) c FROM users WHERE role='student' AND is_active=1")
    faculty = _count(
        "SELECT COUNT(*) c FROM users WHERE role='teacher' AND is_active=1")
    subjects = _count(
        """SELECT COUNT(*) c FROM (
               SELECT DISTINCT subject_code FROM class_sessions
               WHERE is_deleted=0 AND subject_code IS NOT NULL AND TRIM(subject_code)<>''
           ) x""")
    # Feedback completion: submissions vs eligible slots (approx)
    eligible = _count(
        """SELECT COUNT(*) c FROM session_students ss
           JOIN class_sessions cs ON cs.id=ss.session_id AND cs.is_deleted=0""")
    submitted = _count("SELECT COUNT(*) c FROM submissions")
    completion = 0
    if eligible > 0:
        completion = min(100, round(submitted * 100.0 / eligible))

    stats = {
        'students': students,
        'faculty': faculty,
        'subjects': subjects,
        'completion': completion,
    }
    return render_template('landing.html', stats=stats)


# --------------------------------------------------------------------------- #
# Student
# --------------------------------------------------------------------------- #
@bp.route('/student/login', methods=['GET', 'POST'])
def student_login():
    user = current_user()
    if user and user['role'] == 'student':
        return redirect(url_for('student.dashboard'))
    if request.method == 'POST':
        roll = (request.form.get('roll') or '').strip().upper()
        password = request.form.get('password') or ''
        if not roll or not password:
            flash('Roll number and password are required.', 'error')
        else:
            resp = _login_attempt('student', roll, password, 'student.dashboard')
            if resp:
                flash('Welcome back!', 'success')
                return resp
    return render_template('login.html')


@bp.route('/scan-login', methods=['POST'])
def scan_login():
    """Student ID-card scan: log straight in (JSON in/out, CSRF-protected)."""
    code = (request.form.get('code') or '').strip().upper()
    if not current_app.config.get('SCAN_LOGIN', True) or not code or len(code) > 40:
        return jsonify(ok=False, fallback=True)
    key = f'scan:{request.remote_addr}'
    remaining = login_locked(key)
    if remaining:
        return jsonify(ok=False, fallback=True,
                       message=f'Too many attempts. Try again in {remaining} seconds.')
    user = _existing_user(code, 'student')
    if not user:
        register_login_failure(key, current_app.config['LOGIN_MAX_ATTEMPTS'],
                               current_app.config['LOGIN_LOCKOUT_SECONDS'])
        return jsonify(ok=False, fallback=True,
                       message='This ID card was not found. Type your roll number instead.')
    if (current_app.config.get('SCAN_LOGIN_ONLY_DEFAULT', True)
            and not verify_password(user['password_hash'],
                                    current_app.config['DEFAULT_STUDENT_PASSWORD'])):
        # Personal password set: scanning only fills the roll number.
        return jsonify(ok=False, fallback=True, roll=user['username'])
    clear_login_failures(key)
    login_user(user)
    audit('user.scan_login', 'users', user['id'], 'Student logged in by ID-card scan')
    flash('Welcome back!', 'success')
    return jsonify(ok=True, redirect=_safe_next('student.dashboard'))


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #
@bp.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    user = current_user()
    if user and user['role'] == 'admin':
        return redirect(url_for('admin.dashboard'))
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        if not email or not password:
            flash('Email and password are required.', 'error')
        else:
            resp = _login_attempt('admin', email, password, 'admin.dashboard')
            if resp:
                return resp
    return render_template('admin_login.html')


# --------------------------------------------------------------------------- #
# Teacher
# --------------------------------------------------------------------------- #
@bp.route('/teacher/login', methods=['GET', 'POST'])
def teacher_login():
    user = current_user()
    if user and user['role'] == 'teacher':
        return redirect(url_for('teacher.dashboard'))
    if request.method == 'POST':
        emp = (request.form.get('emp_code') or '').strip()
        password = request.form.get('password') or ''
        if not emp or not password:
            flash('Employee code and password are required.', 'error')
        else:
            resp = _login_attempt('teacher', emp, password, 'teacher.dashboard')
            if resp:
                return resp
    return render_template('teacher_login.html')


# --------------------------------------------------------------------------- #
# HOD
# --------------------------------------------------------------------------- #
@bp.route('/hod/login', methods=['GET', 'POST'])
def hod_login():
    user = current_user()
    if user and user['role'] == 'hod':
        return redirect(url_for('hod.dashboard'))
    if request.method == 'POST':
        emp = (request.form.get('emp_code') or '').strip()
        password = request.form.get('password') or ''
        if not emp or not password:
            flash('Employee code and password are required.', 'error')
        else:
            # Importer stores employee_id uppercased in emp_code/username
            resp = _login_attempt('hod', emp.strip().upper(), password, 'hod.dashboard')
            if resp:
                return resp
    return render_template('hod_login.html')


# --------------------------------------------------------------------------- #
# Dean
# --------------------------------------------------------------------------- #
@bp.route('/dean/login', methods=['GET', 'POST'])
def dean_login():
    user = current_user()
    if user and user['role'] == 'dean':
        return redirect(url_for('dean.dashboard'))
    if request.method == 'POST':
        emp = (request.form.get('emp_code') or '').strip()
        password = request.form.get('password') or ''
        if not emp or not password:
            flash('Employee code and password are required.', 'error')
        else:
            resp = _login_attempt('dean', emp.strip().upper(), password, 'dean.dashboard')
            if resp:
                return resp
    return render_template('dean_login.html')


# --------------------------------------------------------------------------- #
# Logout / password change
# --------------------------------------------------------------------------- #
@bp.route('/logout')
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('auth.landing'))


@bp.route('/change-password', methods=['GET', 'POST'])
@role_required('student', 'teacher', 'admin', 'hod', 'dean')
def change_password():
    user = current_user()
    if request.method == 'POST':
        current = request.form.get('current_password') or ''
        new = request.form.get('new_password') or ''
        confirm = request.form.get('confirm_password') or ''
        row = db.query('SELECT password_hash FROM users WHERE id=?', (user['id'],), one=True)
        if not verify_password(row['password_hash'], current):
            flash('Your current password is incorrect.', 'error')
        elif len(new) < 8:
            flash('New password must be at least 8 characters.', 'error')
        elif new != confirm:
            flash('The new passwords do not match.', 'error')
        elif user['role'] == 'teacher' and new == current_app.config['DEFAULT_TEACHER_PASSWORD']:
            flash('Please choose a new password — not the default one.', 'error')
        else:
            db.execute('UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?',
                       (hash_password(new), user['id']))
            flash('Your password has been updated.', 'success')
            home = _home_for(user['role'])
            return redirect(url_for(home))
    return render_template('change_password.html')
