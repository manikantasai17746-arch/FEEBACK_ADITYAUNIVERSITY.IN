"""HOD Form Management / Recent Forms: 'Change deadline' button and dialog."""
import re

from feedback_system import services
from helpers import future_deadline, token_from
from test_deadline_and_pending import _row, _sql, pending_setup  # noqa: F401


def _forms_row(html, fid):
    i = html.find('/hod/forms/%d/deadline' % fid)
    assert i != -1, 'Change deadline button not rendered for the form'
    return html[html.rfind('<tr', 0, i):html.find('</tr>', i)]


def test_row_shows_deadline_and_change_button(app, pending_setup):
    fid = pending_setup['fid']
    _sql(app, "UPDATE feedback_forms SET deadline_at='2030-05-01T04:00:00' WHERE id=?", (fid,))
    html = pending_setup['hod'].get('/hod/forms').get_data(as_text=True)
    row = _forms_row(html, fid)
    assert '01 May 2030' in row and 'No deadline' not in row
    assert 'Change deadline' in row
    # 04:00 UTC is 09:30 college time (+05:30): the dialog is pre-filled with local time
    assert 'data-val="2030-05-01T09:30"' in row
    # the dialog has its own <form>, outside the table form (forms cannot nest)
    assert 'id="rfDlForm"' in html and 'name="deadline_at"' in html
    assert html.index('id="rfDlForm"') > html.index('id="rfForm"')
    assert html.index('id="rfDlForm"') > html.rindex('</form>', 0, html.index('id="rfDlForm"'))


def test_hod_can_change_deadline_from_form_management(app, pending_setup):
    hod, fid = pending_setup['hod'], pending_setup['fid']
    tok = token_from(hod, '/hod/forms')
    new_local = future_deadline(10)
    r = hod.post('/hod/forms/%d/deadline' % fid,
                 data={'_csrf': tok, 'deadline_at': new_local, 'next': '/hod/forms'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/hod/forms')
    assert _row(app, fid)['deadline_at'] == services.parse_deadline_input(new_local)
    # the new deadline is what the table now shows
    html = hod.get('/hod/forms').get_data(as_text=True)
    assert re.search(r'data-val="%s"' % re.escape(new_local), _forms_row(html, fid))


def test_past_deadline_is_refused(app, pending_setup):
    hod, fid = pending_setup['hod'], pending_setup['fid']
    before = _row(app, fid)['deadline_at']
    tok = token_from(hod, '/hod/forms')
    hod.post('/hod/forms/%d/deadline' % fid,
             data={'_csrf': tok, 'deadline_at': '2001-01-01T00:00', 'next': '/hod/forms'})
    assert _row(app, fid)['deadline_at'] == before


# ---------------------------------------------------------------- whole phase
def _phase_forms(app, admin, pending_setup):
    """Phase 1: open + draft + closed; Phase 2: draft; Phase 1 in ANOTHER department."""
    from helpers import make_form
    sid, base = pending_setup['sid'], pending_setup['fid']
    ids = {'open': base,
           'draft': make_form(admin, sid, title='Phase 1 draft'),
           'closed': make_form(admin, sid, title='Phase 1 closed'),
           'p2': make_form(admin, sid, title='Phase 2 draft'),
           'other': make_form(admin, sid, title='Phase 1 other dept')}
    with app.app_context():
        from feedback_system import db
        branch = db.query('SELECT branch_id FROM feedback_forms WHERE id=?', (base,), one=True)['branch_id']
        other_branch = db.query('SELECT id FROM branches WHERE id<>? LIMIT 1', (branch,), one=True)['id']
    for k in ('open', 'draft', 'closed', 'other'):
        _sql(app, "UPDATE feedback_forms SET phase='phase1' WHERE id=?", (ids[k],))
    _sql(app, "UPDATE feedback_forms SET phase='phase2' WHERE id=?", (ids['p2'],))
    _sql(app, "UPDATE feedback_forms SET status='closed' WHERE id=?", (ids['closed'],))
    _sql(app, "UPDATE feedback_forms SET branch_id=? WHERE id=?", (other_branch, ids['other']))
    return ids


def test_change_whole_phase_deadline(app, admin, pending_setup):
    hod = pending_setup['hod']
    ids = _phase_forms(app, admin, pending_setup)
    before = {k: _row(app, v) for k, v in ids.items()}
    new_local = future_deadline(20)
    tok = token_from(hod, '/hod/forms')
    r = hod.post('/hod/forms/deadline-phase', follow_redirects=True,
                 data={'_csrf': tok, 'phase': 'phase1', 'deadline_at': new_local, 'next': '/hod/forms'})
    want = services.parse_deadline_input(new_local)
    assert _row(app, ids['open'])['deadline_at'] == want            # published Phase 1: changed
    assert _row(app, ids['draft'])['deadline_at'] == want           # draft Phase 1: changed
    assert _row(app, ids['closed'])['deadline_at'] == before['closed']['deadline_at']   # closed: kept
    assert _row(app, ids['closed'])['status'] == 'closed'           # ...and NOT reopened
    assert _row(app, ids['p2'])['deadline_at'] == before['p2']['deadline_at']           # other phase
    assert _row(app, ids['other'])['deadline_at'] == before['other']['deadline_at']     # other department
    page = r.get_data(as_text=True)
    assert '2 Phase 1 form(s)' in page and '1 closed Phase 1 form(s)' in page


def test_phase_change_refuses_past_deadline_and_bad_phase(app, admin, pending_setup):
    hod = pending_setup['hod']
    ids = _phase_forms(app, admin, pending_setup)
    before = _row(app, ids['open'])['deadline_at']
    tok = token_from(hod, '/hod/forms')
    hod.post('/hod/forms/deadline-phase',
             data={'_csrf': tok, 'phase': 'phase1', 'deadline_at': '2001-01-01T00:00', 'next': '/hod/forms'})
    assert _row(app, ids['open'])['deadline_at'] == before
    r = hod.post('/hod/forms/deadline-phase',
                 data={'_csrf': tok, 'phase': 'phase9', 'deadline_at': future_deadline(5)})
    assert r.status_code == 400


def test_dialog_offers_current_and_phase_scope(app, admin, pending_setup):
    ids = _phase_forms(app, admin, pending_setup)
    html = pending_setup['hod'].get('/hod/forms').get_data(as_text=True)
    assert 'This form only (current)' in html and 'name="scope" value="phase"' in html
    assert '/hod/forms/deadline-phase' in html
    row = _forms_row(html, ids['open'])
    assert 'data-phase="phase1"' in row and 'data-phase-label="Phase 1"' in row
