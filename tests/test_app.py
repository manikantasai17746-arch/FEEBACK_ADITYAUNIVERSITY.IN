"""End-to-end and access-control tests for the upgraded feedback system."""
import re

import pytest

from feedback_system import db, services

from helpers import (eligible_rolls, find_session, get_csrf, login, login_student,
                     make_form, publish_form, question_ids, set_status,
                     submit_feedback, token_from)


def in_app(app, fn, *a, **k):
    with app.app_context():
        return fn(*a, **k)


def response_count(app, form_id):
    return in_app(app, services.response_count, form_id)


# --------------------------------------------------------------------------- #
# Schema and import integrity
# --------------------------------------------------------------------------- #
def test_schema_preserved_and_migrated(app):
    with app.app_context():
        cols = {r['name'] for r in db.query('PRAGMA table_info(assignments)')}
        # original columns preserved
        assert {'id', 'roll', 'dept', 'course_code', 'course_name',
                'emp_code', 'emp_name'} <= cols
        # new context columns added by migration
        assert {'timetable', 'period_no', 'semester', 'academic_year'} <= cols
        assert db.query('SELECT name FROM sqlite_master WHERE name=?', ('feedback',), one=True)
        ver = db.query('SELECT MAX(version) v FROM feedback_system_schema_migrations', one=True)['v']
        assert ver >= 4


def test_import_built_full_academic_structure(app):
    with app.app_context():
        stats = {
            'assignments': db.query('SELECT COUNT(*) c FROM assignments', one=True)['c'],
            'branches': db.query('SELECT COUNT(*) c FROM branches', one=True)['c'],
            'timetables': db.query('SELECT COUNT(*) c FROM timetables', one=True)['c'],
            'sessions': db.query('SELECT COUNT(*) c FROM class_sessions', one=True)['c'],
            'students': db.query("SELECT COUNT(*) c FROM users WHERE role='student'", one=True)['c'],
        }
    assert stats['assignments'] == 38341
    assert stats['branches'] == 16
    assert stats['timetables'] == 43
    assert stats['sessions'] == 567
    assert stats['students'] == 5022


def test_anonymous_view_has_no_student_linkage(app):
    with app.app_context():
        cols = {r['name'] for r in db.query('PRAGMA table_info(anonymous_answers)')}
        assert 'student_user_id' not in cols
        assert 'roll' not in cols
        assert 'submission_id' not in cols


# --------------------------------------------------------------------------- #
# Authentication and RBAC
# --------------------------------------------------------------------------- #
def test_admin_area_requires_login(client):
    r = client.get('/admin/')
    assert r.status_code == 302
    assert '/admin/login' in r.headers['Location']


def test_admin_login_rejects_bad_credentials(client):
    r = login(client, '/admin/login', {'email': 'admin@college.edu', 'password': 'wrong'})
    assert r.status_code == 200
    assert 'Invalid credentials' in r.get_data(as_text=True)


def test_admin_login_success(client):
    r = login(client, '/admin/login', {'email': 'admin@college.edu', 'password': 'admin123'})
    assert r.status_code == 302
    assert client.get('/admin/').status_code == 200


def test_student_cannot_access_admin(app):
    c = app.test_client()
    rolls = eligible_rolls(app, find_session(app, '6817', '2501IT07'))
    login_student(c, rolls[0])
    assert c.get('/admin/').status_code == 403
    assert c.get('/admin/teachers').status_code == 403


def test_teacher_cannot_access_admin(teacher):
    assert teacher.get('/admin/').status_code == 403


def test_unauthenticated_teacher_redirected(client):
    r = client.get('/teacher/')
    assert r.status_code == 302
    assert '/teacher/login' in r.headers['Location']


