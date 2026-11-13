"""Phase 3 — Teacher Assignment Setup (room number + lab availability).

Data model
----------
The unit a teacher configures is a *teaching session* (``class_sessions`` row):
one section (timetable) + one subject + one teacher.  The importer already
collapses repeated timetable periods of the same subject into that one row, so
"the same subject in several periods" can never produce several configurations.
Different sections are different sessions and therefore stay independent.

Settings live in ``teacher_subject_settings`` (``UNIQUE(session_id)``, v19):

* ``room``        free text, nullable (optional).
* ``lab_status``  NULL = not configured, ``'available'`` or ``'none'`` (v21).
* ``lab_available`` legacy 0/1, kept in sync (1 only for ``'available'``).

Nothing here touches ``class_sessions``, ``timetables`` or feedback data, and a
lab selection never creates a ``lab_faculty_assignments`` row (Phase 4's job).

Security: every function takes the *authenticated* teacher's user id.  Session
ids sent by a browser are only ever accepted if they belong to that teacher.
"""
import re
from datetime import datetime

from . import db

LAB_AVAILABLE = 'available'
LAB_NONE = 'none'
LAB_STATUSES = (LAB_AVAILABLE, LAB_NONE)

ROOM_MAX_LEN = 20
MAX_ITEMS = 500
# Letters, digits, space and the punctuation seen in room labels ("A-204", "Lab 3/B").
_ROOM_RE = re.compile(r"^[A-Za-z0-9 \-/_.,()#&]+$")


