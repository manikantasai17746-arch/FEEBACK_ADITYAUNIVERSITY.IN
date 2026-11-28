"""Per-teacher report: scoring on both scales, PDF content, access codes, scope."""
import io
import os
import tempfile

import pytest

from feedback_system import create_app, hod, scoring, services, teacher_report
from feedback_system import db
from feedback_system.security import hash_password

from helpers import token_from

OLD = [('Excellent', 5), ('Very Good', 4), ('Good', 3), ('Average', 2), ('Poor', 1)]
NEW = list(scoring.FIVE_POINT_SCALE)


@pytest.fixture()
def app():
    tmp = tempfile.mkdtemp(prefix='ffs-report-')
    return create_app({
        'TESTING': True, 'DATABASE': os.path.join(tmp, 'test.db'), 'DATABASE_URL': None,
        'SECRET_KEY': 'test-secret-report', 'AUTO_IMPORT': False,
        'ADMIN_EMAIL': 'admin@college.edu', 'ADMIN_PASSWORD': 'admin123',
        'REQUIRE_ACCOUNT_SETUP': False, 'LOGIN_MAX_ATTEMPTS': 3,
    })


def _insert(sql, args):
    db.execute(sql, args)
    table = sql.split()[2].split('(')[0]
    return db.query(f'SELECT MAX(rowid) AS i FROM {table}', one=True)['i']


def _user(role, emp):
    return _insert("""INSERT INTO users(role, username, emp_code, name, password_hash,
                      is_active, onboarding_complete) VALUES(?,?,?,?,?,1,1)""",
                   (role, emp, emp, f'Name {emp}', hash_password('pass12345')))


def _form(branch, teacher, category, scale, picks, comment=None, status='closed',
          subject='Maths'):
    """One form for one class; ``picks`` is one rating label per respondent."""
    sem = db.query('SELECT id FROM semesters WHERE number=1', one=True)
    sem = sem['id'] if sem else _insert(
        'INSERT INTO semesters(number, label) VALUES(?,?)', (1, 'Semester 1'))
    n = db.query('SELECT COUNT(*) AS c FROM feedback_forms', one=True)['c']
    tt = _insert('INSERT INTO timetables(branch_id, semester_id, code, name) VALUES(?,?,?,?)',
                 (branch, sem, f'BCA-T{n}', f'T{n}'))
    cs = _insert("""INSERT INTO class_sessions(timetable_id, subject_code, subject_name,
                     teacher_user_id) VALUES(?,?,?,?)""", (tt, f'S{n}', subject, teacher))
    fid = _insert("""INSERT INTO feedback_forms(title, branch_id, semester_id, status,
                      session_id, category, subject_name, phase) VALUES(?,?,?,?,?,?,?,?)""",
                  (f'F{n}', branch, sem, status, cs, category, subject, 'phase1'))
    db.execute('INSERT INTO form_sessions(form_id, session_id) VALUES(?,?)', (fid, cs))
    qid = _insert("""INSERT INTO form_questions(form_id, text, qtype, position)
                      VALUES(?,?,'rating',0)""", (fid, f'Question of {category}'))
    options = {label: _insert(
        'INSERT INTO question_options(question_id, label, value, position) VALUES(?,?,?,?)',
        (qid, label, value, i)) for i, (label, value) in enumerate(scale)}
    tq = _insert("""INSERT INTO form_questions(form_id, text, qtype, position)
                     VALUES(?,'Suggestions','text',1)""", (fid,))
    for i, label in enumerate(picks):
        student = _user('student', f'ROLL{n}X{i}')
        sub = _insert("""INSERT INTO submissions(form_id, session_id, student_user_id)
                          VALUES(?,?,?)""", (fid, cs, student))
        db.execute('INSERT INTO answers(submission_id, question_id, option_id) VALUES(?,?,?)',
                   (sub, qid, options[label]))
        if comment and i == 0:
            db.execute('INSERT INTO answers(submission_id, question_id, text_value) '
                       'VALUES(?,?,?)', (sub, tq, comment))
    return fid


