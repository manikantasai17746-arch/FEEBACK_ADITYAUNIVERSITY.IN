"""Step D (admin side): the Authentication Required choice on the form builder and the
bulk screen, the draft-only change, and the published lock.
"""
import re
import uuid

import pytest

from feedback_system import db
from helpers import find_session

DEADLINE = '2030-01-31T23:59'


def _q():
    return [{'text': 'Teaching quality', 'qtype': 'rating', 'is_required': True,
             'options': [{'label': 'Excellent', 'value': 5}, {'label': 'Poor', 'value': 1}]}]


def _token(client):
    with client.session_transaction() as sess:
        tok = sess.get('token')
        if not tok:
            import secrets
            tok = secrets.token_hex(16)
            sess['token'] = tok
        return tok


@pytest.fixture()
def made(app):
    bag = {'titles': []}
    yield bag
    with app.app_context():
        for title in bag['titles']:
            for row in db.query('SELECT id FROM feedback_forms WHERE title=?', (title,)):
                fid = row['id']
                for q in db.query('SELECT id FROM form_questions WHERE form_id=?', (fid,)):
                    db.execute('DELETE FROM question_options WHERE question_id=?', (q['id'],))
                db.execute('DELETE FROM form_questions WHERE form_id=?', (fid,))
                db.execute('DELETE FROM form_sessions WHERE form_id=?', (fid,))
                db.execute('DELETE FROM feedback_forms WHERE id=?', (fid,))


def _title(bag):
    t = f'StageD {uuid.uuid4().hex[:10]}'
    bag['titles'].append(t)
    return t


def _save(admin, sid, title, form_id=None, **extra):
    body = {'title': title, 'session_id': sid, 'scope': 'session', 'instructions': 'Anonymous.',
            'deadline_at': DEADLINE, 'questions': _q(), **extra}
    if form_id:
        body['form_id'] = form_id
    return admin.post('/admin/forms/save', json=body, headers={'X-CSRF-Token': _token(admin)})


def _auth_of(app, title):
    with app.app_context():
        return db.query('SELECT auth_required FROM feedback_forms WHERE title=?', (title,),
                        one=True)['auth_required']


def _fid(app, title):
    with app.app_context():
        return db.query('SELECT id FROM feedback_forms WHERE title=?', (title,), one=True)['id']


def _sid(app):
    return find_session(app, '6817', '2501IT07')


# --------------------------------------------------------------------------- #
# Saving from the builder
# --------------------------------------------------------------------------- #
def test_builder_new_form_is_on_when_the_choice_is_absent(app, admin, made):
    title = _title(made)
    assert _save(admin, _sid(app), title).status_code == 200
    assert _auth_of(app, title) == 1


def test_builder_unchecked_box_saves_the_form_off(app, admin, made):
    title = _title(made)
    assert _save(admin, _sid(app), title, auth_required='0').status_code == 200
    assert _auth_of(app, title) == 0


def test_builder_null_choice_means_the_default_on(app, admin, made):
    title = _title(made)
    assert _save(admin, _sid(app), title, auth_required=None).status_code == 200
    assert _auth_of(app, title) == 1


def test_builder_rejects_an_unrecognised_choice(app, admin, made):
    title = _title(made)
    r = _save(admin, _sid(app), title, auth_required='maybe')
    assert r.status_code == 400
    with app.app_context():
        assert db.query('SELECT COUNT(*) c FROM feedback_forms WHERE title=?', (title,),
                        one=True)['c'] == 0


def test_draft_choice_can_be_changed_from_the_builder(app, admin, made):
    title = _title(made)
    _save(admin, _sid(app), title, auth_required='1')
    fid = _fid(app, title)
    assert _save(admin, _sid(app), title, form_id=fid, auth_required='0').status_code == 200
    assert _auth_of(app, title) == 0


def test_published_choice_is_locked_from_the_builder(app, admin, made):
    title = _title(made)
    _save(admin, _sid(app), title, auth_required='1')
    fid = _fid(app, title)
    with app.app_context():
        db.execute("UPDATE feedback_forms SET status='open', published_at=? WHERE id=?",
                   ('2026-11-01T10:00:00', fid))
    r = _save(admin, _sid(app), title, form_id=fid, auth_required='0')
    assert r.status_code == 409
    assert b'locked' in r.data.lower()
    assert _auth_of(app, title) == 1


# --------------------------------------------------------------------------- #
# What the screens show
# --------------------------------------------------------------------------- #
def _checkbox(html):
    m = re.search(r'<input type="checkbox" id="auth_required"[^>]*>', html)
    assert m, 'the Authentication Required checkbox is missing from the page'
    return m.group(0)


def test_new_form_page_shows_the_box_checked(admin):
    html = admin.get('/admin/forms/new').get_data(as_text=True)
    box = _checkbox(html)
    assert 'checked' in box and 'disabled' not in box


def test_edit_page_shows_the_stored_choice(app, admin, made):
    title = _title(made)
    _save(admin, _sid(app), title, auth_required='0')
    fid = _fid(app, title)
    box = _checkbox(admin.get(f'/admin/forms/{fid}/edit').get_data(as_text=True))
    assert 'checked' not in box and 'disabled' not in box


def test_published_form_page_shows_the_box_disabled(app, admin, made):
    title = _title(made)
    _save(admin, _sid(app), title, auth_required='1')
    fid = _fid(app, title)
    with app.app_context():
        db.execute("UPDATE feedback_forms SET status='open', published_at=? WHERE id=?",
                   ('2026-11-01T10:00:00', fid))
    box = _checkbox(admin.get(f'/admin/forms/{fid}/edit').get_data(as_text=True))
    assert 'disabled' in box and 'checked' in box


# --------------------------------------------------------------------------- #
# Bulk creation
# --------------------------------------------------------------------------- #
def test_bulk_creation_saves_the_choice(app, admin, made):
    sid = _sid(app)
    with app.app_context():
        s = db.query('SELECT timetable_id, teacher_user_id, subject_code FROM class_sessions '
                     'WHERE id=?', (sid,), one=True)
    title = _title(made)
    r = admin.post('/admin/forms/bulk/create',
                   json={'filters': {'timetable_id': s['timetable_id'],
                                     'teacher_id': s['teacher_user_id'],
                                     'subject_code': s['subject_code']},
                         'title_pattern': title, 'status': 'draft', 'questions': _q(),
                         'deadline_at': DEADLINE, 'auth_required': '0'},
                   headers={'X-CSRF-Token': _token(admin)})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert _auth_of(app, title) == 0
