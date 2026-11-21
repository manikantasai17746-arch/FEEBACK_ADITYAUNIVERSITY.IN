"""Admin can review imported timetable files and delete them behind an e-mail OTP."""
import io
import os
import re
import sqlite3
import tempfile

import openpyxl
import pytest

from feedback_system import create_app, db, mailer
from feedback_system.import_data import USER_REFERENCES, ensure_admin

from helpers import login, token_from

YEAR = '2026-27'


@pytest.fixture()
def iapp():
    """A fresh, empty database per test (these tests create and destroy imports)."""
    tmpdir = tempfile.mkdtemp(prefix='ffs-import-delete-')
    application = create_app({
        'TESTING': True, 'DATABASE': os.path.join(tmpdir, 't.db'), 'DATABASE_URL': None,
        'SECRET_KEY': 'test-secret-key', 'AUTO_IMPORT': False,
        'ADMIN_EMAIL': 'admin@college.edu', 'ADMIN_PASSWORD': 'admin123',
        'DEFAULT_TEACHER_PASSWORD': 'teacher@123', 'DEFAULT_STUDENT_PASSWORD': 'student@123',
        'LOGIN_MAX_ATTEMPTS': 50, 'REQUIRE_ACCOUNT_SETUP': False,
        'TOTP_ENCRYPTION_KEY': 'slFqyvDkoDYXvVZAqiTyPsKcY1ekJ684A0nVc5i4JME=',
    })
    with application.app_context():
        ensure_admin(db.get_db(), 'admin@college.edu', 'admin123')
        db.get_db().commit()
    return application


@pytest.fixture()
def sent(monkeypatch):
    box = []
    monkeypatch.setattr(mailer, 'send_email',
                        lambda to, subject, text, html=None: box.append((to, subject, text)) or True)
    monkeypatch.setenv('DELETE_OTP_EMAIL', 'dean-office@example.com')
    return box