def test_csrf_is_enforced(admin):
    # POST without a CSRF token is rejected.
    r = admin.post('/admin/forms/new', data={})
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# Form builder and publication
# --------------------------------------------------------------------------- #
def test_save_form_without_session_is_rejected(admin):
    tok = get_csrf(admin.get('/admin/forms/new').get_data(as_text=True))
    r = admin.post('/admin/forms/save', json={'title': 'x', 'session_id': None, 'questions': []},
                   headers={'X-CSRF-Token': tok})
    assert r.status_code == 400
    assert r.get_json()['ok'] is False


def test_publish_validation_blocks_empty_question(admin, app):
    sid = find_session(app, '6817', '2501IT07')
    form_id = make_form(admin, sid, questions=[
        {'text': '', 'qtype': 'rating', 'is_required': True,
         'options': [{'label': 'A', 'value': 1}]}])
    r = set_status(admin, form_id, 'open')
    assert r.status_code == 302  # redirected back with errors
    with app.app_context():
        assert db.query('SELECT status FROM feedback_forms WHERE id=?', (form_id,),
                        one=True)['status'] == 'draft'


def test_draft_is_hidden_from_students(admin, app):
    sid = find_session(app, '6817', '2501IT07')
    form_id = make_form(admin, sid)  # saved as draft only
    c = app.test_client()
    roll = eligible_rolls(app, sid)[0]
    login_student(c, roll)
    html = c.get(f'/student/form/{form_id}').get_data(as_text=True)
    # draft form should not be reachable
    assert c.get(f'/student/form/{form_id}').status_code == 302


# --------------------------------------------------------------------------- #
# Student submission flow
# --------------------------------------------------------------------------- #
def test_student_submits_feedback(admin, app):
    sid = find_session(app, '6817', '2501IT07')
    form_id = make_form(admin, sid)
    publish_form(admin, form_id)
    roll = eligible_rolls(app, sid)[0]

    c = app.test_client()
    login_student(c, roll)
    html = c.get(f'/student/form/{form_id}').get_data(as_text=True)
    assert 'How well does the teacher explain?' in html
    rating_qid, text_qid, opt_id = question_ids(html)
    r = submit_feedback(c, form_id, rating_qid, text_qid, opt_id, 'Please share the notes.')
    assert r.status_code == 302
    assert response_count(app, form_id) == 1
    # completion tracking is available to the admin
    with app.app_context():
        assert db.query('SELECT COUNT(*) c FROM submissions WHERE form_id=?', (form_id,),
                        one=True)['c'] == 1


def test_duplicate_submission_blocked(admin, app):
    sid = find_session(app, '6817', '2501IT07')
    form_id = make_form(admin, sid)
    publish_form(admin, form_id)
    roll = eligible_rolls(app, sid)[0]
    c = app.test_client()
    login_student(c, roll)
    html = c.get(f'/student/form/{form_id}').get_data(as_text=True)
    rating_qid, text_qid, opt_id = question_ids(html)
    submit_feedback(c, form_id, rating_qid, text_qid, opt_id)
    # second attempt
    html2 = c.get(f'/student/form/{form_id}').get_data(as_text=True)
    assert 'already submitted' in html2.lower()
    submit_feedback(c, form_id, rating_qid, text_qid, opt_id)
    assert response_count(app, form_id) == 1


def test_required_question_validation(admin, app):
    sid = find_session(app, '6817', '2501IT07')
    form_id = make_form(admin, sid)
    publish_form(admin, form_id)
    roll = eligible_rolls(app, sid)[0]
    c = app.test_client()
    login_student(c, roll)
    html = c.get(f'/student/form/{form_id}').get_data(as_text=True)
    tok = get_csrf(html)
    # submit without answering the required rating question
    r = c.post(f'/student/form/{form_id}/submit', data={'_csrf': tok})
    assert r.status_code == 200
    assert 'Please answer' in r.get_data(as_text=True)
    assert response_count(app, form_id) == 0


