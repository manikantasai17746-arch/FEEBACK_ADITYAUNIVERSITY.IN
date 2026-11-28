"""Import the timetable workbook into the database.

Builds, from the real source data:

* ``assignments``  - unchanged legacy shape, plus timetable/period/semester/year
* ``branches``, ``semesters``, ``academic_years``, ``timetables``
* ``users`` (teachers + students), ``class_sessions``, ``enrollments``

Everything is idempotent: re-running only inserts what is missing.
"""
import os
import re
import time
from collections import defaultdict

import openpyxl
from werkzeug.security import generate_password_hash

from . import db


def _row_id(row):
    """Return the primary-key value from a sqlite3.Row or mapping."""
    if row is None:
        return None
    try:
        return row["id"]
    except Exception:
        return row[0]


HEADER_ROWS = 4  # rows 1..4 are titles/headers in the source workbook


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _branch_name(title, row3):
    """Best-effort human readable branch name from the sheet title row."""
    if row3:
        text = str(row3)
        if ',' in text:
            tail = text.split(',', 1)[1].strip()
            if tail:
                return tail
    return title


def ensure_reference_data(conn, semester_number, academic_year_name):
    conn.execute('INSERT OR IGNORE INTO academic_years(name) VALUES(?)', (academic_year_name,))
    for n in range(1, 9):
        conn.execute('INSERT OR IGNORE INTO semesters(number, label) VALUES(?,?)',
                     (n, f'Semester {n}'))
    ay = conn.execute('SELECT id FROM academic_years WHERE name=?',
                      (academic_year_name,)).fetchone()[0]
    sem = conn.execute('SELECT id FROM semesters WHERE number=?',
                       (semester_number,)).fetchone()[0]
    return ay, sem


def ensure_admin(conn, email, password):
    """Create the first admin only when none exists. Never overwrites an existing admin.

    Returns the admin user id, or None when credentials are missing and no admin
    has been created yet (caller decides whether to raise).
    """
    row = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
    if row:
        return _row_id(row)
    if not email or not password:
        return None
    email = email.strip().lower()
    conn.execute(
        'INSERT INTO users(role, username, email, name, password_hash, is_active, '
        'must_change_password) VALUES(?, ?, ?, ?, ?, 1, 0)',
        ('admin', email, email, 'Administrator', generate_password_hash(password)))
    row = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
    return _row_id(row)