def _workbook(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'CSE'
    ws.append(['Aditya University'])
    ws.append(['Student timetable'])
    ws.append(['III Semester, Computer Science'])
    ws.append(['Time Table', 'Course Code', 'Course Name', 'Emp.Code', 'Emp.Name', 'Roll.No'])
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


ROWS = [('TT1', 'CS101', 'Maths', 'T100', 'Teacher One', '24A01'),
        ('TT1', 'CS102', 'Physics', 'T101', 'Teacher Two', '24A01'),
        ('TT1', 'CS101', 'Maths', 'T100', 'Teacher One', '24A02'),
        ('TT1', 'CS102', 'Physics', 'T101', 'Teacher Two', '24A02')]


def _admin(app):
    c = app.test_client()
    assert login(c, '/admin/login', {'email': 'admin@college.edu',
                                     'password': 'admin123'}).status_code == 302
    return c


def _do_import(c, semester=3, rows=ROWS, name='sem3.xlsx'):
    tok = token_from(c, '/admin/import')
    return c.post('/admin/import', data={
        '_csrf': tok, 'semester': str(semester), 'academic_year': YEAR, 'action': 'import',
        'workbook': (_workbook(rows), name)}, content_type='multipart/form-data')


def _count(app, table):
    with app.app_context():
        return db.query(f'SELECT COUNT(*) c FROM {table}', one=True)['c']


def _import_id(app):
    with app.app_context():
        return db.query('SELECT id FROM timetable_imports ORDER BY id', one=True)['id']


def _request_code(c, iid, sent):
    tok = token_from(c, '/admin/import')
    r = c.post(f'/admin/import/{iid}/delete/request', data={'_csrf': tok})
    assert r.status_code == 302
    return re.search(r'\n\s+(\d{6})\n', sent[-1][2]).group(1)


def test_imported_file_is_listed(iapp):
    c = _admin(iapp)
    assert 'No timetable has been imported yet' in c.get('/admin/import').get_data(as_text=True)
    assert _do_import(c).status_code == 200
    page = c.get('/admin/import').get_data(as_text=True)
    assert 'sem3.xlsx' in page and YEAR in page and 'Delete…' in page


def test_delete_needs_correct_otp_then_removes_everything(iapp, sent):
    c = _admin(iapp)
    _do_import(c)
    iid = _import_id(iapp)
    assert _count(iapp, 'timetables') == 1 and _count(iapp, 'class_sessions') == 2
    assert _count(iapp, 'enrollments') == 2 and _count(iapp, 'assignments') == 4

    code = _request_code(c, iid, sent)
    assert sent[-1][0] == 'dean-office@example.com'      # goes to the approval mailbox
    tok = token_from(c, f'/admin/import/{iid}/delete')

    # Wrong code: nothing is deleted.
    c.post(f'/admin/import/{iid}/delete', data={'_csrf': tok, 'code': '000000'})
    assert _count(iapp, 'timetables') == 1 and _count(iapp, 'timetable_imports') == 1

    # Calling the confirm URL without a code, or from a logged-out client, deletes nothing.
    assert iapp.test_client().post(f'/admin/import/{iid}/delete',
                                   data={'code': code}).status_code in (302, 400, 401, 403)
    assert _count(iapp, 'timetables') == 1

    # A wrong guess does not burn the real code; the right one now deletes.
    r = c.post(f'/admin/import/{iid}/delete', data={'_csrf': tok, 'code': code})
    assert r.status_code == 302
    for table in ('timetables', 'class_sessions', 'enrollments', 'session_students',
                  'assignments', 'timetable_imports'):
        assert _count(iapp, table) == 0, table
    with iapp.app_context():
        left = db.query("SELECT COUNT(*) c FROM users WHERE role IN ('student','teacher')",
                        one=True)['c']
    assert left == 0                                     # import-only accounts removed
    assert _count(iapp, 'users') == 1                    # admin untouched

    # The same workbook can be imported again afterwards.
    assert _do_import(c).status_code == 200
    assert _count(iapp, 'timetables') == 1


def test_refused_without_delete_mailbox(iapp, monkeypatch):
    monkeypatch.delenv('DELETE_OTP_EMAIL', raising=False)
    mails = []
    monkeypatch.setattr(mailer, 'send_email', lambda *a, **k: mails.append(a) or True)
    c = _admin(iapp)
    _do_import(c)
    iid = _import_id(iapp)
    c.post(f'/admin/import/{iid}/delete/request', data={'_csrf': token_from(c, '/admin/import')})
    assert mails == [] and _count(iapp, 'timetables') == 1
    # and the confirm page is unreachable without a pending code
    assert c.get(f'/admin/import/{iid}/delete').status_code == 302


def test_accounts_used_elsewhere_are_kept(iapp, sent):
    c = _admin(iapp)
    _do_import(c, semester=3)
    # Student 24A01 and teacher T100 also appear in a second semester's import.
    other = [('TT9', 'CS201', 'Algo', 'T100', 'Teacher One', '24A01'),
             ('TT9', 'CS201', 'Algo', 'T100', 'Teacher One', '24A03')]
    _do_import(c, semester=4, rows=other, name='sem4.xlsx')
    with iapp.app_context():
        sem3 = db.query('SELECT id FROM timetable_imports WHERE semester=3', one=True)['id']
    code = _request_code(c, sem3, sent)
    c.post(f'/admin/import/{sem3}/delete', data={
        '_csrf': token_from(c, f'/admin/import/{sem3}/delete'), 'code': code})
    with iapp.app_context():
        rolls = {r['username'] for r in db.query("SELECT username FROM users WHERE role='student'")}
        emps = {r['username'] for r in db.query("SELECT username FROM users WHERE role='teacher'")}
        sems = [r['semester'] for r in db.query('SELECT semester FROM timetable_imports')]
    assert rolls == {'24A01', '24A03'}      # 24A02 existed only for sem 3
    assert emps == {'T100'}                 # T101 existed only for sem 3
    assert sems == [4]                      # the other semester's import is untouched
    assert _count(iapp, 'enrollments') == 2 and _count(iapp, 'class_sessions') == 1


def test_locked_when_feedback_form_uses_the_import(iapp, sent):
    c = _admin(iapp)
    _do_import(c)
    iid = _import_id(iapp)
    with iapp.app_context():
        tt = db.query('SELECT id FROM timetables', one=True)['id']
        db.execute("INSERT INTO feedback_forms(title, timetable_id) VALUES('F', ?)", (tt,))
    assert 'Locked' in c.get('/admin/import').get_data(as_text=True)
    c.post(f'/admin/import/{iid}/delete/request', data={'_csrf': token_from(c, '/admin/import')})
    assert sent == []                                    # no code is even sent
    assert _count(iapp, 'timetables') == 1


def test_code_issued_before_form_creation_cannot_delete(iapp, sent):
    c = _admin(iapp)
    _do_import(c)
    iid = _import_id(iapp)
    code = _request_code(c, iid, sent)
    with iapp.app_context():
        tt = db.query('SELECT id FROM timetables', one=True)['id']
        db.execute("INSERT INTO feedback_forms(title, timetable_id) VALUES('F', ?)", (tt,))
    c.post(f'/admin/import/{iid}/delete', data={
        '_csrf': token_from(c, f'/admin/import/{iid}/delete'), 'code': code})
    assert _count(iapp, 'timetables') == 1 and _count(iapp, 'class_sessions') == 2


def test_non_admin_cannot_reach_delete(iapp, sent):
    c = _admin(iapp)
    _do_import(c)
    iid = _import_id(iapp)
    anon = iapp.test_client()
    assert anon.post(f'/admin/import/{iid}/delete/request').status_code in (302, 400, 401, 403)
    assert anon.get(f'/admin/import/{iid}/delete').status_code in (302, 401, 403)
    assert sent == [] and _count(iapp, 'timetables') == 1


def test_user_reference_list_matches_schema(iapp):
    """Fails if a new table points at users.id but is missing from USER_REFERENCES."""
    conn = sqlite3.connect(iapp.config['DATABASE'])
    live = set()
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        for fk in conn.execute(f'PRAGMA foreign_key_list({table})'):
            if fk[2] == 'users':
                live.add((table, fk[3]))
    assert live == set(USER_REFERENCES)
