"""HOD question sets: private per HOD, auto-saved, and the only source of form questions."""
import json
import re

from feedback_system import db, services
from feedback_system.security import hash_password

from helpers import login, token_from


def _seed_hod(app, username):
    with app.app_context():
        row = db.query("SELECT id FROM users WHERE username=? AND role='hod'", (username,), one=True)
        if row:
            return row['id']
        db.execute("""INSERT INTO users(role,username,emp_code,name,email,password_hash,is_active)
                      VALUES('hod',?,?,?,?,?,1)""",
                   (username, username, 'Test ' + username, username + '@test.edu',
                    hash_password('hodpass123')))
        uid = db.query("SELECT id FROM users WHERE username=?", (username,), one=True)['id']
        branch = db.query('SELECT id FROM branches LIMIT 1', one=True)
        db.execute('INSERT INTO user_department_scopes(user_id, branch_id) VALUES(?,?)',
                   (uid, branch['id']))
        db.execute('UPDATE users SET email_verified=1, onboarding_complete=1 WHERE id=?', (uid,))
        return uid


def _login(app, client, username):
    _seed_hod(app, username)
    r = login(client, '/hod/login', {'emp_code': username, 'password': 'hodpass123'})
    assert r.status_code == 302


def _api(client, url, body=None):
    tok = re.search(r'name="csrf-token" content="([^"]+)"',
                    client.get('/hod/question-sets').get_data(as_text=True)).group(1)
    return client.post(url, json=body or {}, headers={'X-CSRF-Token': tok,
                                                      'X-Requested-With': 'XMLHttpRequest'})


Q = {'text': 'How approachable is the teacher?', 'qtype': 'rating', 'is_required': True,
     'options': services.SCALE_PRESETS['five_point']['options']}


def test_create_set_autosave_and_reload(app, client):
    _login(app, client, 'HODQS1')
    r = _api(client, '/hod/api/question-sets', {'name': 'My Own Set'})
    assert r.status_code == 200
    sid = r.get_json()['id']
    # duplicate name refused
    assert _api(client, '/hod/api/question-sets', {'name': ' my own SET '}).status_code == 400
    r = _api(client, f'/hod/api/question-sets/{sid}/questions', {'questions': [Q]})
    assert r.get_json()['ok']
    page = client.get('/hod/question-sets').get_data(as_text=True)
    data = json.loads(re.search(r'id="qs-sets-data">(.*?)</script>', page, re.S).group(1))
    names = [s['name'] for s in data]
    assert names[0] == 'Common questions' and 'My Own Set' in names
    mine = [s for s in data if s['id'] == sid][0]
    assert mine['questions'][0]['text'] == Q['text'] and len(mine['questions'][0]['options']) == 5


def test_sets_are_private_to_each_hod(app, client):
    _login(app, client, 'HODQS2')
    sid = _api(client, '/hod/api/question-sets', {'name': 'Mine only'}).get_json()['id']
    other = app.test_client()
    _login(app, other, 'HODQS3')
    page = other.get('/hod/question-sets').get_data(as_text=True)
    assert 'Mine only' not in page
    assert _api(other, f'/hod/api/question-sets/{sid}/questions', {'questions': [Q]}).status_code == 404
    assert _api(other, f'/hod/api/question-sets/{sid}/delete').status_code == 404
    assert _api(other, f'/hod/api/question-sets/{sid}/rename', {'name': 'x'}).status_code == 400


def test_common_set_cannot_be_deleted_or_renamed(app, client):
    uid = _seed_hod(app, 'HODQS4')
    _login(app, client, 'HODQS4')
    with app.app_context():
        cid = services.ensure_common_set(uid)
    assert _api(client, f'/hod/api/question-sets/{cid}/delete').status_code == 404
    assert _api(client, f'/hod/api/question-sets/{cid}/rename', {'name': 'x'}).status_code == 400
    # but its questions can be edited (auto-save)
    assert _api(client, f'/hod/api/question-sets/{cid}/questions', {'questions': [Q]}).get_json()['ok']


