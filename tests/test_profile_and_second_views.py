"""Teacher dashboard role labels, Second Faculty "Assigned to me" tab, Profile page.

Run:  python -m pytest tests/test_profile_and_second_views.py -q
"""
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from feedback_system import create_app, db
from helpers import get_csrf, login
from test_second_faculty_phase4 import PW, _sess, _tt, _user


def _form(category, session_id, title, sfa_id=None):
    fid = db.insert_id(
        """INSERT INTO feedback_forms(title, status, category, session_id, subject_code,
               subject_name, second_faculty_assignment_id, created_by)
           SELECT ?, 'open', ?, cs.id, cs.subject_code, cs.subject_name, ?, NULL
           FROM class_sessions cs WHERE cs.id=?""", (title, category, sfa_id, session_id))
    db.execute('INSERT INTO form_sessions(form_id, session_id) VALUES(?,?)', (fid, session_id))
    return fid


class Views(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp(prefix='ffs-views-')
        cls.app = create_app({
            'TESTING': True, 'DATABASE': os.path.join(tmp, 't.db'), 'DATABASE_URL': None,
            'SECRET_KEY': 'kv', 'AUTO_IMPORT': False, 'ADMIN_EMAIL': 'admin@college.edu',
            'ADMIN_PASSWORD': 'admin123', 'LOGIN_MAX_ATTEMPTS': 1000,
            'REQUIRE_ACCOUNT_SETUP': False})
        with cls.app.app_context():
            db.execute("INSERT INTO branches(code,name) VALUES('CSE','Computer Science')")
            br = db.query('SELECT id FROM branches', one=True)['id']
            db.execute("INSERT INTO semesters(number,label) VALUES(5,'V')")
            sem = db.query('SELECT id FROM semesters', one=True)['id']
            db.execute("INSERT INTO academic_years(name,is_active) VALUES('2025-26',1)")
            yr = db.query('SELECT id FROM academic_years', one=True)['id']
            u = {'main': _user('teacher', '101', 'Main Mohan'),
                 'second': _user('teacher', '102', 'Second Sita'),
                 'other': _user('teacher', '103', 'Other Omar')}
            stu = _user('student', 'S1')
            t1 = _tt(br, sem, yr, 'T1-General')
            sess = _sess(t1, 'ADSA', 'Adv Data Structures', u['main'], 1, 'available')
            lab_only = _sess(t1, 'OOP', 'OOP through C++', u['main'], 2, 'available')
            db.execute('UPDATE teacher_subject_settings SET room=? WHERE session_id=?',
                       ('B-301', sess))
            db.execute('INSERT INTO session_students(session_id,student_user_id) VALUES(?,?)',
                       (sess, stu))
            sfa = db.insert_id(
                """INSERT INTO second_faculty_assignments(
                       academic_year_id, semester_id, branch_id, timetable_id, session_id,
                       subject_code, subject_name, main_emp_code, main_teacher_user_id,
                       second_emp_code, second_teacher_user_id, room, status, source)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'confirmed','teacher')""",
                (yr, sem, br, t1, sess, 'ADSA', 'Adv Data Structures', '101', u['main'],
                 '102', u['second'], 'LAB-7', ))
            cls.second_form = _form('second', sess, 'Phase 1', sfa)
            cls.lab_form = _form('lab', lab_only, 'Phase 1')
            cls.main_form = _form('main', sess, 'Phase 1')
            cls.u = u

    def as_user(self, code):
        c = self.app.test_client()
        self.assertEqual(login(c, '/teacher/login', {'emp_code': code, 'password': PW}).status_code, 302)
        return c

    # ---- dashboard role labels ----------------------------------------- #
    def test_second_faculty_row_shows_role_room_and_main_faculty(self):
        html = self.as_user('102').get('/teacher/').get_data(as_text=True)
        self.assertIn('Second Faculty', html)
        self.assertIn('Room: LAB-7', html)
        self.assertIn('Main faculty: Main Mohan (101)', html)
        self.assertNotIn('Main Faculty</span>', html)

    def test_main_and_lab_rows_are_labelled_differently(self):
        html = self.as_user('101').get('/teacher/').get_data(as_text=True)
        self.assertIn('role-main', html)
        self.assertIn('role-lab', html)
        self.assertIn('Lab Faculty', html)
        self.assertIn('Room: B-301', html)          # main row: Assignment Setup room
        self.assertNotIn('role-second', html)       # the Main teacher never sees a second row

    # ---- Assigned to me ------------------------------------------------- #
    def test_assigned_to_me_lists_main_teacher_class_and_room(self):
        html = self.as_user('102').get('/teacher/second-faculty/?tab=to-me').get_data(as_text=True)
        for needle in ('Main Mohan', 'Adv Data Structures', 'ADSA', 'T1-General', 'LAB-7',
                       'Assigned to me', 'Assigned by me'):
            self.assertIn(needle, html)
        self.assertNotIn('id="sf-root"', html)

    def test_assigned_to_me_is_private_to_the_second_faculty(self):
        html = self.as_user('103').get('/teacher/second-faculty/?tab=to-me').get_data(as_text=True)
        self.assertIn('No one has assigned you yet', html)
        self.assertNotIn('Main Mohan', html)

    def test_removed_assignment_disappears(self):
        with self.app.app_context():
            db.execute("UPDATE second_faculty_assignments SET status='rejected'")
        try:
            html = self.as_user('102').get('/teacher/second-faculty/?tab=to-me').get_data(as_text=True)
            self.assertIn('No one has assigned you yet', html)
        finally:
            with self.app.app_context():
                db.execute("UPDATE second_faculty_assignments SET status='confirmed'")

    def test_default_tab_still_has_assign_ui(self):
        html = self.as_user('101').get('/teacher/second-faculty/').get_data(as_text=True)
        self.assertIn('id="sf-root"', html)
        self.assertIn('second_faculty.js', html)

    # ---- Profile -------------------------------------------------------- #
    def test_nav_has_profile_instead_of_photo_and_password(self):
        html = self.as_user('101').get('/teacher/').get_data(as_text=True)
        self.assertIn('>Profile</a>', html)
        self.assertNotIn('>My Photo</a>', html)
        self.assertNotIn('>Password</a>', html)

    def test_profile_page_has_photo_and_password_forms(self):
        c = self.as_user('101')
        html = c.get('/profile').get_data(as_text=True)
        self.assertIn('Main Mohan', html)
        self.assertIn('id="photoForm"', html)
        self.assertIn('/photos/manage/101', html)
        self.assertIn('/change-password', html)

    def test_password_change_from_profile_returns_to_profile(self):
        c = self.as_user('103')
        tok = get_csrf(c.get('/profile').get_data(as_text=True))
        bad = c.post('/change-password', data={
            '_csrf': tok, 'from': 'profile', 'current_password': 'wrong',
            'new_password': 'newpass123', 'confirm_password': 'newpass123'})
        self.assertEqual(bad.status_code, 302)
        self.assertTrue(bad.headers['Location'].endswith('/profile'))
        ok = c.post('/change-password', data={
            '_csrf': tok, 'from': 'profile', 'current_password': PW,
            'new_password': 'newpass123', 'confirm_password': 'newpass123'})
        self.assertEqual(ok.status_code, 302)
        self.assertTrue(ok.headers['Location'].endswith('/profile'))
        # the old standalone page keeps working
        self.assertEqual(c.get('/change-password').status_code, 200)

    def test_profile_requires_employee_login(self):
        self.assertIn(self.app.test_client().get('/profile').status_code, (302, 401, 403))


if __name__ == '__main__':
    unittest.main()
