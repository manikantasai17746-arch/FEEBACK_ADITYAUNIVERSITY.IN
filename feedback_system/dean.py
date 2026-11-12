"""Dean portal — publication monitoring and department analytics."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from . import db
from .security import (audit, current_user, role_required, scoped_branch_ids_or_none,
                       scoped_branches)

bp = Blueprint('dean', __name__, url_prefix='/dean')


def _dean_branches(user):
    # One query (was: scope-ids query + branches query).
    return scoped_branches(user)


def _in_clause(ids):
    return ','.join('?' * len(ids))


def _pct(part, whole):
    """Percentage rounded to 1 decimal; 0 when there is nothing to divide by."""
    if not whole:
        return 0.0
    return round(min(100.0, 100.0 * part / whole), 1)


def _fmt_date(value):
    """'2026-10-24 10:24:00' -> '24 Oct 2026' (None when missing/unparseable)."""
    from datetime import date
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10]).strftime('%d %b %Y')
    except ValueError:
        return str(value)[:10]


def _dean_hods(user, branch_ids):
    """Active HODs in the Dean's scope: assigned HODs first, else by branch."""
    rows = db.query(
        """SELECT u.id, u.name, u.emp_code, u.onboarding_complete
           FROM dean_hod_assignments dha
           JOIN users u ON u.id = dha.hod_user_id
           WHERE dha.dean_user_id=? AND u.role='hod' AND u.is_active=1
           ORDER BY u.name""", (user['id'],))
    if rows or not branch_ids:
        return rows
    ph = _in_clause(branch_ids)
    return db.query(
        f"""SELECT DISTINCT u.id, u.name, u.emp_code, u.onboarding_complete
            FROM users u
            WHERE u.role='hod' AND u.is_active=1 AND (
                  u.id IN (SELECT hod_user_id FROM hod_allocations
                           WHERE branch_id IN ({ph}))
               OR u.id IN (SELECT user_id FROM user_department_scopes
                           WHERE branch_id IN ({ph})))
            ORDER BY u.name""", tuple(branch_ids) * 2)


