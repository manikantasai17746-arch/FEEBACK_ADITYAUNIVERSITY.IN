"""Typing a college e-mail on the student login modal hands it to Microsoft as a hint."""
from urllib.parse import parse_qs, urlparse

import pytest

from helpers import token_from

TENANT = '11111111-2222-3333-4444-555555555555'
DOMAIN = 'adityauniversity.in'


@pytest.fixture()
def ms_env(monkeypatch):
    monkeypatch.setenv('MICROSOFT_CLIENT_ID', 'microsoft-client-id')
    monkeypatch.setenv('MICROSOFT_CLIENT_SECRET', 'm-secret')
    monkeypatch.setenv('MICROSOFT_TENANT_ID', TENANT)


def test_modal_has_mail_form(client, ms_env):
    html = client.get('/student/login').get_data(as_text=True)
    assert 'name="student_email"' in html and 'id="lpMailForm"' in html


def test_college_mail_goes_to_microsoft_with_login_hint(client, ms_env):
    email = f'26b21cs073@{DOMAIN}'
    r = client.post('/student/login', data={'student_email': email,
                                            '_csrf': token_from(client, '/student/login')})
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers['Location']).query)
    assert r.headers['Location'].startswith('https://login.microsoftonline.com/')
    assert q['login_hint'] == [email]


def test_non_college_mail_is_rejected_and_form_stays_open(client, ms_env):
    r = client.post('/student/login', data={'student_email': 'someone@gmail.com',
                                            '_csrf': token_from(client, '/student/login')})
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'Please enter your college mail ID' in html
    assert 'id="lpMailForm"' in html and 'id="lpMailForm"' in html.split('hidden')[0] + 'id="lpMailForm"'