def set_admin_credentials(conn, email, password):
    """Explicitly create or replace the first admin's login credentials.

    Safe to re-run: updates username, email, and password_hash for the first
    admin row, or creates one when the table has no admin yet.
    """
    if not email or not password:
        raise RuntimeError(
            'Set ADMIN_EMAIL and ADMIN_PASSWORD in the environment before syncing admin credentials.')
    email = email.strip().lower()
    row = conn.execute(
        "SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
    if row:
        admin_id = _row_id(row)
        conn.execute(
            'UPDATE users SET username=?, email=?, password_hash=?, is_active=1, '
            'must_change_password=0 WHERE id=?',
            (email, email, generate_password_hash(password), admin_id))
    else:
        admin_id = ensure_admin(conn, email, password)
        if admin_id is None:
            raise RuntimeError('Failed to create admin account.')
    conn.commit()
    return admin_id


def reset_student_teacher_passwords(conn, teacher_password, student_password):
    """Reset all student and teacher accounts to their configured defaults."""
    if not teacher_password or not student_password:
        raise RuntimeError(
            'Set DEFAULT_TEACHER_PASSWORD and DEFAULT_STUDENT_PASSWORD before resetting accounts.')
    teacher_hash = generate_password_hash(teacher_password)
    student_hash = generate_password_hash(student_password)
    try:
        teacher_count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role='teacher'").fetchone()[0]
        student_count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0]
        conn.execute(
            "UPDATE users SET password_hash=?, must_change_password=1 WHERE role='teacher'",
            (teacher_hash,))
        conn.execute(
            "UPDATE users SET password_hash=? WHERE role='student'",
            (student_hash,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return teacher_count, student_count


# --------------------------------------------------------------------------- #
# Reading + validating a timetable workbook
# --------------------------------------------------------------------------- #
REQUIRED_HEADERS = {
    'timetable': ('time table', 'timetable'),
    'course_code': ('course code',),
    'course_name': ('course name',),
    'emp_code': ('emp.code', 'emp code', 'empcode'),
    'emp_name': ('emp.name', 'emp name', 'empname'),
    'roll': ('roll.no', 'roll no', 'rollno', 'roll'),
}
# Optional columns.  Teachers no longer log in to set these, so when the workbook
# carries them the import records lab availability, room and Second Faculty.
OPTIONAL_HEADERS = {
    'lab': ('lab', 'lab available', 'lab?'),
    'second_emp_code': ('second emp.code', 'second emp code', 'second empcode',
                        'second faculty emp.code', 'second faculty code'),
    'second_emp_name': ('second emp.name', 'second emp name', 'second empname',
                        'second faculty emp.name', 'second faculty name'),
    'room': ('room no', 'room no.', 'room', 'room number', 'lab room no'),
}
_YES = {'yes', 'y', 'true', '1', 'available'}
ACADEMIC_YEAR_RE = re.compile(r'^\d{4}-\d{2}$')


class ImportErrorReport(ValueError):
    """Raised when a workbook cannot be imported; ``problems`` lists why."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__('; '.join(self.problems[:5]))


def claim_timetable_import(conn, fingerprint, semester, academic_year, filename,
                           now=None, stale_after=3600):
    """Atomically claim an import, returning (state, import_id)."""
    now = int(time.time() if now is None else now)
    row = conn.execute(
        """INSERT INTO timetable_imports(
               fingerprint, semester, academic_year, filename, status, started_ts)
           VALUES(?,?,?,?, 'importing', ?)
           ON CONFLICT(fingerprint, semester, academic_year) DO NOTHING
           RETURNING id""",
        (fingerprint, semester, academic_year, filename, now)).fetchone()
    if row:
        conn.commit()
        return 'claimed', row[0]

    existing = conn.execute(
        """SELECT id, status, started_ts FROM timetable_imports
           WHERE fingerprint=? AND semester=? AND academic_year=?""",
        (fingerprint, semester, academic_year)).fetchone()
    if not existing:
        raise RuntimeError('Could not determine the status of the workbook import.')
    if existing[1] == 'completed':
        return 'completed', None
    if existing[2] < now - stale_after:
        row = conn.execute(
            """UPDATE timetable_imports SET filename=?, started_ts=?
               WHERE id=? AND status='importing' AND started_ts < ?
               RETURNING id""",
            (filename, now, existing[0], now - stale_after)).fetchone()
        if row:
            conn.commit()
            return 'claimed', row[0]
    return 'importing', None


def complete_timetable_import(conn, import_id):
    conn.execute("UPDATE timetable_imports SET status='completed' WHERE id=?",
                 (import_id,))
    conn.commit()


def release_timetable_import(conn, import_id):
    conn.execute("DELETE FROM timetable_imports WHERE id=? AND status='importing'",
                 (import_id,))
    conn.commit()


# --------------------------------------------------------------------------- #
# Reviewing and deleting imported timetable data
#
# The import is additive and does NOT tag rows with the file that produced them, so
# several workbooks imported for the same semester + academic year cannot be told
# apart afterwards.  Deletion is therefore scoped to a (semester, academic year)
# pair: it removes everything the imports for that pair created.
# --------------------------------------------------------------------------- #
_SCOPE_TIMETABLES = (
    "SELECT t.id FROM timetables t "
    "JOIN semesters s ON s.id = t.semester_id "
    "JOIN academic_years ay ON ay.id = t.academic_year_id "
    "WHERE s.number = ? AND ay.name = ?")
_SCOPE_SESSIONS = (
    f"SELECT cs.id FROM class_sessions cs WHERE cs.timetable_id IN ({_SCOPE_TIMETABLES})")

# Data that other features build on top of an import.  If any of it exists for the
# scope the import is locked: deleting it would orphan feedback, results or HOD work.
# (label, SQL counting the dependent rows; each takes (semester, year) twice or once)
_BLOCKERS = (
    ('feedback form(s), including archived ones',
     f"SELECT COUNT(*) FROM feedback_forms WHERE timetable_id IN ({_SCOPE_TIMETABLES}) "
     f"OR session_id IN ({_SCOPE_SESSIONS})", 4),
    ('form-to-session link(s)',
     f"SELECT COUNT(*) FROM form_sessions WHERE session_id IN ({_SCOPE_SESSIONS})", 2),
    ('student submission(s)',
     f"SELECT COUNT(*) FROM submissions WHERE session_id IN ({_SCOPE_SESSIONS})", 2),
    ('feedback response(s)',
     f"SELECT COUNT(*) FROM feedback_responses WHERE session_id IN ({_SCOPE_SESSIONS})", 2),
    ('published result(s)',
     f"SELECT COUNT(*) FROM result_publications WHERE session_id IN ({_SCOPE_SESSIONS})", 2),
    ('second-faculty assignment(s)',
     f"SELECT COUNT(*) FROM second_faculty_assignments WHERE timetable_id IN "
     f"({_SCOPE_TIMETABLES}) OR session_id IN ({_SCOPE_SESSIONS})", 4),
    ('lab faculty assignment(s)',
     f"SELECT COUNT(*) FROM lab_faculty_assignments WHERE session_id IN ({_SCOPE_SESSIONS})", 2),
    ('teacher room/lab setting(s)',
     f"SELECT COUNT(*) FROM teacher_subject_settings WHERE session_id IN ({_SCOPE_SESSIONS})", 2),
    ('HOD allocation(s)',
     f"SELECT COUNT(*) FROM hod_allocations WHERE timetable_id IN ({_SCOPE_TIMETABLES})", 2),
    ('student complaint(s)',
     f"SELECT COUNT(*) FROM student_complaints WHERE timetable_id IN ({_SCOPE_TIMETABLES})", 2),
)

# Every (table, column) that points at users.id.  A student/teacher account created by
# an import is only removed when none of these still reference it.  A test compares this
# list with the live schema so a future table cannot be forgotten.
USER_REFERENCES = (
    ('allocation_history', 'actor_user_id'), ('audit_log', 'actor_user_id'),
    ('class_sessions', 'teacher_user_id'),
    ('dean_hod_assignments', 'created_by'), ('dean_hod_assignments', 'hod_user_id'),
    ('dean_hod_assignments', 'dean_user_id'),
    ('email_codes', 'user_id'), ('employee_photos', 'uploaded_by'),
    ('enrollments', 'student_user_id'), ('feedback_forms', 'created_by'),
    ('feedback_requirements', 'created_by'), ('form_drafts', 'student_user_id'),
    ('form_eligible_students', 'student_user_id'),
    ('hod_allocations', 'created_by'), ('hod_allocations', 'hod_user_id'),
    ('improvement_action_log', 'actor_user_id'), ('improvement_actions', 'created_by'),
    ('improvement_actions', 'owner_user_id'), ('lab_batch_students', 'student_user_id'),
    ('lab_faculty_assignments', 'created_by'),
    ('lab_faculty_assignments', 'lab_teacher_user_id'),
    ('lab_faculty_assignments', 'main_teacher_user_id'),
    ('notifications', 'user_id'), ('question_sets', 'owner_user_id'),
    ('result_publications', 'unpublished_by'), ('result_publications', 'published_by'),
    ('second_faculty_assignments', 'second_teacher_user_id'),
    ('second_faculty_assignments', 'main_teacher_user_id'),
    ('second_faculty_import_batches', 'imported_by'),
    ('session_students', 'student_user_id'),
    ('student_authenticators', 'user_id'), ('student_backup_codes', 'user_id'),
    ('student_complaints', 'student_user_id'), ('submissions', 'student_user_id'),
    ('teacher_subject_settings', 'updated_by'),
    ('teacher_subject_settings', 'teacher_user_id'),
    ('user_department_scopes', 'user_id'),
)


def timetable_import_scope_counts(conn, semester, academic_year):
    """What an import for this semester + academic year put into the system."""
    p = (semester, academic_year)
    one = lambda sql, n=1: conn.execute(sql, p * n).fetchone()[0]    # noqa: E731
    return {
        'timetables': one(f"SELECT COUNT(*) FROM ({_SCOPE_TIMETABLES}) x"),
        'sessions': one(f"SELECT COUNT(*) FROM ({_SCOPE_SESSIONS}) x"),
        'students': one(
            f"SELECT COUNT(DISTINCT student_user_id) FROM enrollments "
            f"WHERE timetable_id IN ({_SCOPE_TIMETABLES})"),
        'teachers': one(
            f"SELECT COUNT(DISTINCT teacher_user_id) FROM class_sessions "
            f"WHERE timetable_id IN ({_SCOPE_TIMETABLES})"),
        'assignments': conn.execute(
            "SELECT COUNT(*) FROM assignments WHERE semester=? AND academic_year=?",
            p).fetchone()[0],
    }


def timetable_import_blockers(conn, semester, academic_year):
    """[(label, count)] of dependent data that makes this import un-deletable."""
    found = []
    for label, sql, times in _BLOCKERS:
        n = conn.execute(sql, (semester, academic_year) * (times // 2)).fetchone()[0]
        if n:
            found.append((label, n))
    return found


def list_timetable_imports(conn):
    """Every recorded workbook import (newest first) with what its semester holds now."""
    rows = conn.execute(
        """SELECT id, filename, semester, academic_year, status, started_ts
           FROM timetable_imports ORDER BY started_ts DESC, id DESC""").fetchall()
    cache, out = {}, []
    for r in rows:
        key = (r[2], r[3])
        if key not in cache:
            cache[key] = (timetable_import_scope_counts(conn, *key),
                          timetable_import_blockers(conn, *key))
        counts, blockers = cache[key]
        out.append({'id': r[0], 'filename': r[1], 'semester': r[2], 'academic_year': r[3],
                    'status': r[4], 'started_ts': r[5], 'counts': counts,
                    'blockers': blockers,
                    'imported_at': time.strftime('%d %b %Y, %H:%M UTC', time.gmtime(r[5])),
                    'siblings': sum(1 for x in rows if (x[2], x[3]) == key) - 1})
    return out


def get_timetable_import(conn, import_id):
    row = conn.execute(
        "SELECT id, filename, semester, academic_year, status, started_ts "
        "FROM timetable_imports WHERE id=?", (import_id,)).fetchone()
    return None if row is None else {
        'id': row[0], 'filename': row[1], 'semester': row[2], 'academic_year': row[3],
        'status': row[4], 'started_ts': row[5]}


def delete_timetable_import(conn, semester, academic_year):
    """Remove all imported timetable data for one semester + academic year.

    Deletes the timetables, class sessions, enrollments, eligibility rows and legacy
    assignments, forgets the recorded files (so the workbook may be imported again) and
    removes student/teacher accounts that exist only because of the import (no other
    record references them).  Refuses when feedback/results depend on the data.

    The caller owns the transaction: commit on success, roll back on any exception.
    Returns a dict of what was removed.
    """
    blockers = timetable_import_blockers(conn, semester, academic_year)
    if blockers:
        raise ValueError('This import is in use: ' +
                         ', '.join(f'{n} {label}' for label, n in blockers))
    p = (semester, academic_year)
    # Accounts the import may have created; decided after their links are gone.
    candidates = sorted({r[0] for r in conn.execute(
        f"""SELECT student_user_id FROM enrollments WHERE timetable_id IN ({_SCOPE_TIMETABLES})
            UNION SELECT student_user_id FROM session_students
                  WHERE session_id IN ({_SCOPE_SESSIONS})
            UNION SELECT teacher_user_id FROM class_sessions
                  WHERE timetable_id IN ({_SCOPE_TIMETABLES}) AND teacher_user_id IS NOT NULL""",
        p * 3).fetchall()})

    removed = {}
    def run(name, sql, params):
        cur = conn.execute(sql, params)
        removed[name] = cur.rowcount if cur.rowcount is not None else 0

    run('eligibility', f"DELETE FROM session_students WHERE session_id IN ({_SCOPE_SESSIONS})",
        p)
    run('enrollments', f"DELETE FROM enrollments WHERE timetable_id IN ({_SCOPE_TIMETABLES})", p)
    run('sessions', f"DELETE FROM class_sessions WHERE timetable_id IN ({_SCOPE_TIMETABLES})", p)
    run('timetables', f"DELETE FROM timetables WHERE id IN ({_SCOPE_TIMETABLES})", p)
    run('assignments', "DELETE FROM assignments WHERE semester=? AND academic_year=?", p)
    run('files', "DELETE FROM timetable_imports WHERE semester=? AND academic_year=?", p)

    unused = ' AND '.join(
        f'NOT EXISTS (SELECT 1 FROM {t} WHERE {c} = users.id)' for t, c in USER_REFERENCES)
    removed['accounts'] = 0
    for i in range(0, len(candidates), 500):
        chunk = candidates[i:i + 500]
        marks = ','.join('?' for _ in chunk)
        cur = conn.execute(
            f"DELETE FROM users WHERE role IN ('student','teacher') AND id IN ({marks}) "
            f"AND {unused}", tuple(chunk))
        removed['accounts'] += cur.rowcount or 0
    return removed


def _norm_header(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip().lower()


def _clean(value):
    """Cell -> trimmed string; whole-number floats lose the trailing '.0'."""
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _find_header(rows):
    """Return (index_in_rows, {field: column_index}) for the header row."""
    for i, row in enumerate(rows):
        cells = [_norm_header(c) for c in row]
        cols = {}
        for field, names in REQUIRED_HEADERS.items():
            for j, c in enumerate(cells):
                if c in names:
                    cols[field] = j
                    break
        if len(cols) == len(REQUIRED_HEADERS):
            for field, names in OPTIONAL_HEADERS.items():
                for j, c in enumerate(cells):
                    if c in names:
                        cols[field] = j
                        break
            return i, cols
    return None, {}


def split_second_faculty(codes, names=''):
    """Split a "Second Emp.Code" / "Second Emp.Name" cell pair into people.

    The college workbook may list several Second Faculty in one cell,
    separated by ``;`` (codes ``6470;6705`` with names ``A;B``).  Each code is
    one person.  Names are paired with codes by position; a missing name falls
    back to the code.  Blank pieces and repeated codes are dropped.
    Returns a list of ``(code, name)``.
    """
    code_list = [c.strip() for c in str(codes or '').split(';')]
    name_list = [n.strip() for n in str(names or '').split(';')]
    out, seen = [], set()
    for i, code in enumerate(code_list):
        if not code or code in seen:
            continue
        seen.add(code)
        name = name_list[i] if i < len(name_list) and name_list[i] else code
        out.append((code, name))
    return out


def _collect_extra(extras, warnings, key, vals):
    """Fold one row's Lab / Room No / Second Emp into its teaching session.

    Every student row of a session repeats the same values.  The first
    non-empty value wins; a different later value is reported, not applied.
    """
    lab = vals.get('lab', '').strip().lower()
    new = {
        'lab': None if not lab else (lab in _YES),
        'room': vals.get('room', '').strip(),
        'second_code': vals.get('second_emp_code', '').strip(),
        'second_name': vals.get('second_emp_name', '').strip(),
    }
    cur = extras.setdefault(key, {'lab': None, 'room': '', 'second_code': '',
                                  'second_name': ''})
    for field, value in new.items():
        if value in (None, ''):
            continue
        if cur[field] in (None, ''):
            cur[field] = value
        elif cur[field] != value:
            msg = (f'{key[0]} / {key[1]} / {key[2]}: different {field.replace("_", " ")} '
                   f'values in the same section ({cur[field]!r} kept, {value!r} ignored).')
            if msg not in warnings and len(warnings) < 20:
                warnings.append(msg)


def read_workbook(source):
    """Parse a timetable workbook (path or file-like object).

    Returns ``(raw, branch_names, report)`` where ``raw`` is a list of
    ``(dept, timetable, course_code, course_name, emp_code, emp_name, roll)``
    tuples.  ``report`` carries ``sheets``, ``skipped_rows`` and ``warnings``.
    Raises :class:`ImportErrorReport` if nothing usable is found.
    """
    try:
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
    except Exception as exc:
        raise ImportErrorReport(
            [f'This is not a readable .xlsx workbook ({type(exc).__name__}).'])

    raw, branch_names = [], {}
    report = {'sheets': [], 'skipped_rows': 0, 'warnings': [], 'extras': {}}
    extras = report['extras']          # (dept, timetable, course, emp) -> lab/room/second
    try:
        for ws in wb:
            head = list(ws.iter_rows(min_row=1, max_row=12, values_only=True))
            hidx, cols = _find_header(head)
            if hidx is None:
                report['warnings'].append(
                    f'Sheet "{ws.title}" skipped: no header row with '
                    f'Time Table / Course Code / Course Name / Emp.Code / '
                    f'Emp.Name / Roll.No.')
                continue
            title_row = head[2][0] if len(head) >= 3 and head[2] and hidx >= 3 else None
            branch_names[ws.title] = _branch_name(ws.title, title_row)
            count = 0
            for r in ws.iter_rows(min_row=hidx + 2, values_only=True):
                if not r or all(c is None or _clean(c) == '' for c in r):
                    continue
                vals = {f: _clean(r[j]) if j < len(r) else '' for f, j in cols.items()}
                if not all(vals[f] for f in ('roll', 'emp_code', 'course_code', 'timetable')):
                    report['skipped_rows'] += 1
                    continue
                if 'lab' in vals or 'room' in vals or 'second_emp_code' in vals:
                    _collect_extra(extras, report['warnings'],
                                   (ws.title, vals['timetable'], vals['course_code'],
                                    vals['emp_code']), vals)
                raw.append((ws.title, vals['timetable'], vals['course_code'],
                            vals['course_name'] or vals['course_code'],
                            vals['emp_code'], vals['emp_name'] or vals['emp_code'],
                            vals['roll'].upper()))
                count += 1
            report['sheets'].append((ws.title, count))
    finally:
        wb.close()

    if not raw:
        raise ImportErrorReport(
            report['warnings'] or ['No timetable rows were found in this workbook.'])
    if report['skipped_rows']:
        report['warnings'].append(
            f"{report['skipped_rows']} row(s) skipped because Roll.No, Emp.Code, "
            f"Course Code or Time Table was empty.")
    return raw, branch_names, report


def summarize(raw, report):
    """Counts shown on the 'Check file' preview (nothing is written)."""
    extras = report.get('extras') or {}
    return {
        'rows': len(raw),
        'branches': len({r[0] for r in raw}),
        'timetables': len({(r[0], r[1]) for r in raw}),
        'subjects': len({r[2] for r in raw}),
        'teachers': len({r[4] for r in raw}),
        'students': len({r[6] for r in raw}),
        'sheets': report['sheets'],
        'warnings': report['warnings'],
        'lab_sessions': sum(1 for e in extras.values() if e['lab']),
        'no_lab_sessions': sum(1 for e in extras.values() if e['lab'] is False),
        'second_faculty': len({(k, code) for k, e in extras.items() if e['lab']
                               for code, _n in split_second_faculty(
                                   e['second_code'], e['second_name'])
                               if code != k[3]}),   # k[3] = the main Emp.Code
        'rooms': sum(1 for e in extras.values() if e['room']),
        'has_extras': bool(extras),
    }


def validate_semester_year(semester_number, academic_year):
    try:
        semester_number = int(semester_number)
    except (TypeError, ValueError):
        raise ImportErrorReport(['Semester must be a number from 1 to 8.'])
    if not 1 <= semester_number <= 8:
        raise ImportErrorReport(['Semester must be a number from 1 to 8.'])
    academic_year = (academic_year or '').strip()
    if not ACADEMIC_YEAR_RE.match(academic_year):
        raise ImportErrorReport(['Academic year must look like 2025-26.'])
    return semester_number, academic_year


# --------------------------------------------------------------------------- #
# Main import
# --------------------------------------------------------------------------- #
def import_timetable(app, semester_number=3, academic_year=None, progress=print,
                     source=None, raw_data=None):
    """Import a timetable workbook for one semester / academic year.

    ``source`` is a path or file-like object (defaults to the configured
    workbook).  Callers that already parsed the file may pass ``raw_data`` as
    the ``(raw, branch_names)`` pair to avoid reading it twice.
    """
    academic_year = academic_year or os.environ.get('ACADEMIC_YEAR') or '2025-26'
    semester_number, academic_year = validate_semester_year(semester_number, academic_year)
    conn = db.get_db()

    # --- 1. read workbook -------------------------------------------------- #
    extras = {}
    if raw_data is not None:
        raw, branch_names = raw_data[0], raw_data[1]
        extras = raw_data[2] if len(raw_data) > 2 and raw_data[2] else {}
        label = 'uploaded workbook'
    else:
        source = source or app.config['XLSX_PATH']
        if isinstance(source, str) and not os.path.exists(source):
            raise FileNotFoundError(f'Timetable workbook not found: {source}')
        raw, branch_names, _report = read_workbook(source)
        extras = _report.get('extras') or {}
        label = os.path.basename(source) if isinstance(source, str) else 'uploaded workbook'
    progress(f'Read {len(raw)} timetable rows from {label}')

    # --- 2. period index per student (position within their own timetable) -- #
    subject_period = {}                       # (dept, roll, cc) -> 1-based period
    seen_order = defaultdict(dict)            # (dept, roll) -> {cc: period}
    for dept, tt, cc, cn, emp, empname, roll in raw:
        bucket = seen_order[(dept, roll)]
        if cc not in bucket:
            bucket[cc] = len(bucket) + 1
    for (dept, roll), bucket in seen_order.items():
        for cc, p in bucket.items():
            subject_period[(dept, roll, cc)] = p

    # --- 3. reference rows ------------------------------------------------- #
    ay_id, sem_id = ensure_reference_data(conn, semester_number, academic_year)
    progress(f'Academic year: {academic_year} (id={ay_id}), semester {semester_number}')

    # 3a. branches
    branch_id = {}
    for dept, name in branch_names.items():
        conn.execute('INSERT OR IGNORE INTO branches(code, name) VALUES(?,?)', (dept, name))
        branch_id[dept] = conn.execute('SELECT id FROM branches WHERE code=?',
                                       (dept,)).fetchone()[0]

    # 3b. legacy assignments (original shape + new columns)
    progress('Importing assignments...')
    conn.executemany(
        """INSERT OR IGNORE INTO assignments(
               roll, dept, course_code, course_name, emp_code, emp_name,
               timetable, period_no, semester, academic_year)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        [(roll, dept, cc, cn, emp, empname, tt,
          subject_period.get((dept, roll, cc)), semester_number, academic_year)
         for (dept, tt, cc, cn, emp, empname, roll) in raw])

    # 3c. teacher users
    teacher_hash = generate_password_hash(app.config['DEFAULT_TEACHER_PASSWORD'])
    teacher_users = {}
    for dept, tt, cc, cn, emp, empname, roll in raw:
        teacher_users.setdefault(emp, empname)
    for ex in extras.values():                 # Second Faculty need an account too
        if ex['lab']:
            for code, name in split_second_faculty(ex['second_code'], ex['second_name']):
                teacher_users.setdefault(code, name)
    conn.executemany(
        """INSERT OR IGNORE INTO users(role, username, name, emp_code, password_hash)
           VALUES('teacher', ?, ?, ?, ?)""",
        [(emp, name, emp, teacher_hash) for emp, name in teacher_users.items()])
    teacher_id = {row[0]: row[1] for row in
                  conn.execute("SELECT emp_code, id FROM users WHERE role='teacher'")}
    progress(f'Teachers: {len(teacher_id)}')

    # 3d. student users
    student_hash = generate_password_hash(app.config['DEFAULT_STUDENT_PASSWORD'])
    students = sorted({roll for (*_, roll) in raw})
    conn.executemany(
        """INSERT OR IGNORE INTO users(role, username, name, roll, password_hash)
           VALUES('student', ?, ?, ?, ?)""",
        [(roll, roll, roll, student_hash) for roll in students])
    student_id = {row[0]: row[1] for row in
                  conn.execute("SELECT roll, id FROM users WHERE role='student'")}
    progress(f'Students: {len(student_id)}')

    # 3e. timetables
    tt_pairs = sorted({(dept, tt) for dept, tt, *_ in raw})
    timetable_id = {}
    for dept, tt in tt_pairs:
        conn.execute(
            """INSERT OR IGNORE INTO timetables(
                   branch_id, semester_id, academic_year_id, code, name)
               VALUES(?,?,?,?,?)""", (branch_id[dept], sem_id, ay_id, tt, tt))
        timetable_id[(dept, tt)] = conn.execute(
            'SELECT id FROM timetables WHERE branch_id=? AND semester_id=? AND '
            'academic_year_id=? AND code=?', (branch_id[dept], sem_id, ay_id, tt)).fetchone()[0]
    progress(f'Timetables: {len(timetable_id)}')

    # 3f. class sessions (timetable + subject + teacher) with a stable period
    sess_period, sess_names = {}, {}
    for dept, tt, cc, cn, emp, empname, roll in raw:
        k = (dept, tt, cc, emp)
        p = subject_period.get((dept, roll, cc))
        if p is not None and (k not in sess_period or p < sess_period[k]):
            sess_period[k] = p
        sess_names[k] = cn
    conn.executemany(
        """INSERT OR IGNORE INTO class_sessions(
               timetable_id, subject_code, subject_name, teacher_user_id, period_no)
           VALUES(?,?,?,?,?)""",
        [(timetable_id[(dept, tt)], cc, sess_names[(dept, tt, cc, emp)],
          teacher_id.get(emp), period)
         for (dept, tt, cc, emp), period in sess_period.items()])
    session_id = {}
    for row in conn.execute(
            """SELECT cs.id, t.branch_id, t.code AS tt, cs.subject_code, u.emp_code
               FROM class_sessions cs
               JOIN timetables t ON t.id = cs.timetable_id
               LEFT JOIN users u ON u.id = cs.teacher_user_id"""):
        dept = next((d for d, n in branch_id.items() if n == row['branch_id']), None)
        session_id[(dept, row['tt'], row['subject_code'], row['emp_code'])] = row['id']
    progress(f'Class sessions: {len(session_id)}')

    # 3g. enrollments (each student belongs to exactly one timetable)
    student_tt = {}
    for dept, tt, cc, cn, emp, empname, roll in raw:
        student_tt[roll] = (dept, tt)
    conn.executemany(
        """INSERT OR IGNORE INTO enrollments(
               student_user_id, timetable_id, semester_id, academic_year_id)
           VALUES(?,?,?,?)""",
        [(student_id[roll], timetable_id[key], sem_id, ay_id)
         for roll, key in student_tt.items()])
    progress(f'Enrollments: {len(student_tt)}')

    # 3h. eligible student population per teaching session.
    # A student is eligible for a session only when the timetable assigns that
    # exact (timetable, subject, teacher) triple to them.
    conn.executemany(
        'INSERT OR IGNORE INTO session_students(session_id, student_user_id) VALUES(?,?)',
        [(session_id[(dept, tt, cc, emp)], student_id[roll])
         for (dept, tt, cc, cn, emp, empname, roll) in raw
         if (dept, tt, cc, emp) in session_id])
    n_elig = conn.execute('SELECT COUNT(*) FROM session_students').fetchone()[0]
    progress(f'Session eligibility rows: {n_elig}')

    # 3i. lab availability, room and Second Faculty straight from the workbook.
    # Existing values (set earlier) are never overwritten.
    n_lab = n_second = 0
    if extras:
        sess_info = {row['id']: row for row in conn.execute(
            """SELECT cs.id, cs.timetable_id, cs.subject_code, cs.subject_name,
                      cs.period_no, cs.teacher_user_id, t.branch_id
               FROM class_sessions cs JOIN timetables t ON t.id = cs.timetable_id""")}
        for (dept, tt, cc, emp), ex in extras.items():
            sid = session_id.get((dept, tt, cc, emp))
            info = sess_info.get(sid)
            if info is None or info['teacher_user_id'] is None:
                continue
            if ex['lab'] is not None or ex['room']:
                status = None if ex['lab'] is None else ('available' if ex['lab'] else 'none')
                conn.execute(
                    """INSERT OR IGNORE INTO teacher_subject_settings(
                           session_id, teacher_user_id, room, lab_available, lab_status)
                       VALUES(?,?,?,?,?)""",
                    (sid, info['teacher_user_id'], ex['room'] or None,
                     1 if ex['lab'] else 0, status))
                if status:
                    conn.execute(
                        """UPDATE teacher_subject_settings SET lab_status=?, lab_available=?
                           WHERE session_id=? AND lab_status IS NULL""",
                        (status, 1 if ex['lab'] else 0, sid))
                if ex['room']:
                    conn.execute(
                        """UPDATE teacher_subject_settings SET room=?
                           WHERE session_id=? AND (room IS NULL OR room='')""",
                        (ex['room'], sid))
                n_lab += 1
            if ex['lab']:
                for second, _name in split_second_faculty(ex['second_code'],
                                                          ex['second_name']):
                    if second == emp or second not in teacher_id:
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO second_faculty_assignments(
                               academic_year_id, semester_id, branch_id, timetable_id,
                               session_id, subject_code, subject_name, main_emp_code,
                               main_teacher_user_id, second_emp_code, second_teacher_user_id,
                               period_no, room, status, source)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'confirmed','import')""",
                        (ay_id, sem_id, info['branch_id'], info['timetable_id'], sid,
                         cc, info['subject_name'], emp, info['teacher_user_id'], second,
                         teacher_id[second], info['period_no'], ex['room'] or None))
                    n_second += 1
        progress(f'Lab/room settings: {n_lab}; Second Faculty assignments: {n_second}')

    conn.commit()
    return {
        'rows': len(raw), 'branches': len(branch_id), 'timetables': len(timetable_id),
        'sessions': len(session_id), 'teachers': len(teacher_id),
        'students': len(student_id), 'enrollments': len(student_tt),
        'lab_settings': n_lab, 'second_faculty': n_second,
    }


def bootstrap_if_empty(app):
    """Create the admin account (if missing) and import the workbook on a fresh DB.

    Admin is only created when ADMIN_EMAIL and ADMIN_PASSWORD are set and no
    admin row exists yet. Existing admin passwords are never changed here —
    use ``scripts/sync_admin_credentials.py`` to reset them deliberately.
    """
    conn = db.get_db()
    # Fast path for every cold start after the first: one round trip tells us
    # an admin exists and data is already imported, so there is nothing to do.
    try:
        probe = conn.execute(
            "SELECT (SELECT 1 FROM users WHERE role='admin' LIMIT 1), "
            "(SELECT 1 FROM assignments LIMIT 1)").fetchone()
        if probe is not None and probe[0] and probe[1]:
            return
    except Exception:
        conn.rollback()
    admin_id = ensure_admin(conn, app.config.get('ADMIN_EMAIL'),
                            app.config.get('ADMIN_PASSWORD'))
    conn.commit()
    if admin_id is None:
        # No credentials and no admin: surface a clear operational message.
        # Do not crash the process — login will simply fail until credentials
        # are configured and the sync script (or a restart with env set) runs.
        app.logger.error(
            'No admin account exists and ADMIN_EMAIL/ADMIN_PASSWORD are not set. '
            'Set both environment variables and run: python scripts/sync_admin_credentials.py')
    count_row = conn.execute('SELECT COUNT(*) FROM assignments').fetchone()
    count = 0
    if count_row is not None:
        try:
            count = int(count_row[0])
        except Exception:
            count = int(list(count_row)[0])
    if count == 0 and os.path.exists(app.config['XLSX_PATH']):
        try:
            import_timetable(app)
        except Exception as exc:  # pragma: no cover - surface but don't crash boot
            app.logger.exception('Timetable import failed: %s', exc)
