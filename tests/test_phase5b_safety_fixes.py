"""Phase 5B safety fixes - focused regression tests (unittest-style).

FIX 1  anonymous comments must not be listable in submission order;
FIX 2  the legacy HOD/Admin status route must not bypass the Phase 5 lifecycle.

    cd <project root>
    python -m unittest tests.test_phase5b_safety_fixes -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from feedback_system import db, services                                  # noqa: E402
from feedback_system import category_forms as cf                          # noqa: E402
from feedback_system import feedback_categories as fc                     # noqa: E402
from test_phase5b_verification import Base, FUTURE                        # noqa: E402
from helpers import login                                                 # noqa: E402

# Deliberately neither alphabetical nor reverse-alphabetical, so "sorted" is a
# non-trivial permutation of the submission order.
TEXTS = ['m comment', 'c comment', 'x comment', 'a comment', 'q comment', 'e comment',
         't comment', 'h comment']


class TestFix1CommentOrderCannotLinkStudents(Base):
    def _submit_with_comments(self, fid, students, texts):
        """Submit in the given order, then stamp admin-visible, strictly increasing
        per-student submission times (what the admin tracking pages show)."""
        base = datetime(2026, 1, 5, 9, 0, 0)
        text_q = next(q['id'] for q in services.form_questions(fid) if q['qtype'] == 'text')
        for n, (stu, txt) in enumerate(zip(students, texts)):
            ans = self.answers(fid)
            ans[f'q_{text_q}'] = txt
            ok, msg = services.submit_feedback(stu, fid, ans)
            self.assertTrue(ok, msg)
            db.execute('UPDATE submissions SET submitted_at=? WHERE form_id=? '
                       'AND student_user_id=?',
                       ((base + timedelta(minutes=n)).isoformat(timespec='seconds'), fid, stu))

    def _all(self, fid, per_page):
        out, page = [], 1
        while True:
            pg = services.suggestions_paged(fid, page=page, per_page=per_page)
            out.extend(pg['comments'])
            if page >= pg['pages']:
                return out
            page += 1

    def test_comment_order_is_not_submission_order_and_is_page_stable(self):
        i = self.ids
        students = i['students'][:len(TEXTS)]
        f1 = self.make(i['hod_a'], 'main', i['sec']['sid'])
        f2 = self.make(i['hod_a'], 'lab', i['sec']['sid'])
        try:
            self._submit_with_comments(f1, students, TEXTS)                 # one order
            self._submit_with_comments(f2, students, list(reversed(TEXTS)))  # the opposite

            shown1 = self._all(f1, per_page=100)
            shown2 = self._all(f2, per_page=100)
            self.assertEqual(sorted(shown1), sorted(TEXTS))                  # text preserved

            # What an admin can learn from the tracking pages: submission order.
            sub_order = [r['submitted_at'] for r in services.submitted_rolls(f1)]
            self.assertEqual(len(set(sub_order)), len(TEXTS))               # timestamps differ
            by_time = {}
            for stu, txt in zip(students, TEXTS):
                roll = db.query('SELECT roll FROM users WHERE id=?', (stu,), one=True)['roll']
                by_time[roll] = txt
            ordered_rolls = [r['roll'] for r in sorted(services.submitted_rolls(f1),
                                                       key=lambda r: r['submitted_at'])]
            submission_order = [by_time[r] for r in ordered_rolls]
            self.assertEqual(submission_order, TEXTS)
            self.assertNotEqual(shown1, submission_order)                    # not same order
            self.assertNotEqual(shown1, list(reversed(submission_order)))    # nor reversed

            # The listing is a function of the SET of comments only: opposite
            # submission orders give an identical display order.
            self.assertEqual(shown1, shown2)

            # Pagination must not bring the submission order back.
            for per_page in (1, 2, 3):
                self.assertEqual(self._all(f1, per_page=per_page), shown1, per_page)
            pg = services.suggestions_paged(f1, page=1, per_page=3)
            self.assertEqual(set(pg), {'comments', 'total', 'page', 'pages', 'per_page'})
            self.assertTrue(all(isinstance(c, str) for c in pg['comments']))  # text only

            # The inline per-question list (question_stats) follows the same rule.
            stats = services.question_stats(f1)
            text_stat = next(q for q in stats if q['qtype'] == 'text')
            self.assertEqual(text_stat['suggestions'], shown1)
        finally:
            self.cleanup_forms(f1, f2)

    def test_legacy_answers_branch_is_ordered_the_same_way(self):
        """Pre-5B rows (``answers`` table) share the anonymous_answers view."""
        i = self.ids
        f = self.make(i['hod_a'], 'main', i['sec']['sid'])
        try:
            text_q = next(q['id'] for q in services.form_questions(f) if q['qtype'] == 'text')
            for n, (stu, txt) in enumerate(zip(i['students'], TEXTS)):
                sid = db.insert_id(
                    'INSERT INTO submissions(form_id, session_id, student_user_id, submitted_at)'
                    ' VALUES(?,?,?,?)',
                    (f, i['sec']['sid'], stu, f'2026-01-05T09:{n:02d}:00'))
                db.get_db().execute('INSERT INTO answers(submission_id, question_id, text_value)'
                                    ' VALUES(?,?,?)', (sid, text_q, txt))
                db.get_db().commit()
            shown = self._all(f, per_page=3)
            self.assertEqual(shown, sorted(TEXTS, key=lambda t: (t.lower(), t)))
            self.assertNotEqual(shown, TEXTS)
        finally:
            db.execute('DELETE FROM answers WHERE submission_id IN '
                       '(SELECT id FROM submissions WHERE form_id=?)', (f,))
            self.cleanup_forms(f)

    def test_admin_result_page_lists_comments_in_content_order(self):
        i = self.ids
        f = self.make(i['hod_a'], 'main', i['sec']['sid'])
        try:
            self._submit_with_comments(f, i['students'][:len(TEXTS)], TEXTS)
            self.ctx.pop()                       # keep flask.g away from the HTTP client
            try:
                c = self.app.test_client()
                self.assertEqual(login(c, '/admin/login', {
                    'email': 'admin@college.edu', 'password': 'admin123'}).status_code, 302)
                html = c.get(f'/admin/results/{f}').get_data(as_text=True)
            finally:
                self.ctx.push()
            pos = [html.index(t) for t in TEXTS]
            by_position = [t for _, t in sorted(zip(pos, TEXTS))]
            self.assertEqual(by_position, sorted(TEXTS))
            self.assertNotEqual(by_position, TEXTS)
        finally:
            self.cleanup_forms(f)


class TestFix2LegacyStatusRoute(Base):
    def _client(self, path, ident, pw, key='emp_code'):
        c = self.app.test_client()
        r = login(c, path, {key: ident, 'password': pw})
        self.assertEqual(r.status_code, 302)
        return c

    def _hod_post(self, c, fid, status):
        with c.session_transaction() as sess:
            tok = sess['token']
        return c.post(f'/hod/forms/{fid}/status', json={'status': status},
                      headers={'X-CSRF-Token': tok, 'X-Requested-With': 'XMLHttpRequest'})

    def _admin_post(self, c, fid, status):
        with c.session_transaction() as sess:
            tok = sess['token']
        return c.post(f'/admin/forms/{fid}/status', data={'_csrf': tok, 'status': status})

    def _status(self, fid):
        return db.query('SELECT status FROM feedback_forms WHERE id=?', (fid,), one=True)['status']

    def _snapshot_n(self, fid):
        return db.query('SELECT COUNT(*) c FROM form_eligible_students WHERE form_id=?',
                        (fid,), one=True)['c']

    def _http(self, fn):
        """Run HTTP calls outside the test's app context (flask.g caching)."""
        self.ctx.pop()
        try:
            return fn()
        finally:
            self.ctx.push()

    # ---- the invalid transition -------------------------------------------
    def test_form_with_submissions_cannot_return_to_draft_via_hod_or_admin(self):
        i = self.ids
        f = self.make(i['hod_a'], 'lab', i['sec']['sid'])               # published
        try:
            self.assertTrue(services.submit_feedback(i['students'][0], f, self.answers(f))[0])
            frozen = self._snapshot_n(f)
            self.assertGreater(frozen, 0)

            def attempt():
                hod = self._client('/hod/login', 'HODA', 'x12345678')
                r1 = self._hod_post(hod, f, 'draft')
                adm = self.app.test_client()
                login(adm, '/admin/login', {'email': 'admin@college.edu',
                                            'password': 'admin123'})
                r2 = self._admin_post(adm, f, 'draft')
                return r1, r2
            r1, r2 = self._http(attempt)
            self.assertEqual(r1.status_code, 409, r1.get_data(as_text=True))
            self.assertFalse(r1.get_json()['ok'])
            self.assertIn('cannot be returned to draft', r1.get_json()['errors'][0])
            self.assertEqual(r2.status_code, 302)                         # flash + redirect
            self.assertEqual(self._status(f), 'open')                     # still published
            self.assertEqual(self._snapshot_n(f), frozen)                 # snapshot untouched
            # closed + submissions is blocked as well (any non-draft state)
            cf.close(self.actor(i['hod_a']), f)
            with self.assertRaises(fc.ScopeError) as cm:
                cf.change_status(self.actor(i['hod_a']), f, 'draft')
            self.assertEqual(cm.exception.code, 'invalid_transition')
            self.assertEqual(self._status(f), 'closed')
            # defence in depth: the low-level service refuses too
            with self.assertRaises(ValueError):
                services.set_form_status(f, 'draft')
            self.assertEqual(self._status(f), 'closed')
        finally:
            self.cleanup_forms(f)

    # ---- valid transitions ------------------------------------------------
    def test_valid_transitions_use_category_lifecycle_and_snapshot_atomically(self):
        i = self.ids
        f = self.make(i['hod_a'], 'lab', i['sec']['sid'], publish=False)
        try:
            self.assertEqual(self._status(f), 'draft')
            self.assertEqual(self._snapshot_n(f), 0)
            sc = fc.resolve_scope('lab', i['sec']['sid'])

            def publish_via_route():
                hod = self._client('/hod/login', 'HODA', 'x12345678')
                return hod, self._hod_post(hod, f, 'open')
            hod, r = self._http(publish_via_route)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            row = db.query('SELECT * FROM feedback_forms WHERE id=?', (f,), one=True)
            self.assertEqual(row['status'], 'open')
            self.assertEqual(self._snapshot_n(f), fc.eligible_count(sc))     # snapshot == eligible
            self.assertEqual(row['eligible_count_at_publish'], self._snapshot_n(f))
            self.assertIsNotNone(row['eligibility_snapshot_at'])
            self.assertEqual(row['category_basis'], 'declared')

            # asking for the state it is already in changes nothing (no re-freeze)
            stamp = db.query('SELECT eligibility_snapshot_at a, published_at p FROM '
                             'feedback_forms WHERE id=?', (f,), one=True)
            r = self._http(lambda: self._hod_post(hod, f, 'open'))
            self.assertEqual(r.status_code, 200)
            again = db.query('SELECT eligibility_snapshot_at a, published_at p FROM '
                             'feedback_forms WHERE id=?', (f,), one=True)
            self.assertEqual((stamp['a'], stamp['p']), (again['a'], again['p']))
            self.assertEqual(self._snapshot_n(f), fc.eligible_count(sc))
            # a published (non-draft) form can never be published a second time
            with self.assertRaises(fc.ScopeError):
                cf.publish(self.actor(i['hod_a']), f)

            # students can submit; then open -> closed goes through category_forms.close
            self.assertTrue(services.submit_feedback(i['students'][0], f, self.answers(f))[0])
            r = self._http(lambda: self._hod_post(hod, f, 'closed'))
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            row = db.query('SELECT status, closed_at FROM feedback_forms WHERE id=?', (f,),
                           one=True)
            self.assertEqual(row['status'], 'closed')
            self.assertIsNotNone(row['closed_at'])
            self.assertEqual(self._snapshot_n(f), fc.eligible_count(sc))     # snapshot retained
            self.assertFalse(services.submit_feedback(i['students'][1], f,
                                                      self.answers(f))[0])

            # re-open (closed -> open): keeps the frozen list, no duplicates
            before = sorted(r['student_user_id'] for r in db.query(
                'SELECT student_user_id FROM form_eligible_students WHERE form_id=?', (f,)))
            r = self._http(lambda: self._hod_post(hod, f, 'open'))
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            after = sorted(r['student_user_id'] for r in db.query(
                'SELECT student_user_id FROM form_eligible_students WHERE form_id=?', (f,)))
            self.assertEqual(before, after)
            self.assertEqual(self._status(f), 'open')
            self.assertEqual(fc.duplicate_eligibility_records(f), [])
        finally:
            self.cleanup_forms(f)

    def test_admin_route_valid_transitions_and_snapshot(self):
        i = self.ids
        f = self.make(i['hod_a'], 'main', i['sec']['sid'], publish=False)
        try:
            def run():
                adm = self.app.test_client()
                login(adm, '/admin/login', {'email': 'admin@college.edu',
                                            'password': 'admin123'})
                return [self._admin_post(adm, f, s) for s in ('open', 'closed', 'open')]
            rs = self._http(run)
            self.assertEqual([r.status_code for r in rs], [302, 302, 302])
            self.assertEqual(self._status(f), 'open')
            sc = fc.resolve_scope('main', i['sec']['sid'])
            self.assertEqual(self._snapshot_n(f), fc.eligible_count(sc))
            self.assertEqual(fc.duplicate_eligibility_records(f), [])
            row = db.query('SELECT eligible_count_at_publish c FROM feedback_forms WHERE id=?',
                           (f,), one=True)
            self.assertEqual(row['c'], self._snapshot_n(f))
        finally:
            self.cleanup_forms(f)

    def test_draft_without_submissions_can_still_be_closed_archived_or_unpublished(self):
        i = self.ids
        hod_a = self.actor(i['hod_a'])
        # open form with NO submissions may be sent back to draft, then re-published
        f = self.make(i['hod_a'], 'main', i['sec']['sid'])
        try:
            cf.change_status(hod_a, f, 'draft')
            self.assertEqual(self._status(f), 'draft')
            cf.change_status(hod_a, f, 'open')               # re-publish refreshes snapshot
            self.assertEqual(self._status(f), 'open')
            sc = fc.resolve_scope('main', i['sec']['sid'])
            self.assertEqual(self._snapshot_n(f), fc.eligible_count(sc))
            cf.change_status(hod_a, f, 'closed')
            cf.change_status(hod_a, f, 'archived')
            self.assertEqual(self._status(f), 'archived')
        finally:
            self.cleanup_forms(f)
        # a draft can be closed directly (the HOD forms page offers this button)
        g = self.make(i['hod_a'], 'second', i['a_ok'], publish=False)
        try:
            cf.change_status(hod_a, g, 'closed')
            self.assertEqual(self._status(g), 'closed')
        finally:
            self.cleanup_forms(g)

    def test_second_form_publish_via_status_route_and_authorization(self):
        i = self.ids
        g = self.make(i['hod_a'], 'second', i['a_ok'], publish=False)
        try:
            # another department's HOD (and a teacher) cannot move it
            def other():
                hod_b = self._client('/hod/login', 'HODB', 'x12345678')
                return self._hod_post(hod_b, g, 'open')
            self.assertEqual(self._http(other).status_code, 403)
            self.assertEqual(self._status(g), 'draft')
            with self.assertRaises(fc.ScopeError) as cm:     # service-level authority check
                cf.change_status(self.actor(i['hod_b']), g, 'open')
            self.assertEqual(cm.exception.status, 403)
            self.assertEqual(self._snapshot_n(g), 0)
            # the owning HOD publishes through the legacy route -> snapshot + open together
            def own():
                hod_a = self._client('/hod/login', 'HODA', 'x12345678')
                return self._hod_post(hod_a, g, 'open')
            self.assertEqual(self._http(own).status_code, 200)
            sc = fc.resolve_scope('second', i['a_ok'])
            self.assertEqual(self._snapshot_n(g), fc.eligible_count(sc))
            self.assertEqual(self._status(g), 'open')
        finally:
            self.cleanup_forms(g)

    def test_main_conflict_rule_is_unchanged_on_legacy_route(self):
        """Several Main forms per session/phase remain publishable via the legacy
        route (this task must not change the Main-form conflict rules)."""
        i = self.ids
        hod_a = self.actor(i['hod_a'])
        f1 = self.make(i['hod_a'], 'main', i['sec']['sid'])               # open
        second_main = None
        try:
            payload = dict(title='extra main', instructions='', category='main',
                           academic_year_id=i['sec']['ay'], semester_id=i['sec']['sem'],
                           branch_id=i['sec']['bid'], timetable_id=i['sec']['ttid'],
                           session_id=i['sec']['sid'], scope='session',
                           subject_code=i['sec']['sc'], subject_name=i['sec']['sn'],
                           deadline_at=FUTURE)
            second_main = services.save_form(payload, cf.default_questions('main'),
                                             hod_a['id'], session_ids=[i['sec']['sid']])
            self.assertEqual(services.validate_form_for_publish(second_main), [])
            cf.change_status(hod_a, second_main, 'open')
            self.assertEqual(self._status(second_main), 'open')
            self.assertGreater(self._snapshot_n(second_main), 0)
        finally:
            self.cleanup_forms(*(x for x in (f1, second_main) if x))


if __name__ == '__main__':
    unittest.main(verbosity=2)
