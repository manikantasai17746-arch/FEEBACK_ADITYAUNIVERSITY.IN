"""Student dashboard and feedback submission."""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from . import db, services
from .security import current_user, role_required

bp = Blueprint('student', __name__, url_prefix='/student')


def _enrollment_context(user_id):
    return db.query("""SELECT t.code AS timetable, b.code AS branch, b.name AS branch_name,
            s.number AS semester, ay.name AS academic_year
        FROM enrollments e
        LEFT JOIN timetables t ON t.id=e.timetable_id
        LEFT JOIN branches b ON b.id=t.branch_id
        LEFT JOIN semesters s ON s.id=e.semester_id
        LEFT JOIN academic_years ay ON ay.id=e.academic_year_id
        WHERE e.student_user_id=? LIMIT 1""", (user_id,), one=True)


def _year_from_semester(semester):
    if not semester:
        return None
    return (int(semester) + 1) // 2


@bp.route('/')
@role_required('student')
def dashboard():
    user = current_user()
    forms = services.student_forms(user['id'])
    ctx = _enrollment_context(user['id'])
    pending = [f for f in forms if f['status'] == 'open' and not f['submission_id']]
    submitted = [f for f in forms if f['submission_id']]
    closed = [f for f in forms if f['status'] in ('closed', 'archived') and not f['submission_id']]
    return render_template('student_dashboard.html', user=user, ctx=ctx,
                           pending=pending, submitted=submitted, closed=closed,
                           total=len(forms), year=_year_from_semester(ctx['semester'] if ctx else None))


@bp.route('/form/<int:form_id>')
@role_required('student')
def view_form(form_id):
    user = current_user()
    form = services.student_form(user['id'], form_id)
    if not form:
        flash('That feedback form is not available to you.', 'error')
        return redirect(url_for('student.dashboard'))
    questions = services.form_questions(form_id)
    already = db.query('SELECT * FROM submissions WHERE form_id=? AND student_user_id=?',
                       (form_id, user['id']), one=True)
    return render_template('student_form.html', user=user, form=form, questions=questions,
                           already=already)


@bp.route('/form/<int:form_id>/submit', methods=['POST'])
@role_required('student')
def submit(form_id):
    user = current_user()
    ok, message = services.submit_feedback(user['id'], form_id, request.form)
    if ok:
        flash(message, 'success')
        # Change 2: auto-open the next pending form after submit
        forms = services.student_forms(user['id'])
        pending = [f for f in forms
                   if f['status'] == 'open' and not f.get('submission_id')
                   and f['id'] != form_id]
        if pending:
            flash('Opening your next pending form…', 'info')
            return redirect(url_for('student.view_form', form_id=pending[0]['id']))
        return redirect(url_for('student.dashboard'))
    flash(message, 'error')
    # Re-render preserving the student's answers so nothing is lost.
    form = services.student_form(user['id'], form_id)
    if not form:
        return redirect(url_for('student.dashboard'))
    questions = services.form_questions(form_id)
    return render_template('student_form.html', user=user, form=form, questions=questions,
                           already=None, form_data=request.form)


@bp.route('/notifications')
@role_required('student')
def notifications():
    """Change 4: in-app notification inbox."""
    user = current_user()
    rows = db.query(
        'SELECT * FROM notifications WHERE user_id=? ORDER BY is_read ASC, created_at DESC LIMIT 100',
        (user['id'],))
    unread = db.query(
        'SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0',
        (user['id'],), one=True)['c']
    return render_template('student_notifications.html', user=user, rows=rows, unread=unread)


@bp.route('/notifications/<int:nid>/read', methods=['POST'])
@role_required('student')
def notification_read(nid):
    user = current_user()
    conn = db.get_db()
    conn.execute('UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?',
                 (nid, user['id']))
    conn.commit()
    return redirect(url_for('student.notifications'))


@bp.route('/notifications/read-all', methods=['POST'])
@role_required('student')
def notifications_read_all():
    user = current_user()
    conn = db.get_db()
    conn.execute('UPDATE notifications SET is_read=1 WHERE user_id=? AND is_read=0',
                 (user['id'],))
    conn.commit()
    flash('All notifications marked as read.', 'success')
    return redirect(url_for('student.notifications'))
