"""Student dashboard and feedback submission."""
from flask import (Blueprint, current_app, flash, redirect, render_template, request,
                   session, url_for)

from . import authenticator, complaints, db, photos, services
from .security import audit, current_user, is_safe_next, role_required

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
    services.add_student_feedback_reminders(user['id'], forms)
    ctx = _enrollment_context(user['id'])
    pending = [f for f in forms if f['status'] == 'open' and not f['submission_id']]
    submitted = [f for f in forms if f['submission_id']]
    closed = [f for f in forms if f['status'] in ('closed', 'archived') and not f['submission_id']]
    # Keep the required Main, Lab, Second order per subject in both layouts.
    rank = {k: i for i, k in enumerate(services.CATEGORY_ORDER)}
    pending.sort(key=lambda f: (f.get('session_id') or 0, rank.get(f['category_key'], 9), f['id']))
    pending_by_title = {}
    for form in pending:
        title = (form['title'] or '').strip() or 'Feedback form'
        pending_by_title.setdefault(title, []).append(form)
    sections = [{'key': 'all', 'pending': pending, 'submitted': submitted, 'closed': closed}] \
        if forms else []
    # The selected category filters only the history.
    history = []
    for key in services.CATEGORY_ORDER:
        in_cat = [f for f in forms if f['category_key'] == key]
        if in_cat:
            history.append({
                'key': key, 'label': services.CATEGORY_LABELS[key], 'hint': services.CATEGORY_HINTS[key],
                'pending': [f for f in in_cat if f in pending],
                'submitted': [f for f in in_cat if f in submitted],
                'closed': [f for f in in_cat if f in closed]})
    chosen = request.args.get('cat')
    active_cat = next((h for h in history if h['key'] == chosen), history[0] if history else None)
    report_context = None
    if request.args.get('skip_issue') != '1':
        report_context = complaints.report_context(user['id'], forms)
    return render_template('student_dashboard.html', user=user, ctx=ctx,
                           history=history, active_cat=active_cat,
                           pending=pending, submitted=submitted, closed=closed,
                           sections=sections, pending_by_title=pending_by_title,
                           report=complaints.report_form(report_context) if report_context else None,
                           total=len(forms), year=_year_from_semester(ctx['semester'] if ctx else None))


@bp.post('/complaints')
@role_required('student')
def report_issue():
    return complaints.submit(current_user()['id'])


# --------------------------------------------------------------------------- #
# Step C: authenticator code before feedback forms marked "Authentication Required"
# --------------------------------------------------------------------------- #
# The verified state lives in the signed session. It lasts for this login session only:
# logout, a new login, the idle timeout (30 min) or the session maximum (8 h) clear it.
FEEDBACK_CODE_KEY = 'feedback_code_ok'


def _needs_code(form_id):
    row = db.query('SELECT auth_required FROM feedback_forms WHERE id=?', (form_id,), one=True)
    return bool(row and row['auth_required'])


def _feedback_gate(user, form_id):
    """A redirect if this form needs the authenticator code and it has not been given."""
    if not _needs_code(form_id) or session.get(FEEDBACK_CODE_KEY):
        return None
    if not authenticator.is_active(user['id']):
        flash('Set up your authenticator app before giving this feedback.', 'warning')
        return redirect(url_for('auth.setup_authenticator'))
    return redirect(url_for('student.verify_code', next=url_for('student.view_form', form_id=form_id)))


@bp.route('/verify-code', methods=['GET', 'POST'])
@role_required('student')
def verify_code():
    """Ask for the current code from the student's authenticator app."""
    user = current_user()
    nxt = request.values.get('next') or url_for('student.dashboard')
    if not is_safe_next(nxt):
        nxt = url_for('student.dashboard')
    if not authenticator.is_active(user['id']):
        return redirect(url_for('auth.setup_authenticator'))
    if request.method == 'POST':
        try:
            ok, reason = authenticator.verify_second_factor(user['id'], request.form.get('code'))
        except authenticator.AuthenticatorConfigError:
            # TOTP_ENCRYPTION_KEY is missing, malformed, or not the key this student's
            # secret was saved with. Show a message instead of a 500 page.
            current_app.logger.exception('Authenticator secret could not be read for user %s',
                                         user['id'])
            flash('Authenticator verification is not available right now. '
                  'Please contact the administrator.', 'error')
            return render_template('feedback_verify.html', user=user, next=nxt)
        if ok:
            session[FEEDBACK_CODE_KEY] = True
            # Recorded without any form, so the audit trail does not link a student to feedback.
            audit('user.feedback_code_verified', 'users', user['id'],
                  'Authenticator code accepted for feedback')
            return redirect(nxt)
        if reason == 'locked':
            flash('Too many incorrect codes. Please wait a few minutes and try again.', 'error')
        else:
            flash('That code is not correct. Check your authenticator app and try again.', 'error')
    return render_template('feedback_verify.html', user=user, next=nxt)


