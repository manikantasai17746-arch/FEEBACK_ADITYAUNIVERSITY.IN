"""Phase 5A - lifecycle of Main / Second / Lab feedback forms.

draft --publish--> open --close--> closed --(archive: existing admin flow)--> archived

Rules enforced here (never in the browser):

* the assignment is looked up in the DATABASE (``feedback_categories.resolve_scope``);
* only an admin, or an HOD whose department scope contains the assignment's
  branch, may create / edit / publish / close a category form;
* only DRAFT forms can be edited; once a form is open, closed or archived its
  category, assignment and questions are locked (no answers can be orphaned);
* a form cannot be published with zero eligible students, with an invalid
  question set, with an expired deadline or while another live form covers
  the same category + assignment;
* publishing freezes the eligible students (``form_eligible_students``) and
  opens the form in ONE transaction, and a concurrent second publish loses.
"""
from datetime import datetime

from . import db, services
from .feedback_categories import (LAB, MAIN, SECOND, ScopeError, conflicting_forms,
                                  eligible_count, expected_categories, has_snapshot,
                                  normalize_category, resolve_scope, scope_for_form,
                                  snapshot_eligibility)
from .security import audit, scoped_branch_ids_or_none


# --------------------------------------------------------------------------- #
# Default question sets - the three forms have DIFFERENT purposes/questions
# --------------------------------------------------------------------------- #
_SCALE = [{'label': 'Excellent', 'value': 5}, {'label': 'Very Good', 'value': 4},
          {'label': 'Good', 'value': 3}, {'label': 'Average', 'value': 2},
          {'label': 'Poor', 'value': 1}]


def _rating(text):
    return {'text': text, 'qtype': 'rating', 'is_required': True,
            'options': [dict(o) for o in _SCALE]}


def _comment(text):
    return {'text': text, 'qtype': 'text', 'is_required': False, 'options': []}


DEFAULT_QUESTIONS = {
    # Main Faculty: regular subject teaching.
    MAIN: [
        _rating('How clearly does the faculty explain the subject concepts?'),
        _rating('How well is the syllabus covered and the class time used?'),
        _rating('How approachable is the faculty for doubts in class?'),
        _rating('How regular and punctual are the classes?'),
        _comment('Suggestions to improve the subject teaching'),
    ],
    # Second Faculty: lab ASSISTANCE.
    SECOND: [
        _rating('How well does the faculty observe and monitor students during the lab?'),
        _rating('How carefully does the faculty check your practical work?'),
        _rating('How helpful is the faculty in coding and debugging during the lab?'),
        _rating('How well does the faculty clear your doubts in the lab?'),
        _comment('Suggestions to improve the lab assistance'),
    ],
    # Lab feedback: the MAIN faculty's lab / practical teaching.
    LAB: [
        _rating('How clearly does the faculty explain the lab experiments and procedures?'),
        _rating('How well does the faculty demonstrate and guide the practical work?'),
        _rating('How useful are the lab sessions for understanding the theory?'),
        _rating('How fairly and promptly are your lab records and work evaluated?'),
        _comment('Suggestions to improve the lab sessions'),
    ],
}


def default_questions(category):
    """A fresh copy of the default question set of one category."""
    return [dict(q, options=[dict(o) for o in q['options']])
            for q in DEFAULT_QUESTIONS[normalize_category(category)]]


def default_title(sc):
    who = sc.get('faculty_name') or sc.get('faculty_emp_code') or ''
    label = {MAIN: 'Main Faculty feedback', SECOND: 'Second Faculty feedback (lab assistance)',
             LAB: 'Lab feedback'}[sc['category']]
    return f'{label} - {who} - {sc["subject_name"] or sc["subject_code"]}'.strip()[:200]


# --------------------------------------------------------------------------- #
# Authorization
# --------------------------------------------------------------------------- #
def actor_may_manage(actor, sc):
    """Admin: any scope.  HOD: only branches in their allocation/scope.
    Teachers, students and everyone else: never."""
    if not actor:
        return False
    role = actor['role']
    if role == 'admin':
        return True
    if role == 'hod':
        allowed = scoped_branch_ids_or_none(actor) or []
        return sc['branch_id'] in allowed
    return False


def _require_actor(actor, sc):
    if not actor_may_manage(actor, sc):
        raise ScopeError('forbidden', 'You are not allowed to manage this feedback form.', 403)


def _load_form(form_id):
    form = services.get_form(form_id)
    if not form:
        raise ScopeError('form_not_found', 'That feedback form does not exist.', 404)
    return form


def _category_of(form):
    try:
        return normalize_category(form['category'])
    except ScopeError:
        raise ScopeError('legacy_form',
                         'This is a legacy form that is not in one of the three categories; '
                         'it cannot be managed here.', 409)


