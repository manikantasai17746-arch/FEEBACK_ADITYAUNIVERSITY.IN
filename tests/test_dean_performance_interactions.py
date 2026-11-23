"""Dean Performance interactions - synthetic data and a recording mail transport only.

Nothing here talks to SMTP: ``feedback_system.mailer.send_email`` is replaced by an
in-memory outbox (optionally failing).  Covers the result popup (scope, real data,
anonymity), Mail teacher, Email HOD about ticked teachers, the optional improvement
action, validation, delivery failure, duplicate submission and Dean scope isolation.
"""
import os
import re
import tempfile

import pytest

from feedback_system import create_app
from feedback_system import db as db_module
from helpers import get_csrf, login
from test_dean_hod_scope import _form, _user, SCALE  # noqa: F401  (synthetic builders)

BAND = '95-100'          # every 'Excellent' teacher below scores 100


@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-deanperf-')
    yield create_app({
        'TESTING': True, 'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None, 'SECRET_KEY': 'test-secret-dean-perf',
        'AUTO_IMPORT': False, 'ADMIN_EMAIL': 'admin@college.edu',
        'ADMIN_PASSWORD': 'admin123', 'REQUIRE_ACCOUNT_SETUP': False,
        'LOGIN_MAX_ATTEMPTS': 50,
    })


@pytest.fixture(scope='module')
def world(app):
    with app.app_context():
        db_module.execute("INSERT INTO academic_years(name, is_active) VALUES('2026-27', 1)")
        ay = db_module.query("SELECT id FROM academic_years", one=True)['id']
        db_module.execute("INSERT INTO semesters(number, label) VALUES(3, 'Semester 3')")
        sem = db_module.query('SELECT id FROM semesters', one=True)['id']
        w = {'ay': ay, 'sem': sem}
        for code in ('CSE', 'MECH'):
            db_module.execute('INSERT INTO branches(code, name) VALUES(?,?)', (code, code + ' Dept'))
            w[code] = db_module.query('SELECT id FROM branches WHERE code=?', (code,), one=True)['id']
        tt = {}
        for code, br in (('CSE-A', 'CSE'), ('CSE-B', 'CSE'), ('MECH-A', 'MECH')):
            tt[code] = db_module.insert_id(
                'INSERT INTO timetables(branch_id, semester_id, code, name) VALUES(?,?,?,?)',
                (w[br], sem, code, code))
        studs = [_user('student', f'PS{i}') for i in range(9)]
        w['teachers'], w['forms'] = {}, {}
        plan = [('TA1', 'CSE', 'CSE-A'), ('TA3', 'CSE', 'CSE-A'), ('TB1', 'CSE', 'CSE-B'),
                ('TM1', 'MECH', 'MECH-A')]
        for code, br, tcode in plan:
            tid = _user('teacher', code, f'Teacher {code}')
            w['teachers'][code] = tid
            w['forms'][code] = _form(w[br], ay, sem, tt[tcode], tid, studs, 'Excellent', f'S-{code}')
        # a Lab form for TA1 (second category bar in the popup)
        w['lab'] = _form(w['CSE'], ay, sem, tt['CSE-A'], w['teachers']['TA1'], studs[:3],
                         'Good', 'L-TA1')
        db_module.execute("UPDATE feedback_forms SET category='lab' WHERE id=?", (w['lab'],))
        # anonymous comments (text + stars) on TA1's main form
        fid = w['forms']['TA1']
        qid = db_module.insert_id(
            "INSERT INTO form_questions(form_id, position, text, qtype) VALUES(?,?,?,'text')",
            (fid, 2, 'Suggestions'))
        subs = db_module.query('SELECT id FROM submissions WHERE form_id=? ORDER BY id', (fid,))
        stars = [5, 5, 4, 3, 3, 2, 1, None]
        for sub, st in zip(subs, stars):
            db_module.execute(
                'INSERT INTO answers(submission_id, question_id, text_value, comment_stars)'
                ' VALUES(?,?,?,?)', (sub['id'], qid, f'Comment text star {st}', st))
        w['hodA'] = _user('hod', 'PHODA', 'Dr. Alpha')
        w['hodB'] = _user('hod', 'PHODB', 'Dr. Beta')
        w['hodM'] = _user('hod', 'PHODM', 'Dr. Mech')
        for hid, br, t in ((w['hodA'], 'CSE', 'CSE-A'), (w['hodB'], 'CSE', 'CSE-B'),
                           (w['hodM'], 'MECH', 'MECH-A')):
            db_module.execute(
                """INSERT INTO hod_allocations(hod_user_id, academic_year_id, branch_id,
                       semester_id, timetable_id) VALUES(?,?,?,?,?)""",
                (hid, ay, w[br], sem, tt[t]))
        w['dean'] = _user('dean', 'PDEAN1', 'Dean One')
        w['dean2'] = _user('dean', 'PDEAN2', 'Dean Two')
        for dean, hods in ((w['dean'], (w['hodA'], w['hodB'])), (w['dean2'], (w['hodM'],))):
            for h in hods:
                db_module.execute(
                    'INSERT INTO dean_hod_assignments(dean_user_id, hod_user_id) VALUES(?,?)',
                    (dean, h))
        return w


