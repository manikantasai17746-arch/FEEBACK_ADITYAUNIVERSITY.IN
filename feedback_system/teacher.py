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

    # Forms whose results are not published yet: show the form name plus the
    # eligible / responded counts only. No score or marks are computed or shown.
    for f in services.teacher_unpublished_forms(user['id'], subject_code=subject_code):
        f = dict(f)
        if academic_year_id and f.get('academic_year_id') and int(f['academic_year_id']) != academic_year_id:
            continue
        if semester_id and f.get('semester_id') and int(f['semester_id']) != semester_id:
            continue
        session_ids = services.teacher_form_session_ids(user['id'], f['id'])
        row = {
            'form': f,
            'responses': services.response_count(f['id'], session_ids=session_ids),
            'eligible': services.eligible_count_for_sessions(session_ids) or 0,
            'not_submitted': 0,
            'score': None,
            'obtained_marks': None,
            'max_marks': None,
            'marks_per_question': None,
            'question_count': 0,
            'session_ids': session_ids,
            'scores': None,
            'unpublished': True,
        }
        row['not_submitted'] = max(0, row['eligible'] - row['responses'])
        code = f.get('subject_code') or f.get('snapshot_subject_code') or ''
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
                'marks_per_question': None,
                'question_count': 0,
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
                             r.get('round_no') or 0))

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
            'rounds': sum(1 for r in rows if r['form'].get('id')),
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
    stats = services.question_stats(form_id, session_ids=session_ids)
    page = request.args.get('page', 1, type=int)
    suggestions = services.suggestions_paged(form_id, page=page, per_page=20,
                                             session_ids=session_ids)
    responses = services.response_count(form_id, session_ids=session_ids)
    row = _subject_row(user['id'], form, session_ids)
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
