"""HOD Feedback modules (Main + Lab) and shared helpers.

Main (Module A): category=main, keyed by class_sessions.id.
Lab (Module C): category=lab, keyed by class_sessions.id, only sessions with
teacher_subject_settings.lab_status = available (Phase 5B).
"""
from __future__ import annotations

import json

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template,
                   request, url_for)

from . import category_forms, db, services
from .feedback_categories import (LAB, MAIN, SECOND, ScopeError, eligible_count,
                                   eligible_students, resolve_scope)
from .security import (audit, current_user, role_required,
                       scoped_branch_ids_or_none, scoped_branches)

bp = Blueprint('hod_feedback', __name__, url_prefix='/hod/feedback')


def _hod_or_abort():
    user = current_user()
    if not user or user['role'] != 'hod':
        abort(403)
    branches = scoped_branches(user)
    if not branches:
        return user, [], []
    return user, branches, [b['id'] for b in branches]


def _session_in_scope(user, session_id):
    if not session_id:
        return None
    try:
        sid = int(session_id)
    except (TypeError, ValueError):
        return None
    row = services.session(sid)
    if not row:
        return None
    row = dict(row)
    allowed = scoped_branch_ids_or_none(user) or []
    if row.get('branch_id') not in allowed:
        return None
    return row


def _list_main_sessions(branch_ids, subject_q=None, section_q=None, faculty_q=None):
    if not branch_ids:
        return []
    ph = ','.join('?' * len(branch_ids))
    args = list(branch_ids)
    where = [f't.branch_id IN ({ph})', 'cs.is_deleted=0', 'cs.teacher_user_id IS NOT NULL']
    if subject_q:
        where.append('(cs.subject_code LIKE ? OR cs.subject_name LIKE ?)')
        like = f'%{subject_q}%'
        args.extend([like, like])
    if section_q:
        where.append('(t.code LIKE ? OR t.name LIKE ?)')
        like = f'%{section_q}%'
        args.extend([like, like])
    if faculty_q:
        where.append('(u.name LIKE ? OR u.emp_code LIKE ?)')
        like = f'%{faculty_q}%'
        args.extend([like, like])
    sql = f"""
        SELECT cs.id AS session_id, cs.subject_code, cs.subject_name, cs.period_no,
               u.id AS teacher_user_id, u.name AS teacher_name, u.emp_code AS teacher_emp_code,
               t.id AS timetable_id, t.code AS section_code, t.name AS section_name,
               t.branch_id, b.code AS branch_code, b.name AS branch_name,
               s.room, s.lab_status,
               (SELECT COUNT(*) FROM session_students ss
                  JOIN users stu ON stu.id = ss.student_user_id AND stu.is_active = 1
                 WHERE ss.session_id = cs.id) AS eligible_count,
               (SELECT COUNT(*) FROM feedback_forms ff
                 WHERE ff.session_id = cs.id AND ff.category = 'main'
                   AND ff.is_deleted = 0 AND ff.status IN ('draft', 'open')) AS live_main_forms
        FROM class_sessions cs
        JOIN users u ON u.id = cs.teacher_user_id
        JOIN timetables t ON t.id = cs.timetable_id
        JOIN branches b ON b.id = t.branch_id
        LEFT JOIN teacher_subject_settings s ON s.session_id = cs.id
        WHERE {' AND '.join(where)}
        ORDER BY LOWER(cs.subject_code), LOWER(u.name), t.code, cs.id
    """
    return [dict(r) for r in db.query(sql, tuple(args))]


