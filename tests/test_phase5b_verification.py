"""Phase 5B final verification (unittest-style: runs with `python -m unittest` or pytest).

Covers only the critical requirements: category ownership, eligibility, submission,
anonymity and publishing security.  Runs on an isolated temporary database
(the bundled timetable import); no production data is touched.

    cd <project root>
    python -m unittest tests.test_phase5b_verification -v
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feedback_system import create_app, db, scoring, services          # noqa: E402
from feedback_system import category_forms as cf                        # noqa: E402
from feedback_system import feedback_categories as fc                   # noqa: E402
from feedback_system.security import hash_password                      # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import login                                               # noqa: E402


def teacher_client(app, emp_code):
    c = app.test_client()
    r = login(c, '/teacher/login', {'emp_code': emp_code, 'password': 'teacher@123'})
    assert r.status_code == 302, r.status_code
    with app.app_context():
        db.execute("UPDATE users SET must_change_password=0, first_login_complete=1 "
                   "WHERE emp_code=?", (emp_code,))
    return c

FUTURE = (datetime.utcnow() + timedelta(days=7)).isoformat(timespec='seconds')
PAST = (datetime.utcnow() - timedelta(days=1)).isoformat(timespec='seconds')


class Base(unittest.TestCase):
    app = None
    ids = {}

    @classmethod
    def setUpClass(cls):
        d = tempfile.mkdtemp(prefix='ffs-5bv-')
        cls.app = create_app({
            'TESTING': True, 'DATABASE': os.path.join(d, 't.db'), 'DATABASE_URL': None,
            'SECRET_KEY': 'k', 'AUTO_IMPORT': True, 'ADMIN_EMAIL': 'admin@college.edu',
            'ADMIN_PASSWORD': 'admin123', 'REQUIRE_ACCOUNT_SETUP': False,
            'DEFAULT_TEACHER_PASSWORD': 'teacher@123', 'DEFAULT_STUDENT_PASSWORD': 'student@123',
            'LOGIN_MAX_ATTEMPTS': 50})
        with cls.app.app_context():
            cls._seed()

    @classmethod
    def _seed(cls):
        rows = db.query("""SELECT cs.id sid, cs.teacher_user_id tid, t.branch_id bid,
                  cs.subject_code sc, cs.subject_name sn, t.id ttid, t.semester_id sem,
                  t.academic_year_id ay, u.emp_code emp,
                  (SELECT COUNT(*) FROM session_students ss JOIN users s ON s.id=ss.student_user_id
                    WHERE ss.session_id=cs.id AND s.is_active=1) n
               FROM class_sessions cs JOIN timetables t ON t.id=cs.timetable_id
               JOIN users u ON u.id=cs.teacher_user_id AND u.is_active=1
               WHERE cs.is_deleted=0 AND t.is_deleted=0 ORDER BY cs.id""")
        rows = [dict(r) for r in rows if r['n'] >= 3 and r['emp']]
        bx = rows[0]['bid']
        same = []
        for r in rows:
            if r['bid'] == bx and r['tid'] not in {x['tid'] for x in same}:
                same.append(r)
            if len(same) == 3:
                break
        other = next(r for r in rows if r['bid'] != bx)
        nolab, labonly, sec = same
        used = {x['tid'] for x in same} | {other['tid']}
        ts = [dict(r) for r in db.query(
            "SELECT id, emp_code FROM users WHERE role='teacher' AND is_active=1 "
            "AND emp_code IS NOT NULL ORDER BY id")]
        ts = [t for t in ts if t['id'] not in used]
        second, rej, unres = ts[0], ts[1], ts[2]

        def setting(s, status):
            db.execute("INSERT INTO teacher_subject_settings(session_id, teacher_user_id, "
                       "lab_available, lab_status) VALUES(?,?,?,?)",
                       (s['sid'], s['tid'], 1 if status == 'available' else 0, status))
        setting(nolab, 'none')
        setting(labonly, 'available')
        setting(sec, 'available')
        setting(other, 'available')

        def sfa(s, who, status):
            return db.execute_returning_id(
                """INSERT INTO second_faculty_assignments(academic_year_id, semester_id, branch_id,
                   timetable_id, session_id, subject_code, subject_name, main_emp_code,
                   main_teacher_user_id, second_emp_code, second_teacher_user_id, status)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (s['ay'], s['sem'], s['bid'], s['ttid'], s['sid'], s['sc'], s['sn'], s['emp'],
                 s['tid'], who['emp_code'], who['id'], status))
        a_ok = sfa(sec, second, 'confirmed')
        a_rej = sfa(sec, rej, 'rejected')
        a_unres = sfa(sec, unres, 'unresolved')

        def user(role, name):
            return db.execute_returning_id(
                "INSERT INTO users(role, username, emp_code, name, email, password_hash, is_active)"
                " VALUES(?,?,?,?,?,?,1)", (role, name, name, name, name + '@t.edu',
                                           hash_password('x12345678')))
        hod_a, hod_b = user('hod', 'HODA'), user('hod', 'HODB')
        db.execute("UPDATE users SET onboarding_complete=1, email_verified=1, "
                   "first_login_complete=1 WHERE id IN (?,?)", (hod_a, hod_b))
        db.execute("INSERT INTO user_department_scopes(user_id, branch_id, is_primary) VALUES(?,?,1)",
                   (hod_a, bx))
        db.execute("INSERT INTO user_department_scopes(user_id, branch_id, is_primary) VALUES(?,?,1)",
                   (hod_b, other['bid']))
        students = [r['student_user_id'] for r in db.query(
            "SELECT student_user_id FROM session_students WHERE session_id=? ORDER BY 1", (sec['sid'],))]
        outsider = db.query(
            "SELECT id FROM users WHERE role='student' AND id NOT IN "
            "(SELECT student_user_id FROM session_students WHERE session_id=?) LIMIT 1",
            (sec['sid'],), one=True)['id']
        cls.ids = dict(nolab=nolab, labonly=labonly, sec=sec, other=other, second=second,
                       a_ok=a_ok, a_rej=a_rej, a_unres=a_unres, hod_a=hod_a, hod_b=hod_b,
                       students=students, outsider=outsider, bx=bx)

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    # helpers
    def actor(self, uid):
        return db.query('SELECT * FROM users WHERE id=?', (uid,), one=True)

    def make(self, actor_id, cat, aid, publish=True):
        a = self.actor(actor_id)
        fid = cf.create_form(a, cat, aid, f'{cat} test {aid}', cf.default_questions(cat),
                             deadline_at=FUTURE)
        if publish:
            cf.publish(a, fid)
        return fid

    def answers(self, fid):
        out = {}
        for q in services.form_questions(fid):
            out[f'q_{q["id"]}'] = q['options'][0]['id'] if q['qtype'] == 'rating' else 'ok'
        return out

    def cleanup_forms(self, *fids):
        for f in fids:
            db.execute('DELETE FROM feedback_responses WHERE form_id=?', (f,))
            db.execute('DELETE FROM submissions WHERE form_id=?', (f,))
            db.execute('DELETE FROM form_eligible_students WHERE form_id=?', (f,))
            db.execute('UPDATE feedback_forms SET is_deleted=1, status=\'archived\' WHERE id=?', (f,))


