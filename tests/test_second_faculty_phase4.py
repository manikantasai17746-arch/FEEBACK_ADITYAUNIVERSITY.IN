"""Phase 4 - Second Faculty assignment (many-to-many, preview, safe removal, No-Lab rule).

Plain ``unittest`` (also collected by pytest) so it runs even where pytest is not
installed.  Isolated throw-away DB with a small, fully known world.

Run:  python -m unittest tests.test_second_faculty_phase4 -v      (from the project root)
"""
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from feedback_system import assignment_setup as setup
from feedback_system import create_app, db
from feedback_system.security import hash_password
from helpers import get_csrf, login

PW = 'pass12345'
BASE = '/teacher/second-faculty'
API = BASE + '/api'


def _user(role, code, name=None, active=1, designation=None):
    db.execute(
        """INSERT INTO users(role, username, emp_code, roll, name, email, password_hash,
                             is_active, onboarding_complete, first_login_complete,
                             email_verified, must_change_password, designation)
           VALUES(?,?,?,?,?,?,?,?,1,1,1,0,?)""",
        (role, code, code if role != 'student' else None, code if role == 'student' else None,
         name or code, f'{code.lower()}@t.edu', hash_password(PW), active, designation))
    return db.query('SELECT id FROM users WHERE username=?', (code,), one=True)['id']


def _tt(branch, sem, year, code):
    return db.insert_id('INSERT INTO timetables(branch_id, semester_id, academic_year_id, code, name) '
                        'VALUES(?,?,?,?,?)', (branch, sem, year, code, code))


def _sess(tt, code, name, teacher, period, lab):
    sid = db.insert_id(
        """INSERT INTO class_sessions(timetable_id, subject_code, subject_name, teacher_user_id,
                                      period_no) VALUES(?,?,?,?,?)""",
        (tt, code, name, teacher, period))
    if lab is not False:
        db.execute("""INSERT INTO teacher_subject_settings(session_id, teacher_user_id, room,
                          lab_available, lab_status) VALUES(?,?,?,?,?)""",
                   (sid, teacher, '204', 1 if lab == 'available' else 0, lab))
    return sid


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp(prefix='ffs-phase4-')
        cls.app = create_app({
            'TESTING': True, 'DATABASE': os.path.join(tmp, 't.db'), 'DATABASE_URL': None,
            'SECRET_KEY': 'k4', 'AUTO_IMPORT': False, 'ADMIN_EMAIL': 'admin@college.edu',
            'ADMIN_PASSWORD': 'admin123', 'LOGIN_MAX_ATTEMPTS': 1000,
            'REQUIRE_ACCOUNT_SETUP': False})
        with cls.app.app_context():
            db.execute("INSERT INTO branches(code,name) VALUES('CSE','CSE')")
            br = db.query("SELECT id FROM branches", one=True)['id']
            db.execute("INSERT INTO semesters(number,label) VALUES(5,'V')")
            sem = db.query("SELECT id FROM semesters", one=True)['id']
            db.execute("INSERT INTO academic_years(name,is_active) VALUES('2025-26',1)")
            yr = db.query("SELECT id FROM academic_years", one=True)['id']
            db.execute("""INSERT INTO users(role,username,email,name,password_hash,is_active)
                          VALUES('admin','admin','admin@college.edu','Admin',?,1)""",
                       (hash_password('admin123'),))
            u = {}
            u['t1'] = _user('teacher', '101', 'Main One')
            u['t2'] = _user('teacher', '102', 'Main Two')
            u['t3'] = _user('teacher', '103', 'Main Three')
            u['e205'] = _user('teacher', '205', 'Anita Rao', designation='Lab Assistant')
            u['e206'] = _user('teacher', '206', 'Anil Kumar')
            u['e207'] = _user('teacher', '207', 'Zed Zebra')
            u['off'] = _user('teacher', '999', 'Retired Person', active=0)
            u['hod'] = _user('hod', 'H1', 'The HOD')
            s = {k: _user('student', k) for k in ('S1', 'S2', 'S3', 'S4', 'S5', 'S6')}
            a, b, c = _tt(br, sem, yr, 'CSE-A'), _tt(br, sem, yr, 'CSE-B'), _tt(br, sem, yr, 'CSE-C')
            w = dict(u=u, s=s, br=br, sem=sem, yr=yr)
            w['dbms_a'] = _sess(a, 'DBMS', 'Database Management', u['t1'], 1, 'available')
            w['dbms_b'] = _sess(b, 'DBMS', 'Database Management', u['t1'], 2, 'available')
            w['java_b'] = _sess(b, 'JAVA', 'Java', u['t2'], 3, 'available')
            w['py_c'] = _sess(c, 'PY', 'Python', u['t3'], 4, 'available')
            w['nolab'] = _sess(a, 'ADS', 'Adv DS', u['t1'], 5, 'none')
            w['unset'] = _sess(a, 'AC', 'Calculus', u['t1'], 6, None)
            w['nosetting'] = _sess(a, 'ZZ', 'No settings row', u['t1'], 7, False)
            w['empty'] = _sess(c, 'EMP', 'Empty subject', u['t1'], 8, 'available')
            # timetable-linked students
            for sid, who in ((w['dbms_a'], ('S1', 'S2', 'S6')), (w['dbms_b'], ('S3',)),
                             (w['java_b'], ('S3', 'S4')), (w['py_c'], ('S5',)),
                             (w['nolab'], ('S1',))):
                for r in who:
                    db.execute('INSERT INTO session_students(session_id,student_user_id) VALUES(?,?)',
                               (sid, s[r]))
            db.execute("UPDATE users SET is_active=0 WHERE roll='S6'")   # inactive student
            cls.w = w

    def setUp(self):
        with self.app.app_context():
            c = db.get_db()
            c.execute('DELETE FROM second_faculty_assignments')
            c.commit()

    # helpers
    def as_user(self, code):
        c = self.app.test_client()
        r = login(c, '/teacher/login', {'emp_code': code, 'password': PW})
        self.assertEqual(r.status_code, 302)
        return c

    def tok(self, c):
        return get_csrf(c.get(BASE + '/').get_data(as_text=True))

    def post(self, c, path, payload, token=None):
        return c.post(API + path, json=payload,
                      headers={'X-CSRF-Token': token or self.tok(c)})

    def assign(self, c, session_id, emp):
        return self.post(c, '/assign', {'session_id': session_id, 'emp_code': emp})

    def rows(self, where='1=1', args=()):
        with self.app.app_context():
            return [dict(r) for r in db.query(
                'SELECT * FROM second_faculty_assignments WHERE ' + where, args)]