@pytest.fixture()
def outbox(monkeypatch):
    """Test mail transport: records messages, can be told to fail. Never touches SMTP."""
    from feedback_system import mailer
    box = type('Box', (), {})()
    box.sent, box.fail_for, box.raise_for = [], set(), set()

    def fake(to, subject, text, html=None, attachments=None):
        if to in box.raise_for:
            raise RuntimeError('smtp exploded')
        if to in box.fail_for:
            return False
        box.sent.append({'to': to, 'subject': subject, 'text': text})
        return True
    monkeypatch.setattr(mailer, 'send_email', fake)
    return box


def _login(app, code):
    c = app.test_client()
    assert login(c, '/dean/login', {'emp_code': code, 'password': 'pass12345'}).status_code == 302
    return c


def _band_page(c, band=BAND):
    return c.get(f'/dean/performance?band={band}').get_data(as_text=True)


def _post(c, path, data, page=None, json=True):
    tok = get_csrf(page or _band_page(c))
    headers = {'X-Requested-With': 'fetch'} if json else {}
    r = c.post(path, data={**data, '_csrf': tok}, headers=headers)
    return r


def _actions(app):
    with app.app_context():
        return [dict(a) for a in db_module.query(
            'SELECT * FROM improvement_actions ORDER BY id')]


def _clear_actions(app):
    with app.app_context():
        db_module.execute('DELETE FROM improvement_action_log')
        db_module.execute('DELETE FROM improvement_actions')


SEND = '/dean/performance/bands/send'
HODMAIL = '/dean/performance/bands/email-hod'


def _teacher_payload(w, code='TA1', **extra):
    d = {'band': BAND, 'form_id': str(w['forms'][code]), 'require_text': '1',
         'subject': 'Your result', 'message': 'Dear {name}, {score} for {subject}. {dean}',
         'submit_token': f'tok-{code}-{len(extra)}-{os.urandom(3).hex()}'}
    d.update(extra)
    return d


# --------------------------------------------------------------------------- #
# 1. Result popup
# --------------------------------------------------------------------------- #
def test_popup_shows_real_scores_questions_and_comment_tags(app, world):
    c = _login(app, 'PDEAN1')
    t, f = world['teachers']['TA1'], world['forms']['TA1']
    r = c.get(f'/dean/performance/faculty/{t}/result?form_id={f}')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Teacher TA1' in html and '100.00%' in html          # real score
    assert 'Score by feedback category' in html
    assert 'Main Faculty' in html and 'Lab Faculty' in html      # real categories (main + lab)
    assert 'Question-wise results' in html and 'Q1' in html      # real question
    assert 'data-page="5"' in html                               # Show more pages of 5
    # tags from real stars: 3 positive, 2 neutral, 2 needs improvement, 1 not rated
    assert 'All (8)' in html and 'Positive (3)' in html
    assert 'Neutral (2)' in html and 'Needs improvement (2)' in html
    assert html.count('data-tag="pos"') == 3 and html.count('data-tag="unrated"') == 1
    assert 'data-rd-print' in html and html.count('data-rd-close') >= 2


def test_popup_does_not_invent_or_leak(app, world):
    c = _login(app, 'PDEAN1')
    t, f = world['teachers']['TA1'], world['forms']['TA1']
    html = c.get(f'/dean/performance/faculty/{t}/result?form_id={f}').get_data(as_text=True)
    # no data for these exists in the application: not shown (gap, not faked)
    assert 'Flagged' not in html and 'Section ' not in html
    assert not re.search(r'\b\d{1,2} Nov\b', html)
    # anonymity: no respondent identity
    for i in range(9):
        assert f'PS{i}' not in html
    assert 'Anonymous student' in html


