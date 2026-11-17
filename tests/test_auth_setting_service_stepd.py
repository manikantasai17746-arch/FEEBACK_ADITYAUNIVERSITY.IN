"""Step D repair: the service-level functions behind the Authentication Required choice.

* services.auth_setting_problem: what a save may change, and what is locked;
* services.bulk_create_forms: the optional auth_required argument (default ON).
"""
import uuid

import pytest

from feedback_system import db, services
from helpers import find_session


def _q():
    return [{'text': 'Teaching quality', 'qtype': 'rating', 'is_required': True,
             'options': [{'label': 'Excellent', 'value': 5}, {'label': 'Poor', 'value': 1}]}]


def _admin_id(app):
    with app.app_context():
        return db.query("SELECT id FROM users WHERE role='admin' LIMIT 1", one=True)['id']


@pytest.fixture()
def made(app):
    bag = {'ids': [], 'titles': []}
    yield bag
    with app.app_context():
        ids = list(bag['ids'])
        for title in bag['titles']:
            ids += [r['id'] for r in db.query('SELECT id FROM feedback_forms WHERE title=?', (title,))]
        for fid in dict.fromkeys(ids):
            for q in db.query('SELECT id FROM form_questions WHERE form_id=?', (fid,)):
                db.execute('DELETE FROM question_options WHERE question_id=?', (q['id'],))
            db.execute('DELETE FROM form_questions WHERE form_id=?', (fid,))
            db.execute('DELETE FROM form_sessions WHERE form_id=?', (fid,))
            db.execute('DELETE FROM feedback_forms WHERE id=?', (fid,))


def _draft(app, admin_id, made, auth=1):
    title = f'StageD-svc {uuid.uuid4().hex[:8]}'
    made['titles'].append(title)
    with app.app_context():
        sid = find_session(app, '6817', '2501IT07')
    fid = services.save_form({'title': title, 'session_id': sid, 'scope': 'session',
                              'auth_required': auth}, _q(), admin_id, session_ids=[sid])
    made['ids'].append(fid)
    return fid


# --------------------------------------------------------------------------- #
# auth_setting_problem
# --------------------------------------------------------------------------- #
def test_new_form_is_never_locked(app):
    with app.app_context():
        assert services.auth_setting_problem(None, '0') is None
        assert services.auth_setting_problem(0, '0') is None


def test_no_choice_or_same_choice_is_never_a_problem(app, made):
    with app.app_context():
        fid = _draft(app, _admin_id(app), made, auth=1)
        assert services.auth_setting_problem(fid, None) is None
        assert services.auth_setting_problem(fid, '1') is None
        db.execute("UPDATE feedback_forms SET status='open', published_at=? WHERE id=?",
                   ('2026-11-01T10:00:00', fid))
        assert services.auth_setting_problem(fid, '1') is None      # same value: not a change


def test_draft_choice_may_change(app, made):
    with app.app_context():
        fid = _draft(app, _admin_id(app), made, auth=1)
        assert services.auth_setting_problem(fid, '0') is None


def test_published_choice_is_locked(app, made):
    with app.app_context():
        fid = _draft(app, _admin_id(app), made, auth=1)
        db.execute("UPDATE feedback_forms SET status='open', published_at=? WHERE id=?",
                   ('2026-11-01T10:00:00', fid))
        msg = services.auth_setting_problem(fid, '0')
        assert msg and 'locked' in msg


def test_lock_is_sticky_after_a_revert_to_draft(app, made):
    with app.app_context():
        fid = _draft(app, _admin_id(app), made, auth=1)
        db.execute("UPDATE feedback_forms SET status='draft', published_at=? WHERE id=?",
                   ('2026-11-01T10:00:00', fid))
        assert services.auth_setting_problem(fid, '0') is not None


def test_unrecognised_value_raises(app, made):
    with app.app_context():
        fid = _draft(app, _admin_id(app), made, auth=1)
        with pytest.raises(ValueError):
            services.auth_setting_problem(fid, 'maybe')


# --------------------------------------------------------------------------- #
# bulk_create_forms
# --------------------------------------------------------------------------- #
def _bulk_filters(app):
    with app.app_context():
        sid = find_session(app, '6817', '2501IT07')
        s = db.query('SELECT timetable_id, teacher_user_id, subject_code FROM class_sessions '
                     'WHERE id=?', (sid,), one=True)
    return {'timetable_id': s['timetable_id'], 'teacher_id': s['teacher_user_id'],
            'subject_code': s['subject_code']}


def _flags(app, title):
    with app.app_context():
        return [r['auth_required'] for r in db.query(
            'SELECT auth_required FROM feedback_forms WHERE title=?', (title,))]


def test_bulk_without_the_argument_keeps_the_old_call_working_and_defaults_on(app, made):
    title = f'StageD-bulk-default {uuid.uuid4().hex[:8]}'
    made['titles'].append(title)
    # The five-argument call used by existing tests must still work.
    with app.app_context():
        services.bulk_create_forms(_bulk_filters(app), _q(), title, 'draft', _admin_id(app))
    flags = _flags(app, title)
    assert flags and all(f == 1 for f in flags)


def test_bulk_with_auth_required_zero_creates_forms_off(app, made):
    title = f'StageD-bulk-off {uuid.uuid4().hex[:8]}'
    made['titles'].append(title)
    with app.app_context():
        services.bulk_create_forms(_bulk_filters(app), _q(), title, 'draft', _admin_id(app),
                                   auth_required=0)
    flags = _flags(app, title)
    assert flags and all(f == 0 for f in flags)
