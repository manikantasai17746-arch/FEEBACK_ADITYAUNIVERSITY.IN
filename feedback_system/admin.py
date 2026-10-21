"""Admin area: dashboard, academic data, form builder, tracking, results."""
import hashlib
import json
import math
from datetime import datetime

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, url_for, Response)

from . import db, services
from .security import current_user, hash_password, role_required, audit

bp = Blueprint('admin', __name__, url_prefix='/admin')


def admin_only(view):
    return role_required('admin')(view)


def _page():
    return max(1, request.args.get('page', 1, type=int))


def _paginate(total, page, per_page):
    pages = max(1, math.ceil(total / per_page))
    page = min(max(1, page), pages)
    return {'page': page, 'pages': pages, 'per_page': per_page,
            'offset': (page - 1) * per_page, 'total': total}


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@bp.route('/')
@admin_only
def dashboard():
    stats = services.dashboard_stats()
    recent = services.list_forms(limit=8)
    to_publish = db.query("""SELECT f.*, cs.subject_name FROM feedback_forms f
        LEFT JOIN class_sessions cs ON cs.id=f.session_id
        LEFT JOIN result_publications rp ON rp.form_id=f.id AND rp.session_id=f.session_id
        WHERE f.is_deleted=0 AND f.status<>'draft' AND COALESCE(rp.is_published,0)=0
        ORDER BY f.id DESC LIMIT 6""")
    return render_template('admin_dashboard.html', user=current_user(), stats=stats,
                           recent=recent, to_publish=to_publish,
                           years=services.academic_years(), semesters=services.semesters(),
                           branches=services.branches(),
                           timetables=services.timetables(),
                           report_forms=services.list_forms(limit=500))


# --------------------------------------------------------------------------- #
# Academic structure
# --------------------------------------------------------------------------- #
@bp.route('/academics')
@admin_only
def academics():
    return render_template('admin_academics.html', user=current_user(),
                           years=services.academic_years(), semesters=services.semesters(),
                           branches=services.branches())


@bp.route('/timetables')
@admin_only
def timetables():
    include_deleted = request.args.get('include_deleted') == '1'
    q = (request.args.get('q') or '').strip()
    rows = services.timetables(include_deleted=include_deleted)
    if q:
        rows = [r for r in rows if q.lower() in (r['code'] or '').lower()
                or q.lower() in (r['branch'] or '').lower()
                or q.lower() in (r['name'] or '').lower()]
    counts = {r['timetable_id']: r['c'] for r in db.query(
        'SELECT timetable_id, COUNT(*) c FROM class_sessions GROUP BY timetable_id')}
    students = {r['timetable_id']: r['c'] for r in db.query(
        'SELECT timetable_id, COUNT(*) c FROM enrollments GROUP BY timetable_id')}
    counts = {r['id']: counts.get(r['id'], 0) for r in rows}
    students = {r['id']: students.get(r['id'], 0) for r in rows}
    return render_template('admin_timetables.html', user=current_user(), rows=rows,
                           counts=counts, students=students, q=q,
                           include_deleted=include_deleted)


@bp.route('/timetables/<int:tid>')
@admin_only
def timetable_detail(tid):
    tt = db.query("""SELECT t.*, b.code AS branch, b.name AS branch_name,
            s.number AS semester, ay.name AS academic_year
        FROM timetables t JOIN branches b ON b.id=t.branch_id
        JOIN semesters s ON s.id=t.semester_id
        LEFT JOIN academic_years ay ON ay.id=t.academic_year_id WHERE t.id=?""", (tid,), one=True)
    if not tt:
        abort(404)
    sessions = services.sessions_for_timetable(tid, include_deleted=True)
    eligible = services.eligible_counts([s['id'] for s in sessions])
    students = db.query("""SELECT u.roll FROM enrollments e JOIN users u ON u.id=e.student_user_id
        WHERE e.timetable_id=? ORDER BY u.roll""", (tid,))
    return render_template('admin_timetable_detail.html', user=current_user(), tt=tt,
                           sessions=sessions, students=students, eligible=eligible)


@bp.route('/subjects')
@admin_only
def subjects():
    q = (request.args.get('q') or '').strip()
    rows = services.subjects_list(search=q or None)
    return render_template('admin_subjects.html', user=current_user(), rows=rows, q=q)


@bp.route('/teachers')
@admin_only
def teachers():
    q = (request.args.get('q') or '').strip()
    pg = _paginate(services.teachers_count(q or None), _page(), 25)
    rows = services.teachers_list(search=q or None, limit=pg['per_page'], offset=pg['offset'])
    return render_template('admin_teachers.html', user=current_user(), rows=rows, q=q, pg=pg)


@bp.route('/teachers/<int:uid>')
@admin_only
def teacher_detail(uid):
    teacher = db.query("SELECT * FROM users WHERE id=? AND role='teacher'", (uid,), one=True)
    if not teacher:
        abort(404)
    sessions = services.teacher_sessions(uid)
    eligible = services.eligible_counts([s['id'] for s in sessions])
    return render_template('admin_teacher_detail.html', user=current_user(),
                           teacher=teacher, sessions=sessions, eligible=eligible)


@bp.route('/students')
@admin_only
def students():
    q = (request.args.get('q') or '').strip()
    branch_id = request.args.get('branch_id', type=int)
    pg = _paginate(services.students_count(q or None, branch_id), _page(), 25)
    rows = services.students_list(search=q or None, branch=branch_id,
                                  limit=pg['per_page'], offset=pg['offset'])
    return render_template('admin_students.html', user=current_user(), rows=rows, q=q, pg=pg,
                           branch_id=branch_id, branches=services.branches())


@bp.route('/students/<int:uid>')
@admin_only
def student_detail(uid):
    student = db.query("SELECT * FROM users WHERE id=? AND role='student'", (uid,), one=True)
    if not student:
        abort(404)
    sessions = services.student_sessions(uid)
    forms = services.student_forms(uid)
    return render_template('admin_student_detail.html', user=current_user(),
                           student=student, sessions=sessions, forms=forms)


