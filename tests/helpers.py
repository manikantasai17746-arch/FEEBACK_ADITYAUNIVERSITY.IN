"""Test helpers shared across the suite."""
import re

from feedback_system import db, services


def get_csrf(html):
    m = (re.search(r'name="_csrf" value="([^"]+)"', html)
         or re.search(r'name="csrf-token" content="([^"]+)"', html))
    assert m, 'CSRF token not found in page'
    return m.group(1)


def token_from(client, path):
    return get_csrf(client.get(path).get_data(as_text=True))


def login(client, path, data, token_path=None):
    tok = token_from(client, token_path or path)
    return client.post(path, data={**data, '_csrf': tok})


def login_teacher(client, emp_code='6817', password='teacher@123', app=None):
    """Log in as a teacher and clear the forced password-change gate.

    Logging in with the shared default password sets must_change_password=1.
    First-login / account-setup flows are covered by dedicated tests; other
    tests need a teacher session that can reach the portal immediately.

    When *app* is provided, the user's password hash is reset to *password*
    first so earlier tests that mutated the shared session DB cannot break login.
    """
    if app is not None:
        from feedback_system.security import hash_password
        with app.app_context():
            db.execute(
                "UPDATE users SET password_hash=?, must_change_password=0, "
                "first_login_complete=1 WHERE emp_code=?",
                (hash_password(password), emp_code))
    r = login(client, '/teacher/login', {'emp_code': emp_code, 'password': password})
    if app is not None:
        with app.app_context():
            db.execute(
                "UPDATE users SET must_change_password=0, first_login_complete=1 "
                "WHERE emp_code=?",
                (emp_code,))
    return r


def find_session(app, emp_code, subject_code, timetable=None):
    with app.app_context():
        sql = """SELECT cs.id FROM class_sessions cs
                 JOIN users u ON u.id=cs.teacher_user_id
                 JOIN timetables t ON t.id=cs.timetable_id
                 WHERE u.emp_code=? AND cs.subject_code=?"""
        args = [emp_code, subject_code]
        if timetable:
            sql += ' AND t.code=?'
            args.append(timetable)
        row = db.query(sql + ' LIMIT 1', tuple(args), one=True)
    return row['id'] if row else None


def eligible_rolls(app, session_id):
    with app.app_context():
        return [r['roll'] for r in services.eligible_students(session_id)]


def future_deadline(days=7):
    """A local ``datetime-local`` string ``days`` ahead (what the form field posts)."""
    from datetime import datetime, timedelta
    return (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%dT%H:%M')


DEFAULT_QUESTIONS = [
    {'text': 'How well does the teacher explain?', 'qtype': 'rating', 'is_required': True,
     'options': [{'label': 'Excellent', 'value': 1}, {'label': 'Good', 'value': 2},
                 {'label': 'Bad', 'value': 3}, {'label': 'Very poor', 'value': 4}]},
    {'text': 'Suggestions', 'qtype': 'text', 'is_required': False, 'options': []},
]


def make_form(admin_client, session_id, title='Test form', questions=None):
    questions = questions or DEFAULT_QUESTIONS
    tok = get_csrf(admin_client.get('/admin/forms/new').get_data(as_text=True))
    r = admin_client.post('/admin/forms/save',
                          json={'form_id': None, 'title': title, 'instructions': 'Anon',
                                'session_id': session_id, 'questions': questions,
                                'deadline_at': future_deadline()},
                          headers={'X-CSRF-Token': tok, 'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code == 200 and r.get_json()['ok'], r.get_data(as_text=True)
    return r.get_json()['form_id']


def publish_form(admin_client, form_id):
    tok = token_from(admin_client, '/admin/forms')
    return admin_client.post(f'/admin/forms/{form_id}/status',
                             data={'_csrf': tok, 'status': 'open'})


def set_status(admin_client, form_id, status):
    tok = token_from(admin_client, '/admin/forms')
    return admin_client.post(f'/admin/forms/{form_id}/status',
                             data={'_csrf': tok, 'status': status})


def give_feedback_code(client):
    """Step C test shortcut: mark this login session as having given the authenticator code.

    The real code check is covered by tests/test_feedback_gate_stepc.py. Here only the
    session flag is set, because a real code can be used once per 30-second window and
    the suite logs students in many times.
    """
    with client.session_transaction() as sess:
        sess['feedback_code_ok'] = True


def submit_feedback(client, form_id, rating_qid, text_qid, option_id, text='Nice class'):
    """Submit via the real student HTTP flow, returning the response."""
    give_feedback_code(client)
    html = client.get(f'/student/form/{form_id}').get_data(as_text=True)
    tok = get_csrf(html)
    data = {'_csrf': tok}
    if rating_qid:
        data[f'q_{rating_qid}'] = option_id
    if text_qid:
        data[f'q_{text_qid}'] = text
        if (text or '').strip():
            data[f'qs_{text_qid}'] = '4'     # a written comment needs its star rating
    return client.post(f'/student/form/{form_id}/submit', data=data)


def question_ids(html):
    """Return (rating_qid, text_qid, first-option-id) parsed from a form page."""
    import re
    rating = re.search(r'name="q_(\d+)"', html)
    opts = re.findall(r'name="q_(\d+)"', html)
    ids = sorted({int(x) for x in opts})
    # first question with radio options is the rating; last is the text box
    rating_qid = ids[0] if ids else None
    text_qid = ids[-1] if len(ids) > 1 else None
    opt_ids = re.findall(r'type="radio" name="q_%d" value="(\d+)"' % rating_qid, html) if rating_qid else []
    return rating_qid, text_qid, (opt_ids[0] if opt_ids else None)


# C1.1: students created at import keep the shared default, which now forces a password
# change before any protected page. Tests use a personal password unless they test the default.
STUDENT_PERSONAL_PASSWORD = 'Personal-test-pass1'


def ensure_personal_student_password(app, roll):
    """Give a test student a personal password (and clear the forced-change flag)."""
    from feedback_system.security import hash_password
    with app.app_context():
        db.execute("UPDATE users SET password_hash=?, must_change_password=0 "
                   "WHERE role='student' AND username=?",
                   (hash_password(STUDENT_PERSONAL_PASSWORD), roll))


def login_student(client, roll):
    """Sign a student in through the Microsoft callback (the only student sign-in).

    Microsoft itself is replaced by stubs: the callback receives a verified college e-mail
    ``<roll>@<STUDENT_EMAIL_DOMAIN>`` exactly as it would after a real sign-in.
    """
    from unittest import mock
    from feedback_system import sso
    email = f"{roll.lower()}@{client.application.config['STUDENT_EMAIL_DOMAIN']}"
    with client.session_transaction() as s:
        s['student_sso'] = {'state': 'test-state', 'nonce': 'test-nonce', 'next': None}
    with mock.patch.object(sso, 'student_microsoft_ready', return_value=True), \
            mock.patch.object(sso, 'client_credentials', return_value=('cid', 'secret')), \
            mock.patch.object(sso, 'fetch_claims', return_value={}), \
            mock.patch.object(sso, 'verified_email', return_value=email):
        return client.get('/student/sso/callback', query_string={'state': 'test-state', 'code': 'c'})
