"""Teacher dashboard: subject-scoped, anonymous, published-only results."""
from flask import Blueprint, abort, current_app, jsonify, render_template, request

from . import analytics, assignment_setup as setup, db, services
from . import scoring
from .security import audit, current_user, role_required

bp = Blueprint('teacher', __name__, url_prefix='/teacher')

TREND_EPSILON = 0.05
EXPECTED_LEVEL = 75.0


def _marks_for_option(opt):
    """Marks from label first (Excellent=5 … Poor=1); value only as fallback."""
    lab = (opt.get('label') or '').strip().lower()
    scale = {
        'excellent': 5,
        'very good': 4,
        'verygood': 4,
        'good': 3,
        'average': 2,
        'fair': 2,
        'poor': 1,
        'very poor': 1,
        'verypoor': 1,
        'bad': 1,
        'satisfactory': 3,
        'unsatisfactory': 1,
    }
    if lab in scale:
        return scale[lab]
    for key, pts in scale.items():
        if key in lab:
            return pts
    if opt.get('value') is not None:
        try:
            return int(opt['value'])
        except (TypeError, ValueError):
            pass
    return None


def _aggregate_rating_distribution(stats):
    """Merge rating option counts across scoreable questions into one summary."""
    buckets = {}  # label -> {count, marks}
    for q in stats:
        if q.get('qtype') == 'text':
            continue
        for o in q.get('options') or []:
            marks = _marks_for_option(o)
            key = (o.get('label') or 'Option').strip()
            if key not in buckets:
                buckets[key] = {'label': key, 'count': 0, 'marks': marks}
            buckets[key]['count'] += o.get('count') or 0
            if buckets[key]['marks'] is None and marks is not None:
                buckets[key]['marks'] = marks
    order = ['excellent', 'very good', 'good', 'average', 'poor', 'bad', 'very poor']
    items = list(buckets.values())

    def sort_key(it):
        lab = (it['label'] or '').lower()
        for i, o in enumerate(order):
            if o in lab:
                return i
        m = it.get('marks')
        return 10 - (m or 0)

    items.sort(key=sort_key)
    total = sum(i['count'] for i in items) or 0
    for it in items:
        it['pct'] = round(it['count'] * 100.0 / total, 1) if total else 0.0
        it['total_marks'] = (it['marks'] or 0) * it['count'] if it.get('marks') is not None else None
    return items, total


def _subject_row(user_id, form, session_ids, section=None):
    responses = services.response_count(form['id'], session_ids=session_ids)
    # Everything here is scoped to THIS teacher's sessions of the form: the
    # eligible population (distinct active students), the responses, the score
    # and the marks. Using the form-wide audience would inflate "eligible" and
    # "max marks" with other teachers' sections and mix their ratings into this
    # teacher's score.
    eligible = services.eligible_count_for_sessions(session_ids)
    scores = scoring.compute_form_scores(form['id'], min_responses=1,
                                         session_ids=session_ids)
    overall = scores.get('overall_score')
    fm = scoring.form_marks(form['id'], eligible, session_ids=session_ids)
    return {
        'form': form,
        'section': (section or {}).get('timetable'),
        'section_id': (section or {}).get('timetable_id'),
        'responses': responses,
        'eligible': eligible or 0,
        'not_submitted': max(0, (eligible or 0) - responses),
        'score': overall,
        'obtained_marks': fm['obtained'],
        'max_marks': fm['maximum'],
        'marks_per_question': fm['marks_per_question'],
        'question_count': fm['question_count'],
        'answer_count': fm['answer_count'],
        'session_ids': session_ids,
        'scores': scores,
        'form_marks': fm,
    }


