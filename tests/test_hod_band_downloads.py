"""HOD 'Teachers by Score Band' Excel / PDF downloads."""
import os
import tempfile
from io import BytesIO

import pytest

from feedback_system import create_app, db, reports
from feedback_system.security import hash_password
from helpers import login

PW = 'hodpass123'
TEACHER_PW = 'teacher@123'


@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-hod-band-dl-')
    return create_app({
        'TESTING': True, 'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None, 'SECRET_KEY': 'test-hod-band-dl', 'AUTO_IMPORT': True,
        'ADMIN_EMAIL': 'admin@college.edu', 'ADMIN_PASSWORD': 'admin123',
        'DEFAULT_TEACHER_PASSWORD': TEACHER_PW, 'DEFAULT_STUDENT_PASSWORD': 'student@123',
        'LOGIN_MAX_ATTEMPTS': 50, 'REQUIRE_ACCOUNT_SETUP': False,
    })


@pytest.fixture()
def client(app):
    return app.test_client()


def _seed_hod(app):
    with app.app_context():
        branch = db.query("SELECT id FROM branches WHERE code='CSE'", one=True)
        if not branch:
            db.execute("INSERT INTO branches(code, name) VALUES('CSE','CSE')")
            branch = db.query("SELECT id FROM branches WHERE code='CSE'", one=True)
        if not db.query("SELECT id FROM users WHERE username='HODBAND1'", one=True):
            db.execute(
                """INSERT INTO users(role, username, emp_code, name, email, password_hash,
                                     is_active, onboarding_complete)
                   VALUES('hod','HODBAND1','HODBAND1','Band HOD','hodband@test.edu',?,1,1)""",
                (hash_password(PW),))
            hod = db.query("SELECT id FROM users WHERE username='HODBAND1'", one=True)
            db.execute("INSERT INTO user_department_scopes(user_id, branch_id, is_primary) "
                       "VALUES(?,?,1)", (hod['id'], branch['id']))


def _hod(client):
    assert login(client, '/hod/login', {'emp_code': 'HODBAND1', 'password': PW}).status_code == 302
    return client


BANDS = [
    {'key': 'le75', 'label': '75% or less', 'count': 2, 'rows': [
        {'form_id': 1, 'teacher_user_id': 1, 'teacher_name': 'A One', 'teacher_emp_code': 'E1', 'subject_code': 'CS1',
         'subject_name': 'Data Structures', 'category': 'main', 'valid_responses': 3,
         'overall_score': 62.5, 'teacher_email': 'a@x.edu'},
        {'form_id': 2, 'teacher_user_id': 2, 'teacher_name': 'B Two', 'teacher_emp_code': 'E2', 'subject_code': 'CS2',
         'subject_name': 'Algorithms', 'category': 'lab', 'response_count': 1,
         'overall_score': 52.08, 'teacher_email': ''}]},
    {'key': '75-80', 'label': 'Above 75 – 80%', 'count': 0, 'rows': []},
]


def test_unit_pdf_and_xlsx_builders():
    pdf = reports.build_band_list_pdf(BANDS, 'T', generated_at='now', filter_lines=['x'])
    assert pdf[:4] == b'%PDF' and len(pdf) > 500
    from openpyxl import load_workbook
    wb = load_workbook(BytesIO(reports.build_band_list_xlsx(BANDS, 'T')))
    assert wb.sheetnames == ['All bands', '75% or less']          # empty band gets no sheet
    ws = wb['All bands']
    values = [[c.value for c in row] for row in ws.iter_rows()]
    data = [r for r in values if r[0] == '75% or less']
    assert [r[2] for r in data] == ['A One', 'B Two']
    assert [r[8] for r in data] == [62.5, 52.08]


def test_download_routes_and_scope(app, client):
    _seed_hod(app)
    # not logged in -> blocked
    assert client.get('/hod/performance/bands/download.pdf').status_code in (302, 401, 403)
    c = _hod(client)
    for fmt, mime in (('pdf', 'application/pdf'),
                      ('xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')):
        for q in ('', '?band=le75', '?band=95-100&category=main&semester_id=1'):
            r = c.get(f'/hod/performance/bands/download.{fmt}{q}')
            assert r.status_code == 200, (fmt, q)
            assert r.mimetype == mime
            assert 'attachment' in r.headers['Content-Disposition']
    assert c.get('/hod/performance/bands/download.csv').status_code == 404
    assert c.get('/hod/performance/bands/download.pdf?band=nope').status_code == 404


def test_teacher_cannot_download(app, client):
    _seed_hod(app)
    login(client, '/teacher/login', {'emp_code': '6817', 'password': TEACHER_PW})
    assert client.get('/hod/performance/bands/download.xlsx').status_code in (302, 403)


def test_buttons_and_file_use_same_rows_as_screen(app, client, monkeypatch):
    """Per-band buttons keep the on-screen filters, and the file holds the screen's rows."""
    from feedback_system import hod
    _seed_hod(app)
    calls = []

    def fake_band_data(*args, **kwargs):
        calls.append(kwargs.get('category'))
        return [dict(b) for b in BANDS]
    monkeypatch.setattr(hod, '_band_data', fake_band_data)
    c = _hod(client)

    html = c.get('/hod/?category=main&semester_id=1').get_data(as_text=True)
    assert 'download.xlsx?band=all' in html
    assert 'download.pdf?band=le75' in html
    assert 'category=main' in html and 'semester_id=1' in html

    page = c.get('/hod/performance/bands?category=lab').get_data(as_text=True)
    assert 'download.xlsx?band=le75' in page and 'category=lab' in page

    calls.clear()
    r = c.get('/hod/performance/bands/download.xlsx?band=le75&category=lab')
    assert calls == ['lab']                       # same category filter as the screen
    from openpyxl import load_workbook
    wb = load_workbook(BytesIO(r.data))
    assert wb.sheetnames == ['All bands', '75% or less']
    names = [row[1].value for row in wb['75% or less'].iter_rows(min_row=1) if row[0].value == 1 or row[0].value == 2]
    assert names == ['A One', 'B Two']