class TestAOwnershipAndBEligibility(Base):
    def test_expected_categories_matrix(self):
        i = self.ids
        e = fc.expected_categories(i['nolab']['sid'])
        self.assertEqual((e['main'], e['lab'], e['second']), (True, False, []))
        e = fc.expected_categories(i['labonly']['sid'])
        self.assertEqual((e['main'], e['lab'], e['second']), (True, True, []))
        e = fc.expected_categories(i['sec']['sid'])
        self.assertEqual((e['main'], e['lab'], e['second']), (True, True, [i['a_ok']]))

    def test_unavailable_categories_are_refused(self):
        i, hod = self.ids, self.actor(self.ids['hod_a'])
        for cat, aid, code in [('lab', i['nolab']['sid'], 'no_lab'),
                               ('second', i['a_rej'], 'assignment_not_confirmed'),
                               ('second', i['a_unres'], 'assignment_not_confirmed')]:
            with self.assertRaises(fc.ScopeError) as cm:
                cf.create_form(hod, cat, aid, 't', cf.default_questions(cat), deadline_at=FUTURE)
            self.assertEqual(cm.exception.code, code, (cat, aid))
        with self.assertRaises(fc.ScopeError):       # second needs lab-available session
            fc.resolve_scope('second', 999999)

    def test_ownership(self):
        i = self.ids
        sc_m = fc.resolve_scope('main', i['sec']['sid'])
        sc_l = fc.resolve_scope('lab', i['sec']['sid'])
        sc_s = fc.resolve_scope('second', i['a_ok'])
        self.assertEqual(sc_m['faculty_user_id'], i['sec']['tid'])
        self.assertEqual(sc_l['faculty_user_id'], i['sec']['tid'])
        self.assertEqual(sc_s['faculty_user_id'], i['second']['id'])
        self.assertIsNone(sc_l['lab_faculty_assignment_id'])      # no separate Lab Faculty
        fm = self.make(i['hod_a'], 'main', i['sec']['sid'], publish=False)
        fl = self.make(i['hod_a'], 'lab', i['sec']['sid'], publish=False)
        fs = self.make(i['hod_a'], 'second', i['a_ok'], publish=False)
        try:
            main_t = {f['id'] for f in services.teacher_unpublished_forms(i['sec']['tid'])} \
                | set()
            # drafts are excluded from teacher views; publish then compare
            for f in (fm, fl, fs):
                cf.publish(self.actor(i['hod_a']), f)
            main_t = {f['id'] for f in services.teacher_unpublished_forms(i['sec']['tid'])}
            sec_t = {f['id'] for f in services.teacher_unpublished_forms(i['second']['id'])}
            self.assertTrue({fm, fl} <= main_t and fs not in main_t)
            self.assertEqual(sec_t & {fm, fl, fs}, {fs})
            rows = scoring.faculty_scores_for_branches([i['bx']], form_id=fs, min_responses=0)
            self.assertTrue(all(r['teacher_user_id'] == i['second']['id'] for r in rows))
            for f in (fm, fl):
                rows = scoring.faculty_scores_for_branches([i['bx']], form_id=f, min_responses=0)
                self.assertTrue(all(r['teacher_user_id'] == i['sec']['tid'] for r in rows))
        finally:
            self.cleanup_forms(fm, fl, fs)


