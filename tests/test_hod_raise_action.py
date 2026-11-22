"""HOD "My Actions": raise actions for the Dean, reply, start, resolve, Dean confirmation."""
import os
import re
import tempfile

import pytest

from feedback_system import create_app
from feedback_system import db as db_module
from feedback_system import hod as hod_module
from feedback_system.security import hash_password
from helpers import get_csrf, login


@pytest.fixture(scope='module')
def app():
    tmpdir = tempfile.mkdtemp(prefix='ffs-hod-raise-')
    yield create_app({
        'TESTING': True,
        'DATABASE': os.path.join(tmpdir, 'test.db'),
        'DATABASE_URL': None,
        'SECRET_KEY': 'test-secret-key-hod-raise',
        'AUTO_IMPORT': True,
        'ADMIN_EMAIL': 'admin@college.edu',
        'ADMIN_PASSWORD': 'admin123',
        'DEFAULT_TEACHER_PASSWORD': 'teacher@123',
        'DEFAULT_STUDENT_PASSWORD': 'student@123',
        'LOGIN_MAX_ATTEMPTS': 50,
        'REQUIRE_ACCOUNT_SETUP': False,
    })


def _user(role, code, name):
    db_module.execute(
        """INSERT INTO users(role, username, emp_code, name, email, password_hash,
                             is_active, onboarding_complete)
           VALUES(?,?,?,?,?,?,1,1)""",
        (role, code, code, name, f'{code.lower()}@t.edu', hash_password('pass12345')))
    return db_module.query('SELECT id FROM users WHERE username=?', (code,), one=True)['id']


def _dept_teacher(branch_id, skip=(), not_in_branch=None):
    """(teacher_id, class_session_id) of a teacher who teaches in ``branch_id``
    (and, when given, nowhere in ``not_in_branch``)."""
    extra, args = '', [branch_id]
    if skip:
        extra += f' AND u.id NOT IN ({",".join("?" * len(skip))})'
        args += list(skip)
    if not_in_branch:
        extra += (' AND u.id NOT IN (SELECT cs2.teacher_user_id FROM class_sessions cs2 '
                  'JOIN timetables t2 ON t2.id = cs2.timetable_id WHERE t2.branch_id=? '
                  'AND cs2.teacher_user_id IS NOT NULL)')
        args.append(not_in_branch)
    r = db_module.query(
        f"""SELECT u.id AS tid, cs.id AS sid FROM class_sessions cs
            JOIN timetables t ON t.id = cs.timetable_id
            JOIN users u ON u.id = cs.teacher_user_id
            WHERE t.branch_id=? AND t.is_deleted=0 AND cs.is_deleted=0
              AND u.role='teacher' AND u.is_active=1{extra}
            ORDER BY u.id LIMIT 1""", tuple(args), one=True)
    return r['tid'], r['sid']


def _form(branch_id, session_id, title):
    fid = db_module.insert_id(
        """INSERT INTO feedback_forms(title, branch_id, session_id, status, is_deleted)
           VALUES(?,?,?,'open',0)""", (title, branch_id, session_id))
    db_module.get_db().commit()
    return fid


@pytest.fixture(scope='module')
def seeded(app):
    """Dean over HOD1 (branch A) and HOD2 (branch B); teachers + forms in each department."""
    with app.app_context():
        branches = [r['branch_id'] for r in db_module.query(
            """SELECT t.branch_id FROM class_sessions cs
               JOIN timetables t ON t.id = cs.timetable_id
               WHERE t.is_deleted=0 AND cs.is_deleted=0 AND cs.teacher_user_id IS NOT NULL
               GROUP BY t.branch_id HAVING COUNT(DISTINCT cs.teacher_user_id) >= 2
               ORDER BY t.branch_id LIMIT 2""")]
        assert len(branches) == 2
        dean = _user('dean', 'RDEAN', 'Raise Dean')
        hods = {}
        for i, b in enumerate(branches, 1):
            tt = db_module.query(
                'SELECT id, semester_id, academic_year_id FROM timetables '
                'WHERE branch_id=? AND is_deleted=0 ORDER BY id LIMIT 1', (b,), one=True)
            hid = _user('hod', f'RHOD{i}', f'Raise HOD {i}')
            db_module.execute(
                """INSERT INTO hod_allocations(hod_user_id, academic_year_id, branch_id,
                       semester_id, timetable_id) VALUES(?,?,?,?,?)""",
                (hid, tt['academic_year_id'], b, tt['semester_id'], tt['id']))
            db_module.execute(
                'INSERT INTO dean_hod_assignments(dean_user_id, hod_user_id) VALUES(?,?)',
                (dean, hid))
            hods[i] = hid
        ta, sa = _dept_teacher(branches[0])
        ta2, _ = _dept_teacher(branches[0], skip=(ta,))
        tb, sb = _dept_teacher(branches[1], not_in_branch=branches[0])
        return {'dean': dean, 'hod1': hods[1], 'hod2': hods[2],
                'ba': branches[0], 'bb': branches[1],
                'ta': ta, 'ta2': ta2, 'tb': tb,
                'form_a': _form(branches[0], sa, 'RAISE Form A'),
                'form_b': _form(branches[1], sb, 'RAISE Form B')}


