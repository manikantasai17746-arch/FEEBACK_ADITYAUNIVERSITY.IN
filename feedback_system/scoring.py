"""Central faculty performance scoring service.

One source of truth for overall marks (out of 100), criterion-wise scores,
rating distributions, and historical comparison. Used by HOD dashboard,
Dean dashboard, API endpoints and PDF exports so all numbers always agree.

Rules (Change 11 / 12):
- Score = (earned points / maximum possible points) * 100
- Scale prefers option labels (Excellent=5 … Poor=1); falls back to
  question_options.value when the label is not a known rating.
- Unanswered / text-only items are excluded from both numerator and denominator.
- Zero valid responses → overall = None ("N/A"), never 0.
- Minimum response threshold (default 5) hides aggregates below the threshold.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from . import db

# Default rating scale labels → points (used only when option.value is NULL)
DEFAULT_SCALE = {
    'excellent': 5,
    'very good': 4,
    'verygood': 4,
    'good': 3,
    'average': 2,
    'poor': 1,
    'very poor': 1,
    'verypoor': 1,
    'bad': 1,
    'fair': 2,
    'satisfactory': 3,
    'unsatisfactory': 1,
}

MIN_RESPONSE_THRESHOLD = 5


def _option_points(opt: dict) -> Optional[int]:
    """Return numeric points for an option, or None if it is not scoreable.

    Prefer label-based scale (Excellent=5 … Poor=1) so older forms that stored
    sequential values (Excellent=1, Good=2, …) still score correctly.
    """
    label = (opt.get('label') or '').strip().lower()
    if label in DEFAULT_SCALE:
        return DEFAULT_SCALE[label]
    first = label.split()[0] if label else ''
    if first in DEFAULT_SCALE:
        return DEFAULT_SCALE[first]
    for key, pts in DEFAULT_SCALE.items():
        if key in label:
            return pts
    if opt.get('value') is not None:
        try:
            return int(opt['value'])
        except (TypeError, ValueError):
            pass
    return None


def _question_max_points(options: List[dict]) -> Optional[int]:
    pts = [p for p in (_option_points(o) for o in options) if p is not None]
    return max(pts) if pts else None


def form_rating_questions(form_id: int) -> List[dict]:
    """Return scoreable (rating / single_choice with numeric options) questions."""
    questions = db.query(
        'SELECT * FROM form_questions WHERE form_id=? ORDER BY position, id',
        (form_id,))
    out = []
    for q in questions:
        q = dict(q)
        opts = [dict(o) for o in db.query(
            'SELECT * FROM question_options WHERE question_id=? ORDER BY position, id',
            (q['id'],))]
        q['options'] = opts
        if q['qtype'] in ('rating', 'single_choice') and _question_max_points(opts) is not None:
            out.append(q)
    return out


def _session_filter(session_ids, alias='s'):
    """SQL fragment + args limiting submissions to given teaching sessions."""
    ids = [int(i) for i in (session_ids or [])]
    if not ids:
        return '', ()
    return f' AND {alias}.session_id IN ({",".join("?" * len(ids))})', tuple(ids)


def compute_form_scores(form_id: int, min_responses: int = MIN_RESPONSE_THRESHOLD,
                        session_ids=None) -> Dict[str, Any]:
    """Aggregate scores for one feedback form.

    Returns dict with:
      overall_score (float|None), valid_responses (int),
      criteria (list of criterion dicts), distribution (per-question),
      hidden (bool) — True when below min_responses threshold.
    """
    questions = form_rating_questions(form_id)
    if not questions:
        return {
            'form_id': form_id,
            'overall_score': None,
            'valid_responses': 0,
            'criteria': [],
            'distribution': {},
            'hidden': True,
            'reason': 'No scoreable questions',
        }

    # Valid submissions for this form
    sf, sargs = _session_filter(session_ids, 'submissions')
    sub_count = db.query(
        'SELECT COUNT(*) c FROM submissions WHERE form_id=?' + sf,
        (form_id,) + sargs, one=True)['c']

    if sub_count < min_responses:
        return {
            'form_id': form_id,
            'overall_score': None,
            'valid_responses': sub_count,
            'criteria': [],
            'distribution': {},
            'hidden': True,
            'reason': f'Fewer than {min_responses} responses',
        }

    qids = [q['id'] for q in questions]
    ph = ','.join('?' * len(qids))

    # All answers for scoreable questions (anonymous view — no student link)
    sf2, sargs2 = _session_filter(session_ids, 's')
    rows = db.query(
        f"""SELECT a.question_id, a.option_id, qo.label, qo.value, qo.position
            FROM answers a
            JOIN submissions s ON s.id = a.submission_id
            LEFT JOIN question_options qo ON qo.id = a.option_id
            WHERE s.form_id = ? AND a.question_id IN ({ph})
              AND a.option_id IS NOT NULL{sf2}""",
        (form_id, *qids, *sargs2))

    # Group by question
    by_q: Dict[int, List[dict]] = defaultdict(list)
    for r in rows:
        by_q[r['question_id']].append(dict(r))

    criteria = []
    total_earned = 0.0
    total_max = 0.0
    distribution: Dict[int, dict] = {}

    for q in questions:
        opts = q['options']
        max_pts = _question_max_points(opts)
        if max_pts is None:
            continue
        answers = by_q.get(q['id'], [])
        earned = 0.0
        scored_n = 0
        # Rating distribution: label → count
        dist_counts: Dict[str, int] = {o['label']: 0 for o in opts}
        for a in answers:
            pts = _option_points(a)
            if pts is None:
                continue
            earned += pts
            scored_n += 1
            lbl = a.get('label') or str(a.get('value'))
            dist_counts[lbl] = dist_counts.get(lbl, 0) + 1

        if scored_n == 0:
            crit_score = None
        else:
            crit_score = round((earned / (max_pts * scored_n)) * 100, 2)
            total_earned += earned
            total_max += max_pts * scored_n

        pct_dist = {}
        for lbl, cnt in dist_counts.items():
            pct_dist[lbl] = {
                'count': cnt,
                'pct': round((cnt / scored_n) * 100, 1) if scored_n else 0.0,
            }

        criteria.append({
            'question_id': q['id'],
            'text': q['text'],
            'position': q['position'],
            'score': crit_score,
            'valid_answers': scored_n,
            'max_points': max_pts,
            'distribution': pct_dist,
        })
        distribution[q['id']] = pct_dist

    overall = None
    if total_max > 0:
        overall = round((total_earned / total_max) * 100, 2)

    return {
        'form_id': form_id,
        'overall_score': overall,
        'valid_responses': sub_count,
        'criteria': criteria,
        'distribution': distribution,
        'hidden': False,
        'reason': None,
    }


def faculty_scores_for_branches(
    branch_ids: List[int],
    *,
    academic_year_id: Optional[int] = None,
    semester_id: Optional[int] = None,
    subject_code: Optional[str] = None,
    form_id: Optional[int] = None,
    teacher_user_id: Optional[int] = None,
    min_responses: int = MIN_RESPONSE_THRESHOLD,
) -> List[dict]:
    """Marks-sheet rows for faculty in the given branches.

    Each row: teacher info, form info, overall_score, valid_responses, criteria summary.
    """
    if not branch_ids:
        return []

    ph = ','.join('?' * len(branch_ids))
    sql = f"""
        SELECT ff.id AS form_id, ff.title, ff.status, ff.published_at,
               ff.snapshot_subject_code AS subject_code,
               ff.snapshot_subject_name AS subject_name,
               ff.snapshot_branch AS branch_code,
               ff.category,
               ff.academic_year_id, ff.semester_id, ff.branch_id,
               cs.teacher_user_id,
               u.name AS teacher_name, u.emp_code AS teacher_emp_code,
               (SELECT COUNT(*) FROM submissions s WHERE s.form_id = ff.id) AS response_count
        FROM feedback_forms ff
        LEFT JOIN class_sessions cs ON cs.id = ff.session_id
        LEFT JOIN users u ON u.id = cs.teacher_user_id
        WHERE ff.branch_id IN ({ph})
          AND ff.is_deleted = 0
          AND ff.status IN ('open', 'closed', 'archived')
    """
    params: list = list(branch_ids)
    if academic_year_id:
        sql += ' AND ff.academic_year_id = ?'
        params.append(academic_year_id)
    if semester_id:
        sql += ' AND ff.semester_id = ?'
        params.append(semester_id)
    if subject_code:
        sql += ' AND (ff.subject_code = ? OR ff.snapshot_subject_code = ?)'
        params.extend([subject_code, subject_code])
    if form_id:
        sql += ' AND ff.id = ?'
        params.append(form_id)
    if teacher_user_id:
        sql += ' AND cs.teacher_user_id = ?'
        params.append(teacher_user_id)
    sql += ' ORDER BY u.name, ff.id'

    forms = db.query(sql, tuple(params))
    rows = []
    for f in forms:
        f = dict(f)
        scores = compute_form_scores(f['form_id'], min_responses=min_responses)
        f['overall_score'] = scores['overall_score']
        f['valid_responses'] = scores['valid_responses']
        f['hidden'] = scores['hidden']
        f['criteria'] = scores['criteria']
        f['reason'] = scores.get('reason')
        rows.append(f)
    return rows


def compare_periods(
    teacher_user_id: int,
    form_id_a: int,
    form_id_b: int,
    min_responses: int = MIN_RESPONSE_THRESHOLD,
) -> Dict[str, Any]:
    """Historical comparison between any two distinct forms for the same teacher.

    Questions are matched by stable question text (normalised) when IDs differ.
    Incompatible scales → N/A for that criterion.
    """
    if form_id_a == form_id_b:
        return {'error': 'Identical periods are not allowed', 'ok': False}

    sa = compute_form_scores(form_id_a, min_responses=min_responses)
    sb = compute_form_scores(form_id_b, min_responses=min_responses)

    # Build text → criterion maps
    def by_text(criteria):
        m = {}
        for c in criteria:
            key = (c['text'] or '').strip().lower()
            if key:
                m[key] = c
        return m

    ma, mb = by_text(sa['criteria']), by_text(sb['criteria'])
    all_keys = sorted(set(ma) | set(mb))

    criterion_changes = []
    for key in all_keys:
        ca, cb = ma.get(key), mb.get(key)
        score_a = ca['score'] if ca else None
        score_b = cb['score'] if cb else None
        change = None
        status = 'ok'
        if score_a is None or score_b is None:
            status = 'n/a'
            reason = 'Missing in one period or below threshold'
        elif ca and cb and ca.get('max_points') != cb.get('max_points'):
            status = 'n/a'
            reason = 'Incompatible scale'
        else:
            change = round(score_b - score_a, 2)
            reason = None
        criterion_changes.append({
            'text': (ca or cb or {}).get('text') or key,
            'score_a': score_a,
            'score_b': score_b,
            'change': change,
            'status': status,
            'reason': reason if status == 'n/a' else None,
        })

    overall_change = None
    if sa['overall_score'] is not None and sb['overall_score'] is not None:
        overall_change = round(sb['overall_score'] - sa['overall_score'], 2)

    return {
        'ok': True,
        'teacher_user_id': teacher_user_id,
        'period_a': sa,
        'period_b': sb,
        'overall_a': sa['overall_score'],
        'overall_b': sb['overall_score'],
        'overall_change': overall_change,
        'responses_a': sa['valid_responses'],
        'responses_b': sb['valid_responses'],
        'criterion_changes': criterion_changes,
    }


def department_average(
    branch_ids: List[int],
    *,
    academic_year_id: Optional[int] = None,
    semester_id: Optional[int] = None,
    min_responses: int = MIN_RESPONSE_THRESHOLD,
) -> Optional[float]:
    """Average overall score across forms in the branches (non-hidden only)."""
    rows = faculty_scores_for_branches(
        branch_ids,
        academic_year_id=academic_year_id,
        semester_id=semester_id,
        min_responses=min_responses,
    )
    scores = [r['overall_score'] for r in rows
              if not r.get('hidden') and r.get('overall_score') is not None]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 2)


# ---------------------------------------------------------------------------
# New 10-marks-per-student marking rule (each student contributes 10 marks
# total, split equally across scoreable questions).
# ---------------------------------------------------------------------------

# Share of a question's marks by rating label (Excellent = full marks, etc.)
RATING_SHARE = {
    'excellent': 1.0,
    'very good': 0.75,
    'verygood': 0.75,
    'good': 0.5,
    'average': 0.375,
    'fair': 0.375,
    'poor': 0.25,
    'very poor': 0.25,
    'verypoor': 0.25,
    'bad': 0.25,
    'unsatisfactory': 0.25,
    'satisfactory': 0.5,
}


def marks_per_question(n: int) -> float:
    """Marks allocated to each scoreable question (10 / n), rounded to 2 dp."""
    if not n or n <= 0:
        return 0.0
    return round(10.0 / n, 2)


def rating_share(label: Optional[str]) -> Optional[float]:
    """Return the fraction of question marks for a rating label, or None."""
    if not label:
        return None
    lab = label.strip().lower()
    if lab in RATING_SHARE:
        return RATING_SHARE[lab]
    first = lab.split()[0] if lab else ''
    if first in RATING_SHARE:
        return RATING_SHARE[first]
    for key, share in RATING_SHARE.items():
        if key in lab:
            return share
    return None


def form_marks(form_id: int, eligible: int, session_ids=None) -> Dict[str, Any]:
    """Compute obtained / maximum marks under the 10-marks-per-student rule.

    Returns:
      questions          – scoreable question list
      marks_per_question – float (10 / n)
      students           – distinct submissions count
      eligible           – eligible students (passed in; used for maximum)
      obtained           – sum of marks over every answer (2 dp)
      maximum            – 10 * eligible (2 dp)
      rating_breakdown   – list of {label, count, marks_each, total_marks}
      answer_count       – total rating answers across all questions
    """
    questions = form_rating_questions(form_id)
    n = len(questions)
    mpq = marks_per_question(n)

    sf, sargs = _session_filter(session_ids, 'submissions')
    sub_count = db.query(
        'SELECT COUNT(*) c FROM submissions WHERE form_id=?' + sf,
        (form_id,) + sargs, one=True)['c']

    eligible = int(eligible or 0)
    maximum = round(10.0 * eligible, 2)

    if not questions:
        return {
            'questions': [],
            'marks_per_question': 0.0,
            'question_count': 0,
            'students': sub_count,
            'eligible': eligible,
            'obtained': 0.0,
            'maximum': maximum,
            'rating_breakdown': [],
            'answer_count': 0,
        }

    qids = [q['id'] for q in questions]
    ph = ','.join('?' * len(qids))
    sf2, sargs2 = _session_filter(session_ids, 's')
    rows = db.query(
        f"""SELECT a.question_id, a.option_id, qo.label, qo.value
            FROM answers a
            JOIN submissions s ON s.id = a.submission_id
            LEFT JOIN question_options qo ON qo.id = a.option_id
            WHERE s.form_id = ? AND a.question_id IN ({ph})
              AND a.option_id IS NOT NULL{sf2}""",
        (form_id, *qids, *sargs2))

    # Aggregate by rating label
    buckets: Dict[str, dict] = {}
    obtained = 0.0
    answer_count = 0

    for r in rows:
        label = (r['label'] or str(r.get('value') or '')).strip()
        share = rating_share(label)
        if share is None:
            # Fallback: try option points relative to max (5-scale)
            pts = _option_points(dict(r))
            if pts is None:
                continue
            share = pts / 5.0
        marks_each = round(mpq * share, 2)
        obtained += marks_each
        answer_count += 1
        key = label or 'Option'
        if key not in buckets:
            buckets[key] = {
                'label': key,
                'count': 0,
                'marks_each': marks_each,
                'total_marks': 0.0,
            }
        buckets[key]['count'] += 1
        buckets[key]['total_marks'] = round(
            buckets[key]['total_marks'] + marks_each, 2)

    order = ['excellent', 'very good', 'good', 'average', 'poor', 'bad', 'very poor']
    breakdown = list(buckets.values())

    def _sk(it):
        lab = (it['label'] or '').lower()
        for i, o in enumerate(order):
            if o in lab:
                return i
        return 99

    breakdown.sort(key=_sk)
    for b in breakdown:
        b['total_marks'] = round(b['total_marks'], 2)

    obtained = round(obtained, 2)

    return {
        'questions': questions,
        'marks_per_question': mpq,
        'question_count': n,
        'students': sub_count,
        'eligible': eligible,
        'obtained': obtained,
        'maximum': maximum,
        'rating_breakdown': breakdown,
        'answer_count': answer_count,
    }
