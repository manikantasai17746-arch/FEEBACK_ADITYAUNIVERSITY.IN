"""Round-by-round comparison rules: rounds, headline, rank scope, department average, question matching."""
from feedback_system import round_comparison as rc

def row(fid, teacher, subject, tt, cat, score, pub, branch=1, status='closed', responses=12, criteria=None, name=None):
    return {'form_id': fid, 'teacher_user_id': teacher, 'teacher_name': name or f'T{teacher}',
            'subject_code': subject, 'subject_name': f'{subject} name', 'timetable_id': tt, 'category': cat,
            'overall_score': score, 'published_at': pub, 'status': status, 'valid_responses': responses,
            'branch_id': branch, 'criteria': criteria or []}

Q = lambda pairs: [{'text': t, 'score': s} for t, s in pairs]
rows = [
    row(3, 7, 'CS310', 5, 'main', 80.0, '2026-10-01', criteria=Q([('Explains clearly', 80), ('Punctual', 60)])),
    row(1, 7, 'CS310', 5, 'main', 70.0, '2026-08-01', criteria=Q([('Explains clearly', 70), ('Punctual', 50)])),
    row(2, 7, 'CS310', 5, 'main', 75.0, '2026-09-01', criteria=Q([('explains  clearly', 75), ('Punctual', 60)])),
    row(9, 7, 'CS310', 6, 'main', 50.0, '2026-09-05'),          # different class: separate series
    row(10, 7, 'CS310', 5, 'lab', 40.0, '2026-09-06'),          # lab: separate series
    row(11, 7, 'CS310', 5, 'main', 99.0, '2026-11-01', status='open'),   # open: ignored
    row(12, 7, 'CS310', 5, 'main', 99.0, '2026-11-02', responses=0),     # no responses: ignored
    row(13, 8, 'CS310', 5, 'main', 60.0, '2026-08-15'),         # other teacher, same subject
    row(14, 8, 'CS310', 5, 'main', 90.0, '2026-09-20'),
    row(15, 9, 'MA101', 5, 'main', 95.0, '2026-09-20', branch=1),  # other subject: not in CS310 peers
]
series = rc.build_series(rows)
main7 = next(s for s in series if s['teacher_user_id'] == 7 and s['category'] == 'main' and s['timetable_id'] == 5)
assert [r['score'] for r in main7['rounds']] == [70.0, 75.0, 80.0], main7['rounds']
assert [r['round_no'] for r in main7['rounds']] == [1, 2, 3]
assert len([s for s in series if s['teacher_user_id'] == 7]) == 3   # main/class5, main/class6, lab/class5
h = rc.headline(main7)
assert (h['latest_score'], h['previous_score'], h['change'], h['trend'], h['average']) == (80.0, 75.0, 5.0, 'improved', 75.0)
assert h['message'] is None
single = rc.build_series([row(20, 3, 'X', 1, 'main', 66.0, '2026-01-01')])[0]
hs = rc.headline(single)
assert hs['latest_score'] == 66.0 and hs['previous_score'] is None and hs['message'] == 'No previous round to compare.'
assert [r['round_no'] for r in single['rounds']] == [1]
q = rc.question_comparison(main7)
assert q['from_round'] == 2 and q['to_round'] == 3
got = {x['text']: (x['from'], x['to'], x['trend']) for x in q['rows']}
assert got == {'Explains clearly': (3.75, 4.0, 'improved'), 'Punctual': (3.0, 3.0, 'nochange')}, got
rank = rc.department_rank(series, main7)
assert rank['of'] == 2 and rank['rank'] == 2, rank   # same class 5: teacher 8 (90) > teacher 7 (80)
avg = rc.department_average_by_round(series, main7)
assert avg == {1: 60.0, 2: 90.0}, avg   # teacher 8 only; teacher 7's own classes excluded