def _login(app, role, code):
    c = app.test_client()
    assert login(c, f'/{role}/login', {'emp_code': code, 'password': 'pass12345'}).status_code == 302
    return c


def _raise(c, **fields):
    tok = get_csrf(c.get('/hod/actions/new').get_data(as_text=True))
    data = {'_csrf': tok, 'title': 'Need lab budget', 'priority': 'high',
            'due_date': '2030-01-01'}
    data.update(fields)
    return c.post('/hod/actions/new', data=data)


def _post(c, url, **fields):
    tok = get_csrf(c.get('/hod/actions').get_data(as_text=True))
    return c.post(url, data={'_csrf': tok, **fields})


def _row(aid):
    return dict(db_module.query('SELECT * FROM improvement_actions WHERE id=?', (aid,), one=True))


def _aid(title):
    return db_module.query('SELECT id FROM improvement_actions WHERE title=?',
                           (title,), one=True)['id']


def _dean_creates(app, seeded, title, owner=None, branch=None):
    dean = _login(app, 'dean', 'RDEAN')
    tok = get_csrf(dean.get('/dean/actions/new').get_data(as_text=True))
    r = dean.post('/dean/actions/new', data={
        '_csrf': tok, 'title': title, 'branch_id': branch or seeded['ba'],
        'owner_user_id': owner or seeded['hod1'], 'priority': 'high',
        'status': 'planned', 'due_date': '2030-01-01',
        'description': 'Please look into this.'})
    assert r.status_code == 302
    with app.app_context():
        return _aid(title)


def _needs(app, hod_id):
    with app.app_context():
        return {a['title']: a['needs_response']
                for a in hod_module._load_my_actions({'id': hod_id})}


def _audit_actions(app, entity_id):
    with app.app_context():
        return [r['action'] for r in db_module.query(
            "SELECT action FROM audit_log WHERE entity='improvement_actions' AND entity_id=? "
            "ORDER BY id", (entity_id,))]


# --------------------------------------------------------------------------- #
def test_migration_29_columns_exist(app):
    with app.app_context():
        ia = {c['name'] for c in db_module.query('PRAGMA table_info(improvement_actions)')}
        lg = {c['name'] for c in db_module.query('PRAGMA table_info(improvement_action_log)')}
        ver = db_module.query(
            'SELECT MAX(version) v FROM feedback_system_schema_migrations', one=True)['v']
    assert {'raised_by_role', 'closed_at'} <= ia and 'kind' in lg
    assert ver >= 29


def test_hod_raises_action_for_own_department_teacher(app, seeded):
    hod = _login(app, 'hod', 'RHOD1')
    r = _raise(hod, title='Raise: lab budget', teacher_id=seeded['ta'],
               form_id=seeded['form_a'], description='Lab kits are missing.')
    assert r.status_code == 302 and 'tab=raised' in r.headers['Location']
    with app.app_context():
        aid = _aid('Raise: lab budget')
        a = _row(aid)
        assert a['raised_by_role'] == 'hod' and a['created_by'] == seeded['hod1']
        assert a['teacher_user_id'] == seeded['ta'] and a['form_id'] == seeded['form_a']
        assert a['branch_id'] == seeded['ba'] and a['status'] == 'planned'
        assert a['owner_user_id'] is None and a['closed_at'] is None
        log = db_module.query('SELECT actor_user_id, kind, new_status FROM improvement_action_log '
                              'WHERE action_id=?', (aid,), one=True)
        assert (log['actor_user_id'], log['kind'], log['new_status']) == (
            seeded['hod1'], 'status', 'planned')
        assert 'hod.action_raise' in _audit_actions(app, aid)
    page = hod.get('/hod/actions?tab=raised').get_data(as_text=True)
    assert 'Raise: lab budget' in page and 'HOD (' in page and '→ Dean' in page
    # ...and it is not listed as something the Dean assigned.
    assert 'Raise: lab budget' not in hod.get('/hod/actions?tab=assigned').get_data(as_text=True)
    dean_page = _login(app, 'dean', 'RDEAN').get('/dean/actions').get_data(as_text=True)
    assert 'Raise: lab budget' in dean_page and 'Raised by HOD' in dean_page