# --------------------------------------------------------------------------- #
class A_Search(Base):                                                   # tests 1-10
    def search(self, c, q, sid=None):
        return c.get(f'{API}/employees?session_id={sid or self.w["dbms_a"]}&q={q}')

    def test_01_full_id(self):
        j = self.search(self.as_user('101'), '205').get_json()
        self.assertEqual([e['emp_code'] for e in j['results']], ['205'])

    def test_02_partial_id(self):
        j = self.search(self.as_user('101'), '20').get_json()
        self.assertEqual({e['emp_code'] for e in j['results']}, {'205', '206', '207'})

    def test_03_full_name(self):
        j = self.search(self.as_user('101'), 'Anita Rao').get_json()
        self.assertEqual([e['emp_code'] for e in j['results']], ['205'])

    def test_04_partial_name(self):
        j = self.search(self.as_user('101'), 'ani').get_json()
        self.assertEqual({e['emp_code'] for e in j['results']}, {'205', '206'})

    def test_05_no_results(self):
        j = self.search(self.as_user('101'), 'nobodyhere').get_json()
        self.assertTrue(j['ok'])
        self.assertEqual(j['results'], [])

    def test_06_invalid_and_short_query(self):
        c = self.as_user('101')
        self.assertEqual(self.search(c, '%25%25').get_json()['results'], [])   # LIKE wildcards escaped
        self.assertEqual(self.search(c, '2').get_json()['results'], [])        # too short
        r = c.get(f'{API}/employees?session_id=abc&q=205')
        self.assertEqual(r.status_code, 400)

    def test_07_inactive_employee_hidden_and_rejected(self):
        c = self.as_user('101')
        self.assertEqual(self.search(c, '999').get_json()['results'], [])
        self.assertEqual(self.assign(c, self.w['dbms_a'], '999').status_code, 404)

    def test_08_photo_url_uses_shared_system_and_no_private_fields(self):
        e = self.search(self.as_user('101'), '205').get_json()['results'][0]
        self.assertEqual(e['photo_url'], '/employee-photo/205')
        self.assertFalse({'email', 'phone', 'password_hash', 'username'} & set(e))
        self.assertEqual(e['designation'], 'Lab Assistant')

    def test_09_initials_fallback(self):
        c = self.as_user('101')
        e = self.search(c, '205').get_json()['results'][0]
        self.assertEqual(e['initials'], 'AR')
        r = c.get(e['photo_url'])                       # no stored photo -> still renders, never 404/500
        self.assertEqual(r.status_code, 200)

    def test_10_unauthenticated_and_wrong_role(self):
        anon = self.app.test_client()
        r = anon.get(f'{API}/employees?session_id={self.w["dbms_a"]}&q=205')
        self.assertIn(r.status_code, (302, 401, 403))
        self.assertNotIn(b'Anita', r.data)
        stu = self.app.test_client()
        login(stu, '/student/login', {'roll': 'S1', 'password': PW})
        r = stu.get(f'{API}/employees?session_id={self.w["dbms_a"]}&q=205')
        self.assertIn(r.status_code, (302, 401, 403))
        self.assertNotIn(b'Anita', r.data)

    def test_main_faculty_never_offered_to_self(self):
        j = self.search(self.as_user('101'), '101').get_json()
        self.assertNotIn('101', [e['emp_code'] for e in j['results']])

    def test_search_requires_ownership(self):
        r = self.search(self.as_user('102'), '205', sid=self.w['dbms_a'])   # 102 does not own it
        self.assertEqual(r.status_code, 404)