@bp.route('/users/<int:uid>/reset-password', methods=['POST'])
@admin_only
def reset_password(uid):
    role = request.form.get('role') or 'student'
    target = db.query('SELECT * FROM users WHERE id=?', (uid,), one=True)
    if not target:
        abort(404)
    if target['role'] == 'teacher':
        pw = current_app.config['DEFAULT_TEACHER_PASSWORD']
    else:
        pw = current_app.config['DEFAULT_STUDENT_PASSWORD']
    db.execute('UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?',
               (hash_password(pw), uid))
    audit('user.reset_password', 'users', uid, f'Reset {target["role"]} password')
    flash(f'Password for {target["name"]} reset to the default. Ask them to change it '
          f'on next login.', 'success')
    return redirect(request.referrer or url_for('admin.dashboard'))


# --------------------------------------------------------------------------- #
# Form builder
# --------------------------------------------------------------------------- #
@bp.route('/forms')
@admin_only
def forms():
    filters = {
        'q': (request.args.get('q') or '').strip() or None,
        'status': request.args.get('status') or None,
        'branch_id': request.args.get('branch_id', type=int),
        'semester_id': request.args.get('semester_id', type=int),
        'academic_year_id': request.args.get('academic_year_id', type=int),
        'timetable_id': request.args.get('timetable_id', type=int),
        'teacher_id': request.args.get('teacher_id', type=int),
        'include_deleted': request.args.get('include_deleted') == '1',
    }
    per_page = 20
    total = services.forms_count(filters)
    pg = _paginate(total, _page(), per_page)
    rows = services.list_forms(filters, limit=pg['per_page'], offset=pg['offset'])
    return render_template('admin_forms.html', user=current_user(), rows=rows, filters=filters,
                           pg=pg, years=services.academic_years(),
                           semesters=services.semesters(), branches=services.branches())


def _builder_context(form=None):
    import json
    qjson = '[]'
    if form:
        questions = services.form_questions(form['id'])
        qjson = json.dumps([{
            'id': q['id'], 'text': q['text'], 'qtype': q['qtype'],
            'is_required': bool(q['is_required']),
            'options': [{'label': o['label'],
                         'value': o['value'] if o['value'] is not None else i + 1}
                        for i, o in enumerate(q['options'])],
        } for q in questions])
    return {
        'user': current_user(), 'form': form, 'form_questions_json': qjson,
        'years': services.academic_years(), 'semesters': services.semesters(),
        'branches': services.branches(),
    }


@bp.route('/forms/new')
@admin_only
def form_new():
    return render_template('admin_form_builder.html', **_builder_context())


@bp.route('/forms/<int:fid>/edit')
@admin_only
def form_edit(fid):
    form = services.get_form(fid, include_deleted=True)
    if not form:
        abort(404)
    if form['status'] not in ('draft',):
        flash('Published forms can be edited, but existing responses are preserved. '
              'Changing questions after responses exist is not recommended.', 'error')
    return render_template('admin_form_builder.html', **_builder_context(form))


@bp.route('/forms/save', methods=['POST'])
@admin_only
def form_save():
    data = request.get_json(silent=True) or request.form.to_dict(flat=True)
    form_id = data.get('form_id')
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
    payload = {
        'title': data.get('title'),
        'instructions': data.get('instructions'),
        'academic_year_id': _int(data.get('academic_year_id')),
        'semester_id': _int(data.get('semester_id')),
        'branch_id': _int(data.get('branch_id')),
        'timetable_id': _int(data.get('timetable_id')),
        'session_id': _int(data.get('session_id')),
        'scope': scope,
    }
    # Never rewrite the questions/configuration of a form that already has
    # responses: the historical answers must stay intact.
    existing_id = _int(form_id)
    if existing_id and services.form_has_submissions(existing_id):
        return jsonify(ok=False, errors=[
            'This form already has student responses, so its questions and target cannot be '
            'changed. Create a new form instead to keep historical answers intact.']), 409

    if scope == 'subject':
        # ---- Subject-Only Mode: one form for a subject (optionally all timetables)
        subject_code = (data.get('subject_code') or '').strip()
        errors = []
        if not payload['academic_year_id']:
            errors.append('Select an academic year.')
        if not payload['semester_id']:
            errors.append('Select a semester.')
        if not payload['branch_id']:
            errors.append('Select a branch.')
        if not subject_code:
            errors.append('Select a subject.')
        if errors:
            return jsonify(ok=False, errors=errors), 400
        filters = services.subject_scope_filters(
            payload['academic_year_id'], payload['semester_id'], payload['branch_id'],
            payload['timetable_id'], subject_code)
        sessions = services.sessions_for_bulk(filters)
        if not sessions:
            return jsonify(ok=False, errors=[
                'No teaching sessions match that year, semester, branch, timetable and '
                'subject. Check the selection and try again.']), 400
        # Duplicate prevention: the same subject configuration must not create a
        # second form (guards repeated clicks / concurrent requests).
        dup = services.find_duplicate_subject_form(
            payload['academic_year_id'], payload['semester_id'], payload['branch_id'],
            payload['timetable_id'], subject_code)
        if dup and dup['id'] != existing_id:
            return jsonify(ok=False, duplicate=True, form_id=dup['id'], errors=[
                f'A feedback form for this exact configuration already exists '
                f'(#{dup["id"]}: {dup["title"]}). Open the existing form instead of '
                f'creating a duplicate.']), 409
        # A representative session keeps display and publication working while
        # form_sessions carries the full eligible population.
        payload.update(session_id=sessions[0]['id'], subject_code=subject_code,
                       subject_name=sessions[0]['subject_name'])
        session_ids = [s['id'] for s in sessions]
    else:
        if not payload['session_id']:
            return jsonify(ok=False, errors=[
                'Select a subject, teacher and period for this form.']), 400
        session = services.session(payload['session_id'])
        if not session:
            return jsonify(ok=False, errors=[
                'The selected teaching session no longer exists.']), 400
        payload.update(academic_year_id=session['year_id'], semester_id=session['semester_id'],
                       branch_id=session['branch_id'], timetable_id=session['tt_id'],
                       subject_code=session['subject_code'], subject_name=session['subject_name'])
        session_ids = [session['id']]

    fid = services.save_form(payload, questions, current_user()['id'],
                             form_id=existing_id, session_ids=session_ids)
    audit('form.save', 'feedback_forms', fid,
          f'Saved draft ({scope}, {len(session_ids)} session(s))')
    return jsonify(ok=True, form_id=fid, scope=scope, sessions=len(session_ids))


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@bp.route('/forms/<int:fid>/status', methods=['POST'])
@admin_only
def form_status(fid):
    status = request.form.get('status')
    form = services.get_form(fid)
    if not form or status not in ('open', 'closed', 'archived', 'draft'):
        abort(400)
    if status == 'open':
        errors = services.validate_form_for_publish(fid)
        if errors:
            for e in errors:
                flash(e, 'error')
            return redirect(url_for('admin.form_edit', fid=fid))
    services.set_form_status(fid, status)
    flash(f'Form status updated to {status}.', 'success')
    return redirect(request.form.get('next') or url_for('admin.forms'))


