"""Teacher leave / removal, class replacement and student TC / removal.

Nothing is ever deleted.  ``users.is_active`` blocks sign-in and eligibility and
``users.status`` records why.  A replaced class is *not* edited: results are
linked to a teacher through ``class_sessions.teacher_user_id``, so the old
session is closed (``is_deleted=1``) and the replacement gets its own session.
"""
from datetime import date, datetime

from . import db

TEACHER_STATUSES = {'active': 'Active', 'on_leave': 'On leave', 'left': 'Left'}
STUDENT_STATUSES = {'active': 'Active', 'tc': 'TC issued', 'removed': 'Removed'}


class PeopleError(ValueError):
    pass


def _now():
    return datetime.utcnow().isoformat(timespec='seconds')


def clean_date(value):
    value = (value or '').strip()
    if not value:
        return date.today().isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise PeopleError('Enter the date as YYYY-MM-DD.')


def get_person(user_id, role):
    row = db.query('SELECT id, role, name, roll, emp_code, is_active, status, status_date '
                   'FROM users WHERE id=? AND role=?', (user_id, role), one=True)
    if not row:
        return None
    person = dict(row)
    person['status'] = person['status'] or ('active' if person['is_active'] else
                                            ('left' if role == 'teacher' else 'removed'))
    return person


def history(user_id):
    return db.query(
        """SELECT h.*, u.name AS changed_by_name FROM user_status_history h
           LEFT JOIN users u ON u.id=h.changed_by
           WHERE h.user_id=? ORDER BY h.id DESC""", (user_id,))


def _record(conn, person, new_status, when, reason, actor_id):
    active = 1 if new_status == 'active' else 0
    conn.execute('UPDATE users SET status=?, status_date=?, is_active=? WHERE id=?',
                 (None if active else new_status, when, active, person['id']))
    conn.execute(
        'INSERT INTO user_status_history(user_id, old_status, new_status, effective_date, '
        'reason, changed_by) VALUES(?,?,?,?,?,?)',
        (person['id'], person['status'], new_status, when,
         ' '.join((reason or '').split())[:200] or None, actor_id))


def _transaction(work):
    conn = db.get_db()
    try:
        result = work(conn)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


# --------------------------------------------------------------------------- #
# Students
# --------------------------------------------------------------------------- #
def set_student_status(user_ids, status, when, reason, actor_id):
    """Set one status on many students. Returns the number actually changed."""
    if status not in STUDENT_STATUSES:
        raise PeopleError('Choose a valid student status.')
    when = clean_date(when)
    people = [p for p in (get_person(i, 'student') for i in user_ids) if p]

    def work(conn):
        changed = 0
        for person in people:
            if person['status'] != status:
                _record(conn, person, status, when, reason, actor_id)
                changed += 1
        return changed
    return _transaction(work)


def match_rolls(text):
    """Split pasted roll numbers into (found students, unknown values)."""
    rolls = []
    for line in (text or '').replace(',', '\n').replace(';', '\n').split():
        roll = line.strip().upper()
        if roll and roll not in rolls:
            rolls.append(roll)
    if len(rolls) > 2000:
        raise PeopleError('Paste at most 2000 roll numbers at a time.')
    found, unknown = [], []
    for roll in rolls:
        row = db.query("SELECT id FROM users WHERE role='student' AND UPPER(roll)=?",
                       (roll,), one=True)
        if row:
            found.append(get_person(row['id'], 'student'))
        else:
            unknown.append(roll)
    return found, unknown


# --------------------------------------------------------------------------- #
# Teachers
# --------------------------------------------------------------------------- #
_CLASS_SQL = """SELECT cs.id, cs.timetable_id, cs.subject_code, cs.subject_name, cs.period_no,
        t.code AS timetable, b.code AS branch, s.number AS semester, ay.name AS academic_year,
        (SELECT COUNT(*) FROM feedback_forms f WHERE f.is_deleted=0
           AND f.status IN ('open','draft')
           AND (f.session_id=cs.id OR EXISTS (SELECT 1 FROM form_sessions fs
                WHERE fs.form_id=f.id AND fs.session_id=cs.id))) AS live_forms
    FROM class_sessions cs
    JOIN timetables t ON t.id=cs.timetable_id
    JOIN branches b ON b.id=t.branch_id
    JOIN semesters s ON s.id=t.semester_id
    LEFT JOIN academic_years ay ON ay.id=t.academic_year_id"""


