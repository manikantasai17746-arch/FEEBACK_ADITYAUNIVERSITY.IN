"""Business logic for the feedback system.

All functions are additive to the existing schema and operate on real data.
Anonymity is enforced here: every teacher-facing query reads from the
``anonymous_answers`` view (which has no student linkage) or aggregates
without ever selecting student identity.
"""
import csv
import io
import os
from datetime import datetime, timedelta, timezone

from . import db
from .security import audit


# --------------------------------------------------------------------------- #
# Reference / option lists
# --------------------------------------------------------------------------- #
def academic_years():
    return db.query('SELECT * FROM academic_years ORDER BY name DESC')


def semesters():
    return db.query('SELECT * FROM semesters ORDER BY number')


def branches():
    return db.query('SELECT * FROM branches ORDER BY code')


def timetables(include_deleted=False):
    sql = """SELECT t.*, b.code AS branch, s.number AS semester, ay.name AS academic_year
             FROM timetables t JOIN branches b ON b.id=t.branch_id
             JOIN semesters s ON s.id=t.semester_id
             LEFT JOIN academic_years ay ON ay.id=t.academic_year_id"""
    if not include_deleted:
        sql += ' WHERE t.is_deleted=0'
    return db.query(sql + ' ORDER BY b.code, t.code')


def subjects_list(search=None):
    sql = """SELECT course_code, course_name, COUNT(DISTINCT emp_code) AS teachers,
                    COUNT(DISTINCT dept) AS branches
             FROM assignments {where}
             GROUP BY course_code, course_name ORDER BY course_code"""
    args = ()
    where = ''
    if search:
        where = 'WHERE course_code LIKE ? OR course_name LIKE ?'
        args = (f'%{search}%', f'%{search}%')
    return db.query(sql.format(where=where), args)


def teachers_list(search=None, limit=None, offset=0):
    where = "WHERE u.role='teacher'"
    args = []
    if search:
        where += ' AND (u.name LIKE ? OR u.emp_code LIKE ?)'
        args += [f'%{search}%', f'%{search}%']
    sql = f"""SELECT u.id, u.name, u.emp_code,
                     COUNT(DISTINCT cs.id) AS sessions,
                     COUNT(DISTINCT cs.subject_code) AS subjects
              FROM users u LEFT JOIN class_sessions cs ON cs.teacher_user_id=u.id
              {where} GROUP BY u.id ORDER BY u.name"""
    if limit:
        sql += f' LIMIT {int(limit)} OFFSET {int(offset)}'
    return db.query(sql, tuple(args))


def teachers_count(search=None):
    where, args = "role='teacher'", ()
    if search:
        where += ' AND (name LIKE ? OR emp_code LIKE ?)'
        args = (f'%{search}%', f'%{search}%')
    return db.query(f'SELECT COUNT(*) c FROM users WHERE {where}', args, one=True)['c']


def students_list(search=None, branch=None, limit=None, offset=0):
    where = "WHERE u.role='student'"
    args = []
    if search:
        where += ' AND u.roll LIKE ?'
        args.append(f'%{search}%')
    if branch:
        where += ' AND e.timetable_id IN (SELECT id FROM timetables WHERE branch_id=?)'
        args.append(branch)
    sql = f"""SELECT u.id, u.roll, t.code AS timetable, b.code AS branch,
                     s.number AS semester, ay.name AS academic_year
              FROM users u
              LEFT JOIN enrollments e ON e.student_user_id=u.id
              LEFT JOIN timetables t ON t.id=e.timetable_id
              LEFT JOIN branches b ON b.id=t.branch_id
              LEFT JOIN semesters s ON s.id=e.semester_id
              LEFT JOIN academic_years ay ON ay.id=e.academic_year_id
              {where} ORDER BY u.roll"""
    if limit:
        sql += f' LIMIT {int(limit)} OFFSET {int(offset)}'
    return db.query(sql, tuple(args))


def students_count(search=None, branch=None):
    where = "role='student'"
    args = []
    if search:
        where += ' AND roll LIKE ?'
        args.append(f'%{search}%')
    return db.query(f'SELECT COUNT(*) c FROM users WHERE {where}', tuple(args), one=True)['c']


# --------------------------------------------------------------------------- #
# Sessions and eligibility
# --------------------------------------------------------------------------- #
SESSION_SELECT = """SELECT cs.*, t.code AS timetable, t.name AS timetable_name,
    t.id AS tt_id, b.id AS branch_id, b.code AS branch, b.name AS branch_name,
    s.id AS semester_id, s.number AS semester, ay.id AS year_id, ay.name AS academic_year,
    u.name AS teacher_name, u.emp_code AS teacher_emp
    FROM class_sessions cs
    JOIN timetables t ON t.id=cs.timetable_id
    JOIN branches b ON b.id=t.branch_id
    JOIN semesters s ON s.id=t.semester_id
    LEFT JOIN academic_years ay ON ay.id=t.academic_year_id
    LEFT JOIN users u ON u.id=cs.teacher_user_id"""


def session(session_id):
    return db.query(SESSION_SELECT + ' WHERE cs.id=?', (session_id,), one=True)


def sessions_for_timetable(timetable_id, include_deleted=False):
    sql = SESSION_SELECT + ' WHERE cs.timetable_id=?'
    if not include_deleted:
        sql += ' AND cs.is_deleted=0'
    return db.query(sql + ' ORDER BY cs.period_no, cs.subject_code', (timetable_id,))


def eligible_students(session_id):
    return db.query("""SELECT u.id, u.roll FROM session_students ss
        JOIN users u ON u.id=ss.student_user_id WHERE ss.session_id=? ORDER BY u.roll""",
        (session_id,))


def eligible_counts(session_ids):
    """{session_id: eligible students} in ONE query (avoids N round trips)."""
    ids = [int(i) for i in session_ids]
    if not ids:
        return {}
    marks = ','.join('?' * len(ids))
    out = {i: 0 for i in ids}
    for r in db.query('SELECT session_id, COUNT(*) c FROM session_students '
                      f'WHERE session_id IN ({marks}) GROUP BY session_id', ids):
        out[r['session_id']] = r['c']
    return out


def eligible_count(session_id):
    return db.query('SELECT COUNT(*) c FROM session_students WHERE session_id=?',
                    (session_id,), one=True)['c']


def student_sessions(student_user_id):
    return db.query(SESSION_SELECT + """ JOIN session_students ss ON ss.session_id=cs.id
        WHERE ss.student_user_id=? AND cs.is_deleted=0
        ORDER BY cs.period_no, cs.subject_code""", (student_user_id,))


def student_is_eligible(student_user_id, session_id):
    return db.query('SELECT 1 FROM session_students WHERE session_id=? AND student_user_id=?',
                    (session_id, student_user_id), one=True) is not None


# --------------------------------------------------------------------------- #
# Form scope: the teaching sessions a form covers (migration v5)
# --------------------------------------------------------------------------- #
def form_session_ids(form_id):
    """Ordered session ids this form is distributed to."""
    return [r['session_id'] for r in db.query(
        'SELECT session_id FROM form_sessions WHERE form_id=? ORDER BY session_id', (form_id,))]


def form_teachers_map(form_ids):
    """{form_id: ["NAME (emp_code)", ...]} - teachers assigned to each form's sessions."""
    ids = [i for i in dict.fromkeys(form_ids or []) if i]
    out = {i: [] for i in ids}
    if not ids:
        return out
    ph = ','.join('?' * len(ids))
    for r in db.query(
            f"""SELECT DISTINCT fs.form_id, u.name, u.emp_code
                FROM form_sessions fs
                JOIN class_sessions cs ON cs.id=fs.session_id
                JOIN users u ON u.id=cs.teacher_user_id
                WHERE fs.form_id IN ({ph}) ORDER BY u.name""", tuple(ids)):
        if r['name']:
            out[r['form_id']].append(
                f"{r['name']} ({r['emp_code']})" if r['emp_code'] else r['name'])
    return out


def form_sessions_rows(form_id):
    """Full teaching-session rows covered by a form."""
    sql = SESSION_SELECT + """ JOIN form_sessions fs ON fs.session_id=cs.id
        WHERE fs.form_id=? ORDER BY b.code, t.code, cs.period_no, cs.subject_code"""
    return db.query(sql, (form_id,))


def form_is_subject_scoped(form):
    return bool(form) and (form['scope'] or 'session') == 'subject'


def eligible_student_ids_for_form(form_id):
    return {r['student_user_id'] for r in db.query(
        """SELECT DISTINCT student_user_id FROM session_students
           WHERE session_id IN (SELECT session_id FROM form_sessions WHERE form_id=?)""",
        (form_id,))}


def eligible_count_for_form(form_id):
    return db.query(
        """SELECT COUNT(DISTINCT student_user_id) c FROM session_students
           WHERE session_id IN (SELECT session_id FROM form_sessions WHERE form_id=?)""",
        (form_id,), one=True)['c']


def eligible_counts_for_forms(form_ids):
    """{form_id: eligible student count} for many forms in one query.

    Same numbers as calling :func:`eligible_count_for_form` per form (distinct
    students across every session the form covers); forms with none map to 0.
    """
    ids = list(dict.fromkeys(int(i) for i in form_ids if i))
    out = {i: 0 for i in ids}
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        ph = ','.join('?' * len(chunk))
        for r in db.query(
                f"""SELECT fs.form_id, COUNT(DISTINCT ss.student_user_id) AS c
                    FROM form_sessions fs
                    JOIN session_students ss ON ss.session_id = fs.session_id
                    WHERE fs.form_id IN ({ph}) GROUP BY fs.form_id""", tuple(chunk)):
            out[r['form_id']] = r['c']
    return out


def response_counts_for_forms(form_ids):
    """{form_id: submission count} for many forms in one query (missing -> 0)."""
    ids = list(dict.fromkeys(int(i) for i in form_ids if i))
    out = {i: 0 for i in ids}
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        ph = ','.join('?' * len(chunk))
        for r in db.query(
                f'SELECT form_id, COUNT(*) AS c FROM submissions '
                f'WHERE form_id IN ({ph}) GROUP BY form_id', tuple(chunk)):
            out[r['form_id']] = r['c']
    return out


def publication_infos(pairs):
    """{(form_id, session_id): publication row} for many forms in one query."""
    pairs = [(int(f), int(s)) for f, s in pairs if f and s]
    out = {}
    fids = list(dict.fromkeys(f for f, _ in pairs))
    wanted = set(pairs)
    for start in range(0, len(fids), 400):
        chunk = fids[start:start + 400]
        ph = ','.join('?' * len(chunk))
        for r in db.query(
                f"""SELECT rp.*, u.name AS published_by_name
                    FROM result_publications rp LEFT JOIN users u ON u.id=rp.published_by
                    WHERE rp.form_id IN ({ph})""", tuple(chunk)):
            key = (r['form_id'], r['session_id'])
            if key in wanted:
                out[key] = r
    return out