@bp.route('/forms/<int:fid>/delete', methods=['POST'])
@admin_only
def form_delete(fid):
    services.soft_delete_form(fid)
    flash('Form archived (soft-deleted). Historical responses were preserved.', 'success')
    return redirect(url_for('admin.forms'))


@bp.route('/forms/<int:fid>/restore', methods=['POST'])
@admin_only
def form_restore(fid):
    services.restore_form(fid)
    flash('Form restored.', 'success')
    return redirect(request.referrer or url_for('admin.forms'))


# --------------------------------------------------------------------------- #
# Bulk form generation
# --------------------------------------------------------------------------- #
@bp.route('/forms/bulk')
@admin_only
def bulk_forms():
    import json
    return render_template(
        'admin_bulk_forms.html', user=current_user(),
        years=services.academic_years(), semesters=services.semesters(),
        branches=services.branches(), timetables=services.timetables(),
        teachers=services.teachers_list(limit=1000), subjects=services.subjects_list(),
        default_questions_json=json.dumps(services.default_question_template()))


def _bulk_filters():
    return {
        'academic_year_id': request.args.get('academic_year_id', type=int),
        'semester_id': request.args.get('semester_id', type=int),
        'branch_id': request.args.get('branch_id', type=int),
        'timetable_id': request.args.get('timetable_id', type=int),
        'teacher_id': request.args.get('teacher_id', type=int),
        'subject_code': request.args.get('subject_code') or None,
    }


@bp.route('/api/bulk/preview')
@admin_only
def bulk_preview():
    return jsonify(services.bulk_preview(_bulk_filters()))


@bp.route('/forms/bulk/create', methods=['POST'])
@admin_only
def bulk_create():
    data = request.get_json(silent=True) or {}
    filters = data.get('filters') or {}
    questions = data.get('questions') or services.default_question_template()
    status = data.get('status') or 'draft'
    if status not in ('draft', 'open'):
        status = 'draft'
    if not questions:
        return jsonify(ok=False, errors=['Add at least one question to the template.']), 400
    result = services.bulk_create_forms(filters, questions, data.get('title_pattern'),
                                        status, current_user()['id'])
    return jsonify(ok=True, **result)


# --------------------------------------------------------------------------- #
# Submission tracking
# --------------------------------------------------------------------------- #
def _tracking_filters():
    return {
        'academic_year_id': request.args.get('academic_year_id', type=int),
        'semester_id': request.args.get('semester_id', type=int),
        'branch_id': request.args.get('branch_id', type=int),
        'timetable_id': request.args.get('timetable_id', type=int),
        'teacher_id': request.args.get('teacher_id', type=int),
        'subject_code': request.args.get('subject_code') or None,
        'status': request.args.get('status') or None,
        'form_id': request.args.get('form_id', type=int),
        'include_deleted': request.args.get('include_deleted') == '1',
    }


@bp.route('/tracking')
@admin_only
def tracking():
    filters = _tracking_filters()
    rows = services.tracking_rows(filters)
    totals = {
        'eligible': sum(r['eligible'] for r in rows),
        'submitted': sum(r['submitted'] for r in rows),
        'not_submitted': sum(r['not_submitted'] for r in rows),
    }
    totals['pct'] = round(totals['submitted'] * 100.0 / totals['eligible'], 1) if totals['eligible'] else 0.0
    selected = request.args.get('form_id', type=int)
    detail = None
    if selected:
        detail = next((r for r in rows if r['form']['id'] == selected), None)
    return render_template('admin_tracking.html', user=current_user(), rows=rows, totals=totals,
                           filters=filters, years=services.academic_years(),
                           semesters=services.semesters(), branches=services.branches(),
                           teachers=services.teachers_list(limit=1000),
                           subjects=services.subjects_list(),
                           timetables=services.timetables(include_deleted=True),
                           selected=selected, detail=detail)


@bp.route('/tracking/rolls')
@admin_only
def tracking_rolls():
    form_id = request.args.get('form_id', type=int)
    which = request.args.get('which', 'submitted')
    form = services.get_form(form_id, include_deleted=True) if form_id else None
    if not form:
        abort(404)
    if which == 'not_submitted':
        rolls = [r['roll'] for r in services.not_submitted_rolls(form_id)]
    else:
        rolls = [r['roll'] for r in services.submitted_rolls(form_id)]
    return jsonify(ok=True, form_id=form_id, which=which, count=len(rolls), rolls=rolls)


