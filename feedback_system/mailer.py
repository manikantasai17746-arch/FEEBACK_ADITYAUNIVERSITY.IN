"""Outgoing e-mail (Gmail SMTP by default).

Configure with environment variables (same names as the EduVault project):

* ``MAIL_USER``          the sending Gmail address
* ``MAIL_APP_PASSWORD``  a Google *App Password* (not the normal password)
* ``MAIL_FROM_NAME``     display name, default "Aditya University Feedback"
* ``SMTP_HOST`` / ``SMTP_PORT``  optional, default smtp.gmail.com / 465 (SSL)

When no mail account is configured and the app is NOT running on Vercel, the
message is printed to the server console instead, so the flow can be tried
locally.  On Vercel an unconfigured mailer simply reports failure (codes are
never written to the logs there).
"""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger(__name__)


def is_configured():
    return bool(os.environ.get('MAIL_USER') and os.environ.get('MAIL_APP_PASSWORD'))


def send_email(to, subject, text, html=None):
    """Send one message. Returns True on success, False otherwise (never raises)."""
    user = os.environ.get('MAIL_USER')
    password = (os.environ.get('MAIL_APP_PASSWORD') or '').replace(' ', '')
    if not (user and password):
        if os.environ.get('VERCEL'):
            log.error('Mail is not configured (MAIL_USER / MAIL_APP_PASSWORD).')
            return False
        print('\n' + '=' * 60 + '\n[DEV MAIL - no MAIL_USER configured]\n'
              f'To: {to}\nSubject: {subject}\n\n{text}\n' + '=' * 60 + '\n', flush=True)
        return True

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = f"{os.environ.get('MAIL_FROM_NAME', 'Aditya University Feedback')} <{user}>"
    msg['To'] = to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype='html')
    host = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
    port = int(os.environ.get('SMTP_PORT', '465'))
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(),
                                  timeout=15) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(user, password)
                smtp.send_message(msg)
        return True
    except Exception:  # pragma: no cover - network dependent
        log.exception('Could not send e-mail to %s', to)
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