def eligible_count_for_sessions(session_ids):
    """Distinct ACTIVE students enrolled in any of the given sessions.

    Used for teacher-scoped numbers: a teacher's eligible population is only
    the students of the sessions they teach, never the whole form's audience.
    """
    ids = [int(i) for i in (session_ids or [])]
    if not ids:
        return 0
    ph = ','.join('?' * len(ids))
    return db.query(
        f"""SELECT COUNT(DISTINCT ss.student_user_id) c FROM session_students ss
            JOIN users u ON u.id=ss.student_user_id
            WHERE ss.session_id IN ({ph}) AND u.is_active=1""",
        tuple(ids), one=True)['c']


_ELIGIBLE_SELECT = """SELECT DISTINCT u.id, u.roll, u.name,
        t.code AS timetable, t.name AS timetable_name, b.code AS branch,
        b.name AS branch_name, s.number AS semester, ay.name AS academic_year
    FROM form_sessions fs
    JOIN session_students ss ON ss.session_id=fs.session_id
    JOIN users u ON u.id=ss.student_user_id
    JOIN class_sessions cs ON cs.id=fs.session_id
    JOIN timetables t ON t.id=cs.timetable_id
    JOIN branches b ON b.id=t.branch_id
    JOIN semesters s ON s.id=t.semester_id
    LEFT JOIN academic_years ay ON ay.id=t.academic_year_id"""


def eligible_students_for_form(form_id):
    """Every distinct, active student eligible for a form, with academic detail.

    Eligibility is derived from the real relationships: a student is eligible
    when one of the form's covered sessions lists them in ``session_students``
    (i.e. the timetable assigns that subject/teacher to them).  DISTINCT keeps a
    student from appearing twice when they are in several covered sessions.
    """
    return db.query(_ELIGIBLE_SELECT + """
        WHERE fs.form_id=? AND u.is_active=1
        ORDER BY u.roll""", (form_id,))


def not_submitted_students_for_form(form_id):
    """Eligible students who have not submitted this form yet."""
    return db.query(_ELIGIBLE_SELECT + """
        WHERE fs.form_id=? AND u.is_active=1
          AND u.id NOT IN (SELECT student_user_id FROM submissions WHERE form_id=?)
        ORDER BY u.roll""", (form_id, form_id))


def pending_by_teacher(form_id):
    """Pending (not yet submitted) eligible students of a form, grouped by teacher.

    Read-only: it never writes to ``submissions`` or ``answers``, and it only
    exposes roll numbers plus counts - no names and no answer content.

    A student is pending for a teacher when the student sits in one of that
    teacher's covered sessions and has no submission for the form.  For
    second-faculty forms the teacher is the confirmed second faculty member.
    Returns a list of dicts sorted by teacher name.
    """
    form = get_form(form_id)
    if not form:
        return []
    second = (form['category'] or 'main') == 'second'
    rows = db.query("""SELECT cs.id AS session_id, cs.subject_code, cs.subject_name,
            cs.teacher_user_id AS t1, sfa.second_teacher_user_id AS t2,
            u.id AS student_id, u.roll AS roll, t.code AS timetable,
            CASE WHEN sb.id IS NULL THEN 0 ELSE 1 END AS done
        FROM form_sessions fs
        JOIN class_sessions cs ON cs.id=fs.session_id AND cs.is_deleted=0
        JOIN timetables t ON t.id=cs.timetable_id
        JOIN session_students ss ON ss.session_id=cs.id
        JOIN users u ON u.id=ss.student_user_id AND u.is_active=1
        LEFT JOIN submissions sb ON sb.form_id=fs.form_id AND sb.student_user_id=u.id
        LEFT JOIN second_faculty_assignments sfa
               ON sfa.session_id=cs.id AND sfa.status='confirmed'
        WHERE fs.form_id=?""", (form_id,))
    groups = {}
    for r in rows:
        tid = r['t2'] if second else r['t1']
        if not tid:
            continue
        g = groups.setdefault(tid, {'teacher_user_id': tid, 'eligible': set(),
                                    'submitted': set(), 'pending': {},
                                    'subjects': {}, 'sections': set()})
        g['eligible'].add(r['student_id'])
        g['sections'].add(r['timetable'])
        if r['subject_code']:
            g['subjects'][r['subject_code']] = r['subject_name'] or r['subject_code']
        if r['done']:
            g['submitted'].add(r['student_id'])
        else:
            g['pending'][r['student_id']] = r['roll']
    if not groups:
        return []
    ids = list(groups)
    ph = ','.join('?' * len(ids))
    info = {t['id']: t for t in db.query(
        f'SELECT id, name, emp_code, email FROM users WHERE id IN ({ph})', tuple(ids))}
    out = []
    for tid, g in groups.items():
        t = info.get(tid)
        out.append({
            'teacher_user_id': tid,
            'teacher_name': (t['name'] if t else None) or 'Unknown faculty',
            'emp_code': t['emp_code'] if t else None,
            'email': ((t['email'] if t else None) or '').strip(),
            'eligible': len(g['eligible']),
            'submitted': len(g['submitted']),
            'pending': len(g['pending']),
            'pending_rolls': sorted(r for r in g['pending'].values() if r),
            'subjects': sorted(g['subjects'].values()),
            'sections': sorted(x for x in g['sections'] if x),
        })
    out.sort(key=lambda x: (x['teacher_name'] or '').lower())
    return out


def submitted_students_for_form(form_id):
    """Students (with academic detail) whose submission was recorded for a form.

    Only completion data is read here — the submitted answers are never joined,
    so the anonymity of the response content is preserved.
    """
    return db.query("""SELECT u.id, u.roll, u.name, sb.submitted_at,
            t.code AS timetable, t.name AS timetable_name, b.code AS branch,
            b.name AS branch_name, s.number AS semester, ay.name AS academic_year
        FROM submissions sb
        JOIN users u ON u.id=sb.student_user_id
        LEFT JOIN class_sessions cs ON cs.id=sb.session_id
        LEFT JOIN timetables t ON t.id=cs.timetable_id
        LEFT JOIN branches b ON b.id=t.branch_id
        LEFT JOIN semesters s ON s.id=t.semester_id
        LEFT JOIN academic_years ay ON ay.id=t.academic_year_id
        WHERE sb.form_id=? ORDER BY u.roll""", (form_id,))


def student_form_session(student_user_id, form_id):
    """The covered session through which this student reaches the form."""
    return db.query("""SELECT fs.session_id FROM form_sessions fs
        JOIN session_students ss ON ss.session_id=fs.session_id
        WHERE fs.form_id=? AND ss.student_user_id=?
        ORDER BY fs.session_id LIMIT 1""",
        (form_id, student_user_id), one=True)


def student_eligible_for_form(student_user_id, form_id):
    return db.query("""SELECT 1 FROM form_sessions fs
        JOIN session_students ss ON ss.session_id=fs.session_id
        WHERE fs.form_id=? AND ss.student_user_id=? LIMIT 1""",
        (form_id, student_user_id), one=True) is not None


# --------------------------------------------------------------------------- #
# Forms
# --------------------------------------------------------------------------- #
FORM_SELECT = """SELECT f.*, cs.period_no,
    t.code AS timetable, b.code AS branch, b.name AS branch_name,
    s.number AS semester, ay.name AS academic_year,
    u.name AS teacher_name, u.emp_code AS teacher_emp, u.designation AS teacher_designation
    FROM feedback_forms f
    LEFT JOIN class_sessions cs ON cs.id=f.session_id
    LEFT JOIN timetables t ON t.id=f.timetable_id
    LEFT JOIN branches b ON b.id=f.branch_id
    LEFT JOIN semesters s ON s.id=f.semester_id
    LEFT JOIN academic_years ay ON ay.id=f.academic_year_id
    LEFT JOIN users u ON u.id=cs.teacher_user_id"""


def get_form(form_id, include_deleted=False):
    sql = FORM_SELECT + ' WHERE f.id=?'
    if not include_deleted:
        sql += ' AND f.is_deleted=0'
    return db.query(sql, (form_id,), one=True)


def form_questions(form_id):
    questions = db.query('SELECT * FROM form_questions WHERE form_id=? ORDER BY position, id',
                         (form_id,))
    if not questions:
        return []
    # One query for every option of the form (was: one query per question).
    by_q = {}
    for o in db.query(
            'SELECT * FROM question_options WHERE question_id IN '
            '(SELECT id FROM form_questions WHERE form_id=?) ORDER BY position, id',
            (form_id,)):
        o = dict(o)
        by_q.setdefault(o['question_id'], []).append(o)
    out = []
    for q in questions:
        q = dict(q)
        q['options'] = by_q.get(q['id'], [])
        out.append(q)
    return out


DEFAULT_INSTRUCTIONS = (
    "Please answer every question honestly and thoughtfully. Your responses are "
    "anonymous and will not be shared with the faculty member in a way that "
    "identifies you. The feedback is used only to improve teaching and learning. "
    "You can submit this form only once.")


def save_form(payload, questions, admin_id, form_id=None, session_ids=None):
    """Create or update a form and replace its questions.

    ``payload`` holds the form-level configuration; ``questions`` is a list of
    dicts each with text/qtype/is_required/options[{label,value}].
    ``session_ids`` is the set of teaching sessions the form is distributed to.
    When it is omitted the form covers exactly its own ``session_id`` (the
    historical, session-scoped behaviour).
    """
    conn = db.get_db()
    now = datetime.utcnow().isoformat(timespec='seconds')
    if session_ids is None:
        session_ids = [payload.get('session_id')] if payload.get('session_id') else []
    session_ids = list(dict.fromkeys(sid for sid in session_ids if sid))
    fields = dict(
        title=(payload.get('title') or 'Untitled feedback form').strip()[:200],
        instructions=((payload.get('instructions') or '').strip() or DEFAULT_INSTRUCTIONS)[:2000],
        academic_year_id=payload.get('academic_year_id'),
        semester_id=payload.get('semester_id'),
        branch_id=payload.get('branch_id'),
        timetable_id=payload.get('timetable_id'),
        session_id=payload.get('session_id'),
        scope=payload.get('scope') or 'session',
        subject_code=payload.get('subject_code'),
        subject_name=payload.get('subject_name'),
    )
    if 'deadline_at' in payload:
        fields['deadline_at'] = payload.get('deadline_at')
    if 'phase' in payload:
        fields['phase'] = payload.get('phase') if payload.get('phase') in PHASE_LABELS else None
    # Optional category / second-faculty linkage (HOD or admin). Defaults keep
    # existing behaviour: category='main', no second_faculty_assignment_id.
    category = (payload.get('category') or 'main').strip().lower()
    if category not in ('main', 'second', 'subject_only', 'any'):
        category = 'main'
    fields['category'] = category
    sfa_id = payload.get('second_faculty_assignment_id')
    if sfa_id is not None:
        try:
            fields['second_faculty_assignment_id'] = int(sfa_id) if sfa_id else None
        except (TypeError, ValueError):
            fields['second_faculty_assignment_id'] = None
    if form_id:
        sets = ', '.join(f'{k}=?' for k in fields)
        conn.execute(f'UPDATE feedback_forms SET {sets}, updated_at=? WHERE id=?',
                     (*fields.values(), now, form_id))
    else:
        cols = ', '.join(fields)
        qs = ', '.join('?' for _ in fields)
        form_id = db.insert_id(
            f'INSERT INTO feedback_forms({cols}, status, created_by, created_at, updated_at) '
            f"VALUES({qs}, 'draft', ?, ?, ?)",
            (*fields.values(), admin_id, now, now))

    # Record exactly which teaching sessions this form is distributed to.  This
    # is the single source of truth for eligibility and for the student list.
    conn.execute('DELETE FROM form_sessions WHERE form_id=?', (form_id,))
    if session_ids:
        # one multi-row INSERT instead of one statement per session
        conn.execute(
            'INSERT OR IGNORE INTO form_sessions(form_id, session_id) VALUES '
            + ','.join('(?,?)' for _ in session_ids),
            [v for sid in session_ids for v in (form_id, sid)])

    # replace questions (answers reference questions; only safe before publish).
    # Batched: each statement is a network round trip on the hosted database,
    # so questions and their options go in as two multi-row INSERTs.
    conn.execute('DELETE FROM form_questions WHERE form_id=?', (form_id,))
    if questions:
        q_args = []
        for pos, q in enumerate(questions):
            q_args += [form_id, pos, (q['text'] or '').strip()[:1000],
                       q.get('qtype', 'rating'), 1 if q.get('is_required') else 0]
        conn.execute(
            'INSERT INTO form_questions(form_id, position, text, qtype, is_required) VALUES '
            + ','.join('(?,?,?,?,?)' for _ in questions), q_args)
        o_rows, o_args = [], []
        for pos, q in enumerate(questions):
            for opos, opt in enumerate(q.get('options') or []):
                o_rows.append('((SELECT id FROM form_questions WHERE form_id=? AND position=?),?,?,?)')
                o_args += [form_id, pos, opos, (opt.get('label') or '').strip()[:200], opt.get('value')]
        if o_rows:
            conn.execute(
                'INSERT INTO question_options(question_id, position, label, value) VALUES '
                + ','.join(o_rows), o_args)
    conn.commit()
    return form_id