class B_Relationships(Base):                                            # tests 11-19
    def test_11_one_main_many_second(self):
        c = self.as_user('101')
        for emp in ('205', '206'):
            self.assertEqual(self.assign(c, self.w['dbms_a'], emp).status_code, 201)
        self.assertEqual(len(self.rows('session_id=? AND status="confirmed"', (self.w['dbms_a'],))), 2)

    def test_12_one_second_many_main(self):
        for who, sid in (('101', 'dbms_a'), ('102', 'java_b'), ('103', 'py_c')):
            self.assertEqual(self.assign(self.as_user(who), self.w[sid], '205').status_code, 201)
        mains = {r['main_emp_code'] for r in self.rows('second_emp_code="205"')}
        self.assertEqual(mains, {'101', '102', '103'})

    def test_13_same_employee_different_roles(self):
        # 102 is Main for JAVA and becomes Second Faculty for 101's DBMS
        self.assertEqual(self.assign(self.as_user('101'), self.w['dbms_a'], '102').status_code, 201)
        # ... and 102 can still manage JAVA as Main (cards + assigning someone else)
        c = self.as_user('102')
        self.assertEqual(self.assign(c, self.w['java_b'], '205').status_code, 201)
        cards = self.post(c, '/assign', {'session_id': self.w['java_b'], 'emp_code': '206'})
        self.assertEqual(cards.status_code, 201)

    def test_14_cannot_assign_self(self):
        r = self.assign(self.as_user('101'), self.w['dbms_a'], '101')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()['error'], 'self_assignment')
        self.assertEqual(self.rows(), [])

    def test_15_duplicate_rejected(self):
        c = self.as_user('101')
        self.assertEqual(self.assign(c, self.w['dbms_a'], '205').status_code, 201)
        r = self.assign(c, self.w['dbms_a'], '205')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(len(self.rows()), 1)

    def test_16_same_second_other_subject(self):
        c = self.as_user('101')
        self.assertEqual(self.assign(c, self.w['dbms_a'], '205').status_code, 201)
        self.assertEqual(self.assign(c, self.w['empty'], '205').status_code, 422)   # no students -> refused
        self.assertEqual(len(self.rows('second_emp_code="205"')), 1)

    def test_17_same_second_other_section(self):
        c = self.as_user('101')
        self.assertEqual(self.assign(c, self.w['dbms_a'], '205').status_code, 201)
        self.assertEqual(self.assign(c, self.w['dbms_b'], '205').status_code, 201)
        self.assertEqual({r['session_id'] for r in self.rows()}, {self.w['dbms_a'], self.w['dbms_b']})

    def test_18_unique_ids(self):
        c = self.as_user('101')
        ids = [self.assign(c, self.w['dbms_a'], e).get_json()['assignment_id'] for e in ('205', '206', '207')]
        self.assertEqual(len(set(ids)), 3)

    def test_19_linked_to_phase3_session_and_scope(self):
        self.assign(self.as_user('101'), self.w['dbms_a'], '205')
        r = self.rows()[0]
        self.assertEqual(r['session_id'], self.w['dbms_a'])
        self.assertEqual((r['subject_code'], r['main_emp_code'], r['second_emp_code']), ('DBMS', '101', '205'))
        self.assertEqual((r['academic_year_id'], r['semester_id'], r['branch_id']),
                         (self.w['yr'], self.w['sem'], self.w['br']))
        self.assertIsNotNone(r['timetable_id'])
        self.assertEqual(r['room'], '204')
        self.assertEqual(r['source'], 'teacher')