def _group_by_subject(rows):
    subjects = {}
    for r in rows:
        code = r['subject_code'] or ''
        sub = subjects.setdefault(code, {
            'subject_code': code,
            'subject_name': r['subject_name'] or code,
            'teachers': {},
            'session_count': 0,
            'eligible_total': 0,
        })
        tid = r['teacher_user_id']
        teach = sub['teachers'].setdefault(tid, {
            'teacher_user_id': tid,
            'teacher_name': r['teacher_name'],
            'teacher_emp_code': r['teacher_emp_code'],
            'sections': [],
        })
        teach['sections'].append({
            'session_id': r['session_id'],
            'section_code': r['section_code'],
            'section_name': r['section_name'],
            'branch_code': r['branch_code'],
            'period_no': r['period_no'],
            'room': r['room'],
            'lab_status': r['lab_status'],
            'eligible_count': r['eligible_count'] or 0,
            'live_main_forms': r['live_main_forms'] or 0,
        })
        sub['session_count'] += 1
        sub['eligible_total'] += r['eligible_count'] or 0
    out = []
    for sub in subjects.values():
        sub['teachers'] = list(sub['teachers'].values())
        out.append(sub)
    return out


def _question_sets_payload(user_id):
    own = [x for x in services.question_sets_for(user_id) if not x.get('base_key')]
    shared = services.effective_shared_sets(user_id)
    return own + shared


def _questions_from_set(user_id, set_id):
    try:
        sid = int(set_id)
    except (TypeError, ValueError):
        return None
    for s in _question_sets_payload(user_id):
        if int(s['id']) == sid:
            return s.get('questions') or []
    return None


def _parse_deadline(raw):
    if raw is None or str(raw).strip() == '':
        return None, None
    iso = services.parse_deadline_input(raw)
    if not iso:
        return None, 'Deadline must be a valid future date and time.'
    err = services.deadline_error(iso)
    return (None, err) if err else (iso, None)


@bp.route('/main')
@role_required('hod')
def main_list():
    user, branches, branch_ids = _hod_or_abort()
    if not branch_ids:
        flash('No department scope assigned. Contact Admin.', 'warning')
        return redirect(url_for('hod.dashboard'))
    subject_q = (request.args.get('subject') or '').strip()
    section_q = (request.args.get('section') or '').strip()
    faculty_q = (request.args.get('faculty') or '').strip()
    rows = _list_main_sessions(branch_ids, subject_q, section_q, faculty_q)
    groups = _group_by_subject(rows)
    return render_template(
        'hod_feedback/main_list.html',
        branches=branches,
        groups=groups,
        total_sessions=len(rows),
        filters={'subject': subject_q, 'section': section_q, 'faculty': faculty_q},
    )


@bp.route('/main/create')
@role_required('hod')
def main_create():
    user, branches, branch_ids = _hod_or_abort()
    if not branch_ids:
        flash('No department scope assigned. Contact Admin.', 'warning')
        return redirect(url_for('hod.dashboard'))
    sets = _question_sets_payload(user['id'])
    return render_template(
        'hod_feedback/main_create.html',
        branches=branches,
        question_sets_json=json.dumps(sets),
        default_instructions=services.DEFAULT_INSTRUCTIONS,
        api_sessions=url_for('hod_feedback.api_sessions'),
        api_preview=url_for('hod_feedback.api_preview'),
        api_create=url_for('hod_feedback.api_create'),
    )


@bp.route('/main/api/sessions')
@role_required('hod')
def api_sessions():
    user, _branches, branch_ids = _hod_or_abort()
    if not branch_ids:
        return jsonify(ok=True, subjects=[])
    rows = _list_main_sessions(
        branch_ids,
        (request.args.get('subject') or '').strip() or None,
        (request.args.get('section') or '').strip() or None,
        (request.args.get('faculty') or '').strip() or None,
    )
    return jsonify(ok=True, subjects=_group_by_subject(rows), total=len(rows))


@bp.route('/main/api/preview')
@role_required('hod')
def api_preview():
    user, _b, branch_ids = _hod_or_abort()
    raw = request.args.getlist('session_id') or request.args.get('session_ids', '').split(',')
    ids = []
    for x in raw:
        x = (x or '').strip()
        if not x:
            continue
        try:
            ids.append(int(x))
        except ValueError:
            return jsonify(ok=False, error='Invalid session id.'), 400
    if not ids:
        return jsonify(ok=False, error='Select at least one section.'), 400
    if len(ids) > 50:
        return jsonify(ok=False, error='Too many sections selected (maximum 50).'), 400
    out = []
    for sid in ids:
        sess = _session_in_scope(user, sid)
        if not sess:
            return jsonify(ok=False, error='One or more sections are outside your department scope.'), 403
        try:
            sc = resolve_scope(MAIN, sid)
        except ScopeError as exc:
            return jsonify(ok=False, error=exc.message), exc.status
        students = [
            {'roll': s.get('roll') or '', 'name': s.get('name') or ''}
            for s in eligible_students(sc)
        ]
        out.append({
            'session_id': sid,
            'subject_code': sc.get('subject_code'),
            'subject_name': sc.get('subject_name'),
            'section_code': sess.get('timetable') or sess.get('code'),
            'teacher_name': sc.get('faculty_name'),
            'eligible_count': eligible_count(sc),
            'students': students,
        })
    return jsonify(ok=True, previews=out)


