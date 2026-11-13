"""Phase 1: migration v19 + shared employee photo system."""
import io
import re

import pytest
from PIL import Image

from feedback_system import db, pg, photos, schema
from helpers import login


def _img(fmt='PNG', size=(200, 240), color=(30, 90, 160)):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, fmt)
    return buf.getvalue()


def _upload(client, emp, raw, filename='me.png', ctype='image/png'):
    html = client.get(f'/photos/manage/{emp}').get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
    return client.post(f'/photos/manage/{emp}', data={
        '_csrf': tok, 'photo': (io.BytesIO(raw), filename, ctype)},
        content_type='multipart/form-data')


def _login_session(client, user_id, role):
    with client.session_transaction() as s:
        import time
        s.update({'uid': user_id, 'role': role, 'name': 'x', 'token': 'tok',
                  'started': time.time(), 'last': time.time()})


@pytest.fixture()
def tclient(app, teacher):
    """Teacher 6817 past the forced password change (a separate, tested gate)."""
    with app.app_context():
        db.execute("UPDATE users SET must_change_password=0 WHERE emp_code='6817'")
    yield teacher
    with app.app_context():
        db.execute("UPDATE users SET must_change_password=0 WHERE emp_code='6817'")


@pytest.fixture()
def student_client(app):
    c = app.test_client()
    r = login(c, '/student/login', {'roll': '25B11CS015', 'password': 'student@123'})
    assert r.status_code == 302
    return c


# ---------------------------------------------------------------- migration
def test_migration_v19_applied(app):
    with app.app_context():
        assert schema.MIGRATIONS[-1][0] >= 19
        for t in ('employee_photos', 'teacher_subject_settings', 'lab_faculty_assignments'):
            assert db.query(f'SELECT COUNT(*) c FROM {t}', one=True)['c'] == 0
        cols = {r['name'] for r in db.query('PRAGMA table_info(second_faculty_assignments)')}
        assert {'created_by', 'source'} <= cols
        cols = {r['name'] for r in db.query('PRAGMA table_info(feedback_forms)')}
        assert 'lab_faculty_assignment_id' in cols


def test_v19_translates_to_postgres_cleanly():
    stmts = [s for v, _d, ss in schema.MIGRATIONS if v == 19 for s in ss]
    assert stmts
    for s in stmts:
        out = pg.to_postgres(s)
        assert 'BLOB' not in out and 'AUTOINCREMENT' not in out
    assert any('BYTEA' in pg.to_postgres(s) for s in stmts)


def test_settings_unique_per_session_and_lab_flag_checked(app):
    with app.app_context():
        conn = db.get_db()
        row = db.query('SELECT id, teacher_user_id FROM class_sessions LIMIT 1', one=True)
        conn.execute('INSERT INTO teacher_subject_settings(session_id, teacher_user_id, lab_available)'
                     ' VALUES(?,?,1)', (row['id'], row['teacher_user_id']))
        with pytest.raises(Exception):
            conn.execute('INSERT INTO teacher_subject_settings(session_id, teacher_user_id)'
                         ' VALUES(?,?)', (row['id'], row['teacher_user_id']))
        conn.rollback()
        with pytest.raises(Exception):
            conn.execute('INSERT INTO teacher_subject_settings(session_id, teacher_user_id, lab_available)'
                         ' VALUES(?,?,5)', (row['id'], row['teacher_user_id']))
        conn.rollback()


# ---------------------------------------------------------------- serving
def test_guest_cannot_fetch_photo(client):
    r = client.get('/employee-photo/6817')
    assert r.status_code == 302


def test_invalid_code_is_404(tclient):
    assert tclient.get('/employee-photo/..%2Fetc').status_code == 404
    assert tclient.get('/employee-photo/bad code!').status_code == 404


def test_legacy_static_photo_and_initials_fallback(tclient):
    r = tclient.get('/employee-photo/6817')           # legacy static jpg
    assert r.status_code == 200 and r.mimetype == 'image/jpeg'
    r = tclient.get('/employee-photo/NOSUCH1')       # nothing -> initials svg
    assert r.status_code == 200 and r.mimetype == 'image/svg+xml'
    assert b'<svg' in r.data


def test_initials_helper():
    assert photos.initials('DR. CHANDRA SEKHAR KOLLI') == 'CK'
    assert photos.initials('') == '?'
    assert photos.initials('Madonna') == 'MA'


