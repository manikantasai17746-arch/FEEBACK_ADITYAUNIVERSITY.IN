"""Google / Microsoft sign-in used to verify a user's e-mail address.

A successful sign-in proves the person controls that mailbox, so the address
can be stored as verified without sending a one-time code (saves mail cost).

Configuration (environment variables; a provider is off unless both are set):

* ``GOOGLE_CLIENT_ID`` / ``GOOGLE_CLIENT_SECRET``
* ``GOOGLE_HOSTED_DOMAIN``   optional, e.g. ``aditya.ac.in``; only that Workspace
                              domain is accepted when set.
* ``MICROSOFT_CLIENT_ID`` / ``MICROSOFT_CLIENT_SECRET``
* ``MICROSOFT_TENANT_ID``    optional. A tenant GUID restricts sign-in to the
                              college's Microsoft tenant. Unset (or ``common``)
                              accepts any Microsoft account, including personal
                              Outlook.com ones, so set it in production.

Tokens are fetched directly from the provider's token endpoint over HTTPS by
the server, so the ID token is read without a separate signature check, as the
OpenID Connect spec allows for that case. ``aud``, ``nonce`` and (for Microsoft)
``tid`` are still checked.
"""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

GOOGLE = 'google'
MICROSOFT = 'microsoft'
PROVIDERS = (GOOGLE, MICROSOFT)
_TIMEOUT = 10


class SSOError(Exception):
    """A sign-in could not be used to verify an e-mail (message is user-facing)."""


def _cfg(name):
    return (os.environ.get(name) or '').strip()


def client_credentials(provider):
    if provider == GOOGLE:
        return _cfg('GOOGLE_CLIENT_ID'), _cfg('GOOGLE_CLIENT_SECRET')
    if provider == MICROSOFT:
        return _cfg('MICROSOFT_CLIENT_ID'), _cfg('MICROSOFT_CLIENT_SECRET')
    return '', ''


def enabled(provider):
    client_id, secret = client_credentials(provider)
    return bool(provider in PROVIDERS and client_id and secret)


def enabled_providers():
    return [p for p in PROVIDERS if enabled(p)]


def student_microsoft_ready():
    """Microsoft sign-in for students needs the college tenant GUID.

    Without it the multi-tenant endpoint would accept an e-mail claim from any Azure
    tenant, which proves nothing about who the student is.
    """
    return enabled(MICROSOFT) and _is_tenant_guid(_microsoft_tenant())


def providers_for(role):
    """Sign-in options offered to a role. Students: college Microsoft account only."""
    if role == 'student':
        return [MICROSOFT] if student_microsoft_ready() else []
    return enabled_providers()


RECOVERY_ROLES = ('student', 'teacher', 'hod', 'dean')


def recovery_providers_for(role):
    """Sign-in options for lost-device recovery (Student/Teacher/HOD/Dean; never Admin).

    Stricter than :func:`providers_for`: Microsoft is offered to EVERY role only when the
    college tenant GUID is configured (otherwise a token from any Azure tenant could carry
    any e-mail). Google is for staff only, when enabled; ``verified_email`` still requires
    ``email_verified`` and the optional hosted domain. Students: college Microsoft only.
    """
    if role not in RECOVERY_ROLES:
        return []
    out = [MICROSOFT] if student_microsoft_ready() else []
    if role != 'student' and enabled(GOOGLE):
        out.append(GOOGLE)
    return out


def _microsoft_tenant():
    return _cfg('MICROSOFT_TENANT_ID') or 'common'


def _is_tenant_guid(value):
    parts = value.split('-')
    return len(parts) == 5 and all(c in '0123456789abcdefABCDEF' for p in parts for c in p)


def authorize_url(provider, redirect_uri, state, nonce):
    client_id, _ = client_credentials(provider)
    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'nonce': nonce,
    }
    if provider == GOOGLE:
        base = 'https://accounts.google.com/o/oauth2/v2/auth'
        params['prompt'] = 'select_account'
        domain = _cfg('GOOGLE_HOSTED_DOMAIN')
        if domain:
            params['hd'] = domain
    elif provider == MICROSOFT:
        base = f'https://login.microsoftonline.com/{_microsoft_tenant()}/oauth2/v2.0/authorize'
        params['prompt'] = 'select_account'
    else:
        raise SSOError('Unknown sign-in provider.')
    return base + '?' + urllib.parse.urlencode(params)


def _token_endpoint(provider):
    if provider == GOOGLE:
        return 'https://oauth2.googleapis.com/token'
    return f'https://login.microsoftonline.com/{_microsoft_tenant()}/oauth2/v2.0/token'


def _decode_jwt_payload(token):
    try:
        payload = token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode()))
    except (IndexError, ValueError):
        raise SSOError('The sign-in response was not valid. Please try again.')


def fetch_claims(provider, code, redirect_uri):
    """Exchange an authorization code for the ID-token claims.

    Raises SSOError if the provider refuses the code or the request fails.
    """
    client_id, secret = client_credentials(provider)
    body = urllib.parse.urlencode({
        'client_id': client_id,
        'client_secret': secret,
        'code': code,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }).encode()
    req = urllib.request.Request(_token_endpoint(provider), data=body,
                                 headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise SSOError('We could not reach the sign-in provider. Please try again.')
    id_token = data.get('id_token')
    if not id_token:
        raise SSOError('The sign-in provider did not return your identity. Please try again.')
    return _decode_jwt_payload(id_token)


def verified_email(provider, claims, expected_nonce, expected_client_id):
    """Return the lower-cased e-mail the provider vouches for, or raise SSOError."""
    if claims.get('nonce') != expected_nonce:
        raise SSOError('This sign-in link has expired. Please try again.')
    aud = claims.get('aud')
    audiences = aud if isinstance(aud, list) else [aud]
    if expected_client_id not in audiences:
        raise SSOError('The sign-in response was for a different application.')

    if provider == GOOGLE:
        if claims.get('iss') not in ('https://accounts.google.com', 'accounts.google.com'):
            raise SSOError('The sign-in response was not from Google.')
        if str(claims.get('email_verified')).lower() != 'true':
            raise SSOError('Google has not verified this email address. Use another account.')
        domain = _cfg('GOOGLE_HOSTED_DOMAIN').lower()
        if domain and (claims.get('hd') or '').lower() != domain:
            raise SSOError(f'Please sign in with your {domain} Google account.')
        email = claims.get('email') or ''
    else:
        tenant = _microsoft_tenant()
        if _is_tenant_guid(tenant) and claims.get('tid', '').lower() != tenant.lower():
            raise SSOError('Please sign in with your college Microsoft account.')
        email = claims.get('email') or ''
        if not email and '@' in (claims.get('preferred_username') or ''):
            email = claims['preferred_username']

    email = email.strip().lower()
    if not email or '@' not in email:
        raise SSOError('This account does not have an email address we can use.')
    return email