def teacher_classes(teacher_id):
    """The classes this teacher currently teaches."""
    return db.query(_CLASS_SQL + """ WHERE cs.teacher_user_id=? AND cs.is_deleted=0
        ORDER BY ay.name DESC, s.number DESC, b.code, t.code, cs.subject_code""", (teacher_id,))


def second_faculty_roles(teacher_id):
    return db.query(
        """SELECT sfa.id, sfa.subject_code, sfa.subject_name, t.code AS timetable
           FROM second_faculty_assignments sfa
           JOIN class_sessions cs ON cs.id=sfa.session_id AND cs.is_deleted=0
           JOIN timetables t ON t.id=cs.timetable_id
           WHERE sfa.second_teacher_user_id=? AND sfa.status='confirmed'
           ORDER BY t.code, sfa.subject_code""", (teacher_id,))


def replacement_choices(teacher_id):
    return db.query("SELECT id, name, emp_code FROM users WHERE role='teacher' "
                    'AND is_active=1 AND id<>? ORDER BY name, emp_code', (teacher_id,))


def teacher_by_code(emp_code):
    row = db.query("SELECT id FROM users WHERE role='teacher' AND is_active=1 "
                   'AND UPPER(emp_code)=?', ((emp_code or '').strip().upper(),), one=True)
    return row['id'] if row else None


def open_replacements(teacher_id=None, vacant_only=False):
    sql = """SELECT cr.*, cs.subject_code, cs.subject_name, t.code AS timetable,
            o.name AS old_name, o.emp_code AS old_emp, n.name AS new_name, n.emp_code AS new_emp
        FROM class_replacements cr
        JOIN class_sessions cs ON cs.id=cr.old_session_id
        JOIN timetables t ON t.id=cs.timetable_id
        JOIN users o ON o.id=cr.old_teacher_user_id
        LEFT JOIN users n ON n.id=cr.new_teacher_user_id
        WHERE cr.ended_at IS NULL"""
    args = []
    if teacher_id:
        sql += ' AND cr.old_teacher_user_id=?'
        args.append(teacher_id)
        # Not the rows that only record a substitute handing a class back.
        sql += (' AND NOT EXISTS (SELECT 1 FROM class_replacements r '
                'WHERE r.ended_at IS NOT NULL AND r.old_session_id=cr.new_session_id '
                'AND r.new_session_id=cr.old_session_id)')
    if vacant_only:
        sql += ' AND cr.new_session_id IS NULL'
    return db.query(sql + ' ORDER BY t.code, cs.subject_code', tuple(args))


def _close_session(conn, session_id):
    conn.execute('UPDATE class_sessions SET is_deleted=1, deleted_at=? WHERE id=?',
                 (_now(), session_id))


def _reopen_session(conn, session_id):
    conn.execute('UPDATE class_sessions SET is_deleted=0, deleted_at=NULL WHERE id=?',
                 (session_id,))


def _copy_students(conn, source_id, target_id):
    conn.execute(
        """INSERT INTO session_students(session_id, student_user_id)
           SELECT ?, ss.student_user_id FROM session_students ss
           WHERE ss.session_id=? AND NOT EXISTS (SELECT 1 FROM session_students x
                 WHERE x.session_id=? AND x.student_user_id=ss.student_user_id)""",
        (target_id, source_id, target_id))