def test_form_questions_must_come_from_own_sets(app):
    uid = _seed_hod(app, 'HODQS5')
    with app.app_context():
        sid, _ = services.create_question_set(uid, 'CRT Questions')
        services.save_set_questions(uid, sid, [Q])
        assert not services.questions_not_from_sets(uid, [Q])
        assert services.questions_not_from_sets(uid, [{**Q, 'text': 'Invented question'}])
        # another HOD's identical question is not accepted unless they own it too
        other = _seed_hod(app, 'HODQS6')
        assert services.questions_not_from_sets(other, [Q])


def test_form_page_lists_only_own_sets(app, client):
    uid = _seed_hod(app, 'HODQS7')
    with app.app_context():
        sid, _ = services.create_question_set(uid, 'Phase 2 Questions')
        services.save_set_questions(uid, sid, [Q])
    _login(app, client, 'HODQS7')
    html = client.get('/hod/forms/new').get_data(as_text=True)
    data = json.loads(re.search(r'id="hod-sets-data">(.*?)</script>', html, re.S).group(1))
    assert 'Phase 2 Questions' in [s['name'] for s in data]
    assert 'id="set-buttons"' in html and 'id="add-question"' not in html


def test_word_file_questions_are_shared_with_every_hod(app, client):
    shared = services.shared_question_sets()
    by = {s['name']: s for s in shared}
    assert len(by['Phase 1 Questions']['questions']) == 6
    assert len(by['Phase 2 Questions']['questions']) == 7
    assert len(by['Lab Questions (Phase 1 & 2)']['questions']) == 3
    crt = by['CRT Questions (Phase 1 & 2)']['questions']
    assert len(crt) == 7 and crt[-1]['qtype'] == 'text' and not crt[-1]['is_required']
    assert all(len(q['options']) == 5 for q in by['Phase 1 Questions']['questions'])
    # two different HODs both see them on the form page, and may use them in a form
    for name in ('HODQS8', 'HODQS9'):
        c = app.test_client()
        _login(app, c, name)
        html = c.get('/hod/forms/new').get_data(as_text=True)
        data = json.loads(re.search(r'id="hod-sets-data">(.*?)</script>', html, re.S).group(1))
        assert {'shared-phase1', 'shared-phase2', 'shared-lab', 'shared-crt'} <= {s['id'] for s in data}
        uid = _seed_hod(app, name)
        with app.app_context():
            assert not services.questions_not_from_sets(uid, by['Phase 1 Questions']['questions'])


def test_copy_shared_set_into_own_sets(app, client):
    _login(app, client, 'HODQS10')
    r = _api(client, '/hod/api/question-sets/copy-shared', {'key': 'shared-crt'})
    assert r.get_json()['ok']
    r2 = _api(client, '/hod/api/question-sets/copy-shared', {'key': 'shared-crt'})
    assert r2.get_json()['ok']                       # second copy gets a new name
    assert _api(client, '/hod/api/question-sets/copy-shared', {'key': 'nope'}).status_code == 400
    page = client.get('/hod/question-sets').get_data(as_text=True)
    mine = json.loads(re.search(r'id="qs-sets-data">(.*?)</script>', page, re.S).group(1))
    names = [s['name'] for s in mine]
    assert 'CRT Questions (Phase 1 & 2) (my copy)' in names
    assert 'CRT Questions (Phase 1 & 2) (my copy) 2' in names
    assert len([s for s in mine if 'my copy' in s['name']][0]['questions']) == 7


def test_extra_and_comment_questions_allowed_but_must_be_valid(app):
    uid = _seed_hod(app, 'HODQS11')
    five = services.SCALE_PRESETS['five_point']['options']
    with app.app_context():
        extra = {'text': 'Lab equipment adequate?', 'qtype': 'rating', 'is_required': True,
                 'options': five, 'custom': True}
        comment = {'text': 'Please share your comments or suggestions (optional)',
                   'qtype': 'text', 'is_required': False, 'options': [], 'custom': True}
        assert not services.questions_not_from_sets(uid, [extra, comment])
        # an invented question that is NOT flagged as an extra is still refused
        assert services.questions_not_from_sets(uid, [{**extra, 'custom': False}])
        # an extra needs text, and a rating extra needs at least two options
        assert services.questions_not_from_sets(uid, [{**extra, 'text': '  '}])
        assert services.questions_not_from_sets(uid, [{**extra, 'options': five[:1]}])
