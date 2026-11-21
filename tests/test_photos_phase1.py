"""Phase 1: migration v19 + shared employee photo system."""
import io
import re

import pytest
from PIL import Image

from feedback_system import db, pg, photos, schema
from helpers import (eligible_rolls, find_session, give_feedback_code, login, login_student,
                     make_form, publish_form)


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


def _admin_upload(app, emp, raw, filename='me.png', ctype='image/png'):
    """Upload through the admin's own client (teachers can no longer upload)."""
    c = app.test_client()
    assert login(c, '/admin/login',
                 {'email': 'admin@college.edu', 'password': 'admin123'}).status_code == 302
    return _upload(c, emp, raw, filename, ctype)


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
    r = login_student(c, '25B11CS015')
    assert r.status_code == 302
    return c


# ---------------------------------------------------------------- migration
def test_migration_v19_applied(app):
    with app.app_context():
        assert schema.MIGRATIONS[-1][0] >= 19
        # Shared test DB: earlier tests legitimately write rows, so check that the
        # tables exist (have columns) rather than that they are empty.
        for t in ('employee_photos', 'teacher_subject_settings', 'lab_faculty_assignments'):
            assert {r['name'] for r in db.query(f'PRAGMA table_info({t})')}, t
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
        # Shared test DB: pick a session with no settings row yet (earlier tests
        # may have inserted one for the first sessions).
        row = db.query('SELECT cs.id, cs.teacher_user_id FROM class_sessions cs '
                       'WHERE cs.is_deleted=0 AND NOT EXISTS (SELECT 1 FROM '
                       'teacher_subject_settings tss WHERE tss.session_id=cs.id) '
                       'ORDER BY cs.id LIMIT 1', one=True)
        assert row, 'need a session without a settings row'
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
def test_upload_replace_and_serve(tclient, app, admin, monkeypatch):
    r = _admin_upload(app, '6817', _img('PNG'))
    assert r.status_code == 302
    with app.app_context():
        row = db.query('SELECT filename, mime FROM employee_photos WHERE emp_code=?',
                       ('6817',), one=True)
        assert row['filename'] == 'employee_photos/6817.png' and row['mime'] == 'image/png'
    served = tclient.get('/employee-photo/6817')
    assert served.mimetype == 'image/png' and served.headers['ETag']
    session_id = find_session(app, '6817', '2501IT07')
    form_id = make_form(admin, session_id)
    publish_form(admin, form_id)
    student = app.test_client()
    login_student(student, eligible_rolls(app, session_id)[0])
    give_feedback_code(student)
    form_url = f'/student/form/{form_id}'
    html = student.get(form_url).get_data(as_text=True)
    assert 'src="/employee-photo/6817"' in html
    assert 'class="photo-fallback"' not in html
    assert student.get('/employee-photo/6817').data == served.data
    with monkeypatch.context() as missing:
        missing.setattr(photos, 'photo_meta', lambda code: None)
        missing.setattr(photos, '_legacy_static_path', lambda code: None)
        html = student.get(form_url).get_data(as_text=True)
        assert 'class="photo-fallback"' in html
        assert 'src="/employee-photo/6817"' not in html
    # conditional request
    again = tclient.get('/employee-photo/6817', headers={'If-None-Match': served.headers['ETag']})
    assert again.status_code == 304
    # replace with a JPEG -> still exactly one row, new extension
    assert _admin_upload(app, '6817', _img('JPEG'), 'me.jpg', 'image/jpeg').status_code == 302
    with app.app_context():
        rows = db.query('SELECT filename FROM employee_photos WHERE emp_code=?', ('6817',))
        assert len(rows) == 1 and rows[0]['filename'] == 'employee_photos/6817.jpg'


def test_large_photo_is_downscaled(tclient, app):
    _admin_upload(app, '6817', _img('PNG', (2000, 1500)))
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
    r = _admin_upload(app, '6817', raw, name, ctype)
    assert r.status_code == 200                        # re-rendered with an error
    with app.app_context():
        after = db.query('SELECT updated_at FROM employee_photos WHERE emp_code=?',
                         ('6817',), one=True)
    assert (before and before['updated_at']) == (after and after['updated_at'])


def test_oversize_rejected(tclient, app, monkeypatch):
    monkeypatch.setattr(photos, 'MAX_UPLOAD_BYTES', 100)
    assert _admin_upload(app, '6817', _img('PNG')).status_code == 200


# ---------------------------------------------------------------- authorization
def test_teacher_cannot_manage_any_photo_not_even_their_own(tclient):
    assert tclient.get('/photos/manage/6247').status_code == 403
    assert tclient.get('/photos/manage/6817').status_code == 403      # own photo too
    html = tclient.get('/profile').get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
    for emp in ('6247', '6817'):
        r = tclient.post(f'/photos/manage/{emp}', data={
            '_csrf': tok, 'photo': (io.BytesIO(_img()), 'x.png', 'image/png')},
            content_type='multipart/form-data')
        assert r.status_code == 403