def test_grid_and_band_cards_open_the_popup(app, world):
    c = _login(app, 'PDEAN1')
    grid = c.get('/dean/performance').get_data(as_text=True)
    cards = c.get(f'/dean/performance?band={BAND}').get_data(as_text=True)
    for html in (grid, cards):
        assert 'data-result-url="/dean/performance/faculty/' in html
        assert 'id="result-dialog"' in html and 'dean-performance.js' in html
        assert re.search(r'data-result-url="[^"]+"\s+href="/dean/performance/faculty/\d+"', html)  # no-JS fallback


def test_performance_category_filters_cards_counts_bands_and_mail(app, world, outbox):
    from flask import template_rendered
    c = _login(app, 'PDEAN1')
    forms = world['forms']
    second = forms['TA3']
    with app.app_context():
        assignment = db_module.insert_id(
            "INSERT INTO second_faculty_assignments(subject_code, second_teacher_user_id)"
            " VALUES(?, ?)", ('S-TA3', world['teachers']['TA3']))
        db_module.execute(
            "UPDATE feedback_forms SET category='second', second_faculty_assignment_id=? WHERE id=?",
            (assignment, second))
    try:
        expected = {'main': {forms['TA1'], forms['TB1']}, 'lab': {world['lab']},
                    'second': {second}}
        expected[''] = set.union(*expected.values())
        contexts = []

        def capture(sender, template, context, **extra):
            contexts.append(context)

        with template_rendered.connected_to(capture, app):
            for category, ids in expected.items():
                page = c.get('/dean/performance', query_string={'category': category})
                assert page.status_code == 200
                ctx = contexts[-1]
                assert {r['form_id'] for r in ctx['rows']} == ids
                assert ctx['summary']['forms'] == len(ids)
                assert {r['form_id'] for b in ctx['bands'] for r in b['rows']} == ids
            c.get('/dean/performance', query_string={'category': 'main', 'hod_id': world['hodA']})
            assert {r['form_id'] for r in contexts[-1]['rows']} == {forms['TA1']}
        payload = _teacher_payload(world, code='TA3', category='main')
        resp = _post(c, SEND, payload)
        assert resp.status_code == 422 and not outbox.sent
    finally:
        with app.app_context():
            db_module.execute("UPDATE feedback_forms SET category='main', second_faculty_assignment_id=NULL WHERE id=?",
                              (second,))
            db_module.execute('DELETE FROM second_faculty_assignments WHERE id=?', (assignment,))


def test_popup_scope_is_enforced(app, world):
    c = _login(app, 'PDEAN1')
    tm, fm = world['teachers']['TM1'], world['forms']['TM1']
    ta, fa = world['teachers']['TA1'], world['forms']['TA1']
    assert c.get(f'/dean/performance/faculty/{tm}/result').status_code == 404       # other Dean's teacher
    assert c.get(f'/dean/performance/faculty/{tm}/result?form_id={fm}').status_code == 404
    assert c.get(f'/dean/performance/faculty/{ta}/result?form_id={fm}').status_code == 404  # foreign form id
    assert c.get(f'/dean/performance/faculty/{ta}/result?form_id=999999').status_code == 404
    assert c.get('/dean/performance/faculty/999999/result').status_code == 404
    page = c.get(f'/dean/performance/faculty/{tm}')                                  # full page too
    assert page.status_code == 404 and 'Teacher TM1' not in page.get_data(as_text=True)
    c2 = _login(app, 'PDEAN2')
    assert c2.get(f'/dean/performance/faculty/{tm}/result?form_id={fm}').status_code == 200
    assert c2.get(f'/dean/performance/faculty/{ta}/result').status_code == 404


def test_popup_refuses_other_roles_and_anonymous(app, world):
    t = world['teachers']['TA1']
    assert app.test_client().get(f'/dean/performance/faculty/{t}/result').status_code in (302, 401, 403)
    c = app.test_client()
    assert login(c, '/teacher/login', {'emp_code': 'TA1', 'password': 'pass12345'}).status_code in (200, 302)
    assert c.get(f'/dean/performance/faculty/{t}/result').status_code in (302, 401, 403)


