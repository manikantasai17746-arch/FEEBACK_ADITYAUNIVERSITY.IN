"""Several feedback rounds for the same teacher + subject + timetable.

Each round is its own form: its own eligibility, submissions, marks and score.
"""
import re

from feedback_system import db, scoring, services
from feedback_system.security import hash_password

from helpers import login

TEACHER = '6817'
SUBJECT = '2501CS10'


def _q():
    return [{'text': 'Teaching quality', 'qtype': 'rating', 'is_required': True,
             'options': [{'label': 'Excellent', 'value': 5}, {'label': 'Poor', 'value': 1}]}]


def _make_round(app, session_id, title, status='open', publish=True):
    with app.app_context():
        admin = db.query("SELECT id FROM users WHERE role='admin' LIMIT 1", one=True)['id']
        tt = db.query('SELECT t.id, t.branch_id, t.semester_id, t.academic_year_id '
                      'FROM class_sessions cs JOIN timetables t ON t.id=cs.timetable_id '
                      'WHERE cs.id=?', (session_id,), one=True)
        fid = services.save_form(
            {'title': title, 'session_id': session_id, 'scope': 'session',
             'subject_code': SUBJECT, 'subject_name': 'ADSA', 'timetable_id': tt['id'],
             'branch_id': tt['branch_id'], 'semester_id': tt['semester_id'],
             'academic_year_id': tt['academic_year_id']}, _q(), admin)
        db.get_db().execute('UPDATE feedback_forms SET status=? WHERE id=?', (status, fid))
        if publish:
            db.get_db().execute(
                "INSERT INTO result_publications(form_id,session_id,is_published,published_at) "
                "VALUES(?,?,1,'2026-01-01')", (fid, session_id))
        db.get_db().commit()
        return fid


def _session(app):
    with app.app_context():
        return db.query(
            "SELECT cs.id FROM class_sessions cs JOIN users u ON u.id=cs.teacher_user_id "
            "WHERE u.emp_code=? AND cs.subject_code=? AND cs.is_deleted=0 LIMIT 1",
            (TEACHER, SUBJECT), one=True)['id']


def _students(app, session_id, n):
    with app.app_context():
        return [r['id'] for r in services.eligible_students(session_id)][:n]


def _submit(app, student_id, form_id, label):
    with app.test_request_context():
        qid = db.query('SELECT id FROM form_questions WHERE form_id=?', (form_id,), one=True)['id']
        opt = db.query('SELECT id FROM question_options WHERE question_id=? AND label=?',
                       (qid, label), one=True)['id']
        return services.submit_feedback(student_id, form_id, {f'q_{qid}': str(opt)})


def test_four_rounds_are_independent(app):
    sess = _session(app)
    rounds = [_make_round(app, sess, f'ADSA — Feedback Round {i}') for i in range(1, 5)]
    assert len(set(rounds)) == 4                       # 4 distinct form ids (scenarios 1-2)
    s1, s2, s3 = _students(app, sess, 3)

    # Round 1: submitted by s1, s2.  Round 2 must still be pending for them (scenario 3).
    assert _submit(app, s1, rounds[0], 'Excellent')[0]
    assert _submit(app, s2, rounds[0], 'Poor')[0]
    with app.app_context():
        r1_before = scoring.form_marks(rounds[0], 72)
        score1_before = scoring.compute_form_scores(rounds[0], min_responses=1)['overall_score']
        status = {f['id']: f['submission_id'] for f in services.student_forms(s1)}
        assert status[rounds[0]] and not status[rounds[1]]          # R1 submitted, R2 pending
        assert services.student_eligible_for_form(s1, rounds[1])

    # Round 2 by s1 works after Round 1 (scenarios 4, 12) ...
    ok, _ = _submit(app, s1, rounds[1], 'Excellent')
    assert ok
    # ... and a duplicate in the SAME round is rejected (scenario 5).
    ok, msg = _submit(app, s1, rounds[1], 'Excellent')
    assert not ok and 'already submitted' in msg
    assert _submit(app, s3, rounds[1], 'Poor')[0]

    with app.app_context():
        # Round 1 results unchanged by Round 2 activity (scenario 4)
        assert scoring.form_marks(rounds[0], 72) == r1_before
        assert scoring.compute_form_scores(rounds[0], min_responses=1)['overall_score'] == score1_before
        # Each round has its own count (scenario 6)
        assert services.response_count(rounds[0]) == 2
        assert services.response_count(rounds[1]) == 2
        assert services.response_count(rounds[2]) == 0 == services.response_count(rounds[3])
        for f in rounds:
            assert services.eligible_count_for_form(f) == 72
        # Round labels follow creation order
        info = services.round_info(rounds)
        assert [info[f]['label'] for f in rounds] == [f'Round {i}' for i in range(1, 5)]
        assert all(info[f]['rounds_total'] == 4 for f in rounds)
        # Round 1 submitters appear as submitted ONLY in round 1 reports (scenario 10)
        sub1 = {r['roll'] for r in services.report_rows([services.get_form(rounds[0])], 'submitted')}
        sub3 = {r['roll'] for r in services.report_rows([services.get_form(rounds[2])], 'submitted')}
        assert len(sub1) == 2 and not sub3
        pend3 = services.report_rows([services.get_form(rounds[2])], 'not_submitted')
        assert len(pend3) == 72 and all(r['form_id'] == rounds[2] for r in pend3)
        # HOD / Dean scoring service returns one row per round (scenarios 8, 9)
        branch = db.query('SELECT branch_id FROM feedback_forms WHERE id=?', (rounds[0],), one=True)['branch_id']
        tid = db.query("SELECT id FROM users WHERE emp_code=?", (TEACHER,), one=True)['id']
        rows = scoring.faculty_scores_for_branches([branch], teacher_user_id=tid, subject_code=SUBJECT)
        assert set(rounds) <= {r['form_id'] for r in rows}
        by = {r['form_id']: r for r in rows}
        assert by[rounds[0]]['valid_responses'] == 2 and by[rounds[2]]['valid_responses'] == 0
        assert scoring.compare_periods(tid, rounds[0], rounds[1], min_responses=1)['ok']


