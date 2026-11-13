"""Second Faculty assignment service (Phase 4).

Domain model (re-using the existing schema, nothing is duplicated):

* A Main Faculty "assignment" is a teaching **session** (``class_sessions``):
  one subject taught by one teacher to one section (timetable).  It already
  carries academic year, semester, branch, section and subject code through its
  timetable, so ``session_id`` *is* the Phase 3 teacher-assignment reference.
* Eligible students of that assignment are the rows of ``session_students``
  (built from the imported timetable / enrolment data) for **active** students.
* A Second Faculty assignment is a ``second_faculty_assignments`` row keyed by
  ``UNIQUE(session_id, second_emp_code)``.  Many second faculty may share one
  session, and one employee may be second faculty on many sessions of many
  different main teachers.
* Removal is a *soft* removal: ``status='rejected'`` + ``removed_at``.  Rows are
  never deleted, so forms / feedback that point at them remain valid.

Every function here takes the authenticated main-faculty user and re-validates
ownership; nothing trusts ids supplied by the browser.
"""
import logging

from . import db, photos
from .services import SESSION_SELECT

logger = logging.getLogger(__name__)

MIN_QUERY_LEN = 2
MAX_QUERY_LEN = 60
SEARCH_PAGE_SIZE = 10
STUDENT_PAGE_SIZE = 25
MAX_STUDENT_PAGE_SIZE = 100
EMPLOYEE_ROLES = ('teacher', 'hod', 'dean')


