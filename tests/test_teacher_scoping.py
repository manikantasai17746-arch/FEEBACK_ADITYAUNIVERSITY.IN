"""Teacher dashboard numbers must be scoped to the teacher's OWN sessions.

A subject-wide form covers many teachers' sections. Eligible students, marks
and score shown to one teacher must not include another teacher's sections.
"""
from feedback_system import db, scoring, services
from feedback_system.security import hash_password


def _build(app):
    with app.app_context():
        t = db.query("SELECT id FROM users WHERE emp_code='6817'", one=True)['id']
        mine = db.query("SELECT id FROM class_sessions WHERE teacher_user_id=? "
                        "AND subject_code='2501CS10'", (t,), one=True)['id']
        others = [r['id'] for r in db.query(
            "SELECT id FROM class_sessions WHERE subject_code='2501CS10' "
            "AND teacher_user_id<>? AND is_deleted=0 ORDER BY id LIMIT 2", (t,))]
        admin = db.query("SELECT id FROM users WHERE role='admin' LIMIT 1", one=True)['id']
        fid = services.save_form(
            {'title': 'Scoping', 'session_id': mine, 'scope': 'subject',
             'subject_code': '2501CS10', 'subject_name': 'ADSA'},
            [{'text': 'Q1', 'qtype': 'rating', 'is_required': True,
              'options': [{'label': 'Excellent', 'value': 5}, {'label': 'Poor', 'value': 1}]}],
            admin, session_ids=[mine] + others)
        qid = db.query('SELECT id FROM form_questions WHERE form_id=?', (fid,), one=True)['id']
        opt = {r['label']: r['id'] for r in db.query(
            'SELECT id, label FROM question_options WHERE question_id=?', (qid,))}

        def submit(sess, label, n):
            for r in db.query('SELECT student_user_id s FROM session_students '
                              'WHERE session_id=? LIMIT ?', (sess, n)):
                sid = db.insert_id('INSERT INTO submissions(form_id,session_id,student_user_id) '
                                   'VALUES(?,?,?)', (fid, sess, r['s']))
                db.get_db().execute('INSERT INTO answers(submission_id,question_id,option_id) '
                                    'VALUES(?,?,?)', (sid, qid, opt[label]))
        submit(mine, 'Excellent', 2)
        submit(others[0], 'Poor', 3)
        db.get_db().commit()
        return fid, mine, others


def test_teacher_scoped_eligible_marks_and_score(app):
    fid, mine, others = _build(app)
    with app.app_context():
        form_wide = services.eligible_count_for_form(fid)
        own = services.eligible_count_for_sessions([mine])
        assert own == len(services.eligible_students(mine))      # real timetable roster
        assert form_wide > own                                     # form covers other sections

        fm = scoring.form_marks(fid, own, session_ids=[mine])
        assert fm['students'] == 2 and fm['maximum'] == 10.0 * own
        assert fm['obtained'] == 20.0                              # 2 x Excellent only
        assert scoring.compute_form_scores(
            fid, min_responses=1, session_ids=[mine])['overall_score'] == 100.0
        # unscoped (old behaviour) mixes in the other teacher's 'Poor' answers
        assert scoring.form_marks(fid, own)['students'] == 5
