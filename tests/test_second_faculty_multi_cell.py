"""A Second Emp.Code cell may list several people ("6470;6705").

Each person must get their own account and their own Second Faculty
assignment.  No account may be created whose employee code contains ';'.
Also covers scripts/remove_combined_teacher_accounts.py.
"""
import importlib.util
import os
import tempfile
from io import BytesIO

import openpyxl

from feedback_system import create_app, db
from feedback_system.import_data import (import_timetable, read_workbook,
                                         split_second_faculty, summarize)

HEAD = ['S.No', 'Time Table', 'Course Code', 'Course Name', 'Emp.Code', 'Emp.Name',
        'Roll.No', 'Lab', 'Second Emp.Code', 'Second Emp.Name', 'Room No']
ROWS = [
    (1, 'Time Table-1', 'X101', 'Algorithms', 9001, 'MAIN ONE', 'T26A001', 'YES',
     '9002;9003', 'SECOND ONE;SECOND TWO', 'CB-101'),
    (2, 'Time Table-1', 'X101', 'Algorithms', 9001, 'MAIN ONE', 'T26A002', 'YES',
     '9002;9003', 'SECOND ONE;SECOND TWO', 'CB-101'),
    # one person is also the main teacher of the section: must be skipped
    (3, 'Time Table-1', 'X102', 'Networks', 9004, 'MAIN TWO', 'T26A001', 'YES',
     '9004;9002', 'MAIN TWO;SECOND ONE', 'CB-102'),
]


