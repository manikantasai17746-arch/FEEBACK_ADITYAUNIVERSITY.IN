"""Business logic for the feedback system.

All functions are additive to the existing schema and operate on real data.
Anonymity is enforced here: every teacher-facing query reads from the
``anonymous_answers`` view (which has no student linkage) or aggregates
without ever selecting student identity.
"""
import csv
import io
from datetime import datetime

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
    out = []
    for q in questions:
        q = dict(q)
        q['options'] = [dict(o) for o in db.query(
            'SELECT * FROM question_options WHERE question_id=? ORDER BY position, id', (q['id'],))]
        out.append(q)
    return out


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
        instructions=(payload.get('instructions') or '').strip()[:2000],
        academic_year_id=payload.get('academic_year_id'),
        semester_id=payload.get('semester_id'),
        branch_id=payload.get('branch_id'),
        timetable_id=payload.get('timetable_id'),
        session_id=payload.get('session_id'),
        scope=payload.get('scope') or 'session',
        subject_code=payload.get('subject_code'),
        subject_name=payload.get('subject_name'),
    )
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
    for sid in session_ids:
        conn.execute('INSERT OR IGNORE INTO form_sessions(form_id, session_id) VALUES(?,?)',
                     (form_id, sid))

    # replace questions (answers reference questions; only safe before publish)
    conn.execute('DELETE FROM form_questions WHERE form_id=?', (form_id,))
    for pos, q in enumerate(questions):
        qid = db.insert_id(
            'INSERT INTO form_questions(form_id, position, text, qtype, is_required) '
            'VALUES(?,?,?,?,?)',
            (form_id, pos, (q['text'] or '').strip()[:1000], q.get('qtype', 'rating'),
             1 if q.get('is_required') else 0))
        for opos, opt in enumerate(q.get('options') or []):
            conn.execute(
                'INSERT INTO question_options(question_id, position, label, value) VALUES(?,?,?,?)',
                (qid, opos, (opt.get('label') or '').strip()[:200], opt.get('value')))
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
    if not form_session_ids(form_id):
        errors.append('This form is not linked to any eligible students '
                      '(no matching teaching sessions were found).')
    if form_is_subject_scoped(form) and eligible_count_for_form(form_id) == 0:
        errors.append('No students are enrolled in this subject for the selected '
                      'academic configuration, so the form cannot be published.')
    if not form['title'] or not form['title'].strip():
        errors.append('A form title is required.')
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
    out = []
    for f in rows:
        eligible = eligible_count_for_form(f['id'])
        submitted = response_count(f['id'])
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
    def scalar(sql, args=()):
        return db.query(sql, args, one=True)['c']
    return {
        'students': scalar("SELECT COUNT(*) c FROM users WHERE role='student'"),
        'teachers': scalar("SELECT COUNT(*) c FROM users WHERE role='teacher'"),
        'branches': scalar('SELECT COUNT(*) c FROM branches'),
        'timetables': scalar('SELECT COUNT(*) c FROM timetables WHERE is_deleted=0'),
        'sessions': scalar('SELECT COUNT(*) c FROM class_sessions WHERE is_deleted=0'),
        'forms': scalar('SELECT COUNT(*) c FROM feedback_forms WHERE is_deleted=0'),
        'forms_open': scalar("SELECT COUNT(*) c FROM feedback_forms WHERE is_deleted=0 AND status='open'"),
        'forms_draft': scalar("SELECT COUNT(*) c FROM feedback_forms WHERE is_deleted=0 AND status='draft'"),
        'submissions': scalar('SELECT COUNT(*) c FROM submissions'),
        'published': scalar('SELECT COUNT(*) c FROM result_publications WHERE is_published=1'),
    }


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
    return db.query(SESSION_SELECT + ' WHERE ' + ' AND '.join(where) +
                    ' ORDER BY b.code, t.code, cs.period_no, cs.subject_code', tuple(args))


def existing_form_session_ids():
    return {r['session_id'] for r in db.query(
        'SELECT DISTINCT session_id FROM feedback_forms '
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


def bulk_preview(filters):
    sessions = sessions_for_bulk(filters)
    existing = existing_form_session_ids()
    to_create = [s for s in sessions if s['id'] not in existing]
    sample = [{
        'label': f"Period {s['period_no']}: {s['subject_name']} ({s['subject_code']}) – "
                 f"{s['teacher_name'] or 'Unassigned'}",
        'timetable': f"{s['branch']} · {s['timetable']}",
    } for s in to_create[:10]]
    return {'matched': len(sessions), 'existing': len(sessions) - len(to_create),
            'to_create': len(to_create), 'sample': sample}


def bulk_create_forms(filters, questions, title_pattern, status, admin_id):
    """Create one draft (or open) form per matching session that has none yet."""
    sessions = sessions_for_bulk(filters)
    existing = existing_form_session_ids()
    created, skipped, opened, errors, created_ids = 0, 0, 0, [], []
    for s in sessions:
        if s['id'] in existing:
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
        existing.add(s['id'])
    audit('form.bulk_create', 'feedback_forms', None,
          f'{created} form(s) generated, {skipped} skipped (already existed)')
    return {'matched': len(sessions), 'created': created, 'skipped': skipped,
            'opened': opened, 'errors': errors, 'created_ids': created_ids}


# --------------------------------------------------------------------------- #
# Subject-scoped forms ("No Preference (All Timetables)")
# --------------------------------------------------------------------------- #
def subject_scope_filters(academic_year_id=None, semester_id=None, branch_id=None,
                          timetable_id=None, subject_code=None):
    return {
        'academic_year_id': academic_year_id,
        'semester_id': semester_id,
        'branch_id': branch_id,
        'timetable_id': timetable_id,
        'subject_code': subject_code,
    }


def subjects_for_scope(filters):
    """Distinct subjects taught in the selected academic configuration."""
    rows = sessions_for_bulk(filters)
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
        existing = find_duplicate_subject_form(
            filters.get('academic_year_id'), filters.get('semester_id'),
            filters.get('branch_id'), filters.get('timetable_id'),
            filters.get('subject_code'))
        if existing:
            result['existing_form_id'] = existing['id']
            result['existing_form_title'] = existing['title']
    return result


def find_duplicate_subject_form(academic_year_id, semester_id, branch_id,
                                timetable_id, subject_code):
    """An existing, non-deleted subject-scoped form with the same configuration.

    Used to prevent duplicate forms for repeated clicks or requests.  Session-
    scoped (Normal-mode) forms are intentionally excluded: the application has
    always allowed several forms per period (e.g. different cycles).
    """
    if not subject_code:
        return None
    return db.query("""SELECT * FROM feedback_forms
        WHERE is_deleted=0 AND scope='subject' AND subject_code=?
          AND COALESCE(academic_year_id,-1)=COALESCE(?,-1)
          AND COALESCE(semester_id,-1)=COALESCE(?,-1)
          AND COALESCE(branch_id,-1)=COALESCE(?,-1)
          AND COALESCE(timetable_id,-1)=COALESCE(?,-1)
        ORDER BY id LIMIT 1""",
        (subject_code, academic_year_id, semester_id, branch_id, timetable_id), one=True)


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
