"""HOD portal — department-scoped form management and results."""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)

from . import db, services
from .security import (audit, current_user, role_required, require_branch_access,
                       scoped_branch_ids_or_none, csrf_token)

bp = Blueprint('hod', __name__, url_prefix='/hod')


def _hod_branches(user):
    ids = scoped_branch_ids_or_none(user)
    if not ids:
        return []
    placeholders = ','.join('?' * len(ids))
    return db.query(
        f'SELECT id, code, name FROM branches WHERE id IN ({placeholders}) ORDER BY code',
        tuple(ids))


@bp.route('/')
@role_required('hod')
def dashboard():
    user = current_user()
    branches = _hod_branches(user)
    branch_ids = [b['id'] for b in branches]

    stats = {
        'branches': branches,
        'student_count': 0,
        'faculty_count': 0,
        'forms_draft': 0,
        'forms_open': 0,
        'forms_closed': 0,
        'sfa_confirmed': 0,
        'sfa_unresolved': 0,
        'recent_forms': [],
    }
    if branch_ids:
        ph = ','.join('?' * len(branch_ids))
        stats['student_count'] = db.query(
            f"""SELECT COUNT(DISTINCT e.student_user_id) c FROM enrollments e
                JOIN timetables t ON t.id=e.timetable_id
                WHERE t.branch_id IN ({ph}) AND e.is_deleted=0""",
            tuple(branch_ids), one=True)['c']
        stats['faculty_count'] = db.query(
            f"""SELECT COUNT(DISTINCT cs.teacher_user_id) c FROM class_sessions cs
                JOIN timetables t ON t.id=cs.timetable_id
                WHERE t.branch_id IN ({ph}) AND cs.is_deleted=0
                  AND cs.teacher_user_id IS NOT NULL""",
            tuple(branch_ids), one=True)['c']
        for status, key in [('draft', 'forms_draft'), ('open', 'forms_open'),
                            ('closed', 'forms_closed')]:
            stats[key] = db.query(
                f"""SELECT COUNT(*) c FROM feedback_forms
                    WHERE branch_id IN ({ph}) AND status=? AND is_deleted=0""",
                tuple(branch_ids) + (status,), one=True)['c']
        stats['sfa_confirmed'] = db.query(
            f"""SELECT COUNT(*) c FROM second_faculty_assignments
                WHERE branch_id IN ({ph}) AND status='confirmed'""",
            tuple(branch_ids), one=True)['c']
        stats['sfa_unresolved'] = db.query(
            f"""SELECT COUNT(*) c FROM second_faculty_assignments
                WHERE branch_id IN ({ph}) AND status='unresolved'""",
            tuple(branch_ids), one=True)['c']
        stats['recent_forms'] = db.query(
            f"""SELECT id, title, status, category, snapshot_subject_code,
                       snapshot_branch, published_at, created_at
                FROM feedback_forms
                WHERE branch_id IN ({ph}) AND is_deleted=0
                ORDER BY created_at DESC LIMIT 15""",
            tuple(branch_ids))

    return render_template('hod_dashboard.html', stats=stats, user=user)


@bp.route('/forms')
@role_required('hod')
def forms():
    user = current_user()
    branches = _hod_branches(user)
    branch_ids = [b['id'] for b in branches]

    academic_year_id = request.args.get('academic_year_id', type=int)
    semester_id = request.args.get('semester_id', type=int)
    subject_code = (request.args.get('subject_code') or '').strip() or None
    status = (request.args.get('status') or '').strip() or None
    q = (request.args.get('q') or '').strip()

    forms_list = []
    counts = {'total': 0, 'open': 0, 'draft': 0, 'closed': 0}
    subjects = []
    years = db.query('SELECT id, name FROM academic_years ORDER BY name DESC')
    try:
        semesters = db.query('SELECT id, number, label FROM semesters ORDER BY number')
    except Exception:
        semesters = db.query('SELECT id, number FROM semesters ORDER BY number')

    if branch_ids:
        ph = ','.join('?' * len(branch_ids))
        where = [f'branch_id IN ({ph})', 'is_deleted=0']
        args = list(branch_ids)
        if academic_year_id:
            where.append('academic_year_id=?'); args.append(academic_year_id)
        if semester_id:
            where.append('semester_id=?'); args.append(semester_id)
        if subject_code:
            where.append('(subject_code=? OR snapshot_subject_code=?)')
            args.extend([subject_code, subject_code])
        if status in ('draft', 'open', 'closed', 'archived'):
            where.append('status=?'); args.append(status)
        if q:
            where.append('(title LIKE ? OR snapshot_subject_code LIKE ? OR snapshot_subject_name LIKE ?)')
            like = f'%{q}%'
            args.extend([like, like, like])
        sql = f"""SELECT id, title, status, category, snapshot_subject_code,
                         snapshot_subject_name, snapshot_branch, snapshot_timetable,
                         published_at, closed_at, created_at
                  FROM feedback_forms WHERE {' AND '.join(where)}
                  ORDER BY created_at DESC"""
        forms_list = db.query(sql, tuple(args))
        # Counts (unfiltered by status/search for tiles)
        for st, key in [('open', 'open'), ('draft', 'draft'), ('closed', 'closed')]:
            counts[key] = db.query(
                f"""SELECT COUNT(*) c FROM feedback_forms
                    WHERE branch_id IN ({ph}) AND is_deleted=0 AND status=?""",
                tuple(branch_ids) + (st,), one=True)['c']
        counts['total'] = db.query(
            f"""SELECT COUNT(*) c FROM feedback_forms
                WHERE branch_id IN ({ph}) AND is_deleted=0""",
            tuple(branch_ids), one=True)['c']
        subjects = db.query(
            f"""SELECT DISTINCT COALESCE(subject_code, snapshot_subject_code) AS code,
                       COALESCE(subject_name, snapshot_subject_name) AS name
                FROM feedback_forms
                WHERE branch_id IN ({ph}) AND is_deleted=0
                  AND COALESCE(subject_code, snapshot_subject_code) IS NOT NULL
                ORDER BY code""", tuple(branch_ids))

    return render_template(
        'hod_forms.html', forms=forms_list, branches=branches,
        counts=counts, years=years, semesters=semesters, subjects=subjects,
        filters={
            'academic_year_id': academic_year_id,
            'semester_id': semester_id,
            'subject_code': subject_code or '',
            'status': status or '',
            'q': q,
        })


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _hod_session_in_scope(user, session_id):
    """Return the session row (as dict) if it belongs to an HOD-scoped branch; else None."""
    if not session_id:
        return None
    session = services.session(session_id)
    if not session:
        return None
    session = dict(session)
    branch_id = session.get('branch_id')
    if branch_id is None:
        return None
    allowed = scoped_branch_ids_or_none(user) or []
    if branch_id not in allowed:
        return None
    return session


def category_is_not_second(data):
    return (data.get('category') or 'main').strip().lower() != 'second'


