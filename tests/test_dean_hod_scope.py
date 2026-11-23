"""Dean -> HOD scoping, synthetic data only (no timetable workbook needed).

Two HODs share ONE department and semester but own different timetables.
Each Dashboard "View" link must open exactly that HOD's teachers, the score-band
cards must name the right HOD, and records outside the Dean's scope must stay
inaccessible.  A third HOD/department belongs to another Dean.
"""
import os
import re
import tempfile

import pytest

from feedback_system import create_app
from feedback_system import db as db_module
from feedback_system.security import hash_password
from helpers import get_csrf, login

SCALE = [('Excellent', 5), ('Very Good', 4), ('Good', 3), ('Average', 2), ('Poor', 1)]


@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-deanhod-')
    yield create_app({
        'TESTING': True, 'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None, 'SECRET_KEY': 'test-secret-dean-hod',
        'AUTO_IMPORT': False, 'ADMIN_EMAIL': 'admin@college.edu',
        'ADMIN_PASSWORD': 'admin123', 'REQUIRE_ACCOUNT_SETUP': False,
        'LOGIN_MAX_ATTEMPTS': 50,
    })


def _user(role, code, name=None):
    db_module.execute(
        """INSERT INTO users(role, username, emp_code, roll, name, email, password_hash,
                             is_active, onboarding_complete)
           VALUES(?,?,?,?,?,?,?,1,1)""",
        (role, code, code, code if role == 'student' else None, name or code,
         f'{code.lower()}@t.edu', hash_password('pass12345')))
    return db_module.query('SELECT id FROM users WHERE username=?', (code,), one=True)['id']


def _form(branch, ay, sem, tt, teacher, students, rating, subj):
    sid = db_module.insert_id(
        """INSERT INTO class_sessions(timetable_id, subject_code, subject_name,
               teacher_user_id, period_no) VALUES(?,?,?,?,?)""",
        (tt, subj, f'Subject {subj}', teacher, 1))
    fid = db_module.insert_id(
        """INSERT INTO feedback_forms(title, academic_year_id, semester_id, branch_id,
               timetable_id, session_id, status, snapshot_subject_code, snapshot_subject_name,
               category) VALUES(?,?,?,?,?,?, 'open', ?, ?, 'main')""",
        (f'Form {subj}', ay, sem, branch, tt, sid, subj, f'Subject {subj}'))
    db_module.execute('INSERT INTO form_sessions(form_id, session_id) VALUES(?,?)', (fid, sid))
    qid = db_module.insert_id(
        "INSERT INTO form_questions(form_id, position, text, qtype) VALUES(?,?,?,'rating')",
        (fid, 1, 'Q1'))
    opt = {}
    for k, (label, val) in enumerate(SCALE):
        opt[label] = db_module.insert_id(
            'INSERT INTO question_options(question_id, position, label, value) VALUES(?,?,?,?)',
            (qid, k, label, val))
    for stu in students:
        db_module.execute(
            'INSERT INTO session_students(session_id, student_user_id) VALUES(?,?)', (sid, stu))
        sub = db_module.insert_id(
            'INSERT INTO submissions(form_id, session_id, student_user_id) VALUES(?,?,?)',
            (fid, sid, stu))
        db_module.execute(
            'INSERT INTO answers(submission_id, question_id, option_id) VALUES(?,?,?)',
            (sub, qid, opt[rating]))
    return fid


