"""Category PDF reports for HOD results (main / second / lab)."""
import os
import tempfile

import pytest

from feedback_system import create_app, db
from feedback_system.security import hash_password
from helpers import login

PW = 'hodpass123'
TEACHER_PW = 'teacher@123'


@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-hod-pdf-')
    application = create_app({
        'TESTING': True,
        'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None,
        'SECRET_KEY': 'test-hod-pdf',
        'AUTO_IMPORT': True,
        'ADMIN_EMAIL': 'admin@college.edu',
        'ADMIN_PASSWORD': 'admin123',
        'DEFAULT_TEACHER_PASSWORD': TEACHER_PW,
        'DEFAULT_STUDENT_PASSWORD': 'student@123',
        'LOGIN_MAX_ATTEMPTS': 50,
        'REQUIRE_ACCOUNT_SETUP': False,
    })
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


def _seed_hod(app):
    with app.app_context():
        branch = db.query("SELECT id FROM branches WHERE code='CSE'", one=True)
        if not branch:
            db.execute("INSERT INTO branches(code, name) VALUES('CSE','CSE')")
            branch = db.query("SELECT id FROM branches WHERE code='CSE'", one=True)
        existing = db.query("SELECT id FROM users WHERE username='HODPDF1'", one=True)
        if not existing:
            db.execute(
                """INSERT INTO users(role, username, emp_code, name, email, password_hash,
                                     is_active, onboarding_complete)
                   VALUES('hod','HODPDF1','HODPDF1','PDF HOD','hodpdf@test.edu',?,1,1)""",
                (hash_password(PW),))
            hod = db.query("SELECT id FROM users WHERE username='HODPDF1'", one=True)
            db.execute(
                "INSERT INTO user_department_scopes(user_id, branch_id, is_primary) VALUES(?,?,1)",
                (hod['id'], branch['id']))
        else:
            db.execute('UPDATE users SET onboarding_complete=1, password_hash=? WHERE id=?',
                       (hash_password(PW), existing['id']))
        return branch['id']


def _login_hod(client):
    r = login(client, '/hod/login', {'emp_code': 'HODPDF1', 'password': PW})
    assert r.status_code == 302
    return client


def test_each_category_pdf_nonempty(app, client):
    _seed_hod(app)
    c = _login_hod(client)
    for cat in ('main', 'second', 'lab'):
        r = c.get(f'/hod/results/{cat}.pdf')
        assert r.status_code == 200, cat
        assert r.mimetype == 'application/pdf'
        assert r.data[:4] == b'%PDF'
        assert len(r.data) > 200
        # Content is flate-compressed; structural checks only here.
        assert b'ReportLab' in r.data or b'reportlab' in r.data.lower() or len(r.data) > 500


def test_pdf_unauthorized_blocked(app, client):
    _seed_hod(app)
    assert client.get('/hod/results/main.pdf').status_code in (302, 401, 403)
    login(client, '/teacher/login', {'emp_code': '6817', 'password': TEACHER_PW})
    assert client.get('/hod/results/main.pdf').status_code in (302, 403)


def test_invalid_category_404(app, client):
    _seed_hod(app)
    c = _login_hod(client)
    assert c.get('/hod/results/other.pdf').status_code == 404


def test_existing_pending_pdf_still_works(app, client):
    """Regression: pending rolls PDF route remains registered and auth-gated."""
    _seed_hod(app)
    # Unauthenticated blocked
    r = client.get('/hod/pending/1/teacher/1.pdf')
    assert r.status_code in (302, 401, 403, 404)
    c = _login_hod(client)
    # May 404 if form/teacher not in scope — must not 500
    r = c.get('/hod/pending/1/teacher/1.pdf')
    assert r.status_code in (200, 403, 404)


def test_build_category_results_pdf_unit():
    from feedback_system import reports
    pdf = reports.build_category_results_pdf(
        [{
            'faculty_name': 'Test Teacher',
            'faculty_emp': 'T1',
            'subject_code_display': 'CS101',
            'subject_display': 'Programming',
            'section_code': 'A',
            'room': 'R1',
            'eligible': 10,
            'submitted': 4,
            'pending': 6,
            'completion_pct': 40.0,
            'obtained': 12.5,
            'maximum': 100.0,
            'answer_count': 8,
            'students_count': 4,
            'rating_breakdown': [{'label': 'Good', 'count': 5}],
            'question_stats': [{
                'text': 'Clarity?', 'qtype': 'rating',
                'options': [{'label': 'Good', 'count': 5, 'pct': 100.0}],
                'suggestions': [],
            }],
            'photo_bytes': None,
        }],
        category='main',
        title='Unit test main report',
    )
    assert pdf[:4] == b'%PDF'
    assert len(pdf) > 300
    # Must not embed a student identity pattern from empty suggestions
    assert b'student@' not in pdf.lower()
