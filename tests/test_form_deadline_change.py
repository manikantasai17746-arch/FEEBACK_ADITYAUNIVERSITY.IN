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
