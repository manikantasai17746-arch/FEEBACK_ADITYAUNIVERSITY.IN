"""Lost-device recovery (Checkpoint 3). Separate from normal password reset.

* ``/lost-phone``  Student, Teacher, HOD, Dean: ID + current password + ONE unused backup
  code, in a single request (no session state to tamper with).
* ``/lost-phone/no-codes``  Student, Teacher, HOD, Dean with NO phone and NO backup codes
  (Checkpoint B2): an e-mailed 6-digit code to the confirmed address, a new password, then
  the same atomic removal. The 2-hour recovery lock (``recovery_lock``) covers this page and
  the backup-code page above.
* ``/lost-phone/sso/<provider>/start|callback`` + ``/lost-phone/sso/finish``  (Checkpoint B3)
  the same recovery, but the mailbox is proved with a Microsoft (college tenant) or Google
  sign-in instead of an e-mailed code (free, no mail cost). Works before login.
* ``/admin/lost-authenticator``  Admin: both registered recovery e-mails, a code to each,
  and both codes must be right in one submission.

A successful recovery removes the old authenticator and every remaining backup code in one
atomic step and leaves a "re-enrollment required" marker, so the next sign-in goes
straight to authenticator setup. Nothing is removed on any failure. Every failure shows the
same message so the pages cannot be used to find out which accounts exist or which have an
authenticator. Normal password reset (``auth.forgot_password`` and friends) is untouched.
"""
import hmac
import logging
import secrets
import time

from flask import (Blueprint, current_app, flash, redirect, render_template, request, session,
                   url_for)

from . import authenticator, db, mailer, recovery_lock, sso, verification
from .security import (audit, clear_login_failures, hash_password, is_default_student_password,
                       reserve_login_attempt, verify_password)

logger = logging.getLogger(__name__)
bp = Blueprint('lost_device', __name__)

ROLES = {'student': 'Roll number', 'teacher': 'Employee code', 'hod': 'Employee ID',
         'dean': 'Employee ID'}
_LOGIN = {'student': 'auth.student_login', 'teacher': 'auth.teacher_login',
          'hod': 'auth.hod_login', 'dean': 'auth.dean_login'}
GENERIC = 'We could not verify those details. Check them and try again.'
ADMIN_STATE_SECONDS = 10 * 60
_DUMMY_HASH = None
_SYSTEM = {'id': None, 'name': 'system', 'username': 'system'}


