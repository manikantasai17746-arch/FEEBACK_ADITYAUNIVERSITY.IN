"""Results auto-publish to the teacher once enough students have responded."""
from feedback_system import db, services

from test_multi_round import _make_round, _session, _students, _submit


def _published(app, fid):
    with app.app_context():
        r = db.query('SELECT is_published FROM result_publications WHERE form_id=?',
                     (fid,), one=True)
        return bool(r and r['is_published'])


def test_required_count_matches_example():
    assert services.auto_publish_required(72, 84) == 60      # 84% of 72
    assert services.auto_publish_required(72, 0) is None     # feature off
    assert services.auto_publish_required(0, 84) is None


def test_auto_publish_at_threshold(app):
    sess = _session(app)
    fid = _make_round(app, sess, 'Auto publish round', publish=False)
    with app.app_context():
        eligible = services.eligible_count_for_form(fid)
        needed = services.auto_publish_required(eligible)
    students = _students(app, sess, needed)
    for sid in students[:needed - 1]:
        assert _submit(app, sid, fid, 'Excellent')[0]
    assert not _published(app, fid)                          # one short
    assert _submit(app, students[needed - 1], fid, 'Excellent')[0]
    assert _published(app, fid)                              # threshold reached


def test_manual_unpublish_is_respected(app):
    sess = _session(app)
    fid = _make_round(app, sess, 'Auto publish unpublished', publish=False)
    with app.app_context():
        needed = services.auto_publish_required(services.eligible_count_for_form(fid))
        db.execute("INSERT INTO result_publications(form_id,session_id,is_published,"
                   "unpublished_at) VALUES(?,?,0,'2026-01-01')", (fid, sess))
    for sid in _students(app, sess, needed):
        assert _submit(app, sid, fid, 'Excellent')[0]
    assert not _published(app, fid)                          # HOD decision stands