def _hod_validate_form_payload(user, data):
    """Server-side permission checks for HOD form creation.

    Returns (payload, questions, session_ids, errors).
    Never trusts client-supplied branch/teacher/role without validation against
    the authenticated HOD's department scope and confirmed second-faculty rules.
    """
    from flask import abort
    errors = []
    questions = data.get('questions') or []
    if isinstance(questions, str):
        import json
        try:
            questions = json.loads(questions)
        except Exception:
            questions = []

    scope = (data.get('scope') or 'session').strip().lower()
    if scope not in ('session', 'subject'):
        scope = 'session'

    category = (data.get('category') or 'main').strip().lower()
    if category not in ('main', 'second'):
        category = 'main'

    # Form name: pick list (Phase 1 / Phase 2) or a custom name typed by the HOD.
    phase_choice = (data.get('phase') or '').strip().lower()
    title_in = (data.get('title') or '').strip()
    if phase_choice in services.PHASE_LABELS:
        phase = phase_choice
        title_in = services.PHASE_LABELS[phase]
    else:
        # Custom name. Typing "Phase 1" by hand still counts as that phase.
        phase = services.phase_from_title(title_in)
        if phase:
            title_in = services.PHASE_LABELS[phase]
    if not title_in and category_is_not_second(data):
        errors.append('Choose Phase 1, Phase 2, or type a custom form name.')

    payload = {
        'title': title_in,
        'phase': phase,
        'instructions': data.get('instructions'),
        'academic_year_id': _int(data.get('academic_year_id')),
        'semester_id': _int(data.get('semester_id')),
        'branch_id': _int(data.get('branch_id')),
        'timetable_id': _int(data.get('timetable_id')),
        'session_id': _int(data.get('session_id')),
        'scope': scope,
        'category': category,
        'second_faculty_assignment_id': _int(data.get('second_faculty_assignment_id')),
    }

    # Timetables ticked in the multi-select (none = "No Preference / all timetables").
    raw_tt = data.get('timetable_ids')
    if isinstance(raw_tt, str):
        raw_tt = [x for x in raw_tt.split(',') if x.strip()]
    tt_ids = []
    for x in (raw_tt or []):
        v = _int(x)
        if v and v not in tt_ids:
            tt_ids.append(v)
    if not tt_ids and payload['timetable_id']:
        tt_ids = [payload['timetable_id']]

    allowed_branches = scoped_branch_ids_or_none(user) or []
    if not allowed_branches:
        return None, None, None, ['No department scope assigned. Contact Admin.']

    existing_id = _int(data.get('form_id'))
    if existing_id:
        form = services.get_form(existing_id, include_deleted=True)
        if not form:
            return None, None, None, ['Form not found.']
        form = dict(form)
        if form.get('branch_id') not in allowed_branches:
            return None, None, None, ['You do not have permission to edit this form.']
        if services.form_has_submissions(existing_id):
            return None, None, None, [
                'This form already has student responses, so its questions and target '
                'cannot be changed. Create a new form instead.']

    session_ids = []
    if scope == 'subject':
        subject_code = (data.get('subject_code') or '').strip()
        if not payload['academic_year_id']:
            errors.append('Select an academic year.')
        if not payload['semester_id']:
            errors.append('Select a semester.')
        if not payload['branch_id']:
            errors.append('Select a branch.')
        if not subject_code:
            errors.append('Select a subject.')
        if payload['branch_id'] and payload['branch_id'] not in allowed_branches:
            errors.append('You are not authorised for that department.')
        if errors:
            return None, None, None, errors
        if tt_ids:
            # every ticked timetable must exist and belong to the chosen department,
            # year and semester (and therefore to this HOD's scope)
            ph = ','.join('?' * len(tt_ids))
            ok_rows = db.query(
                f'SELECT id FROM timetables WHERE is_deleted=0 AND branch_id=? '
                f'AND academic_year_id=? AND semester_id=? AND id IN ({ph})',
                (payload['branch_id'], payload['academic_year_id'],
                 payload['semester_id'], *tt_ids))
            if len(ok_rows) != len(tt_ids):
                return None, None, None, [
                    'One or more selected timetables do not belong to that department, '
                    'academic year and semester.']
        # One ticked timetable keeps the old single-timetable behaviour; several
        # (or none) are stored without a single timetable_id.  The form_sessions
        # rows are the source of truth for who receives the form.
        payload['timetable_id'] = tt_ids[0] if len(tt_ids) == 1 else None
        filters = services.subject_scope_filters(
            payload['academic_year_id'], payload['semester_id'], payload['branch_id'],
            payload['timetable_id'], subject_code,
            timetable_ids=tt_ids if len(tt_ids) > 1 else None)
        sessions = [dict(r) for r in services.sessions_for_bulk(filters)]
        if not sessions:
            return None, None, None, [
                'No teaching sessions match that year, semester, branch, timetable and '
                'subject.']
        # Ensure every session is inside HOD scope (subject_scope_filters already
        # filtered by branch, but re-validate).
        for s in sessions:
            if s.get('branch_id') not in allowed_branches:
                return None, None, None, ['Session outside your department scope.']
        _dup_title = (payload.get('title') or '').strip() or 'Untitled feedback form'
        if len(tt_ids) > 1:
            dup = services.find_duplicate_subject_form_for_sessions(
                payload['academic_year_id'], payload['semester_id'], payload['branch_id'],
                subject_code, [s['id'] for s in sessions], title=_dup_title)
        else:
            dup = services.find_duplicate_subject_form(
                payload['academic_year_id'], payload['semester_id'], payload['branch_id'],
                payload['timetable_id'], subject_code, title=_dup_title)
        if dup and dup['id'] != existing_id:
            return None, None, None, [
                f'A feedback form for this exact configuration already exists '
                f'(#{dup["id"]}: {dup["title"]}).']
        payload.update(
            session_id=sessions[0]['id'],
            subject_code=subject_code,
            subject_name=sessions[0].get('subject_name'),
        )
        session_ids = [s['id'] for s in sessions]
    else:
        # Session-scoped: one teacher + period
        if not payload['session_id']:
            return None, None, None, [
                'Select a subject, teacher and period for this form.']
        session = _hod_session_in_scope(user, payload['session_id'])
        if not session:
            return None, None, None, [
                'The selected teaching session is outside your authorised department '
                'or does not exist.']
        payload.update(
            academic_year_id=session.get('year_id') or session.get('academic_year_id'),
            semester_id=session.get('semester_id'),
            branch_id=session.get('branch_id'),
            timetable_id=session.get('tt_id') or session.get('timetable_id'),
            subject_code=session.get('subject_code'),
            subject_name=session.get('subject_name'),
        )
        session_ids = [session['id']]

        # Second-faculty path: HOD may create a form targeted at a confirmed
        # second faculty member for this session.
        if category == 'second':
            sfa = services.confirmed_second_faculty_for_session(session['id'])
            if not sfa:
                return None, None, None, [
                    'No confirmed second-faculty assignment exists for this session. '
                    'Resolve the assignment before creating a second-faculty form.']
            sfa = dict(sfa)
            # Ensure the second teacher is permitted (assignment already scoped
            # to the session's branch which we validated).
            payload['second_faculty_assignment_id'] = sfa.get('id')
            # Optionally override title hint
            if not (payload.get('title') or '').strip():
                second_name = sfa.get('second_teacher_name') or sfa.get('second_emp_code') or 'Second faculty'
                payload['title'] = f'Second faculty feedback — {second_name}'

    errors.extend(services.phase_rule_errors(
        payload.get('phase'), session_ids, exclude_form_id=existing_id))
    if errors:
        return None, None, None, errors

    return payload, questions, session_ids, errors