def validate_form_for_publish(form_id):
    errors = []
    form = get_form(form_id)
    if not form:
        return ['Form not found.']
    if not form['session_id']:
        errors.append('Select a subject / teacher / period (a teaching session), '
                      'or choose a subject to reach every matching timetable.')
    sess_ids = form_session_ids(form_id)
    if not sess_ids:
        errors.append('This form is not linked to any eligible students '
                      '(no matching teaching sessions were found).')
    if form_is_subject_scoped(form) and eligible_count_for_form(form_id) == 0:
        errors.append('No students are enrolled in this subject for the selected '
                      'academic configuration, so the form cannot be published.')
    if not form['title'] or not form['title'].strip():
        errors.append('A form title is required.')
    err = deadline_error(form['deadline_at'])
    if err:
        errors.append(err + ' (Use "Change deadline" on the forms list.)')
    errors.extend(phase_rule_errors(
        form['phase'], sess_ids, exclude_form_id=form_id))
    questions = form_questions(form_id)
    if not questions:
        errors.append('Add at least one question.')
    for i, q in enumerate(questions, 1):
        if not q['text'] or not q['text'].strip():
            errors.append(f'Question {i} has no text.')
        if q['qtype'] in ('rating', 'single_choice', 'multiple_choice'):
            if len(q['options']) < 2:
                errors.append(f'Question {i} needs at least two answer options.')
            for o in q['options']:
                if not o['label'].strip():
                    errors.append(f'Question {i} has an empty option label.')
    return errors


def _snapshot(form_id):
    """Persist human-readable snapshots so history survives later edits."""
    f = get_form(form_id)
    db.execute("""UPDATE feedback_forms SET
        snapshot_academic_year=?, snapshot_semester=?, snapshot_branch=?,
        snapshot_timetable=?, snapshot_subject_code=?, snapshot_subject_name=?,
        snapshot_teacher=?, snapshot_period=? WHERE id=?""",
        (f['academic_year'], f['semester'], f['branch'], f['timetable'],
         f['subject_code'], f['subject_name'], f['teacher_name'], f['period_no'], form_id))


# --------------------------------------------------------------------------- #
# Form deadlines (migration v16)
# --------------------------------------------------------------------------- #
# Deadlines are typed in local (college) time and stored as naive UTC ISO text,
# the same convention as every other timestamp in this database.  India has no
# daylight saving, so a fixed offset (default +05:30) is exact; override with
# APP_UTC_OFFSET_MINUTES for another zone.
def _local_tz():
    try:
        minutes = int(os.environ.get('APP_UTC_OFFSET_MINUTES', '330'))
    except ValueError:
        minutes = 330
    return timezone(timedelta(minutes=minutes))


def _utc_now_iso():
    return datetime.utcnow().isoformat(timespec='seconds')


def parse_deadline_input(value):
    """Local ``YYYY-MM-DDTHH:MM`` (datetime-local) -> UTC ISO text, or None."""
    value = (value or '').strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_local_tz())
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds')


def _deadline_local(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso)).replace(tzinfo=timezone.utc).astimezone(_local_tz())
    except ValueError:
        return None


def deadline_input_value(iso):
    """UTC ISO text -> value for an ``<input type=datetime-local>``."""
    dt = _deadline_local(iso)
    return dt.strftime('%Y-%m-%dT%H:%M') if dt else ''


def format_deadline(iso):
    dt = _deadline_local(iso)
    return dt.strftime('%d %b %Y, %I:%M %p') if dt else 'No deadline'


def deadline_error(deadline_iso):
    """Message when a deadline is missing/invalid/already past, else None."""
    if not deadline_iso:
        return 'Set a feedback deadline (date and time).'
    if deadline_iso <= _utc_now_iso():
        return 'The deadline must be in the future.'
    return None


def deadline_passed(form):
    """True when the form has a deadline and it has already passed."""
    try:
        dl = form['deadline_at']
    except (KeyError, IndexError, TypeError):
        dl = None
    return bool(dl) and str(dl) <= _utc_now_iso()


_last_expiry_sweep = 0.0


def close_expired_forms(force=False):
    """Close every open form whose deadline has passed. Returns how many.

    Serverless hosting has no background worker, so this runs lazily on web
    requests (at most every 30 s per process unless ``force``) and the
    submission path additionally refuses late answers on its own.
    """
    import time
    global _last_expiry_sweep
    now_ts = time.monotonic()
    if not force and now_ts - _last_expiry_sweep < 30:
        return 0
    _last_expiry_sweep = now_ts
    now = _utc_now_iso()
    due = db.query("""SELECT id FROM feedback_forms
        WHERE status='open' AND deadline_at IS NOT NULL AND deadline_at<=?""", (now,))
    for r in due:
        db.execute("""UPDATE feedback_forms SET status='closed', closed_at=?, updated_at=?
            WHERE id=? AND status='open'""", (now, now, r['id']))
        audit('form.auto_closed', 'feedback_forms', r['id'],
              'Closed automatically: deadline reached', actor={'id': None, 'name': 'system',
                                                               'username': 'system'})
    return len(due)


def resolve_deadline_for_save(raw_value, existing_form=None):
    """Validate the deadline posted with a form. Returns (utc_iso, error).

    A deadline is required for every new form. When editing, keeping the stored
    value is always accepted; a changed value must lie in the future.
    """
    iso = parse_deadline_input(raw_value)
    if not iso:
        return None, 'Set a feedback deadline (date and time).'
    stored = None
    if existing_form:
        try:
            stored = existing_form['deadline_at']
        except (KeyError, IndexError, TypeError):
            stored = None
    if stored and iso == stored:
        return iso, None
    err = deadline_error(iso)
    return (None, err) if err else (iso, None)


def set_form_deadline(form_id, deadline_iso):
    """Change a form's deadline; reopen a closed form when moved into the future."""
    now = _utc_now_iso()
    db.execute('UPDATE feedback_forms SET deadline_at=?, updated_at=? WHERE id=?',
               (deadline_iso, now, form_id))
    audit('form.deadline', 'feedback_forms', form_id, f'Deadline set to {deadline_iso} UTC')


def set_form_status(form_id, status, admin_id=None):
    assert status in ('draft', 'open', 'closed', 'archived')
    now = datetime.utcnow().isoformat(timespec='seconds')
    extra, args = '', []
    if status == 'open':
        extra = ', published_at=COALESCE(published_at, ?)'
        args.append(now)
        _snapshot(form_id)
    elif status == 'closed':
        extra = ', closed_at=?'
        args.append(now)
    elif status == 'archived':
        extra = ', archived_at=?'
        args.append(now)
    db.execute(f'UPDATE feedback_forms SET status=?, updated_at=?{extra} WHERE id=?',
               (status, now, *args, form_id))
    audit(f'form.{status}', 'feedback_forms', form_id, f'Status set to {status}')
    if status == 'open':
        link_form_to_requirement(form_id)


def _requirement_matches_form(req, form):
    """True when a published form satisfies a feedback requirement.

    Branch must match. Academic year / semester / subject code are only
    compared when the requirement specifies them. Category must match unless
    the requirement is 'any'; 'subject_only' requirements need a subject code.
    """
    if req['branch_id'] != form['branch_id']:
        return False
    for key in ('academic_year_id', 'semester_id'):
        if req[key] and req[key] != form[key]:
            return False
    rc = req['category'] or 'main'
    fc = form['category'] or 'main'
    if rc == 'subject_only':
        if not form['subject_code']:
            return False
    elif rc != 'any' and rc != fc:
        return False
    if req['subject_code'] and (req['subject_code'] or '').strip().lower() != \
            (form['subject_code'] or '').strip().lower():
        return False
    return True


def link_form_to_requirement(form_id):
    """Attach a just-published form to the best unmatched requirement.

    Returns the requirement id, or None. Earliest deadline wins; a requirement
    with a subject code is preferred over a generic one.
    """
    form = db.query(
        """SELECT id, branch_id, academic_year_id, semester_id, category,
                  subject_code FROM feedback_forms
           WHERE id=? AND is_deleted=0""", (form_id,), one=True)
    if not form or not form['branch_id']:
        return None
    if db.query('SELECT 1 FROM feedback_requirements WHERE matched_form_id=?',
                (form_id,), one=True):
        return None
    cands = [r for r in db.query(
        """SELECT * FROM feedback_requirements
           WHERE branch_id=? AND matched_form_id IS NULL
             AND status != 'cancelled'""", (form['branch_id'],))
        if _requirement_matches_form(r, form)]
    if not cands:
        return None
    cands.sort(key=lambda r: (0 if r['subject_code'] else 1,
                              str(r['deadline'] or '9999'), r['id']))
    rid = cands[0]['id']
    now = datetime.utcnow().isoformat(timespec='seconds')
    db.execute('UPDATE feedback_requirements SET matched_form_id=?, updated_at=? WHERE id=?',
               (form_id, now, rid))
    audit('requirement.matched', 'feedback_requirements', rid,
          f'Linked to form {form_id}')
    return rid


def rematch_requirements():
    """Link already-published forms to unmatched requirements (backfill)."""
    forms = db.query(
        """SELECT id FROM feedback_forms
           WHERE is_deleted=0 AND status IN ('open','closed','archived')
           ORDER BY published_at, id""")
    return sum(1 for f in forms if link_form_to_requirement(f['id']))


