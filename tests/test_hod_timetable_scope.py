"""HOD timetable-level scope: an HOD who claimed only some timetables of a
department must not see assignments, sections or counts of the others."""
import os
import tempfile

import pytest

from feedback_system import create_app, db
from feedback_system.security import hash_password
from helpers import login

PW = 'hodpass123'


@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-hod-ttscope-')
    yield create_app({
        'TESTING': True,
        'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None,
        'SECRET_KEY': 'test-hod-tt-scope',
        'AUTO_IMPORT': True,
        'ADMIN_EMAIL': 'admin@college.edu',
        'ADMIN_PASSWORD': 'admin123',
        'DEFAULT_TEACHER_PASSWORD': 'teacher@123',
        'DEFAULT_STUDENT_PASSWORD': 'student@123',
        'LOGIN_MAX_ATTEMPTS': 50,
        'REQUIRE_ACCOUNT_SETUP': False,
    })


def _seed(app, claimed_codes):
    """HOD for CSE Sem 3 that owns only the timetables in *claimed_codes*."""
    with app.app_context():
        cse = db.query("SELECT id FROM branches WHERE code='CSE'", one=True)
        rows = db.query(
            """SELECT t.id, t.code, t.semester_id, t.academic_year_id
               FROM timetables t WHERE t.branch_id=? AND t.code IN ({})""".format(
                ','.join('?' * len(claimed_codes))),
            (cse['id'], *claimed_codes))
        assert rows, 'expected imported CSE timetables'
        existing = db.query("SELECT id FROM users WHERE username='HODTT1'", one=True)
        if existing:
            uid = existing['id']
            db.execute('DELETE FROM hod_allocations WHERE hod_user_id=?', (uid,))
        else:
            uid = db.insert_id(
                """INSERT INTO users(role, username, emp_code, name, email, password_hash,
                                     is_active, onboarding_complete)
                   VALUES('hod','HODTT1','HODTT1','TT HOD','hodtt@test.edu',?,1,1)""",
                (hash_password(PW),))
        for r in rows:
            db.execute(
                """INSERT INTO hod_allocations(hod_user_id, academic_year_id, branch_id,
                                               semester_id, timetable_id)
                   VALUES(?,?,?,?,?)""",
                (uid, r['academic_year_id'], cse['id'], r['semester_id'], r['id']))
        db.get_db().commit()
        return uid, [r['id'] for r in rows]


def test_main_list_and_dashboard_only_claimed_timetable(app):
    _uid, tt_ids = _seed(app, ['T1-General'])
    client = app.test_client()
    r = login(client, '/hod/login', {'emp_code': 'HODTT1', 'password': PW})
    assert r.status_code == 302

    data = client.get('/hod/feedback/main/api/sessions').get_json()
    assert data['ok']
    sections = {s['section_code']
                for sub in data['subjects']
                for t in sub['teachers'] for s in t['sections']}
    assert sections == {'T1-General'}

    ai06 = [s for s in data['subjects'] if s['subject_code'] == '2501AI06']
    assert ai06, 'Data Analysis Essentials should be listed for T1'
    assert len(ai06[0]['teachers']) == 3
    assert ai06[0]['session_count'] == 3
    assert ai06[0]['eligible_total'] == 212

    from feedback_system.hod import _category_overview
    with app.app_context():
        ov = {o['key']: o for o in _category_overview(
            [db.query("SELECT id FROM branches WHERE code='CSE'", one=True)['id']], tt_ids)}
    assert ov['main']['assignments'] == 19


def test_session_in_other_timetable_is_rejected(app):
    _seed(app, ['T1-General'])
    with app.app_context():
        other = db.query(
            """SELECT cs.id FROM class_sessions cs JOIN timetables t ON t.id=cs.timetable_id
               WHERE t.code='T2-General' AND cs.is_deleted=0
                 AND cs.teacher_user_id IS NOT NULL LIMIT 1""", one=True)
    client = app.test_client()
    login(client, '/hod/login', {'emp_code': 'HODTT1', 'password': PW})
    r = client.get(f"/hod/feedback/main/api/preview?session_id={other['id']}")
    assert r.status_code == 403


def test_hod_without_allocations_keeps_branch_scope(app):
    from feedback_system.security import scoped_timetable_ids
    with app.app_context():
        db.execute("DELETE FROM hod_allocations WHERE hod_user_id=(SELECT id FROM users WHERE username='HODTT1')")
        db.get_db().commit()
        uid = db.query("SELECT id FROM users WHERE username='HODTT1'", one=True)['id']
        from flask import g
        with app.test_request_context():
            assert scoped_timetable_ids({'id': uid, 'role': 'hod'}) is None


# ---- Main create flow API (shared sets, phase, per-form questions) ----
def _hod_client(app):
    _seed(app, ['T1-General'])
    client = app.test_client()
    assert login(client, '/hod/login', {'emp_code': 'HODTT1', 'password': PW}).status_code == 302
    page = client.get('/hod/feedback/main/create').get_data(as_text=True)
    import re
    token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
    return client, token, page