def test_student_cannot_upload(student_client):
    assert student_client.get('/photos/manage/6817').status_code == 403
    assert student_client.get('/photos/me').status_code == 403
    assert student_client.get('/admin/photos').status_code in (302, 403)
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


def test_csrf_required_on_upload(app):
    c = app.test_client()
    login(c, '/admin/login', {'email': 'admin@college.edu', 'password': 'admin123'})
    r = c.post('/photos/manage/6817', data={
        'photo': (io.BytesIO(_img()), 'x.png', 'image/png')},
        content_type='multipart/form-data')
    assert r.status_code == 400


def test_can_manage_rules(app):
    with app.app_context():
        teacher = {'role': 'teacher', 'emp_code': '6817'}
        assert not photos.can_manage(teacher, '6817')             # no self-upload
        assert not photos.can_manage(teacher, '6247')
        assert photos.can_manage({'role': 'admin'}, '6817')
        assert photos.can_manage({'role': 'dean', 'emp_code': 'D1'}, 'D1')
        assert not photos.can_manage({'role': 'student', 'emp_code': '6817'}, '6817')


# ---------------------------------------------------------------- admin: Excel import
def _sheet(rows, header_row=7):
    """Build a college-style photo sheet: S.No, Time Table, Emp.Code, Emp.Name, Photo."""
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    wb = Workbook()
    ws = wb.active
    ws.title = 'BCA I Sem'
    ws['A5'] = 'Class/Semester:BCA/I Sem'
    for i, h in enumerate(['S.No', 'Time Table', 'Emp.Code', 'Emp.Name', 'Photo'], 1):
        ws.cell(header_row, i, h)
    r = header_row + 1
    for code, name, raw in rows:
        ws.cell(r, 1, r - header_row)
        ws.cell(r, 2, 'Time Table-1')
        ws.cell(r, 3, code)
        ws.cell(r, 4, name)
        if raw:
            img = XLImage(io.BytesIO(raw))
            img.width, img.height = 60, 70
            ws.add_image(img, f'E{r}')
        ws.row_dimensions[r].height = 60
        r += 1
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _admin_client(app):
    c = app.test_client()
    assert login(c, '/admin/login',
                 {'email': 'admin@college.edu', 'password': 'admin123'}).status_code == 302
    return c


def _import(c, data, name='photos.xlsx', overwrite=True):
    html = c.get('/admin/photos/import').get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
    form = {'_csrf': tok, 'workbook': (io.BytesIO(data), name)}
    if overwrite:
        form['overwrite'] = '1'
    return c.post('/admin/photos/import', data=form, content_type='multipart/form-data')


def _two_teacher_codes(app):
    with app.app_context():
        rows = db.query("SELECT emp_code FROM users WHERE role='teacher' AND is_active=1 "
                        "AND emp_code NOT IN ('6817') ORDER BY id LIMIT 3")
        return [r['emp_code'] for r in rows]


def test_admin_excel_import_matches_photos_by_emp_code(app):
    a, b, c3 = _two_teacher_codes(app)
    with app.app_context():
        db.execute('DELETE FROM employee_photos WHERE emp_code IN (?,?,?)', (a, b, c3))
    blue, red = _img('PNG', color=(20, 40, 220)), _img('PNG', color=(220, 30, 30))
    data = _sheet([(a, 'A', blue), (b, 'B', red), ('ZZ9999', 'Nobody', blue), (c3, 'C', None)])
    r = _import(_admin_client(app), data)
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    with app.app_context():
        pa, pb = photos.get_photo(a), photos.get_photo(b)
        assert pa and pb and not photos.photo_meta(c3)         # row without a picture
        assert not photos.photo_meta('ZZ9999')                 # unknown code is not stored
        assert Image.open(io.BytesIO(pa['data'])).convert('RGB').getpixel((5, 5))[2] > 150
        assert Image.open(io.BytesIO(pb['data'])).convert('RGB').getpixel((5, 5))[0] > 150
    assert 'ZZ9999' in html and c3 in html                     # reported back to the admin


def test_admin_excel_import_overwrite_flag(app):
    a = _two_teacher_codes(app)[0]
    c = _admin_client(app)
    _import(c, _sheet([(a, 'A', _img('PNG', color=(20, 40, 220)))]))
    with app.app_context():
        before = photos.get_photo(a)['data']
    r = _import(c, _sheet([(a, 'A', _img('PNG', color=(220, 30, 30)))]), overwrite=False)
    assert b'Skipped' in r.data
    with app.app_context():
        assert photos.get_photo(a)['data'] == before
    _import(c, _sheet([(a, 'A', _img('PNG', color=(220, 30, 30)))]), overwrite=True)
    with app.app_context():
        assert photos.get_photo(a)['data'] != before