# --------------------------------------------------------------------------- #
# 2. Mail teacher (+ optional action)
# --------------------------------------------------------------------------- #
def test_band_page_has_mail_controls(app, world):
    html = _band_page(_login(app, 'PDEAN1'))
    assert html.count('data-mail-teacher') == 3                 # TA1, TA3, TB1 (not the other Dean's)
    assert 'data-open-hod' in html and 'id="mail-dialog"' in html
    assert 'data-hod-id="%d"' % world['hodA'] in html and 'data-hod-id="%d"' % world['hodB'] in html
    assert 'onsubmit=' not in html                               # CSP-safe: no inline handler
    assert 'Dr. Mech' not in html


def test_mail_teacher_only(app, world, outbox):
    _clear_actions(app)
    c = _login(app, 'PDEAN1')
    r = _post(c, SEND, _teacher_payload(world))
    j = r.get_json()
    assert r.status_code == 200 and j['sent'] == 1 and j['level'] == 'success' and j['ok']
    assert [m['to'] for m in outbox.sent] == ['ta1@t.edu']
    assert 'Teacher TA1' in outbox.sent[0]['text'] and 'Dean One' in outbox.sent[0]['text']
    assert j['actions_created'] == 0 and j['actions_url'] is None
    assert _actions(app) == []


def test_mail_teacher_with_action_owner_priority_due(app, world, outbox):
    _clear_actions(app)
    c = _login(app, 'PDEAN1')
    r = _post(c, SEND, _teacher_payload(
        world, create_action='1', priority='critical', due_date='2099-01-31',
        owner_user_id=str(world['hodA'])))
    j = r.get_json()
    assert j['sent'] == 1 and j['actions_created'] == 1 and j['level'] == 'success'
    acts = _actions(app)
    assert len(acts) == 1
    a = acts[0]
    assert (a['teacher_user_id'], a['form_id']) == (world['teachers']['TA1'], world['forms']['TA1'])
    assert a['owner_user_id'] == world['hodA'] and a['priority'] == 'critical'
    assert a['due_date'] == '2099-01-31' and a['status'] == 'planned'
    assert a['branch_id'] == world['CSE']
    # the existing assignment notice reached the owning HOD
    assert sorted(m['to'] for m in outbox.sent) == ['phoda@t.edu', 'ta1@t.edu']
    # working "View in Actions" link
    assert j['actions_url'] == f"/dean/actions?new={a['id']}"
    page = c.get(j['actions_url'])
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and f'id="action-{a["id"]}"' in html and 'is-new' in html


def test_action_owner_defaults_to_the_teachers_hod(app, world, outbox):
    _clear_actions(app)
    c = _login(app, 'PDEAN1')
    j = _post(c, SEND, _teacher_payload(world, code='TB1', create_action='1')).get_json()
    assert j['actions_created'] == 1
    assert _actions(app)[0]['owner_user_id'] == world['hodB']       # TB1 -> HOD B, not HOD A
    assert _actions(app)[0]['priority'] == 'high'                     # default


def test_owner_must_be_a_hod_of_this_dean_and_department(app, world, outbox):
    _clear_actions(app)
    c = _login(app, 'PDEAN1')
    r = _post(c, SEND, _teacher_payload(world, create_action='1',
                                        owner_user_id=str(world['hodM'])))   # other Dean's HOD
    assert r.status_code == 422 and 'Invalid HOD' in r.get_json()['message']
    assert _actions(app) == []


@pytest.mark.parametrize('extra,needle', [
    ({'subject': '  '}, 'Enter a subject'),
    ({'message': ''}, 'Enter a message'),
    ({'create_action': '1', 'priority': 'urgent!'}, 'priority'),
    ({'create_action': '1', 'due_date': '2000-01-01'}, 'past'),
    ({'create_action': '1', 'due_date': 'tomorrow'}, 'valid date'),
])
def test_validation_blocks_sending(app, world, outbox, extra, needle):
    _clear_actions(app)
    c = _login(app, 'PDEAN1')
    r = _post(c, SEND, _teacher_payload(world, **extra))
    assert r.status_code == 422 and needle in r.get_json()['message']
    assert outbox.sent == [] and _actions(app) == []


def test_empty_selection_is_rejected(app, world, outbox):
    c = _login(app, 'PDEAN1')
    for path in (SEND, HODMAIL):
        r = _post(c, path, {'band': BAND, 'require_text': '1', 'subject': 's', 'message': 'm',
                            'submit_token': os.urandom(4).hex()})
        assert r.status_code == 422 and 'Tick at least one teacher' in r.get_json()['message']
        r = _post(c, path, {'band': BAND, 'form_id': 'abc', 'subject': 's', 'message': 'm'})
        assert r.status_code == 422
    assert outbox.sent == []