def _t1_session(app, subject='2501AI06'):
    with app.app_context():
        return db.query(
            """SELECT cs.id FROM class_sessions cs JOIN timetables t ON t.id=cs.timetable_id
               WHERE t.code='T1-General' AND cs.subject_code=? AND cs.is_deleted=0
                 AND cs.teacher_user_id IS NOT NULL ORDER BY cs.id LIMIT 1""",
            (subject,), one=True)['id']


def test_create_page_preselects_session_and_rejects_foreign_one(app):
    client, _t, _p = _hod_client(app)
    sid = _t1_session(app)
    html = client.get(f'/hod/feedback/main/create?session_id={sid}').get_data(as_text=True)
    assert f'data-preselect="{sid}"' in html
    with app.app_context():
        other = db.query(
            """SELECT cs.id FROM class_sessions cs JOIN timetables t ON t.id=cs.timetable_id
               WHERE t.code='T2-General' AND cs.is_deleted=0 LIMIT 1""", one=True)['id']
    html = client.get(f'/hod/feedback/main/create?session_id={other}').get_data(as_text=True)
    assert 'data-preselect=""' in html


def test_create_with_shared_set_phase_exclusions_and_extras(app):
    client, token, _p = _hod_client(app)
    sid = _t1_session(app)
    with app.app_context():
        db.execute('UPDATE feedback_forms SET is_deleted=1 WHERE session_id=?', (sid,))
        db.get_db().commit()
    sets = client.get('/hod/feedback/main/api/question-sets').get_json()['sets']
    shared = [s for s in sets if s['name'].startswith('Phase 1')][0]
    assert isinstance(shared['id'], str)               # shared sets use string keys
    n = len(shared['questions'])
    r = client.post('/hod/feedback/main/api/create', headers={'X-CSRF-Token': token}, json={
        'session_ids': [sid], 'question_set_id': shared['id'],
        'exclude_questions': [0], 'phase': 'phase1', 'auth_required': False,
        'extra_questions': [{'text': 'Any lab feedback?', 'qtype': 'text'},
                            {'text': '  ', 'qtype': 'rating'}],
        'publish': False})
    assert r.status_code == 200, r.get_json()
    fid = r.get_json()['created'][0]['form_id']
    with app.app_context():
        from feedback_system import services
        form = services.get_form(fid)
        assert form['phase'] == 'phase1' and form['title'] == 'Phase 1'
        assert not form['auth_required']
        qs = services.form_questions(fid)
        assert len(qs) == n - 1 + 1                     # one left out, one extra (blank ignored)
        assert qs[-1]['text'] == 'Any lab feedback?'
    # same phase again for the same section is refused
    r2 = client.post('/hod/feedback/main/api/create', headers={'X-CSRF-Token': token}, json={
        'session_ids': [sid], 'question_set_id': shared['id'], 'phase': 'phase1'})
    assert r2.status_code in (207, 409)
    assert r2.get_json()['errors']


def test_cannot_leave_out_every_question(app):
    client, token, _p = _hod_client(app)
    sid = _t1_session(app, '2501CS08')
    sets = client.get('/hod/feedback/main/api/question-sets').get_json()['sets']
    shared = [s for s in sets if s['name'].startswith('Phase 1')][0]
    r = client.post('/hod/feedback/main/api/create', headers={'X-CSRF-Token': token}, json={
        'session_ids': [sid], 'question_set_id': shared['id'],
        'exclude_questions': list(range(len(shared['questions'])))})
    assert r.status_code == 400


# ---- Regressions found by the browser walk-through of the create page ----
def test_create_page_embeds_question_sets_as_valid_json_attribute(app):
    """Phase 2 has "teacher's" in a question; the JSON sits in an HTML attribute and an
    unescaped apostrophe used to truncate it, leaving the page with no question sets."""
    import json
    from html.parser import HTMLParser
    client, _t, page = _hod_client(app)

    class P(HTMLParser):
        attrs = {}

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            if d.get('id') == 'hfCreate':
                P.attrs = d
    P().feed(page)
    sets = json.loads(P.attrs['data-question-sets'])
    names = {s['name'] for s in sets}
    assert {'Phase 1 Questions', 'Phase 2 Questions'} <= names
    assert any("teacher's" in q['text'] for s in sets for q in s['questions'])
    assert json.loads(P.attrs['data-presets'])


def test_create_page_buttons_do_not_use_global_history_back_attribute(app):
    """app.js turns every [data-back] element into history.back(); the wizard's own
    Back buttons must not carry it or they leave the page."""
    _c, _t, page = _hod_client(app)
    assert 'data-back' not in page.replace('data-hf-back', '')
    assert page.count('data-hf-back=') == 3


def test_preview_includes_student_names_and_follows_requested_order(app):
    client, _t, _p = _hod_client(app)
    sid = _t1_session(app)
    with app.app_context():
        first = db.query(
            """SELECT u.id FROM session_students ss JOIN users u ON u.id=ss.student_user_id
               WHERE ss.session_id=? ORDER BY u.roll LIMIT 1""", (sid,), one=True)['id']
        db.execute("UPDATE users SET name='Test Student' WHERE id=?", (first,))
        db.get_db().commit()
    data = client.get(f'/hod/feedback/main/api/preview?session_id={sid}').get_json()
    st = data['previews'][0]['students']
    assert st[0]['name'] == 'Test Student' and st[0]['roll']
    assert data['previews'][0]['eligible_count'] == len(st)