@pytest.fixture()
def data(app):
    with app.app_context():
        a = _insert('INSERT INTO branches(code, name) VALUES(?,?)', ('AAA', 'A'))
        b = _insert('INSERT INTO branches(code, name) VALUES(?,?)', ('BBB', 'B'))
        t = _user('teacher', 'E100')
        other = _user('teacher', 'E200')
        _form(a, t, 'main', NEW, ['Excellent', 'Good', 'Below average'], 'Nice classes')
        _form(a, t, 'lab', OLD, ['Very Good', 'Good'], 'More lab time')
        _form(a, t, 'main', NEW, [], subject='Empty subject')
        _form(a, t, 'main', NEW, ['Poor'], status='open', subject='OpenSubject')
        _form(b, t, 'main', NEW, ['Good'], subject='OtherDeptSubject')
        _form(a, other, 'main', NEW, ['Poor'], subject='ColleagueSubject')
        h = _user('hod', 'H1')
    return {'a': a, 'b': b, 't': t, 'other': other, 'hod': h}


def _text(pdf):
    pdfplumber = pytest.importorskip('pdfplumber')
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        return '\n'.join(page.extract_text() or '' for page in doc.pages)


def test_scores_use_the_same_weight_by_position_on_both_scales(app):
    for scale in (OLD, NEW):
        question = {'new_scale': scoring.is_new_scale([{'label': l} for l, _ in scale])}
        shares = [scoring._answer_share(dict(question, label=l, value=v), 5) for l, v in scale]
        assert shares == [1.0, 0.75, 0.5, 0.375, 0.25]


def test_report_numbers_match_the_app(app, data):
    with app.app_context():
        entries = teacher_report.report_entries(data['t'], [data['a']])
        first = next(e for e in entries if e['subject'] == 'Maths' and e['students'] == 3)
        columns, rows = first['tables'][0]
        assert columns == ('Excellent', 'Good', 'Average', 'Below average', 'Poor')
        assert rows[0]['counts'] == [1, 1, 0, 1, 0]
        assert rows[0]['percent'] == pytest.approx(70.83)       # (100 + 75 + 37.5) / 3
        assert first['average'] == pytest.approx(70.83)
        assert first['section'].startswith('I SEM_Phase-I_BCA-T')
        empty = next(e for e in entries if e['subject'] == 'Empty subject')
        assert empty['students'] == 0 and empty['average'] is None


def test_pdf_has_all_categories_but_no_students_open_or_other_teachers(app, data):
    with app.app_context():
        entries = teacher_report.report_entries(data['t'], [data['a']])
        text = _text(teacher_report.build_pdf(
            [{'name': 'Name E100', 'emp_code': 'E100', 'entries': entries}]))
    for wanted in ('Main Faculty Feedback', 'Lab Faculty Feedback', 'Nice classes',
                   'More lab time', 'No responses', 'Very Good'):
        assert wanted in text
    for unwanted in ('ROLL', 'OpenSubject', 'OtherDeptSubject', 'ColleagueSubject', 'DIV/0'):
        assert unwanted not in text


def _post(app, emp, code):
    client = app.test_client()
    token = token_from(client, '/teacher-report')
    return client.post('/teacher-report',
                       data={'_csrf': token, 'emp_code': emp, 'access_code': code})


def test_public_download_needs_the_right_code(app, data):
    with app.app_context():
        code = teacher_report.access_code(data['t'], 'E100', [data['a']])
    good = _post(app, 'e100', code.lower())
    assert good.status_code == 200 and good.mimetype == 'application/pdf'
    assert good.headers['Cache-Control'] == 'no-store'
    # A code issued for department A never opens department B's feedback.
    text = _text(good.data)
    assert 'Maths' in text and 'OtherDeptSubject' not in text
    wrong = _post(app, 'E100', 'AAAAA-AAAAA')
    unknown = _post(app, 'NOPE', code)
    borrowed = _post(app, 'E200', code)
    assert {wrong.status_code, unknown.status_code, borrowed.status_code} == {403}
    message = 'Emp ID or access code is not correct'
    assert all(message in r.get_data(as_text=True) for r in (wrong, unknown, borrowed))