@bp.route('/main/api/create', methods=['POST'])
@role_required('hod')
def api_create():
    user, _b, branch_ids = _hod_or_abort()
    data = request.get_json(silent=True) or {}
    raw_ids = data.get('session_ids') or data.get('session_id') or []
    if not isinstance(raw_ids, list):
        raw_ids = [raw_ids]
    session_ids = []
    for x in raw_ids:
        try:
            session_ids.append(int(x))
        except (TypeError, ValueError):
            return jsonify(ok=False, error='Invalid session id.'), 400
    if not session_ids:
        return jsonify(ok=False, error='Select at least one section.'), 400
    if len(session_ids) > 50:
        return jsonify(ok=False, error='Too many sections (maximum 50).'), 400

    questions = _questions_from_set(user['id'], data.get('question_set_id'))
    if questions is None:
        return jsonify(ok=False, error='Choose a question set.'), 400
    if not questions:
        return jsonify(ok=False, error='That question set has no questions.'), 400

    title_in = (data.get('title') or '').strip()
    instructions = (data.get('instructions') if data.get('instructions') is not None
                    else services.DEFAULT_INSTRUCTIONS)
    deadline_at, derr = _parse_deadline(data.get('deadline_at'))
    if derr:
        return jsonify(ok=False, error=derr), 400
    do_publish = bool(data.get('publish'))
    if do_publish and not deadline_at:
        return jsonify(ok=False, error='A future deadline is required to publish.'), 400

    created, errors = [], []
    for sid in session_ids:
        sess = _session_in_scope(user, sid)
        if not sess:
            errors.append({'session_id': sid, 'error': 'Outside your department scope.'})
            continue
        try:
            sc = resolve_scope(MAIN, sid)
            title = title_in or category_forms.default_title(sc)
            form_id = category_forms.create_form(
                user, MAIN, sid, title, questions,
                instructions=instructions or '',
                deadline_at=deadline_at,
            )
            published = False
            eligible = 0
            if do_publish:
                eligible = category_forms.publish(user, form_id)
                published = True
            created.append({
                'session_id': sid,
                'form_id': form_id,
                'published': published,
                'eligible_count': eligible if published else eligible_count(sc),
                'title': title,
            })
            audit('hod_feedback.main.create', 'feedback_forms', form_id,
                  f'Main form session={sid} published={published}', actor=user)
        except ScopeError as exc:
            errors.append({'session_id': sid, 'error': exc.message, 'code': exc.code})
        except Exception:
            errors.append({'session_id': sid, 'error': 'Could not create the form.'})

    status = 200 if created and not errors else (207 if created else 409)
    return jsonify(ok=bool(created), created=created, errors=errors), status