@bp.route('/forms/new', methods=['GET', 'POST'])
@role_required('hod')
def form_new():
    """HOD form creation page (UI is minimal; full builder is admin-side).

    POST accepts the same JSON/form payload shape as admin form_save and
    enforces department + second-faculty permission checks server-side.
    """
    user = current_user()
    branches = _hod_branches(user)
    if not branches:
        flash('No department scope assigned. Contact Admin.', 'warning')
        return redirect(url_for('hod.dashboard'))

    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form.to_dict(flat=True)
        payload, questions, session_ids, errors = _hod_validate_form_payload(user, data)
        if errors:
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                from flask import jsonify
                return jsonify(ok=False, errors=errors), 400
            for e in errors:
                flash(e, 'error')
            return redirect(url_for('hod.form_new'))
        fid = services.save_form(
            payload, questions or [], user['id'],
            form_id=_int(data.get('form_id')), session_ids=session_ids)
        audit('hod.form.save', 'feedback_forms', fid,
              f'HOD saved form (category={payload.get("category")}, '
              f'{len(session_ids)} session(s))')
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            from flask import jsonify
            return jsonify(ok=True, form_id=fid,
                           scope=payload.get('scope'),
                           category=payload.get('category'),
                           sessions=len(session_ids))
        flash(f'Form #{fid} saved as draft.', 'success')
        return redirect(url_for('hod.forms'))

    years = db.query('SELECT id, name FROM academic_years ORDER BY name DESC')
    semesters = db.query('SELECT id, number, label FROM semesters ORDER BY number')
    return render_template('hod_form_new.html', branches=branches,
                           years=years, semesters=semesters,
                           default_instructions=services.DEFAULT_INSTRUCTIONS)


@bp.route('/forms/save', methods=['POST'])
@role_required('hod')
def form_save():
    """JSON API mirroring admin.form_save with HOD department isolation.

    Clients must not supply a branch/teacher outside the authenticated HOD's
    scope; all checks are performed server-side against session and
    hod_allocations / user_department_scopes.
    """
    from flask import jsonify
    user = current_user()
    data = request.get_json(silent=True) or request.form.to_dict(flat=True)
    payload, questions, session_ids, errors = _hod_validate_form_payload(user, data)
    if errors:
        return jsonify(ok=False, errors=errors), 400
    fid = services.save_form(
        payload, questions or [], user['id'],
        form_id=_int(data.get('form_id')), session_ids=session_ids)
    audit('hod.form.save', 'feedback_forms', fid,
          f'HOD saved form (category={payload.get("category")}, '
          f'{len(session_ids)} session(s))')
    return jsonify(ok=True, form_id=fid,
                   scope=payload.get('scope'),
                   category=payload.get('category'),
                   sessions=len(session_ids))


@bp.route('/forms/<int:fid>/status', methods=['POST'])
@role_required('hod')
def form_status(fid):
    """Publish / close / archive a form within the HOD's department scope."""
    from flask import jsonify
    user = current_user()
    form = services.get_form(fid)
    if not form:
        abort(404)
    form = dict(form)
    allowed = scoped_branch_ids_or_none(user) or []
    if form.get('branch_id') not in allowed:
        abort(403)
    status = request.form.get('status') or (request.get_json(silent=True) or {}).get('status')
    if status not in ('open', 'closed', 'archived', 'draft'):
        abort(400)
    if status == 'open':
        errors = services.validate_form_for_publish(fid)
        if errors:
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify(ok=False, errors=errors), 400
            for e in errors:
                flash(e, 'error')
            return redirect(url_for('hod.form_new'))
    services.set_form_status(fid, status)
    audit('hod.form.status', 'feedback_forms', fid, f'Status -> {status}')
    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify(ok=True, form_id=fid, status=status)
    flash(f'Form status updated to {status}.', 'success')
    return redirect(request.form.get('next') or url_for('hod.forms'))


@bp.route('/api/timetables')
@role_required('hod')
def api_timetables():
    from flask import jsonify
    user = current_user()
    allowed = scoped_branch_ids_or_none(user) or []
    year_id = request.args.get('year_id', type=int)
    semester_id = request.args.get('semester_id', type=int)
    branch_id = request.args.get('branch_id', type=int)
    where, args = ['t.is_deleted=0'], []
    if allowed:
        ph = ','.join('?' * len(allowed))
        where.append(f't.branch_id IN ({ph})')
        args.extend(allowed)
    else:
        return jsonify(timetables=[])
    if year_id:
        where.append('t.academic_year_id=?'); args.append(year_id)
    if semester_id:
        where.append('t.semester_id=?'); args.append(semester_id)
    if branch_id:
        if branch_id not in allowed:
            return jsonify(timetables=[])
        where.append('t.branch_id=?'); args.append(branch_id)
    rows = db.query(
        f"""SELECT t.id, t.code, t.name, b.code AS branch
            FROM timetables t JOIN branches b ON b.id=t.branch_id
            WHERE {' AND '.join(where)} ORDER BY b.code, t.code""",
        tuple(args))
    return jsonify(timetables=[dict(r) for r in rows])


@bp.route('/api/sessions')
@role_required('hod')
def api_sessions():
    from flask import jsonify
    user = current_user()
    allowed = scoped_branch_ids_or_none(user) or []
    # One or several timetables (?timetable_id=1&timetable_id=2).
    tt_ids = list(dict.fromkeys(request.args.getlist('timetable_id', type=int)))
    if not tt_ids:
        return jsonify(sessions=[])
    out = []
    for timetable_id in tt_ids:
        tt = db.query(
            'SELECT id, code, branch_id FROM timetables WHERE id=? AND is_deleted=0',
            (timetable_id,), one=True)
        if not tt or tt['branch_id'] not in allowed:
            continue
        prefix = f"{tt['code']} · " if len(tt_ids) > 1 else ''
        for r in services.sessions_for_timetable(timetable_id):
            out.append({
                'id': r['id'], 'subject_code': r['subject_code'],
                'subject_name': r['subject_name'],
                'teacher_name': r['teacher_name'] or 'Unassigned',
                'period_no': r['period_no'], 'timetable_id': timetable_id,
                'label': f"{prefix}Period {r['period_no']}: {r['subject_name']} "
                         f"({r['subject_code']}) - {r['teacher_name'] or 'Unassigned'}"})
    return jsonify(sessions=out)