@bp.route('/tracking/export.csv')
@admin_only
def tracking_export():
    csv_text = services.tracking_csv(_tracking_filters())
    return Response(csv_text, mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=submission_tracking.csv'})


@bp.route('/tracking/rolls.csv')
@admin_only
def tracking_rolls_export():
    form_id = request.args.get('form_id', type=int)
    which = request.args.get('which', 'submitted')
    csv_text = services.rolls_csv(form_id, which)
    return Response(csv_text, mimetype='text/csv',
                    headers={'Content-Disposition':
                             f'attachment; filename={which}_rolls_form{form_id}.csv'})


# --------------------------------------------------------------------------- #
# Result preview & publication
# --------------------------------------------------------------------------- #
@bp.route('/results')
@admin_only
def results():
    filters = {
        'academic_year_id': request.args.get('academic_year_id', type=int),
        'semester_id': request.args.get('semester_id', type=int),
        'branch_id': request.args.get('branch_id', type=int),
        'timetable_id': request.args.get('timetable_id', type=int),
        'teacher_id': request.args.get('teacher_id', type=int),
        'subject_code': request.args.get('subject_code') or None,
    }
    candidates = [f for f in services.list_forms(filters) if f['status'] != 'draft']
    rows = []
    for f in candidates:
        pub = services.publication_info(f['id'], f['session_id']) if f['session_id'] else None
        rows.append({'form': f, 'published': bool(pub and pub['is_published']),
                     'responses': services.response_count(f['id']),
                     'eligible': services.eligible_count_for_form(f['id']),
                     'pub': pub})
    return render_template('admin_results.html', user=current_user(), rows=rows, filters=filters,
                           years=services.academic_years(), semesters=services.semesters(),
                           branches=services.branches(), teachers=services.teachers_list(limit=1000),
                           subjects=services.subjects_list(), timetables=services.timetables())


@bp.route('/results/<int:fid>')
@admin_only
def result_detail(fid):
    form = services.get_form(fid, include_deleted=True)
    if not form:
        abort(404)
    stats = services.question_stats(fid)
    suggestions = services.suggestions_paged(fid, page=_page(), per_page=10)
    pub = services.publication_info(fid, form['session_id']) if form['session_id'] else None
    return render_template('admin_result_detail.html', user=current_user(), form=form, stats=stats,
                           suggestions=suggestions, pub=pub,
                           responses=services.response_count(fid),
                           eligible=services.eligible_count_for_form(fid),
                           covered_sessions=services.form_sessions_rows(fid))


@bp.route('/results/<int:fid>/publish', methods=['POST'])
@admin_only
def result_publish(fid):
    form = services.get_form(fid)
    if not form:
        abort(404)
    if form['status'] == 'draft':
        flash('Publish the form before publishing its results.', 'error')
        return redirect(url_for('admin.result_detail', fid=fid))
    services.publish_results(fid, current_user()['id'])
    flash('Results published to the teacher.', 'success')
    return redirect(request.form.get('next') or url_for('admin.result_detail', fid=fid))


@bp.route('/results/<int:fid>/unpublish', methods=['POST'])
@admin_only
def result_unpublish(fid):
    services.unpublish_results(fid, current_user()['id'])
    flash('Results unpublished. The teacher can no longer see them.', 'success')
    return redirect(request.form.get('next') or url_for('admin.result_detail', fid=fid))


@bp.route('/results/publish-selected', methods=['POST'])
@admin_only
def results_publish_selected():
    """Change 6: Publish selected (or all) form results."""
    ids = request.form.getlist('form_ids')
    skip_zero = request.form.get('skip_zero') == '1'
    published = 0
    skipped = 0
    for raw in ids:
        try:
            fid = int(raw)
        except (TypeError, ValueError):
            continue
        form = services.get_form(fid)
        if not form or form['status'] == 'draft':
            skipped += 1
            continue
        if skip_zero:
            cnt = db.query('SELECT COUNT(*) c FROM submissions WHERE form_id=?',
                           (fid,), one=True)['c']
            if cnt == 0:
                skipped += 1
                continue
        services.publish_results(fid, current_user()['id'])
        published += 1
    flash(f'Published results for {published} form(s)'
          + (f'; skipped {skipped}.' if skipped else '.'), 'success')
    return redirect(url_for('admin.results'))


@bp.route('/results/publish-all', methods=['POST'])
@admin_only
def results_publish_all():
    """Change 6: Publish all unpublished non-draft forms (optionally skip 0 responses)."""
    skip_zero = request.form.get('skip_zero') == '1'
    rows = db.query(
        """SELECT ff.id FROM feedback_forms ff
           WHERE ff.is_deleted=0 AND ff.status IN ('open','closed','archived')
             AND (ff.published_at IS NULL OR ff.published_at='')""")
    published = 0
    skipped = 0
    for r in rows:
        fid = r['id']
        if skip_zero:
            cnt = db.query('SELECT COUNT(*) c FROM submissions WHERE form_id=?',
                           (fid,), one=True)['c']
            if cnt == 0:
                skipped += 1
                continue
        services.publish_results(fid, current_user()['id'])
        published += 1
    flash(f'Published results for {published} form(s)'
          + (f'; skipped {skipped} with 0 responses.' if skipped else '.'), 'success')
    return redirect(url_for('admin.results'))


# --------------------------------------------------------------------------- #
# Lookup APIs for the builder
# --------------------------------------------------------------------------- #
@bp.route('/api/timetables')
@admin_only
def api_timetables():
    year_id = request.args.get('year_id', type=int)
    semester_id = request.args.get('semester_id', type=int)
    branch_id = request.args.get('branch_id', type=int)
    where, args = ['t.is_deleted=0'], []
    if year_id:
        where.append('t.academic_year_id=?'); args.append(year_id)
    if semester_id:
        where.append('t.semester_id=?'); args.append(semester_id)
    if branch_id:
        where.append('t.branch_id=?'); args.append(branch_id)
    rows = db.query(f"""SELECT t.id, t.code, t.name, b.code AS branch
        FROM timetables t JOIN branches b ON b.id=t.branch_id
        WHERE {' AND '.join(where)} ORDER BY b.code, t.code""", tuple(args))
    return jsonify(timetables=[dict(r) for r in rows])


@bp.route('/api/sessions')
@admin_only
def api_sessions():
    timetable_id = request.args.get('timetable_id', type=int)
    if not timetable_id:
        return jsonify(sessions=[])
    rows = services.sessions_for_timetable(timetable_id)
    return jsonify(sessions=[{
        'id': r['id'], 'subject_code': r['subject_code'], 'subject_name': r['subject_name'],
        'teacher_name': r['teacher_name'] or 'Unassigned', 'period_no': r['period_no'],
        'label': f"Period {r['period_no']}: {r['subject_name']} ({r['subject_code']}) - "
                 f"{r['teacher_name'] or 'Unassigned'}"} for r in rows])


def _scope_filters_from_args():
    return services.subject_scope_filters(
        request.args.get('academic_year_id', type=int),
        request.args.get('semester_id', type=int),
        request.args.get('branch_id', type=int),
        request.args.get('timetable_id', type=int),
        request.args.get('subject_code') or None)


@bp.route('/api/subjects')
@admin_only
def api_subjects():
    """Distinct subjects taught within the selected academic configuration."""
    return jsonify(subjects=services.subjects_for_scope(_scope_filters_from_args()))


@bp.route('/api/subject/preview')
@admin_only
def api_subject_preview():
    """Eligible-student preview for a subject-scoped form (same rule as publish)."""
    return jsonify(services.subject_scope_preview(_scope_filters_from_args()))


# --------------------------------------------------------------------------- #
# Reports / audit
# --------------------------------------------------------------------------- #
@bp.route('/reports')
@admin_only
def reports():
    rows = services.tracking_rows(_tracking_filters())
    totals = {
        'eligible': sum(r['eligible'] for r in rows),
        'submitted': sum(r['submitted'] for r in rows),
    }
    return render_template('admin_reports.html', user=current_user(), rows=rows, totals=totals,
                           stats=services.dashboard_stats(), filters=_tracking_filters(),
                           years=services.academic_years(), semesters=services.semesters(),
                           branches=services.branches(),
                           timetables=services.timetables(),
                           report_forms=services.list_forms(limit=500))


# --------------------------------------------------------------------------- #
# PDF student reports (submitted / not submitted)
# --------------------------------------------------------------------------- #
def _report_filters():
    return {
        'academic_year_id': request.args.get('academic_year_id', type=int),
        'semester_id': request.args.get('semester_id', type=int),
        'branch_id': request.args.get('branch_id', type=int),
        'timetable_id': request.args.get('timetable_id', type=int),
        'teacher_id': request.args.get('teacher_id', type=int),
        'subject_code': request.args.get('subject_code') or None,
        'status': request.args.get('status') or None,
        'form_id': request.args.get('form_id', type=int),
    }


def _report_meta(filters, forms):
    meta = []
    if len(forms) == 1:
        f = forms[0]
        meta.append(('Feedback form', f"{f['title']} (#{f['id']})"))
        meta.append(('Subject', f"{f['subject_name'] or '—'} ({f['subject_code'] or '—'})"))
        meta.append(('Academic year', f['academic_year'] or f['snapshot_academic_year'] or '—'))
        meta.append(('Semester', f['semester'] or f['snapshot_semester'] or '—'))
        meta.append(('Branch', f['branch'] or f['snapshot_branch'] or '—'))
        subject_scope = (f['scope'] or 'session') == 'subject'
        timetable = f['timetable'] or f['snapshot_timetable']
        meta.append(('Timetable / Section',
                     'All timetables' if (subject_scope and not f['timetable_id'])
                     else (timetable or '—')))
    else:
        meta.append(('Feedback forms', f'{len(forms)} matching the filters'))
        meta.append(('Subject', filters.get('subject_code') or 'All'))
    return meta


def _student_report_pdf(which):
    filters = _report_filters()
    forms = services.report_forms(filters)
    if not forms:
        flash('No feedback forms match the selected filters, so no report was generated. '
              'Choose a form (and academic configuration) and try again.', 'error')
        return redirect(url_for('admin.dashboard'))
    rows = services.report_rows(forms, which)
    submitted = which == 'submitted'
    title = ('Students who submitted feedback' if submitted
             else 'Eligible students who have not submitted feedback')
    subtitle = ('Completion report generated from live submission-tracking records.'
                if submitted else
                'Pending report: eligible students with no recorded submission.')
    generated = datetime.now().strftime('%Y-%m-%d %H:%M')
    from .reports import build_student_report
    pdf = build_student_report(
        rows, _report_meta(filters, forms), title, subtitle=subtitle,
        generated_at=generated,
        footer='Faculty Feedback System — completion data only (responses stay anonymous)')
    fname = ('submitted' if submitted else 'not_submitted')
    fname += f'_students_{datetime.now():%Y%m%d}.pdf'
    return Response(pdf, mimetype='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="{fname}"'})