class TestCSubmissionAndDAnonymity(Base):
    def test_independent_submission_duplicate_and_anonymity(self):
        i = self.ids
        stu = i['students'][0]
        fids = [self.make(i['hod_a'], c, a) for c, a in
                [('main', i['sec']['sid']), ('lab', i['sec']['sid']), ('second', i['a_ok'])]]
        try:
            for f in fids:                                   # each category works independently
                ok, msg = services.submit_feedback(stu, f, self.answers(f))
                self.assertTrue(ok, msg)
            for f in fids:                                   # duplicates prevented
                ok, _ = services.submit_feedback(stu, f, self.answers(f))
                self.assertFalse(ok)
                self.assertEqual(db.query('SELECT COUNT(*) c FROM submissions WHERE form_id=?',
                                          (f,), one=True)['c'], 1)
            # --- anonymity
            cols = {r[1] for r in db.query('PRAGMA table_info(feedback_responses)')}
            self.assertFalse(cols & {'student_user_id', 'user_id', 'roll', 'submission_id', 'email'})
            vcols = {r[1] for r in db.query('PRAGMA table_info(anonymous_answers)')}
            self.assertFalse(vcols & {'student_user_id', 'user_id', 'roll', 'submission_id'})
            self.assertEqual(db.query(
                'SELECT COUNT(*) c FROM answers WHERE submission_id IN '
                '(SELECT id FROM submissions WHERE form_id IN (%s))' % ','.join('?' * 3),
                tuple(fids), one=True)['c'], 0)               # 5B answers live ONLY in feedback_responses
            for f in fids:
                rs = db.query('SELECT * FROM anonymous_answers WHERE form_id=?', (f,))
                self.assertTrue(rs)
                self.assertTrue(all(len(str(r['submitted_at'])) == 10 for r in rs))  # day only
            # completion tracking is separate and holds no answers
            sub_cols = {r[1] for r in db.query('PRAGMA table_info(submissions)')}
            self.assertFalse(sub_cols & {'question_id', 'option_id', 'text_value'})
            self.assertEqual(db.query('SELECT COUNT(*) c FROM submissions WHERE student_user_id=? '
                                      'AND form_id IN (%s)' % ','.join('?' * 3),
                                      (stu, *fids), one=True)['c'], 3)
        finally:
            self.cleanup_forms(*fids)

    def test_teacher_results_page_does_not_identify_respondents(self):
        i = self.ids
        stu = i['students'][1]
        f = self.make(i['hod_a'], 'main', i['sec']['sid'])
        try:
            self.assertTrue(services.submit_feedback(stu, f, self.answers(f))[0])
            db.execute("INSERT OR REPLACE INTO result_publications(form_id, session_id, is_published,"
                       " published_at) VALUES(?,?,1,'2026-01-01')", (f, i['sec']['sid']))
            urow = db.query('SELECT name, roll, username FROM users WHERE id=?', (stu,), one=True)
            tuser = self.actor(i['sec']['tid'])
            db.execute('UPDATE users SET must_change_password=0, first_login_complete=1 WHERE id=?',
                       (tuser['id'],))
            # HTTP requests must not share the test's app context (flask.g would cache the
            # anonymous user across requests), so step out of it for the client calls.
            self.ctx.pop()
            try:
                c = teacher_client(self.app, tuser['emp_code'])
                r = c.get(f'/teacher/results/{f}')
                self.assertEqual(r.status_code, 200, r.headers.get('Location'))
                body = r.get_data(as_text=True)
                self.assertNotIn(urow['roll'], body)
                if urow['name']:
                    self.assertNotIn(urow['name'], body)
                # the Second Faculty cannot open the Main Faculty's results
                c2 = teacher_client(self.app, i['second']['emp_code'])
                self.assertEqual(c2.get(f'/teacher/results/{f}').status_code, 403)
            finally:
                self.ctx.push()
        finally:
            db.execute('DELETE FROM result_publications WHERE form_id=?', (f,))
            self.cleanup_forms(f)

    def test_ineligible_snapshot_draft_closed_expired(self):
        i = self.ids
        f = self.make(i['hod_a'], 'main', i['sec']['sid'])
        try:
            # outsider (never in the session) cannot submit
            ok, _ = services.submit_feedback(i['outsider'], f, self.answers(f))
            self.assertFalse(ok)
            # a student added to the session AFTER publishing is not in the frozen snapshot
            late = i['outsider']
            db.execute('INSERT INTO session_students(session_id, student_user_id) VALUES(?,?)',
                       (i['sec']['sid'], late))
            try:
                self.assertFalse(fc.student_in_snapshot(late, f))
                self.assertFalse(services.submit_feedback(late, f, self.answers(f))[0])
                self.assertNotIn(f, [x['id'] for x in services.student_forms(late)])
            finally:
                db.execute('DELETE FROM session_students WHERE session_id=? AND student_user_id=?',
                           (i['sec']['sid'], late))
            stu = i['students'][2]
            # expired deadline
            db.execute('UPDATE feedback_forms SET deadline_at=? WHERE id=?', (PAST, f))
            self.assertFalse(services.submit_feedback(stu, f, self.answers(f))[0])
            self.assertEqual(db.query('SELECT status FROM feedback_forms WHERE id=?', (f,),
                                      one=True)['status'], 'closed')
            # closed
            self.assertFalse(services.submit_feedback(stu, f, self.answers(f))[0])
        finally:
            self.cleanup_forms(f)
        # draft
        d = self.make(i['hod_a'], 'lab', i['sec']['sid'], publish=False)
        try:
            self.assertFalse(services.submit_feedback(i['students'][2], d, self.answers(d))[0])
        finally:
            self.cleanup_forms(d)

    def test_lab_made_unavailable_after_publish_blocks_submission(self):
        i = self.ids
        f = self.make(i['hod_a'], 'lab', i['labonly']['sid'])
        stu = db.query('SELECT student_user_id s FROM session_students WHERE session_id=? LIMIT 1',
                       (i['labonly']['sid'],), one=True)['s']
        try:
            db.execute("UPDATE teacher_subject_settings SET lab_status='none', lab_available=0 "
                       "WHERE session_id=?", (i['labonly']['sid'],))
            self.assertFalse(services.submit_feedback(stu, f, self.answers(f))[0])
        finally:
            db.execute("UPDATE teacher_subject_settings SET lab_status='available', lab_available=1 "
                       "WHERE session_id=?", (i['labonly']['sid'],))
            self.cleanup_forms(f)


