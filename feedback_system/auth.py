"""Authentication routes for all three roles."""
import hmac
import logging
import os
import re
import secrets
import time

from flask import (Blueprint, current_app, flash, jsonify, make_response, redirect,
                   render_template, request, url_for, session)

from . import authenticator, db, mailer, sso, verification
from .security import (audit, clear_login_failures, current_user, hash_password,
                       is_safe_next, login_locked, login_user, logout_user,
                       register_login_failure, role_required, verify_password,
                       is_default_student_hash, is_default_student_password,
                       student_college_email)

logger = logging.getLogger(__name__)
bp = Blueprint('auth', __name__)


def _safe_next(default_endpoint):
    nxt = request.args.get('next') or request.form.get('next')
    if is_safe_next(nxt):
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
        if role == 'student' and user['must_change_password']:
            # Students are not forced to replace the shared default password (most would
            # forget a new one). Changing it stays available from the Password menu, so
            # an old forced-change flag is cleared at login.
            db.execute('UPDATE users SET must_change_password=0 WHERE id=?', (user['id'],))
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
                    'auth.sso_start', 'auth.sso_callback',
                    'auth.change_password', 'static',
                    'hod.onboarding', 'hod.onboarding_verify',
                    'hod.onboarding_save', 'dean.onboarding',
                    'dean.onboarding_verify', 'dean.onboarding_save',
                    'auth.admin_setup_emails', 'auth.admin_setup_emails_verify',
                    # Phase 2: teacher photo step (+ the image it displays)
                    'auth.setup_photo', 'photos.serve',
                    # Step B: authenticator setup (student, after the e-mail step)
                    'auth.setup_authenticator', 'auth.setup_authenticator_verify',
                    'auth.setup_authenticator_confirm'}


def _home_for(role):
    return {
        'admin': 'admin.dashboard',
        'teacher': 'teacher.dashboard',
        'student': 'student.dashboard',
        'hod': 'hod.dashboard',
        'dean': 'dean.dashboard',
    }.get(role, 'auth.student_login')


def _authenticator_gate(user):
    """Per-role authenticator enforcement. A redirect when the role must enrol now.

    During a role's grace period nothing is blocked: a reminder is shown once per login
    session. See ``authenticator.enforcement_state`` for the configuration.
    """
    # After a lost-device or admin recovery the new authenticator is mandatory for every
    # role, whatever the enforcement config says (including Admin).
    if authenticator.needs_reenrollment(user['id']):
        return redirect(url_for('auth.setup_authenticator'))
    state, until = authenticator.enforcement_state(user['role'], current_app.config)
    if state == 'off' or authenticator.is_active(user['id']):
        return None
    if state == 'required':
        return redirect(url_for('auth.setup_authenticator'))
    if request.method == 'GET' and not session.get('auth_grace_shown'):
        session['auth_grace_shown'] = 1
        days = max(0, (until - authenticator.datetime.utcnow().date()).days)
        flash(f'Please set up an authenticator app. It becomes mandatory on {until:%d %b %Y} '
              f'({days} day{"s" if days != 1 else ""} left). Open Security in the menu.', 'warning')
    return None


