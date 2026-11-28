"""Teacher leave / replacement and student TC / removal."""
import pytest

from feedback_system import create_app, db, services
from feedback_system import people_service as ps
from feedback_system.import_data import ensure_admin, import_timetable

RAW = [('CSE', 'CSE-1', 'S1', 'Maths', 'T1', 'Teacher One', roll) for roll in ('R1', 'R2', 'R3')]
DATA = (RAW, {'CSE': 'Computer Science'})


@pytest.fixture()
def env(tmp_path):
    app = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'p.db'), 'DATABASE_URL': None,
                      'SECRET_KEY': 'k', 'AUTO_IMPORT': False,
                      'ADMIN_EMAIL': 'a@x.edu', 'ADMIN_PASSWORD': 'admin-pass-123',
                      'DEFAULT_TEACHER_PASSWORD': 'teacher@123',
                      'DEFAULT_STUDENT_PASSWORD': 'student@123'})
    with app.app_context():
        ensure_admin(db.get_db(), 'a@x.edu', 'admin-pass-123')
        import_timetable(app, raw_data=DATA, progress=lambda *_: None)
        db.execute("INSERT INTO users(role,username,name,emp_code,password_hash) "
                   "VALUES('teacher','T2','Teacher Two','T2','x')")
        yield app


def uid(code):
    return db.query('SELECT id FROM users WHERE username=?', (code,), one=True)['id']


def sessions():
    return {r['emp_code']: (r['id'], r['is_deleted']) for r in db.query(
        'SELECT cs.id, cs.is_deleted, u.emp_code FROM class_sessions cs '
        'JOIN users u ON u.id=cs.teacher_user_id')}


def students_of(session_id):
    return [r['roll'] for r in services.eligible_students(session_id)]


def replace(status='on_leave', target='T2'):
    old = sessions()['T1'][0]
    ps.deactivate_teacher(uid('T1'), status, '2026-11-28', '',
                          {old: uid(target) if target else None}, None)
    return old


def test_replacement_keeps_old_session_with_old_teacher(env):
    old = replace()
    new, closed = sessions()['T2']
    assert closed == 0 and sessions()['T1'] == (old, 1)
    assert students_of(new) == ['R1', 'R2', 'R3']
    assert ps.get_person(uid('T1'), 'teacher')['status'] == 'on_leave'


def test_return_from_leave(env):
    old = replace()
    sub = sessions()['T2'][0]
    ids = [r['id'] for r in ps.open_replacements(teacher_id=uid('T1'))]
    assert ps.reactivate_teacher(uid('T1'), '', '', ids, None) == 1
    assert sessions()['T1'] == (old, 0) and sessions()['T2'] == (sub, 1)
    # and the same substitute can cover again later
    replace()
    assert sessions()['T2'] == (sub, 0) and sessions()['T1'] == (old, 1)


def test_vacant_then_assigned(env):
    replace('left', None)
    row = ps.open_replacements(vacant_only=True)[0]
    ps.assign_vacancy(row['id'], uid('T2'), None)
    assert students_of(sessions()['T2'][0]) == ['R1', 'R2', 'R3']
    assert not ps.open_replacements(vacant_only=True)


def test_failed_replacement_changes_nothing(env):
    old = sessions()['T1'][0]
    with pytest.raises(ps.PeopleError):
        ps.deactivate_teacher(uid('T1'), 'left', '', '', {old: uid('T1')}, None)
    assert sessions()['T1'] == (old, 0)
    assert ps.get_person(uid('T1'), 'teacher')['status'] == 'active'


def test_reimport_does_not_undo_replacement_or_reactivate(env):
    old = replace('left')
    new = sessions()['T2'][0]
    extra = RAW + [('CSE', 'CSE-1', 'S1', 'Maths', 'T1', 'Teacher One', 'R4')]
    import_timetable(env, raw_data=(extra, DATA[1]), progress=lambda *_: None)
    assert sessions()['T1'] == (old, 1)
    assert students_of(new) == ['R1', 'R2', 'R3', 'R4']
    assert 'R4' not in students_of(old)
    assert ps.get_person(uid('T1'), 'teacher')['status'] == 'left'


def test_removed_student_leaves_counts_and_stays_removed(env):
    sid = sessions()['T1'][0]
    ps.set_student_status([uid('R1')], 'tc', '', '', None)
    assert students_of(sid) == ['R2', 'R3']
    assert services.eligible_count(sid) == 2
    messages = []
    import_timetable(env, raw_data=DATA, progress=messages.append)
    assert ps.get_person(uid('R1'), 'student')['status'] == 'tc'
    assert any('R1' in m for m in messages if m.startswith('Warning'))
    ps.set_student_status([uid('R1')], 'active', '', '', None)
    assert students_of(sid) == ['R1', 'R2', 'R3']


