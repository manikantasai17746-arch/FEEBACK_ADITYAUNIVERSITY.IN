"""Subject-wise, multi-round teacher feedback comparison.

For every teacher this groups the *closed* feedback rounds by subject and shows
how the score moved from round to round.  Subjects are never mixed: each
subject keeps its own average and its own trend, so an unrelated subject can
never pull another one up or down.

The same data feeds the HOD and Dean comparison pages and the PDF download.
"""
from typing import Any, Dict, List, Optional

from . import db, scoring

CLOSED_STATUSES = ('closed', 'archived')

# A change smaller than this (in percentage points) is reported as "No change".
TREND_EPSILON = 0.05


def trend_label(change: Optional[float]) -> str:
    """'Improved' / 'Declined' / 'No change' for a score change (None -> '—')."""
    if change is None:
        return '—'
    if change > TREND_EPSILON:
        return 'Improved'
    if change < -TREND_EPSILON:
        return 'Declined'
    return 'No change'


def band_for(score: Optional[float]) -> Optional[Dict[str, str]]:
    """The score band (same bands as the Performance page) a percentage falls in."""
    if score is None:
        return None
    from .hod import SCORE_BANDS  # lazy: hod imports this module's helpers lazily too
    for key, label, lo, hi in SCORE_BANDS:
        if (lo is None or score > lo) and (hi is None or score <= hi):
            return {'key': key, 'label': label}
    return None


def _date_part(value) -> str:
    return str(value)[:10] if value else ''


def _subject_block(subject_code: str, subject_name: str, forms: List[dict]) -> Dict[str, Any]:
    """One subject: its rounds in date order, step changes and the net change."""
    forms = sorted(forms, key=lambda f: (_date_part(f.get('published_at')), f['form_id']))
    rounds = []
    previous = None
    for f in forms:
        score = round(float(f['overall_score']), 2)
        step = None if previous is None else round(score - previous, 2)
        rounds.append({
            'form_id': f['form_id'],
            'title': f.get('title') or f"Form #{f['form_id']}",
            'round_date': _date_part(f.get('published_at')),
            'score': score,
            'responses': f.get('valid_responses') or 0,
            'step_change': step,
            'step_trend': trend_label(step),
        })
        previous = score

    average = round(sum(r['score'] for r in rounds) / len(rounds), 2)
    net = None
    if len(rounds) >= 2:
        net = round(rounds[-1]['score'] - rounds[0]['score'], 2)
    return {
        'subject_code': subject_code,
        'subject_name': subject_name,
        'round_count': len(rounds),
        'average_score': average,
        'first_score': rounds[0]['score'],
        'latest_score': rounds[-1]['score'],
        'net_change': net,
        'net_trend': trend_label(net),
        'latest_date': rounds[-1]['round_date'] or None,
        'band': band_for(average),
        'rounds': rounds,
    }


def build_teacher_profiles(
    branch_ids: List[int],
    *,
    teacher_user_id: Optional[int] = None,
    academic_year_id: Optional[int] = None,
    semester_id: Optional[int] = None,
    q: str = '',
) -> List[Dict[str, Any]]:
    """Comparison profile for every teacher that has closed, scored rounds.

    Each profile: teacher_user_id / teacher_name / teacher_emp_code, ``subjects``
    (see :func:`_subject_block`), plus ``subject_count``, ``round_count`` and
    ``overall_average`` (mean of the subject averages, for sorting only).
    Only forms inside ``branch_ids`` are ever read, so the caller's scope is
    the only data that can appear.
    """
    if not branch_ids:
        return []
    rows = scoring.faculty_scores_for_branches(
        branch_ids,
        academic_year_id=academic_year_id,
        semester_id=semester_id,
        teacher_user_id=teacher_user_id,
        min_responses=1,
    )

    by_teacher: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        if r.get('status') not in CLOSED_STATUSES:
            continue
        if r.get('overall_score') is None or not r.get('teacher_user_id'):
            continue
        t = by_teacher.setdefault(r['teacher_user_id'], {
            'teacher_user_id': r['teacher_user_id'],
            'teacher_name': r.get('teacher_name'),
            'teacher_emp_code': r.get('teacher_emp_code'),
            'subjects': {},
        })
        code = (r.get('subject_code') or '').strip() or '—'
        name = r.get('subject_name') or ''
        subj = t['subjects'].setdefault(code, {'name': name, 'forms': []})
        if not subj['name'] and name:
            subj['name'] = name
        subj['forms'].append(r)

    needle = (q or '').strip().lower()
    profiles = []
    for t in by_teacher.values():
        subjects = [_subject_block(code, s['name'], s['forms'])
                    for code, s in sorted(t['subjects'].items())]
        profile = {
            'teacher_user_id': t['teacher_user_id'],
            'teacher_name': t['teacher_name'],
            'teacher_emp_code': t['teacher_emp_code'],
            'subjects': subjects,
            'subject_count': len(subjects),
            'round_count': sum(s['round_count'] for s in subjects),
            'overall_average': round(
                sum(s['average_score'] for s in subjects) / len(subjects), 2),
        }
        if needle:
            hay = ' '.join([
                (profile['teacher_name'] or ''), (profile['teacher_emp_code'] or ''),
                ' '.join(f"{s['subject_code']} {s['subject_name']}" for s in subjects),
            ]).lower()
            if needle not in hay:
                continue
        profiles.append(profile)

    profiles.sort(key=lambda p: ((p['teacher_name'] or '').lower(), p['teacher_user_id']))
    return profiles


def profile_for_teacher(
    branch_ids: List[int],
    teacher_user_id: int,
    *,
    academic_year_id: Optional[int] = None,
    semester_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """One teacher's profile, or an empty one (no subjects) if there are no closed rounds.

    Returns None when the user is not a teacher/HOD at all.
    """
    found = build_teacher_profiles(
        branch_ids, teacher_user_id=teacher_user_id,
        academic_year_id=academic_year_id, semester_id=semester_id)
    if found:
        return found[0]
    user = db.query(
        "SELECT id, name, emp_code FROM users WHERE id=? AND role IN ('teacher','hod')",
        (teacher_user_id,), one=True)
    if not user:
        return None
    return {
        'teacher_user_id': user['id'],
        'teacher_name': user['name'],
        'teacher_emp_code': user['emp_code'],
        'subjects': [], 'subject_count': 0, 'round_count': 0, 'overall_average': None,
    }