@bp.route('/form/<int:form_id>')
@role_required('student')
def view_form(form_id):
    user = current_user()
    form = services.student_form(user['id'], form_id)
    if not form:
        flash('That feedback form is not available to you.', 'error')
        return redirect(url_for('student.dashboard'))
    if form.get('locked'):
        flash('Please finish your earlier feedback for this subject first.', 'warning')
        return redirect(url_for('student.dashboard'))
    gate = _feedback_gate(user, form_id)
    if gate:
        return gate
    questions = services.form_questions(form_id)
    already = db.query('SELECT * FROM submissions WHERE form_id=? AND student_user_id=?',
                       (form_id, user['id']), one=True)
    # After the code, an open form is shown on its own page without the student's details.
    anon = bool(_needs_code(form_id) and not already and form['status'] == 'open')
    return render_template('student_form.html', user=user, form=form, questions=questions,
                           default_instructions=services.DEFAULT_INSTRUCTIONS, anon=anon,
                           already=already, teacher_has_photo=photos.has_any_photo(form.get('teacher_emp')),
                           feedback_arrival=request.args.get('from_feedback') == '1')


@bp.route('/form/<int:form_id>/submit', methods=['POST'])
@role_required('student')
def submit(form_id):
    user = current_user()
    gate = _feedback_gate(user, form_id)
    if gate:
        return gate
    anon = _needs_code(form_id)
    ok, message = services.submit_feedback(user['id'], form_id, request.form)
    if ok:
        # Auto-open the next pending form after submit. For a protected (anonymous) form the
        # authenticator code was already accepted for this login session, so the student
        # is taken straight to the next form without being asked for a code again.
        forms = services.student_forms(user['id'])
        pending = [f for f in forms
                   if f['status'] == 'open' and not f['submission_id']
                   and f['id'] != form_id and not f.get('locked')]
        if anon:
            if pending:
                return redirect(url_for('student.view_form', form_id=pending[0]['id'],
                                        from_feedback=1))
            # Nothing left to fill in: show the "submitted" page with the Log out option.
            return redirect(url_for('student.anonymous_submitted'))
        flash(message, 'success')
        if pending:
            flash('Opening your next pending form…', 'info')
            return redirect(url_for('student.view_form', form_id=pending[0]['id'], from_feedback=1))
        return redirect(url_for('student.dashboard'))
    flash(message, 'error')
    # Re-render preserving the student's answers so nothing is lost.
    form = services.student_form(user['id'], form_id)
    if not form:
        return redirect(url_for('student.dashboard'))
    questions = services.form_questions(form_id)
    return render_template('student_form.html', user=user, form=form, questions=questions,
                           default_instructions=services.DEFAULT_INSTRUCTIONS,
                           already=None, form_data=request.form,
                           anon=anon and form['status'] == 'open',
                           teacher_has_photo=photos.has_any_photo(form.get('teacher_emp')))


@bp.route('/anonymous/submitted')
@role_required('student')
def anonymous_submitted():
    """Shown after an anonymous submission. Holds no student, form or answer details."""
    if not session.get(FEEDBACK_CODE_KEY):
        return redirect(url_for('student.progress'))
    return render_template('anonymous_submitted.html')


@bp.post('/anonymous/logout')
@role_required('student')
def anonymous_logout():
    """Leave anonymous mode. The next protected form asks for a new code."""
    session.pop(FEEDBACK_CODE_KEY, None)
    return redirect(url_for('student.progress'))


@bp.route('/progress')
@role_required('student')
def progress():
    """How many feedback forms are submitted and how many are still pending."""
    user = current_user()
    forms = services.student_forms(user['id'])
    submitted = [f for f in forms if f['submission_id']]
    pending = [f for f in forms if f['status'] == 'open' and not f['submission_id']]
    next_form = next((f for f in pending if not f.get('locked')), None)
    return render_template('student_progress.html', user=user, submitted=submitted,
                           pending=pending, next_form=next_form)


@bp.route('/notifications')
@role_required('student')
def notifications():
    """Change 4: in-app notification inbox."""
    user = current_user()
    services.add_student_feedback_reminders(user['id'])
    rows = db.query(
        """SELECT n.*, f.deadline_at, f.status AS form_status, f.is_deleted AS form_deleted
           FROM notifications n LEFT JOIN feedback_forms f ON f.id=n.form_id
           WHERE n.user_id=? AND n.is_read=0 ORDER BY n.created_at DESC, n.id DESC LIMIT 100""",
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