@bp.route('/api/subjects')
@role_required('hod')
def api_subjects():
    from flask import jsonify
    user = current_user()
    allowed = scoped_branch_ids_or_none(user) or []
    branch_id = request.args.get('branch_id', type=int)
    if branch_id and branch_id not in allowed:
        return jsonify(subjects=[])
    tt_ids = list(dict.fromkeys(request.args.getlist('timetable_id', type=int)))
    filters = services.subject_scope_filters(
        request.args.get('academic_year_id', type=int),
        request.args.get('semester_id', type=int),
        branch_id,
        tt_ids[0] if len(tt_ids) == 1 else None,
        request.args.get('subject_code') or None,
        timetable_ids=tt_ids if len(tt_ids) > 1 else None)
    # Extra guard: if no branch filter, limit to HOD branches via post-filter of subjects
    subjects = services.subjects_for_scope(filters)
    if branch_id is None and allowed:
        # subjects_for_scope may span all branches when branch unset; filter via sessions
        # Prefer requiring branch selection in UI; return empty if no branch.
        return jsonify(subjects=[])
    return jsonify(subjects=subjects)


@bp.route('/api/subject/preview')
@role_required('hod')
def api_subject_preview():
    from flask import jsonify
    user = current_user()
    allowed = scoped_branch_ids_or_none(user) or []
    branch_id = request.args.get('branch_id', type=int)
    if not branch_id or branch_id not in allowed:
        return jsonify(eligible=0, sessions=0, timetables=0, teachers=0,
                       subject_code=None, subject_name=None)
    tt_ids = list(dict.fromkeys(request.args.getlist('timetable_id', type=int)))
    filters = services.subject_scope_filters(
        request.args.get('academic_year_id', type=int),
        request.args.get('semester_id', type=int),
        branch_id,
        tt_ids[0] if len(tt_ids) == 1 else None,
        request.args.get('subject_code') or None,
        timetable_ids=tt_ids if len(tt_ids) > 1 else None)
    return jsonify(services.subject_scope_preview(filters))

def _hod_form_or_403(fid):
    """Return the form if it belongs to one of this HOD's branches, else 403/404."""
    user = current_user()
    form = services.get_form(fid)
    if not form:
        abort(404)
    allowed = scoped_branch_ids_or_none(user) or []
    if form['branch_id'] not in allowed:
        abort(403)
    return form


@bp.route('/results')
@role_required('hod')
def results():
    user = current_user()
    branches = _hod_branches(user)
    branch_ids = [b['id'] for b in branches]
    forms_list = []
    if branch_ids:
        ph = ','.join('?' * len(branch_ids))
        forms_list = db.query(
            f"""SELECT f.id, f.title, f.status, f.category, f.session_id,
                       f.snapshot_subject_code, f.snapshot_teacher, f.snapshot_branch,
                       (SELECT COUNT(*) FROM submissions s WHERE s.form_id=f.id) AS responses,
                       COALESCE(rp.is_published, 0) AS is_published,
                       rp.published_at AS results_published_at
                FROM feedback_forms f
                LEFT JOIN result_publications rp
                       ON rp.form_id=f.id AND rp.session_id=f.session_id
                WHERE f.branch_id IN ({ph}) AND f.is_deleted=0
                  AND f.status IN ('open','closed','archived')
                ORDER BY f.published_at DESC, f.id DESC""",
            tuple(branch_ids))
    return render_template('hod_results.html', forms=forms_list)


@bp.route('/results/<int:fid>/publish', methods=['POST'])
@role_required('hod')
def result_publish(fid):
    user = current_user()
    form = _hod_form_or_403(fid)
    if form['status'] == 'draft':
        flash('Publish the form before publishing its results.', 'error')
    elif services.publish_results(fid, user['id']):
        audit('hod.results.publish', 'feedback_forms', fid, 'HOD published results to teacher')
        flash('Results published to the teacher.', 'success')
    else:
        flash('This form has no class session linked, so results cannot be published.', 'error')
    return redirect(url_for('hod.results'))


@bp.route('/results/<int:fid>/unpublish', methods=['POST'])
@role_required('hod')
def result_unpublish(fid):
    user = current_user()
    _hod_form_or_403(fid)
    services.unpublish_results(fid, user['id'])
    audit('hod.results.unpublish', 'feedback_forms', fid, 'HOD unpublished results')
    flash('Results unpublished. The teacher can no longer see them.', 'success')
    return redirect(url_for('hod.results'))


@bp.route('/results/publish-selected', methods=['POST'])
@role_required('hod')
def results_publish_selected():
    user = current_user()
    skip_zero = request.form.get('skip_zero') == '1'
    published = skipped = 0
    for raw in request.form.getlist('form_ids'):
        try:
            fid = int(raw)
        except (TypeError, ValueError):
            continue
        form = services.get_form(fid)
        allowed = scoped_branch_ids_or_none(user) or []
        if not form or form['branch_id'] not in allowed or form['status'] == 'draft':
            skipped += 1
            continue
        if skip_zero:
            cnt = db.query('SELECT COUNT(*) c FROM submissions WHERE form_id=?',
                           (fid,), one=True)['c']
            if cnt == 0:
                skipped += 1
                continue
        if services.publish_results(fid, user['id']):
            audit('hod.results.publish', 'feedback_forms', fid,
                  'HOD published results to teacher')
            published += 1
        else:
            skipped += 1
    flash(f'Published {published} result(s). Skipped {skipped}.', 'success')
    return redirect(url_for('hod.results'))

# --------------------------------------------------------------------------- #
# Improvement actions assigned to this HOD by the Dean
# --------------------------------------------------------------------------- #
_HOD_ACTION_STATUSES = ('planned', 'in_progress', 'completed')


@bp.route('/actions')
@role_required('hod')
def my_actions():
    from datetime import date
    user = current_user()
    rows = db.query(
        """SELECT ia.id, ia.title, ia.description, ia.status, ia.priority,
                  ia.due_date, ia.updated_at, ia.completed_at,
                  b.code AS branch_code, b.name AS branch_name,
                  cu.name AS assigned_by
           FROM improvement_actions ia
           LEFT JOIN branches b ON b.id = ia.branch_id
           LEFT JOIN users cu ON cu.id = ia.created_by
           WHERE ia.owner_user_id=?
           ORDER BY
             CASE ia.status WHEN 'in_progress' THEN 0 WHEN 'planned' THEN 1
                            WHEN 'completed' THEN 2 ELSE 3 END,
             CASE WHEN ia.due_date IS NULL THEN 1 ELSE 0 END, ia.due_date""",
        (user['id'],))
    open_count = sum(1 for r in rows if r['status'] in ('planned', 'in_progress'))
    return render_template('hod_actions.html', user=user, rows=rows,
                           open_count=open_count, now_iso=date.today().isoformat(),
                           statuses=_HOD_ACTION_STATUSES)