def soft_delete_form(form_id):
    db.execute('UPDATE feedback_forms SET is_deleted=1, deleted_at=?, updated_at=? WHERE id=?',
               (datetime.utcnow().isoformat(timespec='seconds'),) * 2 + (form_id,))
    audit('form.delete', 'feedback_forms', form_id, 'Soft deleted')


def restore_form(form_id):
    db.execute('UPDATE feedback_forms SET is_deleted=0, deleted_at=NULL WHERE id=?', (form_id,))
    audit('form.restore', 'feedback_forms', form_id, 'Restored')


def list_forms(filters=None, limit=None, offset=0):
    filters = filters or {}
    where, args = ['1=1'], []
    if not filters.get('include_deleted'):
        where.append('f.is_deleted=0')
    for key, col in (('status', 'f.status'), ('branch_id', 'f.branch_id'),
                     ('semester_id', 'f.semester_id'), ('academic_year_id', 'f.academic_year_id'),
                     ('timetable_id', 'f.timetable_id'), ('session_id', 'f.session_id')):
        if filters.get(key):
            where.append(f'{col}=?')
            args.append(filters[key])
    if filters.get('teacher_id'):
        where.append('cs.teacher_user_id=?')
        args.append(filters['teacher_id'])
    if filters.get('subject_code'):
        where.append('COALESCE(f.subject_code, cs.subject_code)=?')
        args.append(filters['subject_code'])
    if filters.get('q'):
        where.append('(f.title LIKE ? OR COALESCE(f.subject_name, cs.subject_name) LIKE ? '
                     'OR COALESCE(f.subject_code, cs.subject_code) LIKE ?)')
        args += [f"%{filters['q']}%"] * 3
    sql = FORM_SELECT + ' WHERE ' + ' AND '.join(where) + ' ORDER BY f.id DESC'
    if limit:
        sql += f' LIMIT {int(limit)} OFFSET {int(offset)}'
    return db.query(sql, tuple(args))


def forms_count(filters=None):
    rows = list_forms(filters)
    return len(rows)


# --------------------------------------------------------------------------- #
# Student side
# --------------------------------------------------------------------------- #
_STUDENT_FORM_SELECT = """SELECT f.*,
        MIN(cs.period_no) AS period_no,
        MIN(t.code) AS timetable, MIN(b.code) AS branch, MIN(b.name) AS branch_name,
        MIN(s.number) AS semester, MIN(ay.name) AS academic_year,
        MIN(u.name) AS teacher_name, MIN(u.designation) AS teacher_designation,
        MIN(u.emp_code) AS teacher_emp,
        (SELECT s2.id FROM submissions s2 WHERE s2.form_id=f.id AND s2.student_user_id=?)
            AS submission_id,
        (SELECT s2.submitted_at FROM submissions s2
            WHERE s2.form_id=f.id AND s2.student_user_id=?) AS submitted_at
    FROM feedback_forms f
    JOIN form_sessions fs ON fs.form_id=f.id
    JOIN session_students ss ON ss.session_id=fs.session_id AND ss.student_user_id=?
    JOIN class_sessions cs ON cs.id=fs.session_id
    JOIN timetables t ON t.id=cs.timetable_id
    JOIN branches b ON b.id=t.branch_id
    JOIN semesters s ON s.id=t.semester_id
    LEFT JOIN academic_years ay ON ay.id=t.academic_year_id
    LEFT JOIN users u ON u.id=cs.teacher_user_id"""


def student_forms(student_user_id):
    """All non-draft, non-deleted forms the student is eligible to answer.

    Eligibility is resolved through ``form_sessions`` so a subject-scoped form
    reaches every student group that studies the subject.  ``GROUP BY f.id``
    guarantees a single row per form even when a student matches several of the
    covered sessions.
    """
    return db.query(_STUDENT_FORM_SELECT + """
        WHERE f.is_deleted=0 AND f.status<>'draft'
        GROUP BY f.id
        ORDER BY (f.status='open') DESC, period_no, f.subject_code""",
        (student_user_id, student_user_id, student_user_id))


def student_form(student_user_id, form_id):
    return db.query(_STUDENT_FORM_SELECT + """
        WHERE f.id=? AND f.is_deleted=0 AND f.status<>'draft'
        GROUP BY f.id""",
        (student_user_id, student_user_id, student_user_id, form_id), one=True)


def submit_feedback(student_user_id, form_id, form_payload):
    """Validate and record a submission. Returns (ok, message).

    Enforces: form open, student eligible, no duplicate, required questions and
    option ownership. Answers are stored against the (anonymous) submission.
    """
    form = student_form(student_user_id, form_id)
    if not form:
        return False, 'That feedback form is not available to you.'
    if form['status'] != 'open':
        return False, 'This feedback form is closed and no longer accepts responses.'
    if deadline_passed(form):
        close_expired_forms(force=True)
        return False, 'The deadline for this feedback form has passed. It is now closed.'
    if form['submission_id']:
        return False, 'You have already submitted feedback for this form.'
    if not student_eligible_for_form(student_user_id, form_id):
        return False, 'You are not eligible for this feedback form.'

    questions = form_questions(form_id)
    prepared = []
    for q in questions:
        key = f'q_{q["id"]}'
        if q['qtype'] == 'text':
            text = (form_payload.get(key) or '').strip()[:2000]
            if q['is_required'] and not text:
                return False, f'Please answer: {q["text"]}'
            prepared.append((q['id'], None, text or None))
        else:
            raw = form_payload.get(key)
            option_ids = {str(o['id']) for o in q['options']}
            if q['qtype'] == 'multiple_choice':
                chosen = form_payload.getlist(key) if hasattr(form_payload, 'getlist') else \
                    ([raw] if raw else [])
                chosen = [c for c in chosen if str(c) in option_ids]
                if q['is_required'] and not chosen:
                    return False, f'Please answer: {q["text"]}'
                for c in chosen:
                    prepared.append((q['id'], int(c), None))
            else:
                if raw and str(raw) in option_ids:
                    prepared.append((q['id'], int(raw), None))
                elif q['is_required']:
                    return False, f'Please answer: {q["text"]}'

    # Bind the submission to the student's OWN covered session, so a form that
    # spans several timetables still keeps each group's responses attributable
    # to the right teaching session (for per-session, anonymous statistics).
    covered = student_form_session(student_user_id, form_id)
    submission_session = covered['session_id'] if covered else form['session_id']

    conn = db.get_db()
    try:
        now = datetime.utcnow().isoformat(timespec='seconds')
        sub_id = db.insert_id(
            'INSERT INTO submissions(form_id, session_id, student_user_id, submitted_at) '
            'VALUES(?,?,?,?)', (form_id, submission_session, student_user_id, now))
        conn.executemany(
            'INSERT INTO answers(submission_id, question_id, option_id, text_value) '
            'VALUES(?,?,?,?)', [(sub_id, qid, oid, txt) for (qid, oid, txt) in prepared])
        conn.commit()
    except Exception:
        conn.rollback()
        return False, 'You have already submitted feedback for this form.'
    audit('feedback.submit', 'feedback_forms', form_id,
          'Anonymous response recorded', actor=None)
    # Never let auto-publishing break a student's submission.
    try:
        maybe_auto_publish(form_id)
    except Exception:
        pass
    return True, 'Thank you! Your feedback has been recorded anonymously.'


# --------------------------------------------------------------------------- #
# Anonymous result statistics (teacher facing)
# --------------------------------------------------------------------------- #
def _scope_clause(session_ids):
    """SQL fragment restricting an anonymous_answers query to given sessions."""
    ids = [int(s) for s in (session_ids or [])]
    if not ids:
        return '', ()
    return ' AND session_id IN (%s)' % ','.join('?' * len(ids)), tuple(ids)


def question_stats(form_id, session_ids=None):
    """Per-question statistics computed from the anonymous answers view.

    ``session_ids`` (optional) limits the aggregation to specific teaching
    sessions — used so a teacher only ever sees their own sessions' responses,
    never another teacher's, while the answers themselves stay anonymous.
    """
    clause, args = _scope_clause(session_ids)
    result = []
    for q in form_questions(form_id):
        entry = {'id': q['id'], 'text': q['text'], 'qtype': q['qtype'],
                 'required': q['is_required'], 'options': [], 'suggestions': [],
                 'valid': 0}
        if q['qtype'] == 'text':
            rows = db.query("""SELECT text_value FROM anonymous_answers
                WHERE question_id=? AND text_value IS NOT NULL AND TRIM(text_value)<>''
                """ + clause + ' ORDER BY answer_id', (q['id'],) + args)
            entry['suggestions'] = [r['text_value'] for r in rows]
            entry['valid'] = len(rows)
            result.append(entry)
            continue
        counts = {o['id']: 0 for o in q['options']}
        for row in db.query("""SELECT option_id, COUNT(*) c FROM anonymous_answers
                WHERE question_id=? AND option_id IS NOT NULL""" + clause +
                ' GROUP BY option_id', (q['id'],) + args):
            if row['option_id'] in counts:
                counts[row['option_id']] = row['c']
        valid = sum(counts.values())
        for o in q['options']:
            c = counts[o['id']]
            entry['options'].append({
                'label': o['label'], 'count': c,
                'value': o.get('value'),
                'pct': round(c * 100.0 / valid, 1) if valid else 0.0})
        entry['valid'] = valid
        result.append(entry)
    return result


def response_count(form_id, session_ids=None):
    clause, args = _scope_clause(session_ids)
    return db.query('SELECT COUNT(*) c FROM submissions WHERE form_id=?' + clause,
                    (form_id,) + args, one=True)['c']


def form_has_submissions(form_id):
    return db.query('SELECT COUNT(*) c FROM submissions WHERE form_id=?',
                    (form_id,), one=True)['c'] > 0


