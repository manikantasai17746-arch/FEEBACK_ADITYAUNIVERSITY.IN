"""HOD Pending submissions: the search box and per-row search text are rendered."""
import re

from feedback_system import db
from test_hod_feedback_second_and_results import app, client  # noqa: F401
from test_pending_all_categories import _three_forms  # noqa: F401


def test_pending_page_has_search_and_row_text(app, client):
    c, sfa, fid = _three_forms(app, client)
    html = c.get('/hod/pending?form_id=%d' % fid['main']).get_data(as_text=True)
    # search box, count, empty-state and the script/stylesheet that drive it
    assert 'id="pdQ"' in html and 'type="search"' in html
    assert 'id="pdCount"' in html and 'id="pdEmpty"' in html
    assert 'js/hod_pending_search.js' in html and 'css/hod_pending_search.css' in html
    # the search box is NOT inside the form-selector <form> (Enter must not reload the page)
    form_block = re.search(r'<form method="get".*?</form>', html, re.S).group(0)
    assert 'pdQ' not in form_block
    # every teacher row carries lower-cased, searchable text with the teacher's emp ID
    with app.app_context():
        emp = db.query('SELECT emp_code FROM users WHERE id=?', (sfa['main_teacher_user_id'],),
                       one=True)['emp_code']
    rows = re.findall(r'<tr class="pd-row" data-q="([^"]*)"', html)
    assert rows, 'no searchable teacher rows rendered'
    assert any(emp.lower() in r for r in rows)
    assert all(r == r.lower() for r in rows)


def test_dashboard_loads_search_assets(app, client):
    c, _sfa, _fid = _three_forms(app, client)
    html = c.get('/hod/').get_data(as_text=True)
    assert 'js/hod_pending_search.js' in html and 'css/hod_pending_search.css' in html
