"""A student gives a 1-5 star rating to their own written comment, only when they wrote one."""
import os
import tempfile

import pytest

from feedback_system import category_forms, create_app, db, services
from feedback_system.feedback_categories import MAIN
from helpers import future_deadline
import test_hod_feedback_lab as T


@pytest.fixture(scope='module')
def env():
    tmp = tempfile.mkdtemp(prefix='ffs-stars-')
    app = create_app({'TESTING': True, 'DATABASE': os.path.join(tmp, 't.db'), 'DATABASE_URL': None,
                      'SECRET_KEY': 'k', 'AUTO_IMPORT': True, 'ADMIN_EMAIL': 'a@b.c',
                      'ADMIN_PASSWORD': 'admin123', 'DEFAULT_STUDENT_PASSWORD': 'student@123',
                      'DEFAULT_TEACHER_PASSWORD': 'teacher@123', 'REQUIRE_ACCOUNT_SETUP': False})
    return app


def _form(app, n):
    """A fresh published Main form with one rating question and one comment box; returns ids."""
    seed = T._seed_hod(app)
    sess = T._session_with_lab(app, seed['branch_id'], 'available')
    with app.app_context():
        hod = dict(db.query('SELECT * FROM users WHERE id=?', (seed['hod_id'],), one=True))
        db.execute("UPDATE feedback_forms SET is_deleted=1 WHERE session_id=?", (sess['id'],))
        qs = [{'text': 'Rate', 'qtype': 'rating', 'is_required': True,
               'options': [{'label': 'Good', 'value': 5}, {'label': 'Bad', 'value': 1}]},
              {'text': 'Comment', 'qtype': 'text', 'is_required': False, 'options': []}]
        fid = category_forms.create_form(hod, MAIN, sess['id'], f'P{n}', qs,
                                         deadline_at=services.parse_deadline_input(future_deadline(7)))
        category_forms.publish(hod, fid)
        students = services.eligible_students(sess['id'])
        qrows = services.form_questions(fid)
        rate = next(q for q in qrows if q['qtype'] == 'rating')
        text = next(q for q in qrows if q['qtype'] == 'text')
        return fid, students[n]['id'], rate, text


def _payload(rate, text, comment=None, stars=None):
    p = {f'q_{rate["id"]}': str(rate['options'][0]['id'])}
    if comment is not None:
        p[f'q_{text["id"]}'] = comment
    if stars is not None:
        p[f'qs_{text["id"]}'] = str(stars)
    return p


def _stars(fid, text):
    return [dict(r) for r in db.query(
        'SELECT text_value, comment_stars FROM anonymous_answers WHERE form_id=? AND question_id=?',
        (fid, text['id']))]


def test_comment_without_stars_is_refused(env):
    fid, sid, rate, text = _form(env, 0)
    with env.app_context():
        ok, msg = services.submit_feedback(sid, fid, _payload(rate, text, 'Good class'))
        assert not ok and 'star rating' in msg
        assert db.query('SELECT 1 FROM submissions WHERE form_id=?', (fid,), one=True) is None


@pytest.mark.parametrize('bad', ['0', '6', 'x', ''])
def test_invalid_stars_are_refused(env, bad):
    fid, sid, rate, text = _form(env, 0)
    with env.app_context():
        ok, msg = services.submit_feedback(sid, fid, _payload(rate, text, 'Good class', bad))
        assert not ok and 'star rating' in msg


def test_comment_with_stars_is_stored_anonymously(env):
    fid, sid, rate, text = _form(env, 0)
    with env.app_context():
        ok, msg = services.submit_feedback(sid, fid, _payload(rate, text, 'Very clear', 4))
        assert ok, msg
        assert _stars(fid, text) == [{'text_value': 'Very clear', 'comment_stars': 4}]
        cols = [r[1] for r in db.get_db().execute("PRAGMA table_info(feedback_responses)")]
        assert 'student_user_id' not in cols and 'comment_stars' in cols


def test_no_comment_needs_no_stars_and_stray_stars_are_ignored(env):
    fid, sid, rate, text = _form(env, 0)
    with env.app_context():
        ok, msg = services.submit_feedback(sid, fid, _payload(rate, text, '   ', 5))
        assert ok, msg
        assert all(r['comment_stars'] is None for r in _stars(fid, text))