@pytest.fixture(scope='module')
def world(app):
    with app.app_context():
        db_module.execute("INSERT INTO academic_years(name, is_active) VALUES('2026-27', 1)")
        ay = db_module.query("SELECT id FROM academic_years WHERE name='2026-27'", one=True)['id']
        db_module.execute("INSERT INTO semesters(number, label) VALUES(3, 'Semester 3')")
        sem = db_module.query('SELECT id FROM semesters WHERE number=3', one=True)['id']
        w = {'ay': ay, 'sem': sem}
        for code in ('CSE', 'MECH'):
            db_module.execute('INSERT INTO branches(code, name) VALUES(?,?)', (code, code + ' Dept'))
            w[code] = db_module.query('SELECT id FROM branches WHERE code=?', (code,), one=True)['id']
        tt = {}
        for code, br in (('CSE-A', 'CSE'), ('CSE-B', 'CSE'), ('MECH-A', 'MECH')):
            tt[code] = db_module.insert_id(
                'INSERT INTO timetables(branch_id, semester_id, code, name) VALUES(?,?,?,?)',
                (w[br], sem, code, code))
        w['tt'] = tt
        studs = [_user('student', f'ST{i}') for i in range(3)]
        # teachers: two on CSE-A (HOD A), three on CSE-B (HOD B), one on MECH (other Dean)
        w['teachers'] = {}
        plan = [('TA1', 'CSE', 'CSE-A', 'Excellent'), ('TA2', 'CSE', 'CSE-A', 'Good'),
                ('TB1', 'CSE', 'CSE-B', 'Excellent'), ('TB2', 'CSE', 'CSE-B', 'Average'),
                ('TB3', 'CSE', 'CSE-B', 'Very Good'), ('TM1', 'MECH', 'MECH-A', 'Excellent')]
        w['forms'] = {}
        for code, br, tcode, rating in plan:
            tid = _user('teacher', code, f'Teacher {code}')
            w['teachers'][code] = tid
            w['forms'][code] = _form(w[br], ay, sem, tt[tcode], tid, studs, rating, f'S-{code}')
        w['hodA'] = _user('hod', 'HODA', 'Dr. Alpha')
        w['hodB'] = _user('hod', 'HODB', 'Dr. Beta')
        w['hodM'] = _user('hod', 'HODM', 'Dr. Mech')
        for hid, br, t in ((w['hodA'], 'CSE', 'CSE-A'), (w['hodB'], 'CSE', 'CSE-B'),
                           (w['hodM'], 'MECH', 'MECH-A')):
            db_module.execute(
                """INSERT INTO hod_allocations(hod_user_id, academic_year_id, branch_id,
                       semester_id, timetable_id) VALUES(?,?,?,?,?)""",
                (hid, ay, w[br], sem, tt[t]))
        w['dean'] = _user('dean', 'DEAN1', 'Dean One')
        w['dean2'] = _user('dean', 'DEAN2', 'Dean Two')
        for hid in (w['hodA'], w['hodB']):
            db_module.execute(
                'INSERT INTO dean_hod_assignments(dean_user_id, hod_user_id) VALUES(?,?)',
                (w['dean'], hid))
        db_module.execute(
            'INSERT INTO dean_hod_assignments(dean_user_id, hod_user_id) VALUES(?,?)',
            (w['dean2'], w['hodM']))
        return w


def _login(app, code):
    c = app.test_client()
    assert login(c, '/dean/login', {'emp_code': code, 'password': 'pass12345'}).status_code == 302
    return c


def _teacher_ids_in(html):
    return set(map(int, re.findall(r'/dean/performance/faculty/(\d+)', html)))


def _view_links(html):
    return re.findall(r'href="(/dean/performance\?[^"]*)"[^>]*>\s*View', html)


def test_dashboard_has_one_view_link_per_hod_with_teachers_anchor(app, world):
    c = _login(app, 'DEAN1')
    html = c.get('/dean/').get_data(as_text=True)
    links = [h.replace('&amp;', '&') for h in _view_links(html)]
    assert len(links) == 2
    for link in links:
        assert link.endswith('#teachers')
        assert f"branch_id={world['CSE']}" in link
    assert {int(re.search(r'hod_id=(\d+)', l).group(1)) for l in links} \
        == {world['hodA'], world['hodB']}


def test_each_view_link_opens_exactly_that_hods_teachers(app, world):
    c = _login(app, 'DEAN1')
    t = world['teachers']
    expect = {world['hodA']: {t['TA1'], t['TA2']},
              world['hodB']: {t['TB1'], t['TB2'], t['TB3']}}
    html = c.get('/dean/').get_data(as_text=True)
    for link in _view_links(html):
        link = link.replace('&amp;', '&')
        hid = int(re.search(r'hod_id=(\d+)', link).group(1))
        page = c.get(link.split('#')[0])
        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert 'id="teachers"' in body                       # anchor target exists
        assert _teacher_ids_in(body) == expect[hid]
        assert t['TM1'] not in _teacher_ids_in(body)         # other Dean's teacher


def test_score_band_cards_name_the_correct_hod(app, world):
    c = _login(app, 'DEAN1')
    t = world['teachers']
    seen = {}
    from feedback_system.hod import SCORE_BANDS
    for key, *_ in SCORE_BANDS:
        body = c.get(f"/dean/performance?branch_id={world['CSE']}&band={key}").get_data(as_text=True)
        # split into cards and read teacher + HOD line of each
        for card in body.split('class="band-card"')[1:]:
            tid = int(re.search(r'/dean/performance/faculty/(\d+)', card).group(1))
            hod = re.search(r'HOD: <strong>([^<]*)</strong>', card).group(1)
            seen[tid] = hod
    assert seen == {t['TA1']: 'Dr. Alpha', t['TA2']: 'Dr. Alpha',
                    t['TB1']: 'Dr. Beta', t['TB2']: 'Dr. Beta', t['TB3']: 'Dr. Beta'}


def test_band_cards_respect_the_hod_filter(app, world):
    c = _login(app, 'DEAN1')
    t = world['teachers']
    from feedback_system.hod import SCORE_BANDS
    ids = set()
    for key, *_ in SCORE_BANDS:
        body = c.get(f"/dean/performance?hod_id={world['hodB']}&band={key}").get_data(as_text=True)
        ids |= _teacher_ids_in(body)
    assert ids == {t['TB1'], t['TB2'], t['TB3']}


