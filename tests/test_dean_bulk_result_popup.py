"""Dean > Actions > Send feedback review requests: the "View result" popup.

Synthetic data only.  One teacher has TWO forms for the SAME subject (Main + Lab), so
every row must open the popup for its own form (``form_id``), never the other one.
"""
import os
import re
import tempfile

import pytest

from feedback_system import create_app
from feedback_system import db as db_module
from helpers import login
from test_dean_hod_scope import SCALE, _form, _user

SUBJ = '2501CS10'


def _lab_form_on_same_session(main_fid, students, rating):
    """A Lab form on the SAME class session as ``main_fid`` (Lab is keyed by the session)."""
    m = db_module.query('SELECT * FROM feedback_forms WHERE id=?', (main_fid,), one=True)
    fid = db_module.insert_id(
        """INSERT INTO feedback_forms(title, academic_year_id, semester_id, branch_id,
               timetable_id, session_id, status, snapshot_subject_code, snapshot_subject_name,
               category) VALUES(?,?,?,?,?,?, 'open', ?, ?, 'lab')""",
        ('Lab ' + m['title'], m['academic_year_id'], m['semester_id'], m['branch_id'],
         m['timetable_id'], m['session_id'], m['snapshot_subject_code'], m['snapshot_subject_name']))
    db_module.execute('INSERT INTO form_sessions(form_id, session_id) VALUES(?,?)',
                      (fid, m['session_id']))
    qid = db_module.insert_id(
        "INSERT INTO form_questions(form_id, position, text, qtype) VALUES(?,?,?,'rating')",
        (fid, 1, 'Q1'))
    opt = {label: db_module.insert_id(
        'INSERT INTO question_options(question_id, position, label, value) VALUES(?,?,?,?)',
        (qid, k, label, val)) for k, (label, val) in enumerate(SCALE)}
    for stu in students:
        sub = db_module.insert_id(
            'INSERT INTO submissions(form_id, session_id, student_user_id) VALUES(?,?,?)',
            (fid, m['session_id'], stu))
        db_module.execute('INSERT INTO answers(submission_id, question_id, option_id) VALUES(?,?,?)',
                          (sub, qid, opt[rating]))
    return fid


@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-bulkpop-')
    yield create_app({
        'TESTING': True, 'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None, 'SECRET_KEY': 'test-secret-bulk-popup',
        'AUTO_IMPORT': False, 'ADMIN_EMAIL': 'admin@college.edu',
        'ADMIN_PASSWORD': 'admin123', 'REQUIRE_ACCOUNT_SETUP': False,
        'LOGIN_MAX_ATTEMPTS': 50,
    })


@pytest.fixture(scope='module')
def world(app):
    with app.app_context():
        db_module.execute("INSERT INTO academic_years(name, is_active) VALUES('2026-27', 1)")
        ay = db_module.query('SELECT id FROM academic_years', one=True)['id']
        db_module.execute("INSERT INTO semesters(number, label) VALUES(3, 'Semester 3')")
        sem = db_module.query('SELECT id FROM semesters', one=True)['id']
        db_module.execute("INSERT INTO branches(code, name) VALUES('CSE', 'CSE Dept')")
        br = db_module.query("SELECT id FROM branches WHERE code='CSE'", one=True)['id']
        tt = db_module.insert_id(
            'INSERT INTO timetables(branch_id, semester_id, code, name) VALUES(?,?,?,?)',
            (br, sem, 'CSE-A', 'CSE-A'))
        studs = [_user('student', f'BS{i}') for i in range(8)]
        teacher = _user('teacher', 'BT1', 'Teacher BT1')
        other = _user('teacher', 'BT2', 'Teacher BT2')
        # same teacher + same subject, two feedback categories
        main = _form(br, ay, sem, tt, teacher, studs[:6], 'Poor', SUBJ)     # 6 responses -> scored
        lab = _lab_form_on_same_session(main, studs[:3], 'Poor')            # 3 responses -> N/A
        solo = _form(br, ay, sem, tt, other, studs, 'Poor', 'OTHER1')
        hod = _user('hod', 'BHOD', 'Dr. Bulk')
        db_module.execute(
            """INSERT INTO hod_allocations(hod_user_id, academic_year_id, branch_id,
                   semester_id, timetable_id) VALUES(?,?,?,?,?)""", (hod, ay, br, sem, tt))
        dean = _user('dean', 'BDEAN', 'Dean Bulk')
        db_module.execute(
            'INSERT INTO dean_hod_assignments(dean_user_id, hod_user_id) VALUES(?,?)', (dean, hod))
        return {'teacher': teacher, 'other': other, 'main': main, 'lab': lab, 'solo': solo,
                'br': br, 'ay': ay, 'sem': sem}


