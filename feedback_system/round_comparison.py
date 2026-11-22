"""Round-by-round comparison for one teacher, one subject, one class, one category.

A *round* is one closed (or archived) feedback form with at least one response.
Forms that share teacher + subject + class (timetable) + category form one series.
Main, second-faculty and lab forms are never mixed.  Pure functions only: the
caller supplies rows from the database, so this module is easy to test.
"""
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

CLOSED_STATUSES = ('closed', 'archived')
TREND_EPSILON = 0.05
CATEGORY_LABELS = {'main': 'Main Faculty', 'second': 'Second Faculty', 'lab': 'Lab Faculty'}


def trend(change: Optional[float]) -> str:
    if change is None:
        return 'none'
    if change > TREND_EPSILON:
        return 'improved'
    if change < -TREND_EPSILON:
        return 'declined'
    return 'nochange'


def norm_question(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '').strip().lower())


def series_key(row: Dict[str, Any]) -> Tuple:
    return (row['teacher_user_id'], row.get('subject_code') or '', row.get('timetable_id'), row.get('category') or '')


def eligible(row: Dict[str, Any]) -> bool:
    return (row.get('status') in CLOSED_STATUSES
            and row.get('overall_score') is not None
            and (row.get('valid_responses') or row.get('response_count') or 0) > 0
            and row.get('teacher_user_id') is not None)


def build_series(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group eligible rows into series, each with numbered rounds in publish order."""
    grouped: Dict[Tuple, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if eligible(r):
            grouped[series_key(r)].append(r)
    out = []
    for key, forms in grouped.items():
        forms.sort(key=lambda f: (str(f.get('published_at') or ''), f['form_id']))
        rounds = []
        for n, f in enumerate(forms, start=1):
            score = round(float(f['overall_score']), 2)
            prev = rounds[-1]['score'] if rounds else None
            change = None if prev is None else round(score - prev, 2)
            rounds.append({
                'round_no': n,
                'form_id': f['form_id'],
                'published_at': str(f.get('published_at') or '')[:10],
                'score': score,
                'change': change,
                'trend': trend(change),
                'responses': f.get('valid_responses') or f.get('response_count') or 0,
                'questions': {norm_question(c['text']): c.get('score')
                              for c in (f.get('criteria') or []) if c.get('text')},
                'question_text': {norm_question(c['text']): c['text']
                                  for c in (f.get('criteria') or []) if c.get('text')},
            })
        teacher_id, subject_code, timetable_id, category = key
        out.append({
            'key': key,
            'teacher_user_id': teacher_id,
            'teacher_name': forms[-1].get('teacher_name'),
            'branch_id': forms[-1].get('branch_id'),
            'subject_code': subject_code,
            'subject_name': forms[-1].get('subject_name') or subject_code,
            'timetable_id': timetable_id,
            'category': category,
            'category_label': CATEGORY_LABELS.get(category, ''),
            'rounds': rounds,
        })
    out.sort(key=lambda s: ((s['teacher_name'] or '').lower(), s['subject_code'], str(s['timetable_id']), s['category']))
    return out


def headline(series: Dict[str, Any]) -> Dict[str, Any]:
    rounds = series['rounds']
    latest = rounds[-1]
    previous = rounds[-2] if len(rounds) >= 2 else None
    change = None if previous is None else round(latest['score'] - previous['score'], 2)
    avg = round(sum(r['score'] for r in rounds) / len(rounds), 2)
    return {
        'latest_round_no': latest['round_no'],
        'latest_score': latest['score'],
        'previous_round_no': previous['round_no'] if previous else None,
        'previous_score': previous['score'] if previous else None,
        'change': change,
        'trend': trend(change),
        'average': avg,
        'message': None if previous else 'No previous round to compare.',
    }


def latest_scores_by_scope(all_series: List[Dict[str, Any]]) -> Dict[Tuple, float]:
    """Map series key -> latest round score (only series with at least one round)."""
    return {s['key']: s['rounds'][-1]['score'] for s in all_series if s['rounds']}


def department_rank(all_series: List[Dict[str, Any]], target: Dict[str, Any]) -> Dict[str, Any]:
    """Rank the target's series by latest score among series in the same branch, subject, category and class."""
    peers = [s for s in all_series
             if s['branch_id'] == target['branch_id']
             and s['subject_code'] == target['subject_code']
             and s['category'] == target['category']
             and s['timetable_id'] == target['timetable_id']
             and s['rounds']]
    peers.sort(key=lambda s: s['rounds'][-1]['score'], reverse=True)
    position = next((i for i, s in enumerate(peers, start=1) if s['key'] == target['key']), None)
    return {
        'rank': position,
        'of': len(peers),
        'scope': f"{target['subject_name']} · {target['category_label']} · class {target['timetable_id']} · same department",
    }


def department_average_by_round(all_series: List[Dict[str, Any]], target: Dict[str, Any]) -> Dict[int, float]:
    """Average of the other teachers' scores for the same department, subject, category and class, per round number."""
    buckets: Dict[int, List[float]] = defaultdict(list)
    for s in all_series:
        if (s['teacher_user_id'] == target['teacher_user_id'] or s['branch_id'] != target['branch_id']
                or s['subject_code'] != target['subject_code'] or s['category'] != target['category']
                or s['timetable_id'] != target['timetable_id']):
            continue
        for r in s['rounds']:
            buckets[r['round_no']].append(r['score'])
    return {n: round(sum(v) / len(v), 2) for n, v in sorted(buckets.items()) if v}


def question_comparison(series: Dict[str, Any]) -> Dict[str, Any]:
    """Per-question scores for the two most recent rounds, matched by question text."""
    rounds = series['rounds']
    if len(rounds) < 2:
        return {'rows': [], 'from_round': None, 'to_round': None}
    a, b = rounds[-2], rounds[-1]
    rows = []
    for key in a['questions']:
        if key in b['questions'] and a['questions'][key] is not None and b['questions'][key] is not None:
            pa, pb = round(a['questions'][key] / 20, 2), round(b['questions'][key] / 20, 2)
            rows.append({'text': b['question_text'].get(key, key), 'from': pa, 'to': pb,
                         'change': round(pb - pa, 2), 'trend': trend(round(pb - pa, 2))})
    return {'rows': rows, 'from_round': a['round_no'], 'to_round': b['round_no']}