@bp.before_app_request
def _enforce_account_setup():
    if request.endpoint in _SETUP_ENDPOINTS or request.endpoint is None:
        return None
    role = session.get('role')
    # Admin must register + OTP-verify two recovery e-mails (once).
    if (session.get('uid') and role == 'admin'
            and current_app.config.get('REQUIRE_ACCOUNT_SETUP', True)
            and _admin_setup_required(session['uid'])):
        return redirect(url_for('auth.admin_setup_emails'))
    if (session.get('uid') and role == 'admin'
            and current_app.config.get('REQUIRE_ACCOUNT_SETUP', True)):
        # Refused while the Admin recovery procedure is not implemented (always 'off').
        admin = current_user()
        if admin:
            gate = _authenticator_gate(admin)
            if gate:
                return gate
    if not session.get('uid') or role not in ('student', 'teacher', 'hod', 'dean'):
        return None
    user = current_user()
    if not user or user['role'] not in ('student', 'teacher', 'hod', 'dean'):
        return None

    # Teachers follow a fixed one-time order (Phase 2):
    #   1. confirm e-mail with a code   2. replace the default password
    #   3. photo step                   4. Teacher Assignment Setup page
    # The photo step is only enforced while account setup is switched on.
    if role == 'teacher':
        setup_on = current_app.config.get('REQUIRE_ACCOUNT_SETUP', True)
        if setup_on and not (user.get('email') or '').strip():
            return redirect(url_for('auth.setup_email', next=request.full_path.rstrip('?')
                                    if request.method == 'GET' else None))
        if user.get('must_change_password'):
            return redirect(url_for('auth.change_password'))
        if setup_on:
            gate = _authenticator_gate(user)
            if gate:
                return gate
        if setup_on and not user.get('first_login_complete'):
            return redirect(url_for('auth.setup_photo'))
        return None

    # Password change is always enforced for any other role that has the flag.
    if user.get('must_change_password'):
        return redirect(url_for('auth.change_password'))

    # Student email setup (existing behaviour, gated by config).
    if current_app.config.get('REQUIRE_ACCOUNT_SETUP', True) and role == 'student':
        if not (user.get('email') or '').strip():
            return redirect(url_for('auth.setup_email', next=request.full_path.rstrip('?')
                                    if request.method == 'GET' else None))

    # Step B: a student must link an authenticator app after the e-mail step
    # (default policy; configurable per role, see authenticator.enforcement_state).
    if role == 'student' and current_app.config.get('REQUIRE_ACCOUNT_SETUP', True):
        gate = _authenticator_gate(user)
        if gate:
            return gate

    # HOD / Dean first-login onboarding (email verify + scope claim).
    if role in ('hod', 'dean'):
        # current_user() already loaded these columns this request: reuse them
        # instead of a second identical users lookup on every HOD/Dean page.
        if not user.get('onboarding_complete'):
            ep = 'hod.onboarding' if role == 'hod' else 'dean.onboarding'
            return redirect(url_for(ep))
        if current_app.config.get('REQUIRE_ACCOUNT_SETUP', True):
            gate = _authenticator_gate(user)
            if gate:
                return gate
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


def _student_email_rule(user):
    """The one address a student may confirm (``<roll>@<college domain>``), or None."""
    if user['role'] != 'student':
        return None
    return student_college_email(user.get('roll') or user.get('username'))


def _grant_enrollment_proofs():
    """A student has just proved the mailbox (Microsoft sign-in or e-mailed code) right after
    typing the password, so the authenticator QR can be shown without asking again.
    Both proofs expire on their own after AUTH_SETUP_PROOF_SECONDS."""
    until = int(time.time()) + AUTH_SETUP_PROOF_SECONDS
    session['auth_setup_until'] = until
    session['auth_pw_until'] = until


@bp.route('/setup/email', methods=['GET', 'POST'])
@role_required('student', 'teacher')
def setup_email():
    user = current_user()
    if (user.get('email') or '').strip() and request.method == 'GET':
        return redirect(url_for(_home_for(user['role'])))
    if request.method == 'POST' and (user.get('email') or '').strip():
        # Step A: a confirmed email is locked. Only an administrator can clear it.
        flash('Your email is already confirmed. Ask an administrator if it must be changed.',
              'error')
    elif request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        college = _student_email_rule(user)
        if not _EMAIL_RE.match(email) or len(email) > 254:
            flash('Please enter a valid email address, e.g. name@gmail.com.'
                  if not college else
                  f'Please enter your college email, e.g. {college}.', 'error')
        elif college and email != college:
            flash(f'Please enter your own college email: {college}.', 'error')
        else:
            ok, problem = _send_code(user['id'], 'verify_email', email)
            if ok:
                flash(f'We sent a 6-digit code to {email}.', 'success')
                return redirect(url_for('auth.verify_email', next=request.form.get('next') or None))
            flash(problem, 'error')
    return render_template('setup_email.html', sso_providers=sso.providers_for(user['role']),
                           college_email=_student_email_rule(user))


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
                # Step A: write only if the email is still empty, so a code sent before
                # the email was confirmed cannot replace it.
                cur = db.execute(
                    "UPDATE users SET email=?, email_verified=1 "
                    "WHERE id=? AND (email IS NULL OR TRIM(email)='')",
                    (email, user['id']))
                if cur.rowcount != 1:
                    flash('Your email is already confirmed. Ask an administrator if it must be changed.',
                          'error')
                    return redirect(url_for('auth.setup_email'))
                audit('user.verify_email', 'users', user['id'], 'Email verified at first login')
                flash('Email confirmed. Thank you!', 'success')
                if user['role'] == 'student':
                    _grant_enrollment_proofs()
                return redirect(_safe_next(_home_for(user['role'])))
            flash('That code is not correct or has expired.', 'error')
            if not verification.pending(user['id'], 'verify_email'):
                return redirect(url_for('auth.setup_email'))
    return render_template('verify_email.html', email=row['email'])


