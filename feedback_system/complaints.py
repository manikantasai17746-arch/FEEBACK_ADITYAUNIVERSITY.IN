"""Student issue reports, independent of faculty feedback and its answers."""
from uuid import UUID, uuid4

from flask import abort, flash, redirect, render_template, request, url_for

from . import db, services
from .security import audit, scoped_branch_ids_or_none


CATEGORIES = {
    'fans': 'Fans',
    'lighting': 'Lighting',
    'lab': 'Lab facilities',
    'classroom': 'Classroom',
    'other': 'Other college issue',
}
STATUSES = {'pending': 'Pending', 'in_progress': 'In Progress', 'resolved': 'Resolved'}
MAX_DESCRIPTION = 2000


def report_context(student_id, forms=None):
    """Use the same eligible forms as My Feedback and the latest active enrollment."""
    if forms is None:
        forms = services.student_forms(student_id)
    if not forms or any(form['submission_id'] is None for form in forms):
        return None
    return db.query("""
        SELECT t.branch_id, e.semester_id, e.timetable_id,
               COALESCE(e.academic_year_id, t.academic_year_id) AS academic_year_id,
               b.name AS department, s.number AS semester
        FROM enrollments e
        JOIN timetables t ON t.id=e.timetable_id AND t.is_deleted=0
        JOIN branches b ON b.id=t.branch_id
        JOIN semesters s ON s.id=e.semester_id
        LEFT JOIN academic_years ay ON ay.id=COALESCE(e.academic_year_id, t.academic_year_id)
        WHERE e.student_user_id=? AND e.is_deleted=0
        ORDER BY COALESCE(ay.is_active, 0) DESC, ay.name DESC NULLS LAST, s.number DESC, e.id DESC
        LIMIT 1
    """, (student_id,), one=True)


def report_form(context, category='', description='', submission_key=None):
    return {
        'context': context, 'categories': CATEGORIES, 'category': category,
        'description': description, 'maximum': MAX_DESCRIPTION,
        'submission_key': submission_key or uuid4().hex,
    }


def submit(student_id):
    context = report_context(student_id)
    if context is None:
        abort(403)
    category = request.form.get('category', '')
    description = request.form.get('description', '').strip()
    try:
        key = UUID(request.form.get('submission_key', '')).hex
    except ValueError:
        abort(400)
    if category not in CATEGORIES or not description or len(description) > MAX_DESCRIPTION:
        return render_template(
            'student_complaint.html', report=report_form(context, category, description, key),
            complaint_error=f'Choose a category and enter a description of up to {MAX_DESCRIPTION} characters.',
        ), 422
    db.execute("""
        INSERT INTO student_complaints
            (student_user_id, branch_id, semester_id, timetable_id, academic_year_id,
             submission_key, category, description)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(student_user_id, submission_key) DO NOTHING
    """, (student_id, context['branch_id'], context['semester_id'], context['timetable_id'],
          context['academic_year_id'], key, category, description))
    flash('Your college issue was submitted.', 'success')
    return redirect(url_for('student.dashboard', skip_issue=1))


def _filters(dean):
    filters = {}
    for key in ('department', 'semester') if dean else ('semester',):
        selection = request.args.get(key, type=int)
        if selection is not None and 0 < selection <= 2147483647:
            filters[key] = selection
    return filters


def staff_list(actor):
    dean = actor['role'] == 'dean'
    filters = _filters(dean)
    conditions, params = [], []
    if not dean:
        branch_ids = scoped_branch_ids_or_none(actor)
        placeholders = ','.join('?' for _ in branch_ids)
        conditions.append(f'c.branch_id IN ({placeholders})' if branch_ids else '1=0')
        params.extend(branch_ids)
    if 'department' in filters:
        conditions.append('c.branch_id=?')
        params.append(filters['department'])
    if 'semester' in filters:
        conditions.append('c.semester_id=?')
        params.append(filters['semester'])
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    # An exact timetable allocation takes precedence over a legacy department scope.
    hod_name = """, COALESCE(
        (SELECT COALESCE(NULLIF(h.name, ''), h.username)
         FROM hod_allocations ha JOIN users h ON h.id=ha.hod_user_id
         WHERE ha.academic_year_id=c.academic_year_id AND ha.branch_id=c.branch_id
           AND ha.semester_id=c.semester_id AND ha.timetable_id=c.timetable_id
           AND h.role='hod' AND h.is_active=1),
        (SELECT COALESCE(NULLIF(h.name, ''), h.username)
         FROM user_department_scopes scope JOIN users h ON h.id=scope.user_id
         WHERE scope.branch_id=c.branch_id AND h.role='hod' AND h.is_active=1
           AND NOT EXISTS (SELECT 1 FROM hod_allocations ha WHERE ha.hod_user_id=h.id)
         ORDER BY scope.is_primary DESC, h.id LIMIT 1)
    ) AS hod_name""" if dean else ''
    rows = db.query("""
        SELECT c.id, c.category, c.description, c.status, c.created_at,
               s.number AS semester, b.name AS department
    """ + hod_name + """
        FROM student_complaints c JOIN semesters s ON s.id=c.semester_id
        JOIN branches b ON b.id=c.branch_id
    """ + where + ' ORDER BY c.created_at DESC, c.id DESC', tuple(params))
    return render_template(
        'staff_complaints.html', dean=dean, rows=rows, categories=CATEGORIES, statuses=STATUSES,
        departments=db.query('SELECT id, name FROM branches ORDER BY name') if dean else [],
        semesters=db.query('SELECT id, number FROM semesters ORDER BY number'),
        filters=filters, list_endpoint=f"{actor['role']}.student_complaints",
        status_endpoint=f"{actor['role']}.complaint_status",
    )


def update_status(actor, complaint_id):
    complaint = db.query('SELECT branch_id, status FROM student_complaints WHERE id=?',
                         (complaint_id,), one=True)
    if complaint is None:
        abort(404)
    if actor['role'] == 'hod' and complaint['branch_id'] not in scoped_branch_ids_or_none(actor):
        abort(404)
    status = request.form.get('status')
    prior_status = request.form.get('prior_status')
    if status not in STATUSES or prior_status not in STATUSES:
        abort(400)
    if status != complaint['status']:
        conn = db.get_db()
        try:
            updated = conn.execute("""
                UPDATE student_complaints SET status=? WHERE id=? AND status=? AND status<>?
                RETURNING student_user_id, category
            """, (status, complaint_id, prior_status, status)).fetchone()
            if updated is not None and updated['student_user_id'] is not None:
                role = 'HOD' if actor['role'] == 'hod' else 'Dean'
                category = CATEGORIES.get(updated['category'], 'college issue')
                conn.execute("""
                    INSERT INTO notifications(user_id, kind, title, body) VALUES (?, 'info', ?, ?)
                """, (updated['student_user_id'], f'Complaint #{complaint_id} · {STATUSES[status]}',
                      f'The {role} updated your {category} complaint from {STATUSES[prior_status]} '
                      f'to {STATUSES[status]}.'))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if updated is not None:
            audit('complaint.status', 'student_complaints', complaint_id, status, actor=actor)
            flash('Complaint status updated.', 'success')
        else:
            flash('This complaint changed since you opened the page. Review its current status.', 'warning')
    return redirect(url_for(f"{actor['role']}.student_complaints", **_filters(actor['role'] == 'dean')))
