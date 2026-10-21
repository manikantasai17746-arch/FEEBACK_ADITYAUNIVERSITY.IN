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


def compute_form_scores(form_id: int, min_responses: int = MIN_RESPONSE_THRESHOLD
                        ) -> Dict[str, Any]:
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
    sub_count = db.query(
        'SELECT COUNT(*) c FROM submissions WHERE form_id=?',
        (form_id,), one=True)['c']

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
    rows = db.query(
        f"""SELECT a.question_id, a.option_id, qo.label, qo.value, qo.position
            FROM answers a
            JOIN submissions s ON s.id = a.submission_id
            LEFT JOIN question_options qo ON qo.id = a.option_id
            WHERE s.form_id = ? AND a.question_id IN ({ph})
              AND a.option_id IS NOT NULL""",
        (form_id, *qids))

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