def test_public_download_is_rate_limited_and_needs_csrf(app, data):
    for _ in range(3):
        assert _post(app, 'E100', 'AAAAA-AAAAA').status_code == 403
    with app.app_context():
        code = teacher_report.access_code(data['t'], 'E100', [data['a']])
    assert _post(app, 'E100', code).status_code == 429
    assert app.test_client().post('/teacher-report',
                                  data={'emp_code': 'E100'}).status_code == 400


def test_hod_downloads_only_their_department(app, data, monkeypatch):
    monkeypatch.setattr(hod, '_hod_branches',
                        lambda user: [{'id': data['b'], 'code': 'BBB', 'name': 'B'}])
    client = app.test_client()
    with client.session_transaction() as session:
        session['uid'] = data['hod']
        session['role'] = 'hod'
    page = client.get('/hod/teacher-reports').get_data(as_text=True)
    assert 'Name E100' in page and 'Name E200' not in page
    one = _text(client.get(f"/hod/teacher-reports/{data['t']}.pdf").data)
    assert 'OtherDeptSubject' in one and 'Maths' not in one and 'Access code' in one
    assert client.get(f"/hod/teacher-reports/{data['other']}.pdf").status_code == 404
    everyone = client.get('/hod/teacher-reports/all.pdf')
    assert 'ColleagueSubject' not in _text(everyone.data)
    assert app.test_client().get('/hod/teacher-reports/all.pdf').status_code != 200


def test_old_saved_question_sets_offer_the_new_names(app):
    old = [{'label': l, 'value': v} for l, v in OLD]
    assert services._current_scale(old) == scoring.five_point_options()
    custom = [{'label': 'Yes', 'value': 2}, {'label': 'No', 'value': 1}]
    assert services._current_scale(custom) == custom


def test_second_faculty_feedback_goes_to_the_second_teacher_only(app, data):
    with app.app_context():
        fid = _form(data['a'], data['other'], 'second', NEW, ['Good'], 'Helpful in lab',
                    subject='AssistSubject')
        session_id = db.query('SELECT session_id FROM feedback_forms WHERE id=?',
                              (fid,), one=True)['session_id']
        sfa = _insert("""INSERT INTO second_faculty_assignments(branch_id, session_id,
                          subject_code, second_emp_code, second_teacher_user_id, status)
                          VALUES(?,?,'S','E100',?,'confirmed')""",
                      (data['a'], session_id, data['t']))
        db.execute('UPDATE feedback_forms SET second_faculty_assignment_id=? WHERE id=?',
                   (sfa, fid))
        mine = teacher_report.report_entries(data['t'], [data['a']])
        theirs = teacher_report.report_entries(data['other'], [data['a']])
        categories = [e['category'] for e in mine]
    assert 'AssistSubject' in [e['subject'] for e in mine]
    assert 'AssistSubject' not in [e['subject'] for e in theirs]
    # One document, in the order Main, Second, Lab.
    assert list(dict.fromkeys(categories)) == [
        'Main Faculty Feedback', 'Second Faculty Feedback', 'Lab Faculty Feedback']


def test_student_form_shows_marks_for_the_new_scale(app):
    from flask import render_template_string
    source = open(os.path.join(app.template_folder, 'student_form.html'),
                  encoding='utf-8').read()
    start = source.index('{# Rating / single choice')
    block = source[start:source.index('</label>', start)] + '</label>{% endfor %}'
    options = [{'id': i, 'label': l, 'value': v} for i, (l, v) in enumerate(NEW)]
    with app.test_request_context():
        html = render_template_string(
            '<div>' + block, saved='',
            q={'id': 1, 'text': 'Q', 'is_required': True, 'options': options})
    import re
    assert re.findall(r'\((\d) marks?\)', html) == ['5', '4', '3', '2', '1']
