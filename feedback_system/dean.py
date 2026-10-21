"""Dean portal — publication monitoring and department analytics."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from . import db
from .security import audit,  current_user, role_required, scoped_branch_ids_or_none

bp = Blueprint('dean', __name__, url_prefix='/dean')


def _dean_branches(user):
    ids = scoped_branch_ids_or_none(user)
    if not ids:
        return []
    placeholders = ','.join('?' * len(ids))
    return db.query(
        f'SELECT id, code, name FROM branches WHERE id IN ({placeholders}) ORDER BY code',
        tuple(ids))


@bp.route('/')
@role_required('dean')
def dashboard():
    user = current_user()
    branches = _dean_branches(user)
    branch_ids = [b['id'] for b in branches]

    # Publication monitoring requires configured feedback_requirements.
    requirements_configured = False
    pub_rows = []
    if branch_ids:
        ph = ','.join('?' * len(branch_ids))
        req_count = db.query(
            f"""SELECT COUNT(*) c FROM feedback_requirements
                WHERE branch_id IN ({ph}) AND status != 'cancelled'""",
            tuple(branch_ids), one=True)['c']
        requirements_configured = req_count > 0

        if requirements_configured:
            pub_rows = db.query(
                f"""SELECT b.code AS branch_code, b.name AS branch_name,
                           COUNT(fr.id) AS required_count,
                           SUM(CASE WHEN fr.matched_form_id IS NOT NULL
                                    AND ff.status IN ('open','closed','archived')
                                    THEN 1 ELSE 0 END) AS published_count
                    FROM feedback_requirements fr
                    JOIN branches b ON b.id=fr.branch_id
                    LEFT JOIN feedback_forms ff ON ff.id=fr.matched_form_id
                    WHERE fr.branch_id IN ({ph}) AND fr.status='required'
                    GROUP BY b.id, b.code, b.name
                    ORDER BY b.code""",
                tuple(branch_ids))

    return render_template('dean_dashboard.html', user=user, branches=branches,
                           requirements_configured=requirements_configured,
                           pub_rows=pub_rows)


@bp.route('/publication')
@role_required('dean')
def publication():
    user = current_user()
    branches = _dean_branches(user)
    branch_ids = [b['id'] for b in branches]
    requirements_configured = False
    rows = []
    if branch_ids:
        ph = ','.join('?' * len(branch_ids))
        req_count = db.query(
            f"""SELECT COUNT(*) c FROM feedback_requirements
                WHERE branch_id IN ({ph})""",
            tuple(branch_ids), one=True)['c']
        requirements_configured = req_count > 0
        if requirements_configured:
            rows = db.query(
                f"""SELECT fr.id, fr.title, fr.category, fr.deadline, fr.status,
                           fr.subject_code, b.code AS branch_code, b.name AS branch_name,
                           ff.id AS form_id, ff.status AS form_status, ff.published_at
                    FROM feedback_requirements fr
                    JOIN branches b ON b.id=fr.branch_id
                    LEFT JOIN feedback_forms ff ON ff.id=fr.matched_form_id
                    WHERE fr.branch_id IN ({ph})
                    ORDER BY b.code, fr.deadline""",
                tuple(branch_ids))
    return render_template('dean_publication.html', rows=rows,
                           requirements_configured=requirements_configured,
                           branches=branches)


@bp.route('/analytics')
@role_required('dean')
def analytics():
    user = current_user()
    branches = _dean_branches(user)
    branch_ids = [b['id'] for b in branches]
    # Real aggregates only — no fabricated scores.
    dept_stats = []
    if branch_ids:
        ph = ','.join('?' * len(branch_ids))
        dept_stats = db.query(
            f"""SELECT b.code, b.name,
                       COUNT(DISTINCT f.id) AS form_count,
                       SUM(CASE WHEN f.category='main' THEN 1 ELSE 0 END) AS main_forms,
                       SUM(CASE WHEN f.category='second' THEN 1 ELSE 0 END) AS second_forms,
                       (SELECT COUNT(*) FROM submissions s
                        JOIN feedback_forms ff ON ff.id=s.form_id
                        WHERE ff.branch_id=b.id AND ff.is_deleted=0) AS response_count
                FROM branches b
                LEFT JOIN feedback_forms f ON f.branch_id=b.id AND f.is_deleted=0
                  AND f.status IN ('open','closed','archived')
                WHERE b.id IN ({ph})
                GROUP BY b.id, b.code, b.name
                ORDER BY b.code""",
            tuple(branch_ids))
    return render_template('dean_analytics.html', dept_stats=dept_stats,
                           branches=branches)


@bp.route('/historical')
@role_required('dean')
def historical():
    user = current_user()
    branches = _dean_branches(user)
    # Historical comparison requires comparable stored answers.
    # When insufficient data exists, mark as unavailable (do not invent scores).
    return render_template('dean_historical.html', branches=branches,
                           comparison_available=False,
                           reason='Insufficient comparable historical periods or '
                                  'configured department mappings for score-point change.')


# --------------------------------------------------------------------------- #
# First-login onboarding: email verify + select HODs
# --------------------------------------------------------------------------- #
@bp.route('/onboarding', methods=['GET', 'POST'])
@role_required('dean')
def onboarding():
    from flask import session as _session
    from . import verification, mailer

    user = current_user()
    row = db.query(
        'SELECT email, email_verified, onboarding_complete FROM users WHERE id=?',
        (user['id'],), one=True)
    if row and row.get('onboarding_complete'):
        return redirect(url_for('dean.dashboard'))

    email_verified = bool(row and row.get('email_verified'))
    step = request.args.get('step', 'email' if not email_verified else 'hods')
    staging = _session.get('dean_onboard_staging') or []  # list of hod_user_ids

    # Eligible HODs: active, onboarded or with scopes, not already exclusively
    # claimed in a way that would create Dean overlap — we show all active HODs
    # whose branch scopes intersect the Dean's department scopes.
    dean_branches = set()
    for r in db.query(
            'SELECT branch_id FROM user_department_scopes WHERE user_id=?',
            (user['id'],)):
        dean_branches.add(r['branch_id'])

    eligible_hods = db.query(
        """SELECT u.id, u.name, u.emp_code, u.email, u.onboarding_complete
           FROM users u
           WHERE u.role='hod' AND u.is_active=1
           ORDER BY u.name""")

    # Filter to HODs that share at least one branch with the Dean (if Dean has scopes)
    if dean_branches:
        filtered = []
        for h in eligible_hods:
            h_branches = {r['branch_id'] for r in db.query(
                'SELECT branch_id FROM user_department_scopes WHERE user_id=?',
                (h['id'],))}
            # Also include allocation branches
            for r in db.query(
                    'SELECT DISTINCT branch_id FROM hod_allocations WHERE hod_user_id=?',
                    (h['id'],)):
                h_branches.add(r['branch_id'])
            if h_branches & dean_branches or not h_branches:
                filtered.append(h)
        eligible_hods = filtered

    preview = None

    if request.method == 'POST':
        action = request.form.get('action') or ''
        if action == 'send_code':
            email = (request.form.get('email') or user.get('email') or '').strip().lower()
            if not email or '@' not in email:
                flash('Enter a valid official email.', 'error')
            else:
                try:
                    code = verification.issue(user['id'], 'verify_email', email)
                    subj, body, html = mailer.code_email(code, 'verify_email', 10)
                    mailer.send_email(email, subj, body, html)
                    flash('Verification code sent.', 'success')
                    step = 'verify'
                except verification.Cooldown as cd:
                    flash(f'Wait {cd.seconds}s before resending.', 'error')
                except Exception:
                    flash('Could not send code.', 'error')
        elif action == 'verify_code':
            verified = verification.check(user['id'], 'verify_email',
                                          (request.form.get('code') or '').strip())
            if verified:
                db.execute('UPDATE users SET email_verified=1, email=? WHERE id=?',
                           (verified, user['id']))
                audit('dean.email_verified', 'users', user['id'])
                flash('Email verified.', 'success')
                email_verified = True
                step = 'hods'
            else:
                flash('Invalid or expired code.', 'error')
                step = 'verify'
        elif action == 'toggle_hod':
            hid = request.form.get('hod_id', type=int)
            if hid and any(h['id'] == hid for h in eligible_hods):
                if hid in staging:
                    staging.remove(hid)
                else:
                    staging.append(hid)
                _session['dean_onboard_staging'] = staging
            step = 'hods'
        elif action == 'preview':
            step = 'preview'
            preview = _dean_preview(staging)
        elif action == 'confirm':
            if not staging:
                flash('Select at least one HOD.', 'error')
                step = 'hods'
            else:
                conn = db.get_db()
                try:
                    for hid in staging:
                        conn.execute(
                            """INSERT INTO dean_hod_assignments
                               (dean_user_id, hod_user_id, created_by)
                               VALUES(?,?,?)
                               ON CONFLICT(dean_user_id, hod_user_id) DO NOTHING""",
                            (user['id'], hid, user['id']))
                    conn.execute(
                        'UPDATE users SET onboarding_complete=1 WHERE id=?',
                        (user['id'],))
                    conn.commit()
                    _session.pop('dean_onboard_staging', None)
                    audit('dean.onboarding_complete', 'users', user['id'],
                          f'{len(staging)} HODs')
                    flash('Onboarding complete.', 'success')
                    return redirect(url_for('dean.dashboard'))
                except Exception as exc:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    flash(f'Save failed: {exc}', 'error')
                    step = 'hods'

    if step == 'preview' and preview is None:
        preview = _dean_preview(staging)

    return render_template(
        'dean_onboarding.html',
        step=step, email_verified=email_verified, user=user,
        eligible_hods=eligible_hods, staging=staging, preview=preview)


def _dean_preview(hod_ids):
    if not hod_ids:
        return {'hods': [], 'sessions': 0, 'students': 0, 'forms': 0}
    from .security import hod_allocation_keys
    items = []
    sess = stud = forms = 0
    seen_tt = set()
    for hid in hod_ids:
        h = db.query('SELECT id, name, emp_code FROM users WHERE id=?', (hid,), one=True)
        allocs = hod_allocation_keys(hid)
        for a in allocs:
            if a['timetable_id'] in seen_tt:
                continue
            seen_tt.add(a['timetable_id'])
            c = db.query(
                'SELECT COUNT(*) c FROM class_sessions WHERE timetable_id=? AND is_deleted=0',
                (a['timetable_id'],), one=True)['c']
            s = db.query(
                'SELECT COUNT(DISTINCT student_user_id) c FROM enrollments WHERE timetable_id=? AND is_deleted=0',
                (a['timetable_id'],), one=True)['c']
            f = db.query(
                'SELECT COUNT(*) c FROM feedback_forms WHERE timetable_id=? AND is_deleted=0',
                (a['timetable_id'],), one=True)['c']
            sess += c
            stud += s
            forms += f
        items.append({'hod': h, 'allocations': allocs})
    return {'hods': items, 'sessions': sess, 'students': stud, 'forms': forms}


# ---------------------------------------------------------------------------
# Change 12: Improvement Action Tracker + Faculty Performance (Dean)
# ---------------------------------------------------------------------------

@bp.route('/actions')
@role_required('dean')
def actions():
    user = current_user()
    branches = _dean_branches(user)
    branch_ids = [b['id'] for b in branches]
    rows = []
    if branch_ids:
        ph = ','.join('?' * len(branch_ids))
        rows = db.query(
            f"""SELECT ia.*, b.code AS branch_code, b.name AS branch_name
                FROM improvement_actions ia
                LEFT JOIN branches b ON b.id = ia.branch_id
                WHERE ia.branch_id IN ({ph}) OR ia.branch_id IS NULL
                ORDER BY
                  CASE ia.status
                    WHEN 'in_progress' THEN 0
                    WHEN 'planned' THEN 1
                    WHEN 'completed' THEN 2
                    ELSE 3 END,
                  ia.due_date IS NULL, ia.due_date""",
            tuple(branch_ids))
    return render_template('dean_actions.html', user=user, branches=branches, rows=rows)


@bp.route('/actions/new', methods=['GET', 'POST'])
@role_required('dean')
def action_new():
    user = current_user()
    branches = _dean_branches(user)
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()[:200]
        if not title:
            flash('Title is required.', 'error')
        else:
            branch_id = request.form.get('branch_id', type=int)
            allowed = {b['id'] for b in branches}
            if branch_id and branch_id not in allowed:
                flash('Invalid department.', 'error')
            else:
                from datetime import datetime
                now = datetime.utcnow().isoformat(timespec='seconds')
                aid = db.insert_id(
                    """INSERT INTO improvement_actions
                       (branch_id, title, description, status, priority,
                        owner_name, due_date, created_by, created_at, updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (branch_id,
                     title,
                     (request.form.get('description') or '').strip()[:2000],
                     request.form.get('status') or 'planned',
                     request.form.get('priority') or 'medium',
                     (request.form.get('owner_name') or '').strip()[:120],
                     (request.form.get('due_date') or '').strip() or None,
                     user['id'], now, now))
                db.get_db().execute(
                    """INSERT INTO improvement_action_log
                       (action_id, actor_user_id, note, old_status, new_status)
                       VALUES(?,?,?,?,?)""",
                    (aid, user['id'], 'Created', None, request.form.get('status') or 'planned'))
                db.get_db().commit()
                flash('Action created.', 'success')
                return redirect(url_for('dean.actions'))
    return render_template('dean_action_form.html', user=user, branches=branches, action=None)