def test_bulk_match(env):
    found, unknown = ps.match_rolls('r1\nR2, nope\nR1')
    assert [p['roll'] for p in found] == ['R1', 'R2'] and unknown == ['NOPE']


def _admin(app):
    client = app.test_client()
    with client.session_transaction() as s:
        s['uid'] = db.query("SELECT id FROM users WHERE role='admin'", one=True)['id']
        s['token'] = 't'
        import time
        s['started'] = s['last'] = int(time.time())
    return client


def test_admin_screens(env):
    client = _admin(env)
    t1, old = uid('T1'), sessions()['T1'][0]
    for url in (f'/admin/people/teachers/{t1}', f'/admin/people/students/{uid("R1")}',
                '/admin/people/students/bulk', '/admin/people/vacancies',
                '/admin/teachers', '/admin/students'):
        assert client.get(url).status_code == 200, url
    preview = client.post('/admin/people/students/bulk',
                          data={'_csrf': 't', 'rolls': 'R1', 'status': 'tc'})
    assert b'Confirm change' in preview.data
    assert ps.get_person(uid('R1'), 'student')['status'] == 'active'
    client.post('/admin/people/students/bulk',
                data={'_csrf': 't', 'rolls': 'R1', 'status': 'tc', 'confirm': '1'})
    assert ps.get_person(uid('R1'), 'student')['status'] == 'tc'
    data = {'_csrf': 't', 'status': 'on_leave', 'session_id': str(old),
            f'mode_{old}': 'replace', f'emp_{old}': 'T2'}
    url = f'/admin/people/teachers/{t1}/deactivate'
    client.post(url, data=data)                       # confirmation box not ticked
    assert sessions()['T1'] == (old, 0)
    assert client.post(url, data={**data, '_csrf': 'bad', 'confirm': '1'}).status_code == 400
    client.post(url, data={**data, 'confirm': '1'})
    assert sessions()['T1'] == (old, 1) and 'T2' in sessions()
    assert b'Mark active again' in client.get(f'/admin/people/teachers/{t1}').data


def test_screens_need_admin(env):
    assert env.test_client().get('/admin/people/vacancies').status_code == 302


QUESTIONS = [{'text': 'Explains well?', 'qtype': 'rating', 'is_required': True,
              'options': [{'label': 'Excellent', 'value': 1}, {'label': 'Bad', 'value': 3}]}]


def open_form(session_id):
    from datetime import datetime, timedelta
    admin = db.query("SELECT id FROM users WHERE role='admin'", one=True)['id']
    deadline = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M')
    fid = services.save_form({'title': f'F{session_id}', 'instructions': '',
                              'session_id': session_id, 'deadline_at': deadline,
                              'auth_required': False}, QUESTIONS, admin)
    if isinstance(fid, tuple):
        fid = fid[0]
    services.set_form_status(fid, 'open', admin)
    assert services.get_form(fid)['status'] == 'open'
    return fid


def submit(roll, fid):
    q = services.form_questions(fid)[0]
    return services.submit_feedback(uid(roll), fid,
                                    {f"q_{q['id']}": str(q['options'][0]['id'])})


def test_open_form_around_replacement(env):
    old = sessions()['T1'][0]
    fid = open_form(old)
    with env.test_request_context():
        assert submit('R1', fid)[0]
        replace('left')
        assert not submit('R2', fid)[0]                       # old class is closed
        assert fid not in [f['id'] for f in services.student_forms(uid('R2'))]
        assert fid in [f['id'] for f in services.student_forms(uid('R1'))]
        assert services.get_form(fid)['teacher_emp'] == 'T1'  # feedback did not move
        assert services.response_count(fid) == 1
        new_form = open_form(sessions()['T2'][0])
        assert submit('R1', new_form)[0] and submit('R2', new_form)[0]


def test_open_form_around_student_removal(env):
    fid = open_form(sessions()['T1'][0])
    with env.test_request_context():
        assert submit('R1', fid)[0]
        ps.set_student_status([uid('R1'), uid('R2')], 'removed', '', '', None)
        assert not submit('R2', fid)[0]
        assert services.eligible_count_for_form(fid) == 2     # R1 answered, R3 pending
        assert [r['roll'] for r in services.not_submitted_rolls(fid)] == ['R3']


def test_substitute_has_nothing_to_take_back(env):
    replace()
    ps.reactivate_teacher(uid('T1'), '', '', [r['id'] for r in
                          ps.open_replacements(teacher_id=uid('T1'))], None)
    assert not ps.open_replacements(teacher_id=uid('T2'))