def test_hod_can_raise_without_teacher_or_form(app, seeded):
    hod = _login(app, 'hod', 'RHOD1')
    assert _raise(hod, title='Raise: general').status_code == 302
    with app.app_context():
        a = _row(_aid('Raise: general'))
    assert a['teacher_user_id'] is None and a['form_id'] is None and a['branch_id'] == seeded['ba']


def test_hod_cannot_target_another_department(app, seeded):
    hod = _login(app, 'hod', 'RHOD1')
    for fields in ({'teacher_id': seeded['tb']},          # teacher of department B
                   {'form_id': seeded['form_b']},         # form of department B
                   {'branch_id': seeded['bb']},           # department B itself
                   {'teacher_id': seeded['dean']}):       # not a teacher at all
        r = _raise(hod, title='Raise: forbidden', **fields)
        assert r.status_code in (403, 404), fields
    with app.app_context():
        assert db_module.query('SELECT COUNT(*) c FROM improvement_actions WHERE title=?',
                               ('Raise: forbidden',), one=True)['c'] == 0
    page = hod.get('/hod/actions/new').get_data(as_text=True)
    assert 'RAISE Form A' in page and 'RAISE Form B' not in page


def test_rejects_bad_input_without_creating(app, seeded):
    hod = _login(app, 'hod', 'RHOD1')
    assert _raise(hod, title='').status_code == 200
    assert _raise(hod, title='Raise: bad prio', priority='urgent').status_code == 200
    assert _raise(hod, title='Raise: bad date', due_date='31/12/2030').status_code == 200
    assert _raise(hod, title='Raise: not a number', teacher_id='abc').status_code == 400
    # a form that belongs to a different teacher than the one selected
    r = _raise(hod, title='Raise: mismatch', teacher_id=seeded['ta2'], form_id=seeded['form_a'])
    assert r.status_code == 200 and b'different teacher' in r.data
    with app.app_context():
        n = db_module.query("SELECT COUNT(*) c FROM improvement_actions WHERE title LIKE 'Raise: bad%' "
                            "OR title='Raise: mismatch' OR title='Raise: not a number' OR title=''",
                            one=True)['c']
    assert n == 0


def test_only_a_hod_with_a_department_can_raise(app, seeded):
    assert app.test_client().get('/hod/actions/new').status_code == 302
    assert _login(app, 'dean', 'RDEAN').get('/hod/actions/new').status_code == 403
    with app.app_context():
        _user('hod', 'RHODX', 'Raise HOD Nowhere')      # no department allocated
    assert _login(app, 'hod', 'RHODX').get('/hod/actions/new').status_code == 403


def test_dean_raised_action_appears_under_assigned_to_me(app, seeded):
    _dean_creates(app, seeded, 'Raise: from dean')
    hod1 = _login(app, 'hod', 'RHOD1')
    assigned = hod1.get('/hod/actions?tab=assigned').get_data(as_text=True)
    assert 'Raise: from dean' in assigned and 'Dean → HOD' in assigned
    assert 'Raise: from dean' not in hod1.get('/hod/actions?tab=raised').get_data(as_text=True)
    hod2 = _login(app, 'hod', 'RHOD2')
    for tab in ('assigned', 'raised'):
        assert 'Raise: from dean' not in hod2.get(f'/hod/actions?tab={tab}').get_data(as_text=True)