def _workbook(rows=ROWS):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'TST I SEM'
    ws.append(['#VALUE!'])
    ws.append(['Time Table Selection List'])
    ws.append(['TST I Semester, Test Branch'])
    ws.append(HEAD)
    for r in rows:
        ws.append(list(r))
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _app():
    return create_app({
        'TESTING': True, 'DATABASE': os.path.join(tempfile.mkdtemp(), 'multi.db'),
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


# --------------------------------------------------------------------------- #
# split_second_faculty
# --------------------------------------------------------------------------- #
def test_split_pairs_codes_with_names():
    assert split_second_faculty('6470;6705', 'V V SARMA M;AALAMURI DURGA') == [
        ('6470', 'V V SARMA M'), ('6705', 'AALAMURI DURGA')]


def test_split_single_person_is_unchanged():
    assert split_second_faculty('6470', 'V V SARMA M') == [('6470', 'V V SARMA M')]


def test_split_trims_blanks_and_duplicates():
    assert split_second_faculty(' 6470 ; ;6470;6705 ', 'A; ;A;B') == [
        ('6470', 'A'), ('6705', 'B')]


def test_split_missing_name_falls_back_to_code():
    assert split_second_faculty('6470;6705', 'ONLY ONE') == [
        ('6470', 'ONLY ONE'), ('6705', '6705')]
    assert split_second_faculty('6470', '') == [('6470', '6470')]


def test_split_empty():
    assert split_second_faculty('', '') == []
    assert split_second_faculty(None, None) == []


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #
def test_import_creates_one_account_and_assignment_per_person():
    app = _app()
    with app.app_context():
        raw, report, stats = _run(app, _workbook())

        assert db.query("SELECT COUNT(*) AS n FROM users "
                        "WHERE emp_code LIKE '%;%' OR username LIKE '%;%'",
                        one=True)['n'] == 0
        codes = {r['emp_code'] for r in db.query(
            "SELECT emp_code FROM users WHERE role='teacher'")}
        assert {'9001', '9002', '9003', '9004'} <= codes

        pairs = {(r['subject_code'], r['second_emp_code']) for r in db.query(
            "SELECT subject_code, second_emp_code FROM second_faculty_assignments")}
        # X101: two people; X102: 9004 is the main teacher so only 9002 remains
        assert pairs == {('X101', '9002'), ('X101', '9003'), ('X102', '9002')}
        assert stats['second_faculty'] == 3
        assert summarize(raw, report)['second_faculty'] == 3


def test_second_faculty_account_gets_its_own_name():
    app = _app()
    with app.app_context():
        _run(app, _workbook())
        name = db.query("SELECT name FROM users WHERE emp_code='9003'", one=True)['name']
        assert name == 'SECOND TWO'


# --------------------------------------------------------------------------- #
# clean-up script
# --------------------------------------------------------------------------- #
def _script():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'scripts', 'remove_combined_teacher_accounts.py')
    spec = importlib.util.spec_from_file_location('remove_combined', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _plant_fake(app):
    """Recreate the old bug: a combined account holding one assignment."""
    conn = db.get_db()
    sfa = conn.execute("SELECT * FROM second_faculty_assignments LIMIT 1").fetchone()
    conn.execute("INSERT INTO users(role, username, name, emp_code, password_hash) "
                 "VALUES('teacher','9002;9003','SECOND ONE;SECOND TWO','9002;9003','x')")
    uid = conn.execute("SELECT id FROM users WHERE emp_code='9002;9003'").fetchone()[0]
    conn.execute(
        """INSERT INTO second_faculty_assignments(
               academic_year_id, semester_id, branch_id, timetable_id, session_id,
               subject_code, subject_name, main_emp_code, main_teacher_user_id,
               second_emp_code, second_teacher_user_id, period_no, status, source)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'confirmed','import')""",
        (sfa['academic_year_id'], sfa['semester_id'], sfa['branch_id'],
         sfa['timetable_id'], sfa['session_id'], 'FAKE', 'Fake',
         sfa['main_emp_code'], sfa['main_teacher_user_id'], '9002;9003', uid, 1))
    conn.commit()
    return uid


def test_cleanup_previews_without_changing_anything():
    app = _app()
    with app.app_context():
        _run(app, _workbook())
        _plant_fake(app)
        removed, skipped = _script().clean(db.get_db(), apply=False, log=lambda *_: None)
        assert len(removed) == 1 and not skipped
        assert db.query("SELECT COUNT(*) AS n FROM users WHERE emp_code LIKE '%;%'",
                        one=True)['n'] == 1


def test_cleanup_apply_removes_only_combined_accounts():
    app = _app()
    with app.app_context():
        _run(app, _workbook())
        before_real = db.query("SELECT COUNT(*) AS n FROM second_faculty_assignments "
                               "WHERE second_emp_code NOT LIKE '%;%'", one=True)['n']
        _plant_fake(app)
        removed, skipped = _script().clean(db.get_db(), apply=True, log=lambda *_: None)
        assert len(removed) == 1 and not skipped
        assert db.query("SELECT COUNT(*) AS n FROM users WHERE emp_code LIKE '%;%'",
                        one=True)['n'] == 0
        assert db.query("SELECT COUNT(*) AS n FROM second_faculty_assignments "
                        "WHERE second_emp_code LIKE '%;%'", one=True)['n'] == 0
        # real teachers and real assignments are untouched
        assert db.query("SELECT COUNT(*) AS n FROM second_faculty_assignments "
                        "WHERE second_emp_code NOT LIKE '%;%'", one=True)['n'] == before_real
        assert db.query("SELECT COUNT(*) AS n FROM users WHERE emp_code='9002'",
                        one=True)['n'] == 1


def test_cleanup_skips_account_that_is_in_real_use():
    app = _app()
    with app.app_context():
        _run(app, _workbook())
        uid = _plant_fake(app)
        conn = db.get_db()
        # the fake account also "teaches" a session -> must not be deleted
        sid = conn.execute("SELECT id FROM class_sessions LIMIT 1").fetchone()[0]
        conn.execute("UPDATE class_sessions SET teacher_user_id=? WHERE id=?", (uid, sid))
        conn.commit()
        removed, skipped = _script().clean(conn, apply=True, log=lambda *_: None)
        assert not removed and len(skipped) == 1
        assert conn.execute("SELECT COUNT(*) FROM users WHERE id=?",
                            (uid,)).fetchone()[0] == 1