class SetupError(Exception):
    """A request the teacher can fix.  ``status`` is the HTTP status to use."""

    def __init__(self, message, status=400, errors=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.errors = errors or []


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def normalize_room(value):
    """Return the cleaned room (``None`` when empty).  Raises ``ValueError``."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('Room number must be text.')
    room = ' '.join(value.split())
    if not room:
        return None
    if len(room) > ROOM_MAX_LEN:
        raise ValueError(f'Room number is too long (maximum {ROOM_MAX_LEN} characters).')
    if not _ROOM_RE.match(room):
        raise ValueError('Room number may contain only letters, digits, spaces and - / _ . , ( ) # &')
    return room


def normalize_lab_status(value):
    """``'available'`` / ``'none'`` / ``None`` (clear back to "not configured")."""
    if value is None or value == '':
        return None
    if value in LAB_STATUSES:
        return value
    raise ValueError("Lab availability must be 'available' or 'none'.")


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
_ASSIGNMENT_SQL = """
    SELECT cs.id AS session_id, cs.subject_code, cs.subject_name, cs.period_no,
           t.id AS timetable_id, t.code AS section, t.name AS section_name,
           b.code AS branch, b.name AS branch_name,
           sem.number AS semester_number, sem.label AS semester_label,
           ay.name AS academic_year,
           (SELECT COUNT(*) FROM session_students ss
             WHERE ss.session_id = cs.id) AS students,
           s.room AS room, s.lab_status AS lab_status, s.updated_at AS updated_at,
           (SELECT COUNT(*) FROM lab_faculty_assignments l
             WHERE l.session_id = cs.id AND l.status = 'confirmed') AS lab_assignments
    FROM class_sessions cs
    JOIN timetables t ON t.id = cs.timetable_id AND t.is_deleted = 0
    JOIN branches b ON b.id = t.branch_id
    JOIN semesters sem ON sem.id = t.semester_id
    LEFT JOIN academic_years ay ON ay.id = t.academic_year_id
    LEFT JOIN teacher_subject_settings s
           ON s.session_id = cs.id AND s.teacher_user_id = cs.teacher_user_id
    WHERE cs.teacher_user_id = ? AND cs.is_deleted = 0"""

_ORDER = " ORDER BY cs.subject_name, cs.subject_code, b.code, t.code, cs.id"


def _row(r):
    sem = r['semester_label'] or (f"Semester {r['semester_number']}"
                                  if r['semester_number'] is not None else '')
    return {
        'session_id': r['session_id'],
        'timetable_id': r['timetable_id'],
        'subject_code': r['subject_code'],
        'subject_name': r['subject_name'],
        'section': r['section'],
        'section_name': r['section_name'],
        'branch': r['branch'],
        'branch_name': r['branch_name'],
        'semester': sem,
        'semester_number': r['semester_number'],
        'academic_year': r['academic_year'],
        'period_no': r['period_no'],
        'students': r['students'],
        'room': r['room'],
        'lab_status': r['lab_status'],
        'configured': r['lab_status'] in LAB_STATUSES,
        'lab_assignments': r['lab_assignments'],
        'updated_at': r['updated_at'],
    }


def teacher_assignments(teacher_user_id, academic_year_id=None):
    """Flat list: one dict per teaching session of this teacher (section-wise)."""
    sql, args = _ASSIGNMENT_SQL, [teacher_user_id]
    if academic_year_id:
        sql += ' AND t.academic_year_id = ?'
        args.append(academic_year_id)
    return [_row(r) for r in db.query(sql + _ORDER, tuple(args))]


def group_by_subject(rows):
    """Group sessions into one card per *subject code* (never by name alone)."""
    groups, order = {}, []
    for r in rows:
        key = r['subject_code']
        if key not in groups:
            groups[key] = {'subject_code': key, 'subject_name': r['subject_name'],
                           'sections': []}
            order.append(key)
        groups[key]['sections'].append(r)
    return [groups[k] for k in order]


def get_assignment(teacher_user_id, session_id):
    """One session, only if it belongs to this teacher; else ``None``."""
    r = db.query(_ASSIGNMENT_SQL + ' AND cs.id = ?', (teacher_user_id, session_id), one=True)
    return _row(r) if r else None


def summarize(rows):
    """Counts shown on the review step and the completion banner."""
    total = len(rows)
    configured = sum(1 for r in rows if r['configured'])
    return {
        'total': total,
        'configured': configured,
        'pending': total - configured,
        'lab_available': sum(1 for r in rows if r['lab_status'] == LAB_AVAILABLE),
        'no_lab': sum(1 for r in rows if r['lab_status'] == LAB_NONE),
        'without_room': sum(1 for r in rows if not (r['room'] or '').strip()),
        'complete': total > 0 and configured == total,
    }


def teacher_summary(teacher_user_id):
    return summarize(teacher_assignments(teacher_user_id))


def lab_eligible_sessions(teacher_user_id):
    """Sessions this teacher marked 'Lab Available'.

    This is the hook for Phase 4 (Lab Faculty Assignment): ``No Lab`` and
    unconfigured sessions are excluded.  It only *reads*; selecting Lab Available
    never creates an assignment.
    """
    return [r for r in teacher_assignments(teacher_user_id)
            if r['lab_status'] == LAB_AVAILABLE]


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #
def _validate_items(teacher_user_id, items):
    """Return ``(clean, errors)`` without writing anything.

    ``items`` is untrusted browser input.  Each item may carry ``room`` and/or
    ``lab_status``; a key that is absent leaves that field unchanged.
    """
    if not isinstance(items, list) or not items:
        raise SetupError('Nothing to save.')
    if len(items) > MAX_ITEMS:
        raise SetupError('Too many items in one request.')

    ids, clean = [], []
    for it in items:
        if not isinstance(it, dict):
            raise SetupError('Malformed item.')
        sid = it.get('session_id')
        if isinstance(sid, bool) or not isinstance(sid, int):
            raise SetupError('Malformed session id.')
        ids.append(sid)
    if len(set(ids)) != len(ids):
        raise SetupError('The same assignment was submitted twice.')

    # Ownership: anything not returned here is not this teacher's (or is gone).
    ph = ','.join('?' * len(ids))
    owned = {r['id'] for r in db.query(
        f"""SELECT cs.id FROM class_sessions cs
            JOIN timetables t ON t.id = cs.timetable_id AND t.is_deleted = 0
            WHERE cs.teacher_user_id = ? AND cs.is_deleted = 0 AND cs.id IN ({ph})""",
        (teacher_user_id, *ids))}
    if any(i not in owned for i in ids):
        # Same answer for "not yours" and "does not exist": no probing.
        raise SetupError('You are not authorised to change one or more of these assignments.',
                         status=403)

    lab_taken = {r['session_id'] for r in db.query(
        f"""SELECT DISTINCT session_id FROM lab_faculty_assignments
            WHERE status = 'confirmed' AND session_id IN ({ph})""", tuple(ids))}

    # Phase 4: Second Faculty only exists for sections with a lab, so "No Lab"
    # must not orphan an active Second Faculty assignment either.
    sf_taken = {r['session_id'] for r in db.query(
        f"""SELECT DISTINCT session_id FROM second_faculty_assignments
            WHERE status = 'confirmed' AND second_teacher_user_id IS NOT NULL
              AND session_id IN ({ph})""", tuple(ids))}

    errors = []
    for it in items:
        sid = it['session_id']
        entry = {'session_id': sid, 'room_set': 'room' in it, 'lab_set': 'lab_status' in it}
        try:
            if entry['room_set']:
                entry['room'] = normalize_room(it['room'])
        except ValueError as exc:
            errors.append({'session_id': sid, 'field': 'room', 'message': str(exc)})
        try:
            if entry['lab_set']:
                entry['lab_status'] = normalize_lab_status(it['lab_status'])
        except ValueError as exc:
            errors.append({'session_id': sid, 'field': 'lab_status', 'message': str(exc)})
        else:
            # Timetable/assignment inconsistency: lab faculty already confirmed for
            # this session, so "No Lab" would orphan them.  Never resolved silently.
            if (entry['lab_set'] and entry.get('lab_status') != LAB_AVAILABLE
                    and sid in lab_taken):
                errors.append({
                    'session_id': sid, 'field': 'lab_status',
                    'message': 'Lab faculty is already assigned to this subject and section. '
                               'Ask the HOD/admin to cancel that assignment before changing '
                               'the lab availability.'})
            elif (entry['lab_set'] and entry.get('lab_status') != LAB_AVAILABLE
                    and sid in sf_taken):
                errors.append({
                    'session_id': sid, 'field': 'lab_status',
                    'message': 'Second Faculty is already assigned to this subject and section. '
                               'Remove the Second Faculty (Second Faculty page) before marking '
                               'it as No Lab.'})
        clean.append(entry)
    return clean, errors


def save_configuration(teacher_user_id, items, actor_user_id=None):
    """Validate and persist several settings atomically.

    All-or-nothing: any validation problem (or database error) leaves the
    database exactly as it was.  Returns the fresh summary.
    """
    clean, errors = _validate_items(teacher_user_id, items)
    if errors:
        raise SetupError('Some entries need attention. Nothing was saved.',
                         status=422, errors=errors)

    conn = db.get_db()
    now = datetime.utcnow().isoformat(timespec='seconds')
    actor = actor_user_id or teacher_user_id
    try:
        for e in clean:
            sid = e['session_id']
            existing = conn.execute(
                'SELECT teacher_user_id FROM teacher_subject_settings WHERE session_id=?',
                (sid,)).fetchone()
            if not existing:
                conn.execute(
                    """INSERT INTO teacher_subject_settings
                       (session_id, teacher_user_id, room, lab_available, lab_status,
                        updated_by, created_at, updated_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (sid, teacher_user_id, e.get('room'),
                     1 if e.get('lab_status') == LAB_AVAILABLE else 0,
                     e.get('lab_status'), actor, now, now))
                continue
            if existing['teacher_user_id'] != teacher_user_id:
                # The session was re-assigned to this teacher after another teacher
                # configured it: start clean, never inherit their room/lab choice.
                conn.execute(
                    """UPDATE teacher_subject_settings
                       SET teacher_user_id=?, room=?, lab_available=?, lab_status=?,
                           updated_by=?, updated_at=? WHERE session_id=?""",
                    (teacher_user_id, e.get('room'),
                     1 if e.get('lab_status') == LAB_AVAILABLE else 0,
                     e.get('lab_status'), actor, now, sid))
                continue
            sets, args = [], []
            if e['room_set']:
                sets.append('room=?')
                args.append(e['room'])
            if e['lab_set']:
                sets += ['lab_status=?', 'lab_available=?']
                args += [e['lab_status'], 1 if e['lab_status'] == LAB_AVAILABLE else 0]
            if sets:
                sets += ['updated_by=?', 'updated_at=?']
                args += [actor, now, sid]
                conn.execute(
                    f"UPDATE teacher_subject_settings SET {', '.join(sets)} WHERE session_id=?",
                    tuple(args))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return teacher_summary(teacher_user_id)


def save_room(teacher_user_id, session_id, room, actor_user_id=None):
    """Save (or clear) the room of one session."""
    return save_configuration(teacher_user_id, [{'session_id': session_id, 'room': room}],
                              actor_user_id)


def save_lab_status(teacher_user_id, session_id, lab_status, actor_user_id=None):
    """Save the lab availability of one session."""
    return save_configuration(
        teacher_user_id, [{'session_id': session_id, 'lab_status': lab_status}], actor_user_id)
