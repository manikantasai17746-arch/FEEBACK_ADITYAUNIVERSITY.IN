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
                                'session_id': session_id, 'questions': questions},
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


def submit_feedback(client, form_id, rating_qid, text_qid, option_id, text='Nice class'):
    """Submit via the real student HTTP flow, returning the response."""
    html = client.get(f'/student/form/{form_id}').get_data(as_text=True)
    tok = get_csrf(html)
    data = {'_csrf': tok}
    if rating_qid:
        data[f'q_{rating_qid}'] = option_id
    if text_qid:
        data[f'q_{text_qid}'] = text
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


def login_student(client, roll):
    return login(client, '/', {'roll': roll, 'password': 'student@123'})