# --------------------------------------------------------------------------- #
# Module C — Lab Faculty Feedback (lab_status = available only)
# --------------------------------------------------------------------------- #
def _list_lab_sessions(branch_ids, subject_q=None, section_q=None, faculty_q=None):
    """Sessions with lab_status='available' in HOD branch scope.

    Aligns with assignment_setup.lab_eligible_sessions (same lab_status filter)
    but across all teachers in the HOD's departments. Includes optional
    confirmed lab_faculty_assignments for display only; form identity stays
    on session_id (Phase 5B).
    """
    if not branch_ids:
        return []
    ph = ','.join('?' * len(branch_ids))
    args = list(branch_ids)
    where = [
        f't.branch_id IN ({ph})',
        'cs.is_deleted=0',
        'cs.teacher_user_id IS NOT NULL',
        "s.lab_status = 'available'",
    ]
    if subject_q:
        where.append('(cs.subject_code LIKE ? OR cs.subject_name LIKE ?)')
        like = f'%{subject_q}%'
        args.extend([like, like])
    if section_q:
        where.append('(t.code LIKE ? OR t.name LIKE ?)')
        like = f'%{section_q}%'
        args.extend([like, like])
    if faculty_q:
        where.append(
            '(u.name LIKE ? OR u.emp_code LIKE ? OR '
            'lu.name LIKE ? OR lfa.lab_emp_code LIKE ?)')
        like = f'%{faculty_q}%'
        args.extend([like, like, like, like])
    sql = f"""
        SELECT cs.id AS session_id, cs.subject_code, cs.subject_name, cs.period_no,
               u.id AS teacher_user_id, u.name AS teacher_name, u.emp_code AS teacher_emp_code,
               t.id AS timetable_id, t.code AS section_code, t.name AS section_name,
               t.branch_id, b.code AS branch_code,
               s.room AS teacher_room, s.lab_status,
               lfa.id AS lab_assignment_id, lfa.lab_emp_code, lfa.room AS lab_room,
               lu.id AS lab_teacher_user_id, lu.name AS lab_teacher_name,
               (SELECT COUNT(*) FROM session_students ss
                  JOIN users stu ON stu.id = ss.student_user_id AND stu.is_active = 1
                 WHERE ss.session_id = cs.id) AS eligible_count,
               (SELECT COUNT(*) FROM feedback_forms ff
                 WHERE ff.session_id = cs.id AND ff.category = 'lab'
                   AND ff.is_deleted = 0 AND ff.status IN ('draft', 'open')) AS live_lab_forms
        FROM class_sessions cs
        JOIN users u ON u.id = cs.teacher_user_id
        JOIN timetables t ON t.id = cs.timetable_id
        JOIN branches b ON b.id = t.branch_id
        JOIN teacher_subject_settings s ON s.session_id = cs.id AND s.lab_status = 'available'
        LEFT JOIN lab_faculty_assignments lfa
               ON lfa.session_id = cs.id AND lfa.status = 'confirmed'
        LEFT JOIN users lu ON lu.id = lfa.lab_teacher_user_id
        WHERE {' AND '.join(where)}
        ORDER BY LOWER(cs.subject_code), t.code, cs.id
    """
    rows = [dict(r) for r in db.query(sql, tuple(args))]
    for r in rows:
        r['room'] = r.get('lab_room') or r.get('teacher_room')
        # Display faculty: lab assignment if present, else main teacher (Phase 5B lab evaluates main)
        r['display_faculty_name'] = r.get('lab_teacher_name') or r.get('teacher_name')
        r['display_emp_code'] = r.get('lab_emp_code') or r.get('teacher_emp_code')
    return rows


def _group_lab_rows(rows):
    """Group lab sessions by subject (separate Lab section UI)."""
    subjects = {}
    for r in rows:
        code = r['subject_code'] or ''
        sub = subjects.setdefault(code, {
            'subject_code': code,
            'subject_name': r['subject_name'] or code,
            'sessions': [],
            'session_count': 0,
            'eligible_total': 0,
        })
        sub['sessions'].append({
            'session_id': r['session_id'],
            'section_code': r['section_code'],
            'branch_code': r['branch_code'],
            'period_no': r['period_no'],
            'room': r['room'],
            'lab_status': r['lab_status'],
            'teacher_name': r['teacher_name'],
            'teacher_emp_code': r['teacher_emp_code'],
            'lab_teacher_name': r.get('lab_teacher_name'),
            'lab_emp_code': r.get('lab_emp_code'),
            'lab_assignment_id': r.get('lab_assignment_id'),
            'display_faculty_name': r['display_faculty_name'],
            'display_emp_code': r['display_emp_code'],
            'eligible_count': r['eligible_count'] or 0,
            'live_lab_forms': r['live_lab_forms'] or 0,
        })
        sub['session_count'] += 1
        sub['eligible_total'] += r['eligible_count'] or 0
    return list(subjects.values())