def _dashboard_data(user, branches, ay_id, sem_id, form_id):
    """Every number on the Dean dashboard, computed from real rows only."""
    from datetime import date, timedelta

    branch_ids = [b['id'] for b in branches]
    ph = _in_clause(branch_ids)
    data = {'branch_count': len(branches)}

    # ---- HODs -------------------------------------------------------------
    hods = _dean_hods(user, branch_ids)
    hod_ids = [h['id'] for h in hods]
    data['hod_count'] = len(hods)
    data['hod_onboarded'] = sum(1 for h in hods if h['onboarding_complete'])
    hod_names = {h['id']: h['name'] for h in hods}

    # branch -> HOD names (allocations first, then explicit department scopes)
    branch_hods = {b['id']: [] for b in branches}
    if hod_ids:
        hph = _in_clause(hod_ids)
        pairs = db.query(
            f"""SELECT DISTINCT hod_user_id, branch_id FROM hod_allocations
                WHERE hod_user_id IN ({hph}) AND branch_id IN ({ph})""",
            tuple(hod_ids) + tuple(branch_ids))
        pairs += db.query(
            f"""SELECT DISTINCT user_id AS hod_user_id, branch_id
                FROM user_department_scopes
                WHERE user_id IN ({hph}) AND branch_id IN ({ph})""",
            tuple(hod_ids) + tuple(branch_ids))
        for p in pairs:
            lst = branch_hods.get(p['branch_id'])
            if lst is not None and p['hod_user_id'] not in lst:
                lst.append(p['hod_user_id'])

    # ---- Forms (published ones only: open / closed / archived) ------------
    sql = f"""SELECT ff.id, ff.title, ff.status, ff.branch_id
              FROM feedback_forms ff
              WHERE ff.branch_id IN ({ph}) AND ff.is_deleted=0
                AND ff.status IN ('open','closed','archived')"""
    args = list(branch_ids)
    if ay_id:
        sql += ' AND ff.academic_year_id=?'
        args.append(ay_id)
    if sem_id:
        sql += ' AND ff.semester_id=?'
        args.append(sem_id)
    scope_forms = db.query(sql + ' ORDER BY ff.title', tuple(args))
    # Dropdown label "<emp id> (<department>) <title>" so forms that share the
    # same title (e.g. many "Phase 1") can be told apart.
    emp_by_form = {}
    if scope_forms:
        sph = _in_clause([f['id'] for f in scope_forms])
        for r in db.query(
                f"""SELECT DISTINCT fs.form_id, u.emp_code
                    FROM form_sessions fs
                    JOIN class_sessions cs ON cs.id=fs.session_id
                    JOIN users u ON u.id=cs.teacher_user_id
                    WHERE fs.form_id IN ({sph}) AND u.emp_code IS NOT NULL
                      AND u.emp_code != '' ORDER BY u.emp_code""",
                tuple(f['id'] for f in scope_forms)):
            emp_by_form.setdefault(r['form_id'], []).append(r['emp_code'])
    code_by_branch = {b['id']: b['code'] for b in branches}

    def _form_label(f):
        emp = ', '.join(emp_by_form.get(f['id'], []))
        dept = code_by_branch.get(f['branch_id'], '')
        head = ' '.join(x for x in (emp, f'({dept})' if dept else '') if x)
        return f"{head} {f['title']}".strip()

    data['form_options'] = [{'id': f['id'], 'title': _form_label(f)} for f in scope_forms]
    forms = [f for f in scope_forms if not form_id or f['id'] == form_id]
    fids = [f['id'] for f in forms]
    data['active_forms'] = sum(1 for f in forms if f['status'] == 'open')

    # Students are counted ONCE per department, however many feedback forms
    # (one per subject/teacher) they are eligible for.  Summing per-form counts
    # would count the same student once per form, so we work with sets of
    # student ids instead.  "Submitted" = the student has submitted at least
    # one of the in-scope forms they are eligible for.
    form_branch = {f['id']: f['branch_id'] for f in forms}
    elig_pairs, sub_pairs, last = set(), set(), {}
    if fids:
        fph = _in_clause(fids)
        for r in db.query(
                f"""SELECT DISTINCT fs.form_id, ss.student_user_id AS sid
                    FROM form_sessions fs
                    JOIN session_students ss ON ss.session_id=fs.session_id
                    JOIN users u ON u.id=ss.student_user_id AND u.is_active=1
                    WHERE fs.form_id IN ({fph})""",
                tuple(fids)):
            elig_pairs.add((r['form_id'], r['sid']))
        # Completion only - answers are never read, so anonymity is untouched.
        for r in db.query(
                f"""SELECT form_id, student_user_id AS sid, submitted_at
                    FROM submissions WHERE form_id IN ({fph})""",
                tuple(fids)):
            sub_pairs.add((r['form_id'], r['sid']))
            la = r['submitted_at']
            if la and (last.get(r['form_id']) is None or str(la) > str(last[r['form_id']])):
                last[r['form_id']] = la

    per = {b['id']: {'eligible': 0, 'submitted': 0, 'last': None,
                     'e_ids': set(), 's_ids': set()} for b in branches}
    all_e, all_s = set(), set()
    for (fid, sid) in elig_pairs:
        per[form_branch[fid]]['e_ids'].add(sid)
        all_e.add(sid)
    for (fid, sid) in sub_pairs:
        if (fid, sid) in elig_pairs:      # only students eligible for that form
            per[form_branch[fid]]['s_ids'].add(sid)
            all_s.add(sid)
    for f in forms:
        d = per[f['branch_id']]
        la = last.get(f['id'])
        if la and (d['last'] is None or str(la) > str(d['last'])):
            d['last'] = la
    for d in per.values():
        d['eligible'] = len(d['e_ids'])
        d['submitted'] = len(d['s_ids'])
    tot_e, tot_s = len(all_e), len(all_s)
    data['eligible'] = tot_e
    data['submitted'] = tot_s
    data['pending'] = max(tot_e - tot_s, 0)
    data['rate'] = _pct(tot_s, tot_e)

    # ---- Requirements / publication status --------------------------------
    sql = f"""SELECT fr.id, fr.branch_id, fr.title, fr.deadline, fr.status,
                     fr.matched_form_id, ff.status AS form_status
              FROM feedback_requirements fr
              LEFT JOIN feedback_forms ff ON ff.id=fr.matched_form_id
              WHERE fr.branch_id IN ({ph}) AND fr.status != 'cancelled'"""
    args = list(branch_ids)
    if ay_id:
        sql += ' AND fr.academic_year_id=?'
        args.append(ay_id)
    if sem_id:
        sql += ' AND fr.semester_id=?'
        args.append(sem_id)
    reqs = db.query(sql, tuple(args))
    published_states = ('open', 'closed', 'archived')
    today = date.today()
    soon = today + timedelta(days=7)

    rb = {b['id']: {'total': 0, 'required': 0, 'published': 0} for b in branches}
    deadline_soon = 0
    for r in reqs:
        d = rb[r['branch_id']]
        d['total'] += 1
        is_pub = bool(r['matched_form_id']) and r['form_status'] in published_states
        if r['status'] == 'required':
            d['required'] += 1
            if is_pub:
                d['published'] += 1
            else:
                try:
                    dl = date.fromisoformat(str(r['deadline'] or '')[:10])
                    if today <= dl <= soon:
                        deadline_soon += 1
                except ValueError:
                    pass

    rows = []
    for i, b in enumerate(branches, 1):
        d, p = rb[b['id']], per[b['id']]
        if d['required']:
            published = d['published'] == d['required']
            detail = f"{d['published']}/{d['required']}"
        else:
            published = any(f['branch_id'] == b['id'] for f in forms)
            detail = ''
        rate = _pct(p['submitted'], p['eligible'])
        names = [hod_names[h] for h in branch_hods[b['id']] if h in hod_names]
        rows.append({
            'n': i, 'id': b['id'], 'code': b['code'], 'name': b['name'],
            'hod': ', '.join(names) if names else '\u2014',
            'hod_ids': [h for h in branch_hods[b['id']] if h in hod_names],
            'configured': d['total'] > 0,
            'published': published, 'pub_detail': detail,
            'rate': rate, 'has_rate': p['eligible'] > 0,
            'last': _fmt_date(p['last']),
            'eligible': p['eligible'], 'submitted': p['submitted'],
            'pending': max(p['eligible'] - p['submitted'], 0),
        })
    data['rows'] = rows

    # ---- Chart ------------------------------------------------------------
    peak = max([r['eligible'] for r in rows] + [1])
    step = 10000
    # tidy "nice" axis step so there are at most 5 gridlines
    for nice in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000):
        if peak / nice <= 5:
            step = nice
            break
    top = step * (-(-peak // step))
    data['chart_top'] = top
    data['chart_ticks'] = [top - i * step for i in range(int(top // step) + 1)]
    data['chart'] = [{
        'code': r['code'], 'eligible': r['eligible'], 'submitted': r['submitted'],
        'pending': r['pending'],
        'h_e': round(100.0 * r['eligible'] / top, 1) if top else 0,
        'h_s': round(100.0 * r['submitted'] / top, 1) if top else 0,
        'h_p': round(100.0 * r['pending'] / top, 1) if top else 0,
    } for r in rows]

    # ---- Alerts -----------------------------------------------------------
    closed_ids = [f['id'] for f in forms if f['status'] == 'closed']
    awaiting = 0
    if closed_ids:
        cph = _in_clause(closed_ids)
        done = {r['form_id'] for r in db.query(
            f"""SELECT DISTINCT form_id FROM result_publications
                WHERE is_published=1 AND form_id IN ({cph})""", tuple(closed_ids))}
        awaiting = sum(1 for i in closed_ids if i not in done)
    unpublished_hods = set()
    for r in rows:
        if not r['published']:
            unpublished_hods.update(r['hod_ids'])
    data['alerts'] = [
        {'label': 'Departments without feedback requirements', 'count': sum(1 for r in rows if not r['configured']),
         'tone': 'red', 'icon': 'warn', 'endpoint': 'dean.publication'},
        {'label': 'Feedback forms approaching deadline', 'count': deadline_soon,
         'tone': 'amber', 'icon': 'clock', 'endpoint': 'dean.publication'},
        {'label': 'Low submission rate (< 60%)', 'count': sum(1 for r in rows if r['has_rate'] and r['rate'] < 60),
         'tone': 'amber', 'icon': 'clock', 'endpoint': 'dean.analytics'},
        {'label': 'HODs yet to publish feedback', 'count': len(unpublished_hods),
         'tone': 'blue', 'icon': 'info', 'endpoint': 'dean.publication'},
        {'label': 'Completed forms awaiting publication', 'count': awaiting,
         'tone': 'blue', 'icon': 'doc', 'endpoint': 'dean.performance'},
    ]
    return data


@bp.route('/')
@role_required('dean')
def dashboard():
    user = current_user()
    branches = _dean_branches(user)
    branch_ids = [b['id'] for b in branches]

    years = db.query('SELECT id, name, is_active FROM academic_years ORDER BY name DESC')
    semesters = db.query('SELECT id, number, label FROM semesters ORDER BY number')

    # Filters: a missing parameter means "default"; an empty one means "All".
    if 'academic_year_id' in request.args:
        ay_id = request.args.get('academic_year_id', type=int)
    else:
        active = [y for y in years if y['is_active']]
        ay_id = active[0]['id'] if active else None
    sem_id = request.args.get('semester_id', type=int)
    form_id = request.args.get('form_id', type=int)

    dash = None
    if branch_ids:
        dash = _dashboard_data(user, branches, ay_id, sem_id, form_id)
        if form_id and form_id not in {f['id'] for f in dash['form_options']}:
            form_id = None  # stale selection after changing year/semester

    return render_template(
        'dean_dashboard.html', user=user, branches=branches, dash=dash,
        years=years, semesters=semesters, new_hod_count=len(_available_hods(user)),
        filters={'academic_year_id': ay_id, 'semester_id': sem_id, 'form_id': form_id})


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
    # Live publication data from the real forms, so the page is useful even
    # before Admin defines any requirement.  Same student-level counting as
    # the dashboard (each student counted once per department).
    summary = []
    if branch_ids:
        dash = _dashboard_data(user, branches, None, None, None)
        by_status = {b['id']: {'open': 0, 'closed': 0, 'archived': 0} for b in branches}
        for r in db.query(
                f"""SELECT branch_id, status, COUNT(*) AS c FROM feedback_forms
                    WHERE branch_id IN ({ph}) AND is_deleted=0
                      AND status IN ('open','closed','archived')
                    GROUP BY branch_id, status""", tuple(branch_ids)):
            by_status[r['branch_id']][r['status']] = r['c']
        for r in dash['rows']:
            summary.append(dict(r, forms=by_status[r['id']],
                                form_total=sum(by_status[r['id']].values())))
    return render_template('dean_publication.html', rows=rows, summary=summary,
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


# ---------------------------------------------------------------------------
# My HODs: Dean can add HODs created AFTER onboarding (any department)
# ---------------------------------------------------------------------------
def _hod_departments(hod_id):
    rows = db.query(
        """SELECT DISTINCT b.code FROM branches b
           WHERE b.id IN (SELECT branch_id FROM hod_allocations WHERE hod_user_id=?)
              OR b.id IN (SELECT branch_id FROM user_department_scopes WHERE user_id=?)
           ORDER BY b.code""", (hod_id, hod_id))
    return ', '.join(r['code'] for r in rows)


def _assigned_hods(user):
    rows = db.query(
        """SELECT u.id, u.name, u.emp_code, u.email, u.onboarding_complete
           FROM dean_hod_assignments dha
           JOIN users u ON u.id = dha.hod_user_id
           WHERE dha.dean_user_id=? AND u.role='hod' AND u.is_active=1
           ORDER BY u.name""", (user['id'],))
    rows = [dict(r) for r in rows]
    for r in rows:
        r['departments'] = _hod_departments(r['id'])
    return rows


def _available_hods(user, with_departments=False):
    """Active HODs that are not yet linked to this Dean (e.g. newly created)."""
    rows = db.query(
        """SELECT u.id, u.name, u.emp_code, u.email, u.onboarding_complete
           FROM users u
           WHERE u.role='hod' AND u.is_active=1
             AND u.id NOT IN (SELECT hod_user_id FROM dean_hod_assignments
                              WHERE dean_user_id=?)
           ORDER BY u.name""", (user['id'],))
    if with_departments:
        rows = [dict(r) for r in rows]
        for r in rows:
            r['departments'] = _hod_departments(r['id'])
    return rows


@bp.route('/hods', methods=['GET', 'POST'])
@role_required('dean')
def my_hods():
    user = current_user()
    if request.method == 'POST':
        allowed = {h['id'] for h in _available_hods(user)}
        chosen = [i for i in request.form.getlist('hod_id', type=int) if i in allowed]
        if not chosen:
            flash('Select at least one HOD to add.', 'error')
        else:
            for hid in chosen:
                db.execute(
                    """INSERT INTO dean_hod_assignments
                       (dean_user_id, hod_user_id, created_by)
                       VALUES(?,?,?)
                       ON CONFLICT(dean_user_id, hod_user_id) DO NOTHING""",
                    (user['id'], hid, user['id']))
            audit('dean.hods_added', 'users', user['id'], f'{len(chosen)} HODs')
            flash(f'{len(chosen)} HOD(s) added to your scope.', 'success')
        return redirect(url_for('dean.my_hods'))
    return render_template('dean_hods.html', user=user,
                           assigned=_assigned_hods(user),
                           available=_available_hods(user, with_departments=True))



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
            f"""SELECT ia.*, b.code AS branch_code, b.name AS branch_name,
                       tu.name AS teacher_name, tu.emp_code AS teacher_emp_code
                FROM improvement_actions ia
                LEFT JOIN branches b ON b.id = ia.branch_id
                LEFT JOIN users tu ON tu.id = ia.teacher_user_id
                WHERE ia.branch_id IN ({ph}) OR ia.branch_id IS NULL
                ORDER BY
                  CASE ia.status
                    WHEN 'in_progress' THEN 0
                    WHEN 'planned' THEN 1
                    WHEN 'completed' THEN 2
                    ELSE 3 END,
                  ia.due_date IS NULL, ia.due_date""",
            tuple(branch_ids))
    from datetime import date
    return render_template('dean_actions.html', user=user, branches=branches, rows=rows,
                           now_iso=date.today().isoformat())


# --------------------------------------------------------------------------- #
# Bulk "feedback review" request: the Dean filters teachers by score band,
# ticks the ones he is not happy with and writes ONE letter.  An action is
# created per teacher and sent to that teacher's HOD, who checks it and replies
# with the improvement taken / planned.
# --------------------------------------------------------------------------- #
DEFAULT_REVIEW_MESSAGE = (
    "Dear {hod},\n\nI have reviewed the student feedback for {name} ({subject}). "
    "The score is {score}%, which is below what I expect.\n\n"
    "Please meet the teacher, check the feedback and the student comments, and reply "
    "to me with the improvement you will make.\n\nRegards,\n{dean}\nDean")


def _bulk_rows(user, band_key, filters):
    """Teacher rows for the bulk page: the chosen band, or everyone (lowest score first)."""
    bands, branches, hods = _dean_band_data(
        user, filters['branch_id'], filters['hod_id'],
        filters['academic_year_id'], filters['semester_id'])
    if band_key and band_key in {b['key'] for b in bands}:
        rows = next(b for b in bands if b['key'] == band_key)['rows']
    else:
        rows = sorted((r for b in bands for r in b['rows']), key=lambda r: r['overall_score'])
    return bands, branches, hods, rows


def _row_hod(row, hod_list, choices):
    """The HOD that owns this teacher's department/semester row."""
    key = (row.get('academic_year_id'), row.get('branch_id'), row.get('semester_id'))
    for h in hod_list:
        if key in h['triples']:
            return h['id'], h['name']
    cands = [h for h in choices if row.get('branch_id') in h['branches']]
    if len(cands) == 1:
        return cands[0]['id'], cands[0]['name']
    return None, None


@bp.route('/actions/bulk')
@role_required('dean')
def actions_bulk():
    from . import mailer
    user = current_user()
    ay, sem, br, hod = _band_filters_from(request.args)
    band_key = (request.args.get('band') or '').strip()
    filters = {'academic_year_id': ay, 'semester_id': sem, 'branch_id': br, 'hod_id': hod}
    bands, branches, hods, rows = _bulk_rows(user, band_key, filters)
    if br not in [b['id'] for b in branches]:
        filters['branch_id'] = None
    if hod not in [h['id'] for h in hods]:
        filters['hod_id'] = None
    ids = sorted({r['teacher_user_id'] for r in rows if r.get('teacher_user_id')})
    open_pairs = set()
    if ids:
        for a in db.query(
                f"""SELECT teacher_user_id, form_id FROM improvement_actions
                    WHERE teacher_user_id IN ({_in_clause(ids)})
                      AND status IN ('planned','in_progress')""", tuple(ids)):
            open_pairs.add((a['teacher_user_id'], a['form_id']))
    for r in rows:
        r['open_action'] = (r.get('teacher_user_id'), r.get('form_id')) in open_pairs
    years = db.query('SELECT id, name FROM academic_years ORDER BY name DESC')
    semesters = db.query('SELECT id, number FROM semesters ORDER BY number')
    return render_template(
        'dean_actions_bulk.html', user=user, branches=branches, hods=hods, years=years,
        semesters=semesters, bands=bands, rows=rows, band_key=band_key, filters=filters,
        default_message=DEFAULT_REVIEW_MESSAGE, mail_ready=mailer.is_configured())


@bp.route('/actions/bulk/create', methods=['POST'])
@role_required('dean')
def actions_bulk_create():
    from datetime import datetime
    from . import mailer
    from html import escape
    user = current_user()
    f = request.form
    ay, sem, br, hod = _band_filters_from(f)
    band_key = (f.get('band') or '').strip()
    filters = {'academic_year_id': ay, 'semester_id': sem, 'branch_id': br, 'hod_id': hod}
    back = url_for('dean.actions_bulk', band=band_key, **{k: v for k, v in filters.items() if v})
    picked = set()
    for v in f.getlist('pick'):
        try:
            t, fm = v.split(':')
            picked.add((int(t), int(fm)))
        except ValueError:
            pass
    if not picked:
        flash('Tick at least one teacher.', 'error')
        return redirect(back)
    if len(picked) > 100:
        flash('Please select at most 100 teachers at a time.', 'error')
        return redirect(back)
    template = (f.get('message') or '').strip()[:4000] or DEFAULT_REVIEW_MESSAGE
    priority = f.get('priority') if f.get('priority') in ('low', 'medium', 'high', 'critical') else 'high'
    due = (f.get('due_date') or '').strip() or None
    _bands, branches, _hods, rows = _bulk_rows(user, band_key, filters)
    branch_ids = [b['id'] for b in branches]
    choices = _hod_choices(user, branch_ids)
    dean_name = dict(user).get('name') or 'Dean'
    existing = {(a['teacher_user_id'], a['form_id']) for a in db.query(
        "SELECT teacher_user_id, form_id FROM improvement_actions "
        "WHERE teacher_user_id IS NOT NULL AND status IN ('planned','in_progress')")}
    now = datetime.utcnow().isoformat(timespec='seconds')
    created, dup, no_hod = 0, 0, 0
    per_hod = {}
    for r in rows:
        key = (r.get('teacher_user_id'), r.get('form_id'))
        if key not in picked:
            continue
        if key in existing:
            dup += 1
            continue
        owner_id, owner_name = _row_hod(r, _hods, choices)
        if not owner_id:
            no_hod += 1
            continue
        subject = ' '.join(x for x in (r.get('subject_code'), r.get('subject_name')) if x) or 'your subject'
        name = r.get('teacher_name') or 'Faculty'
        score = f"{r['overall_score']:.1f}"
        letter = template
        for k, v in {'{hod}': owner_name, '{name}': name, '{score}': score,
                     '{subject}': subject, '{dean}': dean_name}.items():
            letter = letter.replace(k, v)
        low = sorted((c for c in (r.get('criteria') or []) if c.get('score') is not None),
                     key=lambda c: c['score'])[:3]
        details = [f'Teacher: {name} ({r.get("teacher_emp_code") or "-"})',
                   f'Subject: {subject}', f'Score: {score} / 100',
                   f'Responses: {r.get("valid_responses") or r.get("response_count") or 0}']
        if low:
            details.append('Lowest-rated areas: ' + '; '.join(f'{c["text"]} ({c["score"]:.1f})' for c in low))
        aid = db.insert_id(
            """INSERT INTO improvement_actions
               (branch_id, title, description, status, priority, owner_user_id, owner_name,
                due_date, created_by, created_at, updated_at, teacher_user_id, form_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r.get('branch_id'), f'Feedback review: {name} - {subject}'[:200],
             (letter + '\n\n--- Details ---\n' + '\n'.join(details))[:4000],
             'planned', priority, owner_id, owner_name, due, user['id'], now, now,
             key[0], key[1]))
        db.get_db().execute(
            """INSERT INTO improvement_action_log
               (action_id, actor_user_id, note, old_status, new_status) VALUES(?,?,?,?,?)""",
            (aid, user['id'], f'Review request sent to {owner_name}', None, 'planned'))
        existing.add(key)
        created += 1
        per_hod.setdefault(owner_id, []).append((name, subject, score))
    db.get_db().commit()
    mailed = 0
    if per_hod:
        link = url_for('hod.my_actions', _external=True)
        for oid, items in per_hod.items():
            u = db.query('SELECT name, email FROM users WHERE id=?', (oid,), one=True)
            to = ((u['email'] if u else '') or '').strip()
            if not mailer.is_valid_email(to):
                continue
            lines = '\n'.join(f'  - {n} ({s}): {sc}%' for n, s, sc in items)
            text = (f"Dear {u['name'] or 'HOD'},\n\n{dean_name} has sent you {len(items)} "
                    f"feedback review request(s):\n{lines}\n\nPlease check each one and reply "
                    f"with the improvement:\n{link}\n\nAditya University - Faculty Feedback")
            html = ('<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto">'
                    '<h3 style="color:#0B2F6B">New feedback review requests</h3>'
                    f'<div style="white-space:pre-wrap">{escape(text)}</div></div>')
            try:
                if mailer.send_email(to, f'{len(items)} feedback review request(s) from the Dean',
                                     text, html):
                    mailed += 1
            except Exception:
                pass
    audit('dean.actions.bulk', 'improvement_actions', 0,
          f'Created {created}, duplicates {dup}, no HOD {no_hod}, HODs emailed {mailed}')
    if created:
        flash(f'{created} request(s) sent to the HOD(s)' +
              (f'; {mailed} HOD(s) emailed.' if mailed else '.'), 'success')
    if dup:
        flash(f'{dup} teacher(s) skipped: an open action already exists.', 'warning')
    if no_hod:
        flash(f'{no_hod} teacher(s) skipped: no HOD found for their department.', 'warning')
    return redirect(url_for('dean.actions') if created else back)


def _hod_branch_map(hod_ids, branch_ids):
    """hod_user_id -> set of branch ids (allocations + explicit scopes)."""
    out = {h: set() for h in hod_ids}
    if not hod_ids or not branch_ids:
        return out
    hph, bph = _in_clause(hod_ids), _in_clause(branch_ids)
    args = tuple(hod_ids) + tuple(branch_ids)
    for r in db.query(
            f"""SELECT DISTINCT hod_user_id AS uid, branch_id FROM hod_allocations
                WHERE hod_user_id IN ({hph}) AND branch_id IN ({bph})""", args):
        out[r['uid']].add(r['branch_id'])
    for r in db.query(
            f"""SELECT DISTINCT user_id AS uid, branch_id FROM user_department_scopes
                WHERE user_id IN ({hph}) AND branch_id IN ({bph})""", args):
        out[r['uid']].add(r['branch_id'])
    return out


def _hod_choices(user, branch_ids):
    """HODs a Dean may assign actions to, with the departments each one covers."""
    hods = _dean_hods(user, branch_ids)
    bmap = _hod_branch_map([h['id'] for h in hods], branch_ids)
    return [{'id': h['id'], 'name': h['name'], 'emp_code': h['emp_code'],
             'branches': sorted(bmap.get(h['id'], set()))} for h in hods]


def _action_owner(user, branch_ids, branch_id, form):
    """Resolve the 'assign to' field -> (owner_user_id, owner_name, error)."""
    owner_id = form.get('owner_user_id', type=int)
    typed = (form.get('owner_name') or '').strip()[:120]
    if not owner_id:
        return None, typed, None          # unassigned, or a free-text owner
    chosen = next((h for h in _hod_choices(user, branch_ids) if h['id'] == owner_id), None)
    if not chosen:
        return None, typed, 'Invalid HOD selected.'
    if branch_id and branch_id not in chosen['branches']:
        return None, typed, 'That HOD does not belong to the selected department.'
    return owner_id, chosen['name'], None


def _notify_hod_assigned(owner_id, title, description, priority, due_date, dean_name):
    """E-mail a HOD that the Dean assigned them an improvement action (best effort)."""
    from . import mailer
    from html import escape
    try:
        hod = db.query('SELECT name, email FROM users WHERE id=?', (owner_id,), one=True)
        to = ((hod['email'] if hod else '') or '').strip()
        if not mailer.is_valid_email(to):
            return False
        link = url_for('hod.my_actions', _external=True)
        text = (f"Dear {hod['name'] or 'HOD'},\n\n{dean_name} has assigned you a new "
                f"improvement action.\n\nTitle: {title}\nPriority: {priority}\n"
                f"Due date: {due_date or 'not set'}\n\n{description or ''}\n\n"
                f"Open your actions and update the progress here:\n{link}\n\n"
                "Aditya University - Faculty Feedback")
        html = ('<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto">'
                '<h3 style="color:#0B2F6B">New improvement action assigned</h3>'
                f'<p><b>{escape(title)}</b><br>Priority: {escape(priority)} &middot; '
                f'Due: {escape(due_date or "not set")}</p>'
                f'<div style="white-space:pre-wrap">{escape(description or "")}</div>'
                f'<p><a href="{escape(link)}">Open my actions</a></p></div>')
        return mailer.send_email(to, f'Action assigned: {title}', text, html)
    except Exception:
        return False


def _action_prefill(user, branch_ids, teacher_id, form_id):
    """Pre-fill a new action from a teacher's result (title, notes, dept, priority, HOD, due)."""
    from datetime import date, timedelta
    from . import scoring
    if not teacher_id or not branch_ids:
        return None
    rows = scoring.faculty_scores_for_branches(branch_ids, teacher_user_id=teacher_id)
    row = next((r for r in rows if r.get('form_id') == form_id), None) or (rows[0] if rows else None)
    if not row:
        return None
    score = None if (row.get('hidden') or row.get('overall_score') is None) else row['overall_score']
    name = row.get('teacher_name') or 'Faculty'
    subject = ' '.join(x for x in (row.get('subject_code'), row.get('subject_name')) if x)
    low = [c for c in (row.get('criteria') or []) if c.get('score') is not None]
    low.sort(key=lambda c: c['score'])
    lines = [f'Teacher: {name} ({row.get("teacher_emp_code") or "-"})',
             f'Subject: {subject or "-"}',
             f'Department: {row.get("branch_code") or "-"}',
             f'Feedback score: {score:.1f} / 100' if score is not None else 'Feedback score: N/A',
             f'Responses: {row.get("valid_responses") or row.get("response_count") or 0}']
    if low:
        lines.append('Lowest-rated areas:')
        lines += [f'  - {c["text"]} ({c["score"]:.1f})' for c in low[:3]]
    lines.append('\nRequested action: review the feedback with the teacher and agree an improvement plan.')
    if score is None:
        priority = 'medium'
    elif score <= 60:
        priority = 'critical'
    elif score <= 75:
        priority = 'high'
    elif score <= 85:
        priority = 'medium'
    else:
        priority = 'low'
    bid = row.get('branch_id')
    owner = None
    if bid:
        cands = [h for h in _hod_choices(user, branch_ids) if bid in h['branches']]
        if len(cands) == 1:
            owner = cands[0]['id']
    return {'title': f'Improve feedback: {name}' + (f' - {subject}' if subject else ''),
            'description': '\n'.join(lines), 'branch_id': bid, 'priority': priority,
            'owner_user_id': owner,
            'due_date': (date.today() + timedelta(days=14)).isoformat()}


@bp.route('/actions/new', methods=['GET', 'POST'])
@role_required('dean')
def action_new():
    user = current_user()
    branches = _dean_branches(user)
    branch_ids_list = [b['id'] for b in branches]
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()[:200]
        if not title:
            flash('Title is required.', 'error')
        else:
            branch_id = request.form.get('branch_id', type=int)
            allowed = {b['id'] for b in branches}
            owner_id, owner_name, owner_err = _action_owner(
                user, branch_ids_list, branch_id, request.form)
            if branch_id and branch_id not in allowed:
                flash('Invalid department.', 'error')
            elif owner_err:
                flash(owner_err, 'error')
            else:
                from datetime import datetime
                now = datetime.utcnow().isoformat(timespec='seconds')
                aid = db.insert_id(
                    """INSERT INTO improvement_actions
                       (branch_id, title, description, status, priority,
                        owner_user_id, owner_name, due_date, created_by,
                        created_at, updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (branch_id,
                     title,
                     (request.form.get('description') or '').strip()[:2000],
                     request.form.get('status') or 'planned',
                     request.form.get('priority') or 'medium',
                     owner_id,
                     owner_name,
                     (request.form.get('due_date') or '').strip() or None,
                     user['id'], now, now))
                note = 'Created' + (f' and assigned to {owner_name}' if owner_id else '')
                db.get_db().execute(
                    """INSERT INTO improvement_action_log
                       (action_id, actor_user_id, note, old_status, new_status)
                       VALUES(?,?,?,?,?)""",
                    (aid, user['id'], note, None, request.form.get('status') or 'planned'))
                db.get_db().commit()
                msg = 'Action created.'
                if owner_id:
                    if _notify_hod_assigned(
                            owner_id, title,
                            (request.form.get('description') or '').strip()[:2000],
                            request.form.get('priority') or 'medium',
                            (request.form.get('due_date') or '').strip() or None,
                            dict(user).get('name') or 'The Dean'):
                        msg += f' {owner_name} was notified by email.'
                flash(msg, 'success')
                return redirect(url_for('dean.actions'))
    prefill = None
    if request.method == 'GET':
        prefill = _action_prefill(user, branch_ids_list,
                                  request.args.get('teacher_id', type=int),
                                  request.args.get('form_id', type=int))
    return render_template('dean_action_form.html', user=user, branches=branches,
                           action=None, prefill=prefill,
                           hods=_hod_choices(user, branch_ids_list))


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
    hods = _hod_choices(user, list(branch_ids))
    if request.method == 'POST':
        from datetime import datetime
        now = datetime.utcnow().isoformat(timespec='seconds')
        new_branch = request.form.get('branch_id', type=int) or action['branch_id']
        if new_branch and new_branch not in branch_ids:
            flash('Invalid department.', 'error')
            return redirect(url_for('dean.action_edit', aid=aid))
        owner_id, owner_name, owner_err = _action_owner(
            user, list(branch_ids), new_branch, request.form)
        if owner_err:
            flash(owner_err, 'error')
            return redirect(url_for('dean.action_edit', aid=aid))
        new_status = request.form.get('status') or action['status']
        old_status = action['status']
        completed_at = action['completed_at']
        if new_status == 'completed' and old_status != 'completed':
            completed_at = now
        elif new_status != 'completed':
            completed_at = None
        conn = db.get_db()
        conn.execute(
            """UPDATE improvement_actions SET
               title=?, description=?, status=?, priority=?, owner_user_id=?,
               owner_name=?, due_date=?, branch_id=?, updated_at=?, completed_at=?
               WHERE id=?""",
            ((request.form.get('title') or action['title']).strip()[:200],
             (request.form.get('description') or '').strip()[:2000],
             new_status,
             request.form.get('priority') or action['priority'],
             owner_id,
             owner_name,
             (request.form.get('due_date') or '').strip() or None,
             new_branch,
             now, completed_at, aid))
        if new_status != old_status:
            conn.execute(
                """INSERT INTO improvement_action_log
                   (action_id, actor_user_id, note, old_status, new_status)
                   VALUES(?,?,?,?,?)""",
                (aid, user['id'], (request.form.get('note') or 'Status changed')[:500],
                 old_status, new_status))
        if owner_id and owner_id != action['owner_user_id']:
            _notify_hod_assigned(
                owner_id, (request.form.get('title') or action['title']).strip()[:200],
                (request.form.get('description') or '').strip()[:2000],
                request.form.get('priority') or action['priority'],
                (request.form.get('due_date') or '').strip() or None,
                dict(user).get('name') or 'The Dean')
        if owner_id != action['owner_user_id']:
            conn.execute(
                """INSERT INTO improvement_action_log
                   (action_id, actor_user_id, note, old_status, new_status)
                   VALUES(?,?,?,?,?)""",
                (aid, user['id'],
                 (f'Assigned to {owner_name}' if owner_id else 'Unassigned from HOD'),
                 new_status, new_status))
        conn.commit()
        flash('Action updated.', 'success')
        return redirect(url_for('dean.actions'))
    logs = db.query(
        'SELECT * FROM improvement_action_log WHERE action_id=? ORDER BY created_at DESC, id DESC',
        (aid,))
    return render_template('dean_action_form.html', user=user, branches=branches,
                           action=action, logs=logs, hods=hods)


@bp.route('/performance')
@role_required('dean')
def performance():
    """Dean faculty performance — reuses central scoring service."""
    from . import scoring
    from . import services
    user = current_user()
    branches = _dean_branches(user)
    branch_ids = [b['id'] for b in branches]
    academic_year_id = request.args.get('academic_year_id', type=int)
    semester_id = request.args.get('semester_id', type=int)
    # Optional department filter (the Publication page's "View" button).
    branch_id = request.args.get('branch_id', type=int)
    if branch_id not in branch_ids:
        branch_id = None
    scope_ids = [branch_id] if branch_id else branch_ids
    hod_id = request.args.get('hod_id', type=int)
    hods = _band_hods(user)
    hod_pick = next((h for h in hods if h['id'] == hod_id), None)
    if not hod_pick:
        hod_id = None
    elif branch_id is None:
        scope_ids = [b for b in scope_ids if b in hod_pick['branch_ids']]
    rows = []
    if branch_ids:
        rows = scoring.faculty_scores_for_branches(
            scope_ids,
            academic_year_id=academic_year_id,
            semester_id=semester_id)
        if hod_pick:
            rows = [r for r in rows
                    if (r.get('academic_year_id'), r.get('branch_id'), r.get('semester_id'))
                    in hod_pick['triples']]
        # Eligible counts and marks for ALL forms in a handful of queries
        # (was ~30 queries per row, one row after another).
        fids = [r['form_id'] for r in rows]
        elig_map = services.eligible_counts_for_forms(fids)
        fm_map = scoring.form_marks_bulk(fids, elig_map)
        enriched = []
        for r in rows:
            r = dict(r)
            eligible = elig_map.get(r['form_id'], 0)
            fm = fm_map[r['form_id']]
            r['eligible'] = eligible
            r['students'] = fm['students']
            r['obtained_marks'] = fm['obtained']
            r['max_marks'] = fm['maximum']
            enriched.append(r)
        rows = enriched
        _ri = services.round_info([r['form_id'] for r in rows])
        for r in rows:
            r['round_label'] = (_ri.get(r['form_id']) or {}).get('label')
    years = db.query('SELECT id, name FROM academic_years ORDER BY name DESC')
    semesters = db.query('SELECT id, number FROM semesters ORDER BY number')
    scored = [r['overall_score'] for r in rows
              if not r.get('hidden') and r.get('overall_score') is not None]
    summary = {
        'forms': len(rows),
        'faculty': len({r.get('teacher_user_id') for r in rows if r.get('teacher_user_id')}),
        'avg': round(sum(scored) / len(scored), 1) if scored else None,
        'responded': sum((r.get('students') or 0) for r in rows),
    }
    dept = next((b for b in branches if b['id'] == branch_id), None)
    # Score bands live on this same page (click a band to list its teachers).
    from . import mailer
    from .hod import DEFAULT_BAND_SUBJECT
    bands, _b, _h = _dean_band_data(user, branch_id, hod_id, academic_year_id, semester_id)
    band_key = (request.args.get('band') or '').strip()
    active = next((b for b in bands if b['key'] == band_key), None)
    if not active:
        band_key = ''
    return render_template(
        'dean_performance.html', user=user, branches=branches, rows=rows,
        years=years, semesters=semesters, summary=summary, dept=dept,
        hods=hods, bands=bands, active=active, band_key=band_key,
        default_subject=DEFAULT_BAND_SUBJECT,
        default_message=_dean_default_message(band_key) if active else '',
        mail_ready=mailer.is_configured(),
        filters={'academic_year_id': academic_year_id, 'semester_id': semester_id,
                 'branch_id': branch_id, 'hod_id': hod_id})


# --------------------------------------------------------------------------- #
# Teachers by score band (Dean) - same page as the HOD, plus Department and
# HOD-name filters because a Dean oversees several HODs and departments.
# --------------------------------------------------------------------------- #
def _band_hods(user):
    """HODs assigned to this Dean: [{id, name, triples:{(year, branch, semester)}}]."""
    rows = db.query(
        """SELECT u.id, u.name, ha.academic_year_id AS ay, ha.branch_id AS br,
                  ha.semester_id AS sem
           FROM dean_hod_assignments dha
           JOIN users u ON u.id = dha.hod_user_id
           LEFT JOIN hod_allocations ha ON ha.hod_user_id = u.id
           WHERE dha.dean_user_id=? ORDER BY u.name""", (user['id'],))
    hods = {}
    for r in rows:
        h = hods.setdefault(r['id'], {'id': r['id'], 'name': r['name'] or 'HOD',
                                      'triples': set(), 'branch_ids': set()})
        if r['br'] is not None:
            h['triples'].add((r['ay'], r['br'], r['sem']))
            h['branch_ids'].add(r['br'])
    return list(hods.values())


def _dean_band_data(user, branch_id, hod_id, academic_year_id, semester_id):
    """Score bands for the Dean. Each row carries teacher email, department, HOD name(s)."""
    from . import scoring
    from .hod import SCORE_BANDS
    branches = _dean_branches(user)
    allowed = [b['id'] for b in branches]
    hods = _band_hods(user)
    scope_ids = [branch_id] if branch_id in allowed else allowed
    hod_pick = next((h for h in hods if h['id'] == hod_id), None)
    if hod_pick:
        scope_ids = [b for b in scope_ids if b in hod_pick['branch_ids']]
    rows = []
    if scope_ids:
        rows = scoring.faculty_scores_for_branches(
            scope_ids, academic_year_id=academic_year_id,
            semester_id=semester_id, min_responses=1)
    rows = [dict(r) for r in rows if r.get('overall_score') is not None]
    if hod_pick:
        rows = [r for r in rows
                if (r.get('academic_year_id'), r.get('branch_id'), r.get('semester_id'))
                in hod_pick['triples']]
    code_by_id = {b['id']: b['code'] for b in branches}
    for r in rows:
        r['branch_code'] = r.get('branch_code') or code_by_id.get(r.get('branch_id'))
        key = (r.get('academic_year_id'), r.get('branch_id'), r.get('semester_id'))
        r['hod_names'] = ', '.join(h['name'] for h in hods if key in h['triples']) or '—'
    ids = sorted({r['teacher_user_id'] for r in rows if r.get('teacher_user_id')})
    emails = {}
    if ids:
        ph = _in_clause(ids)
        for u in db.query(f'SELECT id, email FROM users WHERE id IN ({ph})', tuple(ids)):
            emails[u['id']] = (u['email'] or '').strip()
    for r in rows:
        r['teacher_email'] = emails.get(r.get('teacher_user_id'), '')
    bands = []
    for key, label, lo, hi in SCORE_BANDS:
        members = [r for r in rows
                   if (lo is None or r['overall_score'] > lo)
                   and (hi is None or r['overall_score'] <= hi)]
        members.sort(key=lambda r: r['overall_score'], reverse=True)
        bands.append({'key': key, 'label': label, 'rows': members, 'count': len(members)})
    return bands, branches, hods


def _dean_default_message(band_key):
    from .hod import _default_message_for
    return (_default_message_for(band_key)
            .replace('{hod}\nHead of Department', '{dean}\nDean'))


def _band_filters_from(src):
    def _i(k):
        try:
            return int(src.get(k)) if src.get(k) else None
        except (TypeError, ValueError):
            return None
    return (_i('academic_year_id'), _i('semester_id'), _i('branch_id'), _i('hod_id'))


@bp.route('/performance/bands')
@role_required('dean')
def performance_bands():
    """Legacy URL: score bands now live on the Performance page."""
    args = {k: v for k, v in request.args.items() if v}
    return redirect(url_for('dean.performance', **args) + '#bands')


@bp.route('/performance/bands/send', methods=['POST'])
@role_required('dean')
def performance_bands_send():
    """E-mail the ticked teachers of one score band (Dean's own text or the default)."""
    from . import mailer
    from html import escape
    from .hod import DEFAULT_BAND_SUBJECT
    user = current_user()
    f = request.form
    academic_year_id, semester_id, branch_id, hod_id = _band_filters_from(f)
    band_key = (f.get('band') or '').strip()
    back = url_for('dean.performance', band=band_key, academic_year_id=academic_year_id,
                   semester_id=semester_id, branch_id=branch_id, hod_id=hod_id) + '#bands'
    subject_tpl = ' '.join((f.get('subject') or '').split())[:150] or DEFAULT_BAND_SUBJECT
    message_tpl = (f.get('message') or '').strip()[:5000] or _dean_default_message(band_key)
    try:
        selected = {int(x) for x in f.getlist('teacher_user_id')}
    except ValueError:
        selected = set()
    if not selected:
        flash('Tick at least one teacher to send the email.', 'error')
        return redirect(back)
    bands, _b, _h = _dean_band_data(user, branch_id, hod_id, academic_year_id, semester_id)
    band = next((b for b in bands if b['key'] == band_key), None)
    if not band:
        flash('Unknown score band.', 'error')
        return redirect(back)
    per_teacher = {}
    for r in band['rows']:          # only teachers inside this band AND the Dean's scope
        if r.get('teacher_user_id') in selected:
            per_teacher.setdefault(r['teacher_user_id'], []).append(r)
    if len(per_teacher) > 100:
        flash('Please send to at most 100 teachers at a time.', 'error')
        return redirect(back)
    dean_name = dict(user).get('name') or 'Dean'
    sent = failed = no_email = 0
    for trows in per_teacher.values():
        to = (trows[0].get('teacher_email') or '').strip()
        if not mailer.is_valid_email(to):
            no_email += 1
            continue
        score = round(sum(x['overall_score'] for x in trows) / len(trows), 2)
        subs = []
        for x in trows:
            label = (x.get('subject_name') or x.get('subject_code') or '').strip()
            if label and label not in subs:
                subs.append(label)
        values = {'{name}': trows[0].get('teacher_name') or 'Faculty',
                  '{score}': f'{score:.2f}',
                  '{subject}': ', '.join(subs) or 'your subject',
                  '{dean}': dean_name, '{hod}': trows[0].get('hod_names') or ''}

        def _fill(t):
            for k, v in values.items():
                t = t.replace(k, str(v))
            return t
        body = _fill(message_tpl)
        html = ('<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto">'
                '<h3 style="color:#0B2F6B">Aditya University - Faculty Feedback</h3>'
                '<div style="white-space:pre-wrap">' + escape(body) + '</div></div>')
        if mailer.send_email(to, _fill(subject_tpl), body, html):
            sent += 1
        else:
            failed += 1
    audit('dean.band.email', 'users', 0,
          f'Band {band_key}: sent {sent}, failed {failed}, no email {no_email}')
    if sent:
        flash(f'Email sent to {sent} teacher(s).', 'success')
    if no_email:
        flash(f'{no_email} teacher(s) have no email address on file and were skipped.', 'warning')
    if failed:
        flash(f'{failed} email(s) could not be sent. Check the mail settings.', 'error')
    return redirect(back)


@bp.route('/performance/faculty/<int:teacher_id>')
@role_required('dean')
def performance_detail(teacher_id):
    from . import scoring
    user = current_user()
    branches = _dean_branches(user)
    branch_ids = [b['id'] for b in branches]
    if not branch_ids:
        abort(403)
    rows = scoring.faculty_scores_for_branches(branch_ids, teacher_user_id=teacher_id)
    teacher = db.query(
        "SELECT id, name, emp_code FROM users WHERE id=? AND role IN ('teacher','hod')",
        (teacher_id,), one=True)
    if not teacher:
        abort(404)
    cards = _detail_cards(rows, teacher_id)
    profile = db.query(
        'SELECT id, name, emp_code, designation, email FROM users WHERE id=?',
        (teacher_id,), one=True)
    back_branch = next((r.get('branch_id') for r in rows if r.get('branch_id')), None)
    return render_template('dean_performance_detail.html', user=user, teacher=teacher,
                           profile=profile, rows=rows, cards=cards,
                           back_branch=back_branch, branches=branches)


def _all_comments(form_id, session_ids):
    """Every anonymous written comment of a form (this teacher's sessions only)."""
    from . import services
    out, page = [], 1
    while True:
        pg = services.suggestions_paged(form_id, page=page, per_page=100,
                                        session_ids=session_ids)
        out.extend(pg['comments'])
        if page >= pg['pages'] or page >= 50:
            return out
        page += 1


def _detail_cards(rows, teacher_id=None):
    """Per-form view model for the Dean's faculty page (same facts as the teacher's
    results page): eligible / responded / not submitted, marks, rating counts and
    per-question distributions.  Reads aggregate counts only - never individual
    answers or comments - so anonymity is untouched."""
    from . import scoring, services
    fids = [r['form_id'] for r in rows]
    if not fids:
        return []
    elig_map = services.eligible_counts_for_forms(fids)
    fm_map = scoring.form_marks_bulk(fids, elig_map)
    ph = _in_clause(fids)
    meta = {m['id']: m for m in db.query(
        f"""SELECT ff.id, b.name AS branch_name, b.code AS branch_code,
                   t.code AS timetable, s.number AS semester, ay.name AS academic_year
            FROM feedback_forms ff
            LEFT JOIN branches b ON b.id=ff.branch_id
            LEFT JOIN timetables t ON t.id=ff.timetable_id
            LEFT JOIN semesters s ON s.id=ff.semester_id
            LEFT JOIN academic_years ay ON ay.id=ff.academic_year_id
            WHERE ff.id IN ({ph})""", tuple(fids))}
    cards = []
    for r in rows:
        fid = r['form_id']
        eligible = elig_map.get(fid, 0)
        fm = fm_map.get(fid) or {}
        responded = fm.get('students') or r.get('response_count') or 0
        # Rating-wise answer counts across all rating questions.
        totals, order = {}, []
        for c in r.get('criteria') or []:
            for lbl, d in (c.get('distribution') or {}).items():
                if lbl not in totals:
                    totals[lbl] = 0
                    order.append(lbl)
                totals[lbl] += d.get('count') or 0
        grand = sum(totals.values())
        n = len(order)
        distribution = [{
            'label': lbl, 'count': totals[lbl],
            'pct': round(100.0 * totals[lbl] / grand, 1) if grand else 0.0,
            'rank': max(1, min(5, n - i)),
        } for i, lbl in enumerate(order)]
        score = None if (r.get('hidden') or r.get('overall_score') is None) else r['overall_score']
        tid = teacher_id or r.get('teacher_user_id')
        sids = services.teacher_form_session_ids(tid, fid) if tid else None
        comments = _all_comments(fid, sids) if sids else []
        cards.append({
            'comments': comments,
            'row': r, 'meta': meta.get(fid) or {}, 'eligible': eligible,
            'responded': responded, 'not_submitted': max(eligible - responded, 0),
            'score': score, 'distribution': distribution, 'answer_total': grand,
            'max_count': max([d['count'] for d in distribution] + [1]),
            'obtained': fm.get('obtained'), 'maximum': fm.get('maximum'),
            'marks_per_question': fm.get('marks_per_question'),
            'question_count': fm.get('question_count') or len(r.get('criteria') or []),
        })
    return cards


# --------------------------------------------------------------------------- #
# Subject-wise multi-round teacher feedback comparison
# --------------------------------------------------------------------------- #
def _comparison_args():
    return (request.args.get('academic_year_id', type=int),
            request.args.get('semester_id', type=int),
            (request.args.get('q') or '').strip())


@bp.route('/comparison')
@role_required('dean')
def comparison():
    """Every teacher with closed rounds, with subject-wise round-to-round change."""
    from . import analytics
    user = current_user()
    branches = _dean_branches(user)
    branch_ids = [b['id'] for b in branches]
    ay, sem, q = _comparison_args()
    profiles = analytics.build_teacher_profiles(
        branch_ids, academic_year_id=ay, semester_id=sem, q=q)
    years = db.query('SELECT id, name FROM academic_years ORDER BY name DESC')
    semesters = db.query('SELECT id, number FROM semesters ORDER BY number')
    return render_template(
        'dean_comparison.html', user=user, branches=branches, profiles=profiles,
        years=years, semesters=semesters,
        filters={'academic_year_id': ay, 'semester_id': sem, 'q': q})


@bp.route('/comparison/teacher/<int:teacher_id>')
@role_required('dean')
def comparison_teacher(teacher_id):
    from . import analytics
    user = current_user()
    branch_ids = [b['id'] for b in _dean_branches(user)]
    if not branch_ids:
        abort(403)
    ay, sem, _q = _comparison_args()
    profile = analytics.profile_for_teacher(
        branch_ids, teacher_id, academic_year_id=ay, semester_id=sem)
    if not profile:
        abort(404)
    return render_template('dean_comparison_teacher.html', user=user, profile=profile)


@bp.route('/comparison/pdf/<kind>')
@role_required('dean')
def comparison_pdf(kind):
    """PDF of one teacher (?teacher_id=) or of everyone in scope (kind='all')."""
    from datetime import datetime
    from flask import Response
    from . import analytics, reports
    user = current_user()
    branch_ids = [b['id'] for b in _dean_branches(user)]
    if not branch_ids:
        abort(403)
    ay, sem, q = _comparison_args()
    if kind == 'teacher':
        teacher_id = request.args.get('teacher_id', type=int)
        if not teacher_id:
            abort(400)
        profile = analytics.profile_for_teacher(
            branch_ids, teacher_id, academic_year_id=ay, semester_id=sem)
        if not profile:
            abort(404)
        profiles = [profile]
        title = 'Subject-wise feedback comparison - ' + (profile['teacher_name'] or 'Teacher')
        fname = f'comparison_teacher_{teacher_id}.pdf'
    elif kind == 'all':
        profiles = analytics.build_teacher_profiles(
            branch_ids, academic_year_id=ay, semester_id=sem, q=q)
        title = 'Subject-wise feedback comparison - all teachers'
        fname = 'comparison_all_teachers.pdf'
    else:
        abort(404)
    pdf = reports.build_comparison_pdf(
        profiles, title,
        subtitle='Closed feedback rounds only. Each subject is shown separately.',
        generated_at=datetime.now().strftime('%d %b %Y %H:%M'))
    return Response(pdf, mimetype='application/pdf', headers={
        'Content-Disposition': f'inline; filename={fname}'})
