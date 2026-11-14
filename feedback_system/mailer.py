"""Outgoing e-mail over SMTP.

The recipient can be on **any** provider (Gmail, Outlook/Hotmail/Office 365,
Yahoo, a university domain, ...): SMTP delivers to any valid address.  What is
configurable is the *sending* account:

* ``MAIL_USER``          the sending address
* ``MAIL_APP_PASSWORD``  an app password for that account (not the normal one)
* ``MAIL_FROM_NAME``     display name, default "Aditya University Feedback"
* ``MAIL_PROVIDER``      ``gmail`` (default) or ``outlook`` (also ``office365``);
                         picks the right SMTP host/port/security automatically
* ``SMTP_HOST`` / ``SMTP_PORT``  optional explicit override for any other server
                         (port 465 = SSL, anything else = STARTTLS)

Presets: gmail -> smtp.gmail.com:465 (SSL), outlook -> smtp.office365.com:587
(STARTTLS).

When no mail account is configured and the app is NOT running on Vercel, the
message is printed to the server console instead, so the flow can be tried
locally.  On Vercel an unconfigured mailer simply reports failure (codes are
never written to the logs there).
"""
import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger(__name__)

PROVIDERS = {
    'gmail': ('smtp.gmail.com', 465),
    'outlook': ('smtp.office365.com', 587),
    'office365': ('smtp.office365.com', 587),
    'hotmail': ('smtp.office365.com', 587),
}

_LOCAL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
_LABEL_RE = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$')


def clean_email(value):
    """Return the trimmed, lower-cased address, or '' when it is not valid.

    Accepts any real domain (gmail.com, outlook.com, hotmail.com, yahoo.com,
    a college domain, internationalised domains...).  Rejects spaces, missing
    parts, display-name tricks and header-injection characters.
    """
    addr = (value or '').strip()
    if not addr or len(addr) > 254 or any(c in addr for c in ' \t\r\n<>,;"()'):
        return ''
    local, sep, domain = addr.rpartition('@')
    if not sep or not local or len(local) > 64 or not _LOCAL_RE.match(local):
        return ''
    if local.startswith('.') or local.endswith('.') or '..' in local:
        return ''
    try:
        ascii_domain = domain.encode('idna').decode('ascii')
    except UnicodeError:
        return ''
    labels = ascii_domain.split('.')
    if len(labels) < 2 or not all(_LABEL_RE.match(x) for x in labels):
        return ''
    return f'{local}@{domain.lower()}'


def is_valid_email(value):
    return bool(clean_email(value))


def is_configured():
    return bool(os.environ.get('MAIL_USER') and os.environ.get('MAIL_APP_PASSWORD'))


def _smtp_target():
    """(host, port) from SMTP_HOST/SMTP_PORT, else the MAIL_PROVIDER preset."""
    provider = (os.environ.get('MAIL_PROVIDER') or 'gmail').strip().lower()
    host, port = PROVIDERS.get(provider, PROVIDERS['gmail'])
    host = os.environ.get('SMTP_HOST') or host
    try:
        port = int(os.environ.get('SMTP_PORT') or port)
    except ValueError:
        pass
    return host, port