@bp.route('/lab')
@role_required('hod')
def lab_list():
    """Lab Faculty Feedback — only Lab Available sessions."""
    user, branches, branch_ids = _hod_or_abort()
    if not branch_ids:
        flash('No department scope assigned. Contact Admin.', 'warning')
        return redirect(url_for('hod.dashboard'))
    subject_q = (request.args.get('subject') or '').strip()
    section_q = (request.args.get('section') or '').strip()
    faculty_q = (request.args.get('faculty') or '').strip()
    rows = _list_lab_sessions(branch_ids, subject_q, section_q, faculty_q)
    return render_template(
        'hod_feedback/lab_list.html',
        branches=branches,
        groups=_group_lab_rows(rows),
        total_sessions=len(rows),
        filters={'subject': subject_q, 'section': section_q, 'faculty': faculty_q},
    )


@bp.route('/lab/create')
@role_required('hod')
def lab_create():
    user, branches, branch_ids = _hod_or_abort()
    if not branch_ids:
        flash('No department scope assigned. Contact Admin.', 'warning')
        return redirect(url_for('hod.dashboard'))
    sets = _question_sets_payload(user['id'])
    return render_template(
        'hod_feedback/lab_create.html',
        branches=branches,
        question_sets_json=json.dumps(sets),
        default_instructions=services.DEFAULT_INSTRUCTIONS,
        api_sessions=url_for('hod_feedback.api_lab_sessions'),
        api_preview=url_for('hod_feedback.api_lab_preview'),
        api_create=url_for('hod_feedback.api_lab_create'),
    )


@bp.route('/lab/api/sessions')
@role_required('hod')
def api_lab_sessions():
    user, _b, branch_ids = _hod_or_abort()
    if not branch_ids:
        return jsonify(ok=True, subjects=[])
    rows = _list_lab_sessions(
        branch_ids,
        (request.args.get('subject') or '').strip() or None,
        (request.args.get('section') or '').strip() or None,
        (request.args.get('faculty') or '').strip() or None,
    )
    return jsonify(ok=True, subjects=_group_lab_rows(rows), total=len(rows))


@bp.route('/lab/api/preview')
@role_required('hod')
def api_lab_preview():
    user, _b, branch_ids = _hod_or_abort()
    raw = request.args.getlist('session_id') or request.args.get('session_ids', '').split(',')
    ids = []
    for x in raw:
        x = (x or '').strip()
        if not x:
            continue
        try:
            ids.append(int(x))
        except ValueError:
            return jsonify(ok=False, error='Invalid session id.'), 400
    if not ids:
        return jsonify(ok=False, error='Select at least one lab section.'), 400
    if len(ids) > 50:
        return jsonify(ok=False, error='Too many sections selected (maximum 50).'), 400
    out = []
    for sid in ids:
        sess = _session_in_scope(user, sid)
        if not sess:
            return jsonify(ok=False, error='One or more sections are outside your department scope.'), 403
        try:
            sc = resolve_scope(LAB, sid)
        except ScopeError as exc:
            return jsonify(ok=False, error=exc.message), exc.status
        students = [
            {'roll': s.get('roll') or '', 'name': s.get('name') or ''}
            for s in eligible_students(sc)
        ]
        out.append({
            'session_id': sid,
            'subject_code': sc.get('subject_code'),
            'subject_name': sc.get('subject_name'),
            'section_code': sess.get('timetable') or sess.get('code'),
            'teacher_name': sc.get('faculty_name'),
            'eligible_count': eligible_count(sc),
            'students': students,
        })
    return jsonify(ok=True, previews=out)