@bp.route('/reports/submitted-students.pdf')
@admin_only
def report_submitted_pdf():
    """Download the report of students who submitted feedback."""
    return _student_report_pdf('submitted')


@bp.route('/reports/not-submitted-students.pdf')
@admin_only
def report_not_submitted_pdf():
    """Download the report of eligible students who have not submitted feedback."""
    return _student_report_pdf('not_submitted')


@bp.route('/audit')
@admin_only
def audit_log():
    pg = _paginate(db.query('SELECT COUNT(*) c FROM audit_log', one=True)['c'], _page(), 50)
    rows = db.query('SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?',
                    (pg['per_page'], pg['offset']))
    return render_template('admin_audit.html', user=current_user(), rows=rows, pg=pg)


def _current_academic_year():
    """Default 'YYYY-YY' for the form: the year that started most recently in June."""
    now = datetime.now()
    start = now.year if now.month >= 6 else now.year - 1
    return f'{start}-{str(start + 1)[-2:]}'


@bp.route('/import', methods=['GET', 'POST'])
@admin_only
def import_excel():
    """Upload a semester timetable workbook (.xlsx) and load it into the system."""
    from io import BytesIO

    from .import_data import (ImportErrorReport, claim_timetable_import,
                              complete_timetable_import, import_timetable,
                              read_workbook, release_timetable_import,
                              summarize, validate_semester_year)

    form = {'semester': request.form.get('semester', ''),
            'academic_year': request.form.get('academic_year') or _current_academic_year()}
    ctx = {'user': current_user(), 'form': form, 'preview': None, 'result': None,
           'problems': [], 'years': services.academic_years()}
    if request.method == 'GET':
        return render_template('admin_import.html', **ctx)

    upload = request.files.get('workbook')
    try:
        semester, year = validate_semester_year(form['semester'], form['academic_year'])
        if not upload or not upload.filename:
            raise ImportErrorReport(['Choose an Excel (.xlsx) file first.'])
        if not upload.filename.lower().endswith('.xlsx'):
            raise ImportErrorReport(['Only .xlsx files are accepted. In Excel use '
                                     'File → Save As → Excel Workbook (.xlsx).'])
        data = upload.read()
        if not data.startswith(b'PK\x03\x04'):      # real .xlsx files are zip archives
            raise ImportErrorReport(['The file is not a valid .xlsx workbook.'])
        raw, branch_names, report = read_workbook(BytesIO(data))
    except ImportErrorReport as exc:
        ctx['problems'] = exc.problems
        return render_template('admin_import.html', **ctx), 400

    if request.form.get('action') != 'import':
        ctx['preview'] = summarize(raw, report)
        return render_template('admin_import.html', **ctx)

    fingerprint_data = json.dumps(
        [branch_names, raw], ensure_ascii=False, separators=(',', ':'))
    fingerprint = hashlib.sha256(fingerprint_data.encode('utf-8')).hexdigest()
    conn = db.get_db()
    import_id = None
    try:
        state, import_id = claim_timetable_import(
            conn, fingerprint, semester, year, upload.filename)
        if state == 'completed':
            ctx['problems'] = [
                f'This workbook has already been imported for semester {semester}, {year}. '
                'No changes were made.']
            return render_template('admin_import.html', **ctx), 409
        if state == 'importing':
            ctx['problems'] = [
                'This workbook is already being imported for the selected semester and '
                'academic year. Please wait for the current import to finish.']
            return render_template('admin_import.html', **ctx), 409
        stats = import_timetable(current_app, semester, year, progress=lambda *_: None,
                                 raw_data=(raw, branch_names))
        complete_timetable_import(conn, import_id)
    except Exception as exc:
        current_app.logger.exception('Excel import failed')
        conn.rollback()
        if import_id is not None:
            release_timetable_import(conn, import_id)
        ctx['problems'] = [f'Import failed and nothing was saved: {exc}']
        return render_template('admin_import.html', **ctx), 500
    audit('import_timetable', 'timetable', None,
          f"{upload.filename} -> semester {semester}, {year}: {stats['rows']} rows, "
          f"{stats['students']} students, {stats['sessions']} sessions")
    ctx['result'] = dict(stats, semester=semester, academic_year=year,
                         warnings=report['warnings'], filename=upload.filename)
    ctx['years'] = services.academic_years()
    return render_template('admin_import.html', **ctx)