@pytest.fixture()
def dean(app, world):
    c = app.test_client()
    assert login(c, '/dean/login', {'emp_code': 'BDEAN', 'password': 'pass12345'}).status_code == 302
    return c


def _page(c, category):
    r = c.get(f'/dean/actions/bulk?category={category}')
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_each_row_links_to_the_popup_of_its_own_form(dean, world):
    t = world['teacher']
    for category, key in (('main', 'main'), ('lab', 'lab')):
        html = _page(dean, category)
        want = f'data-result-url="/dean/performance/faculty/{t}/result?form_id={world[key]}"'
        assert want in html, category
        other = 'lab' if key == 'main' else 'main'
        assert f'form_id={world[other]}"' not in html.split('<tbody>')[1].split('</tbody>')[0]
        # JavaScript-off fallback is still a real full-page link
        assert re.search(rf'{re.escape(want)}\s+href="/dean/performance/faculty/{t}"', html)


def test_popup_host_is_outside_the_form_and_script_is_loaded(dean):
    html = _page(dean, 'main')
    assert html.count('id="result-dialog"') == 1 and 'data-rd-slot' in html
    assert html.index('<dialog id="result-dialog"') > html.rindex('</form>')   # cannot submit the letter
    assert 'js/dean-performance.js' in html
    assert re.search(r'<a [^>]*data-result-url[^>]*>View result</a>', html)    # a link, not a submit


def test_view_result_exists_even_when_action_is_open(app, dean, world):
    with app.app_context():
        db_module.execute(
            "INSERT INTO improvement_actions(branch_id, title, status, teacher_user_id, form_id)"
            " VALUES(?,?,'planned',?,?)", (world['br'], 'open review', world['teacher'], world['main']))
    html = _page(dean, 'main')
    row = next(r for r in html.split('<tr>') if f"form_id={world['main']}" in r)
    assert 'Action open' in row and '<input type="checkbox" disabled>' in row
    assert 'data-result-url' in row


def test_popup_fragment_focuses_the_clicked_form(dean, world):
    t = world['teacher']
    main = dean.get(f"/dean/performance/faculty/{t}/result?form_id={world['main']}").get_data(as_text=True)
    lab = dean.get(f"/dean/performance/faculty/{t}/result?form_id={world['lab']}").get_data(as_text=True)
    # exactly one card per popup, and it is the requested form
    assert main.count('class="rd-card"') == 1 and lab.count('class="rd-card"') == 1
    assert '<b>6</b><span>responses</span>' in main and '<b>3</b><span>responses</span>' in lab
    # score rules unchanged: 6 responses are scored, 3 hit the threshold and show N/A
    assert re.search(r'<strong>\d+\.\d\d%</strong>\s*<small>feedback score', main)
    assert re.search(r'<strong>N/A</strong>\s*<small>feedback score', lab)
    # category bars for both forms are present in both popups; the clicked one is marked
    for html in (main, lab):
        assert 'Main Faculty' in html and 'Lab Faculty' in html
    assert re.search(r'rd-cat is-current">\s*<div><span>Main Faculty', main)
    assert re.search(r'rd-cat is-current">\s*<div><span>Lab Faculty', lab)
    # controls kept
    for html, key in ((main, 'main'), (lab, 'lab')):
        assert 'data-rd-print' in html and html.count('data-rd-close') >= 2
        assert re.search(rf'href="/dean/actions/new[^"]*form_id={world[key]}"[^>]*>[^<]*Create improvement action', html)


def test_opening_the_popup_changes_nothing(app, dean, world):
    def counts():
        with app.app_context():
            return (db_module.query('SELECT COUNT(*) c FROM improvement_actions', one=True)['c'],
                    db_module.query('SELECT COUNT(*) c FROM submissions', one=True)['c'])
    before = counts()
    dean.get(f"/dean/performance/faculty/{world['teacher']}/result?form_id={world['lab']}")
    assert counts() == before


def test_popup_refuses_a_form_that_belongs_to_another_teacher(dean, world):
    r = dean.get(f"/dean/performance/faculty/{world['teacher']}/result?form_id={world['solo']}")
    assert r.status_code == 404


def test_performance_page_popup_still_wired(dean):
    html = dean.get('/dean/performance').get_data(as_text=True)
    assert html.count('id="result-dialog"') == 1 and 'dean-performance.js' in html