def test_closed_form_rejects_submission(admin, app):
    sid = find_session(app, '6817', '2501IT07')
    form_id = make_form(admin, sid)
    publish_form(admin, form_id)
    set_status(admin, form_id, 'closed')
    roll = eligible_rolls(app, sid)[0]
    c = app.test_client()
    login_student(c, roll)
    html = c.get(f'/student/form/{form_id}').get_data(as_text=True)
    assert 'no longer accepting' in html.lower()
    tok = get_csrf(html)
    r = c.post(f'/student/form/{form_id}/submit', data={'_csrf': tok})
    assert r.status_code in (200, 302)
    assert response_count(app, form_id) == 0


def test_ineligible_student_cannot_see_or_submit(app, admin):
    sid = find_session(app, '6817', '2501IT07')
    form_id = make_form(admin, sid)
    publish_form(admin, form_id)
    # a student from a different timetable / teacher
    other = in_app(app, lambda: db.query(
        """SELECT u.roll FROM users u WHERE u.role='student' AND u.roll NOT IN
           (SELECT u2.roll FROM session_students ss JOIN users u2 ON u2.id=ss.student_user_id
            WHERE ss.session_id=?) LIMIT 1""", (sid,), one=True)['roll'])
    c = app.test_client()
    login_student(c, other)
    r = c.get(f'/student/form/{form_id}')
    assert r.status_code == 302  # not available to them
    assert response_count(app, form_id) == 0


# --------------------------------------------------------------------------- #
# Per-period isolation (the critical business rule)
# --------------------------------------------------------------------------- #
def test_feedback_is_isolated_per_class_period(admin, app):
    adsa = find_session(app, '6817', '2501CS10')   # Lova's ADSA session
    ase = find_session(app, '6817', '2501IT07')     # Lova's ASE session
    assert adsa and ase and adsa != ase

    adsa_students = set(eligible_rolls(app, adsa))
    ase_students = set(eligible_rolls(app, ase))
    shared = sorted(adsa_students & ase_students)   # students taught both by Lova

    f_adsa = make_form(admin, adsa, title='ADSA feedback')
    publish_form(admin, f_adsa)
    roll = shared[0] if shared else sorted(adsa_students)[0]

    c = app.test_client()
    login_student(c, roll)
    html = c.get(f'/student/form/{f_adsa}').get_data(as_text=True)
    rq, tq, oid = question_ids(html)
    submit_feedback(c, f_adsa, rq, tq, oid, 'Great ADSA class')
    assert response_count(app, f_adsa) == 1

    # A separate form for the SAME teacher's other subject must stay empty.
    f_ase = make_form(admin, ase, title='ASE feedback')
    with app.app_context():
        assert services.response_count(f_ase) == 0
        assert services.suggestions_paged(f_adsa)['comments'] == ['Great ADSA class']
        assert services.suggestions_paged(f_ase)['comments'] == []
        # the stored submission is bound to the ADSA session only
        row = db.query('SELECT session_id FROM submissions WHERE form_id=?', (f_adsa,), one=True)
        assert row['session_id'] == adsa
        assert row['session_id'] != ase


# --------------------------------------------------------------------------- #
# Tracking
# --------------------------------------------------------------------------- #
def test_tracking_counts_are_correct(admin, client, app):
    sid = find_session(app, '6817', '2501IT07')
    form_id = make_form(admin, sid)
    publish_form(admin, form_id)
    students = eligible_rolls(app, sid)
    # submit for exactly two students
    for roll in students[:2]:
        c = app.test_client()
        login_student(c, roll)
        html = c.get(f'/student/form/{form_id}').get_data(as_text=True)
        rq, tq, oid = question_ids(html)
        submit_feedback(c, form_id, rq, tq, oid)

    resp = admin.get(f'/admin/tracking/rolls?form_id={form_id}&which=submitted').get_json()
    assert resp['count'] == 2
    assert set(resp['rolls']) == set(students[:2])
    resp2 = admin.get(f'/admin/tracking/rolls?form_id={form_id}&which=not_submitted').get_json()
    assert resp2['count'] == len(students) - 2
    assert students[0] not in resp2['rolls']