@bp.route('/lab/api/create', methods=['POST'])
@role_required('hod')
def api_lab_create():
    """Create/publish Lab forms (category=lab, assignment_id=session_id)."""
    user, _b, branch_ids = _hod_or_abort()
    data = request.get_json(silent=True) or {}
    raw_ids = data.get('session_ids') or data.get('session_id') or []
    if not isinstance(raw_ids, list):
        raw_ids = [raw_ids]
    session_ids = []
    for x in raw_ids:
        try:
            session_ids.append(int(x))
        except (TypeError, ValueError):
            return jsonify(ok=False, error='Invalid session id.'), 400
    if not session_ids:
        return jsonify(ok=False, error='Select at least one lab section.'), 400
    if len(session_ids) > 50:
        return jsonify(ok=False, error='Too many sections (maximum 50).'), 400

    questions = _questions_from_set(user['id'], data.get('question_set_id'))
    if questions is None:
        return jsonify(ok=False, error='Choose a question set.'), 400
    if not questions:
        return jsonify(ok=False, error='That question set has no questions.'), 400

    title_in = (data.get('title') or '').strip()
    instructions = (data.get('instructions') if data.get('instructions') is not None
                    else services.DEFAULT_INSTRUCTIONS)
    deadline_at, derr = _parse_deadline(data.get('deadline_at'))
    if derr:
        return jsonify(ok=False, error=derr), 400
    do_publish = bool(data.get('publish'))
    if do_publish and not deadline_at:
        return jsonify(ok=False, error='A future deadline is required to publish.'), 400

    created, errors = [], []
    for sid in session_ids:
        sess = _session_in_scope(user, sid)
        if not sess:
            errors.append({'session_id': sid, 'error': 'Outside your department scope.'})
            continue
        try:
            sc = resolve_scope(LAB, sid)  # enforces lab_status=available
            title = title_in or category_forms.default_title(sc)
            form_id = category_forms.create_form(
                user, LAB, sid, title, questions,
                instructions=instructions or '',
                deadline_at=deadline_at,
            )
            published = False
            eligible = 0
            if do_publish:
                eligible = category_forms.publish(user, form_id)
                published = True
            created.append({
                'session_id': sid,
                'form_id': form_id,
                'published': published,
                'eligible_count': eligible if published else eligible_count(sc),
                'title': title,
            })
            audit('hod_feedback.lab.create', 'feedback_forms', form_id,
                  f'Lab form session={sid} published={published}', actor=user)
        except ScopeError as exc:
            errors.append({'session_id': sid, 'error': exc.message, 'code': exc.code})
        except Exception:
            errors.append({'session_id': sid, 'error': 'Could not create the form.'})

    status = 200 if created and not errors else (207 if created else 409)
    return jsonify(ok=bool(created), created=created, errors=errors), status



# --------------------------------------------------------------------------- #
# Module B — Second Faculty Feedback
# --------------------------------------------------------------------------- #
def _list_second_assignments(branch_ids, subject_q=None, section_q=None,
                             faculty_q=None, main_q=None):
    """Confirmed, non-removed second-faculty assignments in HOD branch scope.

    Each row is identified by assignment id (never by employee alone).
    """
    if not branch_ids:
        return []
    ph = ','.join('?' * len(branch_ids))
    args = list(branch_ids)
    where = [
        f't.branch_id IN ({ph})',
        "sfa.status = 'confirmed'",
        'sfa.second_teacher_user_id IS NOT NULL',
        'sfa.removed_at IS NULL',
        'cs.is_deleted = 0',
    ]
    if subject_q:
        where.append('(cs.subject_code LIKE ? OR cs.subject_name LIKE ?)')
        like = f'%{subject_q}%'
        args.extend([like, like])
    if section_q:
        where.append('(t.code LIKE ? OR t.name LIKE ?)')
        like = f'%{section_q}%'
        args.extend([like, like])
    if faculty_q:
        where.append('(su.name LIKE ? OR sfa.second_emp_code LIKE ?)')
        like = f'%{faculty_q}%'
        args.extend([like, like])
    if main_q:
        where.append('(mu.name LIKE ? OR mu.emp_code LIKE ?)')
        like = f'%{main_q}%'
        args.extend([like, like])
    sql = f"""
        SELECT sfa.id AS assignment_id, sfa.session_id, sfa.second_emp_code,
               sfa.second_teacher_user_id,
               su.name AS second_name,
               mu.id AS main_user_id, mu.name AS main_name, mu.emp_code AS main_emp_code,
               cs.subject_code, cs.subject_name, cs.period_no,
               t.code AS section_code, t.branch_id, b.code AS branch_code,
               ts.room,
               (SELECT COUNT(*) FROM session_students ss
                  JOIN users stu ON stu.id = ss.student_user_id AND stu.is_active = 1
                 WHERE ss.session_id = cs.id) AS eligible_count,
               (SELECT COUNT(*) FROM feedback_forms ff
                 WHERE ff.second_faculty_assignment_id = sfa.id AND ff.category = 'second'
                   AND ff.is_deleted = 0 AND ff.status IN ('draft', 'open')) AS live_forms
        FROM second_faculty_assignments sfa
        JOIN class_sessions cs ON cs.id = sfa.session_id
        JOIN timetables t ON t.id = cs.timetable_id
        JOIN branches b ON b.id = t.branch_id
        JOIN users mu ON mu.id = cs.teacher_user_id
        LEFT JOIN users su ON su.id = sfa.second_teacher_user_id
        LEFT JOIN teacher_subject_settings ts ON ts.session_id = cs.id
        WHERE {' AND '.join(where)}
        ORDER BY LOWER(cs.subject_code), t.code, sfa.id
    """
    return [dict(r) for r in db.query(sql, tuple(args))]