# --------------------------------------------------------------------------- #
# Authenticator enrollment (all five roles)
#
# Proof required before the secret is shown:
#   * every role: re-enter the current password (rate limited, 15 minutes);
#   * students also: a code e-mailed to the confirmed address (existing step B).
# After the first app code is confirmed, 10 backup codes are rendered ONCE in that same
# response. They are never placed in the session, a redirect or a log.
# --------------------------------------------------------------------------- #
AUTH_SETUP_PURPOSE = 'setup_authenticator'
AUTH_SETUP_PROOF_SECONDS = 15 * 60      # time allowed after each proof is given
ENROLL_ROLES = ('student', 'teacher', 'hod', 'dean', 'admin')


def _setup_proof_valid():
    """The student's e-mailed-code proof."""
    return int(session.get('auth_setup_until') or 0) > int(time.time())


def _pw_proof_valid():
    """The password re-entry proof (every role)."""
    return int(session.get('auth_pw_until') or 0) > int(time.time())


def _enrollment_proofs_ok(user):
    return _pw_proof_valid() and (user['role'] != 'student' or _setup_proof_valid())


def _clear_enrollment_proofs():
    session.pop('auth_setup_until', None)
    session.pop('auth_pw_until', None)


def _on_temporary_password(user):
    """Enrollment is refused while an account is flagged to replace a temporary password
    (teachers on the default password, accounts reset by the administrator)."""
    return bool(user.get('must_change_password'))


def _no_store(resp):
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['Pragma'] = 'no-cache'
    return resp


def render_backup_codes(codes, *, reason, user):
    """Render the one-time backup-code page. The codes exist only in this response."""
    resp = make_response(render_template(
        'backup_codes.html', codes=codes, reason=reason,
        continue_url=url_for('account_security.index'), home_url=url_for(_home_for(user['role']))))
    return _no_store(resp)


def _qr_svg(uri):
    """QR code as inline SVG (segno). None if it cannot be drawn.

    The manual setup key is always shown, so a missing library or an unexpected
    rendering error degrades to the key alone instead of breaking the page.
    """
    try:
        import segno
        return segno.make(uri, error='m').svg_inline(scale=4, border=2)
    except Exception:
        logger.warning('QR code could not be rendered; showing the setup key only',
                       exc_info=True)
        return None


def _notify_authenticator(user, action):
    """Best-effort notice to the registered e-mail. A failure is audited, never raised."""
    try:
        sent = mailer.authenticator_notice(user.get('email'), user.get('name'), action)
    except Exception:
        sent = False
    if not sent:
        audit('user.authenticator_notice_not_sent', 'users', user['id'],
              f'Authenticator notice ({action}) was not sent')


def _grouped(secret):
    return ' '.join(secret[i:i + 4] for i in range(0, len(secret), 4))


def _password_proof_key(user_id):
    return f'authsetup:{user_id}'


@bp.route('/setup/authenticator', methods=['GET', 'POST'])
@role_required(*ENROLL_ROLES)
def setup_authenticator():
    user = current_user()
    role = user['role']
    email = (user.get('email') or '').strip()
    if role == 'student' and not email:
        return redirect(url_for('auth.setup_email'))
    if authenticator.is_active(user['id']):
        return redirect(url_for('account_security.index'))
    if _on_temporary_password(user):
        flash('Change your temporary password first, then set up the authenticator app.', 'warning')
        return redirect(url_for('auth.change_password'))
    state, until = authenticator.enforcement_state(role, current_app.config)
    ms_ok = (role == 'student' and 'microsoft' in sso.providers_for('student')
             and email == (_student_email_rule(user) or '').lower())
    ctx = dict(role=role, email=email, microsoft_ok=ms_ok,
               mandatory=(state == 'required' or authenticator.needs_reenrollment(user['id'])),
               home_url=url_for(_home_for(role)))

    action = request.form.get('action') if request.method == 'POST' else None
    if action == 'password':
        # Password re-entry. Wrong attempts share the OTP limits; the password is never echoed.
        key = _password_proof_key(user['id'])
        remaining = login_locked(key)
        if remaining:
            flash(f'Too many incorrect attempts. Try again in {max(1, remaining // 60)} minutes.',
                  'error')
        else:
            row = db.query('SELECT password_hash FROM users WHERE id=?', (user['id'],), one=True)
            if verify_password(row['password_hash'], request.form.get('password') or ''):
                clear_login_failures(key)
                session['auth_pw_until'] = int(time.time()) + AUTH_SETUP_PROOF_SECONDS
                return redirect(url_for('auth.setup_authenticator'))
            register_login_failure(key, int(current_app.config.get('OTP_MAX_ATTEMPTS', 5)),
                                   int(current_app.config.get('OTP_LOCKOUT_SECONDS', 900)))
            flash('That password is not correct.', 'error')
    if not _pw_proof_valid():
        return render_template('setup_authenticator.html', stage='password', **ctx)

    if role == 'student':
        if action == 'send':
            ok, problem = _send_code(user['id'], AUTH_SETUP_PURPOSE, email)
            if ok:
                flash(f'We sent a 6-digit code to {email}.', 'success')
                return redirect(url_for('auth.setup_authenticator_verify'))
            flash(problem, 'error')
        if not _setup_proof_valid():
            return render_template('setup_authenticator.html', stage='send', **ctx)

    try:
        secret = authenticator.pending_secret(user['id']) or authenticator.begin_enrollment(user['id'])
    except authenticator.AuthenticatorConfigError:
        flash('Authenticator setup is not available yet. Please contact the administrator.', 'error')
        return render_template('setup_authenticator.html', stage='unavailable', **ctx)
    uri = authenticator.provisioning_uri(secret, account=user['username'])
    return render_template('setup_authenticator.html', stage='scan', uri=uri, qr=_qr_svg(uri),
                           secret_display=_grouped(secret), **ctx)