def test_delivery_failure_is_reported_and_creates_no_action(app, world, outbox):
    _clear_actions(app)
    c = _login(app, 'PDEAN1')
    outbox.fail_for.add('ta1@t.edu')
    j = _post(c, SEND, _teacher_payload(world, create_action='1')).get_json()
    assert j['sent'] == 0 and j['failed'] == 1 and j['level'] == 'error' and not j['ok']
    assert j['actions_created'] == 0 and j['actions_url'] is None
    assert 'could not be sent' in j['message'] and 'No action was created' in j['message']
    assert _actions(app) == []
    outbox.fail_for.clear(); outbox.raise_for.add('ta1@t.edu')          # transport raising
    j = _post(c, SEND, _teacher_payload(world)).get_json()
    assert j['failed'] == 1 and j['sent'] == 0


def test_partial_failure_counts_are_exact(app, world, outbox):
    _clear_actions(app)
    c = _login(app, 'PDEAN1')
    outbox.fail_for.add('ta3@t.edu')
    data = _teacher_payload(world, create_action='1')
    data['form_id'] = [str(world['forms'][k]) for k in ('TA1', 'TA3', 'TB1')]
    j = _post(c, SEND, data).get_json()
    assert (j['sent'], j['failed']) == (2, 1) and j['level'] == 'warning'
    assert j['actions_created'] == 2                               # only the two delivered
    owners = {a['teacher_user_id']: a['owner_user_id'] for a in _actions(app)}
    assert owners == {world['teachers']['TA1']: world['hodA'], world['teachers']['TB1']: world['hodB']}


def test_hod_assignment_notice_failure_is_warned_not_hidden(app, world, outbox):
    """Teacher mails and HOD assignment notices are counted separately.

    Alpha's notice fails (HOD A is the owner of TA1 and TA3), Beta's is delivered.
    The actions still exist, but the Dean must be told a HOD was not notified."""
    _clear_actions(app)
    c = _login(app, 'PDEAN1')
    outbox.fail_for.add('phoda@t.edu')
    data = _teacher_payload(world, create_action='1')
    data['form_id'] = [str(world['forms'][k]) for k in ('TA1', 'TA3', 'TB1')]
    j = _post(c, SEND, data).get_json()
    assert (j['sent'], j['failed']) == (3, 0)                      # teacher mail: all delivered
    assert j['actions_created'] == 3                               # actions are kept
    assert (j['hods_notified'], j['hod_notify_failed']) == (1, 2)  # HOD notices counted apart
    assert j['level'] == 'warning' and j['ok'] is True
    assert '2 HODs could not be notified' in j['message']
    assert sorted(m['to'] for m in outbox.sent if m['to'].startswith('phod')) == ['phodb@t.edu']
    # all notices delivered -> no warning at all
    _clear_actions(app); outbox.fail_for.clear(); outbox.sent.clear()
    j = _post(c, SEND, dict(data, submit_token='tok-clean-' + os.urandom(3).hex())).get_json()
    assert j['hod_notify_failed'] == 0 and j['level'] == 'success'
    assert 'could not be notified' not in j['message']


def test_teacher_without_email_is_skipped(app, world, outbox):
    _clear_actions(app)
    with app.app_context():
        db_module.execute("UPDATE users SET email='' WHERE username='TA3'")
    try:
        c = _login(app, 'PDEAN1')
        data = _teacher_payload(world, create_action='1')
        data['form_id'] = [str(world['forms'][k]) for k in ('TA1', 'TA3')]
        j = _post(c, SEND, data).get_json()
        assert (j['sent'], j['no_email']) == (1, 1) and j['actions_created'] == 1
        assert [m['to'] for m in outbox.sent if m['to'].startswith('t')] == ['ta1@t.edu']
    finally:
        with app.app_context():
            db_module.execute("UPDATE users SET email='ta3@t.edu' WHERE username='TA3'")