# --------------------------------------------------------------------------- #
# Create / update (drafts only)
# --------------------------------------------------------------------------- #
def _payload(sc, title, instructions, deadline_at):
    p = dict(
        title=title, instructions=instructions, category=sc['category'],
        academic_year_id=sc['academic_year_id'], semester_id=sc['semester_id'],
        branch_id=sc['branch_id'], timetable_id=sc['timetable_id'],
        session_id=sc['session_id'], scope='session',
        subject_code=sc['subject_code'], subject_name=sc['subject_name'],
        second_faculty_assignment_id=sc['second_faculty_assignment_id'],
        lab_faculty_assignment_id=sc['lab_faculty_assignment_id'],
        category_basis='declared')
    if deadline_at is not None:
        p['deadline_at'] = deadline_at
    return p


def _check_questions(questions):
    if not isinstance(questions, list) or not questions:
        raise ScopeError('no_questions', 'Add at least one question.', 400)
    for i, q in enumerate(questions, 1):
        if not isinstance(q, dict) or not (q.get('text') or '').strip():
            raise ScopeError('invalid_question', f'Question {i} has no text.', 400)
        if q.get('qtype', 'rating') not in ('rating', 'text', 'single_choice', 'multiple_choice'):
            raise ScopeError('invalid_question', f'Question {i} has an unknown type.', 400)


def create_form(actor, category, assignment_id, title, questions, instructions='',
                deadline_at=None):
    """Create a DRAFT form for one assignment.  Returns the new form id."""
    sc = resolve_scope(category, assignment_id)
    _require_actor(actor, sc)
    _check_questions(questions)
    if conflicting_forms(sc['category'], sc['assignment_id'], phase=None):
        raise ScopeError('duplicate_form',
                         'A draft or open form already exists for this assignment.', 409)
    form_id = services.save_form(_payload(sc, title, instructions, deadline_at), questions,
                                 actor['id'], session_ids=[sc['session_id']])
    audit('form.category_created', 'feedback_forms', form_id,
          f'{sc["category"]} form for assignment {sc["assignment_id"]}', actor=actor)
    return form_id


def update_draft(actor, form_id, title=None, questions=None, instructions=None,
                 deadline_at=None, category=None, assignment_id=None):
    """Edit a draft.  Category and assignment are immutable (a different
    category/assignment must be a NEW form); questions are locked after publish."""
    form = _load_form(form_id)
    cat = _category_of(form)
    if form['status'] != 'draft':
        raise ScopeError('form_locked',
                         'Only draft forms can be edited; this form is already '
                         f'{form["status"]}.', 409)
    if category is not None and normalize_category(category) != cat:
        raise ScopeError('category_locked', 'The category of a form cannot be changed.', 409)
    sc = scope_for_form(form)
    if assignment_id is not None and int(assignment_id) != int(sc['assignment_id']):
        raise ScopeError('assignment_locked',
                         'The faculty assignment of a form cannot be changed.', 409)
    _require_actor(actor, sc)
    if db.query('SELECT 1 FROM submissions WHERE form_id=? LIMIT 1', (form_id,), one=True):
        raise ScopeError('form_locked', 'This form already has submissions.', 409)
    qs = questions if questions is not None else _existing_questions(form_id)
    _check_questions(qs)
    services.save_form(
        _payload(sc, title if title is not None else form['title'],
                 instructions if instructions is not None else form['instructions'],
                 deadline_at if deadline_at is not None else form['deadline_at']),
        qs, actor['id'], form_id=form_id, session_ids=[sc['session_id']])
    return form_id


def _existing_questions(form_id):
    return [{'text': q['text'], 'qtype': q['qtype'], 'is_required': q['is_required'],
             'options': [{'label': o['label'], 'value': o['value']} for o in q['options']]}
            for q in services.form_questions(form_id)]


