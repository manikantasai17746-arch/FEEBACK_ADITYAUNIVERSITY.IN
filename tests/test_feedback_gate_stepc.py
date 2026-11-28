"""Step C: a student enters the current code from their authenticator app before giving
feedback on a form marked "Authentication Required".

* a marked form redirects to the code page until the code is accepted;
* an accepted code opens every marked form for the rest of this login session;
* logout or a new login asks again; a replayed code is refused;
* wrong codes lock after the limit; backup codes also work (once);
* forms without the requirement stay open; teachers cannot use the student pages;
* the verification records nothing about any feedback answer.
"""
import time

import pyotp
import pytest

from feedback_system import authenticator, db
from helpers import (eligible_rolls, find_session, login, login_student, make_form,
                     publish_form, question_ids, token_from)

STUDENT_STATE = {}


def _enroll(app, uid):
    """Give a student an active authenticator. The confirming step is in the past, so
    the next code is not blocked by replay protection."""
    past = time.time() - 120
    with app.app_context():
        secret = authenticator.begin_enrollment(uid)
        assert authenticator.confirm_enrollment(uid, pyotp.TOTP(secret).at(past), now=past)
        return authenticator.pending_secret(uid) or _active_secret(uid)


def _active_secret(uid):
    row = db.query('SELECT secret_enc FROM student_authenticators WHERE user_id=?', (uid,), one=True)
    return authenticator.decrypt_secret(row['secret_enc'])


def _current_code(app, uid):
    """A code for the current or next 30-second step that has not been used yet."""
    now = time.time()
    with app.app_context():
        row = db.query('SELECT secret_enc, last_step FROM student_authenticators WHERE user_id=?',
                       (uid,), one=True)
        secret = authenticator.decrypt_secret(row['secret_enc'])
        last = row['last_step'] or -1
    for t in (now + 30, now):
        if int(t) // 30 > last:
            return pyotp.TOTP(secret).at(t)
    raise AssertionError('no unused code window left in this test')


def _wipe(app, uid):
    with app.app_context():
        db.execute('DELETE FROM student_backup_codes WHERE user_id=?', (uid,))
        db.execute('DELETE FROM student_authenticators WHERE user_id=?', (uid,))
        authenticator.clear_otp_failures(uid)


@pytest.fixture()
def gate_setup(app, admin):
    sid = find_session(app, '6817', '2501IT07')
    roll = eligible_rolls(app, sid)[0]
    with app.app_context():
        uid = db.query("SELECT id FROM users WHERE role='student' AND username=?",
                       (roll,), one=True)['id']
    _wipe(app, uid)
    yield {'sid': sid, 'roll': roll, 'uid': uid}
    _wipe(app, uid)


def _open_form(app, admin, sid, title, marked=True):
    fid = make_form(admin, sid, title=title)
    publish_form(admin, fid)
    with app.app_context():
        db.execute('UPDATE feedback_forms SET auth_required=? WHERE id=?', (1 if marked else 0, fid))
    return fid


def _verify(client, code, nxt=''):
    tok = token_from(client, '/student/')
    return client.post('/student/verify-code', data={'_csrf': tok, 'code': code, 'next': nxt})


def _submissions(app, fid):
    with app.app_context():
        return db.query('SELECT COUNT(*) c FROM submissions WHERE form_id=?', (fid,), one=True)['c']


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
def test_new_forms_are_marked_by_default(app, admin, gate_setup):
    fid = make_form(admin, gate_setup['sid'], title='Stage C default marking')
    with app.app_context():
        assert db.query('SELECT auth_required FROM feedback_forms WHERE id=?', (fid,),
                        one=True)['auth_required'] == 1


def test_marked_form_asks_for_the_code_first(app, admin, client, gate_setup):
    _enroll(app, gate_setup['uid'])
    fid = _open_form(app, admin, gate_setup['sid'], 'Stage C marked')
    assert login_student(client, gate_setup['roll']).status_code == 302
    r = client.get(f'/student/form/{fid}')
    assert r.status_code == 302 and 'verify-code' in r.headers['Location']


def test_correct_code_opens_every_marked_form_for_this_session(app, admin, client, gate_setup):
    _enroll(app, gate_setup['uid'])
    f1 = _open_form(app, admin, gate_setup['sid'], 'Stage C first')
    f2 = _open_form(app, admin, gate_setup['sid'], 'Stage C second')
    assert login_student(client, gate_setup['roll']).status_code == 302
    r = _verify(client, _current_code(app, gate_setup['uid']))
    assert r.status_code == 302
    assert client.get(f'/student/form/{f1}').status_code == 200
    assert client.get(f'/student/form/{f2}').status_code == 200


def test_wrong_code_does_not_open_the_form(app, admin, client, gate_setup):
    _enroll(app, gate_setup['uid'])
    fid = _open_form(app, admin, gate_setup['sid'], 'Stage C wrong code')
    assert login_student(client, gate_setup['roll']).status_code == 302
    _verify(client, '000000')
    assert client.get(f'/student/form/{fid}').status_code == 302