class ServiceError(Exception):
    """A request that must be refused; carries an HTTP status and a stable code."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- #
# Ownership
# --------------------------------------------------------------------------- #
def _int_id(value, what='id'):
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ServiceError(400, 'invalid_id', f'Invalid {what}.')
    if n <= 0:
        raise ServiceError(400, 'invalid_id', f'Invalid {what}.')
    return n


def owned_session(user, session_id):
    """The teaching session, only if ``user`` teaches it (and it is live).

    404 is returned for both "does not exist" and "belongs to someone else" so a
    teacher cannot probe other teachers' session ids.
    """
    sid = _int_id(session_id, 'session')
    row = db.query(
        SESSION_SELECT + """ WHERE cs.id=? AND cs.teacher_user_id=?
            AND cs.is_deleted=0 AND t.is_deleted=0""",
        (sid, user['id']), one=True)
    if not row:
        raise ServiceError(404, 'not_found', 'Subject assignment not found.')
    return dict(row)


def _active_sfa_sql():
    return ("sfa.status='confirmed' AND sfa.second_teacher_user_id IS NOT NULL "
            "AND COALESCE(sfa.second_emp_code,'')<>''")


# --------------------------------------------------------------------------- #
# Employees
# --------------------------------------------------------------------------- #
def _like_escape(text):
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _departments_for(user_rows):
    """{user_id: [branch codes]} from the branches a teacher teaches in, plus the
    scopes of HOD/Dean accounts.  One query per source, never per employee."""
    ids = [r['id'] for r in user_rows]
    out = {i: [] for i in ids}
    if not ids:
        return out
    ph = ','.join('?' * len(ids))
    for r in db.query(
            f"""SELECT DISTINCT cs.teacher_user_id AS uid, b.code AS code
                FROM class_sessions cs JOIN timetables t ON t.id=cs.timetable_id
                JOIN branches b ON b.id=t.branch_id
                WHERE cs.teacher_user_id IN ({ph}) AND cs.is_deleted=0""", tuple(ids)):
        out[r['uid']].append(r['code'])
    for r in db.query(
            f"""SELECT DISTINCT s.user_id AS uid, b.code AS code
                FROM user_department_scopes s JOIN branches b ON b.id=s.branch_id
                WHERE s.user_id IN ({ph})""", tuple(ids)):
        if r['code'] not in out[r['uid']]:
            out[r['uid']].append(r['code'])
    return {k: sorted(v) for k, v in out.items()}


def _employee_view(row, departments=None):
    """What the browser may see about an employee: no e-mail, phone or login id."""
    role = row['role']
    return {
        'emp_code': row['emp_code'],
        'name': row['name'] or row['emp_code'],
        'designation': row['designation'] or ('Head of Department' if role == 'hod'
                                              else 'Dean' if role == 'dean' else 'Faculty'),
        'role': role,
        'departments': departments or [],
        'initials': photos.initials(row['name']),
        'photo_url': '/employee-photo/' + str(row['emp_code']),
    }


_DEDUPE = """u.id = (SELECT u2.id FROM users u2
        WHERE UPPER(u2.emp_code)=UPPER(u.emp_code) AND u2.is_active=1
          AND u2.role IN ('teacher','hod','dean')
        ORDER BY CASE u2.role WHEN 'teacher' THEN 0 ELSE 1 END, u2.id LIMIT 1)"""


def resolve_employee(emp_code):
    """The active employee row for ``emp_code`` (teacher account preferred), or None."""
    code = photos.normalize_emp(emp_code)
    if not code:
        return None
    row = db.query(
        """SELECT u.id, u.role, u.name, u.emp_code, u.designation FROM users u
           WHERE UPPER(u.emp_code)=? AND u.is_active=1
             AND u.role IN ('teacher','hod','dean')
           ORDER BY CASE u.role WHEN 'teacher' THEN 0 ELSE 1 END, u.id LIMIT 1""",
        (code,), one=True)
    return dict(row) if row else None


def search_employees(user, session, q, page=1, page_size=SEARCH_PAGE_SIZE):
    """Search active employees by Employee ID or name (partial, case-insensitive).

    The logged-in main faculty is never offered as their own second faculty.
    Employees that already hold an active assignment on this session are
    returned flagged ``already_assigned`` so the UI can disable them.
    """
    q = ' '.join((q or '').split())[:MAX_QUERY_LEN]
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or SEARCH_PAGE_SIZE), 25))
    if len(q) < MIN_QUERY_LEN:
        return {'query': q, 'results': [], 'page': 1, 'has_more': False,
                'hint': f'Type at least {MIN_QUERY_LEN} characters of an Employee ID or name.'}

    upper = q.upper()
    esc = _like_escape(upper)
    contains, prefix = f'%{esc}%', f'{esc}%'
    name_clauses, name_args = [], []
    for tok in upper.split(' '):
        name_clauses.append("UPPER(u.name) LIKE ? ESCAPE '\\'")
        name_args.append(f'%{_like_escape(tok)}%')
    main_code = (user.get('emp_code') or '').strip().upper()

    sql = f"""SELECT u.id, u.role, u.name, u.emp_code, u.designation FROM users u
        WHERE u.role IN ('teacher','hod','dean') AND u.is_active=1
          AND COALESCE(TRIM(u.emp_code),'')<>''
          AND u.id<>? AND UPPER(u.emp_code)<>?
          AND {_DEDUPE}
          AND (UPPER(u.emp_code) LIKE ? ESCAPE '\\' OR ({' AND '.join(name_clauses)}))
        ORDER BY CASE WHEN UPPER(u.emp_code)=? THEN 0
                      WHEN UPPER(u.emp_code) LIKE ? ESCAPE '\\' THEN 1
                      WHEN UPPER(u.name) LIKE ? ESCAPE '\\' THEN 2 ELSE 3 END,
                 u.name, u.id
        LIMIT ? OFFSET ?"""
    args = ([user['id'], main_code, contains] + name_args
            + [upper, prefix, prefix, page_size + 1, (page - 1) * page_size])
    rows = [dict(r) for r in db.query(sql, tuple(args))]
    has_more = len(rows) > page_size
    rows = rows[:page_size]

    assigned = set()
    if rows:
        for r in db.query(
                f"""SELECT UPPER(sfa.second_emp_code) c FROM second_faculty_assignments sfa
                    WHERE sfa.session_id=? AND {_active_sfa_sql()}""", (session['id'],)):
            assigned.add(r['c'])
    deps = _departments_for(rows)
    results = []
    for r in rows:
        view = _employee_view(r, deps.get(r['id']))
        view['already_assigned'] = (r['emp_code'] or '').upper() in assigned
        results.append(view)
    return {'query': q, 'results': results, 'page': page, 'has_more': has_more, 'hint': ''}


def validate_employee(user, session, emp_code):
    """Backend eligibility check for the chosen second faculty."""
    code = photos.normalize_emp(emp_code)
    if not code:
        raise ServiceError(400, 'invalid_employee', 'Invalid Employee ID.')
    emp = resolve_employee(code)
    if not emp:
        raise ServiceError(404, 'employee_not_found',
                           'That employee does not exist or is not active.')
    if emp['id'] == user['id'] or code == (user.get('emp_code') or '').strip().upper() \
            or emp['id'] == session.get('teacher_user_id'):
        raise ServiceError(400, 'self_assignment',
                           'You cannot be your own Second Faculty for this subject.')
    if (session.get('teacher_emp') or '').strip().upper() == code:
        raise ServiceError(400, 'self_assignment',
                           'The Main Faculty cannot also be the Second Faculty.')
    return emp


# --------------------------------------------------------------------------- #
# Eligible students
# --------------------------------------------------------------------------- #
def eligible_student_count(session_id):
    return db.query(
        """SELECT COUNT(*) c FROM session_students ss
           JOIN users u ON u.id=ss.student_user_id
           WHERE ss.session_id=? AND u.is_active=1 AND u.role='student'""",
        (session_id,), one=True)['c']


def eligible_student_counts(session_ids):
    ids = [int(i) for i in session_ids]
    out = {i: 0 for i in ids}
    if not ids:
        return out
    ph = ','.join('?' * len(ids))
    for r in db.query(
            f"""SELECT ss.session_id sid, COUNT(*) c FROM session_students ss
                JOIN users u ON u.id=ss.student_user_id
                WHERE ss.session_id IN ({ph}) AND u.is_active=1 AND u.role='student'
                GROUP BY ss.session_id""", tuple(ids)):
        out[r['sid']] = r['c']
    return out


def eligible_students_page(session, page=1, page_size=STUDENT_PAGE_SIZE):
    """Roll / name / section of the students of exactly this session, paged."""
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or STUDENT_PAGE_SIZE), MAX_STUDENT_PAGE_SIZE))
    total = eligible_student_count(session['id'])
    rows = db.query(
        """SELECT u.roll, u.name FROM session_students ss
           JOIN users u ON u.id=ss.student_user_id
           WHERE ss.session_id=? AND u.is_active=1 AND u.role='student'
           ORDER BY u.roll, u.id LIMIT ? OFFSET ?""",
        (session['id'], page_size, (page - 1) * page_size))
    return {
        'total': total, 'page': page, 'page_size': page_size,
        'pages': max(1, -(-total // page_size)),
        'rows': [{'roll': r['roll'], 'name': r['name'], 'section': session['timetable']}
                 for r in rows],
    }


# --------------------------------------------------------------------------- #
# Assignment records
# --------------------------------------------------------------------------- #
def _assignment_rows(session_ids, active_only=True):
    ids = [int(i) for i in session_ids]
    if not ids:
        return []
    ph = ','.join('?' * len(ids))
    where = f"sfa.session_id IN ({ph})"
    if active_only:
        where += f" AND {_active_sfa_sql()}"
    return [dict(r) for r in db.query(
        f"""SELECT sfa.id, sfa.session_id, sfa.second_emp_code, sfa.second_teacher_user_id,
                   sfa.status, sfa.source, sfa.created_at, sfa.updated_at, sfa.removed_at,
                   u.name AS second_name, u.designation AS second_designation,
                   u.role AS second_role
            FROM second_faculty_assignments sfa
            LEFT JOIN users u ON u.id=sfa.second_teacher_user_id
            WHERE {where} ORDER BY sfa.created_at, sfa.id""", tuple(ids))]


def _assignment_view(r):
    code = r['second_emp_code']
    return {
        'assignment_id': r['id'],
        'session_id': r['session_id'],
        'emp_code': code,
        'name': r['second_name'] or code,
        'designation': r['second_designation'] or 'Faculty',
        'initials': photos.initials(r['second_name']),
        'photo_url': '/employee-photo/' + str(code),
        'source': r['source'] or 'import',
        # Only assignments created by the teacher themself are editable by them;
        # timetable-import mappings stay under admin/HOD control.
        'editable': (r['source'] or 'import') == 'teacher',
        'created_at': r['created_at'],
    }


def session_assignments(session_id):
    return [_assignment_view(r) for r in _assignment_rows([session_id])]


def list_cards(user):
    """One card per teaching session of the authenticated main faculty."""
    sessions = [dict(r) for r in db.query(
        SESSION_SELECT + """ WHERE cs.teacher_user_id=? AND cs.is_deleted=0
            AND t.is_deleted=0 ORDER BY cs.subject_name, b.code, t.code, cs.id""",
        (user['id'],))]
    ids = [s['id'] for s in sessions]
    counts = eligible_student_counts(ids)
    settings = {}
    if ids:
        ph = ','.join('?' * len(ids))
        for r in db.query(
                f"""SELECT session_id, room, lab_available FROM teacher_subject_settings
                    WHERE session_id IN ({ph})""", tuple(ids)):
            settings[r['session_id']] = r
    by_session = {}
    for r in _assignment_rows(ids):
        by_session.setdefault(r['session_id'], []).append(_assignment_view(r))
    cards = []
    for s in sessions:
        st = settings.get(s['id'])
        seconds = by_session.get(s['id'], [])
        cards.append({
            'session_id': s['id'],
            'subject_code': s['subject_code'],
            'subject_name': s['subject_name'],
            'main_name': user.get('name'),
            'main_emp_code': user.get('emp_code'),
            'academic_year': s['academic_year'],
            'semester': s['semester'],
            'branch': s['branch'],
            'section': s['timetable'],
            'room': (st['room'] if st and st['room'] else None),
            # None = Phase 3 has not recorded it yet (never guessed).
            'lab_available': (bool(st['lab_available']) if st else None),
            'eligible_students': counts.get(s['id'], 0),
            'second_faculty': seconds,
            'second_count': len(seconds),
        })
    return cards


def card_for_session(user, session_id):
    for c in list_cards(user):
        if c['session_id'] == int(session_id):
            return c
    return None


def build_preview(user, session_id, emp_code, page=1, page_size=STUDENT_PAGE_SIZE,
                  replace_assignment_id=None):
    """Everything the confirmation steps need, recomputed from live data."""
    session = owned_session(user, session_id)
    emp = validate_employee(user, session, emp_code)
    students = eligible_students_page(session, page, page_size)

    problems = []
    existing = db.query(
        f"""SELECT sfa.id FROM second_faculty_assignments sfa
            WHERE sfa.session_id=? AND UPPER(sfa.second_emp_code)=? AND {_active_sfa_sql()}""",
        (session['id'], emp['emp_code'].upper()), one=True)
    if existing:
        problems.append({'code': 'duplicate',
                         'message': 'This employee is already Second Faculty for this subject and section.'})
    blocked = _blocked_by_admin(session['id'], emp['emp_code'])
    if blocked:
        problems.append({'code': 'blocked_by_admin',
                         'message': 'The administrator rejected this employee for this subject. '
                                    'Please contact the administrator.'})
    warnings = []
    if students['total'] == 0:
        warnings.append({'code': 'no_students',
                         'message': 'No eligible students were found for this subject and section. '
                                    'Check the timetable / enrolment data before assigning.'})
    replace_view = None
    if replace_assignment_id is not None:
        row = _owned_assignment(user, replace_assignment_id, session_id=session['id'])
        replace_view = _assignment_view(row)
        if row['second_emp_code'].upper() == emp['emp_code'].upper():
            problems.append({'code': 'same_employee',
                             'message': 'The selected employee is already the current Second Faculty.'})

    setting = db.query('SELECT room, lab_available FROM teacher_subject_settings WHERE session_id=?',
                       (session['id'],), one=True)
    return {
        'session_id': session['id'],
        'main': {'name': user.get('name'), 'emp_code': user.get('emp_code'),
                 'photo_url': '/employee-photo/' + str(user.get('emp_code') or '')},
        'second': _employee_view(emp, _departments_for([emp]).get(emp['id'])),
        'subject': {'code': session['subject_code'], 'name': session['subject_name']},
        'assignment': {
            'academic_year': session['academic_year'], 'semester': session['semester'],
            'branch': session['branch'], 'section': session['timetable'],
            'room': (setting['room'] if setting and setting['room'] else None),
        },
        'replacing': replace_view,
        'students': students,
        'problems': problems,
        'warnings': warnings,
        # Zero eligible students is a hard stop: never silently create it.
        'can_confirm': not problems and students['total'] > 0,
    }


def _blocked_by_admin(session_id, emp_code):
    row = db.query(
        """SELECT 1 FROM second_faculty_assignments
           WHERE session_id=? AND UPPER(second_emp_code)=? AND status='rejected'
             AND removed_at IS NULL""",
        (session_id, emp_code.upper()), one=True)
    return bool(row)


def _owned_assignment(user, assignment_id, session_id=None):
    aid = _int_id(assignment_id, 'assignment')
    row = db.query(
        f"""SELECT sfa.id, sfa.session_id, sfa.second_emp_code, sfa.second_teacher_user_id,
                   sfa.status, sfa.source, sfa.created_at, sfa.updated_at, sfa.removed_at,
                   u.name AS second_name, u.designation AS second_designation,
                   u.role AS second_role
            FROM second_faculty_assignments sfa
            JOIN class_sessions cs ON cs.id=sfa.session_id
            LEFT JOIN users u ON u.id=sfa.second_teacher_user_id
            WHERE sfa.id=? AND cs.teacher_user_id=? AND cs.is_deleted=0
              AND {_active_sfa_sql()}""",
        (aid, user['id']), one=True)
    if not row or (session_id is not None and row['session_id'] != session_id):
        raise ServiceError(404, 'not_found', 'Assignment not found.')
    row = dict(row)
    if (row['source'] or 'import') != 'teacher':
        raise ServiceError(403, 'read_only',
                           'This assignment came from the timetable import and can only be '
                           'changed by the administrator or HOD.')
    return row


# --------------------------------------------------------------------------- #
# Writes (single transaction each)
# --------------------------------------------------------------------------- #
def _insert_or_revive(conn, user, session, emp, room):
    """Create the assignment, or re-activate a previously soft-removed row.

    Returns (assignment_id, created: bool).  Raises ServiceError(409) on a
    duplicate, whether caught by the pre-check or by the UNIQUE constraint.
    """
    code = emp['emp_code'].strip().upper()
    existing = conn.execute(
        """SELECT id, status, removed_at FROM second_faculty_assignments
           WHERE session_id=? AND UPPER(second_emp_code)=?""",
        (session['id'], code)).fetchone()
    if existing:
        if existing['status'] == 'confirmed':
            raise ServiceError(409, 'duplicate',
                               'This employee is already Second Faculty for this subject and section.')
        if existing['status'] == 'rejected' and existing['removed_at'] is None:
            raise ServiceError(409, 'blocked_by_admin',
                               'The administrator rejected this employee for this subject.')
        conn.execute(
            """UPDATE second_faculty_assignments
               SET status='confirmed', source='teacher', removed_at=NULL, removed_by=NULL,
                   second_teacher_user_id=?, second_emp_code=?,
                   main_teacher_user_id=?, main_emp_code=?, room=?,
                   updated_by=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (emp['id'], code, user['id'], user.get('emp_code'), room, user['id'],
             existing['id']))
        return existing['id'], False
    new_id = db.insert_id(
        """INSERT INTO second_faculty_assignments(
               academic_year_id, semester_id, branch_id, timetable_id, session_id,
               subject_code, subject_name, main_emp_code, main_teacher_user_id,
               second_emp_code, second_teacher_user_id, period_no, room, status,
               created_by, updated_by, source)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'confirmed',?,?,'teacher')""",
        (session['year_id'], session['semester_id'], session['branch_id'],
         session['timetable_id'], session['id'], session['subject_code'],
         session['subject_name'], user.get('emp_code'), user['id'], code, emp['id'],
         session.get('period_no'), room, user['id'], user['id']))
    return new_id, True