# --------------------------------------------------------------------------- #
# Publish / close
# --------------------------------------------------------------------------- #
def validate_for_publish(form_id):
    """List of human-readable problems; empty list = ready to publish."""
    form = services.get_form(form_id)
    if not form:
        return ['Form not found.']
    try:
        cat = _category_of(form)
        sc = scope_for_form(form)
    except ScopeError as exc:
        return [exc.message]
    errors = []
    if form['status'] != 'draft':
        errors.append(f'Only draft forms can be published (this one is {form["status"]}).')
    if eligible_count(sc) == 0:
        errors.append('No eligible students exist for this assignment, so the form cannot '
                      'be published.')
    for c in conflicting_forms(cat, sc['assignment_id'], exclude_form_id=form_id,
                               phase=form['phase']):
        errors.append(f'Another {c["status"]} form (#{c["id"]}) already covers this assignment.')
    errors.extend(services.validate_form_for_publish(form_id))
    seen, out = set(), []
    for e in errors:                      # de-duplicate while keeping order
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def publish(actor, form_id):
    """Validate, freeze eligibility and open the form atomically.  Returns the
    number of eligible students frozen into the form."""
    form = _load_form(form_id)
    _category_of(form)
    sc = scope_for_form(form)
    _require_actor(actor, sc)
    problems = validate_for_publish(form_id)
    if problems:
        raise ScopeError('not_publishable', ' '.join(problems), 422)

    conn = db.get_db()
    now = datetime.utcnow().isoformat(timespec='seconds')
    try:
        frozen = snapshot_eligibility(conn, form_id, sc)
        if frozen == 0:
            raise ScopeError('no_eligible_students',
                             'No eligible students exist for this assignment.', 422)
        cur = conn.execute(
            """UPDATE feedback_forms SET status='open', published_at=COALESCE(published_at, ?),
                   updated_at=?, eligible_count_at_publish=?, eligibility_snapshot_at=?,
                   category_basis='declared'
               WHERE id=? AND status='draft'""", (now, now, frozen, now, form_id))
        if cur.rowcount != 1:                       # someone else published/closed it first
            raise ScopeError('form_locked', 'This form is no longer a draft.', 409)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    # Human-readable history snapshot (same helper the legacy publish uses); for
    # second/lab forms the "teacher" is the category's faculty, not the main one.
    services._snapshot(form_id)
    db.execute('UPDATE feedback_forms SET snapshot_teacher=? WHERE id=?',
               (sc['faculty_name'], form_id))
    audit('form.open', 'feedback_forms', form_id,
          f'Published {sc["category"]} form to {frozen} eligible students', actor=actor)
    services.link_form_to_requirement(form_id)
    return frozen


def close(actor, form_id):
    """open -> closed.  Closed forms accept no submissions; history is kept."""
    form = _load_form(form_id)
    _category_of(form)
    _require_actor(actor, scope_for_form_lenient(form))
    now = datetime.utcnow().isoformat(timespec='seconds')
    cur = db.get_db().execute(
        "UPDATE feedback_forms SET status='closed', closed_at=?, updated_at=? "
        "WHERE id=? AND status='open'", (now, now, form_id))
    db.get_db().commit()
    if cur.rowcount != 1:
        raise ScopeError('invalid_transition', 'Only an open form can be closed.', 409)
    audit('form.closed', 'feedback_forms', form_id, 'Closed', actor=actor)


def scope_for_form_lenient(form):
    """Authorization scope for close(): the branch comes from the form itself so a
    later-removed assignment can never make a published form un-closable."""
    return {'branch_id': form['branch_id']}


# --------------------------------------------------------------------------- #
# Read helpers
# --------------------------------------------------------------------------- #
def eligible_count_for(form_id):
    """Eligible count: frozen number for published forms, live number for drafts."""
    form = _load_form(form_id)
    _category_of(form)
    if form['eligible_count_at_publish'] is not None:
        return form['eligible_count_at_publish']
    return eligible_count(scope_for_form(form))


def create_forms_for_session(actor, session_id, deadline_at=None, phase=None):
    """Create the DRAFT forms a session needs, from the database state:

    No Lab -> main; Lab Available -> main + lab; with confirmed Second Faculty
    -> + one second form per Second Faculty.  Categories that already have a live
    form for the same phase are skipped.  Returns ``[{'category', 'form_id'|None,
    'skipped'}]``.  Each form uses its own category's default questions."""
    exp = expected_categories(session_id)
    wanted = ([(MAIN, session_id)] if exp['main'] else []) \
        + ([(LAB, session_id)] if exp['lab'] else []) \
        + [(SECOND, a) for a in exp['second']]
    if not wanted:
        raise ScopeError('nothing_to_create',
                         'This session has no active Main Faculty, so no forms apply.', 409)
    out = []
    for cat, aid in wanted:
        sc = resolve_scope(cat, aid)
        _require_actor(actor, sc)
        if conflicting_forms(cat, aid, phase=phase):
            out.append({'category': cat, 'form_id': None, 'skipped': 'already exists'})
            continue
        payload = _payload(sc, default_title(sc), '', deadline_at)
        if phase:
            payload['phase'] = phase
        fid = services.save_form(payload, default_questions(cat), actor['id'],
                                 session_ids=[sc['session_id']])
        audit('form.category_created', 'feedback_forms', fid,
              f'{cat} form for assignment {aid}', actor=actor)
        out.append({'category': cat, 'form_id': fid, 'skipped': None})
    return out