@bp.route('/actions/<int:aid>/status', methods=['POST'])
@role_required('hod')
def my_action_status(aid):
    from datetime import datetime
    user = current_user()
    action = db.query('SELECT * FROM improvement_actions WHERE id=?', (aid,), one=True)
    # A HOD can only touch actions the Dean assigned to them.
    if not action or action['owner_user_id'] != user['id']:
        abort(403)
    new_status = request.form.get('status') or ''
    if new_status not in _HOD_ACTION_STATUSES or action['status'] == 'cancelled':
        flash('That status change is not allowed.', 'error')
        return redirect(url_for('hod.my_actions'))
    old_status = action['status']
    note = (request.form.get('note') or '').strip()[:500]
    if new_status == old_status and not note:
        return redirect(url_for('hod.my_actions'))
    now = datetime.utcnow().isoformat(timespec='seconds')
    completed_at = action['completed_at']
    if new_status == 'completed' and old_status != 'completed':
        completed_at = now
    elif new_status != 'completed':
        completed_at = None
    conn = db.get_db()
    conn.execute(
        'UPDATE improvement_actions SET status=?, updated_at=?, completed_at=? WHERE id=?',
        (new_status, now, completed_at, aid))
    conn.execute(
        """INSERT INTO improvement_action_log
           (action_id, actor_user_id, note, old_status, new_status)
           VALUES(?,?,?,?,?)""",
        (aid, user['id'], note or f'Updated by {user["name"]} (HOD)', old_status, new_status))
    conn.commit()
    audit('hod.action_status', 'improvement_actions', aid, f'{old_status}->{new_status}')
    flash('Action updated. The Dean can see your update.', 'success')
    return redirect(url_for('hod.my_actions'))


@bp.route('/second-faculty')
@role_required('hod')
def second_faculty():
    user = current_user()
    branches = _hod_branches(user)
    branch_ids = [b['id'] for b in branches]
    rows = []
    summary = {'confirmed': 0, 'unresolved': 0, 'conflict': 0, 'rejected': 0}
    if branch_ids:
        ph = ','.join('?' * len(branch_ids))
        rows = db.query(
            f"""SELECT sfa.*, b.code AS branch_code, b.name AS branch_name
                FROM second_faculty_assignments sfa
                LEFT JOIN branches b ON b.id=sfa.branch_id
                WHERE sfa.branch_id IN ({ph})
                ORDER BY sfa.status, sfa.subject_code
                LIMIT 500""",
            tuple(branch_ids))
        for st in summary:
            summary[st] = db.query(
                f"""SELECT COUNT(*) c FROM second_faculty_assignments
                    WHERE branch_id IN ({ph}) AND status=?""",
                tuple(branch_ids) + (st,), one=True)['c']
    return render_template('hod_second_faculty.html', rows=rows, summary=summary,
                           branches=branches)


