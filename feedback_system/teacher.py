"""Teacher dashboard: subject-scoped, anonymous, published-only results."""
from flask import Blueprint, abort, render_template, request

from . import db, services
from . import scoring
from .security import current_user, role_required

bp = Blueprint('teacher', __name__, url_prefix='/teacher')


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


def _subject_row(user_id, form, session_ids):
    responses = services.response_count(form['id'], session_ids=session_ids)
    eligible = services.eligible_count_for_form(form['id']) if hasattr(services, 'eligible_count_for_form') else 0
    if session_ids:
        try:
            ph = ','.join('?' * len(session_ids))
            eligible = db.query(
                f'SELECT COUNT(DISTINCT student_user_id) c FROM session_students '
                f'WHERE session_id IN ({ph})', tuple(session_ids), one=True)['c']
        except Exception:
            pass
    scores = scoring.compute_form_scores(form['id'], min_responses=1)
    overall = scores.get('overall_score')
    max_marks = None
    obtained = None
    if responses and overall is not None:
        qs = scoring.form_rating_questions(form['id'])
        if qs:
            max_per = scoring._question_max_points(qs[0]['options']) or 5
            max_marks = max_per * responses
            obtained = round(overall / 100.0 * max_marks, 0)
    return {
        'form': form,
        'responses': responses,
        'eligible': eligible or 0,
        'not_submitted': max(0, (eligible or 0) - responses),
        'score': overall,
        'obtained_marks': int(obtained) if obtained is not None else None,
        'max_marks': int(max_marks) if max_marks is not None else None,
        'session_ids': session_ids,
        'scores': scores,
    }


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
        row = _subject_row(user['id'], f, session_ids)
        code = f.get('subject_code') or f.get('snapshot_subject_code') or ''
        form_rows_by_subject.setdefault(code, []).append(row)

    # Build one display row per subject the teacher handles.
    # Prefer published-form stats; if none published yet, still list the subject.
    rows = []
    total_eligible = 0
    total_responses = 0
    score_sum = 0.0
    score_n = 0
    listed_codes = set()

    # Subjects from timetable (always show all the teacher teaches)
    subject_list = subjects
    if subject_code:
        subject_list = [s for s in subjects if s['subject_code'] == subject_code]

    for s in subject_list:
        code = s['subject_code']
        listed_codes.add(code)
        pub = form_rows_by_subject.get(code) or []
        if pub:
            # One row per published form under this subject
            for row in pub:
                rows.append(row)
                total_eligible += row['eligible']
                total_responses += row['responses']
                if row['score'] is not None:
                    score_sum += row['score']
                    score_n += 1
        else:
            # No published results yet — still show the subject as a placeholder
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
                'responses': 0,
                'eligible': 0,
                'not_submitted': 0,
                'score': None,
                'obtained_marks': None,
                'max_marks': None,
                'session_ids': [],
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
            if row['score'] is not None:
                score_sum += row['score']
                score_n += 1

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
            'eligible': total_eligible,
            'responses': total_responses,
            'avg_score': avg_score,
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
    stats = services.question_stats(form_id, session_ids=session_ids)
    page = request.args.get('page', 1, type=int)
    suggestions = services.suggestions_paged(form_id, page=page, per_page=20,
                                             session_ids=session_ids)
    responses = services.response_count(form_id, session_ids=session_ids)
    row = _subject_row(user['id'], form, session_ids)
    distribution, dist_total = _aggregate_rating_distribution(stats)
    scores = scoring.compute_form_scores(form_id, min_responses=1)

    marks_summary = []
    obtained_total = 0
    max_total = 0
    for d in distribution:
        m = d.get('marks')
        cnt = d.get('count') or 0
        tot = (m * cnt) if m is not None else 0
        marks_summary.append({
            'label': d['label'],
            'marks': m,
            'count': cnt,
            'total_marks': tot,
            'pct': d.get('pct') or 0,
        })
        obtained_total += tot
        if m is not None:
            max_total = max(max_total, m)
    rating_answers = sum(d['count'] for d in distribution) or responses
    max_marks_total = (max_total * rating_answers) if max_total and rating_answers else row.get('max_marks')
    if row.get('obtained_marks') is not None:
        obtained_total = row['obtained_marks']
    if row.get('max_marks') is not None:
        max_marks_total = row['max_marks']

    profile = db.query(
        'SELECT id, name, emp_code, designation FROM users WHERE id=?',
        (user['id'],), one=True)

    return render_template(
        'teacher_results.html',
        user=user, profile=profile, form=form, stats=stats,
        suggestions=suggestions, responses=responses,
        row=row, distribution=distribution, dist_total=dist_total,
        marks_summary=marks_summary, scores=scores,
        obtained_total=obtained_total, max_marks_total=max_marks_total or 0,
    )