def test_teacher_dashboard_lists_every_round_separately(app):
    sess = _session(app)
    with app.app_context():
        tid = db.query("SELECT id FROM users WHERE emp_code=?", (TEACHER,), one=True)['id']
        db.execute('UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?',
                   (hash_password('mypass-1'), tid))
        db.get_db().commit()
    c = app.test_client()
    r = login(c, '/teacher/login', {'emp_code': TEACHER, 'password': 'mypass-1'})
    assert r.status_code == 302
    html = c.get('/teacher/').get_data(as_text=True)
    text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))
    for i in range(1, 5):
        assert f'Round {i} of' in text                       # four separate records
    assert len(re.findall(r'View Details', html)) >= 4        # each with its own action
    # tile counts students once, not once per round
    assert re.search(r'<div class="num">72</div>\s*<div class="lbl">Eligible Students', html)


def test_duplicate_guard_allows_new_round_blocks_same_title(app):
    with app.app_context():
        cfg = db.query("SELECT t.academic_year_id ay, t.semester_id sm, t.branch_id br, t.id tt "
                       "FROM class_sessions cs JOIN timetables t ON t.id=cs.timetable_id "
                       "WHERE cs.subject_code=? LIMIT 1", (SUBJECT,), one=True)
        admin = db.query("SELECT id FROM users WHERE role='admin' LIMIT 1", one=True)['id']
        sess = db.query('SELECT id FROM class_sessions WHERE subject_code=? LIMIT 1',
                        (SUBJECT,), one=True)['id']
        base = {'session_id': sess, 'scope': 'subject', 'subject_code': SUBJECT,
                'subject_name': 'ADSA', 'academic_year_id': cfg['ay'],
                'semester_id': cfg['sm'], 'branch_id': cfg['br'], 'timetable_id': None}
        args = (cfg['ay'], cfg['sm'], cfg['br'], None, SUBJECT)
        services.save_form({**base, 'title': 'Subject round A'}, _q(), admin, session_ids=[sess])
        assert services.find_duplicate_subject_form(*args, title='Subject round A')      # same title
        assert services.find_duplicate_subject_form(*args, title=' subject ROUND a ')    # normalised
        assert not services.find_duplicate_subject_form(*args, title='Subject round B')  # new round OK


def test_bulk_generation_adds_a_new_round(app):
    with app.test_request_context():
        admin = db.query("SELECT id FROM users WHERE role='admin' LIMIT 1", one=True)['id']
        tid = db.query("SELECT id FROM users WHERE emp_code=?", (TEACHER,), one=True)['id']
        f = {'teacher_id': tid, 'subject_code': SUBJECT}
        n_sessions = len(services.sessions_for_bulk(f))
        before = db.query('SELECT COUNT(*) c FROM feedback_forms', one=True)['c']
        a = services.bulk_create_forms(f, _q(), '{subject} — Bulk Round 1', 'draft', admin)
        assert a['created'] == n_sessions and a['skipped'] == 0
        again = services.bulk_create_forms(f, _q(), '{subject} — Bulk Round 1', 'draft', admin)
        assert again['created'] == 0 and again['skipped'] == n_sessions       # idempotent
        b = services.bulk_create_forms(f, _q(), '{subject} — Bulk Round 2', 'draft', admin)
        assert b['created'] == n_sessions                                      # new round allowed
        assert services.bulk_preview(f, '{subject} — Bulk Round 3')['to_create'] == n_sessions
        assert db.query('SELECT COUNT(*) c FROM feedback_forms', one=True)['c'] == before + 2 * n_sessions
        # earlier forms untouched (scenario 11)
        assert db.query("SELECT COUNT(*) c FROM feedback_forms WHERE title LIKE '%Bulk Round 1'",
                        one=True)['c'] == n_sessions