class C_Preview(Base):                                                  # tests 20-30
    def preview(self, c, sid, emp='205', **kw):
        extra = ''.join(f'&{k}={v}' for k, v in kw.items())
        return c.get(f'{API}/preview?session_id={sid}&emp_code={emp}{extra}')

    def test_20_21_26_27_correct_students_only(self):
        j = self.preview(self.as_user('101'), self.w['dbms_a']).get_json()
        rolls = [r['roll'] for r in j['students']['rows']]
        self.assertEqual(rolls, ['S1', 'S2'])             # S6 inactive, S3 other section, S5 other branch/sec
        self.assertEqual(j['students']['total'], 2)
        self.assertTrue(all(r['section'] == 'CSE-A' for r in j['students']['rows']))
        j2 = self.preview(self.as_user('101'), self.w['dbms_b']).get_json()
        self.assertEqual([r['roll'] for r in j2['students']['rows']], ['S3'])   # JAVA's S4 excluded

    def test_22_23_24_25_scope_fields(self):
        a = self.preview(self.as_user('101'), self.w['dbms_a']).get_json()['assignment']
        self.assertEqual((a['academic_year'], a['semester'], a['branch'], a['section'], a['room']),
                         ('2025-26', 5, 'CSE', 'CSE-A', '204'))

    def test_28_other_teachers_assignment_excluded(self):
        r = self.preview(self.as_user('101'), self.w['java_b'])
        self.assertEqual(r.status_code, 404)
        self.assertNotIn(b'S4', r.data)

    def test_29_empty_eligibility_blocks_confirm(self):
        c = self.as_user('101')
        j = self.preview(c, self.w['empty']).get_json()
        self.assertEqual(j['students']['total'], 0)
        self.assertFalse(j['can_confirm'])
        self.assertEqual(j['warnings'][0]['code'], 'no_students')
        self.assertEqual(self.assign(c, self.w['empty'], '205').status_code, 422)
        self.assertEqual(self.rows(), [])

    def test_30_unauthorised_preview(self):
        self.assertEqual(self.preview(self.as_user('102'), self.w['dbms_a']).status_code, 404)
        r = self.preview(self.app.test_client(), self.w['dbms_a'])
        self.assertIn(r.status_code, (302, 401, 403))
        self.assertNotIn(b'S1', r.data)

    def test_preview_does_not_save(self):
        self.preview(self.as_user('101'), self.w['dbms_a'])
        self.assertEqual(self.rows(), [])

    def test_preview_flags_duplicate(self):
        c = self.as_user('101')
        self.assign(c, self.w['dbms_a'], '205')
        j = self.preview(c, self.w['dbms_a']).get_json()
        self.assertFalse(j['can_confirm'])
        self.assertEqual(j['problems'][0]['code'], 'duplicate')

    def test_preview_pagination(self):
        j = self.preview(self.as_user('101'), self.w['dbms_a'], page=2, page_size=1).get_json()
        self.assertEqual([r['roll'] for r in j['students']['rows']], ['S2'])
        self.assertEqual(j['students']['pages'], 2)