def test_tracking_csv_export(admin):
    r = admin.get('/admin/tracking/export.csv')
    assert r.status_code == 200
    assert r.mimetype == 'text/csv'
    assert 'Completion %' in r.get_data(as_text=True)


# --------------------------------------------------------------------------- #
# Publication gating and teacher privacy
# --------------------------------------------------------------------------- #
def test_teacher_cannot_see_unpublished_results(app):
    admin_c = app.test_client()
    login(admin_c, '/admin/login', {'email': 'admin@college.edu', 'password': 'admin123'})
    sid = find_session(app, '6817', '2501IT07')
    form_id = make_form(admin_c, sid)
    publish_form(admin_c, form_id)          # form open, results NOT published

    teacher_c = app.test_client()
    login(teacher_c, '/teacher/login', {'emp_code': '6817', 'password': 'teacher@123'})
    assert teacher_c.get(f'/teacher/results/{form_id}').status_code == 403


def test_teacher_sees_published_results_and_stays_anonymous(admin, app):
    sid = find_session(app, '6817', '2501IT07')
    form_id = make_form(admin, sid)
    publish_form(admin, form_id)
    students = eligible_rolls(app, sid)
    roll = students[0]
    c = app.test_client()
    login_student(c, roll)
    html = c.get(f'/student/form/{form_id}').get_data(as_text=True)
    rq, tq, oid = question_ids(html)
    submit_feedback(c, form_id, rq, tq, oid, 'The lectures are clear.')

    tok = token_from(admin, f'/admin/results/{form_id}')
    admin.post(f'/admin/results/{form_id}/publish', data={'_csrf': tok})

    t = app.test_client()
    login(t, '/teacher/login', {'emp_code': '6817', 'password': 'teacher@123'})
    page = t.get(f'/teacher/results/{form_id}').get_data(as_text=True)
    assert 'How well does the teacher explain?' in page
    assert 'The lectures are clear.' in page
    # anonymity: no roll number from the eligible population appears
    assert roll not in page
    for r in students:
        assert r not in page


def test_teacher_subject_scoping(app):
    admin_c = app.test_client()
    login(admin_c, '/admin/login', {'email': 'admin@college.edu', 'password': 'admin123'})
    # publish ADSA form
    adsa = find_session(app, '6817', '2501CS10')
    f_adsa = make_form(admin_c, adsa, title='Scoped ADSA')
    publish_form(admin_c, f_adsa)
    tok = token_from(admin_c, f'/admin/results/{f_adsa}')
    admin_c.post(f'/admin/results/{f_adsa}/publish', data={'_csrf': tok})

    t = app.test_client()
    login(t, '/teacher/login', {'emp_code': '6817', 'password': 'teacher@123'})
    # filter to ADSA -> only ADSA forms
    page = t.get('/teacher/?subject_code=2501CS10').get_data(as_text=True)
    assert 'Scoped ADSA' in page
    # a subject the teacher does not teach is rejected
    page = t.get('/teacher/?subject_code=9999XX').get_data(as_text=True)
    assert 'not authorised' in page.lower() or 'Forbidden' in page


# --------------------------------------------------------------------------- #
# Search / filters
# --------------------------------------------------------------------------- #
def test_admin_form_search_and_filter(admin):
    r = admin.get('/admin/forms?q=ASE')
    assert r.status_code == 200
    r = admin.get('/admin/forms?status=open')
    assert r.status_code == 200


def test_student_search_admin(admin):
    r = admin.get('/admin/students?q=25B11CS015')
    assert r.status_code == 200
    assert '25B11CS015' in r.get_data(as_text=True)


def test_teacher_search_admin(admin):
    r = admin.get('/admin/teachers?q=6817')
    assert r.status_code == 200