# --------------------------------------------------------------------------- #
# First-login onboarding wizard
# --------------------------------------------------------------------------- #
@bp.route('/onboarding', methods=['GET', 'POST'])
@role_required('hod')
def onboarding():
    """Multi-step HOD onboarding: email verify → claim exclusive allocations."""
    from . import verification, mailer
    from .security import allocation_owner, hod_allocation_keys

    user = current_user()
    row = db.query(
        'SELECT email, email_verified, onboarding_complete FROM users WHERE id=?',
        (user['id'],), one=True)
    if row and row.get('onboarding_complete'):
        return redirect(url_for('hod.dashboard'))

    email_verified = bool(row and row.get('email_verified'))
    step = request.args.get('step', 'email' if not email_verified else 'scopes')

    years = db.query('SELECT id, name, is_active FROM academic_years ORDER BY name DESC')
    branches = db.query(
        """SELECT b.id, b.code, b.name FROM branches b
           JOIN user_department_scopes uds ON uds.branch_id=b.id
           WHERE uds.user_id=? ORDER BY b.code""",
        (user['id'],))
    semesters = db.query('SELECT id, number, label FROM semesters ORDER BY number')
    # Timetables filtered by allowed branches
    branch_ids = [b['id'] for b in branches]
    timetables = []
    if branch_ids:
        ph = ','.join('?' * len(branch_ids))
        timetables = db.query(
            f"""SELECT t.id, t.code, t.name, t.branch_id, t.semester_id,
                       t.academic_year_id, b.code AS branch_code
                FROM timetables t
                JOIN branches b ON b.id=t.branch_id
                WHERE t.branch_id IN ({ph}) AND t.is_deleted=0
                ORDER BY b.code, t.code""",
            tuple(branch_ids))

    selected = []  # list of claimed keys from session staging
    from flask import session as _session
    staging = _session.get('hod_onboard_staging') or []

    preview = None
    conflicts = []

    if request.method == 'POST':
        action = request.form.get('action') or ''
        if action == 'send_code':
            email = (request.form.get('email') or user.get('email') or '').strip().lower()
            if not email or '@' not in email:
                flash('Enter a valid official email address.', 'error')
            else:
                # Rate limit via verification module
                try:
                    code = verification.issue(user['id'], 'verify_email', email)
                    subj, body, html = mailer.code_email(code, 'verify_email', 10)
                    mailer.send_email(email, subj, body, html)
                    flash('Verification code sent. Check your inbox.', 'success')
                    step = 'verify'
                except verification.Cooldown as cd:
                    flash(f'Please wait {cd.seconds}s before requesting another code.', 'error')
                except Exception:
                    flash('Could not send code. Try again later.', 'error')
        elif action == 'verify_code':
            code = (request.form.get('code') or '').strip()
            verified_email = verification.check(user['id'], 'verify_email', code)
            if verified_email:
                db.execute(
                    'UPDATE users SET email_verified=1, email=? WHERE id=?',
                    (verified_email, user['id']))
                audit('hod.email_verified', 'users', user['id'])
                flash('Email verified.', 'success')
                email_verified = True
                step = 'scopes'
            else:
                flash('Invalid or expired code.', 'error')
                step = 'verify'
        elif action == 'add_scope':
            ay = request.form.get('academic_year_id', type=int)
            br = request.form.get('branch_id', type=int)
            sem = request.form.get('semester_id', type=int)
            tt = request.form.get('timetable_id', type=int)
            if not all([ay, br, sem, tt]):
                flash('Select academic year, department, semester and timetable.', 'error')
            elif br not in branch_ids:
                flash('Department not in your authorised list.', 'error')
            else:
                # Validate timetable belongs to branch/semester/year
                tt_row = db.query(
                    """SELECT id FROM timetables
                       WHERE id=? AND branch_id=? AND semester_id=?
                         AND academic_year_id=? AND is_deleted=0""",
                    (tt, br, sem, ay), one=True)
                if not tt_row:
                    flash('Invalid timetable combination.', 'error')
                else:
                    owner = allocation_owner(ay, br, sem, tt)
                    key = {'academic_year_id': ay, 'branch_id': br,
                           'semester_id': sem, 'timetable_id': tt}
                    if owner and owner != user['id']:
                        conflicts.append(key)
                        flash('That allocation is already owned by another HOD.', 'error')
                    elif key not in staging:
                        staging.append(key)
                        _session['hod_onboard_staging'] = staging
                        flash('Allocation added to selection.', 'success')
            step = 'scopes'
        elif action == 'add_scopes':
            # Add several timetable allocations in one go. Year / department /
            # semester come from each timetable itself, so every ticked row is
            # already a valid combination.
            ids = list(dict.fromkeys(request.form.getlist('timetable_ids', type=int)))
            if not ids:
                flash('Tick at least one timetable to add.', 'error')
            else:
                tt_by_id = {t['id']: t for t in timetables}
                staged_ids = {x['timetable_id'] for x in staging}
                added = taken = invalid = 0
                for tid in ids:
                    t = tt_by_id.get(tid)
                    if not t or not t['academic_year_id']:
                        invalid += 1
                        continue
                    key = {'academic_year_id': t['academic_year_id'],
                           'branch_id': t['branch_id'],
                           'semester_id': t['semester_id'],
                           'timetable_id': t['id']}
                    owner = allocation_owner(key['academic_year_id'], key['branch_id'],
                                             key['semester_id'], key['timetable_id'])
                    if owner and owner != user['id']:
                        taken += 1
                    elif tid not in staged_ids:
                        staging.append(key)
                        staged_ids.add(tid)
                        added += 1
                _session['hod_onboard_staging'] = staging
                if added:
                    flash(f'{added} allocation(s) added to your selection.', 'success')
                if taken:
                    flash(f'{taken} allocation(s) skipped: already owned by another HOD.', 'error')
                if invalid:
                    flash(f'{invalid} timetable(s) skipped: not valid for onboarding.', 'error')
            step = 'scopes'
        elif action == 'remove_scope':
            idx = request.form.get('index', type=int)
            if staging and idx is not None and 0 <= idx < len(staging):
                staging.pop(idx)
                _session['hod_onboard_staging'] = staging
            step = 'scopes'
        elif action == 'preview':
            step = 'preview'
            # Build preview counts for staged allocations
            preview = _build_hod_preview(staging)
        elif action == 'confirm':
            # Final transactional claim
            if not staging:
                flash('Add at least one allocation.', 'error')
                step = 'scopes'
            else:
                ok, err = _claim_allocations(user['id'], staging)
                if ok:
                    db.execute(
                        'UPDATE users SET onboarding_complete=1 WHERE id=?',
                        (user['id'],))
                    _session.pop('hod_onboard_staging', None)
                    audit('hod.onboarding_complete', 'users', user['id'],
                          f'{len(staging)} allocations')
                    flash('Onboarding complete. Welcome!', 'success')
                    return redirect(url_for('hod.dashboard'))
                else:
                    flash(err or 'Could not claim allocations (conflict or error).', 'error')
                    step = 'scopes'

    # Rows for the multi-select list (one per timetable, with a status).
    claimed = {}
    if timetables:
        _ids = [t['id'] for t in timetables]
        _ph = ','.join('?' * len(_ids))
        for r in db.query(
                f'SELECT timetable_id, hod_user_id FROM hod_allocations '
                f'WHERE timetable_id IN ({_ph})', tuple(_ids)):
            claimed[r['timetable_id']] = r['hod_user_id']
    year_name = {y['id']: y['name'] for y in years}
    sem_label = {m['id']: (m['label'] or m['number']) for m in semesters}
    staged_ids = {x['timetable_id'] for x in staging}
    options = []
    for t in timetables:
        owner = claimed.get(t['id'])
        if not t['academic_year_id']:
            status = 'noyear'
        elif owner and owner != user['id']:
            status = 'taken'
        elif t['id'] in staged_ids:
            status = 'added'
        else:
            status = 'available'
        options.append({
            'id': t['id'], 'status': status,
            'year': year_name.get(t['academic_year_id'], '—'),
            'branch': t['branch_code'],
            'semester': sem_label.get(t['semester_id'], '?'),
            'code': t['code'], 'name': t['name'],
        })

    # Enrich staging for display
    display_scopes = []
    for s in staging:
        ay = next((y for y in years if y['id'] == s['academic_year_id']), None)
        br = next((b for b in branches if b['id'] == s['branch_id']), None)
        sem = next((m for m in semesters if m['id'] == s['semester_id']), None)
        tt = next((t for t in timetables if t['id'] == s['timetable_id']), None)
        display_scopes.append({
            **s,
            'academic_year': ay['name'] if ay else '?',
            'branch_code': br['code'] if br else '?',
            'semester_label': sem['label'] if sem else '?',
            'timetable_code': tt['code'] if tt else '?',
        })

    if step == 'preview' and preview is None:
        preview = _build_hod_preview(staging)

    return render_template(
        'hod_onboarding.html',
        step=step, email_verified=email_verified, user=user,
        years=years, branches=branches, semesters=semesters,
        timetables=timetables, options=options, display_scopes=display_scopes,
        preview=preview, conflicts=conflicts)


def _build_hod_preview(staging):
    """Read-only preview of sessions/students/teachers/forms for staged keys."""
    if not staging:
        return {'sessions': 0, 'students': 0, 'teachers': 0, 'forms': 0, 'items': []}
    items = []
    total_sessions = total_students = total_teachers = total_forms = 0
    for s in staging:
        sess = db.query(
            """SELECT COUNT(*) c FROM class_sessions
               WHERE timetable_id=? AND is_deleted=0""",
            (s['timetable_id'],), one=True)['c']
        stud = db.query(
            """SELECT COUNT(DISTINCT student_user_id) c FROM enrollments
               WHERE timetable_id=? AND is_deleted=0""",
            (s['timetable_id'],), one=True)['c']
        teach = db.query(
            """SELECT COUNT(DISTINCT teacher_user_id) c FROM class_sessions
               WHERE timetable_id=? AND is_deleted=0 AND teacher_user_id IS NOT NULL""",
            (s['timetable_id'],), one=True)['c']
        forms = db.query(
            """SELECT COUNT(*) c FROM feedback_forms
               WHERE timetable_id=? AND is_deleted=0""",
            (s['timetable_id'],), one=True)['c']
        total_sessions += sess
        total_students += stud
        total_teachers += teach
        total_forms += forms
        items.append({**s, 'sessions': sess, 'students': stud,
                      'teachers': teach, 'forms': forms})
    return {
        'sessions': total_sessions, 'students': total_students,
        'teachers': total_teachers, 'forms': total_forms, 'items': items,
    }


