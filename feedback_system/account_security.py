"""Account Security page (all five roles): authenticator status and backup codes.

* ``GET  /account/security``               status, backup-code count, enrollment policy;
* ``POST /account/security/backup-codes``  new backup codes; needs a current 6-digit TOTP code.

Deliberately NOT here: removing or replacing an active authenticator. Only the separate
lost-device recovery (Checkpoint 3) or an audited administrator reset may do that; a normal
password reset never does. Backup codes are rendered once, in the response that creates
them, and are never kept in the session, a redirect or a log.
"""
import logging
import re

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from . import authenticator, mailer
from .security import audit, current_user, role_required

logger = logging.getLogger(__name__)
bp = Blueprint('account_security', __name__, url_prefix='/account')

_TOTP_RE = re.compile(r'^\d{6}$')


@bp.route('/security')
@role_required('student', 'teacher', 'hod', 'dean', 'admin')
def index():
    user = current_user()
    uid = user['id']
    active = authenticator.is_active(uid)
    state, until = authenticator.enforcement_state(user['role'], current_app.config)
    return render_template(
        'account_security.html', role=user['role'], active=active,
        remaining=authenticator.backup_codes_remaining(uid) if active else 0,
        total=authenticator.BACKUP_CODE_COUNT, state=state, until=until,
        locked=authenticator.otp_locked_seconds(uid) if active else 0,
        key_ready=bool(current_app.config.get('TOTP_ENCRYPTION_KEY')),
        admin_enforcement_disabled=(user['role'] == 'admin'))


@bp.route('/security/backup-codes', methods=['POST'])
@role_required('student', 'teacher', 'hod', 'dean', 'admin')
def regenerate_backup_codes():
    from .auth import render_backup_codes
    user = current_user()
    uid = user['id']
    back = redirect(url_for('account_security.index'))
    if not authenticator.is_active(uid):
        flash('Set up your authenticator app first.', 'warning')
        return redirect(url_for('auth.setup_authenticator'))
    code = (request.form.get('code') or '').strip()
    if not _TOTP_RE.match(code):
        # A backup code must not be accepted (or spent) here: only a current app code.
        flash('Enter the 6-digit code shown in your authenticator app.', 'error')
        return back
    try:
        ok, reason = authenticator.verify_second_factor(uid, code)
    except authenticator.AuthenticatorConfigError:
        flash('The authenticator cannot be checked right now. Please contact the administrator.',
              'error')
        return back
    if reason == 'locked':
        flash(f'Too many incorrect codes. Try again in '
              f'{max(1, authenticator.otp_locked_seconds(uid) // 60)} minutes.', 'error')
        return back
    if not ok or reason != 'totp':
        flash('That code is not correct. Wait for the next code in the app and try again.', 'error')
        return back
    codes = authenticator.regenerate_backup_codes(uid)
    audit('user.backup_codes_regenerated', 'users', uid, 'Backup codes regenerated')
    try:
        sent = mailer.security_notice(user.get('email'), user.get('name'),
                                      'backup_codes_regenerated')
    except Exception:
        sent = False
    if not sent:
        audit('user.security_notice_not_sent', 'users', uid,
              'Backup-codes notice was not sent')
    return render_backup_codes(codes, reason='regenerated', user=user)