def test_duplicate_submission_is_not_repeated(app, world, outbox):
    _clear_actions(app)
    c = _login(app, 'PDEAN1')
    page = _band_page(c)
    data = _teacher_payload(world, create_action='1', submit_token='same-token-1')
    first = _post(c, SEND, data, page).get_json()
    again = _post(c, SEND, data, page).get_json()             # double click / re-post
    assert first['sent'] == 1 and first['duplicate'] is False
    assert again['duplicate'] is True and again['actions_created'] == 1
    assert len([m for m in outbox.sent if m['to'] == 'ta1@t.edu']) == 1
    assert len(_actions(app)) == 1
    # a NEW submission while the action is still open: mail goes out, action is not doubled
    third = _post(c, SEND, _teacher_payload(world, create_action='1'), page).get_json()
    assert third['sent'] == 1 and third['actions_created'] == 0 and third['actions_skipped_open'] == 1
    assert 'open action already exists' in third['message'] and third['actions_url'] is None
    assert len(_actions(app)) == 1


def test_no_javascript_fallback_redirects_with_flash(app, world, outbox):
    c = _login(app, 'PDEAN1')
    r = _post(c, SEND, _teacher_payload(world), json=False)
    assert r.status_code == 302 and '/dean/performance' in r.headers['Location']
    assert 'Email delivered to 1 teacher' in c.get(r.headers['Location']).get_data(as_text=True)


def test_csrf_is_required(app, world, outbox):
    c = _login(app, 'PDEAN1')
    for path in (SEND, HODMAIL):
        r = c.post(path, data=_teacher_payload(world), headers={'X-Requested-With': 'fetch'})
        assert r.status_code in (400, 403)
    assert outbox.sent == []


# --------------------------------------------------------------------------- #
# 2b. Email HOD about ticked teachers
# --------------------------------------------------------------------------- #
def _hod_payload(w, codes, **extra):
    d = {'band': BAND, 'form_id': [str(w['forms'][k]) for k in codes],
         'subject': 'Review {count} teacher(s)',
         'message': 'Dear {hod}, please review:\n{teachers}\n{dean}',
         'submit_token': os.urandom(5).hex()}
    d.update(extra)
    return d


def test_email_hod_groups_ticked_teachers_by_their_real_hod(app, world, outbox):
    _clear_actions(app)
    c = _login(app, 'PDEAN1')
    j = _post(c, HODMAIL, _hod_payload(world, ('TA1', 'TA3', 'TB1'))).get_json()
    assert j['sent'] == 2 and j['level'] == 'success'
    by_to = {m['to']: m for m in outbox.sent}
    assert set(by_to) == {'phoda@t.edu', 'phodb@t.edu'}
    a, b = by_to['phoda@t.edu']['text'], by_to['phodb@t.edu']['text']
    assert 'Dr. Alpha' in a and 'Teacher TA1' in a and 'Teacher TA3' in a and 'Teacher TB1' not in a
    assert 'Dr. Beta' in b and 'Teacher TB1' in b and 'Teacher TA1' not in b
    assert by_to['phoda@t.edu']['subject'] == 'Review 2 teacher(s)'
    assert {g['hod']: sorted(g['teachers']) for g in j['groups']} == {
        'Dr. Alpha': ['Teacher TA1', 'Teacher TA3'], 'Dr. Beta': ['Teacher TB1']}
    assert _actions(app) == []                                     # action is optional


def test_email_hod_list_is_always_included(app, world, outbox):
    c = _login(app, 'PDEAN1')
    _post(c, HODMAIL, _hod_payload(world, ('TA1',), message='Please look at this.'))
    assert 'Teacher TA1' in outbox.sent[0]['text'] and 'Teachers:' in outbox.sent[0]['text']


def test_email_hod_with_actions_owner_priority_due_and_dedupe(app, world, outbox):
    _clear_actions(app)
    c = _login(app, 'PDEAN1')
    j = _post(c, HODMAIL, _hod_payload(world, ('TA1', 'TA3', 'TB1'), create_action='1',
                                       priority='medium', due_date='2099-02-01')).get_json()
    assert j['actions_created'] == 3 and j['actions_url'].startswith('/dean/actions?new=')
    owners = {a['teacher_user_id']: (a['owner_user_id'], a['priority'], a['due_date'])
              for a in _actions(app)}
    t = world['teachers']
    assert owners == {t['TA1']: (world['hodA'], 'medium', '2099-02-01'),
                      t['TA3']: (world['hodA'], 'medium', '2099-02-01'),
                      t['TB1']: (world['hodB'], 'medium', '2099-02-01')}
    assert c.get(j['actions_url']).status_code == 200
    again = _post(c, HODMAIL, _hod_payload(world, ('TA1', 'TA3', 'TB1'), create_action='1')).get_json()
    assert again['actions_created'] == 0 and again['actions_skipped_open'] == 3
    assert len(_actions(app)) == 3