@bp.route('/setup/authenticator/verify', methods=['GET', 'POST'])
@role_required('student')
def setup_authenticator_verify():
    user = current_user()
    email = (user.get('email') or '').strip()
    if not _pw_proof_valid():
        return redirect(url_for('auth.setup_authenticator'))
    row = verification.pending(user['id'], AUTH_SETUP_PURPOSE)
    if not email or not row:
        flash('That code has expired. Please ask for a new one.', 'warning')
        return redirect(url_for('auth.setup_authenticator'))
    if request.method == 'POST':
        if request.form.get('action') == 'resend':
            ok, problem = _send_code(user['id'], AUTH_SETUP_PURPOSE, email)
            flash('A new code has been sent.' if ok else problem, 'success' if ok else 'error')
        else:
            confirmed = verification.check(user['id'], AUTH_SETUP_PURPOSE, request.form.get('code'))
            if confirmed:
                # Proof of e-mail control for a short time only. Stored in the signed session;
                # the TOTP secret itself never goes in the session.
                session['auth_setup_until'] = int(time.time()) + AUTH_SETUP_PROOF_SECONDS
                return redirect(url_for('auth.setup_authenticator'))
            flash('That code is not correct or has expired.', 'error')
            if not verification.pending(user['id'], AUTH_SETUP_PURPOSE):
                return redirect(url_for('auth.setup_authenticator'))
    return render_template('setup_authenticator_verify.html', email=email)


@bp.route('/setup/authenticator/confirm', methods=['POST'])
@role_required(*ENROLL_ROLES)
def setup_authenticator_confirm():
    user = current_user()
    if not _enrollment_proofs_ok(user) or _on_temporary_password(user):
        flash('Please confirm your identity again to continue the authenticator setup.', 'error')
        return redirect(url_for('auth.setup_authenticator'))
    code = (request.form.get('code') or '').strip()
    try:
        ok = authenticator.confirm_enrollment(user['id'], code)
    except authenticator.AuthenticatorConfigError:
        flash('Authenticator setup is not available yet. Please contact the administrator.', 'error')
        return redirect(url_for('auth.setup_authenticator'))
    if ok:
        _clear_enrollment_proofs()
        audit('user.authenticator_enrolled', 'users', user['id'], 'Authenticator app set up')
        _notify_authenticator(user, 'added')
        try:
            codes = authenticator.regenerate_backup_codes(user['id'])
        except Exception:
            logger.warning('Backup codes could not be created after enrollment', exc_info=True)
            flash('Your authenticator app is set up, but the backup codes could not be created. '
                  'Create them from the Security page.', 'warning')
            return redirect(url_for('account_security.index'))
        audit('user.backup_codes_generated', 'users', user['id'], 'Backup codes created at setup')
        return render_backup_codes(codes, reason='enrolled', user=user)
    locked = authenticator.otp_locked_seconds(user['id'])
    if locked:
        flash(f'Too many incorrect codes. Try again in {max(1, locked // 60)} minutes.', 'error')
    else:
        flash('That code is not correct. Check the app and try again.', 'error')
    return redirect(url_for('auth.setup_authenticator'))


# --------------------------------------------------------------------------- #
# Google / Microsoft sign-in: verifies the e-mail without sending a code
# --------------------------------------------------------------------------- #
def _sso_redirect_uri(provider):
    return url_for('auth.sso_callback', provider=provider, _external=True)