def test_status_flow_open_in_progress_resolved_closed(app, seeded):
    aid = _dean_creates(app, seeded, 'Raise: flow')
    hod = _login(app, 'hod', 'RHOD1')
    # Open -> In progress
    assert _post(hod, f'/hod/actions/{aid}/start').status_code == 302
    with app.app_context():
        a = _row(aid)
    assert a['status'] == 'in_progress' and a['closed_at'] is None
    # a second "start" is refused and changes nothing
    _post(hod, f'/hod/actions/{aid}/start')
    with app.app_context():
        assert _row(aid)['status'] == 'in_progress'
    # In progress -> Resolved (awaiting the Dean)
    _post(hod, f'/hod/actions/{aid}/resolve', reply='Peer observation done.')
    with app.app_context():
        a = _row(aid)
        assert a['status'] == 'completed' and a['completed_at'] and a['closed_at'] is None
        kinds = [(l['kind'], l['old_status'], l['new_status']) for l in db_module.query(
            'SELECT * FROM improvement_action_log WHERE action_id=? ORDER BY id', (aid,))]
        assert ('status', 'planned', 'in_progress') in kinds
        assert ('status', 'in_progress', 'completed') in kinds
        assert ('reply', 'completed', 'completed') in kinds
        assert _audit_actions(app, aid).count('hod.action_status') == 2
        stages = {x['title']: x['stage'] for x in hod_module._load_my_actions({'id': seeded['hod1']})}
    assert stages['Raise: flow'] == 'resolved'
    page = hod.get('/hod/actions?tab=assigned&status=resolved').get_data(as_text=True)
    assert 'Raise: flow' in page and 'Resolved (' in page
    # resolving twice is refused
    _post(hod, f'/hod/actions/{aid}/resolve')
    with app.app_context():
        assert db_module.query("SELECT COUNT(*) c FROM improvement_action_log WHERE action_id=? "
                               "AND new_status='completed' AND old_status='in_progress'",
                               (aid,), one=True)['c'] == 1
    # The Dean confirms (closes); the HOD can no longer change or reply.
    dean = _login(app, 'dean', 'RDEAN')
    tok = get_csrf(dean.get(f'/dean/actions/{aid}/edit').get_data(as_text=True))
    assert dean.post(f'/dean/actions/{aid}/edit', data={
        '_csrf': tok, 'title': 'Raise: flow', 'status': 'completed', 'priority': 'high',
        'branch_id': seeded['ba'], 'owner_user_id': seeded['hod1'],
        'due_date': '2030-01-01', 'note': 'Thanks, confirmed.'}).status_code == 302
    with app.app_context():
        assert _row(aid)['closed_at']
        stages = {x['title']: x['stage'] for x in hod_module._load_my_actions({'id': seeded['hod1']})}
    assert stages['Raise: flow'] == 'closed'
    for op in ('start', 'resolve', 'reply'):
        _post(hod, f'/hod/actions/{aid}/{op}', reply='late')
    with app.app_context():
        a = _row(aid)
        assert a['status'] == 'completed' and a['closed_at']
        assert db_module.query("SELECT COUNT(*) c FROM improvement_action_log WHERE action_id=? "
                               "AND note='late'", (aid,), one=True)['c'] == 0


def test_dean_reopening_a_resolved_action_clears_closed(app, seeded):
    aid = _dean_creates(app, seeded, 'Raise: reopen')
    hod = _login(app, 'hod', 'RHOD1')
    _post(hod, f'/hod/actions/{aid}/resolve')
    dean = _login(app, 'dean', 'RDEAN')
    tok = get_csrf(dean.get(f'/dean/actions/{aid}/edit').get_data(as_text=True))
    dean.post(f'/dean/actions/{aid}/edit', data={
        '_csrf': tok, 'title': 'Raise: reopen', 'status': 'in_progress', 'priority': 'high',
        'branch_id': seeded['ba'], 'owner_user_id': seeded['hod1'], 'due_date': '2030-01-01'})
    with app.app_context():
        a = _row(aid)
    assert a['status'] == 'in_progress' and a['closed_at'] is None and a['completed_at'] is None
    assert _needs(app, seeded['hod1'])['Raise: reopen'] is True   # Dean's move waits on the HOD


def test_hod_cannot_start_an_action_it_raised_but_can_resolve_it(app, seeded):
    hod = _login(app, 'hod', 'RHOD1')
    _raise(hod, title='Raise: own')
    with app.app_context():
        aid = _aid('Raise: own')
    _post(hod, f'/hod/actions/{aid}/start')
    with app.app_context():
        assert _row(aid)['status'] == 'planned'
    _post(hod, f'/hod/actions/{aid}/resolve', reply='Solved on our side.')
    with app.app_context():
        a = _row(aid)
    assert a['status'] == 'completed' and a['closed_at'] is None


