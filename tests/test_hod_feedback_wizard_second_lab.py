"""Second and Lab Faculty create pages use the same wizard as Main
(Select -> Preview -> Questions -> Confirm) and accept the same options."""
import os
import re
import tempfile

import pytest

from feedback_system import create_app, db, services
from helpers import login

from test_hod_feedback_lab import _login_hod as _login_lab_hod
from test_hod_feedback_lab import _seed_hod as _seed_lab_hod
from test_hod_feedback_lab import _session_with_lab
from test_hod_feedback_second_and_results import _ensure_sfa
from test_hod_feedback_second_and_results import _login_hod as _login_sec_hod
from test_hod_feedback_second_and_results import _seed_hod as _seed_sec_hod


@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-hod-wizard-')
    yield create_app({
        'TESTING': True,
        'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None,
        'SECRET_KEY': 'test-hod-wizard',
        'AUTO_IMPORT': True,
        'ADMIN_EMAIL': 'admin@college.edu',
        'ADMIN_PASSWORD': 'admin123',
        'DEFAULT_TEACHER_PASSWORD': 'teacher@123',
        'DEFAULT_STUDENT_PASSWORD': 'student@123',
        'LOGIN_MAX_ATTEMPTS': 50,
        'REQUIRE_ACCOUNT_SETUP': False,
    })


@pytest.fixture()
def client(app):
    return app.test_client()


def _csrf(html):
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def _set_id(app, hod_id):
    with app.app_context():
        return db.query('SELECT id FROM question_sets WHERE owner_user_id=? AND is_deleted=0 LIMIT 1',
                        (hod_id,), one=True)['id']


def _wipe(app, category, **where):
    with app.app_context():
        if category == 'lab':
            db.execute("UPDATE feedback_forms SET is_deleted=1 WHERE session_id=? AND category='lab'",
                       (where['session_id'],))
        else:
            db.execute("UPDATE feedback_forms SET is_deleted=1 "
                       "WHERE second_faculty_assignment_id=? AND category='second'",
                       (where['assignment_id'],))


# --------------------------------------------------------------------------- pages
def test_second_wizard_page_has_all_four_steps_and_options(app, client):
    _seed_sec_hod(app)
    c = _login_sec_hod(client)
    html = c.get('/hod/feedback/second/create').get_data(as_text=True)
    assert 'id="hfCreate"' in html and 'data-kind="second"' in html
    for needle in ('Select', 'Preview', 'Questions', 'Confirm', 'Main faculty', 'Second faculty',
                   'id="hfSubjectList"', 'id="hfPhase"', 'id="hfAddRating"', 'id="hfAddText"',
                   'id="hfAuth"', 'id="hfEditSets"', 'hfGoBottom'):
        assert needle in html, needle
    assert 'hod_feedback_main.js' in html


def test_lab_wizard_page_has_all_four_steps_and_options(app, client):
    _seed_lab_hod(app)
    c = _login_lab_hod(client)
    html = c.get('/hod/feedback/lab/create').get_data(as_text=True)
    assert 'id="hfCreate"' in html and 'data-kind="lab"' in html
    for needle in ('Lab faculty', 'Live lab forms', 'id="hfSubjectList"', 'id="hfPhase"',
                   'id="hfAddRating"', 'id="hfAuth"', 'id="hfEditSets"'):
        assert needle in html, needle


def test_main_wizard_page_unchanged(app, client):
    _seed_lab_hod(app)
    c = _login_lab_hod(client)
    html = c.get('/hod/feedback/main/create').get_data(as_text=True)
    assert 'id="hfCreate"' in html
    assert 'data-kind' not in html          # Main keeps its own template


def test_list_pages_open_the_wizard(app, client):
    seed = _seed_sec_hod(app)
    aids = _ensure_sfa(app, seed['branch_id'], 1)
    if not aids:
        pytest.skip('need 1 SFA')
    c = _login_sec_hod(client)
    html = c.get('/hod/feedback/second').get_data(as_text=True)
    assert '/hod/feedback/second/create"' in html
    assert f'/hod/feedback/second/create?assignment_id={aids[0]}' in html
    assert '/hod/forms/new' not in html

    seed2 = _seed_lab_hod(app)
    sess = _session_with_lab(app, seed2['branch_id'], 'available')
    if not sess:
        pytest.skip('no session')
    c2 = app.test_client()
    _login_lab_hod(c2)
    lab_html = c2.get('/hod/feedback/lab').get_data(as_text=True)
    assert '/hod/feedback/lab/create"' in lab_html
    assert f'/hod/feedback/lab/create?session_id={sess["id"]}' in lab_html
    assert '/hod/forms/new' not in lab_html


def test_preselect_only_when_in_scope(app, client):
    seed = _seed_sec_hod(app)
    aids = _ensure_sfa(app, seed['branch_id'], 1)
    if not aids:
        pytest.skip('need 1 SFA')
    c = _login_sec_hod(client)
    ok = c.get(f'/hod/feedback/second/create?assignment_id={aids[0]}').get_data(as_text=True)
    assert f'data-preselect="{aids[0]}"' in ok
    bad = c.get('/hod/feedback/second/create?assignment_id=99999999').get_data(as_text=True)
    assert 'data-preselect=""' in bad


