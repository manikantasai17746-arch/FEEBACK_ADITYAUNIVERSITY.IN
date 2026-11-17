"""Checkpoint 1.1 - Authentication Required on forms.

* H1: the setting is locked permanently once a form has been published
  (``published_at`` is sticky), including after a revert to draft and when there
  are no submissions.
* M3: a change that races a publish is refused by the same conditional UPDATE.
* H2: the split script never copies the source form's setting; each copy is ON.
"""
import importlib.util
import uuid
from pathlib import Path
from unittest import mock

import pytest

from feedback_system import category_forms as cf
from feedback_system import db
from feedback_system import db as db_module
from feedback_system import services
from feedback_system.services import AuthSettingLocked

ROOT = Path(__file__).resolve().parent.parent


def _questions():
    return [{'text': 'Teaching quality', 'qtype': 'rating', 'is_required': True,
             'options': [{'label': 'Excellent', 'value': 5}, {'label': 'Poor', 'value': 1}]}]


def _admin_row():
    return dict(db.query("SELECT * FROM users WHERE role='admin' LIMIT 1", one=True))


def _session_id():
    return db.query('SELECT id FROM class_sessions WHERE is_deleted=0 ORDER BY id LIMIT 1',
                    one=True)['id']


def _new_form(made, title=None, **extra):
    title = title or f'CP11 {uuid.uuid4().hex[:8]}'
    sid = _session_id()
    payload = {'title': title, 'session_id': sid, 'scope': 'session', **extra}
    fid = services.save_form(payload, _questions(), _admin_row()['id'], session_ids=[sid])
    made['ids'].append(fid)
    made['titles'].append(title)
    return fid


def _flag(fid):
    return db.query('SELECT auth_required FROM feedback_forms WHERE id=?', (fid,),
                    one=True)['auth_required']


def _publish_via_sql(fid):
    db.execute("UPDATE feedback_forms SET status='open', published_at=? WHERE id=?",
               ('2026-11-01T10:00:00', fid))


def _save_auth(fid, value):
    return services.save_form({'title': 'CP11 change attempt', 'auth_required': value,
                               'session_id': _session_id(), 'scope': 'session'},
                              _questions(), _admin_row()['id'], form_id=fid,
                              session_ids=[_session_id()])


@pytest.fixture()
def made(app):
    bag = {'ids': [], 'titles': []}
    yield bag
    with app.app_context():
        # Some forms are created through routes, so delete by title as well as by id.
        ids = list(bag['ids'])
        for title in bag['titles']:
            ids += [r['id'] for r in db.query('SELECT id FROM feedback_forms WHERE title=?',
                                              (title,))]
        for fid in dict.fromkeys(ids):
            for q in db.query('SELECT id FROM form_questions WHERE form_id=?', (fid,)):
                db.execute('DELETE FROM question_options WHERE question_id=?', (q['id'],))
            db.execute('DELETE FROM form_questions WHERE form_id=?', (fid,))
            db.execute('DELETE FROM form_sessions WHERE form_id=?', (fid,))
            db.execute('DELETE FROM feedback_forms WHERE id=?', (fid,))


# --------------------------------------------------------------------------- #
# H1: sticky lock
# --------------------------------------------------------------------------- #
def test_published_form_stays_locked_after_revert_to_draft_without_submissions(app, made):
    with app.app_context():
        fid = _new_form(made)
        # Publication is simulated by the two columns publish sets (status and
        # published_at). The full publish side effects are not needed to test the lock,
        # and they add rows that this fixture does not clean up.
        _publish_via_sql(fid)
        assert db.query('SELECT published_at FROM feedback_forms WHERE id=?', (fid,),
                        one=True)['published_at']
        # No submissions exist, so the revert is allowed (existing behaviour) ...
        services.set_form_status(fid, 'draft')
        assert db.query('SELECT status FROM feedback_forms WHERE id=?', (fid,),
                        one=True)['status'] == 'draft'
        # ... but the setting stays locked, because it was published once.
        with pytest.raises(AuthSettingLocked):
            _save_auth(fid, '0')
        assert _flag(fid) == 1


def test_revert_through_category_lifecycle_also_keeps_setting_locked(app, made):
    with app.app_context():
        fid = _new_form(made)
        _publish_via_sql(fid)
        actor = _admin_row()
        cf.change_status(actor, fid, 'draft')                 # legacy HOD/Admin route path
        assert db.query('SELECT status FROM feedback_forms WHERE id=?', (fid,),
                        one=True)['status'] == 'draft'
        with pytest.raises(AuthSettingLocked):
            _save_auth(fid, '0')
        assert _flag(fid) == 1