def _claim_allocations(hod_user_id, staging):
    """Claim exclusive allocations transactionally. Returns (ok, error_msg)."""
    from .security import allocation_owner
    conn = db.get_db()
    try:
        for s in staging:
            owner = allocation_owner(
                s['academic_year_id'], s['branch_id'],
                s['semester_id'], s['timetable_id'])
            if owner and owner != hod_user_id:
                conn.rollback()
                return False, 'Allocation already owned by another HOD.'
            # Validate timetable still exists and matches
            tt = conn.execute(
                """SELECT id FROM timetables
                   WHERE id=? AND branch_id=? AND semester_id=?
                     AND academic_year_id=? AND is_deleted=0""",
                (s['timetable_id'], s['branch_id'], s['semester_id'],
                 s['academic_year_id'])).fetchone()
            if not tt:
                conn.rollback()
                return False, 'Invalid timetable combination.'
            try:
                conn.execute(
                    """INSERT INTO hod_allocations
                       (hod_user_id, academic_year_id, branch_id, semester_id,
                        timetable_id, created_by)
                       VALUES(?,?,?,?,?,?)""",
                    (hod_user_id, s['academic_year_id'], s['branch_id'],
                     s['semester_id'], s['timetable_id'], hod_user_id))
            except Exception:
                # Unique constraint violation = concurrent claim
                conn.rollback()
                return False, 'Allocation claimed concurrently by another HOD.'
            key = f"{s['academic_year_id']}:{s['branch_id']}:{s['semester_id']}:{s['timetable_id']}"
            conn.execute(
                """INSERT INTO allocation_history
                   (allocation_key, academic_year_id, branch_id, semester_id,
                    timetable_id, previous_hod_user_id, new_hod_user_id, action,
                    actor_user_id)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (key, s['academic_year_id'], s['branch_id'], s['semester_id'],
                 s['timetable_id'], None, hod_user_id, 'claim', hod_user_id))
        conn.commit()
        return True, None
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, str(exc)


# ---------------------------------------------------------------------------
# Change 11: Faculty Performance marks & historical comparison
# ---------------------------------------------------------------------------

@bp.route('/performance')
@role_required('hod')
def performance():
    """Marks-sheet for the HOD's own department(s)."""
    from . import scoring
    user = current_user()
    branches = _hod_branches(user)
    branch_ids = [b['id'] for b in branches]

    academic_year_id = request.args.get('academic_year_id', type=int)
    semester_id = request.args.get('semester_id', type=int)
    branch_id = request.args.get('branch_id', type=int)
    # Several timetables can be ticked at once (?timetable_id=1&timetable_id=2).
    timetable_ids = list(dict.fromkeys(request.args.getlist('timetable_id', type=int)))
    timetable_id = timetable_ids[0] if len(timetable_ids) == 1 else None
    # Several subjects can be ticked at once (?subject_code=A&subject_code=B).
    subject_codes = list(dict.fromkeys(
        c.strip() for c in request.args.getlist('subject_code') if c and c.strip()))
    subject_code = subject_codes[0] if len(subject_codes) == 1 else None
    # Several teachers can be ticked at once (?teacher_user_id=1&teacher_user_id=2).
    teacher_user_ids = list(dict.fromkeys(request.args.getlist('teacher_user_id', type=int)))
    teacher_user_id = teacher_user_ids[0] if len(teacher_user_ids) == 1 else None
    form_id = request.args.get('form_id', type=int)
    mode = (request.args.get('mode') or 'timetable').strip().lower()
    q = (request.args.get('q') or '').strip().lower()

    # Restrict branch filter to HOD scope
    if branch_id and branch_id not in branch_ids:
        branch_id = None
    scope_ids = [branch_id] if branch_id else branch_ids

    rows = []
    if scope_ids:
        rows = scoring.faculty_scores_for_branches(
            scope_ids,
            academic_year_id=academic_year_id,
            semester_id=semester_id,
            subject_codes=subject_codes or None,
            teacher_user_ids=teacher_user_ids or None,
            form_id=form_id,
            timetable_ids=timetable_ids or None,
            min_responses=1,
        )
        if q:
            rows = [r for r in rows
                    if q in (r.get('teacher_name') or '').lower()
                    or q in (r.get('teacher_emp_code') or '').lower()
                    or q in (r.get('subject_code') or '').lower()
                    or q in (r.get('subject_name') or '').lower()]

    # Enrich rows with obtained / max marks from the 10-marks-per-student rule
    enriched = []
    for r in rows:
        r = dict(r)
        resp = r.get('valid_responses') or r.get('response_count') or 0
        eligible = 0
        try:
            eligible = services.eligible_count_for_form(r['form_id'])
        except Exception:
            eligible = 0
        fm = scoring.form_marks(r['form_id'], eligible)
        r['eligible'] = eligible
        r['max_marks'] = fm['maximum']
        r['obtained_marks'] = fm['obtained']
        r['responses'] = resp
        r['marks_per_question'] = fm['marks_per_question']
        r['question_count'] = fm['question_count']
        r['answer_count'] = fm['answer_count']
        r['students'] = fm['students']
        r['form_marks'] = fm
        enriched.append(r)
    rows = enriched
    _ri = services.round_info([r['form_id'] for r in rows])
    for r in rows:
        r['round_label'] = (_ri.get(r['form_id']) or {}).get('label')

    # Aggregate tiles
    teachers = {r.get('teacher_user_id') for r in rows if r.get('teacher_user_id')}
    subjects = {r.get('subject_code') for r in rows if r.get('subject_code')}
    total_resp = sum(r.get('responses') or 0 for r in rows)
    scores = [r['overall_score'] for r in rows if r.get('overall_score') is not None]
    avg_score = round(sum(scores) / len(scores), 2) if scores else None

    # Rating Marks Summary from form_marks per-rating breakdown (answer counts, not students)
    dist_buckets = {}
    dist_marks_total = 0.0
    total_students_for_dist = 0
    for r in rows:
        fm = r.get('form_marks')
        if not fm:
            continue
        total_students_for_dist += fm.get('students') or 0
        for b in fm.get('rating_breakdown') or []:
            lab = (b.get('label') or 'Option').strip()
            if lab not in dist_buckets:
                dist_buckets[lab] = {
                    'label': lab,
                    'count': 0,
                    'marks': b.get('marks_each'),
                    'total_marks': 0.0,
                }
            dist_buckets[lab]['count'] += b.get('count') or 0
            dist_buckets[lab]['total_marks'] = round(
                dist_buckets[lab]['total_marks'] + (b.get('total_marks') or 0), 2)
            if dist_buckets[lab].get('marks') is None and b.get('marks_each') is not None:
                dist_buckets[lab]['marks'] = b['marks_each']
    distribution = list(dist_buckets.values())
    order = ['excellent', 'very good', 'good', 'average', 'poor']
    def _sk(it):
        lab = (it['label'] or '').lower()
        for i, o in enumerate(order):
            if o in lab:
                return i
        return 99
    distribution.sort(key=_sk)
    dist_total = sum(d['count'] for d in distribution) or 0
    for d in distribution:
        d['pct'] = round(d['count'] * 100.0 / dist_total, 1) if dist_total else 0.0
        d['total_marks'] = round(d.get('total_marks') or 0, 2)
    dist_marks_total = round(sum(d.get('total_marks') or 0 for d in distribution), 2)
    dist_students_total = total_students_for_dist

    years = db.query('SELECT id, name FROM academic_years ORDER BY name DESC')
    try:
        semesters = db.query('SELECT id, number, label FROM semesters ORDER BY number')
    except Exception:
        semesters = db.query('SELECT id, number FROM semesters ORDER BY number')
        for s in semesters:
            # sqlite Row is immutable-ish; convert later in template via number only
            pass
    # Filter dropdown data scoped to HOD branches
    timetables = []
    teachers_list = []
    subjects_list = []
    forms_list = []
    if branch_ids:
        ph = ','.join('?' * len(branch_ids))
        tt_sql = f"""SELECT t.id, t.code, t.name, b.code AS branch
            FROM timetables t JOIN branches b ON b.id=t.branch_id
            WHERE t.branch_id IN ({ph}) AND t.is_deleted=0"""
        tt_args = list(branch_ids)
        if academic_year_id:
            tt_sql += ' AND t.academic_year_id=?'; tt_args.append(academic_year_id)
        if semester_id:
            tt_sql += ' AND t.semester_id=?'; tt_args.append(semester_id)
        if branch_id:
            tt_sql += ' AND t.branch_id=?'; tt_args.append(branch_id)
        tt_sql += ' ORDER BY b.code, t.code'
        timetables = db.query(tt_sql, tuple(tt_args))
        teach_sql = f"""SELECT DISTINCT u.id, u.name, u.emp_code
            FROM users u
            JOIN class_sessions cs ON cs.teacher_user_id=u.id
            JOIN timetables t ON t.id=cs.timetable_id
            WHERE t.branch_id IN ({ph}) AND cs.is_deleted=0 AND t.is_deleted=0"""
        teach_args = list(branch_ids)
        if academic_year_id:
            teach_sql += ' AND t.academic_year_id=?'; teach_args.append(academic_year_id)
        if semester_id:
            teach_sql += ' AND t.semester_id=?'; teach_args.append(semester_id)
        if branch_id:
            teach_sql += ' AND t.branch_id=?'; teach_args.append(branch_id)
        if timetable_ids:
            teach_sql += (' AND t.id IN (' + ','.join('?' * len(timetable_ids)) + ')')
            teach_args.extend(timetable_ids)
        teach_sql += ' ORDER BY u.name'
        teachers_list = db.query(teach_sql, tuple(teach_args))
        # Subjects come from the class timetable, so they are available before any
        # feedback form exists. They follow the year / semester / branch / timetable
        # filters above.
        sub_sql = f"""SELECT cs.subject_code AS code, MIN(cs.subject_name) AS name
            FROM class_sessions cs JOIN timetables t ON t.id=cs.timetable_id
            WHERE t.branch_id IN ({ph}) AND t.is_deleted=0 AND cs.is_deleted=0
              AND cs.subject_code IS NOT NULL AND cs.subject_code <> ''"""
        sub_args = list(branch_ids)
        if academic_year_id:
            sub_sql += ' AND t.academic_year_id=?'; sub_args.append(academic_year_id)
        if semester_id:
            sub_sql += ' AND t.semester_id=?'; sub_args.append(semester_id)
        if branch_id:
            sub_sql += ' AND t.branch_id=?'; sub_args.append(branch_id)
        if timetable_ids:
            sub_sql += (' AND t.id IN (' + ','.join('?' * len(timetable_ids)) + ')')
            sub_args.extend(timetable_ids)
        sub_sql += ' GROUP BY cs.subject_code ORDER BY cs.subject_code'
        subjects_list = db.query(sub_sql, tuple(sub_args))
        forms_list = db.query(
            f"""SELECT id, title, status FROM feedback_forms
                WHERE branch_id IN ({ph}) AND is_deleted=0
                  AND status IN ('open','closed','archived')
                ORDER BY id DESC LIMIT 100""", tuple(branch_ids))

    dept_label = ', '.join(f"{b['code']}" for b in branches) if branches else '—'

    return render_template(
        'hod_performance.html',
        user=user, branches=branches, rows=rows,
        years=years, semesters=semesters,
        timetables=timetables, teachers_list=teachers_list,
        subjects_list=subjects_list, forms_list=forms_list,
        distribution=distribution, dist_total=dist_total,
        dist_students_total=dist_students_total,
        dept_label=dept_label, mode=mode,
        summary={
            'teachers': len(teachers),
            'subjects': len(subjects),
            'responses': total_resp,
            'avg_score': avg_score,
        },
        dist_marks_total=dist_marks_total,
        filters={
            'academic_year_id': academic_year_id,
            'semester_id': semester_id,
            'branch_id': branch_id,
            'timetable_id': timetable_id,
            'timetable_ids': timetable_ids,
            'subject_code': subject_code or '',
            'subject_codes': subject_codes,
            'teacher_user_id': teacher_user_id,
            'teacher_user_ids': teacher_user_ids,
            'form_id': form_id,
            'q': q,
            'mode': mode,
        })


@bp.route('/performance/faculty/<int:teacher_id>')
@role_required('hod')
def performance_detail(teacher_id):
    from . import scoring
    user = current_user()
    branches = _hod_branches(user)
    branch_ids = [b['id'] for b in branches]
    if not branch_ids:
        abort(403)
    rows = scoring.faculty_scores_for_branches(
        branch_ids, teacher_user_id=teacher_id)
    teacher = db.query(
        'SELECT id, name, emp_code FROM users WHERE id=? AND role IN (\'teacher\',\'hod\')',
        (teacher_id,), one=True)
    if not teacher:
        abort(404)
    return render_template(
        'hod_performance_detail.html',
        user=user, teacher=teacher, rows=rows, branches=branches)


@bp.route('/performance/compare')
@role_required('hod')
def performance_compare():
    from . import scoring
    user = current_user()
    branches = _hod_branches(user)
    branch_ids = [b['id'] for b in branches]
    teacher_id = request.args.get('teacher_id', type=int)
    form_a = request.args.get('form_a', type=int)
    form_b = request.args.get('form_b', type=int)
    comparison = None
    error = None
    teacher_forms = []
    if teacher_id and branch_ids:
        teacher_forms = scoring.faculty_scores_for_branches(
            branch_ids, teacher_user_id=teacher_id)
    if teacher_id and form_a and form_b:
        if form_a == form_b:
            error = 'Select two different periods.'
        else:
            comparison = scoring.compare_periods(teacher_id, form_a, form_b)
            if not comparison.get('ok'):
                error = comparison.get('error', 'Comparison failed')
                comparison = None
    teachers = []
    if branch_ids:
        ph = ','.join('?' * len(branch_ids))
        teachers = db.query(
            f"""SELECT DISTINCT u.id, u.name, u.emp_code
                FROM users u
                JOIN class_sessions cs ON cs.teacher_user_id = u.id
                JOIN timetables t ON t.id = cs.timetable_id
                WHERE t.branch_id IN ({ph}) AND cs.is_deleted=0
                ORDER BY u.name""",
            tuple(branch_ids))
    return render_template(
        'hod_performance_compare.html',
        user=user, branches=branches, teachers=teachers,
        teacher_id=teacher_id, form_a=form_a, form_b=form_b,
        teacher_forms=teacher_forms, comparison=comparison, error=error)