def _store_sso_email(user, email, provider):
    """Save a provider-verified e-mail on the user. Returns an error message or None."""
    current = (user.get('email') or '').strip().lower()
    if current and current != email:
        return ('This account already has a different email on file. '
                'Ask the administrator to update it.')
    taken = db.query('SELECT id FROM users WHERE LOWER(email)=? AND id<>? LIMIT 1',
                     (email, user['id']), one=True)
    if taken:
        return ('That email address is already linked to another account. '
                'Please contact the administrator.')
    db.execute('UPDATE users SET email=?, email_verified=1 WHERE id=?', (email, user['id']))
    verification.discard(user['id'], 'verify_email')
    audit('user.verify_email', 'users', user['id'], f'Email verified via {provider} sign-in')
    return None


@bp.route('/sso/<provider>/start')
@role_required('student', 'teacher')
def sso_start(provider):
    user = current_user()
    if (provider not in sso.PROVIDERS or not sso.enabled(provider)
            or provider not in sso.providers_for(user['role'])):
        flash('That sign-in option is not available. You can use the email code instead.', 'error')
        return redirect(url_for('auth.setup_email'))
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    session['sso'] = {'provider': provider, 'state': state, 'nonce': nonce, 'uid': user['id']}
    return redirect(sso.authorize_url(provider, _sso_redirect_uri(provider), state, nonce))


@bp.route('/sso/<provider>/callback')
@role_required('student', 'teacher')
def sso_callback(provider):
    pending = session.pop('sso', None) or {}
    user = current_user()
    if (provider not in sso.PROVIDERS or pending.get('provider') != provider
            or pending.get('uid') != user['id']
            or not hmac.compare_digest(pending.get('state', ''), request.args.get('state', ''))):
        flash('That sign-in link has expired. Please try again.', 'error')
        return redirect(url_for('auth.setup_email'))
    if request.args.get('error'):
        flash('Sign-in was cancelled. You can try again or use the email code.', 'warning')
        return redirect(url_for('auth.setup_email'))
    code = request.args.get('code') or ''
    if not code:
        flash('Sign-in did not complete. Please try again.', 'error')
        return redirect(url_for('auth.setup_email'))
    try:
        claims = sso.fetch_claims(provider, code, _sso_redirect_uri(provider))
        email = sso.verified_email(provider, claims, pending['nonce'],
                                   sso.client_credentials(provider)[0])
    except sso.SSOError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('auth.setup_email'))
    college = _student_email_rule(user)
    if college and email != college:
        flash(f'Please sign in with your own college Microsoft account: {college}.', 'error')
        return redirect(url_for('auth.setup_email'))
    problem = _store_sso_email(user, email, provider)
    if problem:
        flash(problem, 'error')
        return redirect(url_for('auth.setup_email'))
    flash(f'Email confirmed with {provider.title()}: {email}', 'success')
    if user['role'] == 'student':
        _grant_enrollment_proofs()
    return redirect(url_for(_home_for(user['role'])))