@bp.route('/actions/<int:aid>/edit', methods=['GET', 'POST'])
@role_required('dean')
def action_edit(aid):
    user = current_user()
    branches = _dean_branches(user)
    branch_ids = {b['id'] for b in branches}
    action = db.query('SELECT * FROM improvement_actions WHERE id=?', (aid,), one=True)
    if not action:
        abort(404)
    if action['branch_id'] and action['branch_id'] not in branch_ids:
        abort(403)
    if request.method == 'POST':
        from datetime import datetime
        now = datetime.utcnow().isoformat(timespec='seconds')
        new_status = request.form.get('status') or action['status']
        old_status = action['status']
        completed_at = action.get('completed_at')
        if new_status == 'completed' and old_status != 'completed':
            completed_at = now
        elif new_status != 'completed':
            completed_at = None
        conn = db.get_db()
        conn.execute(
            """UPDATE improvement_actions SET
               title=?, description=?, status=?, priority=?, owner_name=?,
               due_date=?, branch_id=?, updated_at=?, completed_at=?
               WHERE id=?""",
            ((request.form.get('title') or action['title']).strip()[:200],
             (request.form.get('description') or '').strip()[:2000],
             new_status,
             request.form.get('priority') or action['priority'],
             (request.form.get('owner_name') or '').strip()[:120],
             (request.form.get('due_date') or '').strip() or None,
             request.form.get('branch_id', type=int) or action['branch_id'],
             now, completed_at, aid))
        if new_status != old_status:
            conn.execute(
                """INSERT INTO improvement_action_log
                   (action_id, actor_user_id, note, old_status, new_status)
                   VALUES(?,?,?,?,?)""",
                (aid, user['id'], (request.form.get('note') or 'Status changed')[:500],
                 old_status, new_status))
        conn.commit()
        flash('Action updated.', 'success')
        return redirect(url_for('dean.actions'))
    logs = db.query(
        'SELECT * FROM improvement_action_log WHERE action_id=? ORDER BY created_at DESC',
        (aid,))
    return render_template('dean_action_form.html', user=user, branches=branches,
                           action=action, logs=logs)


@bp.route('/performance')
@role_required('dean')
def performance():
    """Dean faculty performance — reuses central scoring service."""
    from . import scoring
    user = current_user()
    branches = _dean_branches(user)
    branch_ids = [b['id'] for b in branches]
    academic_year_id = request.args.get('academic_year_id', type=int)
    semester_id = request.args.get('semester_id', type=int)
    rows = []
    if branch_ids:
        rows = scoring.faculty_scores_for_branches(
            branch_ids,
            academic_year_id=academic_year_id,
            semester_id=semester_id)
    years = db.query('SELECT id, name FROM academic_years ORDER BY name DESC')
    semesters = db.query('SELECT id, number FROM semesters ORDER BY number')
    return render_template(
        'dean_performance.html', user=user, branches=branches, rows=rows,
        years=years, semesters=semesters,
        filters={'academic_year_id': academic_year_id, 'semester_id': semester_id})