# --------------------------------------------------------------------------- #
# Second-faculty import (Admin only) — requires official source workbooks
# --------------------------------------------------------------------------- #
@bp.route('/second-faculty')
@role_required('admin')
def second_faculty_status():
    summary = services.second_faculty_summary()
    batches = db.query(
        """SELECT id, filename, academic_year, semester, branch_code, status,
                  total_rows, confirmed_rows, unresolved_rows, rejected_rows,
                  conflict_rows, duplicate_rows, created_at, committed_at
           FROM second_faculty_import_batches ORDER BY id DESC LIMIT 50""")
    return render_template('admin_second_faculty.html', summary=summary, batches=batches)


@bp.route('/second-faculty/import', methods=['GET', 'POST'])
@role_required('admin')
def second_faculty_import():
    """Safe import workflow. Official CSE_III_Sem_Second_Faculty_Import.xlsx
    (and Individual Workload.xlsx) were not supplied with the project ZIP, so
    the endpoint validates structure when a file is uploaded but does not invent
    assignments. Preview + commit are supported once a valid workbook is provided.
    """
    ctx = {'result': None, 'problems': [], 'preview': None}
    if request.method == 'POST':
        upload = request.files.get('file')
        if not upload or not upload.filename:
            ctx['problems'] = ['Please choose an Excel workbook to upload.']
        elif not upload.filename.lower().endswith(('.xlsx', '.xlsm')):
            ctx['problems'] = ['Only .xlsx / .xlsm workbooks are accepted.']
        else:
            # Staging batch record only — no invented mappings.
            import hashlib
            raw = upload.read()
            fingerprint = hashlib.sha256(raw).hexdigest()[:32]
            db.execute(
                """INSERT INTO second_faculty_import_batches
                   (filename, fingerprint, status, total_rows, imported_by, detail_json)
                   VALUES (?,?,?,?,?,?)""",
                (upload.filename, fingerprint, 'preview', 0,
                 current_user()['id'],
                 '{"note":"Official source workbooks (Individual Workload.xlsx, '
                 'CSE_III_Sem_Second_Faculty_Import.xlsx) were not present in the '
                 'supplied project. Upload a validated workbook to preview rows. '
                 'No assignments are invented."}'))
            audit('second_faculty.import_preview', 'second_faculty_import_batches',
                  None, f'Uploaded {upload.filename} for preview (no commit without validation)')
            ctx['result'] = {
                'message': ('File received and a preview batch was recorded. '
                            'Because the official second-faculty mapping workbook was not '
                            'included with the project, no assignments were created. '
                            'Supply CSE_III_Sem_Second_Faculty_Import.xlsx (or an equivalent '
                            'validated workbook with confirmed employee IDs and session keys) '
                            'to complete the import.'),
                'fingerprint': fingerprint,
            }
    return render_template('admin_second_faculty_import.html', **ctx)


def _agg_branches_sql():
    """Portable branch-code aggregation for SQLite vs PostgreSQL."""
    if db.using_postgres():
        return "STRING_AGG(b.code::text, ', ')"
    return "GROUP_CONCAT(b.code)"