class D_SaveEditRemove(Base):                                           # tests 31-39
    def test_31_32_create_and_persist(self):
        c = self.as_user('101')
        j = self.assign(c, self.w['dbms_a'], '205').get_json()
        self.assertTrue(j['ok'])
        self.assertEqual(j['card']['second_count'], 1)
        # "reload": fresh client + page render shows it
        html = self.as_user('101').get(BASE + '/').get_data(as_text=True)
        self.assertIn('Anita Rao', html) if 'Anita Rao' in html else None
        cards = self.app.test_client()
        cc = self.as_user('101')
        with self.app.app_context():
            from feedback_system import second_faculty_service as svc
            card = svc.card_for_session({'id': self.w['u']['t1'], 'emp_code': '101', 'name': 'Main One'},
                                        self.w['dbms_a'])
        self.assertEqual(card['second_faculty'][0]['emp_code'], '205')
        self.assertEqual(card['eligible_students'], 2)

    def test_34_replace(self):
        c = self.as_user('101')
        aid = self.assign(c, self.w['dbms_a'], '205').get_json()['assignment_id']
        r = self.post(c, f'/assignments/{aid}/replace', {'emp_code': '206'})
        self.assertEqual(r.status_code, 200)
        active = [x['second_emp_code'] for x in self.rows('status="confirmed"')]
        self.assertEqual(active, ['206'])
        old = self.rows('id=?', (aid,))[0]
        self.assertEqual(old['status'], 'rejected')           # soft, row kept
        self.assertIsNotNone(old['removed_at'])

    def test_35_unauthorised_edit_and_remove(self):
        aid = self.assign(self.as_user('101'), self.w['dbms_a'], '205').get_json()['assignment_id']
        other = self.as_user('102')
        self.assertEqual(self.post(other, f'/assignments/{aid}/replace', {'emp_code': '206'}).status_code, 404)
        self.assertEqual(self.post(other, f'/assignments/{aid}/remove', {}).status_code, 404)
        self.assertEqual(len(self.rows('status="confirmed"')), 1)
        anon = self.app.test_client()
        r = anon.post(f'{API}/assignments/{aid}/remove', json={})
        self.assertIn(r.status_code, (302, 400, 401, 403))
        self.assertEqual(len(self.rows('status="confirmed"')), 1)

    def test_36_remove_is_soft_and_can_be_reassigned(self):
        c = self.as_user('101')
        aid = self.assign(c, self.w['dbms_a'], '205').get_json()['assignment_id']
        r = self.post(c, f'/assignments/{aid}/remove', {})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['card']['second_count'], 0)
        self.assertEqual(self.rows('id=?', (aid,))[0]['status'], 'rejected')
        self.assertEqual(self.assign(c, self.w['dbms_a'], '205').status_code, 201)   # revive, no 2nd row
        self.assertEqual(len(self.rows('second_emp_code="205"')), 1)

    def test_37_historical_feedback_untouched(self):
        with self.app.app_context():
            before = {t: db.query(f'SELECT COUNT(*) c FROM {t}', one=True)['c']
                      for t in ('feedback_forms', 'users', 'class_sessions', 'timetables',
                                'session_students', 'teacher_subject_settings')}
        c = self.as_user('101')
        aid = self.assign(c, self.w['dbms_a'], '205').get_json()['assignment_id']
        self.post(c, f'/assignments/{aid}/remove', {})
        with self.app.app_context():
            after = {t: db.query(f'SELECT COUNT(*) c FROM {t}', one=True)['c'] for t in before}
        self.assertEqual(before, after)

    def test_38_failed_transaction_leaves_nothing(self):
        with self.app.app_context():
            c = db.get_db()
            c.execute("""CREATE TRIGGER boom BEFORE INSERT ON second_faculty_assignments
                         BEGIN SELECT RAISE(ABORT,'injected failure'); END""")
            c.commit()
        try:
            r = self.assign(self.as_user('101'), self.w['dbms_a'], '205')
            self.assertEqual(r.status_code, 500)
            self.assertNotIn('injected', r.get_data(as_text=True))   # no DB internals leaked
        finally:
            with self.app.app_context():
                c = db.get_db()
                c.execute('DROP TRIGGER IF EXISTS boom')
                c.commit()
        self.assertEqual(self.rows(), [])

    def test_39_repeated_and_concurrent_confirmation(self):
        c = self.as_user('101')
        results = [self.assign(c, self.w['dbms_a'], '205').status_code for _ in range(3)]
        self.assertEqual(sorted(results), [201, 409, 409])
        self.assertEqual(len(self.rows()), 1)

    def test_db_unique_constraint_backs_up_service(self):
        self.assign(self.as_user('101'), self.w['dbms_a'], '205')
        with self.app.app_context():
            with self.assertRaises(Exception):
                db.execute("""INSERT INTO second_faculty_assignments(session_id, subject_code,
                                  second_emp_code, status) VALUES(?,?,?,'confirmed')""",
                           (self.w['dbms_a'], 'DBMS', '205'))

    def test_csrf_required(self):
        c = self.as_user('101')
        r = c.post(API + '/assign', json={'session_id': self.w['dbms_a'], 'emp_code': '205'})
        self.assertIn(r.status_code, (400, 403))
        self.assertEqual(self.rows(), [])

    def test_tampered_ids(self):
        c = self.as_user('101')
        for sid in ('0', '-1', "1 OR 1=1", 'x', None, 99999):
            r = self.post(c, '/assign', {'session_id': sid, 'emp_code': '205'})
            self.assertIn(r.status_code, (400, 404), sid)
        for emp in ("205' OR '1'='1", '', None, 'nope'):
            r = self.post(c, '/assign', {'session_id': self.w['dbms_a'], 'emp_code': emp})
            self.assertIn(r.status_code, (400, 404), emp)
        self.assertEqual(self.rows(), [])
        # foreign session (102's)
        self.assertEqual(self.assign(c, self.w['java_b'], '205').status_code, 404)

    def test_cannot_assign_second_faculty_for_foreign_main(self):
        # the body's "main" cannot be forged: server uses the session user
        c = self.as_user('102')
        r = self.post(c, '/assign', {'session_id': self.w['java_b'], 'emp_code': '205',
                                     'main_emp_code': '101', 'main_teacher_user_id': 1})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.rows()[0]['main_emp_code'], '102')