def test_published_form_questions_cannot_be_rewritten(admin, app):
    sid = find_session(app, '6817', '2501IT07')
    form_id = make_form(admin, sid)
    publish_form(admin, form_id)
    roll = eligible_rolls(app, sid)[0]
    c = app.test_client()
    login_student(c, roll)
    html = c.get(f'/student/form/{form_id}').get_data(as_text=True)
    rq, tq, oid = question_ids(html)
    submit_feedback(c, form_id, rq, tq, oid)
    tok = get_csrf(admin.get('/admin/forms/new').get_data(as_text=True))
    r = admin.post('/admin/forms/save',
                   json={'form_id': form_id, 'title': 'changed', 'session_id': sid, 'questions': []},
                   headers={'X-CSRF-Token': tok})
    assert r.status_code == 409
    assert response_count(app, form_id) == 1


def test_soft_delete_preserves_history(admin, app):
    sid = find_session(app, '6817', '2501IT07')
    form_id = make_form(admin, sid)
    publish_form(admin, form_id)
    roll = eligible_rolls(app, sid)[0]
    c = app.test_client()
    login_student(c, roll)
    html = c.get(f'/student/form/{form_id}').get_data(as_text=True)
    rq, tq, oid = question_ids(html)
    submit_feedback(c, form_id, rq, tq, oid)
    tok = token_from(admin, '/admin/forms')
    admin.post(f'/admin/forms/{form_id}/delete', data={'_csrf': tok})
    with app.app_context():
        f = services.get_form(form_id, include_deleted=True)
        assert f['is_deleted'] == 1
        assert services.response_count(form_id) == 1   # historical response retained


# --------------------------------------------------------------------------- #
# Bulk form generation
# --------------------------------------------------------------------------- #
def _bulk_teacher(app, idx=0):
    """Deterministically pick a multi-session teacher other than 6817.

    The session-scoped test DB keeps forms created by other tests, so each bulk
    test uses a distinct, previously untouched teacher to stay independent.
    """
    with app.app_context():
        rows = db.query(
            """SELECT cs.teacher_user_id tid, COUNT(DISTINCT cs.id) n
               FROM class_sessions cs JOIN users u ON u.id=cs.teacher_user_id
               WHERE cs.is_deleted=0 AND u.emp_code<>'6817'
               GROUP BY cs.teacher_user_id HAVING n>=2
               ORDER BY n DESC, cs.teacher_user_id LIMIT 8""")
        return rows[idx]['tid']


def test_bulk_page_renders_default_template(admin):
    r = admin.get('/admin/forms/bulk')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Bulk create feedback forms' in html
    assert 'Excellent' in html  # default rating options embedded


def test_bulk_preview_reports_matches(admin, app):
    tid = _bulk_teacher(app, 0)
    data = admin.get(f'/admin/api/bulk/preview?teacher_id={tid}').get_json()
    assert data['matched'] >= 2
    assert data['to_create'] == data['matched'] - data['existing']


def test_bulk_create_makes_one_form_per_session(admin, app):
    tid = _bulk_teacher(app, 0)
    tok = get_csrf(admin.get('/admin/forms/bulk').get_data(as_text=True))
    r = admin.post('/admin/forms/bulk/create',
                   json={'filters': {'teacher_id': tid},
                         'title_pattern': '{subject} — {teacher} (Period {period})',
                         'status': 'draft',
                         'questions': [
                             {'text': 'Rate the teaching', 'qtype': 'rating', 'is_required': True,
                              'options': [{'label': 'Excellent', 'value': 1},
                                          {'label': 'Good', 'value': 2},
                                          {'label': 'Bad', 'value': 3},
                                          {'label': 'Very poor', 'value': 4}]}]},
                   headers={'X-CSRF-Token': tok})
    assert r.status_code == 200
    data = r.get_json()
    assert data['ok'] is True
    assert data['created'] == data['matched'] >= 2
    assert data['skipped'] == 0

    # Each created form is bound to its own session (one form per session).
    with app.app_context():
        rows = db.query('SELECT session_id FROM feedback_forms WHERE is_deleted=0 '
                        'AND session_id IN (SELECT id FROM class_sessions WHERE teacher_user_id=?)',
                        (tid,))
        session_ids = [f['session_id'] for f in rows]
    assert len(session_ids) == data['created']
    assert len(set(session_ids)) == data['created']  # no session got two forms