def _open_class_for(conn, old_session_id, new_teacher_id):
    """Create (or reopen) the replacement teacher's session for the same class."""
    old = conn.execute('SELECT * FROM class_sessions WHERE id=?', (old_session_id,)).fetchone()
    teacher = conn.execute(
        "SELECT id, name, emp_code FROM users WHERE id=? AND role='teacher' AND is_active=1",
        (new_teacher_id,)).fetchone()
    if not teacher or new_teacher_id == old['teacher_user_id']:
        raise PeopleError('Choose a different, active teacher as the replacement.')
    existing = conn.execute(
        'SELECT id, is_deleted FROM class_sessions WHERE timetable_id=? AND subject_code=? '
        'AND teacher_user_id=?',
        (old['timetable_id'], old['subject_code'], new_teacher_id)).fetchone()
    if existing:
        if not existing['is_deleted']:
            raise PeopleError(f'{teacher["name"]} already teaches {old["subject_code"]} in that '
                              'section. Choose another teacher.')
        new_id = existing['id']
        _reopen_session(conn, new_id)
        # This teacher had been replaced on this class earlier and is now taking it back.
        conn.execute('UPDATE class_replacements SET ended_at=? '
                     'WHERE old_session_id=? AND ended_at IS NULL', (_now(), new_id))
    else:
        conn.execute(
            'INSERT INTO class_sessions(timetable_id, subject_code, subject_name, '
            'teacher_user_id, period_no) VALUES(?,?,?,?,?)',
            (old['timetable_id'], old['subject_code'], old['subject_name'], new_teacher_id,
             old['period_no']))
        new_id = conn.execute(
            'SELECT id FROM class_sessions WHERE timetable_id=? AND subject_code=? '
            'AND teacher_user_id=?',
            (old['timetable_id'], old['subject_code'], new_teacher_id)).fetchone()['id']
    _copy_students(conn, old_session_id, new_id)

    # Carry the room / lab setting and the other Second Faculty to the new session.
    if not conn.execute('SELECT 1 FROM teacher_subject_settings WHERE session_id=?',
                        (new_id,)).fetchone():
        conn.execute(
            'INSERT INTO teacher_subject_settings(session_id, teacher_user_id, room, '
            'lab_available, lab_status) SELECT ?, ?, room, lab_available, lab_status '
            'FROM teacher_subject_settings WHERE session_id=?',
            (new_id, new_teacher_id, old_session_id))
    new_code = teacher['emp_code']
    rows = conn.execute("SELECT * FROM second_faculty_assignments "
                        "WHERE session_id=? AND status='confirmed'",
                        (old_session_id,)).fetchall()
    for a in rows:
        if a['second_teacher_user_id'] == new_teacher_id:
            continue
        if conn.execute('SELECT 1 FROM second_faculty_assignments WHERE session_id=? '
                        'AND second_emp_code=?', (new_id, a['second_emp_code'])).fetchone():
            continue
        conn.execute(
            """INSERT INTO second_faculty_assignments(academic_year_id, semester_id, branch_id,
                   timetable_id, session_id, subject_code, subject_name, main_emp_code,
                   main_teacher_user_id, second_emp_code, second_teacher_user_id, period_no,
                   room, status, source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'confirmed',?)""",
            (a['academic_year_id'], a['semester_id'], a['branch_id'], a['timetable_id'], new_id,
             a['subject_code'], a['subject_name'], new_code, new_teacher_id,
             a['second_emp_code'], a['second_teacher_user_id'], a['period_no'], a['room'],
             a['source']))
    return new_id