class TestEPublishingSecurity(Base):
    def test_snapshot_matches_eligibility_and_double_publish(self):
        i = self.ids
        f = self.make(i['hod_a'], 'main', i['sec']['sid'], publish=False)
        try:
            n = cf.publish(self.actor(i['hod_a']), f)
            sc = fc.resolve_scope('main', i['sec']['sid'])
            frozen = [r['student_user_id'] for r in db.query(
                'SELECT student_user_id FROM form_eligible_students WHERE form_id=? ORDER BY 1', (f,))]
            self.assertEqual(n, fc.eligible_count(sc))
            self.assertEqual(frozen, sorted(s['id'] for s in fc.eligible_students(sc)))
            self.assertEqual(fc.duplicate_eligibility_records(f), [])
            with self.assertRaises(fc.ScopeError):                    # already open
                cf.publish(self.actor(i['hod_a']), f)
        finally:
            self.cleanup_forms(f)

    def test_conflicts_prevented(self):
        i = self.ids
        hod = self.actor(i['hod_a'])
        f = self.make(i['hod_a'], 'lab', i['sec']['sid'], publish=False)
        try:
            with self.assertRaises(fc.ScopeError) as cm:
                cf.create_form(hod, 'lab', i['sec']['sid'], 'dup', cf.default_questions('lab'),
                               deadline_at=FUTURE)
            self.assertEqual(cm.exception.code, 'duplicate_form')
            # force a second draft behind the lifecycle's back: only one may be published
            dup = services.save_form(cf._payload(fc.resolve_scope('lab', i['sec']['sid']), 'dup',
                                                 '', FUTURE), cf.default_questions('lab'),
                                     hod['id'], session_ids=[i['sec']['sid']])
            for fid in (f, dup):             # two live forms for one assignment: neither publishes
                with self.assertRaises(fc.ScopeError) as cm:
                    cf.publish(hod, fid)
                self.assertEqual(cm.exception.code, 'not_publishable')
            self.cleanup_forms(dup)
            cf.publish(hod, f)               # once the conflict is gone it publishes
        finally:
            self.cleanup_forms(f)

    def test_unpublishable_cases(self):
        i = self.ids
        hod = self.actor(i['hod_a'])
        past = cf.create_form(hod, 'main', i['sec']['sid'], 't', cf.default_questions('main'),
                              deadline_at=PAST)
        try:
            with self.assertRaises(fc.ScopeError):
                cf.publish(hod, past)                       # expired deadline
        finally:
            self.cleanup_forms(past)
        # a session with zero eligible students cannot be published
        t = i['sec']
        sid = db.execute_returning_id(
            "INSERT INTO class_sessions(timetable_id, subject_code, subject_name, teacher_user_id,"
            " period_no) VALUES(?,?,?,?,1)", (t['ttid'], 'EMPTY1', 'Empty subject', t['tid']))
        f = cf.create_form(hod, 'main', sid, 't', cf.default_questions('main'), deadline_at=FUTURE)
        try:
            with self.assertRaises(fc.ScopeError) as cm:
                cf.publish(hod, f)
            self.assertIn(cm.exception.code, ('not_publishable', 'no_eligible_students'))
        finally:
            self.cleanup_forms(f)

    def test_authorization(self):
        i = self.ids
        other_hod, teacher, student = (self.actor(i['hod_b']), self.actor(i['sec']['tid']),
                                       self.actor(i['students'][0]))
        for who in (other_hod, teacher, student):
            with self.assertRaises(fc.ScopeError) as cm:
                cf.create_form(who, 'main', i['sec']['sid'], 't', cf.default_questions('main'),
                               deadline_at=FUTURE)
            self.assertEqual(cm.exception.status, 403)
        f = self.make(i['hod_a'], 'main', i['sec']['sid'], publish=False)
        try:
            for who in (other_hod, teacher, student):
                with self.assertRaises(fc.ScopeError) as cm:
                    cf.publish(who, f)
                self.assertEqual(cm.exception.status, 403)
            cf.publish(self.actor(i['hod_a']), f)
            with self.assertRaises(fc.ScopeError) as cm:          # close is scoped too
                cf.close(other_hod, f)
            self.assertEqual(cm.exception.status, 403)
        finally:
            self.cleanup_forms(f)