def test_bulk_create_skips_existing_forms(admin, app):
    tid = _bulk_teacher(app, 1)
    tok = get_csrf(admin.get('/admin/forms/bulk').get_data(as_text=True))
    payload = {'filters': {'teacher_id': tid}, 'status': 'draft',
               'questions': [{'text': 'Q', 'qtype': 'text', 'is_required': False, 'options': []}]}
    first = admin.post('/admin/forms/bulk/create', json=payload,
                       headers={'X-CSRF-Token': tok}).get_json()
    assert first['created'] >= 1
    # Running again must not duplicate anything.
    second = admin.post('/admin/forms/bulk/create', json=payload,
                        headers={'X-CSRF-Token': tok}).get_json()
    assert second['created'] == 0
    assert second['skipped'] == second['matched']


def test_bulk_create_publishes_when_requested(admin, app):
    tid = _bulk_teacher(app, 2)
    tok = get_csrf(admin.get('/admin/forms/bulk').get_data(as_text=True))
    r = admin.post('/admin/forms/bulk/create',
                   json={'filters': {'teacher_id': tid}, 'status': 'open',
                         'questions': [{'text': 'Rate', 'qtype': 'rating', 'is_required': True,
                                        'options': [{'label': 'Excellent', 'value': 1},
                                                    {'label': 'Good', 'value': 2}]}]},
                   headers={'X-CSRF-Token': tok})
    data = r.get_json()
    assert data['created'] >= 1
    assert data['opened'] == data['created']
    with app.app_context():
        opened = db.query('SELECT COUNT(*) c FROM feedback_forms WHERE status=? AND is_deleted=0 '
                          'AND session_id IN (SELECT id FROM class_sessions WHERE teacher_user_id=?)',
                          ('open', tid), one=True)['c']
    assert opened == data['opened']


def test_bulk_forms_keep_period_separation(admin, app):
    tid = _bulk_teacher(app, 3)
    tok = get_csrf(admin.get('/admin/forms/bulk').get_data(as_text=True))
    admin.post('/admin/forms/bulk/create',
               json={'filters': {'teacher_id': tid}, 'status': 'draft',
                     'questions': [{'text': 'Rate', 'qtype': 'rating', 'is_required': True,
                                    'options': [{'label': 'Excellent', 'value': 1},
                                                {'label': 'Good', 'value': 2}]}]},
               headers={'X-CSRF-Token': tok})
    with app.app_context():
        sessions = db.query('SELECT id FROM class_sessions WHERE teacher_user_id=? AND is_deleted=0 '
                            'ORDER BY id LIMIT 2', (tid,))
        assert len(sessions) == 2
        first = db.query('SELECT id FROM feedback_forms WHERE session_id=? AND is_deleted=0',
                         (sessions[0]['id'],))
        second = db.query('SELECT id FROM feedback_forms WHERE session_id=? AND is_deleted=0',
                          (sessions[1]['id'],))
        assert first and second
        # two different sessions -> two different, non-overlapping forms
        assert {f['id'] for f in first}.isdisjoint({f['id'] for f in second})


# --------------------------------------------------------------------------- #
# Accessibility of core admin pages
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('path', [
    '/admin/', '/admin/academics', '/admin/timetables', '/admin/subjects',
    '/admin/teachers', '/admin/students', '/admin/forms', '/admin/forms/new',
    '/admin/forms/bulk', '/admin/tracking', '/admin/results', '/admin/reports', '/admin/audit',
])
def test_admin_pages_render(admin, path):
    assert admin.get(path).status_code == 200