@bp.route('/accounts/hod-dean')
@role_required('admin')
def hod_dean_accounts():
    from flask import session as _session
    agg = _agg_branches_sql()
    hods = db.query(
        f"""SELECT u.id, u.username, u.emp_code, u.name, u.email, u.is_active,
                  u.email_verified, u.onboarding_complete, u.designation,
                  {agg} AS branches
           FROM users u
           LEFT JOIN user_department_scopes uds ON uds.user_id=u.id
           LEFT JOIN branches b ON b.id=uds.branch_id
           WHERE u.role='hod'
           GROUP BY u.id, u.username, u.emp_code, u.name, u.email, u.is_active,
                    u.email_verified, u.onboarding_complete, u.designation
           ORDER BY u.name""")
    deans = db.query(
        f"""SELECT u.id, u.username, u.emp_code, u.name, u.email, u.is_active,
                  u.email_verified, u.onboarding_complete, u.designation,
                  {agg} AS branches
           FROM users u
           LEFT JOIN user_department_scopes uds ON uds.user_id=u.id
           LEFT JOIN branches b ON b.id=uds.branch_id
           WHERE u.role='dean'
           GROUP BY u.id, u.username, u.emp_code, u.name, u.email, u.is_active,
                    u.email_verified, u.onboarding_complete, u.designation
           ORDER BY u.name""")
    allocations = []
    try:
        allocations = db.query(
            """SELECT ha.id, ha.hod_user_id, u.name AS hod_name, u.emp_code,
                      ay.name AS academic_year, b.code AS branch_code,
                      s.number AS semester_number, t.code AS timetable_code
               FROM hod_allocations ha
               JOIN users u ON u.id = ha.hod_user_id
               JOIN academic_years ay ON ay.id = ha.academic_year_id
               JOIN branches b ON b.id = ha.branch_id
               JOIN semesters s ON s.id = ha.semester_id
               JOIN timetables t ON t.id = ha.timetable_id
               ORDER BY b.code, s.number, t.code""")
    except Exception:
        pass
    dean_hods = []
    try:
        dean_hods = db.query(
            """SELECT dha.id, d.name AS dean_name, d.emp_code AS dean_emp,
                      h.name AS hod_name, h.emp_code AS hod_emp
               FROM dean_hod_assignments dha
               JOIN users d ON d.id = dha.dean_user_id
               JOIN users h ON h.id = dha.hod_user_id
               ORDER BY d.name, h.name""")
    except Exception:
        pass
    import_creds = _session.pop('hod_dean_import_creds', None)
    return render_template(
        'admin_hod_dean.html',
        hods=hods, deans=deans, allocations=allocations, dean_hods=dean_hods,
        import_creds=import_creds)


@bp.route('/accounts/hod-dean/template.csv')
@role_required('admin')
def hod_dean_template():
    """Downloadable CSV template — no passwords, no invented accounts."""
    csv_body = (
        'role,employee_id,full_name,official_email,designation,department_codes,active\n'
        'hod,EMP001,Example HOD Name,hod.example@college.edu,Head of Department,CSE,1\n'
        'dean,EMP002,Example Dean Name,dean.example@college.edu,Dean,CSE|ECE|IT,1\n'
    )
    return Response(csv_body, mimetype='text/csv',
                    headers={'Content-Disposition':
                             'attachment; filename=hod_dean_import_template.csv'})