def send_email(to, subject, text, html=None, attachments=None):
    """Send one message. Returns True on success, False otherwise (never raises).

    ``attachments`` is an optional list of ``(filename, bytes, mimetype)``,
    e.g. ``('pending.pdf', pdf_bytes, 'application/pdf')``.
    """
    recipient = clean_email(to)
    if not recipient:
        log.warning('Refusing to send mail: invalid recipient address %r', to)
        return False
    subject = ' '.join(str(subject or '').split())      # no header injection
    user = os.environ.get('MAIL_USER')
    password = (os.environ.get('MAIL_APP_PASSWORD') or '').replace(' ', '')
    if not (user and password):
        if os.environ.get('VERCEL'):
            log.error('Mail is not configured (MAIL_USER / MAIL_APP_PASSWORD).')
            return False
        names = ', '.join(a[0] for a in (attachments or [])) or 'none'
        print('\n' + '=' * 60 + '\n[DEV MAIL - no MAIL_USER configured]\n'
              f'To: {recipient}\nSubject: {subject}\nAttachments: {names}\n\n{text}\n'
              + '=' * 60 + '\n', flush=True)
        return True

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = f"{os.environ.get('MAIL_FROM_NAME', 'Aditya University Feedback')} <{user}>"
    msg['To'] = recipient
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype='html')
    for filename, data, mimetype in (attachments or []):
        maintype, _, subtype = (mimetype or 'application/octet-stream').partition('/')
        msg.add_attachment(data, maintype=maintype, subtype=subtype or 'octet-stream',
                           filename=filename)
    host, port = _smtp_target()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(),
                                  timeout=20) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(user, password)
                smtp.send_message(msg)
        return True
    except Exception:  # pragma: no cover - network dependent
        log.exception('Could not send e-mail to %s', recipient)
        return False


def code_email(code, purpose, minutes):
    """Return (subject, text, html) for a verification / reset code."""
    if purpose == 'reset_password':
        subject = 'Your password reset code'
        lead = 'Use this code to reset your Faculty Feedback password:'
    else:
        subject = 'Confirm your email address'
        lead = 'Use this code to confirm your email address:'
    text = (f'{lead}\n\n    {code}\n\nThe code expires in {minutes} minutes. '
            'If you did not ask for it, you can ignore this email.\n\n'
            '— Aditya University · Faculty Feedback System')
    html = (f'<div style="font-family:Arial,sans-serif;max-width:460px;margin:auto">'
            f'<h2 style="color:#0B2F6B">Aditya University</h2><p>{lead}</p>'
            f'<p style="font-size:32px;letter-spacing:8px;font-weight:700;color:#1558C0;'
            f'background:#EEF3FB;padding:14px 18px;border-radius:10px;text-align:center">{code}</p>'
            f'<p style="color:#5A6473">Expires in {minutes} minutes. If you did not ask for this, '
            f'ignore this email.</p></div>')
    return subject, text, html


def credentials_email(role, emp_id, name, temp_password, login_url):
    """Return (subject, text, html) with a new HOD/Dean account's first login."""
    from html import escape
    label = 'Dean' if role == 'dean' else 'HOD'
    subject = f'Your {label} account - Aditya University Faculty Feedback'
    text = (f'Hello {name},\n\nYour {label} account on the Faculty Feedback Portal is ready.\n\n'
            f'    Employee ID:        {emp_id}\n'
            f'    Temporary password: {temp_password}\n'
            f'    Sign in at:         {login_url}\n\n'
            'You will be asked to change this password the first time you sign in. '
            'Do not share it with anyone.\n\n'
            '- Aditya University - Faculty Feedback System')
    html = (f'<div style="font-family:Arial,sans-serif;max-width:480px;margin:auto">'
            f'<h2 style="color:#0B2F6B">Aditya University</h2>'
            f'<p>Hello {escape(name)},</p>'
            f'<p>Your <b>{label}</b> account on the Faculty Feedback Portal is ready.</p>'
            f'<table style="background:#EEF3FB;border-radius:10px;padding:12px 16px">'
            f'<tr><td>Employee ID</td><td><b>{escape(emp_id)}</b></td></tr>'
            f'<tr><td>Temporary password</td><td><b style="font-family:monospace">'
            f'{escape(temp_password)}</b></td></tr></table>'
            f'<p><a href="{escape(login_url)}">Sign in here</a></p>'
            f'<p style="color:#5A6473">You must change this password at your first sign in. '
            f'Do not share it with anyone.</p></div>')
    return subject, text, html