def _room_for(session_id):
    row = db.query('SELECT room FROM teacher_subject_settings WHERE session_id=?',
                   (session_id,), one=True)
    return row['room'] if row and row['room'] else None


def _run_write(fn):
    """Run ``fn(conn)`` as one transaction; roll back on any failure."""
    conn = db.get_db()
    try:
        result = fn(conn)
        conn.commit()
        return result
    except ServiceError:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        # Two simultaneous confirmations race into the UNIQUE constraint.
        text = (str(exc) or '').lower()
        if 'unique' in text or 'duplicate key' in text or 'constraint' in text:
            raise ServiceError(409, 'duplicate',
                               'This employee is already Second Faculty for this subject and section.')
        logger.exception('Second-faculty write failed')
        raise ServiceError(500, 'server_error', 'Something went wrong. Nothing was saved.')


def _require_students(session):
    if eligible_student_count(session['id']) == 0:
        raise ServiceError(422, 'no_students',
                           'No eligible students were found for this subject and section, '
                           'so the assignment was not created.')


def create_assignment(user, session_id, emp_code):
    session = owned_session(user, session_id)
    emp = validate_employee(user, session, emp_code)
    _require_students(session)
    room = _room_for(session['id'])
    aid, _created = _run_write(lambda conn: _insert_or_revive(conn, user, session, emp, room))
    return {'assignment_id': aid, 'session': session, 'employee': emp}