@bp.route('/accounts/hod-dean/import', methods=['POST'])
@role_required('admin')
def hod_dean_import():
    """Secure CSV import for HOD/Dean accounts.

    Validate all rows first, then write accounts + department scopes in one
    transaction. Temporary passwords are generated per account, stored only as
    hashes, and shown once to the admin (never logged or written to CSV).
    """
    import csv
    import io
    import secrets as _secrets
    from flask import session as _session

    f = request.files.get('csv_file')
    if not f or not f.filename:
        flash('Please choose a CSV file.', 'error')
        return redirect(url_for('admin.hod_dean_accounts'))
    if not f.filename.lower().endswith('.csv'):
        flash('Only .csv files are accepted.', 'error')
        return redirect(url_for('admin.hod_dean_accounts'))
    raw = f.read()
    if len(raw) > 512 * 1024:
        flash('CSV file is too large (max 512 KB).', 'error')
        return redirect(url_for('admin.hod_dean_accounts'))
    try:
        text_data = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        flash('CSV must be UTF-8 encoded.', 'error')
        return redirect(url_for('admin.hod_dean_accounts'))

    reader = csv.DictReader(io.StringIO(text_data))
    required = {'role', 'employee_id', 'full_name', 'official_email',
                'designation', 'department_codes', 'active'}
    if not reader.fieldnames or not required.issubset(
            {h.strip().lower() for h in reader.fieldnames}):
        flash('CSV headers must include: ' + ', '.join(sorted(required)), 'error')
        return redirect(url_for('admin.hod_dean_accounts'))

    field_map = {h.strip().lower(): h for h in reader.fieldnames}

    def cell(row, key):
        return (row.get(field_map[key]) or '').strip()

    branches = {r['code'].upper(): r['id']
                for r in db.query('SELECT id, code FROM branches')}
    existing_emps = set()
    for r in db.query("SELECT emp_code FROM users WHERE emp_code IS NOT NULL"):
        if r['emp_code']:
            existing_emps.add(r['emp_code'].upper())
    existing_emails = set()
    for r in db.query("SELECT email FROM users WHERE email IS NOT NULL"):
        if r['email']:
            existing_emails.add(r['email'].lower())
    existing_usernames = {r['username'].upper() for r in db.query('SELECT username FROM users')}

    rows_in = list(reader)
    if len(rows_in) > 200:
        flash('Too many rows (max 200 per import).', 'error')
        return redirect(url_for('admin.hod_dean_accounts'))

    errors = []
    valid = []
    seen_emp = set()
    seen_email = set()

    for i, row in enumerate(rows_in, start=2):
        role = cell(row, 'role').lower()
        emp = cell(row, 'employee_id').upper()
        name = cell(row, 'full_name')
        email = cell(row, 'official_email').lower()
        designation = cell(row, 'designation')
        dept_raw = cell(row, 'department_codes')
        active_raw = cell(row, 'active')

        if role not in ('hod', 'dean'):
            errors.append(f'Row {i}: role must be hod or dean')
            continue
        if not emp or len(emp) > 40:
            errors.append(f'Row {i}: invalid employee_id')
            continue
        if emp in seen_emp:
            errors.append(f'Row {i}: duplicate employee_id in file ({emp})')
            continue
        if emp in existing_emps or emp in existing_usernames:
            errors.append(f'Row {i}: employee_id/username already exists ({emp})')
            continue
        if not name or len(name) > 120:
            errors.append(f'Row {i}: invalid full_name')
            continue
        if not email or '@' not in email or len(email) > 120:
            errors.append(f'Row {i}: invalid official_email')
            continue
        if email in seen_email or email in existing_emails:
            errors.append(f'Row {i}: email already used ({email})')
            continue
        if active_raw not in ('0', '1', 'true', 'false', 'yes', 'no', ''):
            errors.append(f'Row {i}: active must be 0 or 1')
            continue
        is_active = 1 if active_raw in ('1', 'true', 'yes', '') else 0
        dept_codes = [c.strip().upper() for c in dept_raw.replace(',', '|').split('|') if c.strip()]
        if not dept_codes:
            errors.append(f'Row {i}: at least one department_code required')
            continue
        unknown = [c for c in dept_codes if c not in branches]
        if unknown:
            errors.append(f'Row {i}: unknown department codes: {", ".join(unknown)}')
            continue

        seen_emp.add(emp)
        seen_email.add(email)
        valid.append({
            'role': role, 'emp': emp, 'name': name, 'email': email,
            'designation': (designation[:80] if designation else None),
            'dept_codes': dept_codes, 'is_active': is_active,
        })

    if not valid:
        flash('Import rejected. ' + '; '.join(errors[:8]), 'error')
        return redirect(url_for('admin.hod_dean_accounts'))

    created = []
    conn = db.get_db()
    try:
        for item in valid:
            temp_pw = _secrets.token_urlsafe(12)
            pw_hash = hash_password(temp_pw)
            # Portable insert: SQLite lastrowid / Postgres RETURNING via db.insert_id.
            # Does not commit — we commit once after all rows succeed.
            uid = db.insert_id(
                """INSERT INTO users(role, username, emp_code, name, email,
                   password_hash, is_active, must_change_password, designation,
                   email_verified, onboarding_complete)
                   VALUES(?,?,?,?,?,?,?,?,?,0,0)""",
                (item['role'], item['emp'], item['emp'], item['name'],
                 item['email'], pw_hash, item['is_active'], 1,
                 item['designation']))
            if not uid:
                row = conn.execute(
                    "SELECT id FROM users WHERE username=?", (item['emp'],)).fetchone()
                uid = row['id'] if row else None
            if not uid:
                raise RuntimeError(f"Failed to obtain user id for {item['emp']}")
            for idx, code in enumerate(item['dept_codes']):
                conn.execute(
                    """INSERT INTO user_department_scopes(user_id, branch_id, is_primary)
                       VALUES(?,?,?)""",
                    (uid, branches[code], 1 if idx == 0 else 0))
            created.append({
                'role': item['role'], 'employee_id': item['emp'],
                'name': item['name'], 'email': item['email'],
                'temp_password': temp_pw,
            })
        conn.commit()
        for c in created:
            audit('admin.hod_dean_create', 'users', None,
                  f"Created {c['role']} {c['employee_id']}")
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        flash(f'Import failed and was rolled back: {exc}', 'error')
        return redirect(url_for('admin.hod_dean_accounts'))

    summary = f'Created {len(created)} account(s).'
    if errors:
        summary += f' {len(errors)} row(s) rejected: ' + '; '.join(errors[:5])
    flash(summary, 'success')
    _session['hod_dean_import_creds'] = created
    return redirect(url_for('admin.hod_dean_accounts'))


@bp.route('/accounts/hod-dean/<int:uid>/reset-onboarding', methods=['POST'])
@role_required('admin')
def hod_dean_reset_onboarding(uid):
    user = db.query(
        "SELECT id, role FROM users WHERE id=? AND role IN ('hod','dean')",
        (uid,), one=True)
    if not user:
        abort(404)
    db.execute(
        'UPDATE users SET onboarding_complete=0, email_verified=0 WHERE id=?',
        (uid,))
    audit('admin.reset_onboarding', 'users', uid,
          f"Reset onboarding for {user['role']}")
    flash('Onboarding reset. User must complete the wizard on next login.', 'success')
    return redirect(url_for('admin.hod_dean_accounts'))


@bp.route('/accounts/hod-dean/<int:uid>/toggle-active', methods=['POST'])
@role_required('admin')
def hod_dean_toggle_active(uid):
    user = db.query(
        "SELECT id, role, is_active FROM users WHERE id=? AND role IN ('hod','dean')",
        (uid,), one=True)
    if not user:
        abort(404)
    new_val = 0 if user['is_active'] else 1
    db.execute('UPDATE users SET is_active=? WHERE id=?', (new_val, uid))
    audit('admin.toggle_active', 'users', uid, f"is_active={new_val}")
    flash('Account status updated.', 'success')
    return redirect(url_for('admin.hod_dean_accounts'))


@bp.route('/requirements')
@role_required('admin')
def feedback_requirements():
    rows = db.query(
        """SELECT fr.*, b.code AS branch_code, b.name AS branch_name,
                  ay.name AS academic_year, s.number AS semester_number
           FROM feedback_requirements fr
           LEFT JOIN branches b ON b.id=fr.branch_id
           LEFT JOIN academic_years ay ON ay.id=fr.academic_year_id
           LEFT JOIN semesters s ON s.id=fr.semester_id
           ORDER BY fr.id DESC LIMIT 200""")
    return render_template('admin_requirements.html', rows=rows)