# ---------------------------------------------------------------- upload
def test_upload_replace_and_serve(tclient, app):
    r = _upload(tclient, '6817', _img('PNG'))
    assert r.status_code == 302
    with app.app_context():
        row = db.query('SELECT filename, mime FROM employee_photos WHERE emp_code=?',
                       ('6817',), one=True)
        assert row['filename'] == 'employee_photos/6817.png' and row['mime'] == 'image/png'
    served = tclient.get('/employee-photo/6817')
    assert served.mimetype == 'image/png' and served.headers['ETag']
    # conditional request
    again = tclient.get('/employee-photo/6817', headers={'If-None-Match': served.headers['ETag']})
    assert again.status_code == 304
    # replace with a JPEG -> still exactly one row, new extension
    assert _upload(tclient, '6817', _img('JPEG'), 'me.jpg', 'image/jpeg').status_code == 302
    with app.app_context():
        rows = db.query('SELECT filename FROM employee_photos WHERE emp_code=?', ('6817',))
        assert len(rows) == 1 and rows[0]['filename'] == 'employee_photos/6817.jpg'


def test_large_photo_is_downscaled(tclient, app):
    _upload(tclient, '6817', _img('PNG', (2000, 1500)))
    with app.app_context():
        data = photos.get_photo('6817')['data']
    assert max(Image.open(io.BytesIO(data)).size) <= photos.MAX_SIDE


@pytest.mark.parametrize('raw,name,ctype', [
    (_img('GIF'), 'a.gif', 'image/gif'),
    (b'GIF89a not really', 'a.png', 'image/png'),
    (b'<?php echo 1;', 'shell.png', 'image/png'),
    (_img('PNG', (10, 10)), 'tiny.png', 'image/png'),
    (b'', 'empty.png', 'image/png'),
])
def test_bad_uploads_rejected(tclient, app, raw, name, ctype):
    with app.app_context():
        before = db.query('SELECT updated_at FROM employee_photos WHERE emp_code=?',
                          ('6817',), one=True)
    r = _upload(tclient, '6817', raw, name, ctype)
    assert r.status_code == 200                        # re-rendered with an error
    with app.app_context():
        after = db.query('SELECT updated_at FROM employee_photos WHERE emp_code=?',
                         ('6817',), one=True)
    assert (before and before['updated_at']) == (after and after['updated_at'])


def test_oversize_rejected(tclient, monkeypatch):
    monkeypatch.setattr(photos, 'MAX_UPLOAD_BYTES', 100)
    assert _upload(tclient, '6817', _img('PNG')).status_code == 200


# ---------------------------------------------------------------- authorization
def test_teacher_cannot_manage_someone_else(tclient):
    assert tclient.get('/photos/manage/6247').status_code == 403
    html = tclient.get('/photos/manage/6817').get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
    r = tclient.post('/photos/manage/6247', data={
        '_csrf': tok, 'photo': (io.BytesIO(_img()), 'x.png', 'image/png')},
        content_type='multipart/form-data')
    assert r.status_code == 403


def test_student_cannot_upload(student_client):
    assert student_client.get('/photos/manage/6817').status_code == 403
    assert student_client.get('/photos/me').status_code == 403
    assert student_client.get('/employee-photo/6817').status_code == 200  # may view


def test_admin_can_upload_for_anyone(admin, app):
    r = _upload(admin, '6247', _img('PNG'))
    assert r.status_code == 302
    with app.app_context():
        assert photos.photo_meta('6247')


def test_hod_scope_for_on_behalf_upload(app):
    with app.app_context():
        conn = db.get_db()
        t = db.query("SELECT u.id, u.emp_code, t.branch_id FROM class_sessions cs "
                     "JOIN users u ON u.id=cs.teacher_user_id "
                     "JOIN timetables t ON t.id=cs.timetable_id LIMIT 1", one=True)
        other = db.query('SELECT id FROM branches WHERE id<>? LIMIT 1', (t['branch_id'],), one=True)
        ids = {}
        for tag, branch in (('in', t['branch_id']), ('out', other['id'])):
            conn.execute("INSERT INTO users(role, username, name, emp_code, password_hash, is_active, "
                         "onboarding_complete) VALUES('hod', ?, ?, ?, 'x', 1, 1)",
                         (f'hod_{tag}_p1', f'HOD {tag}', f'HX{tag.upper()}'))
            uid = db.query('SELECT id FROM users WHERE username=?', (f'hod_{tag}_p1',), one=True)['id']
            conn.execute('INSERT INTO user_department_scopes(user_id, branch_id) VALUES(?,?)',
                         (uid, branch))
            ids[tag] = uid
        conn.commit()
        emp = t['emp_code']
    c_in, c_out = app.test_client(), app.test_client()
    _login_session(c_in, ids['in'], 'hod')
    _login_session(c_out, ids['out'], 'hod')
    assert c_in.get(f'/photos/manage/{emp}').status_code == 200
    assert c_out.get(f'/photos/manage/{emp}').status_code == 403
    # an HOD can always manage their own photo
    assert c_in.get('/photos/manage/HXIN').status_code == 200


def test_csrf_required_on_upload(tclient):
    r = tclient.post('/photos/manage/6817', data={
        'photo': (io.BytesIO(_img()), 'x.png', 'image/png')},
        content_type='multipart/form-data')
    assert r.status_code == 400
