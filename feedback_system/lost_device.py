"""Lost-device recovery (Checkpoint 3). Separate from normal password reset.

* ``/lost-phone``  Student, Teacher, HOD, Dean: ID + current password + ONE unused backup
  code, in a single request (no session state to tamper with).
* ``/admin/lost-authenticator``  Admin: both registered recovery e-mails, a code to each,
  and both codes must be right in one submission.

A successful recovery removes the old authenticator and every remaining backup code in one
atomic step and leaves a "re-enrollment required" marker, so the next sign-in goes
straight to authenticator setup. Nothing is removed on any failure. Every failure shows the
same message so the pages cannot be used to find out which accounts exist or which have an
authenticator. Normal password reset (``auth.forgot_password`` and friends) is untouched.
"""
import logging
import time

from flask import (Blueprint, current_app, flash, redirect, render_template, request, session,
                   url_for)

from . import authenticator, db, mailer, verification
from .security import (audit, clear_login_failures, hash_password, reserve_login_attempt,
                       verify_password)

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
    try:
        ok, reason = authenticator.recover_lost_device(uid, code, reserve=False)
    except authenticator.AuthenticatorConfigError:
        logger.error('Lost-device recovery failed: the encryption key is not usable')
        return fail('Recovery is not available right now. Please contact the administrator.')
    if not ok:
        audit('user.lost_device_recovery_failed', 'users', uid, f'Failed: {reason}',
              actor=_SYSTEM)
        return fail()
    clear_login_failures(f'lostphone-ip:{request.remote_addr}')
    audit('user.lost_device_recovery', 'users', uid,
          'Lost-device recovery: authenticator and backup codes removed; new setup required',
          actor=user)
    _notify([user['email']], user['name'], 'lost_device_reset', uid)
    flash('Recovery complete. Your old authenticator and backup codes were removed. '
          'Sign in with your password and set up a new authenticator app.', 'success')
    return redirect(url_for(_LOGIN[user['role']]))


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