def replace_assignment(user, assignment_id, emp_code):
    """Swap one Second Faculty for another atomically (old row is soft-removed)."""
    old = _owned_assignment(user, assignment_id)
    session = owned_session(user, old['session_id'])
    emp = validate_employee(user, session, emp_code)
    if emp['emp_code'].upper() == (old['second_emp_code'] or '').upper():
        raise ServiceError(409, 'same_employee',
                           'The selected employee is already the current Second Faculty.')
    _require_students(session)
    room = _room_for(session['id'])

    def work(conn):
        new_id, _ = _insert_or_revive(conn, user, session, emp, room)
        conn.execute(
            """UPDATE second_faculty_assignments
               SET status='rejected', removed_at=CURRENT_TIMESTAMP, removed_by=?,
                   updated_by=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status='confirmed'""", (user['id'], user['id'], old['id']))
        return new_id

    new_id = _run_write(work)
    return {'assignment_id': new_id, 'replaced_id': old['id'], 'session': session,
            'employee': emp}


def remove_assignment(user, assignment_id):
    """Soft-remove: the row, its feedback links and its history are all kept."""
    old = _owned_assignment(user, assignment_id)

    def work(conn):
        cur = conn.execute(
            """UPDATE second_faculty_assignments
               SET status='rejected', removed_at=CURRENT_TIMESTAMP, removed_by=?,
                   updated_by=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status='confirmed'""", (user['id'], user['id'], old['id']))
        if cur.rowcount == 0:
            raise ServiceError(404, 'not_found', 'Assignment not found.')
        return old['id']

    _run_write(work)
    return {'assignment_id': old['id'], 'session_id': old['session_id'],
            'emp_code': old['second_emp_code']}