def test_publication_view_links_carry_hod_and_anchor(app, world):
    c = _login(app, 'DEAN1')
    html = c.get('/dean/publication').get_data(as_text=True)
    links = [h.replace('&amp;', '&') for h in _view_links(html)]
    assert len(links) == 2 and all(l.endswith('#teachers') for l in links)
    hods = {int(re.search(r'hod_id=(\d+)', l).group(1)) for l in links}
    assert hods == {world['hodA'], world['hodB']}
    # each HOD row shows only its own forms (2 vs 3), not the department total (5)
    rows = re.findall(r'<td><span class="dz-chip[^>]*>(\d+) Published', html)
    assert sorted(rows) == ['2', '3']


def test_out_of_scope_records_are_not_reachable(app, world):
    c = _login(app, 'DEAN1')
    t = world['teachers']
    # a department outside the Dean's scope is ignored: result shows only own scope
    body = c.get(f"/dean/performance?branch_id={world['MECH']}").get_data(as_text=True)
    assert t['TM1'] not in _teacher_ids_in(body)
    # a HOD that belongs to another Dean is ignored too (no leak, no error)
    body = c.get(f"/dean/performance?hod_id={world['hodM']}").get_data(as_text=True)
    assert t['TM1'] not in _teacher_ids_in(body)
    # the other Dean's teacher shows no results for this Dean
    detail = c.get(f"/dean/performance/faculty/{t['TM1']}")
    assert detail.status_code in (200, 403, 404)
    assert 'Subject S-TM1' not in detail.get_data(as_text=True)
    # the other Dean sees only the MECH teacher
    c2 = _login(app, 'DEAN2')
    assert _teacher_ids_in(c2.get('/dean/performance').get_data(as_text=True)) == {t['TM1']}


def test_non_dean_roles_are_refused(app, world):
    c = app.test_client()
    assert c.get('/dean/performance', follow_redirects=False).status_code in (302, 401, 403)
    hod = app.test_client()
    login(hod, '/hod/login', {'emp_code': 'HODA', 'password': 'pass12345'})
    assert hod.get('/dean/performance').status_code in (302, 401, 403)
    assert hod.get('/dean/student-complaints').status_code in (302, 401, 403)


@pytest.mark.parametrize('path', ['/dean/', '/dean/performance', '/dean/comparison',
                                  '/dean/actions', '/dean/publication', '/dean/hods',
                                  '/dean/analytics', '/dean/historical'])
def test_student_complaints_in_nav_on_all_eight_pages(app, world, path):
    c = _login(app, 'DEAN1')
    body = c.get(path).get_data(as_text=True)
    assert 'href="/dean/student-complaints"' in body
    assert 'id="btnDarkMode"' in body and 'btnLargeText' in body
    assert 'Logout' in body and 'Security' in body and 'Profile' in body


def test_review_request_goes_to_the_hod_who_owns_the_timetable(app, world):
    """Bulk 'review request' must create the action for the right HOD, even though
    both HODs cover the same department and semester."""
    c = _login(app, 'DEAN1')
    t = world['teachers']
    page = c.get(f"/dean/actions/bulk?branch_id={world['CSE']}").get_data(as_text=True)
    tok = get_csrf(page)
    picks = [f"{t['TA1']}:{world['forms']['TA1']}", f"{t['TB1']}:{world['forms']['TB1']}"]
    r = c.post('/dean/actions/bulk/create', data={
        '_csrf': tok, 'branch_id': world['CSE'], 'band': '', 'pick': picks,
        'message': 'Please review {name} {score}', 'priority': 'high'})
    assert r.status_code == 302
    with app.app_context():
        owner = {row['teacher_user_id']: row['owner_user_id'] for row in db_module.query(
            'SELECT teacher_user_id, owner_user_id FROM improvement_actions '
            'WHERE teacher_user_id IS NOT NULL')}
    assert owner[t['TA1']] == world['hodA']
    assert owner[t['TB1']] == world['hodB']


def test_performance_links_to_review_requests(app, world):
    c = _login(app, 'DEAN1')
    from feedback_system.hod import SCORE_BANDS
    key = SCORE_BANDS[-1][0]
    body = c.get(f"/dean/performance?branch_id={world['CSE']}&band={key}").get_data(as_text=True)
    assert '/dean/actions/bulk?' in body and f'band={key}' in body


def test_same_department_and_semester_rows_are_told_apart(app, world):
    c = _login(app, 'DEAN1')
    html = c.get('/dean/').get_data(as_text=True)
    assert 'CSE · Sem 3 · CSE-A' in html and 'CSE · Sem 3 · CSE-B' in html