def test_email_hod_delivery_failure_and_missing_email(app, world, outbox):
    _clear_actions(app)
    c = _login(app, 'PDEAN1')
    outbox.fail_for.add('phoda@t.edu')
    j = _post(c, HODMAIL, _hod_payload(world, ('TA1', 'TB1'), create_action='1')).get_json()
    assert (j['sent'], j['failed']) == (1, 1) and j['level'] == 'warning'
    assert [a['owner_user_id'] for a in _actions(app)] == [world['hodB']]   # none for the failed HOD
    outbox.fail_for.clear(); _clear_actions(app)
    with app.app_context():
        db_module.execute("UPDATE users SET email='' WHERE username='PHODB'")
    try:
        j = _post(c, HODMAIL, _hod_payload(world, ('TA1', 'TB1'), create_action='1')).get_json()
        assert (j['sent'], j['no_email']) == (1, 1)
        assert {g['hod']: g['status'] for g in j['groups']} == {'Dr. Alpha': 'sent', 'Dr. Beta': 'no_email'}
        assert [a['owner_user_id'] for a in _actions(app)] == [world['hodA']]
    finally:
        with app.app_context():
            db_module.execute("UPDATE users SET email='phodb@t.edu' WHERE username='PHODB'")


def test_email_hod_requires_text_and_dedupes_double_submit(app, world, outbox):
    c = _login(app, 'PDEAN1')
    r = _post(c, HODMAIL, _hod_payload(world, ('TA1',), subject=''))
    assert r.status_code == 422 and outbox.sent == []
    page = _band_page(c)
    data = _hod_payload(world, ('TA1',), submit_token='hod-same')
    first = _post(c, HODMAIL, data, page).get_json()
    again = _post(c, HODMAIL, data, page).get_json()
    assert first['sent'] == 1 and again['duplicate'] is True
    assert len(outbox.sent) == 1


# --------------------------------------------------------------------------- #
# 3. Scope
# --------------------------------------------------------------------------- #
def test_dean_cannot_contact_or_act_outside_scope(app, world, outbox):
    _clear_actions(app)
    c = _login(app, 'PDEAN1')
    tm_form = str(world['forms']['TM1'])
    for path in (SEND, HODMAIL):
        d = _teacher_payload(world, create_action='1')
        d['form_id'] = [tm_form]                        # the other Dean's teacher
        r = _post(c, path, d)
        assert r.status_code == 422 and 'None of the ticked teachers' in r.get_json()['message']
    # mixed: the foreign id is ignored and reported, own teacher still handled
    d = _teacher_payload(world)
    d['form_id'] = [str(world['forms']['TA1']), tm_form]
    j = _post(c, SEND, d).get_json()
    assert j['sent'] == 1 and j['ignored'] == 1 and j['level'] == 'warning'
    assert [m['to'] for m in outbox.sent] == ['ta1@t.edu']
    # a forged department / HOD filter does not widen scope
    d = _teacher_payload(world, code='TM1', branch_id=str(world['MECH']), hod_id=str(world['hodM']))
    assert _post(c, SEND, d).status_code == 422
    assert _actions(app) == []


def test_other_dean_sees_only_own_cards_and_hods(app, world, outbox):
    _clear_actions(app)
    c2 = _login(app, 'PDEAN2')
    html = _band_page(c2)
    assert 'Teacher TM1' in html and 'Teacher TA1' not in html and 'Dr. Alpha' not in html
    d = _teacher_payload(world, code='TA1')               # Dean 1's form
    assert _post(c2, SEND, d, html).status_code == 422
    j = _post(c2, SEND, _teacher_payload(world, code='TM1', create_action='1'), html).get_json()
    assert j['sent'] == 1 and _actions(app)[0]['owner_user_id'] == world['hodM']


def test_non_dean_roles_cannot_post(app, world, outbox):
    c = app.test_client()
    assert login(c, '/teacher/login', {'emp_code': 'TA1', 'password': 'pass12345'}).status_code in (200, 302)
    for path in (SEND, HODMAIL):
        r = c.post(path, data=_teacher_payload(world), headers={'X-Requested-With': 'fetch'})
        assert r.status_code in (302, 400, 401, 403)
    assert outbox.sent == []
