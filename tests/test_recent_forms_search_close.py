"""HOD Recent Forms: search box + "Close feedback" only before the deadline."""
import re
import time

import pytest

from feedback_system import category_forms, db, services
from feedback_system.feedback_categories import ScopeError
from helpers import future_deadline
from test_hod_feedback_main import (_hod_qset_id, _login_hod, _seed_hod,  # noqa: F401
                                    app, client)

PAST = '2001-01-01T00:00:00'


def _no_sweep():
    """Stop the lazy auto-close sweep for the next ~30 s so a test can see an
    open form whose deadline has passed (the window between sweeps)."""
    services._last_expiry_sweep = time.monotonic()


def _csrf(client):
    html = client.get('/hod/feedback/main/create').get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def _published_form(app, client, offset=0):
    """Create + publish one Main form for the offset-th CSE session of the HOD."""
    seed = _seed_hod(app)
    with app.app_context():
        rows = db.query(
            """SELECT cs.id FROM class_sessions cs
               JOIN timetables t ON t.id=cs.timetable_id
               WHERE cs.is_deleted=0 AND cs.teacher_user_id IS NOT NULL
               ORDER BY cs.id LIMIT 6""")
        if len(rows) <= offset:
            pytest.skip('Not enough sessions')
        sid = rows[offset]['id']
        branch = db.query('SELECT t.branch_id FROM class_sessions cs JOIN timetables t '
                          'ON t.id=cs.timetable_id WHERE cs.id=?', (sid,), one=True)['branch_id']
        db.execute('DELETE FROM user_department_scopes WHERE user_id=?', (seed['hod_id'],))
        db.execute('INSERT INTO user_department_scopes(user_id, branch_id, is_primary) VALUES(?,?,1)',
                   (seed['hod_id'], branch))
        db.execute("UPDATE feedback_forms SET is_deleted=1 WHERE session_id=? AND category='main'",
                   (sid,))
    c = _login_hod(client)
    r = c.post('/hod/feedback/main/api/create', headers={'X-CSRF-Token': _csrf(c)},
               json={'session_ids': [sid], 'question_set_id': _hod_qset_id(app, seed['hod_id']),
                     'publish': True, 'deadline_at': future_deadline()})
    data = r.get_json()
    if not (data.get('ok') and data.get('created')):
        pytest.skip('Could not publish a form here: %s' % data)
    return c, data['created'][0]['form_id']


def _set_deadline(app, fid, iso):
    with app.app_context():
        db.execute('UPDATE feedback_forms SET deadline_at=? WHERE id=?', (iso, fid))


def _status(app, fid):
    with app.app_context():
        return db.query('SELECT status, closed_at FROM feedback_forms WHERE id=?',
                        (fid,), one=True)


def _row_html(page, fid):
    m = re.search(r'<tr data-status="[^"]*"[^>]*>(?:(?!</tr>).)*?name="form_ids" value="%d".*?</tr>'
                  % fid, page, re.S)
    assert m, 'row for form %d not found' % fid
    return m.group(0)


def _post_status(c, fid, status):
    return c.post('/hod/forms/%d/status' % fid, json={'status': status},
                  headers={'X-CSRF-Token': _csrf(c), 'X-Requested-With': 'XMLHttpRequest'})


def test_close_works_before_deadline(app, client):
    c, fid = _published_form(app, client)
    _no_sweep()
    r = _post_status(c, fid, 'closed')
    assert r.status_code == 200 and r.get_json()['ok'], r.get_data(as_text=True)
    assert _status(app, fid)['status'] == 'closed'


def test_manual_close_refused_after_deadline(app, client):
    c, fid = _published_form(app, client)
    _set_deadline(app, fid, PAST)
    _no_sweep()
    r = _post_status(c, fid, 'closed')
    assert r.status_code == 409
    assert 'deadline' in ' '.join(r.get_json()['errors']).lower()
    row = _status(app, fid)
    assert row['status'] == 'open' and not row['closed_at']      # untouched by the refusal
    # the service layer refuses on its own as well (not just the route)
    with app.app_context():
        actor = {'id': 0, 'role': 'admin', 'name': 'Admin'}
        with pytest.raises(ScopeError) as exc:
            category_forms.close(actor, fid)
        assert exc.value.code == 'deadline_passed'
    # ...and the normal sweep then closes it by itself
    with app.app_context():
        services.close_expired_forms(force=True)
    assert _status(app, fid)['status'] == 'closed'


def test_markup_search_box_and_close_button_states(app, client):
    c, fid = _published_form(app, client)
    _no_sweep()
    page = c.get('/hod/').get_data(as_text=True)
    assert 'id="rfQ"' in page and 'Search by emp ID or subject' in page
    live = _row_html(page, fid)
    assert 'data-act="closed"' in live and 'closes automatically' not in live
    assert re.search(r'data-q="[^"]*\(\d+\)', live) or 'data-q=' in live
    _set_deadline(app, fid, PAST)
    _no_sweep()
    late = _row_html(c.get('/hod/').get_data(as_text=True), fid)
    assert 'data-act="closed"' not in late
    assert re.search(r'<button[^>]*disabled[^>]*>Close feedback', late)
    assert 'Deadline passed' in late


def test_forms_page_shares_the_same_table(app, client):
    c, fid = _published_form(app, client)
    _no_sweep()
    page = c.get('/hod/forms').get_data(as_text=True)
    assert 'id="rfQ"' in page and 'id="rfForm"' in page