def _group_second_by_subject(rows):
    subjects = {}
    for r in rows:
        code = r['subject_code'] or ''
        sub = subjects.setdefault(code, {
            'subject_code': code,
            'subject_name': r['subject_name'] or code,
            'assignments': [],
            'assignment_count': 0,
        })
        sub['assignments'].append(r)
        sub['assignment_count'] += 1
    return list(subjects.values())


@bp.route('/second')
@role_required('hod')
def second_list():
    """Second Faculty Feedback — confirmed assignments only, by assignment id."""
    user, branches, branch_ids = _hod_or_abort()
    if not branch_ids:
        flash('No department scope assigned. Contact Admin.', 'warning')
        return redirect(url_for('hod.dashboard'))
    filters = {
        'subject': (request.args.get('subject') or '').strip(),
        'section': (request.args.get('section') or '').strip(),
        'faculty': (request.args.get('faculty') or '').strip(),
        'main': (request.args.get('main') or '').strip(),
    }
    rows = _list_second_assignments(
        branch_ids, filters['subject'] or None, filters['section'] or None,
        filters['faculty'] or None, filters['main'] or None)
    return render_template(
        'hod_feedback/second_list.html',
        branches=branches,
        groups=_group_second_by_subject(rows),
        total=len(rows),
        filters=filters,
    )


@bp.route('/second/create')
@role_required('hod')
def second_create():
    user, branches, branch_ids = _hod_or_abort()
    if not branch_ids:
        flash('No department scope assigned. Contact Admin.', 'warning')
        return redirect(url_for('hod.dashboard'))
    sets = _question_sets_payload(user['id'])
    return render_template(
        'hod_feedback/second_create.html',
        branches=branches,
        question_sets_json=json.dumps(sets),
        default_instructions=services.DEFAULT_INSTRUCTIONS,
        api_sessions=url_for('hod_feedback.api_second_list'),
        api_preview=url_for('hod_feedback.api_second_preview'),
        api_create=url_for('hod_feedback.api_second_create'),
    )


@bp.route('/second/api/list')
@role_required('hod')
def api_second_list():
    user, _b, branch_ids = _hod_or_abort()
    if not branch_ids:
        return jsonify(ok=True, subjects=[], total=0)
    rows = _list_second_assignments(
        branch_ids,
        (request.args.get('subject') or '').strip() or None,
        (request.args.get('section') or '').strip() or None,
        (request.args.get('faculty') or '').strip() or None,
        (request.args.get('main') or '').strip() or None,
    )
    return jsonify(ok=True, subjects=_group_second_by_subject(rows), total=len(rows))


