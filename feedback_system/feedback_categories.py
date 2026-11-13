"""Phase 5A - the three independent feedback categories and their eligibility.

Categories
----------
Stored value (``feedback_forms.category``)  Canonical name   Assignment it points at
``main``                                    main_faculty     ``class_sessions.id`` (Phase 3 "teacher assignment")
``second``                                  second_faculty   ``second_faculty_assignments.id`` (Phase 4)
``lab``                                     lab_faculty      ``lab_faculty_assignments.id``

The stored values already existed (``main``/``second``) and are read by the
Dean/HOD code, so they are kept; canonical names are exposed for the API.

Single source of truth
----------------------
Every category resolves a *scope* from the DATABASE only
(:func:`resolve_scope`) and every eligibility question is answered from that
scope.  Nothing here trusts a browser-supplied faculty, student or assignment
id beyond using it as a lookup key.

Eligibility is the set of ACTIVE students linked (``session_students``) to the
assignment's teaching session, i.e. the section + subject + teacher that
Phase 3 / Phase 4 configured.  Second and Lab faculty additionally require the
session to be *Lab Available* (Phase 3 ``lab_status='available'``).
"""
from . import db

MAIN, SECOND, LAB = 'main', 'second', 'lab'
CATEGORIES = (MAIN, SECOND, LAB)
CANONICAL = {MAIN: 'main_faculty', SECOND: 'second_faculty', LAB: 'lab_faculty'}
FROM_CANONICAL = {v: k for k, v in CANONICAL.items()}
LEGACY_CATEGORIES = ('subject_only', 'any')          # pre-Phase-5 values, read-only
FORM_STATUSES = ('draft', 'open', 'closed', 'archived')


class ScopeError(Exception):
    """A request that cannot be honoured.  ``code`` is machine readable and
    ``status`` the HTTP status a route should use."""

    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def normalize_category(value):
    """Accept stored ('main') or canonical ('main_faculty') names; else raise."""
    v = (value or '').strip().lower()
    v = FROM_CANONICAL.get(v, v)
    if v not in CATEGORIES:
        raise ScopeError('invalid_category',
                         'Feedback category must be main_faculty, second_faculty or lab_faculty.',
                         400)
    return v


def canonical_name(category):
    return CANONICAL.get(category, 'legacy')


def _to_int(value, what):
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ScopeError('invalid_id', f'Invalid {what}.', 400)
    if n <= 0:
        raise ScopeError('invalid_id', f'Invalid {what}.', 400)
    return n


# --------------------------------------------------------------------------- #
# Scope resolution (database only)
# --------------------------------------------------------------------------- #
_SESSION_SQL = """SELECT cs.id AS session_id, cs.subject_code, cs.subject_name,
        cs.period_no, cs.teacher_user_id, cs.is_deleted AS session_deleted,
        cs.timetable_id, t.code AS timetable, t.is_deleted AS timetable_deleted,
        t.branch_id, t.semester_id, t.academic_year_id,
        u.emp_code AS main_emp_code, u.name AS main_name, u.is_active AS main_active,
        ts.lab_status
    FROM class_sessions cs
    JOIN timetables t ON t.id = cs.timetable_id
    LEFT JOIN users u ON u.id = cs.teacher_user_id
    LEFT JOIN teacher_subject_settings ts ON ts.session_id = cs.id"""


def _session_scope(session_id):
    row = db.query(_SESSION_SQL + ' WHERE cs.id=?', (session_id,), one=True)
    if not row:
        raise ScopeError('session_not_found', 'That teaching assignment does not exist.', 404)
    row = dict(row)
    if row['session_deleted'] or row['timetable_deleted']:
        raise ScopeError('session_inactive',
                         'That teaching assignment has been removed from the timetable.')
    return row


def _require_lab_available(sc, who):
    if sc.get('lab_status') != 'available':
        raise ScopeError(
            'no_lab',
            f'{who} feedback needs a subject marked "Lab Available" in Assignment Setup; '
            'this subject has No Lab or has not been configured.', 409)