def test_submit_is_refused_without_the_code_and_records_nothing(app, admin, client, gate_setup):
    _enroll(app, gate_setup['uid'])
    fid = _open_form(app, admin, gate_setup['sid'], 'Stage C submit blocked')
    assert login_student(client, gate_setup['roll']).status_code == 302
    before = _submissions(app, fid)
    tok = token_from(client, '/student/')
    r = client.post(f'/student/form/{fid}/submit', data={'_csrf': tok})
    assert r.status_code == 302 and 'verify-code' in r.headers['Location']
    assert _submissions(app, fid) == before


def test_submit_works_after_the_code(app, admin, client, gate_setup):
    _enroll(app, gate_setup['uid'])
    fid = _open_form(app, admin, gate_setup['sid'], 'Stage C submit allowed')
    assert login_student(client, gate_setup['roll']).status_code == 302
    _verify(client, _current_code(app, gate_setup['uid']))
    html = client.get(f'/student/form/{fid}').get_data(as_text=True)
    rq, tq, oid = question_ids(html)
    tok = token_from(client, '/student/')
    data = {'_csrf': tok}
    if rq:
        data[f'q_{rq}'] = oid
    if tq:
        data[f'q_{tq}'] = 'Clear explanations'
        data[f'qs_{tq}'] = '4'
    r = client.post(f'/student/form/{fid}/submit', data=data)
    assert r.status_code == 302 and 'verify-code' not in r.headers['Location']
    assert _submissions(app, fid) == 1


def test_logout_and_login_again_asks_again(app, admin, client, gate_setup):
    _enroll(app, gate_setup['uid'])
    fid = _open_form(app, admin, gate_setup['sid'], 'Stage C relogin')
    assert login_student(client, gate_setup['roll']).status_code == 302
    _verify(client, _current_code(app, gate_setup['uid']))
    assert client.get(f'/student/form/{fid}').status_code == 200
    client.get('/logout')
    assert login_student(client, gate_setup['roll']).status_code == 302
    r = client.get(f'/student/form/{fid}')
    assert r.status_code == 302 and 'verify-code' in r.headers['Location']


def test_a_used_code_cannot_be_replayed_after_a_new_login(app, admin, client, gate_setup):
    _enroll(app, gate_setup['uid'])
    fid = _open_form(app, admin, gate_setup['sid'], 'Stage C replay')
    assert login_student(client, gate_setup['roll']).status_code == 302
    code = _current_code(app, gate_setup['uid'])
    _verify(client, code)
    client.get('/logout')
    assert login_student(client, gate_setup['roll']).status_code == 302
    _verify(client, code)                        # the same code again
    assert client.get(f'/student/form/{fid}').status_code == 302


def test_repeated_wrong_codes_lock_the_gate(app, admin, client, gate_setup):
    _enroll(app, gate_setup['uid'])
    fid = _open_form(app, admin, gate_setup['sid'], 'Stage C lock')
    assert login_student(client, gate_setup['roll']).status_code == 302
    for _ in range(5):
        _verify(client, '000000')
    r = _verify(client, _current_code(app, gate_setup['uid']))
    assert r.status_code == 200 and b'Too many' in r.data
    assert client.get(f'/student/form/{fid}').status_code == 302


def test_backup_code_opens_the_form_once(app, admin, client, gate_setup):
    _enroll(app, gate_setup['uid'])
    codes = authenticator.generate_backup_codes()
    with app.app_context():
        authenticator.store_backup_codes(gate_setup['uid'], codes)
    fid = _open_form(app, admin, gate_setup['sid'], 'Stage C backup')
    assert login_student(client, gate_setup['roll']).status_code == 302
    _verify(client, codes[0])
    assert client.get(f'/student/form/{fid}').status_code == 200
    client.get('/logout')
    assert login_student(client, gate_setup['roll']).status_code == 302
    _verify(client, codes[0])                    # a backup code is single use
    assert client.get(f'/student/form/{fid}').status_code == 302


def test_form_without_the_requirement_is_open_without_a_code(app, admin, client, gate_setup):
    fid = _open_form(app, admin, gate_setup['sid'], 'Stage C unmarked', marked=False)
    assert login_student(client, gate_setup['roll']).status_code == 302
    assert client.get(f'/student/form/{fid}').status_code == 200


def test_student_without_an_authenticator_is_sent_to_setup(app, admin, client, gate_setup):
    fid = _open_form(app, admin, gate_setup['sid'], 'Stage C no app')
    assert login_student(client, gate_setup['roll']).status_code == 302
    r = client.get(f'/student/form/{fid}')
    assert r.status_code == 302 and 'setup/authenticator' in r.headers['Location']