def test_published_at_alone_locks_the_setting_even_if_status_says_draft(app, made):
    with app.app_context():
        fid = _new_form(made)
        db.execute('UPDATE feedback_forms SET published_at=? WHERE id=?',
                   ('2026-11-01T10:00:00', fid))
        with pytest.raises(AuthSettingLocked):
            _save_auth(fid, '0')
        assert _flag(fid) == 1


def test_non_draft_status_locks_the_setting_even_without_published_at(app, made):
    with app.app_context():
        fid = _new_form(made)
        db.execute("UPDATE feedback_forms SET status='closed' WHERE id=?", (fid,))
        with pytest.raises(AuthSettingLocked):
            _save_auth(fid, '0')
        assert _flag(fid) == 1


def test_never_published_draft_can_still_change_the_setting(app, made):
    with app.app_context():
        fid = _new_form(made)
        _save_auth(fid, '0')
        assert _flag(fid) == 0
        _save_auth(fid, '1')
        assert _flag(fid) == 1


# --------------------------------------------------------------------------- #
# M3: publish/save race
# --------------------------------------------------------------------------- #
def test_change_racing_a_publish_is_refused_and_leaves_the_form_unchanged(app, made):
    """save_form read the form as a draft, but publish committed before its write."""
    with app.app_context():
        fid = _new_form(made, title='CP11 race original')
        _publish_via_sql(fid)                       # the publish that won the race
        real_query = db_module.query

        def stale_read(sql, args=(), one=False):
            if sql.startswith('SELECT auth_required FROM feedback_forms'):
                return {'auth_required': 1}         # what save_form saw before publish
            return real_query(sql, args, one=one)

        with mock.patch.object(db_module, 'query', side_effect=stale_read):
            with pytest.raises(AuthSettingLocked):
                services.save_form({'title': 'CP11 race renamed', 'auth_required': '0',
                                    'session_id': _session_id(), 'scope': 'session'},
                                   _questions(), _admin_row()['id'], form_id=fid,
                                   session_ids=[_session_id()])
        row = db.query('SELECT title, auth_required FROM feedback_forms WHERE id=?', (fid,),
                       one=True)
        assert row['auth_required'] == 1
        assert row['title'] == 'CP11 race original'   # refused before any other write


# --------------------------------------------------------------------------- #
# H2: split script
# --------------------------------------------------------------------------- #
def _load_split_script():
    spec = importlib.util.spec_from_file_location(
        'split_forms_by_teacher', ROOT / 'scripts' / 'split_forms_by_teacher.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_copied_form_columns_never_inherit_the_source_setting():
    split = _load_split_script()
    cols = split.copied_form_columns(['id', 'title', 'auth_required', 'status'])
    assert 'auth_required' in cols and cols[-1] == 'auth_required'
    assert 'id' not in cols
    assert cols.count('auth_required') == 1
    assert cols[:-1] == ['title', 'status']


def test_split_copy_is_on_while_the_source_form_stays_off(app, made):
    split = _load_split_script()
    with app.app_context():
        fid = _new_form(made, title='CP11 split source')
        db.execute('UPDATE feedback_forms SET auth_required=0 WHERE id=?', (fid,))  # legacy OFF
        s1 = _session_id()
        s2 = db.query('SELECT id FROM class_sessions WHERE is_deleted=0 AND id<>? '
                      'ORDER BY id LIMIT 1', (s1,), one=True)['id']
        source = dict(db.query('SELECT * FROM feedback_forms WHERE id=?', (fid,), one=True))
        groups = {'A': {'name': 'Teacher A', 'period': 'P1', 'sessions': [s1]},
                  'B': {'name': 'Teacher B', 'period': 'P2', 'sessions': [s2]}}
        conn = db.get_db()
        created = split.apply_form(conn, source, groups, 'A', {})
        conn.commit()
        new_id = created['B']
        made['ids'].append(new_id)
        copy = dict(db.query('SELECT * FROM feedback_forms WHERE id=?', (new_id,), one=True))
        assert copy['auth_required'] == 1                    # explicitly ON, not inherited
        assert _flag(fid) == 0                               # source untouched
        assert copy['title'] == source['title']              # other data preserved
        assert copy['session_id'] == s2
        n_src = db.query('SELECT COUNT(*) c FROM form_questions WHERE form_id=?', (fid,),
                         one=True)['c']
        n_copy = db.query('SELECT COUNT(*) c FROM form_questions WHERE form_id=?', (new_id,),
                          one=True)['c']
        assert n_copy == n_src