def test_needs_your_response_for_both_sides(app, seeded):
    h1 = seeded['hod1']
    # Dean-raised: the opening text is the Dean's, so the HOD owes a response.
    aid = _dean_creates(app, seeded, 'Raise: need-assigned')
    assert _needs(app, h1)['Raise: need-assigned'] is True
    hod = _login(app, 'hod', 'RHOD1')
    assert 'Needs your response' in hod.get('/hod/actions?tab=assigned').get_data(as_text=True)
    _post(hod, f'/hod/actions/{aid}/reply', reply='Will meet the teacher.')
    assert _needs(app, h1)['Raise: need-assigned'] is False
    dean = _login(app, 'dean', 'RDEAN')

    def dean_note(a_id, text, title, status='in_progress'):
        tok = get_csrf(dean.get(f'/dean/actions/{a_id}/edit').get_data(as_text=True))
        dean.post(f'/dean/actions/{a_id}/edit', data={
            '_csrf': tok, 'title': title, 'status': status, 'priority': 'high',
            'branch_id': seeded['ba'], 'owner_user_id': h1, 'note': text})
    dean_note(aid, 'Share the plan by Friday.', 'Raise: need-assigned')
    assert _needs(app, h1)['Raise: need-assigned'] is True
    with app.app_context():
        # the HOD's legacy "HOD replied" view stays in step with the new reply box
        assert _row(aid)['hod_reply'] == 'Will meet the teacher.'
    # HOD-raised: the HOD has spoken last, so it is the Dean's turn.
    _raise(hod, title='Raise: need-raised')
    with app.app_context():
        rid = _aid('Raise: need-raised')
    assert _needs(app, h1)['Raise: need-raised'] is False
    _post(hod, f'/hod/actions/{rid}/reply', reply='Any update?')
    assert _needs(app, h1)['Raise: need-raised'] is False
    tok = get_csrf(dean.get(f'/dean/actions/{rid}/edit').get_data(as_text=True))
    dean.post(f'/dean/actions/{rid}/edit', data={
        '_csrf': tok, 'title': 'Raise: need-raised', 'status': 'planned', 'priority': 'high',
        'branch_id': seeded['ba'], 'note': 'Budget approved, please proceed.'})
    assert _needs(app, h1)['Raise: need-raised'] is True
    _post(hod, f'/hod/actions/{rid}/reply', reply='Thank you.')
    assert _needs(app, h1)['Raise: need-raised'] is False
    # A resolved action never needs a response.
    _post(hod, f'/hod/actions/{aid}/resolve')
    assert _needs(app, h1)['Raise: need-assigned'] is False
    assert 'hod.action_reply' in _audit_actions(app, rid)


def test_stat_cards_and_chip_counts_come_from_the_database(app, seeded):
    hod = _login(app, 'hod', 'RHOD1')
    html = hod.get('/hod/actions').get_data(as_text=True)
    for label in ('Waiting for your reply / action', 'Open &amp; in progress',
                  'Resolved, awaiting confirmation', 'Closed'):
        assert label in html
    nums = [int(n) for n in re.findall(r'<div class="hax-stat[^"]*"><b>(\d+)</b>', html)]
    with app.app_context():
        rows = hod_module._load_my_actions({'id': seeded['hod1']})
    assert nums == [sum(a['needs_response'] for a in rows),
                    sum(a['stage'] in ('open', 'in_progress') for a in rows),
                    sum(a['stage'] == 'resolved' for a in rows),
                    sum(a['stage'] == 'closed' for a in rows)]
    n_assigned = sum(a['direction'] == 'assigned' for a in rows)
    assert f'All ({n_assigned})' in hod.get('/hod/actions?tab=assigned').get_data(as_text=True)
    # an unknown tab / filter falls back instead of failing
    assert hod.get('/hod/actions?tab=zzz&status=zzz').status_code == 200


def test_another_hod_cannot_touch_these_actions(app, seeded):
    with app.app_context():
        assigned, raised = _aid('Raise: need-assigned'), _aid('Raise: need-raised')
    hod2 = _login(app, 'hod', 'RHOD2')
    for aid in (assigned, raised):
        for op in ('reply', 'start', 'resolve'):
            assert _post(hod2, f'/hod/actions/{aid}/{op}', reply='x').status_code == 403
    assert _post(hod2, '/hod/actions/999999/resolve').status_code == 403
    # the Dean has no HOD-side endpoints either
    dean = _login(app, 'dean', 'RDEAN')
    assert dean.post(f'/hod/actions/{assigned}/resolve', data={'_csrf': 'x'}).status_code in (400, 403)


def test_older_hod_endpoints_still_work(app, seeded):
    aid = _dean_creates(app, seeded, 'Raise: legacy')
    hod = _login(app, 'hod', 'RHOD1')
    assert _post(hod, f'/hod/actions/{aid}/status', status='in_progress',
                 note='Working on it').status_code == 302
    assert _post(hod, '/hod/actions/reply', action_id=str(aid), reply='Legacy reply',
                 status='').status_code == 302
    with app.app_context():
        a = _row(aid)
    assert a['status'] == 'in_progress' and a['hod_reply'] == 'Legacy reply'
    page = hod.get('/hod/actions').get_data(as_text=True)
    assert 'Legacy reply' in page and 'Working on it' in page
