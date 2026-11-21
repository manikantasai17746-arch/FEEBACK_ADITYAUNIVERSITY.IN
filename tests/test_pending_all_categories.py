"""HOD Pending submissions: Main, Second Faculty and Lab Faculty forms are all
selectable (grouped by category) and each shows its own faculty."""
import re

import pytest

from feedback_system import db, services
from feedback_system.security import hash_password
from helpers import future_deadline
from test_hod_feedback_second_and_results import (PW, _ensure_sfa, _login_hod,  # noqa: F401
                                                  _seed_hod, app, client)


def _csrf(c, path):
    return re.search(r'name="csrf-token" content="([^"]+)"',
                     c.get(path).get_data(as_text=True)).group(1)


def _three_forms(app, client):
    seed = _seed_hod(app)
    aids = _ensure_sfa(app, seed['branch_id'], 1)
    if not aids:
        pytest.skip('no second-faculty assignment')
    with app.app_context():
        sfa = dict(db.query('SELECT * FROM second_faculty_assignments WHERE id=?',
                            (aids[0],), one=True))
        qsid = db.query('SELECT id FROM question_sets WHERE owner_user_id=? AND is_deleted=0 LIMIT 1',
                        (seed['hod_id'],), one=True)['id']
        db.execute('UPDATE feedback_forms SET is_deleted=1 WHERE session_id=? '
                   'OR second_faculty_assignment_id=?', (sfa['session_id'], aids[0]))
    c = _login_hod(client)
    dl, out = future_deadline(), {}
    for cat, body in (('main', {'session_ids': [sfa['session_id']]}),
                      ('lab', {'session_ids': [sfa['session_id']]}),
                      ('second', {'assignment_ids': [aids[0]]})):
        r = c.post('/hod/feedback/%s/api/create' % cat, headers={'X-CSRF-Token': _csrf(
            c, '/hod/feedback/%s/create' % cat)}, json=dict(body, question_set_id=qsid,
                                                           publish=True, deadline_at=dl))
        d = r.get_json()
        if not (d.get('ok') and d.get('created')):
            pytest.skip('could not publish %s form: %s' % (cat, d))
        out[cat] = d['created'][0]['form_id']
    return c, sfa, out


def test_all_three_categories_listed_and_selectable(app, client):
    c, sfa, fid = _three_forms(app, client)
    page = c.get('/hod/pending').get_data(as_text=True)
    for label in ('Main Faculty Feedback', 'Second Faculty Feedback', 'Lab Faculty Feedback'):
        assert '<optgroup label="%s">' % label in page
    for f in fid.values():
        assert 'value="%d"' % f in page
    with app.app_context():
        main_t = db.query('SELECT emp_code FROM users WHERE id=?', (sfa['main_teacher_user_id'],),
                          one=True)['emp_code']
    # each category's form opens and lists its own teacher
    for cat, emp in (('main', main_t), ('lab', main_t), ('second', sfa['second_emp_code'])):
        html = c.get('/hod/pending?form_id=%d' % fid[cat]).get_data(as_text=True)
        assert re.search(r'<td class="wrap"><strong>[^<]+</strong>\s*<div class="sub">%s' % re.escape(emp), html), cat
        label = {'main': 'Main Faculty Feedback', 'second': 'Second Faculty Feedback',
                 'lab': 'Lab Faculty Feedback'}[cat]
        assert '<span class="badge">%s</span>' % label in html


def test_second_form_lists_only_its_own_second_faculty(app, client):
    c, sfa, fid = _three_forms(app, client)
    with app.app_context():
        other = db.query("SELECT id, emp_code FROM users WHERE role='teacher' AND id NOT IN (?,?) LIMIT 1",
                         (sfa['main_teacher_user_id'], sfa['second_teacher_user_id']), one=True)
        db.execute(
            """INSERT INTO second_faculty_assignments
               (academic_year_id, semester_id, branch_id, timetable_id, session_id, subject_code,
                subject_name, main_emp_code, main_teacher_user_id, second_emp_code,
                second_teacher_user_id, period_no, status, source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'confirmed','test')""",
            (sfa['academic_year_id'], sfa['semester_id'], sfa['branch_id'], sfa['timetable_id'],
             sfa['session_id'], sfa['subject_code'], sfa['subject_name'], sfa['main_emp_code'],
             sfa['main_teacher_user_id'], other['emp_code'], other['id'], sfa['period_no']))
        db.get_db().commit()
        rows = services.pending_by_teacher(fid['second'])
    assert [r['teacher_user_id'] for r in rows] == [sfa['second_teacher_user_id']]