def delete_code_email(code, role, emp_id, name, requested_by, minutes):
    """Return (subject, text, html) for the HOD/Dean permanent-delete confirmation."""
    from html import escape
    label = 'Dean' if role == 'dean' else 'HOD'
    subject = f'Confirm permanent deletion of {label} {emp_id}'
    text = (f'An administrator ({requested_by}) asked to PERMANENTLY DELETE this account:\n\n'
            f'    {label}: {name} ({emp_id})\n\n'
            f'Confirmation code:\n\n    {code}\n\n'
            f'The code expires in {minutes} minutes. If you did not expect this, do not share '
            'the code - the account will not be deleted.\n\n'
            '- Aditya University - Faculty Feedback System')
    html = (f'<div style="font-family:Arial,sans-serif;max-width:480px;margin:auto">'
            f'<h2 style="color:#B3261E">Confirm account deletion</h2>'
            f'<p>An administrator (<b>{escape(requested_by)}</b>) asked to '
            f'<b>permanently delete</b> this account:</p>'
            f'<p><b>{label}: {escape(name)} ({escape(emp_id)})</b></p>'
            f'<p style="font-size:32px;letter-spacing:8px;font-weight:700;color:#B3261E;'
            f'background:#FDECEA;padding:14px 18px;border-radius:10px;text-align:center">{code}</p>'
            f'<p style="color:#5A6473">Expires in {minutes} minutes. If you did not expect this, '
            f'do not share the code.</p></div>')
    return subject, text, html


def delete_forms_code_email(code, count, requested_by, minutes):
    """Return (subject, text, html) for feedback-form deletion confirmation."""
    from html import escape
    subject = f'Confirm deletion of {count} feedback form(s)'
    text = (f'An administrator ({requested_by}) asked to SOFT-DELETE {count} feedback form(s).\n\n'
            f'Confirmation code:\n\n    {code}\n\n'
            f'The code expires in {minutes} minutes. If you did not expect this, do not share '
            'the code - the forms will not be deleted.\n\n'
            '- Aditya University - Faculty Feedback System')
    html = (f'<div style="font-family:Arial,sans-serif;max-width:480px;margin:auto">'
            f'<h2 style="color:#B3261E">Confirm feedback-form deletion</h2>'
            f'<p>An administrator (<b>{escape(requested_by)}</b>) asked to '
            f'<b>soft-delete {count} feedback form(s)</b>.</p>'
            f'<p style="font-size:32px;letter-spacing:8px;font-weight:700;color:#B3261E;'
            f'background:#FDECEA;padding:14px 18px;border-radius:10px;text-align:center">{code}</p>'
            f'<p style="color:#5A6473">Expires in {minutes} minutes. If you did not expect this, '
            f'do not share the code.</p></div>')
    return subject, text, html


def account_removed_email(role, name, emp_id):
    """Return (subject, text, html) telling a HOD/Dean their account was removed."""
    from html import escape
    label = 'Dean' if role == 'dean' else 'HOD'
    subject = f'Your {label} account has been removed - thank you for your contribution'
    text = (f'Dear {name},\n\n'
            f'Your {label} account (Employee ID: {emp_id}) on the Aditya University '
            'Faculty Feedback Portal has been removed by the administrator.\n\n'
            'Thank you for your contribution and support to the Faculty Feedback System. '
            'We truly appreciate the time and effort you gave.\n\n'
            'If you believe this was done by mistake, please contact the administrator.\n\n'
            'Best wishes,\nAditya University - Faculty Feedback System')
    html = (f'<div style="font-family:Arial,sans-serif;max-width:480px;margin:auto">'
            f'<h2 style="color:#0B2F6B">Aditya University</h2>'
            f'<p>Dear {escape(name)},</p>'
            f'<p>Your <b>{label}</b> account (Employee ID: <b>{escape(str(emp_id))}</b>) on the '
            f'Faculty Feedback Portal has been removed by the administrator.</p>'
            f'<p>Thank you for your contribution and support to the Faculty Feedback System. '
            f'We truly appreciate the time and effort you gave.</p>'
            f'<p style="color:#5A6473">If you believe this was done by mistake, please '
            f'contact the administrator.</p>'
            f'<p>Best wishes,<br>Aditya University - Faculty Feedback System</p></div>')
    return subject, text, html
