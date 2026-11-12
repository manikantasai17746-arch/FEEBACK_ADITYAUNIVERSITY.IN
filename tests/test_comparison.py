"""Subject-wise multi-round teacher feedback comparison (HOD + Dean pages, PDF)."""
import os
import tempfile

import pytest

from feedback_system import analytics, create_app, dean, hod, scoring
from feedback_system import db as db_module
from feedback_system.security import hash_password


def _row(form_id, subject, score, published, status='closed', teacher=7, title=None):
    return {
        'form_id': form_id, 'title': title or f'Round {form_id}', 'status': status,
        'published_at': published, 'subject_code': subject,
        'subject_name': f'{subject} name', 'teacher_user_id': teacher,
        'teacher_name': 'Dr. Rao', 'teacher_emp_code': 'T7',
        'overall_score': score, 'valid_responses': 12,
    }


ROWS = [
    # Subject A: three rounds, supplied out of order.
    _row(3, 'A', 90.0, '2026-10-20 10:00:00'),
    _row(1, 'A', 70.0, '2026-08-01 10:00:00'),
    _row(2, 'A', 80.0, '2026-09-10 10:00:00'),
    # Subject B: a single round, must never be mixed into A.
    _row(4, 'B', 60.0, '2026-09-15 10:00:00'),
    # Not comparable: still open, or no score yet.
    _row(5, 'A', 99.0, '2026-11-01 10:00:00', status='open'),
    _row(6, 'B', None, '2026-11-02 10:00:00'),
]


@pytest.fixture()
def fake_scores(monkeypatch):
    def fake(branch_ids, teacher_user_id=None, **kw):
        # Like the real query: only the requested teacher's forms come back.
        return [dict(r) for r in ROWS
                if teacher_user_id is None or r['teacher_user_id'] == teacher_user_id]
    monkeypatch.setattr(scoring, 'faculty_scores_for_branches', fake)


def test_subjects_are_kept_separate_and_ordered_by_date(fake_scores):
    (profile,) = analytics.build_teacher_profiles([1])
    assert profile['subject_count'] == 2
    a, b = profile['subjects']
    assert (a['subject_code'], b['subject_code']) == ('A', 'B')
    assert [r['score'] for r in a['rounds']] == [70.0, 80.0, 90.0]
    assert [r['step_change'] for r in a['rounds']] == [None, 10.0, 10.0]
    assert a['net_change'] == 20.0 and a['net_trend'] == 'Improved'
    assert a['average_score'] == 80.0
    # Open / unscored rounds are ignored, and B is not affected by A.
    assert a['round_count'] == 3 and b['round_count'] == 1
    assert b['average_score'] == 60.0
    assert b['net_change'] is None and b['net_trend'] == '—'


def test_band_uses_the_performance_page_bands(fake_scores):
    a, b = analytics.build_teacher_profiles([1])[0]['subjects']
    assert a['band']['label'] == 'Above 75 – 80%'      # 80.0 is the top of this band
    assert b['band']['label'] == '75% or less'


def test_declined_and_no_change_trends():
    assert analytics.trend_label(-3.0) == 'Declined'
    assert analytics.trend_label(0.0) == 'No change'
    assert analytics.trend_label(None) == '—'


def test_search_and_empty_scope(fake_scores):
    assert analytics.build_teacher_profiles([1], q='no such person') == []
    assert len(analytics.build_teacher_profiles([1], q='t7')) == 1
    assert analytics.build_teacher_profiles([]) == []


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-comparison-')
    return create_app({
        'TESTING': True, 'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None, 'SECRET_KEY': 'test-secret-comparison',
        'AUTO_IMPORT': False, 'ADMIN_EMAIL': 'admin@college.edu',
        'ADMIN_PASSWORD': 'admin123', 'REQUIRE_ACCOUNT_SETUP': False,
    })


def _user(role, code):
    db_module.execute(
        """INSERT INTO users(role, username, emp_code, name, email, password_hash,
                             is_active, onboarding_complete)
           VALUES(?,?,?,?,?,?,1,1)""",
        (role, code, code, 'Dr. Rao' if role == 'teacher' else code,
         f'{code.lower()}@t.edu', hash_password('pass12345')))
    return db_module.query('SELECT id FROM users WHERE username=?', (code,), one=True)['id']


@pytest.mark.parametrize('role, module, scope_fn', [
    ('hod', hod, '_hod_branches'),
    ('dean', dean, '_dean_branches'),
])
def test_pages_and_pdf_render(app, monkeypatch, fake_scores, role, module, scope_fn):
    monkeypatch.setattr(module, scope_fn, lambda user: [{'id': 1, 'code': 'X', 'name': 'X'}])
    with app.app_context():
        uid = _user(role, f'CMP_{role}')
        teacher_id = _user('teacher', f'CMP_T_{role}')
    # The stubbed scores use teacher id 7; point the stub rows at the real teacher.
    for r in ROWS:
        r['teacher_user_id'] = teacher_id

    client = app.test_client()
    with client.session_transaction() as sess:
        sess['uid'] = uid

    page = client.get(f'/{role}/comparison')
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'Dr. Rao' in html and 'A name' in html and 'B name' in html

    detail = client.get(f'/{role}/comparison/teacher/{teacher_id}')
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert 'Round 1' in html and 'Improved' in html
    assert f'/{role}/comparison/pdf/teacher' in html

    for url in (f'/{role}/comparison/pdf/teacher?teacher_id={teacher_id}',
                f'/{role}/comparison/pdf/all'):
        pdf = client.get(url)
        assert pdf.status_code == 200
        assert pdf.mimetype == 'application/pdf'
        assert pdf.data.startswith(b'%PDF')

    assert client.get(f'/{role}/comparison/pdf/bogus').status_code == 404
    assert client.get(f'/{role}/comparison/pdf/teacher').status_code == 400
    assert client.get(f'/{role}/comparison/teacher/999999').status_code == 404


def test_other_roles_cannot_open_the_pages(app):
    client = app.test_client()
    assert client.get('/hod/comparison').status_code in (302, 401, 403)
    assert client.get('/dean/comparison').status_code in (302, 401, 403)
