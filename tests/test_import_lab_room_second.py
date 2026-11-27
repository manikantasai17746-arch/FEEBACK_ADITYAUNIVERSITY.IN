"""Timetable import carries Lab / Second Emp / Room No (teachers no longer set them).

Uses a tiny synthetic workbook and its own throwaway database.
"""
import os
import tempfile
from io import BytesIO

import openpyxl

from feedback_system import create_app, db
from feedback_system.import_data import import_timetable, read_workbook, summarize

HEAD = ['S.No', 'Time Table', 'Course Code', 'Course Name', 'Emp.Code', 'Emp.Name',
        'Roll.No', 'Lab', 'Second Emp.Code', 'Second Emp.Name', 'Room No']
ROWS = [
    # section with a lab, a second faculty and a room
    (1, 'Time Table-1', 'X101', 'Algorithms', 9001, 'MAIN ONE', 'T26A001', 'YES', 9002, 'SECOND ONE', 'CB-101'),
    (2, 'Time Table-1', 'X101', 'Algorithms', 9001, 'MAIN ONE', 'T26A002', 'YES', 9002, 'SECOND ONE', 'CB-101'),
    # theory-only section
    (3, 'Time Table-1', 'X102', 'Maths', 9003, 'MAIN TWO', 'T26A001', 'NO', None, None, 'CB-102'),
    # lab but no second faculty
    (4, 'Time Table-1', 'X103', 'Networks', 9004, 'MAIN THREE', 'T26A001', 'YES', None, None, 'CB-103'),
]


def _workbook(rows=ROWS, head=HEAD):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'TST I SEM'
    ws.append(['#VALUE!'])
    ws.append(['Time Table Selection List'])
    ws.append(['TST I Semester, Test Branch'])
    ws.append(head)
    for r in rows:
        ws.append(list(r))
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _app():
    return create_app({
        'TESTING': True, 'DATABASE': os.path.join(tempfile.mkdtemp(), 'imp.db'),
        'DATABASE_URL': None, 'SECRET_KEY': 'k', 'AUTO_IMPORT': False,
        'ADMIN_EMAIL': 'admin@college.edu', 'ADMIN_PASSWORD': 'admin123',
        'DEFAULT_TEACHER_PASSWORD': 'teacher@123', 'DEFAULT_STUDENT_PASSWORD': 'student@123',
        'TOTP_ENCRYPTION_KEY': 'slFqyvDkoDYXvVZAqiTyPsKcY1ekJ684A0nVc5i4JME=',
    })


def _run(app, buf):
    raw, names, report = read_workbook(buf)
    stats = import_timetable(app, 1, '2026-27', progress=lambda *_: None,
                             raw_data=(raw, names, report['extras']))
    return raw, report, stats


def test_extra_columns_become_lab_room_and_second_faculty():
    app = _app()
    with app.app_context():
        raw, report, stats = _run(app, _workbook())
        info = summarize(raw, report)
        assert (info['lab_sessions'], info['no_lab_sessions'],
                info['second_faculty'], info['rooms']) == (2, 1, 1, 3)
        assert stats['lab_settings'] == 3 and stats['second_faculty'] == 1

        rows = {r['subject_code']: r for r in db.query(
            """SELECT cs.subject_code, s.lab_status, s.lab_available, s.room
               FROM teacher_subject_settings s JOIN class_sessions cs ON cs.id = s.session_id""")}
        assert (rows['X101']['lab_status'], rows['X101']['room']) == ('available', 'CB-101')
        assert rows['X102']['lab_status'] == 'none' and rows['X102']['lab_available'] == 0
        assert rows['X103']['lab_status'] == 'available'

        sfa = db.query('SELECT * FROM second_faculty_assignments')
        assert len(sfa) == 1
        assert (sfa[0]['subject_code'], sfa[0]['second_emp_code'], sfa[0]['main_emp_code'],
                sfa[0]['status'], sfa[0]['source'], sfa[0]['room']) == (
            'X101', '9002', '9001', 'confirmed', 'import', 'CB-101')
        assert db.query("SELECT 1 FROM users WHERE role='teacher' AND emp_code='9002'")


def test_reimport_is_idempotent_and_never_overwrites_existing_settings():
    app = _app()
    with app.app_context():
        _run(app, _workbook())
        db.execute("UPDATE teacher_subject_settings SET room='MANUAL-1' "
                   "WHERE session_id=(SELECT id FROM class_sessions WHERE subject_code='X101')")
        _run(app, _workbook())
        assert len(db.query('SELECT 1 FROM second_faculty_assignments')) == 1
        assert len(db.query('SELECT 1 FROM teacher_subject_settings')) == 3
        room = db.query("SELECT s.room FROM teacher_subject_settings s JOIN class_sessions cs "
                        "ON cs.id = s.session_id WHERE cs.subject_code='X101'")[0]['room']
        assert room == 'MANUAL-1'


def test_workbook_without_extra_columns_still_imports():
    app = _app()
    head = HEAD[:7]
    rows = [r[:7] for r in ROWS]
    with app.app_context():
        raw, report, stats = _run(app, _workbook(rows, head))
        assert report['extras'] == {} and summarize(raw, report)['has_extras'] is False
        assert stats['lab_settings'] == 0 and stats['second_faculty'] == 0
        assert not db.query('SELECT 1 FROM teacher_subject_settings')


def test_second_faculty_equal_to_main_is_skipped():
    app = _app()
    rows = [(1, 'Time Table-1', 'X101', 'Algorithms', 9001, 'MAIN ONE', 'T26A001', 'YES',
             9001, 'MAIN ONE', 'CB-101')]
    with app.app_context():
        _run(app, _workbook(rows))
        assert not db.query('SELECT 1 FROM second_faculty_assignments')