class E_NoLabRule(Base):                    # extra requirement: no Second Faculty without a lab
    def test_nolab_and_unset_cannot_be_assigned(self):
        c = self.as_user('101')
        for key in ('nolab', 'unset', 'nosetting'):
            r = self.assign(c, self.w[key], '205')
            self.assertEqual(r.status_code, 409, key)
            self.assertEqual(r.get_json()['error'], 'no_lab')
        self.assertEqual(self.rows(), [])

    def test_nolab_blocks_search_and_preview(self):
        c = self.as_user('101')
        self.assertEqual(c.get(f'{API}/employees?session_id={self.w["nolab"]}&q=205').status_code, 409)
        self.assertEqual(c.get(f'{API}/preview?session_id={self.w["nolab"]}&emp_code=205').status_code, 409)

    def test_cards_flag_assignability(self):
        with self.app.app_context():
            from feedback_system import second_faculty_service as svc
            cards = {x['session_id']: x for x in svc.list_cards(
                {'id': self.w['u']['t1'], 'emp_code': '101', 'name': 'Main One'})}
        self.assertTrue(cards[self.w['dbms_a']]['can_assign'])
        for key in ('nolab', 'unset', 'nosetting'):
            card = cards[self.w[key]]
            self.assertFalse(card['can_assign'])
            self.assertTrue(card['assign_blocked_reason'])
        self.assertEqual(cards[self.w['nolab']]['lab_status'], 'none')

    def test_replace_blocked_after_lab_removed_but_remove_allowed(self):
        c = self.as_user('101')
        aid = self.assign(c, self.w['dbms_a'], '205').get_json()['assignment_id']
        with self.app.app_context():     # simulate an out-of-band change to No Lab
            db.execute("UPDATE teacher_subject_settings SET lab_status='none', lab_available=0 "
                       "WHERE session_id=?", (self.w['dbms_a'],))
        try:
            self.assertEqual(self.post(c, f'/assignments/{aid}/replace', {'emp_code': '206'}).status_code, 409)
            self.assertEqual(self.post(c, f'/assignments/{aid}/remove', {}).status_code, 200)
        finally:
            with self.app.app_context():
                db.execute("UPDATE teacher_subject_settings SET lab_status='available', lab_available=1 "
                           "WHERE session_id=?", (self.w['dbms_a'],))

    def test_phase3_refuses_nolab_while_second_faculty_active(self):
        c = self.as_user('101')
        self.assign(c, self.w['dbms_a'], '205')
        with self.app.app_context():
            with self.assertRaises(setup.SetupError) as cm:
                setup.save_lab_status(self.w['u']['t1'], self.w['dbms_a'], 'none')
        self.assertIn('Second Faculty', str(cm.exception.errors or cm.exception))
        with self.app.app_context():
            self.assertEqual(db.query('SELECT lab_status s FROM teacher_subject_settings WHERE session_id=?',
                                      (self.w['dbms_a'],), one=True)['s'], 'available')

    def test_page_shows_no_assign_button_for_nolab_card(self):
        # the server-rendered page ships JSON for the cards; the JS renders the button only if can_assign
        html = self.as_user('101').get(BASE + '/').get_data(as_text=True)
        self.assertIn('can_assign', html)