def test_admin_excel_import_rejects_bad_files(app):
    c = _admin_client(app)
    assert _import(c, b'not a zip file', 'x.xlsx').status_code == 400
    assert _import(c, _sheet([('6247', 'A', _img())]), 'x.csv').status_code == 400
    from openpyxl import Workbook
    wb = Workbook()
    wb.active['A1'] = 'nothing useful'
    out = io.BytesIO()
    wb.save(out)
    r = _import(c, out.getvalue())
    assert r.status_code == 400 and b'Emp.Code' in r.data
    html = c.get('/admin/photos/import').get_data(as_text=True)
    r = c.post('/admin/photos/import', data={'workbook': (io.BytesIO(b'x'), 'x.xlsx')},
               content_type='multipart/form-data')           # no CSRF token
    assert r.status_code in (400, 403)


def test_admin_photo_list_edit_and_remove(app):
    a = _two_teacher_codes(app)[0]
    c = _admin_client(app)
    _import(c, _sheet([(a, 'A', _img())]))
    html = c.get(f'/admin/photos?q={a}').get_data(as_text=True)
    assert a in html and 'Photo added' in html
    assert 'No employees match' in c.get(f'/admin/photos?q={a}&status=without').get_data(as_text=True)
    page = c.get(f'/photos/manage/{a}').get_data(as_text=True)
    assert 'Remove photo' in page                               # edit page offers removal
    tok = re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
    assert c.post(f'/admin/photos/{a}/delete', data={'_csrf': tok}).status_code == 302
    with app.app_context():
        assert not photos.photo_meta(a)
    assert c.post(f'/admin/photos/{a}/delete', data={}).status_code == 400   # CSRF


def test_only_admin_can_reach_photo_admin_pages(app, tclient, student_client):
    for client in (tclient, student_client, app.test_client()):
        for path in ('/admin/photos', '/admin/photos/import'):
            assert client.get(path).status_code in (302, 403)
        assert client.post('/admin/photos/6817/delete', data={}).status_code in (302, 400, 403)


def test_teacher_profile_is_read_only_for_photo(tclient):
    html = tclient.get('/profile').get_data(as_text=True)
    assert 'id="photoForm"' not in html and '/photos/manage/6817' not in html
    assert 'administrator' in html
    r = tclient.get('/photos/me')
    assert r.status_code == 302 and '/profile' in r.headers['Location']


def test_photo_url_is_not_browser_cached_across_import(app):
    """After an import the same /employee-photo/<code> URL must show the new photo.

    A fixed max-age made browsers keep the earlier initials avatar for minutes
    even though the list already said "Photo added".
    """
    code = _two_teacher_codes(app)[0]
    with app.app_context():
        db.execute('DELETE FROM employee_photos WHERE emp_code=?', (code,))
    c = _admin_client(app)
    before = c.get(f'/employee-photo/{code}')
    assert before.mimetype == 'image/svg+xml'
    assert 'max-age' not in before.headers['Cache-Control']
    _import(c, _sheet([(code, 'A', _img('PNG', color=(20, 40, 220)))]))
    after = c.get(f'/employee-photo/{code}',
                  headers={'If-None-Match': before.headers['ETag']})
    assert after.status_code == 200 and after.mimetype == 'image/png'
    assert 'max-age' not in after.headers['Cache-Control']
    assert after.headers['ETag'] != before.headers['ETag']
    # unchanged photo -> cheap 304
    assert c.get(f'/employee-photo/{code}',
                 headers={'If-None-Match': after.headers['ETag']}).status_code == 304