class TestFRoutesSecurity(Base):
    """Route-level checks (HTTP): the legacy HOD status route must honour scope and snapshots."""

    def _client(self, path, ident, pw, key='emp_code'):
        c = self.app.test_client()
        r = login(c, path, {key: ident, 'password': pw})
        self.assertEqual(r.status_code, 302)
        return c

    def _post_status(self, c, fid, status):
        with c.session_transaction() as sess:
            tok = sess['token']
        return c.post(f'/hod/forms/{fid}/status', json={'status': status},
                      headers={'X-CSRF-Token': tok, 'X-Requested-With': 'XMLHttpRequest'})

    def test_hod_routes_scope_and_role_gating(self):
        i = self.ids
        f = self.make(i['hod_a'], 'lab', i['sec']['sid'], publish=False)
        self.ctx.pop()
        try:
            hod_b = self._client('/hod/login', 'HODB', 'x12345678')
            self.assertEqual(self._post_status(hod_b, f, 'open').status_code, 403)   # other dept
            self.assertEqual(self._post_status(hod_b, f, 'closed').status_code, 403)
            hod_a = self._client('/hod/login', 'HODA', 'x12345678')
            r = self._post_status(hod_a, f, 'open')                                  # own dept
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            with self.app.app_context():
                frozen = db.query('SELECT COUNT(*) c FROM form_eligible_students WHERE form_id=?',
                                  (f,), one=True)['c']
                sc = fc.resolve_scope('lab', i['sec']['sid'])
                self.assertEqual(frozen, fc.eligible_count(sc))                       # snapshot made
            # teacher / student / anonymous cannot use HOD routes
            t = teacher_client(self.app, i['sec']['emp'])
            self.assertIn(t.get(f'/hod/forms').status_code, (302, 403))
            self.assertIn(t.post(f'/hod/forms/{f}/status', json={'status': 'closed'}).status_code,
                          (302, 400, 403))
            anon = self.app.test_client()
            self.assertIn(anon.get('/hod/forms').status_code, (302, 403))
            # missing CSRF token on a state-changing request is rejected
            self.assertEqual(hod_a.post(f'/hod/forms/{f}/status', json={'status': 'closed'}
                                        ).status_code, 400)
        finally:
            self.ctx.push()
            self.cleanup_forms(f)


if __name__ == '__main__':
    unittest.main(verbosity=2)