# --------------------------------------------------------------------------- Second API
def test_second_api_accepts_wizard_options(app, client):
    seed = _seed_sec_hod(app)
    aids = _ensure_sfa(app, seed['branch_id'], 1)
    if not aids:
        pytest.skip('need 1 SFA')
    _wipe(app, 'second', assignment_id=aids[0])
    qsid = _set_id(app, seed['hod_id'])
    c = _login_sec_hod(client)
    csrf = _csrf(c.get('/hod/feedback/second/create').get_data(as_text=True))
    r = c.post('/hod/feedback/second/api/create', headers={'X-CSRF-Token': csrf}, json={
        'assignment_ids': [aids[0]], 'question_set_id': qsid, 'publish': False,
        'phase': 'phase1', 'auth_required': False,
        'extra_questions': [{'text': 'Any suggestion?', 'qtype': 'text'}]})
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    fid = r.get_json()['created'][0]['form_id']
    with app.app_context():
        form = services.get_form(fid)
        assert form['category'] == 'second'
        assert form['phase'] == 'phase1'
        assert form['title'] == services.PHASE_LABELS['phase1']
        texts = [q['text'] for q in services.form_questions(fid)]
        assert 'Any suggestion?' in texts


def test_second_api_rejects_form_with_no_questions_left(app, client):
    seed = _seed_sec_hod(app)
    aids = _ensure_sfa(app, seed['branch_id'], 1)
    if not aids:
        pytest.skip('need 1 SFA')
    _wipe(app, 'second', assignment_id=aids[0])
    qsid = _set_id(app, seed['hod_id'])
    c = _login_sec_hod(client)
    csrf = _csrf(c.get('/hod/feedback/second/create').get_data(as_text=True))
    r = c.post('/hod/feedback/second/api/create', headers={'X-CSRF-Token': csrf}, json={
        'assignment_ids': [aids[0]], 'question_set_id': qsid, 'publish': False,
        'exclude_questions': list(range(50))})
    assert r.status_code == 400
    assert not r.get_json()['ok']


def test_second_preview_has_labels_for_the_wizard(app, client):
    seed = _seed_sec_hod(app)
    aids = _ensure_sfa(app, seed['branch_id'], 1)
    if not aids:
        pytest.skip('need 1 SFA')
    c = _login_sec_hod(client)
    d = c.get(f'/hod/feedback/second/api/preview?assignment_id={aids[0]}').get_json()
    assert d['ok']
    p = d['previews'][0]
    assert p['assignment_id'] == aids[0]
    assert p['teacher_name']
    assert 'section_code' in p
    # students are only for the authorised eligibility preview
    assert all(set(s) <= {'roll', 'name'} for s in p['students'])


# --------------------------------------------------------------------------- Lab API
def test_lab_api_accepts_wizard_options_and_phase_rule(app, client):
    seed = _seed_lab_hod(app)
    sess = _session_with_lab(app, seed['branch_id'], 'available')
    if not sess:
        pytest.skip('no session')
    _wipe(app, 'lab', session_id=sess['id'])
    qsid = _set_id(app, seed['hod_id'])
    c = _login_lab_hod(client)
    csrf = _csrf(c.get('/hod/feedback/lab/create').get_data(as_text=True))
    r = c.post('/hod/feedback/lab/api/create', headers={'X-CSRF-Token': csrf}, json={
        'session_ids': [sess['id']], 'question_set_id': qsid, 'publish': False,
        'phase': 'phase1', 'auth_required': False,
        'extra_questions': [{'text': 'Lab rating?', 'qtype': 'rating'}]})
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    fid = r.get_json()['created'][0]['form_id']
    with app.app_context():
        form = services.get_form(fid)
        assert form['category'] == 'lab' and form['phase'] == 'phase1'
        assert 'Lab rating?' in [q['text'] for q in services.form_questions(fid)]
    # Phase 2 cannot start while the Phase 1 lab form is still draft/open
    r2 = c.post('/hod/feedback/lab/api/create', headers={'X-CSRF-Token': csrf}, json={
        'session_ids': [sess['id']], 'question_set_id': qsid, 'publish': False, 'phase': 'phase2'})
    assert r2.status_code in (207, 409)
    assert r2.get_json().get('errors')


def test_lab_api_publish_still_needs_a_deadline(app, client):
    seed = _seed_lab_hod(app)
    sess = _session_with_lab(app, seed['branch_id'], 'available')
    if not sess:
        pytest.skip('no session')
    qsid = _set_id(app, seed['hod_id'])
    c = _login_lab_hod(client)
    csrf = _csrf(c.get('/hod/feedback/lab/create').get_data(as_text=True))
    r = c.post('/hod/feedback/lab/api/create', headers={'X-CSRF-Token': csrf}, json={
        'session_ids': [sess['id']], 'question_set_id': qsid, 'publish': True})
    assert r.status_code == 400


def test_lab_api_still_blocks_sessions_without_lab(app, client):
    seed = _seed_lab_hod(app)
    sess = _session_with_lab(app, seed['branch_id'], 'none')
    if not sess:
        pytest.skip('no session')
    qsid = _set_id(app, seed['hod_id'])
    c = _login_lab_hod(client)
    csrf = _csrf(c.get('/hod/feedback/lab/create').get_data(as_text=True))
    r = c.post('/hod/feedback/lab/api/create', headers={'X-CSRF-Token': csrf}, json={
        'session_ids': [sess['id']], 'question_set_id': qsid, 'publish': False})
    assert r.status_code in (207, 409)
    assert not r.get_json()['created']