def resolve_scope(category, assignment_id):
    """Resolve and validate the scope of one category from the database.

    ``assignment_id`` is a ``class_sessions.id`` for main, a
    ``second_faculty_assignments.id`` for second and a
    ``lab_faculty_assignments.id`` for lab.  Returns a plain dict; raises
    :class:`ScopeError` for anything that is missing, removed or inconsistent.
    """
    category = normalize_category(category)
    aid = _to_int(assignment_id, 'assignment')

    if category == MAIN:
        sc = _session_scope(aid)
        if not sc['teacher_user_id'] or not sc['main_active']:
            raise ScopeError('no_main_faculty',
                             'No active Main Faculty is assigned to this subject and section.')
        sc.update(category=MAIN, assignment_id=aid, faculty_user_id=sc['teacher_user_id'],
                  faculty_emp_code=sc['main_emp_code'], faculty_name=sc['main_name'],
                  main_assignment_id=aid, second_faculty_assignment_id=None,
                  lab_faculty_assignment_id=None)
        return sc

    if category == SECOND:
        a = db.query("""SELECT sfa.*, u.name AS second_name, u.is_active AS second_active
                        FROM second_faculty_assignments sfa
                        LEFT JOIN users u ON u.id = sfa.second_teacher_user_id
                        WHERE sfa.id=?""", (aid,), one=True)
        if not a:
            raise ScopeError('assignment_not_found',
                             'That Second Faculty assignment does not exist.', 404)
        if a['status'] != 'confirmed':
            raise ScopeError('assignment_not_confirmed',
                             'That Second Faculty assignment is not confirmed '
                             '(unresolved, rejected or removed).')
        if not a['second_teacher_user_id'] or not (a['second_emp_code'] or '').strip():
            raise ScopeError('assignment_incomplete',
                             'That Second Faculty assignment has no resolved faculty member.')
        if not a['second_active']:
            raise ScopeError('faculty_inactive', 'The Second Faculty member is not active.')
        if not a['session_id']:
            raise ScopeError('assignment_incomplete',
                             'That Second Faculty assignment is not linked to a teaching session.')
        sc = _session_scope(a['session_id'])
        if a['main_teacher_user_id'] and a['main_teacher_user_id'] != sc['teacher_user_id']:
            raise ScopeError('main_mismatch',
                             'The Second Faculty assignment no longer matches the session\'s '
                             'Main Faculty.', 409)
        if a['second_teacher_user_id'] == sc['teacher_user_id']:
            raise ScopeError('self_assignment',
                             'Second Faculty cannot be the same person as Main Faculty here.')
        _require_lab_available(sc, 'Second Faculty')
        sc.update(category=SECOND, assignment_id=aid, faculty_user_id=a['second_teacher_user_id'],
                  faculty_emp_code=a['second_emp_code'], faculty_name=a['second_name'],
                  main_assignment_id=sc['session_id'], second_faculty_assignment_id=aid,
                  lab_faculty_assignment_id=None)
        return sc

    # lab
    a = db.query("""SELECT lfa.*, u.name AS lab_name, u.is_active AS lab_active
                    FROM lab_faculty_assignments lfa
                    LEFT JOIN users u ON u.id = lfa.lab_teacher_user_id
                    WHERE lfa.id=?""", (aid,), one=True)
    if not a:
        raise ScopeError('assignment_not_found',
                         'That Lab Faculty assignment does not exist. Lab Faculty must be '
                         'assigned before Lab feedback can be created.', 404)
    if a['status'] != 'confirmed':
        raise ScopeError('assignment_not_confirmed', 'That Lab Faculty assignment is cancelled.')
    if not a['lab_active']:
        raise ScopeError('faculty_inactive', 'The Lab Faculty member is not active.')
    sc = _session_scope(a['session_id'])
    _require_lab_available(sc, 'Lab Faculty')
    sc.update(category=LAB, assignment_id=aid, faculty_user_id=a['lab_teacher_user_id'],
              faculty_emp_code=a['lab_emp_code'], faculty_name=a['lab_name'],
              main_assignment_id=sc['session_id'], second_faculty_assignment_id=None,
              lab_faculty_assignment_id=aid)
    return sc


def scope_for_form(form):
    """Re-resolve the scope of an existing (category) form from its stored ids."""
    category = normalize_category(form['category'])
    if category == MAIN:
        aid = form['session_id']
    elif category == SECOND:
        aid = form['second_faculty_assignment_id']
    else:
        aid = form['lab_faculty_assignment_id']
    if not aid:
        raise ScopeError('assignment_missing',
                         'This form is not linked to a faculty assignment.')
    sc = resolve_scope(category, aid)
    if form['session_id'] and form['session_id'] != sc['session_id']:
        raise ScopeError('scope_mismatch',
                         'The form\'s teaching session does not match its assignment.', 409)
    return sc


# --------------------------------------------------------------------------- #
# Eligibility (one engine, three categories)
# --------------------------------------------------------------------------- #
def _eligibility_where(sc):
    """FROM/WHERE fragment + args selecting the eligible ACTIVE students."""
    sql = """FROM session_students ss
             JOIN users u ON u.id = ss.student_user_id
             WHERE ss.session_id = ? AND u.role = 'student' AND u.is_active = 1"""
    args = [sc['session_id']]
    if sc['category'] == LAB and _has_lab_batch(sc['assignment_id']):
        sql += (' AND ss.student_user_id IN (SELECT student_user_id FROM lab_batch_students '
                'WHERE lab_faculty_assignment_id = ?)')
        args.append(sc['assignment_id'])
    return sql, args