def deactivate_teacher(teacher_id, status, when, reason, plan, actor_id):
    """Mark a teacher on leave / left.

    ``plan`` maps every current class session id to a replacement teacher id,
    ``None`` (no teacher assigned yet) or ``'keep'`` (finished class: untouched).
    """
    if status not in ('on_leave', 'left'):
        raise PeopleError('Choose "On leave" or "Left".')
    when = clean_date(when)
    person = get_person(teacher_id, 'teacher')
    if not person:
        raise PeopleError('Teacher not found.')
    classes = [c['id'] for c in teacher_classes(teacher_id)]
    if set(classes) != set(plan):
        raise PeopleError('The class list changed. Reload the page and try again.')
    kind = 'temporary' if status == 'on_leave' else 'permanent'

    def work(conn):
        if person['status'] != status:
            _record(conn, person, status, when, reason, actor_id)
        if status == 'left':       # a leave that became permanent
            conn.execute("UPDATE class_replacements SET kind='permanent' "
                         'WHERE old_teacher_user_id=? AND ended_at IS NULL', (teacher_id,))
        moved = vacant = 0
        for session_id in classes:
            target = plan[session_id]
            if target == 'keep':
                continue
            new_id = _open_class_for(conn, session_id, target) if target else None
            _close_session(conn, session_id)
            conn.execute(
                'INSERT INTO class_replacements(old_session_id, new_session_id, '
                'old_teacher_user_id, new_teacher_user_id, kind, start_date, changed_by) '
                'VALUES(?,?,?,?,?,?,?)',
                (session_id, new_id, teacher_id, target or None, kind, when, actor_id))
            moved += bool(target)
            vacant += not target
        # Second Faculty duties stop too (soft removal, same as the existing screen).
        conn.execute(
            "UPDATE second_faculty_assignments SET status='rejected', "
            'removed_at=CURRENT_TIMESTAMP, removed_by=?, updated_at=CURRENT_TIMESTAMP '
            "WHERE second_teacher_user_id=? AND status='confirmed' AND session_id IN "
            '(SELECT id FROM class_sessions WHERE is_deleted=0)', (actor_id, teacher_id))
        return {'replaced': moved, 'vacant': vacant}
    return _transaction(work)


def assign_vacancy(replacement_id, new_teacher_id, actor_id):
    """Give a "teacher not assigned" class to a teacher."""
    def work(conn):
        row = conn.execute('SELECT * FROM class_replacements WHERE id=? AND ended_at IS NULL '
                           'AND new_session_id IS NULL', (replacement_id,)).fetchone()
        if not row:
            raise PeopleError('That class is no longer waiting for a teacher.')
        new_id = _open_class_for(conn, row['old_session_id'], new_teacher_id)
        conn.execute('UPDATE class_replacements SET new_session_id=?, new_teacher_user_id=?, '
                     'changed_by=? WHERE id=?',
                     (new_id, new_teacher_id, actor_id, replacement_id))
    _transaction(work)


def reactivate_teacher(teacher_id, when, reason, return_ids, actor_id):
    """Make a teacher active again and give back the chosen classes."""
    when = clean_date(when)
    person = get_person(teacher_id, 'teacher')
    if not person or person['status'] == 'active':
        raise PeopleError('This teacher is already active.')

    def work(conn):
        _record(conn, person, 'active', when, reason, actor_id)
        returned = 0
        for replacement_id in return_ids:
            row = conn.execute(
                'SELECT * FROM class_replacements WHERE id=? AND old_teacher_user_id=? '
                'AND ended_at IS NULL', (replacement_id, teacher_id)).fetchone()
            if not row:
                raise PeopleError('The class list changed. Reload the page and try again.')
            new_id = row['new_session_id']
            if new_id:
                current = conn.execute('SELECT is_deleted FROM class_sessions WHERE id=?',
                                       (new_id,)).fetchone()
                if current['is_deleted']:
                    raise PeopleError('A substitute was also replaced on one of these classes. '
                                      'Return the substitute first.')
                _copy_students(conn, new_id, row['old_session_id'])
                _close_session(conn, new_id)
            _reopen_session(conn, row['old_session_id'])
            conn.execute('UPDATE class_replacements SET ended_at=?, ended_by=? WHERE id=?',
                         (_now(), actor_id, replacement_id))
            # The substitute's session stays closed for new feedback.
            if new_id:
                conn.execute(
                    'INSERT INTO class_replacements(old_session_id, new_session_id, '
                    'old_teacher_user_id, new_teacher_user_id, kind, start_date, changed_by) '
                    'VALUES(?,?,?,?,?,?,?)',
                    (new_id, row['old_session_id'], row['new_teacher_user_id'], teacher_id,
                     'permanent', when, actor_id))
            returned += 1
        return returned
    return _transaction(work)
