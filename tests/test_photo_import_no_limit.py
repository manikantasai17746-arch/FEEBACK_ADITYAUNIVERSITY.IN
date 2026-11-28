"""The admin teacher-photo Excel import has NO upload size limit.

Every other upload keeps the global MAX_UPLOAD_MB limit.  The global limit is
set very low here so a small generated file proves the difference.
"""
import os
import tempfile
from io import BytesIO

import openpyxl

from feedback_system import create_app, db
from helpers import get_csrf, login


def _app(limit_bytes=200 * 1024):
    return create_app({
        'TESTING': True, 'DATABASE': os.path.join(tempfile.mkdtemp(), 'lim.db'),
        'DATABASE_URL': None, 'SECRET_KEY': 'k', 'AUTO_IMPORT': False,
        'ADMIN_EMAIL': 'admin@college.edu', 'ADMIN_PASSWORD': 'admin123',
        'DEFAULT_TEACHER_PASSWORD': 'teacher@123', 'DEFAULT_STUDENT_PASSWORD': 'student@123',
        'TOTP_ENCRYPTION_KEY': 'slFqyvDkoDYXvVZAqiTyPsKcY1ekJ684A0nVc5i4JME=',
        'LOGIN_MAX_ATTEMPTS': 50, 'REQUIRE_ACCOUNT_SETUP': False,
        'MAX_CONTENT_LENGTH': limit_bytes,
    })


def _big_workbook(min_bytes):
    """A valid .xlsx larger than ``min_bytes`` (random text barely compresses)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['S.No', 'Time Table', 'Emp.Code', 'Emp.Name', 'Photo'])
    n = 0
    while True:
        n += 1
        ws.append([n, 'T', f'9{n:05d}', os.urandom(24).hex(), None])
        if n % 2000 == 0:
            buf = BytesIO()
            wb.save(buf)
            if buf.tell() > min_bytes:
                return buf.getvalue()


def _admin(app):
    from feedback_system.import_data import set_admin_credentials
    with app.app_context():
        set_admin_credentials(db.get_db(), 'admin@college.edu', 'admin123')
    c = app.test_client()
    login(c, '/admin/login', {'email': 'admin@college.edu', 'password': 'admin123'})
    return c


def test_photo_import_accepts_file_larger_than_the_global_limit():
    app = _app(limit_bytes=200 * 1024)
    raw = _big_workbook(400 * 1024)
    assert len(raw) > app.config['MAX_CONTENT_LENGTH']
    c = _admin(app)
    tok = get_csrf(c.get('/admin/photos/import').get_data(as_text=True))
    r = c.post('/admin/photos/import', data={
        '_csrf': tok, 'overwrite': '1',
        'workbook': (BytesIO(raw), 'photos.xlsx')},
        content_type='multipart/form-data')
    assert r.status_code != 413          # not rejected for size
    assert r.status_code in (200, 400)   # processed (no pictures, so a normal result)


def test_import_page_does_not_advertise_a_size_limit():
    app = _app()
    c = _admin(app)
    html = c.get('/admin/photos/import').get_data(as_text=True)
    assert 'Maximum file size' not in html
    assert 'No file size limit' in html


def test_other_uploads_keep_the_global_limit():
    app = _app(limit_bytes=200 * 1024)
    c = _admin(app)
    tok = get_csrf(c.get('/admin/photos/import').get_data(as_text=True))
    big = b'x' * (400 * 1024)
    # the timetable import page is capped as before
    r = c.post('/admin/import', data={'_csrf': tok,
                                      'workbook': (BytesIO(big), 'big.xlsx')},
               content_type='multipart/form-data')
    assert r.status_code == 413