def _has_lab_batch(lab_assignment_id):
    return db.query('SELECT 1 FROM lab_batch_students WHERE lab_faculty_assignment_id=? LIMIT 1',
                    (lab_assignment_id,), one=True) is not None


def eligible_students(sc, limit=None, offset=0):
    """Eligible students for a resolved scope: ``[{'id', 'roll'}]`` ordered by roll."""
    frm, args = _eligibility_where(sc)
    sql = 'SELECT u.id AS id, u.roll AS roll ' + frm + ' ORDER BY u.roll, u.id'
    if limit is not None:
        sql += ' LIMIT ? OFFSET ?'
        args = args + [int(limit), int(offset)]
    return [dict(r) for r in db.query(sql, tuple(args))]


def eligible_count(sc):
    frm, args = _eligibility_where(sc)
    return db.query('SELECT COUNT(*) AS c ' + frm, tuple(args), one=True)['c']


def student_is_eligible(student_user_id, sc):
    """Is this student eligible for the resolved scope RIGHT NOW (live check)?"""
    frm, args = _eligibility_where(sc)
    row = db.query('SELECT 1 ' + frm + ' AND ss.student_user_id = ? LIMIT 1',
                   tuple(args) + (student_user_id,), one=True)
    return row is not None


def eligible_main(session_id):
    sc = resolve_scope(MAIN, session_id)
    return eligible_students(sc)


def eligible_second(second_assignment_id):
    sc = resolve_scope(SECOND, second_assignment_id)
    return eligible_students(sc)


def eligible_lab(lab_assignment_id):
    sc = resolve_scope(LAB, lab_assignment_id)
    return eligible_students(sc)


# --------------------------------------------------------------------------- #
# Frozen eligibility (published forms) - snapshot design
# --------------------------------------------------------------------------- #
def snapshot_eligibility(conn, form_id, sc):
    """Freeze the eligible students of a form.  Runs inside the CALLER's
    transaction (no commit here).  Returns the number of students frozen."""
    frm, args = _eligibility_where(sc)
    conn.execute('DELETE FROM form_eligible_students WHERE form_id=?', (form_id,))
    conn.execute('INSERT INTO form_eligible_students(form_id, student_user_id) '
                 'SELECT ?, ss.student_user_id ' + frm, (form_id, *args))
    return conn.execute('SELECT COUNT(*) FROM form_eligible_students WHERE form_id=?',
                        (form_id,)).fetchone()[0]


def has_snapshot(form_id):
    return db.query('SELECT 1 FROM form_eligible_students WHERE form_id=? LIMIT 1',
                    (form_id,), one=True) is not None


def student_in_snapshot(student_user_id, form_id):
    return db.query("""SELECT 1 FROM form_eligible_students fe
                       JOIN users u ON u.id = fe.student_user_id
                       WHERE fe.form_id=? AND fe.student_user_id=?
                         AND u.role='student' AND u.is_active=1""",
                    (form_id, student_user_id), one=True) is not None


def student_eligible_for_form(student_user_id, form):
    """Eligibility used for category forms: the FROZEN snapshot decides who is
    in; the live scope must also still be valid (assignment not removed, No Lab
    not set later).  Returns ``(ok, reason_code)``."""
    if not student_in_snapshot(student_user_id, form['id']):
        return False, 'ineligible'
    try:
        scope_for_form(form)
    except ScopeError as exc:
        return False, exc.code
    return True, None


# --------------------------------------------------------------------------- #
# Duplicate / conflict detection
# --------------------------------------------------------------------------- #
def conflicting_forms(category, assignment_id, exclude_form_id=None):
    """Live (draft/open, not deleted) forms already covering this exact
    category + assignment.  Closed/archived forms are history (survey rounds)."""
    col = {MAIN: 'session_id', SECOND: 'second_faculty_assignment_id',
           LAB: 'lab_faculty_assignment_id'}[category]
    sql = (f"SELECT id, title, status FROM feedback_forms WHERE category=? AND {col}=? "
           "AND is_deleted=0 AND status IN ('draft','open')")
    args = [category, assignment_id]
    if category == MAIN:
        # A main form is one that is not a second/lab form on the same session.
        sql += ' AND second_faculty_assignment_id IS NULL AND lab_faculty_assignment_id IS NULL'
    if exclude_form_id:
        sql += ' AND id<>?'
        args.append(exclude_form_id)
    return [dict(r) for r in db.query(sql, tuple(args))]


def duplicate_eligibility_records(form_id):
    """Students listed more than once for a form's frozen eligibility.  The
    primary key makes this impossible; kept as an explicit integrity probe."""
    return [dict(r) for r in db.query(
        """SELECT student_user_id, COUNT(*) AS n FROM form_eligible_students
           WHERE form_id=? GROUP BY student_user_id HAVING COUNT(*) > 1""", (form_id,))]