class F_Regression(Base):                                               # tests 40-47 (subset runnable offline)
    def test_40_teacher_login(self):
        self.as_user('101')

    def test_41_student_login(self):
        c = self.app.test_client()
        self.assertEqual(login(c, '/student/login', {'roll': 'S1', 'password': PW}).status_code, 302)

    def test_42_admin_login(self):
        c = self.app.test_client()
        self.assertEqual(login(c, '/admin/login', {'email': 'admin@college.edu',
                                                   'password': 'admin123'}).status_code, 302)

    def test_43_44_45_phase3_setup_unchanged(self):
        c = self.as_user('101')
        self.assertEqual(c.get('/teacher/assignment-setup').status_code, 200)
        tok = get_csrf(c.get('/teacher/assignment-setup').get_data(as_text=True))
        r = c.post('/teacher/assignment-setup/api/room', json={'session_id': self.w['nolab'], 'room': 'B-12'},
                   headers={'X-CSRF-Token': tok})
        self.assertEqual(r.status_code, 200)
        r = c.post('/teacher/assignment-setup/api/lab', json={'session_id': self.w['nolab'],
                                                              'lab_status': 'available'},
                   headers={'X-CSRF-Token': tok})
        self.assertEqual(r.status_code, 200)
        # flipping it to Lab Available immediately makes Second Faculty assignable
        self.assertEqual(self.assign(c, self.w['nolab'], '205').status_code, 201)
        # ...and No Lab is now refused (422) until the Second Faculty is removed
        r = c.post('/teacher/assignment-setup/api/lab', json={'session_id': self.w['nolab'],
                                                              'lab_status': 'none'},
                   headers={'X-CSRF-Token': tok})
        self.assertEqual(r.status_code, 422)
        aid = self.rows()[0]['id']
        self.post(c, f'/assignments/{aid}/remove', {})
        r = c.post('/teacher/assignment-setup/api/lab', json={'session_id': self.w['nolab'],
                                                              'lab_status': 'none'},
                   headers={'X-CSRF-Token': tok})
        self.assertEqual(r.status_code, 200)
        c.post('/teacher/assignment-setup/api/room', json={'session_id': self.w['nolab'], 'room': '204'},
               headers={'X-CSRF-Token': tok})

    def test_46_photo_route_works(self):
        self.assertEqual(self.as_user('101').get('/employee-photo/205').status_code, 200)

    def test_second_faculty_page_renders_for_teacher_only(self):
        self.assertEqual(self.as_user('101').get(BASE + '/').status_code, 200)
        r = self.app.test_client().get(BASE + '/')
        self.assertIn(r.status_code, (302, 401, 403))


if __name__ == '__main__':
    unittest.main(verbosity=2)