# --------------------------------------------------------------------------- #
# Phase 3: Teacher Assignment Setup (subjects -> rooms -> labs -> review/save)
# The teacher is always taken from the session; ids in a request body are only
# accepted if they belong to that teacher (see assignment_setup._validate_items).
# --------------------------------------------------------------------------- #
@bp.route('/assignment-setup')
@role_required('teacher')
def assignment_setup():
    user = current_user()
    rows = setup.teacher_assignments(user['id'])
    return render_template(
        'teacher_assignment_setup.html',
        subjects=setup.group_by_subject(rows),
        summary=setup.summarize(rows),
        room_max=setup.ROOM_MAX_LEN)


def _json_error(exc):
    body = {'ok': False, 'error': exc.message}
    if exc.errors:
        body['errors'] = exc.errors
    return jsonify(body), exc.status


def _saved_response(user, items, saved_fields):
    try:
        summary = setup.save_configuration(user['id'], items)
    except setup.SetupError as exc:
        return _json_error(exc)
    except Exception:
        current_app.logger.exception('Teacher setup save failed for user %s', user['id'])
        return jsonify(ok=False, error='Could not save right now. Nothing was changed; '
                                       'please try again.'), 500
    audit('teacher_setup_save', 'teacher_subject_settings', user['id'],
          f"{saved_fields}: {len(items)} assignment(s)", actor=user)
    return jsonify(ok=True, summary=summary,
                   assignments=setup.teacher_assignments(user['id']))


@bp.route('/assignment-setup/api/assignments')
@role_required('teacher')
def assignment_setup_assignments():
    rows = setup.teacher_assignments(current_user()['id'])
    return jsonify(ok=True, subjects=setup.group_by_subject(rows),
                   summary=setup.summarize(rows))


@bp.route('/assignment-setup/api/summary')
@role_required('teacher')
def assignment_setup_summary():
    return jsonify(ok=True, summary=setup.teacher_summary(current_user()['id']))


@bp.route('/assignment-setup/api/students')
@role_required('teacher')
def assignment_setup_students():
    session_id = request.args.get('session_id', type=int)
    if not session_id:
        return jsonify(ok=False, error='A session is required.'), 400
    data = setup.eligible_students_page(
        current_user()['id'], session_id,
        page=request.args.get('page', 1, type=int),
        search=request.args.get('search', ''),
        page_size=request.args.get('page_size', setup.STUDENT_PAGE_SIZE, type=int))
    if not data:
        return jsonify(ok=False, error='Assignment not found.'), 404
    assignment = data.pop('assignment')
    return jsonify(ok=True, assignment={
        'subject_code': assignment['subject_code'],
        'subject_name': assignment['subject_name'],
        'section': assignment['section'],
        'section_name': assignment['section_name'],
        'branch': assignment['branch'],
        'branch_name': assignment['branch_name'],
        'semester': assignment['semester'],
        'academic_year': assignment['academic_year'],
    }, **data)