def suggestions_paged(form_id, page=1, per_page=10, session_ids=None):
    """Anonymous written answers for a form, paginated."""
    page = max(1, int(page))
    per_page = max(1, min(100, int(per_page)))
    clause, args = _scope_clause(session_ids)
    total = db.query("""SELECT COUNT(*) c FROM anonymous_answers
        WHERE form_id=? AND text_value IS NOT NULL AND TRIM(text_value)<>''""" + clause,
        (form_id,) + args, one=True)['c']
    rows = db.query("""SELECT text_value, submitted_at FROM anonymous_answers
        WHERE form_id=? AND text_value IS NOT NULL AND TRIM(text_value)<>''""" + clause +
        ' ORDER BY answer_id LIMIT ? OFFSET ?',
        (form_id,) + args + (per_page, (page - 1) * per_page))
    pages = max(1, (total + per_page - 1) // per_page)
    return {'comments': [r['text_value'] for r in rows], 'total': total,
            'page': page, 'pages': pages, 'per_page': per_page}


# --------------------------------------------------------------------------- #
# Publication
# --------------------------------------------------------------------------- #
def is_published(form_id, session_id):
    row = db.query('SELECT is_published FROM result_publications WHERE form_id=? AND session_id=?',
                   (form_id, session_id), one=True)
    return bool(row and row['is_published'])


def publish_results(form_id, admin_id):
    form = get_form(form_id)
    if not form or not form['session_id']:
        return False
    now = datetime.utcnow().isoformat(timespec='seconds')
    db.execute("""INSERT INTO result_publications(form_id, session_id, is_published,
            published_by, published_at) VALUES(?,?,1,?,?)
        ON CONFLICT(form_id, session_id) DO UPDATE SET
            is_published=1, published_by=excluded.published_by,
            published_at=excluded.published_at, unpublished_by=NULL, unpublished_at=NULL""",
        (form_id, form['session_id'], admin_id, now))
    audit('results.publish', 'feedback_forms', form_id, 'Feedback results published')
    return True


def auto_publish_threshold_pct():
    """Response percentage at which results publish automatically.

    Default 84. Override with the AUTO_PUBLISH_THRESHOLD_PCT environment
    variable; set it to 0 to switch auto-publishing off.
    """
    try:
        return float(os.environ.get('AUTO_PUBLISH_THRESHOLD_PCT', '84'))
    except (TypeError, ValueError):
        return 84.0


def auto_publish_required(eligible, pct=None):
    """Responses needed before auto-publishing (84% of 72 -> 60)."""
    pct = auto_publish_threshold_pct() if pct is None else pct
    if pct <= 0 or not eligible:
        return None
    return max(1, int(eligible * pct // 100))


def maybe_auto_publish(form_id):
    """Publish a form's results to the teacher once enough students responded.

    Runs after every successful submission. Does nothing if the form is a
    draft/deleted, already published, or was deliberately unpublished by an
    HOD/admin (their decision is never overridden). Returns True only when
    this call published the results.
    """
    form = get_form(form_id)
    if not form or form['status'] == 'draft' \
            or not form['session_id']:
        return False
    row = db.query(
        'SELECT is_published, unpublished_at FROM result_publications '
        'WHERE form_id=? AND session_id=?', (form_id, form['session_id']), one=True)
    if row and (row['is_published'] or row['unpublished_at']):
        return False
    eligible = eligible_count_for_form(form_id)
    needed = auto_publish_required(eligible)
    if needed is None or response_count(form_id) < needed:
        return False
    now = datetime.utcnow().isoformat(timespec='seconds')
    db.execute("""INSERT INTO result_publications(form_id, session_id, is_published,
            published_by, published_at) VALUES(?,?,1,NULL,?)
        ON CONFLICT(form_id, session_id) DO UPDATE SET
            is_published=1, published_by=NULL, published_at=excluded.published_at,
            unpublished_by=NULL, unpublished_at=NULL""",
        (form_id, form['session_id'], now))
    try:
        db.execute(
            'INSERT INTO audit_log(actor_user_id, actor_label, action, entity, '
            'entity_id, detail) VALUES(?,?,?,?,?,?)',
            (None, 'system', 'results.auto_publish', 'feedback_forms', form_id,
             'Auto-published: responses reached %s%% of eligible students'
             % ('%g' % auto_publish_threshold_pct())))
    except Exception:
        pass
    return True


def unpublish_results(form_id, admin_id):
    form = get_form(form_id)
    if not form or not form['session_id']:
        return False
    now = datetime.utcnow().isoformat(timespec='seconds')
    db.execute("""UPDATE result_publications SET is_published=0, unpublished_by=?,
            unpublished_at=? WHERE form_id=? AND session_id=?""",
        (admin_id, now, form_id, form['session_id']))
    audit('results.unpublish', 'feedback_forms', form_id, 'Feedback results unpublished')
    return True


def publication_info(form_id, session_id):
    return db.query("""SELECT rp.*, u.name AS published_by_name
        FROM result_publications rp LEFT JOIN users u ON u.id=rp.published_by
        WHERE rp.form_id=? AND rp.session_id=?""", (form_id, session_id), one=True)


# --------------------------------------------------------------------------- #
# Teacher side
# --------------------------------------------------------------------------- #
def teacher_subjects(teacher_user_id):
    return db.query("""SELECT cs.subject_code, cs.subject_name,
            COUNT(DISTINCT cs.id) AS sessions
        FROM class_sessions cs WHERE cs.teacher_user_id=? AND cs.is_deleted=0
        GROUP BY cs.subject_code, cs.subject_name ORDER BY cs.subject_name""",
        (teacher_user_id,))


def teacher_sessions(teacher_user_id, subject_code=None):
    sql = SESSION_SELECT + """ WHERE cs.teacher_user_id=? AND cs.is_deleted=0"""
    args = [teacher_user_id]
    if subject_code:
        sql += ' AND cs.subject_code=?'
        args.append(subject_code)
    return db.query(sql + ' ORDER BY cs.subject_code, b.code, cs.period_no', tuple(args))


def teacher_form_session_ids(teacher_user_id, form_id):
    """The covered sessions of a form that belong to this teacher."""
    rows = db.query("""SELECT cs.id FROM form_sessions fs
        JOIN class_sessions cs ON cs.id=fs.session_id
        WHERE fs.form_id=? AND cs.teacher_user_id=?""", (form_id, teacher_user_id))
    return [r['id'] for r in rows]


def teacher_published_forms(teacher_user_id, subject_code=None):
    """Only published forms that cover at least one of THIS teacher's sessions.

    Resolved through ``form_sessions`` so subject-scoped forms published to the
    whole subject reach every contributing teacher — but each teacher's view is
    later restricted to their own sessions (see ``teacher_form_session_ids``).
    """
    sql = """SELECT f.*, MIN(cs.period_no) AS period_no,
                MIN(t.code) AS timetable, MIN(b.code) AS branch,
                MIN(s.number) AS semester, MIN(ay.name) AS academic_year,
                MAX(rp.published_at) AS results_published_at,
                MAX(rp.is_published) AS is_published
            FROM feedback_forms f
            JOIN form_sessions fs ON fs.form_id=f.id
            JOIN class_sessions cs ON cs.id=fs.session_id
            JOIN timetables t ON t.id=cs.timetable_id
            JOIN branches b ON b.id=t.branch_id
            JOIN semesters s ON s.id=t.semester_id
            LEFT JOIN academic_years ay ON ay.id=t.academic_year_id
            JOIN result_publications rp ON rp.form_id=f.id AND rp.is_published=1
            WHERE cs.teacher_user_id=? AND f.is_deleted=0 AND f.status<>'draft'"""
    args = [teacher_user_id]
    if subject_code:
        sql += ' AND cs.subject_code=?'
        args.append(subject_code)
    return db.query(sql + ' GROUP BY f.id '
                    'ORDER BY f.subject_name, MIN(b.code), MIN(cs.period_no)',
                    tuple(args))


def teacher_unpublished_forms(teacher_user_id, subject_code=None):
    """Forms (open/closed/archived) covering this teacher's sessions whose
    results are NOT published yet.

    Used only so the teacher can see the form name and how many students are
    eligible / have responded.  Scores and marks are never exposed for these;
    ``teacher_published_forms`` remains the gate for the results pages.
    """
    sql = """SELECT f.*, MIN(cs.period_no) AS period_no,
                MIN(t.code) AS timetable, MIN(b.code) AS branch,
                MIN(s.number) AS semester, MIN(ay.name) AS academic_year
            FROM feedback_forms f
            JOIN form_sessions fs ON fs.form_id=f.id
            JOIN class_sessions cs ON cs.id=fs.session_id
            JOIN timetables t ON t.id=cs.timetable_id
            JOIN branches b ON b.id=t.branch_id
            JOIN semesters s ON s.id=t.semester_id
            LEFT JOIN academic_years ay ON ay.id=t.academic_year_id
            WHERE cs.teacher_user_id=? AND f.is_deleted=0 AND f.status<>'draft'
              AND NOT EXISTS (SELECT 1 FROM result_publications rp
                              WHERE rp.form_id=f.id AND rp.is_published=1)"""
    args = [teacher_user_id]
    if subject_code:
        sql += ' AND cs.subject_code=?'
        args.append(subject_code)
    return db.query(sql + ' GROUP BY f.id '
                    'ORDER BY f.subject_name, MIN(b.code), MIN(cs.period_no)',
                    tuple(args))


# --------------------------------------------------------------------------- #
# Admin submission tracking
# --------------------------------------------------------------------------- #
def tracking_rows(filters=None):
    """One row per form matching the filters with completion counts.

    Eligibility is counted over every session the form covers, so the numbers
    for a subject-scoped (all-timetables) form match its real eligible
    population exactly.
    """
    filters = filters or {}
    rows = list_forms(filters)
    fids = [f['id'] for f in rows]
    elig_map = eligible_counts_for_forms(fids)      # 1 query for all forms
    resp_map = response_counts_for_forms(fids)      # 1 query for all forms
    out = []
    for f in rows:
        eligible = elig_map.get(f['id'], 0)
        submitted = resp_map.get(f['id'], 0)
        out.append({
            'form': f,
            'eligible': eligible,
            'submitted': submitted,
            'not_submitted': max(0, eligible - submitted),
            'pct': round(submitted * 100.0 / eligible, 1) if eligible else 0.0,
        })
    return out


def submitted_rolls(form_id):
    return db.query("""SELECT u.roll, sb.submitted_at FROM submissions sb
        JOIN users u ON u.id=sb.student_user_id
        WHERE sb.form_id=? ORDER BY u.roll""", (form_id,))


def not_submitted_rolls(form_id):
    return db.query("""SELECT DISTINCT u.roll FROM form_sessions fs
        JOIN session_students ss ON ss.session_id=fs.session_id
        JOIN users u ON u.id=ss.student_user_id
        WHERE fs.form_id=? AND u.id NOT IN
            (SELECT student_user_id FROM submissions WHERE form_id=?)
        ORDER BY u.roll""", (form_id, form_id))


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #
def tracking_csv(filters=None):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['Form ID', 'Title', 'Status', 'Academic Year', 'Semester', 'Branch',
                'Timetable', 'Subject Code', 'Subject', 'Teacher', 'Period',
                'Eligible', 'Submitted', 'Not Submitted', 'Completion %'])
    for r in tracking_rows(filters):
        f = r['form']
        w.writerow([f['id'], f['title'], f['status'], f['academic_year'], f['semester'],
                    f['branch'], f['timetable'], f['subject_code'], f['subject_name'],
                    f['teacher_name'], f['period_no'], r['eligible'], r['submitted'],
                    r['not_submitted'], r['pct']])
    return out.getvalue()


def rolls_csv(form_id, which='submitted'):
    out = io.StringIO()
    w = csv.writer(out)
    if which == 'not_submitted':
        w.writerow(['Roll Number', 'Status'])
        for r in not_submitted_rolls(form_id):
            w.writerow([r['roll'], 'Not Submitted'])
    else:
        w.writerow(['Roll Number', 'Submitted At'])
        for r in submitted_rolls(form_id):
            w.writerow([r['roll'], r['submitted_at']])
    return out.getvalue()


def dashboard_stats():
    # All ten counters in a single round trip (was ten separate queries).
    r = db.query("""SELECT
        (SELECT COUNT(*) FROM users WHERE role='student') AS students,
        (SELECT COUNT(*) FROM users WHERE role='teacher') AS teachers,
        (SELECT COUNT(*) FROM branches) AS branches,
        (SELECT COUNT(*) FROM timetables WHERE is_deleted=0) AS timetables,
        (SELECT COUNT(*) FROM class_sessions WHERE is_deleted=0) AS sessions,
        (SELECT COUNT(*) FROM feedback_forms WHERE is_deleted=0) AS forms,
        (SELECT COUNT(*) FROM feedback_forms WHERE is_deleted=0 AND status='open') AS forms_open,
        (SELECT COUNT(*) FROM feedback_forms WHERE is_deleted=0 AND status='draft') AS forms_draft,
        (SELECT COUNT(*) FROM submissions) AS submissions,
        (SELECT COUNT(*) FROM result_publications WHERE is_published=1) AS published""",
        one=True)
    return {k: r[k] for k in ('students', 'teachers', 'branches', 'timetables',
                              'sessions', 'forms', 'forms_open', 'forms_draft',
                              'submissions', 'published')}


# --------------------------------------------------------------------------- #
# Bulk form generation (create one form per matching teaching session)
# --------------------------------------------------------------------------- #
def default_question_template():
    """The standard question set used when generating forms in bulk."""
    return [
        {'text': 'How well does the teacher explain the subject?', 'qtype': 'rating',
         'is_required': True,
         'options': [{'label': 'Excellent', 'value': 1}, {'label': 'Good', 'value': 2},
                     {'label': 'Bad', 'value': 3}, {'label': 'Very poor', 'value': 4}]},
        {'text': 'Suggestions to improve the teaching', 'qtype': 'text',
         'is_required': False, 'options': []},
    ]


# --------------------------------------------------------------------------- #
# HOD question sets: private to the HOD who created them
# --------------------------------------------------------------------------- #
SCALE_PRESETS = {
    'five_point': {
        'label': 'Excellent · Very Good · Good · Average · Poor',
        'options': [{'label': 'Excellent', 'value': 5}, {'label': 'Very Good', 'value': 4},
                    {'label': 'Good', 'value': 3}, {'label': 'Average', 'value': 2},
                    {'label': 'Poor', 'value': 1}]},
    'four_point': {
        'label': 'Excellent · Good · Bad · Very poor',
        'options': [{'label': 'Excellent', 'value': 4}, {'label': 'Good', 'value': 3},
                    {'label': 'Bad', 'value': 2}, {'label': 'Very poor', 'value': 1}]},
}
QSET_QTYPES = ('rating', 'text')
COMMON_SET_NAME = 'Common questions'


def _clean_set_questions(questions):
    out = []
    for q in (questions or [])[:100]:
        if not isinstance(q, dict):
            continue
        qtype = q.get('qtype') if q.get('qtype') in QSET_QTYPES else 'rating'
        options = []
        if qtype == 'rating':
            for i, o in enumerate((q.get('options') or [])[:10]):
                label = str((o or {}).get('label') or '').strip()[:200]
                if not label:
                    continue
                try:
                    value = int(o.get('value'))
                except (TypeError, ValueError):
                    value = None
                options.append({'label': label, 'value': value})
            if len(options) < 2:
                options = [dict(o) for o in SCALE_PRESETS['five_point']['options']]
        out.append({'text': str(q.get('text') or '').strip()[:1000], 'qtype': qtype,
                    'is_required': bool(q.get('is_required')), 'options': options})
    return out


def _set_items(set_id):
    import json
    return [{'text': r['text'], 'qtype': r['qtype'], 'is_required': bool(r['is_required']),
             'options': json.loads(r['options_json'] or '[]')}
            for r in db.query(
                'SELECT text, qtype, is_required, options_json FROM question_set_items '
                'WHERE set_id=? ORDER BY position, id', (set_id,))]


# Shared sets (from FEEDBACK_QUESTIONS.docx), common to all HODs.  Wording as supplied.
_STARTER_RATING = {
    'Phase 1 Questions': [
        'How well did the teacher prepare for the classes?',
        'How well is the teacher able to communicate?',
        'How is the teachers approach to teaching(way of teaching)?',
        'How is the teachers regularity and promptness to class',
        'How is the syllabus coverage as per the lesson plan',
        'How is the overall teaching-learning process?',
    ],
    'Phase 2 Questions': [
        'How well did the teacher prepare for the classes?',
        'How well is the teacher able to communicate?',
        'How is the teachers approach to teaching(way of teaching)?',
        'The teacher illustrated the concepts through examples and applications.',
        'How was the discussion of student\u2019s performance in assignments or examinations?',
        'Fairness of internal evaluation process by teacher.',
        'How is the overall teaching-learning process?',
    ],
    'Lab Questions (Phase 1 & 2)': [
        "How is the teacher's approach in demonstrating the experiments?",
        "How is the teacher's regularity and promptness to lab sessions",
        'How is the overall teaching-learning process in lab?',
    ],
    'CRT Questions (Phase 1 & 2)': [
        'Clarity and effectiveness of the trainer\u2019s classroom teaching of aptitude concepts.',
        'Trainer\u2019s interaction and engagement with students during the sessions',
        'Explanation of logical approaches, formulas, and shortcuts for solving aptitude problems.',
        'Practice exercises and problem-solving activities conducted during the class',
        'Usefulness of the aptitude study material provided in the CRT program for improving problem-solving skills',
        'Support provided by the CRT aptitude training in improving placement readiness for aptitude tests',
    ],
}
_STARTER_WRITTEN = {
    'CRT Questions (Phase 1 & 2)': [
        'Please provide your comments about the trainer and suggestions to improvise'],
}


SHARED_KEYS = {'Phase 1 Questions': 'shared-phase1', 'Phase 2 Questions': 'shared-phase2',
               'Lab Questions (Phase 1 & 2)': 'shared-lab',
               'CRT Questions (Phase 1 & 2)': 'shared-crt'}


def shared_question_sets():
    """Sets that are common to ALL HODs (read-only for them).  A HOD can copy one
    into their own sets to customise it."""
    out = []
    for name, texts in _STARTER_RATING.items():
        qs = [{'text': t, 'qtype': 'rating', 'is_required': True,
               'options': [dict(o) for o in SCALE_PRESETS['five_point']['options']]}
              for t in texts]
        qs += [{'text': t, 'qtype': 'text', 'is_required': False, 'options': []}
               for t in _STARTER_WRITTEN.get(name, [])]
        out.append({'id': SHARED_KEYS[name], 'name': name, 'is_common': False,
                    'shared': True, 'questions': qs})
    return out


def copy_shared_set(owner_id, key):
    """Copy a shared set into the HOD's own sets.  Returns (set_id, error)."""
    src = [s for s in shared_question_sets() if s['id'] == key]
    if not src:
        return None, 'Shared set not found.'
    base = src[0]['name'] + ' (my copy)'
    name, n = base, 2
    while db.query('SELECT 1 FROM question_sets WHERE owner_user_id=? AND is_deleted=0 '
                   'AND LOWER(name)=?', (owner_id, name.lower()), one=True):
        name, n = f'{base} {n}', n + 1
    sid, err = create_question_set(owner_id, name)
    if sid:
        save_set_questions(owner_id, sid, src[0]['questions'])
    return sid, err


def ensure_common_set(owner_id):
    row = db.query('SELECT id FROM question_sets WHERE owner_user_id=? AND is_common=1 '
                   'AND is_deleted=0', (owner_id,), one=True)
    if row:
        return row['id']
    new_id = db.insert_id(
        'INSERT INTO question_sets(owner_user_id, name, is_common) VALUES(?,?,1)',
        (owner_id, COMMON_SET_NAME))
    db.get_db().commit()
    return new_id


def question_sets_for(owner_id):
    """The HOD's own common set plus their named sets, each with its questions."""
    ensure_common_set(owner_id)
    rows = db.query('SELECT id, name, is_common, base_key FROM question_sets '
                    'WHERE owner_user_id=? AND is_deleted=0 '
                    'ORDER BY is_common DESC, LOWER(name), id', (owner_id,))
    return [{'id': r['id'], 'name': r['name'], 'is_common': bool(r['is_common']),
             'base_key': r['base_key'], 'questions': _set_items(r['id'])} for r in rows]


def _override_row(owner_id, key):
    return db.query('SELECT * FROM question_sets WHERE owner_user_id=? AND base_key=? '
                    'AND is_deleted=0', (owner_id, key), one=True)


def customise_shared_set(owner_id, key):
    """Give this HOD their own editable version of a shared (default) set.  The default
    questions stay untouched for everyone else.  Returns (set_id, error)."""
    src = [s for s in shared_question_sets() if s['id'] == key]
    if not src:
        return None, 'Shared set not found.'
    row = _override_row(owner_id, key)
    if row:
        return row['id'], None
    sid = db.insert_id('INSERT INTO question_sets(owner_user_id, name, base_key) VALUES(?,?,?)',
                       (owner_id, src[0]['name'], key))
    db.get_db().commit()
    save_set_questions(owner_id, sid, src[0]['questions'])
    return sid, None


def reset_shared_set(owner_id, key):
    """Throw away this HOD's edits and go back to the default questions."""
    row = _override_row(owner_id, key)
    if not row:
        return False
    db.execute('UPDATE question_sets SET is_deleted=1, updated_at=CURRENT_TIMESTAMP WHERE id=?',
               (row['id'],))
    return True


def effective_shared_sets(owner_id):
    """The phase sets as this HOD sees them: their edited version where they have one,
    otherwise the default."""
    out = []
    for s in shared_question_sets():
        row = _override_row(owner_id, s['id'])
        if row:
            out.append({'id': row['id'], 'name': row['name'], 'is_common': False,
                        'shared': False, 'customised': True, 'base_key': s['id'],
                        'questions': _set_items(row['id'])})
        else:
            out.append(s)
    return out


def get_owned_set(owner_id, set_id):
    return db.query('SELECT * FROM question_sets WHERE id=? AND owner_user_id=? '
                    'AND is_deleted=0', (set_id, owner_id), one=True)


def create_question_set(owner_id, name):
    """Returns (set_id, error)."""
    name = ' '.join((name or '').split())[:80]
    if not name:
        return None, 'Give the set a name.'
    if db.query('SELECT 1 FROM question_sets WHERE owner_user_id=? AND is_deleted=0 '
                'AND LOWER(name)=?', (owner_id, name.lower()), one=True):
        return None, 'You already have a set with that name.'
    sid = db.insert_id('INSERT INTO question_sets(owner_user_id, name) VALUES(?,?)',
                       (owner_id, name))
    db.get_db().commit()
    return sid, None


def rename_question_set(owner_id, set_id, name):
    name = ' '.join((name or '').split())[:80]
    row = get_owned_set(owner_id, set_id)
    if not row or row['is_common'] or row['base_key']:
        return 'Set not found.'
    if not name:
        return 'Give the set a name.'
    if db.query('SELECT 1 FROM question_sets WHERE owner_user_id=? AND is_deleted=0 '
                'AND LOWER(name)=? AND id<>?', (owner_id, name.lower(), set_id), one=True):
        return 'You already have a set with that name.'
    db.execute('UPDATE question_sets SET name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
               (name, set_id))
    return None


def delete_question_set(owner_id, set_id):
    row = get_owned_set(owner_id, set_id)
    if not row or row['is_common'] or row['base_key']:
        return False          # phase sets are reset to default instead of deleted
    db.execute('UPDATE question_sets SET is_deleted=1, updated_at=CURRENT_TIMESTAMP '
               'WHERE id=?', (set_id,))
    return True


def save_set_questions(owner_id, set_id, questions):
    """Replace a set's questions (called by the auto-save).  Returns False if not owned."""
    import json
    if not get_owned_set(owner_id, set_id):
        return False
    conn = db.get_db()
    conn.execute('DELETE FROM question_set_items WHERE set_id=?', (set_id,))
    for pos, q in enumerate(_clean_set_questions(questions)):
        conn.execute(
            'INSERT INTO question_set_items(set_id, position, text, qtype, is_required, '
            'options_json) VALUES(?,?,?,?,?,?)',
            (set_id, pos, q['text'], q['qtype'], 1 if q['is_required'] else 0,
             json.dumps(q['options'])))
    conn.execute('UPDATE question_sets SET updated_at=CURRENT_TIMESTAMP WHERE id=?', (set_id,))
    conn.commit()
    return True


def questions_not_from_sets(owner_id, questions):
    """Questions that do not match any question in the HOD's own sets (by text,
    type and option labels).  Used to enforce 'form questions come from sets'."""
    allowed = set()
    for st in question_sets_for(owner_id) + shared_question_sets():
        for q in st['questions']:
            allowed.add((q['text'].strip(), q['qtype'],
                         tuple(o['label'].strip() for o in q['options'])))
    bad = []
    for q in questions or []:
        if q.get('custom'):          # extra question / comment box added for this form only
            if not (q.get('text') or '').strip():
                bad.append('(blank extra question)')
            elif q.get('qtype', 'rating') not in QSET_QTYPES:
                bad.append(q.get('text'))
            elif q.get('qtype', 'rating') == 'rating' and len(
                    [o for o in (q.get('options') or []) if (o.get('label') or '').strip()]) < 2:
                bad.append(q.get('text'))
            continue
        key = ((q.get('text') or '').strip(), q.get('qtype', 'rating'),
               tuple((o.get('label') or '').strip() for o in (q.get('options') or [])))
        if key not in allowed:
            bad.append(q.get('text') or '(blank question)')
    return bad


def sessions_for_bulk(filters=None):
    """Teaching sessions matching the given filters (all filters optional)."""
    filters = filters or {}
    where, args = ['cs.is_deleted=0'], []
    for key, col in (('academic_year_id', 't.academic_year_id'), ('semester_id', 't.semester_id'),
                     ('branch_id', 't.branch_id'), ('timetable_id', 'cs.timetable_id'),
                     ('teacher_id', 'cs.teacher_user_id'), ('subject_code', 'cs.subject_code')):
        if filters.get(key):
            where.append(f'{col}=?')
            args.append(filters[key])
    # Several timetables may be ticked at once (multi-select on the HOD form page).
    tt_ids = [int(x) for x in (filters.get('timetable_ids') or []) if x]
    if tt_ids and not filters.get('timetable_id'):
        where.append('cs.timetable_id IN (' + ','.join('?' * len(tt_ids)) + ')')
        args.extend(tt_ids)
    return db.query(SESSION_SELECT + ' WHERE ' + ' AND '.join(where) +
                    ' ORDER BY b.code, t.code, cs.period_no, cs.subject_code', tuple(args))


def existing_form_session_ids():
    return {r['session_id'] for r in db.query(
        'SELECT DISTINCT session_id FROM feedback_forms '
        'WHERE is_deleted=0 AND session_id IS NOT NULL')}


def existing_form_session_titles():
    """{(session_id, normalised title)} of every live form.

    Bulk generation uses this instead of "session has any form" so that a new
    feedback round (a different title) can be generated for sessions that
    already have earlier rounds, while an accidental re-run with the SAME title
    stays idempotent.
    """
    return {(r['session_id'], (r['title'] or '').strip().lower()[:200]) for r in db.query(
        'SELECT session_id, title FROM feedback_forms '
        'WHERE is_deleted=0 AND session_id IS NOT NULL')}


def format_bulk_title(pattern, session_row):
    p = pattern or '{subject} — {teacher} (Period {period})'
    repl = {
        '{subject}': session_row['subject_name'] or '',
        '{subject_code}': session_row['subject_code'] or '',
        '{teacher}': session_row['teacher_name'] or 'Unassigned',
        '{period}': str(session_row['period_no'] or ''),
        '{timetable}': session_row['timetable'] or '',
        '{branch}': session_row['branch'] or '',
        '{semester}': str(session_row['semester'] or ''),
    }
    for k, v in repl.items():
        p = p.replace(k, v)
    return p.strip()[:200]


def bulk_preview(filters, title_pattern=None):
    sessions = sessions_for_bulk(filters)
    existing = existing_form_session_titles()
    to_create = [s for s in sessions
                 if (s['id'], format_bulk_title(title_pattern, s).lower()) not in existing]
    sample = [{
        'label': f"Period {s['period_no']}: {s['subject_name']} ({s['subject_code']}) – "
                 f"{s['teacher_name'] or 'Unassigned'}",
        'timetable': f"{s['branch']} · {s['timetable']}",
    } for s in to_create[:10]]
    return {'matched': len(sessions), 'existing': len(sessions) - len(to_create),
            'to_create': len(to_create), 'sample': sample}


def bulk_create_forms(filters, questions, title_pattern, status, admin_id, deadline_at=None):
    """Create one draft (or open) form per matching session for this round.

    A session is skipped only if it already has a form with the SAME title, so
    giving the pattern a round name (e.g. "… — Round 2") adds a new round
    without touching earlier forms.
    """
    sessions = sessions_for_bulk(filters)
    existing = existing_form_session_titles()
    created, skipped, opened, errors, created_ids = 0, 0, 0, [], []
    for s in sessions:
        if (s['id'], format_bulk_title(title_pattern, s).lower()) in existing:
            skipped += 1
            continue
        payload = {
            'title': format_bulk_title(title_pattern, s),
            'instructions': 'Your responses are anonymous.',
            'academic_year_id': s['year_id'], 'semester_id': s['semester_id'],
            'branch_id': s['branch_id'], 'timetable_id': s['tt_id'], 'session_id': s['id'],
            'scope': 'session', 'subject_code': s['subject_code'],
            'subject_name': s['subject_name'],
        }
        if deadline_at:
            payload['deadline_at'] = deadline_at
        fid = save_form(payload, questions, admin_id)
        created += 1
        created_ids.append(fid)
        if status == 'open':
            errs = validate_form_for_publish(fid)
            if errs:
                errors.append(f'{payload["title"]}: ' + '; '.join(errs))
            else:
                set_form_status(fid, 'open', admin_id)
                opened += 1
        existing.add((s['id'], payload['title'].lower()))
    audit('form.bulk_create', 'feedback_forms', None,
          f'{created} form(s) generated, {skipped} skipped (already existed)')
    return {'matched': len(sessions), 'created': created, 'skipped': skipped,
            'opened': opened, 'errors': errors, 'created_ids': created_ids}


# --------------------------------------------------------------------------- #
# Subject-scoped forms ("No Preference (All Timetables)")
# --------------------------------------------------------------------------- #
def subject_scope_filters(academic_year_id=None, semester_id=None, branch_id=None,
                          timetable_id=None, subject_code=None, timetable_ids=None):
    return {
        'academic_year_id': academic_year_id,
        'semester_id': semester_id,
        'branch_id': branch_id,
        'timetable_id': timetable_id,
        'subject_code': subject_code,
        'timetable_ids': list(timetable_ids or []),
    }


def subjects_for_scope(filters, require_students=False):
    """Distinct subjects taught in the selected academic configuration.

    With ``require_students=True`` only subjects that have at least one
    session with enrolled (eligible) students are returned, so subjects that
    are not linked to any students never appear in the form builder.
    """
    rows = sessions_for_bulk(filters)
    if require_students and rows:
        ids = [s['id'] for s in rows]
        with_students = set()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            ph = ','.join('?' * len(chunk))
            with_students.update(r['session_id'] for r in db.query(
                f'SELECT DISTINCT session_id FROM session_students '
                f'WHERE session_id IN ({ph})', tuple(chunk)))
        rows = [s for s in rows if s['id'] in with_students]
    seen = {}
    for s in rows:
        seen.setdefault(s['subject_code'], s['subject_name'])
    return [{'code': code, 'name': name} for code, name in sorted(seen.items())]


def subject_scope_preview(filters):
    """Preview a subject-scoped form before it is created.

    Returns the number of matching sessions/timetables and — using exactly the
    same eligibility rule as publication — the distinct eligible-student count.
    Also reports whether a duplicate form already exists.
    """
    filters = {k: v for k, v in (filters or {}).items() if v not in (None, '')}
    sessions = sessions_for_bulk(filters)
    result = {
        'filters': filters,
        'sessions': len(sessions),
        'timetables': len({s['tt_id'] for s in sessions}),
        'teachers': len({s['teacher_user_id'] for s in sessions if s['teacher_user_id']}),
        'eligible': 0,
        'subject_code': filters.get('subject_code'),
        'subject_name': None,
        'existing_form_id': None,
        'existing_form_title': None,
        'sample': [],
    }
    if sessions:
        result['subject_name'] = sessions[0]['subject_name']
        ids = [s['id'] for s in sessions]
        ph = ','.join('?' * len(ids))
        result['eligible'] = db.query(
            f'SELECT COUNT(DISTINCT student_user_id) c FROM session_students '
            f'WHERE session_id IN ({ph})', tuple(ids), one=True)['c']
        result['sample'] = [{
            'timetable': f"{s['branch']} · {s['timetable']}",
            'label': f"P{s['period_no'] or '?'}: {s['subject_code']} — "
                     f"{s['teacher_name'] or 'Unassigned'}",
        } for s in sessions[:12]]
        if filters.get('timetable_ids'):
            existing = find_duplicate_subject_form_for_sessions(
                filters.get('academic_year_id'), filters.get('semester_id'),
                filters.get('branch_id'), filters.get('subject_code'), ids)
        else:
            existing = find_duplicate_subject_form(
                filters.get('academic_year_id'), filters.get('semester_id'),
                filters.get('branch_id'), filters.get('timetable_id'),
                filters.get('subject_code'))
        if existing:
            result['existing_form_id'] = existing['id']
            result['existing_form_title'] = existing['title']
    return result


def round_info(form_ids):
    """{form_id: {round_no, rounds_total, label, created_at, title}}.

    Forms for the same teacher + subject + academic year + semester + branch +
    timetable are the feedback rounds of one class.  They are numbered by
    creation order (oldest = Round 1) so users can tell them apart; every
    round keeps its own form id, responses and results.
    """
    ids = list(dict.fromkeys(int(i) for i in form_ids if i))
    if not ids:
        return {}
    ph = ','.join('?' * len(ids))
    codes = {r['code'] for r in db.query(
        f"SELECT DISTINCT COALESCE(subject_code, snapshot_subject_code) code "
        f"FROM feedback_forms WHERE id IN ({ph})", tuple(ids)) if r['code']}
    groups = {}
    out = {}
    if codes:
        cph = ','.join('?' * len(codes))
        for r in db.query(
                f"""SELECT f.id, f.title, f.created_at, f.academic_year_id, f.semester_id,
                       f.branch_id, f.timetable_id,
                       COALESCE(f.subject_code, f.snapshot_subject_code) AS code,
                       cs.teacher_user_id AS teacher_id
                    FROM feedback_forms f LEFT JOIN class_sessions cs ON cs.id=f.session_id
                    WHERE f.is_deleted=0 AND f.status<>'draft'
                      AND COALESCE(f.subject_code, f.snapshot_subject_code) IN ({cph})""",
                tuple(codes)):
            key = (r['code'], r['academic_year_id'], r['semester_id'], r['branch_id'],
                   r['timetable_id'], r['teacher_id'])
            groups.setdefault(key, []).append(dict(r))
        for members in groups.values():
            members.sort(key=lambda m: (m['created_at'] or '', m['id']))
            for n, m in enumerate(members, 1):
                out[m['id']] = {'round_no': n, 'rounds_total': len(members),
                                'label': f'Round {n}', 'created_at': m['created_at'],
                                'title': m['title']}
    for i in ids:  # drafts / forms without a subject: still identifiable
        if i not in out:
            r = db.query('SELECT title, created_at FROM feedback_forms WHERE id=?',
                         (i,), one=True)
            out[i] = {'round_no': 1, 'rounds_total': 1, 'label': 'Round 1',
                      'created_at': r['created_at'] if r else None,
                      'title': r['title'] if r else None}
    return out


PHASE_LABELS = {'phase1': 'Phase 1', 'phase2': 'Phase 2'}


def phase_from_title(title):
    """'Phase 1' / 'phase2' (any case or spacing) -> 'phase1' / 'phase2'; else None."""
    t = ''.join((title or '').lower().split())
    return t if t in PHASE_LABELS else None


def phase_rule_errors(phase, session_ids, exclude_form_id=None):
    """Phase rules for a form that covers ``session_ids``.

    * the same phase may not exist twice for the same teacher + subject + section
      (a session is exactly that triple, so any shared session is a clash);
    * Phase 2 needs every Phase 1 form for those sessions to be closed.
    Custom-named forms (phase is None) are not restricted.
    Returns a list of error strings.
    """
    session_ids = [s for s in dict.fromkeys(session_ids or []) if s]
    if phase not in PHASE_LABELS or not session_ids:
        return []
    ph = ','.join('?' * len(session_ids))
    # GROUP_CONCAT is SQLite-only; PostgreSQL (Supabase) needs STRING_AGG.
    # Using GROUP_CONCAT there raised "function group_concat does not exist",
    # which surfaced as a 500 on /hod/forms/save whenever Phase 1/2 was chosen.
    sections_agg = ("STRING_AGG(DISTINCT t.code::text, ',')" if db.using_postgres()
                    else "GROUP_CONCAT(DISTINCT t.code)")
    base = (f"SELECT DISTINCT f.id, f.title, f.status, f.subject_code, "
            f"{sections_agg} AS sections "
            f"FROM feedback_forms f "
            f"JOIN form_sessions fs ON fs.form_id=f.id "
            f"JOIN class_sessions cs ON cs.id=fs.session_id "
            f"JOIN timetables t ON t.id=cs.timetable_id "
            f"WHERE f.is_deleted=0 AND f.phase=? AND fs.session_id IN ({ph})")
    tail = " GROUP BY f.id ORDER BY f.id"
    args = [phase, *session_ids]
    if exclude_form_id:
        base += " AND f.id<>?"
        args.append(exclude_form_id)
    errors = []
    label = PHASE_LABELS[phase]
    for r in db.query(base + tail, tuple(args)):
        tnames = [t['name'] for t in db.query(
            "SELECT DISTINCT u.name FROM form_sessions fs "
            "JOIN class_sessions cs ON cs.id=fs.session_id "
            "JOIN users u ON u.id=cs.teacher_user_id WHERE fs.form_id=? ORDER BY u.name",
            (r['id'],)) if t['name']]
        who = f', teacher: {", ".join(tnames)}' if tnames else ''
        errors.append(
            f'{label} already exists for this teacher, subject and section '
            f'({r["subject_code"] or "subject"}, section {r["sections"]}{who}) — form #{r["id"]}. '
            f'A phase can only be created once.')
    if phase == 'phase2':
        args1 = ['phase1', *session_ids]
        sql1 = base + " AND f.status IN ('draft','open')"
        if exclude_form_id:
            args1.append(exclude_form_id)
        for r in db.query(sql1 + tail, tuple(args1)):
            errors.append(
                f'Phase 2 cannot be created or published until Phase 1 is closed '
                f'(form #{r["id"]}, {r["subject_code"] or "subject"}, section '
                f'{r["sections"]}, currently {r["status"]}).')
    return errors


def find_duplicate_subject_form(academic_year_id, semester_id, branch_id,
                                timetable_id, subject_code, title=None):
    """An existing, non-deleted subject-scoped form that is a true duplicate.

    Used to stop repeated clicks / concurrent requests from creating the same
    form twice.  A college runs several feedback rounds per semester for the
    same subject and timetable, so the SAME configuration is only a duplicate
    when the form TITLE (the round name) is also the same.  Pass ``title`` from
    every create/save call; ``None`` keeps the old "any form" meaning and is
    only meant for informational previews.

    Session-scoped (Normal-mode) forms are intentionally excluded: the
    application has always allowed several forms per period.
    """
    if not subject_code:
        return None
    sql = """SELECT * FROM feedback_forms
        WHERE is_deleted=0 AND scope='subject' AND subject_code=?
          AND COALESCE(academic_year_id,-1)=COALESCE(?,-1)
          AND COALESCE(semester_id,-1)=COALESCE(?,-1)
          AND COALESCE(branch_id,-1)=COALESCE(?,-1)
          AND COALESCE(timetable_id,-1)=COALESCE(?,-1)"""
    args = [subject_code, academic_year_id, semester_id, branch_id, timetable_id]
    if title is not None:
        sql += " AND LOWER(TRIM(title))=?"
        args.append((title or '').strip().lower()[:200])
    return db.query(sql + " ORDER BY id LIMIT 1", tuple(args), one=True)


def find_duplicate_subject_form_for_sessions(academic_year_id, semester_id, branch_id,
                                             subject_code, session_ids, title=None):
    """Duplicate check for a subject form that covers SEVERAL (but not all) timetables.

    Such a form stores no single timetable_id, so it is a duplicate only when an
    existing form has the same subject/title and exactly the same set of sessions.
    """
    if not subject_code or not session_ids:
        return None
    sql = """SELECT * FROM feedback_forms
        WHERE is_deleted=0 AND scope='subject' AND subject_code=?
          AND COALESCE(academic_year_id,-1)=COALESCE(?,-1)
          AND COALESCE(semester_id,-1)=COALESCE(?,-1)
          AND COALESCE(branch_id,-1)=COALESCE(?,-1)"""
    args = [subject_code, academic_year_id, semester_id, branch_id]
    if title is not None:
        sql += " AND LOWER(TRIM(title))=?"
        args.append((title or '').strip().lower()[:200])
    wanted = set(session_ids)
    for row in db.query(sql + ' ORDER BY id', tuple(args)):
        if set(form_session_ids(row['id'])) == wanted:
            return row
    return None


# --------------------------------------------------------------------------- #
# Filtered student reports (used by the admin PDF downloads)
# --------------------------------------------------------------------------- #
def report_forms(filters, include_drafts=False):
    """Forms to include in a student report, honouring the admin filters.

    When a specific ``form_id`` is supplied only that form is returned.  Draft
    forms are excluded by default because they are not distributed to students.
    """
    filters = dict(filters or {})
    form_id = filters.get('form_id')
    if form_id:
        form = get_form(form_id, include_deleted=True)
        return [form] if form else []
    forms = list_forms(filters)
    if not include_drafts and not filters.get('status'):
        forms = [f for f in forms if (f['status'] or '') != 'draft']
    return forms


def report_rows(forms, which):
    """Build the student rows for a submitted / not-submitted report.

    ``which`` is ``'submitted'`` or ``'not_submitted'``.  Rows carry academic and
    form detail only — never any answer content — so anonymity is preserved.
    Duplicate students within a single form are never returned.
    """
    rows = []
    for f in forms:
        if which == 'submitted':
            students = submitted_students_for_form(f['id'])
            status = 'Submitted'
        else:
            students = not_submitted_students_for_form(f['id'])
            status = 'Not Submitted'
        seen = set()
        for s in students:
            if s['id'] in seen:
                continue
            seen.add(s['id'])
            rows.append({
                'roll': s['roll'], 'name': s['name'],
                'academic_year': s['academic_year'], 'semester': s['semester'],
                'branch': s['branch'], 'timetable': s['timetable'],
                'section': s['timetable_name'] or s['timetable'],
                'subject_code': f['subject_code'], 'subject_name': f['subject_name'],
                'form_id': f['id'], 'form_title': f['title'],
                'status': status,
                'submitted_at': s['submitted_at'] if which == 'submitted' else None,
            })
    rows.sort(key=lambda r: (r['branch'] or '', r['semester'] or 0,
                             r['timetable'] or '', r['roll'] or ''))
    return rows


# --------------------------------------------------------------------------- #
# Second-faculty assignments and eligibility
# --------------------------------------------------------------------------- #
def confirmed_second_faculty_for_session(session_id):
    """Return the confirmed second-faculty assignment for a class session, or None.

    Unresolved / missing mappings never grant eligibility.
    """
    return db.query(
        """SELECT * FROM second_faculty_assignments
           WHERE session_id=? AND status='confirmed'
             AND second_teacher_user_id IS NOT NULL
             AND second_emp_code IS NOT NULL AND second_emp_code != ''
           LIMIT 1""",
        (session_id,), one=True)


def student_eligible_for_second_faculty_form(student_user_id, form_id):
    """Eligibility for a second-faculty form.

    Requires:
    * form.category = 'second'
    * a confirmed second_faculty_assignment linked to the form (or covering a
      session the student is enrolled in)
    * the student is in session_students for a covered session
    * no prior submission for this form
    """
    form = get_form(form_id)
    if not form or form.get('status') != 'open':
        return False
    if (form.get('category') or 'main') != 'second':
        return False
    # Student must be eligible under the normal form session coverage first.
    if not student_eligible_for_form(student_user_id, form_id):
        return False
    # And at least one covered session must have a confirmed second-faculty assignment.
    row = db.query(
        """SELECT 1 FROM form_sessions fs
           JOIN second_faculty_assignments sfa ON sfa.session_id=fs.session_id
           WHERE fs.form_id=? AND sfa.status='confirmed'
             AND sfa.second_teacher_user_id IS NOT NULL
           LIMIT 1""",
        (form_id,), one=True)
    return bool(row)


def second_faculty_summary(branch_ids=None):
    """Aggregate counts of second-faculty assignment statuses."""
    sql = """SELECT status, COUNT(*) c FROM second_faculty_assignments"""
    args = []
    if branch_ids:
        ph = ','.join('?' * len(branch_ids))
        sql += f' WHERE branch_id IN ({ph})'
        args = list(branch_ids)
    sql += ' GROUP BY status'
    rows = db.query(sql, tuple(args))
    out = {'confirmed': 0, 'unresolved': 0, 'rejected': 0, 'conflict': 0}
    for r in rows:
        if r['status'] in out:
            out[r['status']] = r['c']
    return out
