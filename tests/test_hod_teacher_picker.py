"""HOD teacher picker: subject-wise multi-select page + API for Main / Lab / Second."""
import json

import pytest

from feedback_system import db
from helpers import future_deadline, get_csrf
from test_hod_feedback_main import (_a_cse_session, _hod_qset_id, _login_hod,  # noqa: F401
                                    _seed_hod, app, client)


def _filters(app):
    with app.app_context():
        sess = _a_cse_session(app)
        row = db.query(
            """SELECT t.academic_year_id AS y, t.semester_id AS s, t.branch_id AS b
               FROM class_sessions cs JOIN timetables t ON t.id=cs.timetable_id
               WHERE cs.id=?""", (sess['id'],), one=True)
        return dict(row), sess


def test_form_new_presets_category_and_picker(app, client):
    _seed_hod(app)
    c = _login_hod(client)
    html = c.get('/hod/forms/new?category=main&scope=subject').get_data(as_text=True)
    assert 'id="picker-field"' in html and 'data-picker="1"' in html
    assert 'data-scope="subject"' in html
    assert '<select id="category" disabled>' in html
    html = c.get('/hod/forms/new?category=lab').get_data(as_text=True)
    assert 'data-scope="session"' in html
    assert 'value="lab" selected' in html


def test_picker_page_and_main_list_links(app, client):
    _seed_hod(app)
    c = _login_hod(client)
    for cat in ('main', 'lab', 'second'):
        assert c.get(f'/hod/forms/pick-teachers?category={cat}').status_code == 200
    page = c.get('/hod/feedback/main').get_data(as_text=True)
    assert '/hod/forms/new?category=main&amp;scope=subject' in page
    assert '/hod/forms/new?category=lab' in c.get('/hod/feedback/lab').get_data(as_text=True)
    assert '/hod/forms/new?category=second' in c.get('/hod/feedback/second').get_data(as_text=True)


def test_picker_api_groups_subject_then_teacher(app, client):
    _seed_hod(app)
    f, sess = _filters(app)
    c = _login_hod(client)
    d = c.get(f"/hod/api/teacher-picker?category=main&academic_year_id={f['y']}"
              f"&semester_id={f['s']}&branch_id={f['b']}").get_json()
    assert d['ok'] and d['subjects']
    sub = d['subjects'][0]
    assert sub['code'] and sub['teachers']
    t = sub['teachers'][0]
    assert t['id'] and t['name'] and t['sessions'] and t['students'] > 0
    # lab list is always a subset of main (only Lab Available sessions)
    lab = c.get(f"/hod/api/teacher-picker?category=lab&academic_year_id={f['y']}"
                f"&semester_id={f['s']}&branch_id={f['b']}").get_json()
    main_ids = {x['id'] for s in d['subjects'] for t in s['teachers'] for x in t['sessions']}
    lab_ids = {x['id'] for s in lab['subjects'] for t in s['teachers'] for x in t['sessions']}
    assert lab_ids <= main_ids
    sec = c.get(f"/hod/api/teacher-picker?category=second&branch_id={f['b']}").get_json()
    assert sec['ok'] is True


def test_picker_api_blocks_other_department(app, client):
    seed = _seed_hod(app)
    c = _login_hod(client)
    with app.app_context():
        other = db.query('SELECT id FROM branches WHERE id <> ? LIMIT 1', (seed['branch_id'],), one=True)
    if not other:
        pytest.skip('only one branch')
    assert c.get(f"/hod/api/teacher-picker?branch_id={other['id']}").get_json()['subjects'] == []
    assert c.get('/hod/api/teacher-picker').get_json()['subjects'] == []


def test_teacher_ids_restricts_subject_forms(app, client):
    seed = _seed_hod(app)
    f, sess = _filters(app)
    c = _login_hod(client)
    d = c.get(f"/hod/api/teacher-picker?category=main&academic_year_id={f['y']}"
              f"&semester_id={f['s']}&branch_id={f['b']}").get_json()
    sub = next((s for s in d['subjects'] if len(s['teachers']) >= 2), None)
    if not sub:
        pytest.skip('no subject with two teachers in seed data')
    keep = sub['teachers'][0]
    tok = get_csrf(c.get('/hod/forms/new?category=main&scope=subject').get_data(as_text=True))
    with app.app_context():
        qs = db.query('SELECT id, text, qtype, options_json FROM question_set_items WHERE set_id=? ORDER BY position',
                      (_hod_qset_id(app, seed['hod_id']),))
        questions = [{'text': q['text'], 'qtype': q['qtype'], 'is_required': True,
                      'options': json.loads(q['options_json'] or '[]')} for q in qs]
    body = {'scope': 'subject', 'category': 'main', 'phase': 'phase1',
            'academic_year_id': f['y'], 'semester_id': f['s'], 'branch_id': f['b'],
            'subject_code': sub['code'], 'teacher_ids': [keep['id']],
            'deadline_at': future_deadline(), 'questions': questions,
            'instructions': 'x'}
    r = c.post('/hod/forms/save', json=body, headers={'X-CSRF-Token': tok})
    data = r.get_json()
    assert r.status_code == 200 and data['ok'], data
    assert len(data['forms']) == 1
    assert data['forms'][0]['teacher_name'] == keep['name']