def _dummy_hash():
    """A real hash to compare against when the account does not exist, so an unknown ID
    takes the same time as a known one."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password('not-a-real-password')
    return _DUMMY_HASH


def _ip_allowed(prefix):
    """Per-IP limit, counted for every request before any work is done."""
    return reserve_login_attempt(
        f'{prefix}:{request.remote_addr}',
        int(current_app.config.get('LOST_DEVICE_IP_MAX_ATTEMPTS', 10)),
        int(current_app.config.get('OTP_LOCKOUT_SECONDS', 900)),
        window_seconds=authenticator.FAILURE_WINDOW_SECONDS)


def _notify(addresses, name, event, uid):
    """Best-effort security notice to each distinct address. Failures are audited."""
    seen = set()
    for to in addresses:
        to = (to or '').strip()
        if not to or to.lower() in seen:
            continue
        seen.add(to.lower())
        try:
            sent = mailer.security_notice(to, name, event)
        except Exception:
            sent = False
        if not sent:
            audit('user.security_notice_not_sent', 'users', uid,
                  f'Notice ({event}) was not sent to one address', actor=_SYSTEM)


# --------------------------------------------------------------------------- #
# Student / Teacher / HOD / Dean
# --------------------------------------------------------------------------- #
@bp.route('/lost-phone', methods=['GET', 'POST'])
def lost_phone():
    from .auth import _existing_user, _normalise_ident
    role = (request.form.get('role') or request.args.get('role') or 'student')
    role = role if role in ROLES else 'student'
    if request.method != 'POST':
        return render_template('lost_phone.html', roles=ROLES, role=role, ident='')
    ident = _normalise_ident(role, request.form.get('ident'))
    password = request.form.get('password') or ''
    code = request.form.get('code') or ''

    def fail(message=GENERIC):
        flash(message, 'error')
        return render_template('lost_phone.html', roles=ROLES, role=role, ident=ident)

    if not _ip_allowed('lostphone-ip'):
        audit('user.lost_device_recovery_failed', None, None,
              'Blocked: too many requests from one address', actor=_SYSTEM)
        return fail('Too many attempts. Please try again later.')
    user = _existing_user(ident, role) if ident else None
    # Always one password check, even for an unknown account (same cost either way).
    password_ok = verify_password(user['password_hash'] if user else _dummy_hash(), password)
    if not user or not authenticator.is_active(user['id']):
        audit('user.lost_device_recovery_failed', 'users', user['id'] if user else None,
              'Failed: unknown account or no authenticator', actor=_SYSTEM)
        return fail()
    uid = user['id']
    if not authenticator.reserve_lost_attempt(uid):
        audit('user.lost_device_recovery_failed', 'users', uid, 'Blocked: account locked',
              actor=_SYSTEM)
        return fail()
    if not password_ok:
        audit('user.lost_device_recovery_failed', 'users', uid, 'Failed: wrong password',
              actor=_SYSTEM)
        return fail()
    # Checkpoint B: code guesses are limited per IP, and 5 wrong codes lock recovery for
    # 2 hours (sign-in is never affected).
    if not recovery_lock.ip_code_allowed():
        audit('user.lost_device_recovery_failed', 'users', uid,
              'Blocked: too many code guesses from one address', actor=_SYSTEM)
        return fail('Too many attempts. Please try again later.')
    if recovery_lock.begin_attempt(user) == 'locked':
        audit('user.lost_device_recovery_failed', 'users', uid,
              'Blocked: recovery is locked', actor=_SYSTEM)
        return fail()
    try:
        ok, reason = authenticator.recover_lost_device(uid, code, reserve=False)
    except authenticator.AuthenticatorConfigError:
        logger.error('Lost-device recovery failed: the encryption key is not usable')
        return fail('Recovery is not available right now. Please contact the administrator.')
    if not ok:
        recovery_lock.record_failure(user)
        audit('user.lost_device_recovery_failed', 'users', uid, f'Failed: {reason}',
              actor=_SYSTEM)
        return fail()
    recovery_lock.record_success(user)
    clear_login_failures(f'lostphone-ip:{request.remote_addr}')
    audit('user.lost_device_recovery', 'users', uid,
          'Lost-device recovery: authenticator and backup codes removed; new setup required',
          actor=user)
    _notify([user['email']], user['name'], 'lost_device_reset', uid)
    flash('Recovery complete. Your old authenticator and backup codes were removed. '
          'Sign in with your password and set up a new authenticator app.', 'success')
    return redirect(url_for(_LOGIN[user['role']]))


# --------------------------------------------------------------------------- #
# Student / Teacher / HOD / Dean with no phone and no backup codes (Checkpoint B2)
# --------------------------------------------------------------------------- #
MAILBOX_PURPOSE = 'lost_device'
MAILBOX_STATE_SECONDS = 10 * 60
CODE_FAIL = 'That code is not correct or has expired.'
MAILBOX_INFO = ('If that account has an authenticator app and a confirmed email, we sent a '
                '6-digit code to it. Enter the code and choose a new password.')


def _mailbox_state():
    """The signed-session record of who is recovering (role + ID only), or None."""
    st = session.get('recov')
    if (not isinstance(st, dict) or st.get('role') not in ROLES
            or not isinstance(st.get('ident'), str) or not isinstance(st.get('exp'), int)
            or st['exp'] <= int(time.time())):
        session.pop('recov', None)
        return None
    return st


def _recovery_user(role, ident):
    """The account if it exists AND has an active authenticator (else None)."""
    from .auth import _existing_user
    user = _existing_user(ident, role) if ident else None
    return dict(user) if user and authenticator.is_active(user['id']) else None


def _send_mailbox_code(user):
    """E-mail a recovery code if every limit allows it. Silent otherwise (the browser is
    told the same thing either way). Returns True if a code was sent."""
    from .auth import _send_code
    email = (user.get('email') or '').strip()
    if not email or recovery_lock.locked_seconds(user) or not recovery_lock.mail_allowed(user):
        return False
    ok, _problem = _send_code(user['id'], MAILBOX_PURPOSE, email)
    if ok:
        audit('user.lost_device_mailbox_requested', 'users', user['id'],
              'Recovery code e-mailed (no phone, no backup codes)', actor=_SYSTEM)
    return ok


def _providers():
    """[(provider, roles it is offered to)] for the start page buttons."""
    out = []
    for p in sso.PROVIDERS:
        roles = [r for r in ROLES if p in sso.recovery_providers_for(r)]
        if roles:
            out.append((p, ','.join(roles)))
    return sorted(out, key=lambda x: x[0] != sso.MICROSOFT)


def _password_problem(role, new, confirm):
    """The first problem with a new password (same rules everywhere), or None."""
    if len(new) < 8:
        return 'New password must be at least 8 characters.'
    if new != confirm:
        return 'The new passwords do not match.'
    if ((role == 'teacher' and new == current_app.config['DEFAULT_TEACHER_PASSWORD'])
            or (role == 'student' and is_default_student_password(new))):
        return 'Please choose a new password, not the default one.'
    return None


def _finish_recovery(user, new, method):
    """The shared last step once a mailbox is proved (e-mailed code or Microsoft/Google).

    Removes the authenticator and all backup codes atomically, changes the password, e-mails
    a notice and writes the audit entry. Returns None on success, else a user-facing message
    (nothing was changed in that case).
    """
    uid = user['id']
    try:
        removed = authenticator.apply_lost_device_reset(uid)
    except authenticator.AuthenticatorConfigError:
        logger.error('Lost-device recovery failed: the encryption key is not usable')
        return 'Recovery is not available right now. Please contact the administrator.'
    if not removed:
        return CODE_FAIL
    db.execute('UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?',
               (hash_password(new), uid))
    for key in ('recov', 'recov_sso', 'recov_proof'):
        session.pop(key, None)
    audit('user.lost_device_recovery', 'users', uid,
          f'Lost-device recovery by {method}: authenticator and backup codes removed, '
          'password changed; new setup required', actor=user)
    event = 'lost_device_reset_mailbox' if method == 'e-mailed code' else 'lost_device_reset_signin'
    _notify([user['email']], user['name'], event, uid)
    return None


RECOVERY_DONE = ('Recovery complete. Sign in with your new password, then set up a new '
                 'authenticator app and enter its code to confirm it. You will get new backup '
                 'codes.')


@bp.route('/lost-phone/no-codes', methods=['GET', 'POST'])
def lost_phone_no_codes():
    from .auth import _normalise_ident
    role = (request.form.get('role') or request.args.get('role') or 'student')
    role = role if role in ROLES else 'student'
    if request.method != 'POST':
        return render_template('lost_phone_mailbox.html', roles=ROLES, role=role, ident='',
                               providers=_providers())
    ident = _normalise_ident(role, request.form.get('ident'))
    if not ident:
        flash('Please enter your ID.', 'error')
        return render_template('lost_phone_mailbox.html', roles=ROLES, role=role, ident='',
                               providers=_providers())
    if not recovery_lock.ip_start_allowed():
        audit('user.lost_device_recovery_failed', None, None,
              'Blocked: too many recovery starts from one address', actor=_SYSTEM)
        flash('Too many attempts. Please try again later.', 'error')
        return render_template('lost_phone_mailbox.html', roles=ROLES, role=role, ident=ident,
                               providers=_providers())
    user = _recovery_user(role, ident)
    if user:
        _send_mailbox_code(user)
    # The same answer for an unknown ID, an account with no authenticator, a locked
    # account and a real one.
    session['recov'] = {'role': role, 'ident': ident,
                        'exp': int(time.time()) + MAILBOX_STATE_SECONDS}
    flash(MAILBOX_INFO, 'info')
    return redirect(url_for('lost_device.lost_phone_no_codes_verify'))


@bp.route('/lost-phone/no-codes/verify', methods=['GET', 'POST'])
def lost_phone_no_codes_verify():
    st = _mailbox_state()
    if not st:
        flash('That page has expired. Please start again.', 'warning')
        return redirect(url_for('lost_device.lost_phone_no_codes'))
    role, ident = st['role'], st['ident']

    def page():
        return render_template('lost_phone_mailbox_verify.html', role=role, ident=ident)

    def fail(message=CODE_FAIL):
        flash(message, 'error')
        return page()

    if request.method != 'POST':
        return page()
    if request.form.get('action') == 'resend':
        user = _recovery_user(role, ident)
        if user and recovery_lock.ip_start_allowed():
            _send_mailbox_code(user)
        flash('If a code can be sent, a new one is on its way. You can ask once a minute.',
              'info')
        return page()

    new = request.form.get('new_password') or ''
    confirm = request.form.get('confirm_password') or ''
    # Password rules are checked first and do not count as a wrong code.
    problem = _password_problem(role, new, confirm)
    if problem:
        return fail(problem)

    if not recovery_lock.ip_code_allowed():
        audit('user.lost_device_recovery_failed', None, None,
              'Blocked: too many code guesses from one address', actor=_SYSTEM)
        return fail('Too many attempts. Please try again later.')
    user = _recovery_user(role, ident)
    if not user:
        return fail()
    uid = user['id']
    if recovery_lock.begin_attempt(user) == 'locked':
        audit('user.lost_device_recovery_failed', 'users', uid, 'Blocked: recovery is locked',
              actor=_SYSTEM)
        return fail()
    if not verification.check(uid, MAILBOX_PURPOSE, request.form.get('code')):
        recovery_lock.record_failure(user)
        audit('user.lost_device_recovery_failed', 'users', uid, 'Failed: wrong e-mailed code',
              actor=_SYSTEM)
        return fail()
    recovery_lock.record_success(user)
    problem = _finish_recovery(user, new, 'e-mailed code')
    if problem:
        return fail(problem)
    flash(RECOVERY_DONE, 'success')
    return redirect(url_for(_LOGIN[role]))


# --------------------------------------------------------------------------- #
# The same recovery, proved with a Microsoft / Google sign-in (Checkpoint B3)
# --------------------------------------------------------------------------- #
SSO_STATE_SECONDS = 10 * 60
SSO_FAIL = 'We could not confirm that sign-in matches your account. Please try again or use the emailed code.'


def _sso_redirect_uri(provider):
    return url_for('lost_device.lost_phone_sso_callback', provider=provider, _external=True)


def _sso_proof():
    """The signed-session proof that a sign-in matched the account, or None (expired/bad)."""
    p = session.get('recov_proof')
    if (not isinstance(p, dict) or not isinstance(p.get('uid'), int)
            or p.get('role') not in ROLES or not isinstance(p.get('exp'), int)
            or p['exp'] <= int(time.time())):
        session.pop('recov_proof', None)
        return None
    return p


def _back_to_start(message, category='error'):
    flash(message, category)
    return redirect(url_for('lost_device.lost_phone_no_codes'))


@bp.route('/lost-phone/sso/<provider>/start', methods=['POST'])
def lost_phone_sso_start(provider):
    from .auth import _normalise_ident
    role = request.form.get('role')
    role = role if role in ROLES else 'student'
    ident = _normalise_ident(role, request.form.get('ident'))
    if provider not in sso.recovery_providers_for(role):
        return _back_to_start('That sign-in option is not available. Use the emailed code instead.')
    if not ident:
        return _back_to_start('Please enter your ID.')
    if not recovery_lock.ip_start_allowed():
        audit('user.lost_device_recovery_failed', None, None,
              'Blocked: too many recovery starts from one address', actor=_SYSTEM)
        return _back_to_start('Too many attempts. Please try again later.')
    # Always go to the provider, even for an unknown ID or a locked account: the answer is
    # decided only after sign-in, so nothing here reveals which accounts exist.
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    session['recov_sso'] = {'provider': provider, 'state': state, 'nonce': nonce, 'role': role,
                            'ident': ident, 'exp': int(time.time()) + SSO_STATE_SECONDS}
    session.pop('recov_proof', None)
    return redirect(sso.authorize_url(provider, _sso_redirect_uri(provider), state, nonce))


@bp.route('/lost-phone/sso/<provider>/callback')
def lost_phone_sso_callback(provider):
    pending = session.pop('recov_sso', None)
    pending = pending if isinstance(pending, dict) else {}
    role, ident = pending.get('role'), pending.get('ident')
    if (provider not in sso.PROVIDERS or pending.get('provider') != provider
            or role not in ROLES or not isinstance(ident, str)
            or not isinstance(pending.get('exp'), int) or pending['exp'] <= int(time.time())
            or provider not in sso.recovery_providers_for(role)
            or not hmac.compare_digest(str(pending.get('state', '')),
                                       request.args.get('state', ''))):
        return _back_to_start('That sign-in link has expired. Please start again.')
    if request.args.get('error'):
        return _back_to_start('Sign-in was cancelled. You can try again or use the emailed code.',
                              'warning')
    code = request.args.get('code') or ''
    if not code:
        return _back_to_start('Sign-in did not complete. Please try again.')
    if not recovery_lock.ip_code_allowed():
        audit('user.lost_device_recovery_failed', None, None,
              'Blocked: too many sign-in attempts from one address', actor=_SYSTEM)
        return _back_to_start('Too many attempts. Please try again later.')
    try:
        claims = sso.fetch_claims(provider, code, _sso_redirect_uri(provider))
        email = sso.verified_email(provider, claims, pending.get('nonce'),
                                   sso.client_credentials(provider)[0])
    except sso.SSOError as exc:
        return _back_to_start(str(exc))
    user = _recovery_user(role, ident)
    if not user:
        return _back_to_start(SSO_FAIL)
    uid = user['id']
    if recovery_lock.begin_attempt(user) == 'locked':
        audit('user.lost_device_recovery_failed', 'users', uid, 'Blocked: recovery is locked',
              actor=_SYSTEM)
        return _back_to_start(SSO_FAIL)
    on_file = (user.get('email') or '').strip().lower()
    college = None
    if role == 'student':
        from .security import student_college_email, is_college_email
        college = student_college_email(user.get('roll') or user.get('username'))
    if not on_file or email != on_file or (college and not is_college_email(email)):
        recovery_lock.record_failure(user)
        audit('user.lost_device_recovery_failed', 'users', uid,
              f'Failed: {provider} sign-in did not match the account', actor=_SYSTEM)
        return _back_to_start(SSO_FAIL)
    recovery_lock.record_success(user)
    session['recov_proof'] = {'uid': uid, 'role': role, 'ident': ident, 'method': provider,
                              'exp': int(time.time()) + SSO_STATE_SECONDS}
    audit('user.lost_device_sso_verified', 'users', uid,
          f'Mailbox proved with {provider} sign-in (no phone, no backup codes)', actor=_SYSTEM)
    return redirect(url_for('lost_device.lost_phone_sso_finish'))


@bp.route('/lost-phone/sso/finish', methods=['GET', 'POST'])
def lost_phone_sso_finish():
    proof = _sso_proof()
    if not proof:
        return _back_to_start('That page has expired. Please start again.', 'warning')
    role, method = proof['role'], proof['method']

    def page():
        return render_template('lost_phone_sso_finish.html', ident=proof['ident'],
                               provider=method.title())

    if request.method != 'POST':
        return page()
    new = request.form.get('new_password') or ''
    problem = _password_problem(role, new, request.form.get('confirm_password') or '')
    if problem:                                   # the proof stays valid: just try again
        flash(problem, 'error')
        return page()
    user = _recovery_user(role, proof['ident'])
    if (not user or user['id'] != proof['uid']
            or recovery_lock.locked_seconds(user)):
        session.pop('recov_proof', None)
        return _back_to_start(SSO_FAIL)
    session.pop('recov_proof', None)              # one use only
    problem = _finish_recovery(user, new, f'{method.title()} sign-in')
    if problem:
        return _back_to_start(problem)
    flash(RECOVERY_DONE, 'success')
    return redirect(url_for(_LOGIN[role]))


# --------------------------------------------------------------------------- #
# Admin: both registered recovery e-mails
# --------------------------------------------------------------------------- #
def _admin_state():
    """The signed-session recovery state, or None if absent, malformed or expired."""
    st = session.get('adminlost')
    if (not isinstance(st, dict) or not isinstance(st.get('uid'), int)
            or not isinstance(st.get('exp'), int) or st['exp'] <= int(time.time())):
        session.pop('adminlost', None)
        return None
    return st


@bp.route('/admin/lost-authenticator', methods=['GET', 'POST'])
def admin_lost_authenticator():
    from .auth import _EMAIL_RE, _admin_pairs, _mail_can_work, _send_code
    if request.method != 'POST':
        return render_template('admin_lost_authenticator.html')
    if not _mail_can_work():
        flash('Recovery e-mail is not configured. Contact the person who runs the server.', 'error')
        return render_template('admin_lost_authenticator.html')
    if not _ip_allowed('adminlost-ip'):
        flash('Too many attempts. Please try again later.', 'error')
        return render_template('admin_lost_authenticator.html')
    e1 = (request.form.get('email1') or '').strip().lower()
    e2 = (request.form.get('email2') or '').strip().lower()
    if not (_EMAIL_RE.match(e1) and _EMAIL_RE.match(e2)) or e1 == e2:
        flash('Enter your two different registered recovery e-mail addresses.', 'error')
        return render_template('admin_lost_authenticator.html')
    target = None
    for u in db.query("SELECT * FROM users WHERE role='admin' AND is_active=1"):
        pair = _admin_pairs(u)
        if len(pair) == 2 and set(pair) == {e1, e2}:
            target = u
            break
    uid = 0
    if target and authenticator.is_active(target['id']):
        pair = _admin_pairs(target)
        ok1, problem = _send_code(target['id'], 'admin_lost_1', pair[0])
        ok2, problem2 = (_send_code(target['id'], 'admin_lost_2', pair[1]) if ok1
                         else (False, problem))
        if ok1 and ok2:
            uid = target['id']
            audit('admin.lost_authenticator_requested', 'users', uid,
                  'Codes sent to both recovery e-mails', actor=_SYSTEM)
        else:
            for purpose in ('admin_lost_1', 'admin_lost_2'):
                verification.discard(target['id'], purpose)
            flash(problem2 or problem or GENERIC, 'error')
            return render_template('admin_lost_authenticator.html')
    session['adminlost'] = {'uid': int(uid), 'exp': int(time.time()) + ADMIN_STATE_SECONDS}
    # Same answer whether or not the details matched (no probing).
    flash('If both addresses match the registered ones, a different 6-digit code has been '
          'sent to each. Check both inboxes.', 'info')
    return redirect(url_for('lost_device.admin_lost_authenticator_verify'))


@bp.route('/admin/lost-authenticator/verify', methods=['GET', 'POST'])
def admin_lost_authenticator_verify():
    from .auth import _admin_pairs
    st = _admin_state()
    if st is None:
        return redirect(url_for('lost_device.admin_lost_authenticator'))
    if request.method != 'POST':
        return render_template('admin_lost_authenticator_verify.html')
    uid = st['uid']

    def fail(detail='Failed'):
        audit('admin.lost_authenticator_failed', 'users', uid or None, detail, actor=_SYSTEM)
        flash(GENERIC, 'error')
        return render_template('admin_lost_authenticator_verify.html')

    if not _ip_allowed('adminlostv-ip'):
        return fail('Blocked: too many requests from one address')
    key = f'adminlostv:{uid}'
    if uid and not reserve_login_attempt(
            key, int(current_app.config.get('OTP_MAX_ATTEMPTS', 5)),
            int(current_app.config.get('OTP_LOCKOUT_SECONDS', 900)),
            window_seconds=authenticator.FAILURE_WINDOW_SECONDS):
        return fail('Blocked: account locked')
    admin = (db.query("SELECT * FROM users WHERE id=? AND role='admin' AND is_active=1",
                      (uid,), one=True) if uid else None)
    # Both codes are always checked (no early exit), and neither is spent unless both are right.
    r1 = verification.check(uid, 'admin_lost_1', request.form.get('code1'), consume=False) if uid else None
    r2 = verification.check(uid, 'admin_lost_2', request.form.get('code2'), consume=False) if uid else None
    pair = _admin_pairs(admin) if admin else []
    if not (admin and r1 and r2 and r1 != r2 and len(pair) == 2
            and {r1.lower(), r2.lower()} == set(pair)):
        return fail('Failed: codes not accepted')
    for purpose in ('admin_lost_1', 'admin_lost_2'):
        verification.discard(uid, purpose)
    try:
        applied = authenticator.apply_lost_device_reset(uid)
    except authenticator.AuthenticatorConfigError:
        logger.error('Admin recovery failed: the encryption key is not usable')
        flash('Recovery is not available right now.', 'error')
        return render_template('admin_lost_authenticator_verify.html')
    if not applied:
        return fail('Failed: no active authenticator (or already recovered)')
    session.pop('adminlost', None)
    clear_login_failures(key)
    audit('admin.lost_authenticator_recovery', 'users', uid,
          'Admin lost-authenticator recovery: both recovery e-mails verified; authenticator '
          'and backup codes removed; new setup required', actor=admin)
    _notify([admin['email'], *pair], admin['name'], 'lost_device_reset', uid)
    flash('Recovery complete. The old authenticator and backup codes were removed. Sign in '
          'and set up a new authenticator app.', 'success')
    return redirect(url_for('auth.admin_login'))
