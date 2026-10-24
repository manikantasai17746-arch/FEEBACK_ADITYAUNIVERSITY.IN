"""Admin feedback requirements and automatic matching to published forms."""
from feedback_system import db, services

from helpers import login, token_from
from test_multi_round import _make_round, _session


def _admin_client(client):
    login(client, '/admin/login', {'email': 'admin@college.edu', 'password': 'admin123'})
    return client


def _form_scope(app, fid):
    with app.app_context():
        return db.query('SELECT branch_id, academic_year_id, semester_id FROM feedback_forms '
                        'WHERE id=?', (fid,), one=True)


def test_admin_adds_requirement_and_publish_links_it(app, client):
    sess = _session(app)
    fid = _make_round(app, sess, 'Req round', status='draft', publish=False)
    with app.app_context():
        db.execute("UPDATE feedback_forms SET subject_code='REQTEST1' WHERE id=?", (fid,))
    sc = _form_scope(app, fid)
    c = _admin_client(client)
    tok = token_from(c, '/admin/requirements')
    r = c.post('/admin/requirements/save', data={
        '_csrf': tok, 'title': 'Mid-sem feedback', 'branch_id': sc['branch_id'],
        'academic_year_id': sc['academic_year_id'] or '', 'semester_id': sc['semester_id'] or '',
        'category': 'main', 'subject_code': 'REQTEST1',
        'deadline': '2030-01-31', 'status': 'required'})
    assert r.status_code == 302
    with app.test_request_context():
        req = db.query("SELECT * FROM feedback_requirements WHERE title='Mid-sem feedback'",
                       one=True)
        assert req and req['matched_form_id'] is None
        services.set_form_status(fid, 'open')
        req = db.query('SELECT matched_form_id FROM feedback_requirements WHERE id=?',
                       (req['id'],), one=True)
        assert req['matched_form_id'] == fid


def test_invalid_requirement_rejected(app, client):
    c = _admin_client(client)
    tok = token_from(c, '/admin/requirements')
    with app.app_context():
        before = db.query('SELECT COUNT(*) c FROM feedback_requirements', one=True)['c']
    c.post('/admin/requirements/save', data={'_csrf': tok, 'title': '', 'branch_id': ''})
    with app.app_context():
        assert db.query('SELECT COUNT(*) c FROM feedback_requirements', one=True)['c'] == before