def _incell_sheet(rows):
    """College sheet whose pictures are Excel 365 'Place in Cell' images (richData)."""
    import re as _re
    import zipfile
    base = _sheet([(code, name, None) for code, name, _ in rows])
    zin = zipfile.ZipFile(io.BytesIO(base))
    out = io.BytesIO()
    n = len(rows)
    ns = 'http://schemas.microsoft.com/office/spreadsheetml/2017/richdata'
    with zipfile.ZipFile(out, 'w') as z:
        for item in zin.namelist():
            data = zin.read(item)
            if item == 'xl/worksheets/sheet1.xml':
                s = data.decode()
                for i in range(n):          # E8, E9, ... -> vm=1.. pointing at the picture
                    cell = f'E{8 + i}'
                    s = s.replace('</row>', '</row>', 1)
                    s = _re.sub(r'(<row r="%d"[^>]*>)' % (8 + i),
                                r'\1<c r="%s" t="e" vm="%d"><v>#VALUE!</v></c>' % (cell, i + 1), s)
                data = s.encode()
            if item == '[Content_Types].xml':
                data = data.decode().replace('</Types>', '<Default Extension="png" ContentType="image/png"/></Types>').encode() \
                    if 'Extension="png"' not in data.decode() else data
            z.writestr(item, data)
        for i, (_, _, raw) in enumerate(rows):
            z.writestr(f'xl/media/image{i + 1}.png', raw)
        z.writestr('xl/richData/richValueRel.xml',
                   '<richValueRels xmlns="%s/richvaluerel" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">%s</richValueRels>'
                   % ('http://schemas.microsoft.com/office/spreadsheetml/2022/richvaluerel',
                      ''.join(f'<rel r:id="rId{i + 1}"/>' for i in range(n))))
        z.writestr('xl/richData/_rels/richValueRel.xml.rels',
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>'
                   % ''.join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image{i + 1}.png"/>' for i in range(n)))
        z.writestr('xl/richData/rdrichvaluestructure.xml',
                   '<rvStructures xmlns="http://schemas.microsoft.com/office/spreadsheetml/2017/richdata" count="1"><s t="_localImage"><k n="_rvRel:LocalImageIdentifier" t="i"/><k n="CalcOrigin" t="i"/></s></rvStructures>')
        z.writestr('xl/richData/rdrichvalue.xml',
                   '<rvData xmlns="http://schemas.microsoft.com/office/spreadsheetml/2017/richdata" count="%d">%s</rvData>'
                   % (n, ''.join(f'<rv s="0"><v>{i}</v><v>5</v></rv>' for i in range(n))))
        z.writestr('xl/metadata.xml',
                   '<metadata xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:xlrd="http://schemas.microsoft.com/office/spreadsheetml/2017/richdata">'
                   '<futureMetadata name="XLRICHVALUE" count="%d">%s</futureMetadata>'
                   '<valueMetadata count="%d">%s</valueMetadata></metadata>'
                   % (n, ''.join(f'<bk><extLst><ext uri="{{3e2802c4-a4d2-4d8b-9148-e3be6c30e623}}"><xlrd:rvb i="{i}"/></ext></extLst></bk>' for i in range(n)),
                      n, ''.join(f'<bk><rc t="1" v="{i}"/></bk>' for i in range(n))))
    return out.getvalue()


def test_import_reads_place_in_cell_pictures(app):
    a, b, _ = _two_teacher_codes(app)
    with app.app_context():
        db.execute('DELETE FROM employee_photos WHERE emp_code IN (?,?)', (a, b))
    blue, red = _img('PNG', color=(20, 40, 220)), _img('PNG', color=(220, 30, 30))
    data = _incell_sheet([(a, 'A', blue), (b, 'B', red)])
    entries, no_image, problems = photos.read_photo_workbook(data)
    assert not problems and not no_image and [e['emp_code'] for e in entries] == [a, b]
    _import(_admin_client(app), data)
    with app.app_context():
        pa, pb = photos.get_photo(a), photos.get_photo(b)
        assert Image.open(io.BytesIO(pa['data'])).convert('RGB').getpixel((5, 5))[2] > 150
        assert Image.open(io.BytesIO(pb['data'])).convert('RGB').getpixel((5, 5))[0] > 150


def test_import_warns_when_pictures_cannot_be_matched(app):
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.drawing.spreadsheet_drawing import AbsoluteAnchor
    from openpyxl.drawing.xdr import XDRPoint2D, XDRPositiveSize2D
    code = _two_teacher_codes(app)[0]
    wb = Workbook(); ws = wb.active
    for i, h in enumerate(['S.No', 'Time Table', 'Emp.Code', 'Emp.Name', 'Photo'], 1):
        ws.cell(7, i, h)
    ws.cell(8, 3, code); ws.cell(8, 4, 'X')
    im = XLImage(io.BytesIO(_img('PNG')))
    im.anchor = AbsoluteAnchor(pos=XDRPoint2D(5000000, 700000), ext=XDRPositiveSize2D(600000, 700000))
    ws.add_image(im)
    buf = io.BytesIO(); wb.save(buf)
    result = photos.import_photo_workbook(buf.getvalue(), None)
    assert result['no_image'] and result['warnings']


def test_results_pages_use_shared_photo_route():
    """No template may read static/img/teachers directly (that ignores imported photos)."""
    import pathlib
    tpl = pathlib.Path(photos.__file__).parent / 'templates'
    bad = [p.name for p in tpl.rglob('*.html') if 'img/teachers/' in p.read_text(encoding='utf-8')]
    assert not bad, bad