@bp.route('/second/api/preview')
@role_required('hod')
def api_second_preview():
    user, _b, branch_ids = _hod_or_abort()
    raw = request.args.getlist('assignment_id') or request.args.get('assignment_ids', '').split(',')
    ids = []
    for x in raw:
        x = (x or '').strip()
        if not x:
            continue
        try:
            ids.append(int(x))
        except ValueError:
            return jsonify(ok=False, error='Invalid assignment id.'), 400
    if not ids:
        return jsonify(ok=False, error='Select at least one assignment.'), 400
    if len(ids) > 50:
        return jsonify(ok=False, error='Too many assignments (maximum 50).'), 400
    out = []
    for aid in ids:
        try:
            sc = resolve_scope(SECOND, aid)
        except ScopeError as exc:
            return jsonify(ok=False, error=exc.message), exc.status
        if sc['branch_id'] not in (branch_ids or []):
            return jsonify(ok=False, error='Assignment outside your department scope.'), 403
        students = [
            {'roll': s.get('roll') or '', 'name': s.get('name') or ''}
            for s in eligible_students(sc)
        ]
        out.append({
            'assignment_id': aid,
            'session_id': sc.get('session_id'),
            'subject_code': sc.get('subject_code'),
            'subject_name': sc.get('subject_name'),
            'faculty_name': sc.get('faculty_name'),
            'faculty_emp_code': sc.get('faculty_emp_code'),
            'eligible_count': eligible_count(sc),
            'students': students,
        })
    return jsonify(ok=True, previews=out)


@bp.route('/second/api/create', methods=['POST'])
@role_required('hod')
def api_second_create():
    """Create/publish Second Faculty forms (category=second, assignment_id=SFA id)."""
    user, _b, branch_ids = _hod_or_abort()
    data = request.get_json(silent=True) or {}
    raw_ids = data.get('assignment_ids') or data.get('assignment_id') or []
    if not isinstance(raw_ids, list):
        raw_ids = [raw_ids]
    assignment_ids = []
    for x in raw_ids:
        try:
            assignment_ids.append(int(x))
        except (TypeError, ValueError):
            return jsonify(ok=False, error='Invalid assignment id.'), 400
    if not assignment_ids:
        return jsonify(ok=False, error='Select at least one assignment.'), 400
    if len(assignment_ids) > 50:
        return jsonify(ok=False, error='Too many assignments (maximum 50).'), 400

    questions = _questions_from_set(user['id'], data.get('question_set_id'))
    if questions is None:
        return jsonify(ok=False, error='Choose a question set.'), 400
    if not questions:
        return jsonify(ok=False, error='That question set has no questions.'), 400

    title_in = (data.get('title') or '').strip()
    instructions = (data.get('instructions') if data.get('instructions') is not None
                    else services.DEFAULT_INSTRUCTIONS)
    deadline_at, derr = _parse_deadline(data.get('deadline_at'))
    if derr:
        return jsonify(ok=False, error=derr), 400
    do_publish = bool(data.get('publish'))
    if do_publish and not deadline_at:
        return jsonify(ok=False, error='A future deadline is required to publish.'), 400

    created, errors = [], []
    for aid in assignment_ids:
        try:
            sc = resolve_scope(SECOND, aid)
            if sc['branch_id'] not in (branch_ids or []):
                errors.append({'assignment_id': aid, 'error': 'Outside your department scope.'})
                continue
            title = title_in or category_forms.default_title(sc)
            form_id = category_forms.create_form(
                user, SECOND, aid, title, questions,
                instructions=instructions or '',
                deadline_at=deadline_at,
            )
            published = False
            eligible = 0
            if do_publish:
                eligible = category_forms.publish(user, form_id)
                published = True
            created.append({
                'assignment_id': aid,
                'form_id': form_id,
                'published': published,
                'eligible_count': eligible if published else eligible_count(sc),
                'title': title,
            })
            audit('hod_feedback.second.create', 'feedback_forms', form_id,
                  f'Second form assignment={aid} published={published}', actor=user)
        except ScopeError as exc:
            errors.append({'assignment_id': aid, 'error': exc.message, 'code': exc.code})
        except Exception:
            errors.append({'assignment_id': aid, 'error': 'Could not create the form.'})

    status = 200 if created and not errors else (207 if created else 409)
    return jsonify(ok=bool(created), created=created, errors=errors), status