# --------------------------------------------------------------------------- #
# Teacher first-login photo step (Phase 2)
# --------------------------------------------------------------------------- #
@bp.route('/setup/photo', methods=['GET', 'POST'])
@role_required('teacher')
def setup_photo():
    """Last onboarding step: show the current photo, or ask for one.

    * photo on file  -> preview card, "Continue" (can also replace it)
    * no photo       -> upload prompt with a live preview; "Skip for now" is
                        allowed because every screen falls back to an initials
                        avatar, and the photo can be added later.
    Finishing (continue / skip / saved upload) sets ``first_login_complete``
    and sends the teacher to the Teacher Assignment Setup page.
    """
    from . import photos
    user = current_user()
    if user.get('first_login_complete'):
        return redirect(url_for('teacher.assignment_setup'))
    # Steps cannot be skipped: send them to whichever earlier step is still open.
    if not (user.get('email') or '').strip():
        return redirect(url_for('auth.setup_email'))
    if user.get('must_change_password'):
        return redirect(url_for('auth.change_password'))
    code = photos.normalize_emp(user.get('emp_code'))
    if request.method == 'POST':
        action = request.form.get('action') or 'upload'
        if action in ('continue', 'skip'):
            if action == 'continue' and code and not photos.has_any_photo(code):
                flash('Please upload a photo, or choose “Skip for now”.', 'warning')
            else:
                return _finish_first_login(user, skipped=(action == 'skip'))
        else:
            upload = request.files.get('photo')
            raw = upload.read(photos.MAX_UPLOAD_BYTES + 1) if upload and upload.filename else b''
            if not code:
                flash('Your account has no employee code, so a photo cannot be attached.', 'error')
            else:
                ok, msg = photos.save_photo(code, raw, user['id'])
                if ok:
                    audit('photo.upload', 'employee', code, f'by user {user["id"]} (onboarding)')
                    return _finish_first_login(user, skipped=False)
                flash(msg, 'error')
    meta = photos.photo_meta(code) if code else None
    return render_template(
        'setup_photo.html', emp_code=code, designation=user.get('designation'),
        has_photo=bool(code and photos.has_any_photo(code)), meta=meta,
        max_kb=photos.MAX_UPLOAD_BYTES // 1024)


def _finish_first_login(user, skipped):
    db.execute('UPDATE users SET first_login_complete=1 WHERE id=?', (user['id'],))
    audit('teacher.first_login_complete', 'users', user['id'],
          'Photo step skipped' if skipped else 'Photo step done')
    flash('Setup complete. Now review your subjects, rooms and labs.', 'success')
    return redirect(url_for('teacher.assignment_setup'))


# --------------------------------------------------------------------------- #
# Admin: two OTP-verified recovery e-mails + two-e-mail forgot-password
# --------------------------------------------------------------------------- #
def _mail_can_work():
    """Do not lock the admin out when the server has no mail account at all."""
    return mailer.is_configured() or not os.environ.get('VERCEL')


def _admin_setup_required(uid):
    if not _mail_can_work():
        return False
    row = db.query('SELECT recovery_email1, recovery_email2 FROM users WHERE id=?',
                   (uid,), one=True)
    return not (row and (row['recovery_email1'] or '').strip()
                and (row['recovery_email2'] or '').strip())


def _admin_pairs(user):
    u = dict(user)
    return [e for e in ((u.get('recovery_email1') or '').strip().lower(),
                        (u.get('recovery_email2') or '').strip().lower()) if e]


@bp.route('/admin/setup-emails', methods=['GET', 'POST'])
@role_required('admin')
def admin_setup_emails():
    """Step 1: the admin enters two different e-mails; a code goes to each."""
    user = current_user()
    if request.method == 'POST':
        e1 = (request.form.get('email1') or '').strip().lower()
        e2 = (request.form.get('email2') or '').strip().lower()
        confirm1 = (request.form.get('confirm1') or '').strip().lower()
        confirm2 = (request.form.get('confirm2') or '').strip().lower()
        if not all(_EMAIL_RE.match(e) and len(e) <= 254 for e in (e1, e2)):
            flash('Please enter two valid email addresses.', 'error')
        elif e1 == e2:
            flash('The two email addresses must be different.', 'error')
        elif e1 != confirm1 or e2 != confirm2:
            flash('Each email must match its confirmation box.', 'error')
        else:
            ok1, p1 = _send_code(user['id'], 'admin_email_1', e1)
            ok2, p2 = _send_code(user['id'], 'admin_email_2', e2)
            if ok1 and ok2:
                session['adm_setup'] = {'e1': e1, 'e2': e2, 'ok1': False, 'ok2': False}
                flash('We sent a 6-digit code to each email address.', 'success')
                return redirect(url_for('auth.admin_setup_emails_verify'))
            flash(p1 or p2, 'error')
    current = _admin_pairs(user)
    return render_template('admin_setup_emails.html', current=current)


@bp.route('/admin/setup-emails/verify', methods=['GET', 'POST'])
@role_required('admin')
def admin_setup_emails_verify():
    """Step 2: enter the code received on EACH address."""
    user = current_user()
    st = session.get('adm_setup')
    if not st:
        return redirect(url_for('auth.admin_setup_emails'))
    if request.method == 'POST':
        action = request.form.get('action') or ''
        if action in ('resend1', 'resend2'):
            i = action[-1]
            ok, problem = _send_code(user['id'], f'admin_email_{i}', st['e' + i])
            flash('A new code has been sent.' if ok else problem, 'success' if ok else 'error')
        else:
            for i in ('1', '2'):
                if st['ok' + i]:
                    continue
                code = (request.form.get('code' + i) or '').strip()
                if not code:
                    continue
                if verification.check(user['id'], f'admin_email_{i}', code):
                    st['ok' + i] = True
                else:
                    flash(f'The code for email {i} is not correct or has expired.', 'error')
            session['adm_setup'] = st
            if st['ok1'] and st['ok2']:
                db.execute('UPDATE users SET recovery_email1=?, recovery_email2=? WHERE id=?',
                           (st['e1'], st['e2'], user['id']))
                audit('admin.recovery_emails', 'users', user['id'],
                      'Admin verified two recovery emails')
                session.pop('adm_setup', None)
                flash('Both email addresses are verified. Password recovery is now enabled.',
                      'success')
                return redirect(url_for('admin.dashboard'))
    return render_template('admin_setup_emails_verify.html', st=st)


@bp.route('/admin/forgot-password', methods=['GET', 'POST'])
def admin_forgot_password():
    """Admin gives BOTH registered e-mails; a code goes to one of them (random)."""
    if request.method == 'POST':
        key = f'adminreset:{request.remote_addr}'
        remaining = login_locked(key)
        if remaining:
            flash(f'Too many attempts. Please try again in {remaining} seconds.', 'error')
            return render_template('admin_forgot_password.html')
        e1 = (request.form.get('email1') or '').strip().lower()
        e2 = (request.form.get('email2') or '').strip().lower()
        if not (_EMAIL_RE.match(e1) and _EMAIL_RE.match(e2)) or e1 == e2:
            flash('Enter your two different registered email addresses.', 'error')
            return render_template('admin_forgot_password.html')
        target_user = None
        for u in db.query("SELECT * FROM users WHERE role='admin' AND is_active=1"):
            if set(_admin_pairs(u)) == {e1, e2} and len(_admin_pairs(u)) == 2:
                target_user = u
                break
        if target_user:
            send_to = secrets.choice(_admin_pairs(target_user))   # either one, at random
            ok, problem = _send_code(target_user['id'], 'reset_password', send_to)
            if not ok and 'wait' in problem:
                flash(problem, 'error')
                return render_template('admin_forgot_password.html')
            clear_login_failures(key)
            session['reset_who'] = ['admin', target_user['username']]
        else:
            register_login_failure(key, current_app.config['LOGIN_MAX_ATTEMPTS'],
                                   current_app.config['LOGIN_LOCKOUT_SECONDS'])
            session['reset_who'] = ['admin', e1]
        # Same answer whether or not the two emails matched (no probing).
        flash('If both emails match the registered ones, a 6-digit code has been sent to '
              'one of them. Check both inboxes.', 'info')
        return redirect(url_for('auth.reset_password'))
    return render_template('admin_forgot_password.html')


# --------------------------------------------------------------------------- #
# Forgot password (code sent to the verified e-mail on file)
# --------------------------------------------------------------------------- #
_RESET_ROLES = {'student': 'Roll number', 'teacher': 'Employee code',
                'hod': 'Employee code', 'dean': 'Employee code'}
_RESET_LOGIN = {'admin': 'auth.admin_login', 'teacher': 'auth.teacher_login',
                'hod': 'auth.hod_login', 'dean': 'auth.dean_login'}
_RESET_INFO = ('Enter the 6-digit code from your authenticator app (or one of your backup codes). '
               'If you have not set up an authenticator app yet, a 6-digit code was emailed to '
               'your confirmed email instead.')


def _normalise_ident(role, ident):
    ident = (ident or '').strip()
    return ident.lower() if role == 'admin' else ident.upper() if role == 'student' else ident


@bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        role = request.form.get('role') or 'student'
        if role == 'admin':
            return redirect(url_for('auth.admin_forgot_password'))
        ident = _normalise_ident(role, request.form.get('ident'))
        if role not in _RESET_ROLES or not ident:
            flash('Please enter your ID.', 'error')
        else:
            user = _existing_user(ident, role)
            email = ((user['email'] if user else '') or '').strip()
            # A user with an active authenticator proves themselves with an app code or a
            # backup code, so no email is sent (saves mail cost). Everyone else keeps the
            # emailed-code reset. The browser is told the same thing either way.
            if user and email and not authenticator.is_active(user['id']):
                ok, problem = _send_code(user['id'], 'reset_password', email)
                if not ok and 'wait' in problem:
                    flash(problem, 'error')
                    return render_template('forgot_password.html', roles=_RESET_ROLES,
                                           role=role, ident=ident)
            session['reset_who'] = [role, ident]
            # Same answer whether or not the account exists (no user enumeration).
            flash(_RESET_INFO, 'info')
            return redirect(url_for('auth.reset_password'))
    if request.args.get('role') == 'admin':
        return redirect(url_for('auth.admin_forgot_password'))
    return render_template('forgot_password.html', roles=_RESET_ROLES,
                           role=request.args.get('role', 'student'), ident='')


def _check_reset_code(user, role, code):
    """Check the code for a forgotten-password reset. Returns 'totp', 'backup', 'email' or None.

    An account with an active authenticator is checked against an app code or an unused
    backup code (rate limited, never removes the authenticator). Any other account uses the
    emailed code. The admin keeps the emailed-code flow (separate two-email recovery).
    """
    if not user:
        return None
    if role != 'admin' and authenticator.is_active(user['id']):
        try:
            ok, reason = authenticator.verify_recovery_code(user['id'], code)
        except authenticator.AuthenticatorConfigError:
            logger.error('Authenticator secret could not be decrypted for user %s', user['id'])
            return None
        return reason if ok else None
    return 'email' if verification.check(user['id'], 'reset_password', code) else None


@bp.route('/forgot-password/reset', methods=['GET', 'POST'])
def reset_password():
    who = session.get('reset_who')
    if not who:
        return redirect(url_for('auth.forgot_password'))
    role, ident = who
    user = _existing_user(ident, role)
    if request.method == 'POST':
        if request.form.get('action') == 'resend':
            if role == 'admin':
                pair = _admin_pairs(user) if user else []
                email = secrets.choice(pair) if len(pair) == 2 else ''
            else:
                email = ((user['email'] if user else '') or '').strip()
            if user and email and (role == 'admin' or not authenticator.is_active(user['id'])):
                ok, problem = _send_code(user['id'], 'reset_password', email)
                flash('A new code has been sent.' if ok else problem, 'success' if ok else 'error')
            else:
                flash('If your account uses an emailed code, a new one has been sent. '
                      'Authenticator codes come from your app.', 'info')
        else:
            new = request.form.get('new_password') or ''
            confirm = request.form.get('confirm_password') or ''
            if len(new) < 8:
                flash('New password must be at least 8 characters.', 'error')
            elif new != confirm:
                flash('The new passwords do not match.', 'error')
            elif role == 'teacher' and new == current_app.config['DEFAULT_TEACHER_PASSWORD']:
                flash('Please choose a new password — not the default one.', 'error')
            elif role == 'student' and is_default_student_password(new):
                flash('Please choose a new password — not the default one.', 'error')
            else:
                how = _check_reset_code(user, role, request.form.get('code'))
                if not how:
                    # One message for every failure (wrong, used, expired, locked, unknown ID).
                    flash('That code is not correct or has expired.', 'error')
                else:
                    db.execute('UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?',
                               (hash_password(new), user['id']))
                    audit('user.forgot_password_reset', 'users', user['id'],
                          {'email': 'Password reset via email code',
                           'totp': 'Password reset via authenticator app code',
                           'backup': 'Password reset via backup code'}[how])
                    session.pop('reset_who', None)
                    flash('Your password has been reset. Please log in.', 'success')
                    return redirect(url_for(_RESET_LOGIN.get(role, 'auth.student_login')))
    return render_template('reset_password.html', role=role,
                           ident='the administrator account' if role == 'admin' else ident)


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

    return render_template('landing.html', stats=_landing_stats())


def _landing_stats():
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
    return stats


def _render_landing_login(role):
    return render_template('landing.html', stats=_landing_stats(),
                           login_role=role, login_values=request.form)


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
        if request.form.get('login_context') == 'landing':
            return _render_landing_login('student')
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
            and not is_default_student_hash(user['password_hash'])):
        # Personal password set: scanning only fills the roll number.
        return jsonify(ok=False, fallback=True, roll=user['username'])
    clear_login_failures(key)
    if user['must_change_password']:
        # Same rule as password login (see _login_attempt): students are not forced.
        db.execute('UPDATE users SET must_change_password=0 WHERE id=?', (user['id'],))
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
        if request.form.get('login_context') == 'landing':
            return _render_landing_login('admin')
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
        if request.form.get('login_context') == 'landing':
            return _render_landing_login('teacher')
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
        if request.form.get('login_context') == 'landing':
            return _render_landing_login('hod')
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
        if request.form.get('login_context') == 'landing':
            return _render_landing_login('dean')
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
        from_profile = request.form.get('from') == 'profile' and user['role'] in (
            'teacher', 'hod', 'dean')
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
        elif user['role'] == 'student' and is_default_student_password(new):
            flash('Please choose a new password — not the default one.', 'error')
        else:
            db.execute('UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?',
                       (hash_password(new), user['id']))
            flash('Your password has been updated.', 'success')
            if from_profile:
                return redirect(url_for('photos.profile'))
            home = _home_for(user['role'])
            return redirect(url_for(home))
        if from_profile:
            return redirect(url_for('photos.profile'))
    return render_template('change_password.html')