@bp.route('/assignment-setup/api/save', methods=['POST'])
@role_required('teacher')
def assignment_setup_save():
    """Save room + lab for many assignments in one transaction (Step 4)."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(ok=False, error='Invalid request.'), 400
    return _saved_response(current_user(), payload.get('items'), 'room+lab')


@bp.route('/assignment-setup/api/room', methods=['POST'])
@role_required('teacher')
def assignment_setup_room():
    """Save or clear the room of ONE assignment."""
    p = request.get_json(silent=True)
    if not isinstance(p, dict) or 'room' not in p:
        return jsonify(ok=False, error='Invalid request.'), 400
    return _saved_response(current_user(),
                           [{'session_id': p.get('session_id'), 'room': p['room']}], 'room')


@bp.route('/assignment-setup/api/lab', methods=['POST'])
@role_required('teacher')
def assignment_setup_lab():
    """Save the lab availability of ONE assignment.  Never creates a lab assignment."""
    p = request.get_json(silent=True)
    if not isinstance(p, dict) or 'lab_status' not in p:
        return jsonify(ok=False, error='Invalid request.'), 400
    return _saved_response(current_user(),
                           [{'session_id': p.get('session_id'), 'lab_status': p['lab_status']}],
                           'lab')


@bp.route('/')
@role_required('teacher')
def dashboard():
    user = current_user()
    subjects = services.teacher_subjects(user['id'])
    subject_code = request.args.get('subject_code') or None
    academic_year_id = request.args.get('academic_year_id', type=int)
    semester_id = request.args.get('semester_id', type=int)

    # Do NOT auto-select a single subject when the teacher teaches several —
    # always show the full list unless the user explicitly filters.
    subject_codes = {s['subject_code'] for s in subjects}
    if subject_code and subject_code not in subject_codes:
        abort(403)

    # All published forms for this teacher (optionally filtered by subject)
    forms = services.teacher_published_forms(user['id'], subject_code=subject_code)
    form_rows_by_subject = {}
    for f in forms:
        f = dict(f) if not isinstance(f, dict) else f
        if academic_year_id and f.get('academic_year_id') and int(f['academic_year_id']) != academic_year_id:
            continue
        if semester_id and f.get('semester_id') and int(f['semester_id']) != semester_id:
            continue
        session_ids = services.teacher_form_session_ids(user['id'], f['id'])
        code = f.get('subject_code') or f.get('snapshot_subject_code') or ''
        # One row per section: sections are never combined into one row.
        for sec in services.sessions_by_section(session_ids) or [None]:
            row = _subject_row(user['id'], f,
                               sec['session_ids'] if sec else session_ids, sec)
            form_rows_by_subject.setdefault(code, []).append(row)

    # Forms whose results are not published yet: show the form name plus the
    # eligible / responded counts only. No score or marks are computed or shown.
    for f in services.teacher_unpublished_forms(user['id'], subject_code=subject_code):
        f = dict(f)
        if academic_year_id and f.get('academic_year_id') and int(f['academic_year_id']) != academic_year_id:
            continue
        if semester_id and f.get('semester_id') and int(f['semester_id']) != semester_id:
            continue
        session_ids = services.teacher_form_session_ids(user['id'], f['id'])
        code = f.get('subject_code') or f.get('snapshot_subject_code') or ''
        for sec in services.sessions_by_section(session_ids) or [None]:
            sids = sec['session_ids'] if sec else session_ids
            row = {
                'form': f,
                'section': (sec or {}).get('timetable'),
                'section_id': (sec or {}).get('timetable_id'),
                'responses': services.response_count(f['id'], session_ids=sids),
                'eligible': services.eligible_count_for_sessions(sids) or 0,
                'not_submitted': 0,
                'score': None,
                'obtained_marks': None,
                'max_marks': None,
                'marks_per_question': None,
                'question_count': 0,
                'session_ids': sids,
                'scores': None,
                'unpublished': True,
            }
            row['not_submitted'] = max(0, row['eligible'] - row['responses'])
            form_rows_by_subject.setdefault(code, []).append(row)

    # Build one display row per subject the teacher handles.
    # Prefer published-form stats; if none published yet, still list the subject.
    rows = []
    total_eligible = 0
    total_responses = 0
    total_obtained = 0.0
    total_max = 0.0
    score_sum = 0.0
    score_n = 0
    listed_codes = set()

    # Subjects from timetable (always show all the teacher teaches)
    subject_list = subjects
    if subject_code:
        subject_list = [s for s in subjects if s['subject_code'] == subject_code]

    for s in subject_list:
        s = dict(s)
        code = s['subject_code']
        listed_codes.add(code)
        pub = form_rows_by_subject.get(code) or []
        if pub:
            # One row per published form under this subject
            for row in pub:
                rows.append(row)
                total_eligible += row['eligible']
                total_responses += row['responses']
                total_obtained += row.get('obtained_marks') or 0
                total_max += row.get('max_marks') or 0
                if row['score'] is not None:
                    score_sum += row['score']
                    score_n += 1
        else:
            # No feedback form yet — still show the subject as a placeholder,
            # with the real eligible population from this teacher's sessions
            # of the subject (responses are necessarily 0 without a form).
            ph_session_ids = [r['id'] for r in services.teacher_sessions(
                user['id'], subject_code=code)]
            total_eligible += services.eligible_count_for_sessions(ph_session_ids) or 0
            for sec in services.sessions_by_section(ph_session_ids) or [None]:
                sids = sec['session_ids'] if sec else ph_session_ids
                ph_eligible = services.eligible_count_for_sessions(sids) or 0
                rows.append({
                    'form': {
                        'id': None,
                        'subject_code': code,
                        'subject_name': s.get('subject_name') or code,
                        'title': s.get('subject_name') or code,
                        'branch': None,
                        'timetable': None,
                        'semester': None,
                        'academic_year': None,
                    },
                    'section': (sec or {}).get('timetable'),
                    'section_id': (sec or {}).get('timetable_id'),
                    'responses': 0,
                    'eligible': ph_eligible,
                    'not_submitted': ph_eligible,
                    'score': None,
                    'obtained_marks': None,
                    'max_marks': None,
                    'marks_per_question': None,
                    'question_count': 0,
                    'session_ids': sids,
                    'scores': None,
                    'unpublished': True,
                })

    # Published forms whose subject_code is missing from teacher_subjects (edge case)
    for code, pub_list in form_rows_by_subject.items():
        if code in listed_codes:
            continue
        for row in pub_list:
            rows.append(row)
            total_eligible += row['eligible']
            total_responses += row['responses']
            total_obtained += row.get('obtained_marks') or 0
            total_max += row.get('max_marks') or 0
            if row['score'] is not None:
                score_sum += row['score']
                score_n += 1

    # Attach round identity (Round N, title, created date, response %) to each
    # row so several feedback rounds of the same class stay distinguishable.
    # Rounds are never merged: every row is one form with its own numbers.
    info = services.round_info([r['form']['id'] for r in rows if r['form'].get('id')])
    for r in rows:
        ri = info.get(r['form'].get('id')) or {}
        r['round_no'] = ri.get('round_no')
        r['round_label'] = ri.get('label')
        r['rounds_total'] = ri.get('rounds_total')
        r['created_at'] = ri.get('created_at')
        r['response_pct'] = (round(r['responses'] * 100.0 / r['eligible'], 1)
                             if r.get('eligible') else None)
    rows.sort(key=lambda r: ((r['form'].get('subject_name') or r['form'].get('title') or ''),
                             r['form'].get('subject_code') or '',
                             r.get('round_no') or 0, r.get('section') or ''))

    # Header tiles: students are counted once (distinct) however many rounds
    # exist, otherwise four rounds would show 4x the students.
    all_sids = []
    for r in rows:
        all_sids.extend(r.get('session_ids') or [])
    distinct_eligible = services.eligible_count_for_sessions(all_sids)
    avg_score = round(score_sum / score_n, 2) if score_n else None
    years = db.query('SELECT id, name FROM academic_years ORDER BY name DESC')
    semesters = db.query('SELECT id, number, label FROM semesters ORDER BY number')
    profile = db.query(
        'SELECT id, name, emp_code, designation, email FROM users WHERE id=?',
        (user['id'],), one=True)
    dept = None
    if rows and rows[0].get('form'):
        dept = rows[0]['form'].get('branch') or rows[0]['form'].get('branch_name')
    if not dept:
        br = db.query(
            """SELECT b.name FROM branches b
               JOIN timetables t ON t.branch_id=b.id
               JOIN class_sessions cs ON cs.timetable_id=t.id
               WHERE cs.teacher_user_id=? AND cs.is_deleted=0 LIMIT 1""",
            (user['id'],), one=True)
        dept = br['name'] if br else None

    return render_template(
        'teacher_dashboard.html',
        user=user, profile=profile, department=dept,
        subjects=subjects, subject_code=subject_code,
        rows=rows, years=years, semesters=semesters,
        academic_year_id=academic_year_id, semester_id=semester_id,
        stats={
            'subjects': len(subjects),
            'eligible': distinct_eligible,
            'rounds': len({r['form']['id'] for r in rows if r['form'].get('id')}),
            'responses': total_responses,
            'avg_score': avg_score,
            'obtained': round(total_obtained, 2),
            'maximum': round(total_max, 2),
        },
    )


@bp.route('/results/<int:form_id>')
@role_required('teacher')
def results(form_id):
    user = current_user()
    allowed = {f['id']: f for f in services.teacher_published_forms(user['id'])}
    form = allowed.get(form_id)
    if not form:
        abort(403)

    session_ids = services.teacher_form_session_ids(user['id'], form_id)
    section = None
    section_id = request.args.get('section', type=int)
    if section_id:
        section = next((g for g in services.sessions_by_section(session_ids)
                        if g['timetable_id'] == section_id), None)
        if not section:
            abort(404)
        session_ids = section['session_ids']
    stats = services.question_stats(form_id, session_ids=session_ids)
    page = request.args.get('page', 1, type=int)
    suggestions = services.suggestions_paged(form_id, page=page, per_page=20,
                                             session_ids=session_ids)
    responses = services.response_count(form_id, session_ids=session_ids)
    row = _subject_row(user['id'], form, session_ids, section)
    distribution, dist_total = _aggregate_rating_distribution(stats)
    scores = row.get('scores') or scoring.compute_form_scores(
        form_id, min_responses=1, session_ids=session_ids)

    fm = row.get('form_marks') or scoring.form_marks(
        form_id, row.get('eligible') or 0, session_ids=session_ids)
    marks_summary = []
    for b in fm.get('rating_breakdown') or []:
        marks_summary.append({
            'label': b['label'],
            'marks': b['marks_each'],
            'count': b['count'],
            'total_marks': b['total_marks'],
            'pct': 0,
        })
    answer_count = fm.get('answer_count') or 0
    if answer_count and marks_summary:
        for m in marks_summary:
            m['pct'] = round(m['count'] * 100.0 / answer_count, 1)

    # Keep distribution chart counts (answer counts) but align marks with form_marks
    obtained_total = fm['obtained']
    max_marks_total = fm['maximum']

    profile = db.query(
        'SELECT id, name, emp_code, designation FROM users WHERE id=?',
        (user['id'],), one=True)

    return render_template(
        'teacher_results.html',
        user=user, profile=profile, form=form, stats=stats,
        round_label=(services.round_info([form_id]).get(form_id) or {}).get('label'),
        suggestions=suggestions, responses=responses,
        row=row, distribution=distribution, dist_total=dist_total,
        marks_summary=marks_summary, scores=scores,
        obtained_total=obtained_total, max_marks_total=max_marks_total or 0,
        marks_per_question=fm['marks_per_question'],
        question_count=fm['question_count'],
        answer_count=answer_count,
        students_count=fm['students'],
    )


# ---------------------------------------------------------------------------
# My Improvement — round-by-round comparison (teacher's own published results)
# ---------------------------------------------------------------------------

def _trend_label(change):
    if change is None:
        return '—'
    if change > TREND_EPSILON:
        return 'Improved'
    if change < -TREND_EPSILON:
        return 'Declined'
    return 'No change'


def _band_label(score):
    b = analytics.band_for(score)
    return b['label'] if b else '—'


def _date_short(value):
    s = str(value or '')[:10]
    if len(s) >= 10 and s[4] == '-' and s[7] == '-':
        # YYYY-MM-DD → "12 Aug"
        months = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()
        try:
            y, m, d = int(s[:4]), int(s[5:7]), int(s[8:10])
            return f'{d} {months[m - 1]}'
        except (ValueError, IndexError):
            pass
    return s or '—'


def _chart_coords(rounds, ymin=60.0, ymax=90.0, w=420, h=180,
                  pad_l=36, pad_r=16, pad_t=16, pad_b=36):
    """Pre-compute SVG point coordinates for the trend line."""
    n = len(rounds)
    if n == 0:
        return []
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b
    span = max(ymax - ymin, 1.0)
    points = []
    for i, r in enumerate(rounds):
        y_val = max(ymin, min(ymax, float(r['score'])))
        px = pad_l + (plot_w * i / (n - 1) if n > 1 else plot_w / 2)
        py = pad_t + plot_h * (1 - (y_val - ymin) / span)
        points.append({
            'x': i,
            'y': r['score'],
            'label': r.get('date_label') or '',
            'form_id': r['form_id'],
            'px': round(px, 1),
            'py': round(py, 1),
        })
    return points


def _build_teacher_comparison(user_id, academic_year_id=None, semester_id=None,
                              subject_code=None):
    """Group this teacher's published forms by subject+section and compute trends.

    Only the teacher's own session responses are scored (never other sections).
    """
    forms = services.teacher_published_forms(user_id, subject_code=subject_code or None)
    year_name = None
    if academic_year_id:
        yr = db.query('SELECT name FROM academic_years WHERE id=?',
                      (academic_year_id,), one=True)
        year_name = yr['name'] if yr else None
        forms = [f for f in forms
                 if f.get('academic_year_id') == academic_year_id
                 or (year_name and f.get('academic_year') == year_name)]
    if semester_id:
        sem = db.query('SELECT number FROM semesters WHERE id=?',
                       (semester_id,), one=True)
        sem_num = sem['number'] if sem else None
        forms = [f for f in forms
                 if f.get('semester_id') == semester_id
                 or (sem_num is not None and f.get('semester') == sem_num)]

    # Enrich each form with teacher-scoped score + section info
    enriched = []
    for f in forms:
        sid_list = services.teacher_form_session_ids(user_id, f['id'])
        if not sid_list:
            continue
        sections = services.sessions_by_section(sid_list)
        scores = scoring.compute_form_scores(f['id'], min_responses=1,
                                             session_ids=sid_list)
        ri = (services.round_info([f['id']]).get(f['id']) or {})
        published = f.get('results_published_at') or f.get('published_at') or f.get('created_at')
        for sec in (sections or [{'timetable_id': None, 'timetable': None,
                                  'session_ids': sid_list}]):
            sec_ids = sec.get('session_ids') or sid_list
            if sec.get('timetable_id') and len(sections) > 1:
                # Re-score when multiple sections share a form
                sec_scores = scoring.compute_form_scores(
                    f['id'], min_responses=1, session_ids=sec_ids)
            else:
                sec_scores = scores
            if sec_scores.get('overall_score') is None:
                continue
            enriched.append({
                'form': f,
                'form_id': f['id'],
                'title': f.get('title') or f'Form #{f["id"]}',
                'subject_code': (f.get('subject_code') or f.get('snapshot_subject_code')
                                 or '—').strip() or '—',
                'subject_name': f.get('subject_name') or f.get('snapshot_subject_name') or '',
                'section': sec.get('timetable') or f.get('timetable') or '',
                'timetable_id': sec.get('timetable_id'),
                'session_ids': sec_ids,
                'score': round(float(sec_scores['overall_score']), 2),
                'responses': sec_scores.get('valid_responses') or 0,
                'criteria': sec_scores.get('criteria') or [],
                'published_at': published,
                'round_label': ri.get('label') or 'Round',
                'round_no': ri.get('round_no') or 1,
                'created_at': f.get('created_at'),
            })

    # Group by subject_code + section
    groups = {}
    for row in enriched:
        key = (row['subject_code'], row['section'] or '', row.get('timetable_id'))
        g = groups.setdefault(key, {
            'subject_code': row['subject_code'],
            'subject_name': row['subject_name'],
            'section': row['section'],
            'timetable_id': row.get('timetable_id'),
            'rounds': [],
        })
        if not g['subject_name'] and row['subject_name']:
            g['subject_name'] = row['subject_name']
        g['rounds'].append(row)

    subjects = []
    for g in groups.values():
        rounds = sorted(g['rounds'], key=lambda r: (
            str(r.get('published_at') or r.get('created_at') or ''),
            r['form_id']))
        # Deduplicate by form_id (keep first chronological)
        seen = set()
        unique = []
        for r in rounds:
            if r['form_id'] in seen:
                continue
            seen.add(r['form_id'])
            unique.append(r)
        rounds = unique

        prev = None
        for i, r in enumerate(rounds):
            r['index'] = i + 1
            r['date_label'] = _date_short(r.get('published_at') or r.get('created_at'))
            step = None if prev is None else round(r['score'] - prev, 2)
            r['step_change'] = step
            r['step_trend'] = _trend_label(step)
            prev = r['score']

        avg = round(sum(r['score'] for r in rounds) / len(rounds), 2) if rounds else None
        net = None
        if len(rounds) >= 2:
            net = round(rounds[-1]['score'] - rounds[0]['score'], 2)
        best = max(rounds, key=lambda r: r['score']) if rounds else None
        latest = rounds[-1] if rounds else None

        subjects.append({
            'subject_code': g['subject_code'],
            'subject_name': g['subject_name'],
            'section': g['section'],
            'timetable_id': g.get('timetable_id'),
            'round_count': len(rounds),
            'rounds': rounds,
            'average_score': avg,
            'net_change': net,
            'net_trend': _trend_label(net),
            'latest_score': latest['score'] if latest else None,
            'latest_band': _band_label(latest['score']) if latest else None,
            'best_round': best,
            'latest_responses': latest['responses'] if latest else 0,
            'chart_points': _chart_coords(rounds),
        })

    subjects.sort(key=lambda s: (
        0 if s['round_count'] >= 2 else 1,
        -(s['net_change'] or 0) if s['net_change'] is not None else 0,
        s['subject_code']))
    return subjects


def _criteria_compare(form_a, form_b, session_ids_a, session_ids_b):
    """Criteria-by-criteria comparison using teacher-scoped scores."""
    sa = scoring.compute_form_scores(form_a, min_responses=1, session_ids=session_ids_a)
    sb = scoring.compute_form_scores(form_b, min_responses=1, session_ids=session_ids_b)

    def by_text(criteria):
        m = {}
        for c in criteria or []:
            key = (c.get('text') or '').strip().lower()
            if key:
                m[key] = c
        return m

    ma, mb = by_text(sa.get('criteria')), by_text(sb.get('criteria'))
    keys = sorted(set(ma) | set(mb), key=lambda k: (
        (ma.get(k) or mb.get(k) or {}).get('position') or 999, k))

    rows = []
    for key in keys:
        ca, cb = ma.get(key), mb.get(key)
        score_a = ca['score'] if ca else None
        score_b = cb['score'] if cb else None
        change = None
        status = 'ok'
        if score_a is None or score_b is None:
            status = 'n/a'
        elif ca and cb and ca.get('max_points') != cb.get('max_points'):
            status = 'n/a'
        else:
            change = round(score_b - score_a, 2)
        rows.append({
            'text': (ca or cb or {}).get('text') or key,
            'score_a': score_a,
            'score_b': score_b,
            'change': change,
            'status': status,
            'trend': _trend_label(change) if status == 'ok' else '—',
        })
    # Highlight most improved / needs attention
    ok_rows = [r for r in rows if r['status'] == 'ok' and r['change'] is not None]
    if ok_rows:
        best = max(ok_rows, key=lambda r: r['change'])
        worst = min(ok_rows, key=lambda r: r['change'])
        if best['change'] > TREND_EPSILON:
            best['tag'] = 'Most improved'
        if worst['change'] < -TREND_EPSILON:
            worst['tag'] = 'Needs attention'
    return rows


@bp.route('/improvement')
@role_required('teacher')
def improvement():
    """Round-by-round comparison of the teacher's own published feedback."""
    user = current_user()
    academic_year_id = request.args.get('academic_year_id', type=int)
    semester_id = request.args.get('semester_id', type=int)
    subject_code = (request.args.get('subject_code') or '').strip() or None

    subjects = _build_teacher_comparison(
        user['id'], academic_year_id=academic_year_id,
        semester_id=semester_id, subject_code=subject_code)

    # Summary tiles
    multi = [s for s in subjects if s['round_count'] >= 2]
    improved = [s for s in multi if (s['net_change'] or 0) > TREND_EPSILON]
    biggest = max(multi, key=lambda s: s['net_change'] or 0) if multi else None
    total_rounds = sum(s['round_count'] for s in subjects)

    # Optional criteria compare for a specific subject (query params)
    criteria_rows = None
    compare_subject = None
    form_a_id = request.args.get('form_a', type=int)
    form_b_id = request.args.get('form_b', type=int)
    focus_code = (request.args.get('focus') or '').strip()
    if form_a_id and form_b_id and form_a_id != form_b_id:
        for s in subjects:
            if focus_code and s['subject_code'] != focus_code:
                continue
            ids = {r['form_id']: r for r in s['rounds']}
            if form_a_id in ids and form_b_id in ids:
                ra, rb = ids[form_a_id], ids[form_b_id]
                criteria_rows = _criteria_compare(
                    form_a_id, form_b_id, ra['session_ids'], rb['session_ids'])
                compare_subject = s
                break

    # Default criteria view: first subject with ≥2 rounds, first vs last
    if criteria_rows is None:
        for s in subjects:
            if s['round_count'] >= 2:
                ra, rb = s['rounds'][0], s['rounds'][-1]
                criteria_rows = _criteria_compare(
                    ra['form_id'], rb['form_id'],
                    ra['session_ids'], rb['session_ids'])
                compare_subject = s
                form_a_id, form_b_id = ra['form_id'], rb['form_id']
                break

    years = db.query('SELECT id, name FROM academic_years ORDER BY name DESC')
    semesters = db.query('SELECT id, number, label FROM semesters ORDER BY number')
    # Distinct subjects for filter
    all_codes = sorted({s['subject_code'] for s in subjects if s['subject_code'] != '—'})
    subject_options = [
        {'subject_code': s['subject_code'],
         'subject_name': s['subject_name'] or s['subject_code']}
        for s in subjects
    ]
    # de-dupe options
    seen = set()
    uniq_opts = []
    for o in subject_options:
        if o['subject_code'] in seen:
            continue
        seen.add(o['subject_code'])
        uniq_opts.append(o)

    profile = db.query(
        'SELECT id, name, emp_code, designation, email FROM users WHERE id=?',
        (user['id'],), one=True)
    dept = None
    br = db.query(
        """SELECT b.name FROM branches b
           JOIN timetables t ON t.branch_id=b.id
           JOIN class_sessions cs ON cs.timetable_id=t.id
           WHERE cs.teacher_user_id=? AND cs.is_deleted=0 LIMIT 1""",
        (user['id'],), one=True)
    if br:
        dept = br['name']

    return render_template(
        'teacher_comparison.html',
        user=user, profile=profile, department=dept,
        subjects=subjects,
        years=years, semesters=semesters,
        subject_options=uniq_opts,
        academic_year_id=academic_year_id, semester_id=semester_id,
        subject_code=subject_code,
        stats={
            'subjects_compared': len(subjects),
            'rounds_published': total_rounds,
            'subjects_improved': len(improved),
            'biggest_gain': biggest['net_change'] if biggest else None,
            'biggest_subject': (
                f"{biggest['subject_code']}" if biggest else None),
        },
        criteria_rows=criteria_rows,
        compare_subject=compare_subject,
        form_a_id=form_a_id,
        form_b_id=form_b_id,
        expected_level=EXPECTED_LEVEL,
    )