def test_verification_records_no_feedback_answer(app, admin, client, gate_setup):
    _enroll(app, gate_setup['uid'])
    fid = _open_form(app, admin, gate_setup['sid'], 'Stage C anonymity')
    assert login_student(client, gate_setup['roll']).status_code == 302
    with app.app_context():
        before = db.query('SELECT COUNT(*) c FROM feedback_responses', one=True)['c']
    _verify(client, _current_code(app, gate_setup['uid']))
    with app.app_context():
        after = db.query('SELECT COUNT(*) c FROM feedback_responses', one=True)['c']
    assert after == before
    assert _submissions(app, fid) == 0


def test_teacher_cannot_use_the_student_code_page(app, client, teacher):
    r = client.get('/student/verify-code')
    assert r.status_code in (302, 403)


# --------------------------------------------------------------------------- #
# The anonymous pages: form -> submitted -> log out of anonymous ID -> progress
# --------------------------------------------------------------------------- #
def _answers(client, fid):
    html = client.get(f'/student/form/{fid}').get_data(as_text=True)
    rq, tq, oid = question_ids(html)
    data = {'_csrf': token_from(client, '/student/')}
    if rq:
        data[f'q_{rq}'] = oid
    if tq:
        data[f'q_{tq}'] = 'Clear explanations'
        data[f'qs_{tq}'] = '4'
    return data


def test_form_after_the_code_hides_the_student(app, admin, client, gate_setup):
    _enroll(app, gate_setup['uid'])
    fid = _open_form(app, admin, gate_setup['sid'], 'Anon page')
    assert login_student(client, gate_setup['roll']).status_code == 302
    _verify(client, _current_code(app, gate_setup['uid']))
    html = client.get(f'/student/form/{fid}').get_data(as_text=True)
    assert 'teacher-card' in html and 'data-feedback-submit' in html
    assert 'class="topnav"' not in html and 'Logout (' not in html
    assert gate_setup['roll'] not in html


def test_submit_then_log_out_of_the_anonymous_id(app, admin, client, gate_setup):
    _enroll(app, gate_setup['uid'])
    fid = _open_form(app, admin, gate_setup['sid'], 'Anon submit')
    other = _open_form(app, admin, gate_setup['sid'], 'Anon next')
    # Other tests in this shared database leave forms open for the same student. Close them
    # for the length of this test (and reopen them after) so "the next pending form" is
    # exactly the one created above.
    with app.app_context():
        leftovers = [r['id'] for r in db.query(
            "SELECT id FROM feedback_forms WHERE status='open' AND id NOT IN (?, ?)",
            (fid, other))]
        for i in leftovers:
            db.execute("UPDATE feedback_forms SET status='closed' WHERE id=?", (i,))
    try:
        assert login_student(client, gate_setup['roll']).status_code == 302
        _verify(client, _current_code(app, gate_setup['uid']))
        r = client.post(f'/student/form/{fid}/submit', data=_answers(client, fid))
        # Another form is still pending: it opens straight away, with no new code asked for.
        assert r.status_code == 302 and f'/student/form/{other}' in r.headers['Location']
        assert client.get(r.headers['Location']).status_code == 200
        r = client.post(f'/student/form/{other}/submit', data=_answers(client, other))
        # Nothing left to fill in: the "submitted" page is shown.
        assert r.status_code == 302 and r.headers['Location'].endswith('/student/anonymous/submitted')
        done = client.get(r.headers['Location']).get_data(as_text=True)
        assert 'Feedback submitted' in done and 'Log out of anonymous ID' in done
        assert gate_setup['roll'] not in done and 'class="topnav"' not in done
        tok = token_from(client, '/student/progress')
        r = client.post('/student/anonymous/logout', data={'_csrf': tok})
        assert r.status_code == 302 and r.headers['Location'].endswith('/student/progress')
        progress = client.get('/student/progress').get_data(as_text=True)
        assert gate_setup['roll'] in progress and 'Submitted' in progress
        # `admin` and `client` are the same test client, now logged in as the student, so a
        # separate admin client creates the next form.
        admin2 = app.test_client()
        assert login(admin2, '/admin/login', {'email': 'admin@college.edu',
                                              'password': 'admin123'}).status_code == 302
        third = _open_form(app, admin2, gate_setup['sid'], 'Anon third')
        again = client.get(f'/student/form/{third}')
        assert again.status_code == 302 and 'verify-code' in again.headers['Location']
        assert client.get('/student/anonymous/submitted').status_code == 302
    finally:
        with app.app_context():
            for i in leftovers:
                db.execute("UPDATE feedback_forms SET status='open' WHERE id=?", (i,))


def test_anonymous_logout_needs_csrf(app, admin, client, gate_setup):
    _enroll(app, gate_setup['uid'])
    fid = _open_form(app, admin, gate_setup['sid'], 'Anon csrf')
    assert login_student(client, gate_setup['roll']).status_code == 302
    _verify(client, _current_code(app, gate_setup['uid']))
    assert client.post('/student/anonymous/logout').status_code != 302
    assert client.get(f'/student/form/{fid}').status_code == 200
